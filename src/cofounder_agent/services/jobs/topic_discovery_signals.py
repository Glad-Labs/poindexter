"""TopicDiscoverySignalsJob — signal-driven topic discovery (#229).

Replaces the long ``_should_trigger_discovery`` + ``_discover_and_queue_topics``
methods that previously lived in ``services/idle_worker.py``. The
behavior is unchanged; the move is part of #151's god-file cleanup +
the umbrella plugin refactor (#64) that gave us apscheduler in the
first place.

Why a Job (not just a daily cron)
---------------------------------

Topic discovery is *event-driven* — it should fire when any of a set
of signals trips, not on a fixed clock. Signals (in priority order):

1. **Throttle gate** — when the approval queue is full, suppress
   discovery entirely. Without this, generated topics pile up behind
   the wall and the operator has to shovel a mountain of stale
   pending work before the pipeline can move (gh-89).
2. **Manual trigger** — ``app_settings.topic_discovery_manual_trigger
   = "true"`` lets an operator force a fire. Cleared after firing.
3. **Cooldown** — minimum 30 min between automatic runs (configurable).
4. **Queue low** — pending content_tasks below threshold (default 2).
5. **Stale content** — last published post older than threshold
   (default 6h).
6. **Rejection streak** — N consecutive task rejections within the
   configured window (default 3 in 6h).
7. **24h safety net** — fires unconditionally once per day so the
   pipeline never stalls if signal evaluation breaks.

All thresholds live in ``app_settings`` so an operator can tune per
niche without a redeploy.

Schedule: every 5 minutes. Cheap — most cycles bail at signal step 3
(cooldown). When a signal does fire, the job hands off to
``services.topic_discovery.TopicDiscovery`` which is the actual
business logic.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from plugins.job import JobResult
from services.logger_config import get_logger

logger = get_logger(__name__)


# Cadence floor — every 5 min. The signal cooldown is the *real*
# rate limiter (default 30m); apscheduler just keeps polling.
_SCHEDULE = "*/5 * * * *"


async def _get_setting(pool: Any, key: str, default: str) -> str:
    """Read app_settings with a fallback. Mirrors the old
    IdleWorker._get_setting helper but takes pool by argument."""
    try:
        row = await pool.fetchrow(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
        if row and row["value"]:
            return row["value"]
    except Exception as e:
        logger.warning(
            "[topic_discovery_signals] app_settings read for %r failed; "
            "using default %r: %s",
            key, default, e,
        )
    return default


async def _persist_last_run(pool: Any, value: float) -> None:
    """Mirror the old idle_last_run_topic_discovery write so the
    cooldown comparison still works for any external dashboards
    keying on that name."""
    try:
        await pool.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()",
            "idle_last_run_topic_discovery", str(value),
        )
    except Exception as e:
        logger.debug(
            "[topic_discovery_signals] persist last_run failed: %s", e,
        )


async def _evaluate_signals(
    pool: Any, site_config: Any,
) -> tuple[bool, str]:
    """Return (should_fire, reason). Same algorithm as the old
    IdleWorker._should_trigger_discovery.

    Mirrors the original priority order so dashboards / Telegram
    notifications that key on the reason string keep working.
    """
    # 0. Throttle gate
    try:
        from services.pipeline_throttle import is_queue_full

        full, queue_size, queue_limit = await is_queue_full(pool, site_config)
        if full:
            return False, f"queue_full({queue_size}>={queue_limit})"
    except Exception as e:
        logger.debug("[topic_discovery_signals] throttle check failed: %s", e)

    # 1. Manual trigger (runs BEFORE cooldown so an operator who
    # explicitly asks for "discover now" isn't silently ignored.)
    try:
        manual = (await _get_setting(
            pool, "topic_discovery_manual_trigger", "false",
        )).strip().lower()
        if manual == "true":
            await pool.execute(
                "UPDATE app_settings SET value = 'false' "
                "WHERE key = 'topic_discovery_manual_trigger'"
            )
            return True, "manual_trigger"
    except Exception as e:
        logger.debug("[topic_discovery_signals] manual trigger failed: %s", e)

    # 2. Cooldown check
    try:
        cooldown_s = int(await _get_setting(
            pool, "topic_discovery_min_cooldown_seconds", "1800",
        ))
    except (ValueError, TypeError):
        cooldown_s = 1800

    try:
        last_raw = await _get_setting(pool, "idle_last_run_topic_discovery", "0")
        last_ts = float(last_raw or 0)
    except (ValueError, TypeError):
        last_ts = 0.0

    now_ts = time.time()
    if now_ts - last_ts < cooldown_s:
        return False, "cooldown"

    # 3. Queue-low signal
    try:
        low_threshold = int(await _get_setting(
            pool, "topic_discovery_queue_low_threshold", "2",
        ))
    except (ValueError, TypeError):
        low_threshold = 2

    try:
        pending = await pool.fetchval(
            "SELECT COUNT(*) FROM content_tasks WHERE status = 'pending'"
        )
        if (pending or 0) < low_threshold:
            return True, f"queue_low({pending}<{low_threshold})"
    except Exception as e:
        logger.debug("[topic_discovery_signals] queue check failed: %s", e)

    # 4. Stale content signal
    try:
        stale_hours = int(await _get_setting(
            pool, "topic_discovery_stale_hours", "6",
        ))
    except (ValueError, TypeError):
        stale_hours = 6

    try:
        last_pub = await pool.fetchval(
            "SELECT MAX(published_at) FROM posts WHERE status = 'published'"
        )
        if last_pub:
            if last_pub.tzinfo is None:
                last_pub = last_pub.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - last_pub).total_seconds() / 3600
            if age_hours > stale_hours:
                return True, f"stale_content({age_hours:.1f}h>{stale_hours}h)"
    except Exception as e:
        logger.debug("[topic_discovery_signals] stale check failed: %s", e)

    # 5. Rejection streak signal
    try:
        streak_threshold = int(await _get_setting(
            pool, "topic_discovery_rejection_streak", "3",
        ))
    except (ValueError, TypeError):
        streak_threshold = 3

    try:
        streak_h = (
            site_config.get_int("topic_discovery_streak_window_hours", 6)
            if site_config else 6
        )
        recent = await pool.fetch(
            "SELECT status FROM content_tasks "
            f"WHERE updated_at > NOW() - INTERVAL '{streak_h} hours' "  # nosec B608  # streak_h is int from app_settings via get_int
            "ORDER BY updated_at DESC LIMIT $1",
            streak_threshold,
        )
        if len(recent) >= streak_threshold and all(
            r["status"] in ("rejected", "rejected_final") for r in recent
        ):
            return True, f"rejection_streak({streak_threshold})"
    except Exception as e:
        logger.debug("[topic_discovery_signals] streak check failed: %s", e)

    # 6. 24h safety net
    if now_ts - last_ts > 86400:
        return True, "safety_net_24h"

    return False, "no_signal"


async def _discover_and_queue(pool: Any, site_config: Any) -> dict[str, Any]:
    """Hand off to TopicDiscovery for the actual work."""
    try:
        from services.topic_discovery import TopicDiscovery

        discovery = TopicDiscovery(pool, site_config=site_config)
        topics = await discovery.discover(max_topics=5)
        if not topics:
            return {"discovered": 0, "queued": 0, "note": "no fresh topics found"}
        queued = await discovery.queue_topics(topics)
        return {
            "discovered": len(topics),
            "queued": queued,
            "topics": [t.title[:50] for t in topics],
        }
    except Exception as e:
        logger.warning("[topic_discovery_signals] discovery failed: %s", e)
        return {"error": str(e), "discovered": 0, "queued": 0}


class TopicDiscoverySignalsJob:
    """Signal-driven topic discovery — port of the old IdleWorker
    methods to the apscheduler-driven Job protocol."""

    name = "topic_discovery_signals"
    description = (
        "Evaluate discovery signals (queue_low, stale_content, "
        "rejection_streak, manual_trigger) and fire topic discovery "
        "when one trips. 24h safety-net catches stalls."
    )
    schedule = _SCHEDULE
    idempotent = True

    async def run(
        self,
        pool: Any,
        config: dict[str, Any],
        site_config: Any = None,
    ) -> JobResult:
        # PluginScheduler injects site_config as a kwarg when the job's
        # signature declares it (see plugins/scheduler.py:120). Without
        # this parameter, site_config arrived only via config.get(...)
        # which PluginScheduler never populates — the discover step then
        # raised "TopicDiscovery: site_config is required" and the queue
        # starved (gh: nothing-generated incident 2026-04-28).
        should_fire, reason = await _evaluate_signals(pool, site_config)
        if not should_fire:
            return JobResult(
                ok=True,
                detail=f"signal-check: {reason}",
                changes_made=0,
                metrics={"trigger": reason, "fired": False},
            )

        result = await _discover_and_queue(pool, site_config)
        await _persist_last_run(pool, time.time())

        return JobResult(
            ok="error" not in result,
            detail=(
                f"fired ({reason}): discovered={result.get('discovered', 0)}, "
                f"queued={result.get('queued', 0)}"
            ),
            changes_made=result.get("queued", 0),
            metrics={
                "trigger": reason,
                "fired": True,
                **{k: v for k, v in result.items() if k != "topics"},
            },
        )


__all__ = ["TopicDiscoverySignalsJob"]
