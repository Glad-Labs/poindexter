"""EnqueueNewsShortsJob — queue the day's news Shorts (``services/news_shorts.py``).

Hourly. Each fire queues at most ONE news Short task, and only while all of
these hold:

- ``news_shorts_enabled`` is on (default off);
- the operator-local hour is at or past ``news_shorts_earliest_hour`` (default
  8), so the day's Short is made from the day's news rather than whatever the
  pool held at midnight;
- fewer than ``news_shorts_per_day`` news Short tasks were created since local
  midnight, whatever became of them (a failed render does not earn a second
  attempt the same day);
- no news Short task is still pending or running, so a slow render never
  stacks a second one behind it;
- :func:`services.news_shorts.pick_story` finds a fresh, unused, in-scope story.

The Prefect content flow claims the task like any other; the Short waits for
the operator's review in ``media_approvals`` once it renders. Every fire
returns its counts as ``JobResult.metrics``.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

_IN_FLIGHT_SQL = """
    SELECT COUNT(*) FROM pipeline_tasks
     WHERE task_type = $1 AND status IN ('pending', 'in_progress', 'rejected_retry')
"""


def _cfg_int(site_config: Any, key: str, default: int) -> int:
    try:
        return int(site_config.get_int(key, default)) if site_config is not None else default
    except (TypeError, ValueError):
        return default


class EnqueueNewsShortsJob:
    """Queue one news Short when the day's quota and the news allow it."""

    name = "enqueue_news_shorts"
    description = (
        "Queue a YouTube Short made from one fresh, in-scope news story "
        "(no blog post), up to news_shorts_per_day a day; off by default"
    )
    schedule = "every 1 hours"
    idempotent = False

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        from poindexter.services import news_shorts
        from poindexter.services.clock import get_operator_tz, now_local

        if pool is None:
            return JobResult(ok=False, detail="no pool available", changes_made=0)
        site_config = config.get("_site_config")
        if not news_shorts.enabled(site_config):
            return JobResult(ok=True, detail="disabled (news_shorts_enabled=false)", changes_made=0)

        tz = await get_operator_tz(pool)
        now = now_local(tz)
        earliest = min(23, max(0, _cfg_int(site_config, "news_shorts_earliest_hour", 8)))
        per_day = max(0, _cfg_int(site_config, "news_shorts_per_day", 1))
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        made_today = await news_shorts.made_since(pool, midnight)
        in_flight = int(await pool.fetchval(_IN_FLIGHT_SQL, news_shorts.TASK_TYPE) or 0)
        metrics: dict[str, Any] = {
            "made_today": made_today, "per_day": per_day, "in_flight": in_flight,
        }
        if now.hour < earliest:
            return JobResult(ok=True, detail=f"before news_shorts_earliest_hour={earliest}",
                             changes_made=0, metrics=metrics)
        if made_today >= per_day:
            return JobResult(ok=True, detail=f"{made_today}/{per_day} news Short(s) made today",
                             changes_made=0, metrics=metrics)
        if in_flight:
            return JobResult(ok=True, detail=f"{in_flight} news Short task(s) still in flight",
                             changes_made=0, metrics=metrics)

        try:
            picked = await news_shorts.pick_story(pool, site_config)
        except Exception as exc:  # noqa: BLE001 — a failed pick retries next hour
            return JobResult(ok=False, detail=f"story pick failed: {describe_exception(exc)}",
                             changes_made=0, metrics=metrics)
        metrics.update(considered=picked.considered, in_scope=picked.in_scope)
        if picked.story is None:
            return JobResult(ok=True, detail=picked.note or "no story", changes_made=0, metrics=metrics)

        task_id = await news_shorts.enqueue_news_short(
            pool, picked.story, niche=news_shorts.niche_slug(site_config),
        )
        metrics["task_id"] = task_id
        return JobResult(
            ok=True,
            detail=f"queued news Short {task_id}: {picked.story.title[:80]}",
            changes_made=1,
            metrics=metrics,
        )


__all__ = ["EnqueueNewsShortsJob"]
