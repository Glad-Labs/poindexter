"""Coalesced "N posts pending review" summary probe (Glad-Labs/poindexter#338).

Closes the second half of the gate-system polish issue's notification
batching bullet:

    > currently every gate flip pings Telegram. Coalesce to "N posts
    > pending review" once per hour when the queue is non-empty.

Per-flip Telegram pings are handled separately — the
``services/gates/post_approval_gates.notify_gate_pending`` helper now
hard-pins ``critical=False`` so per-flip notifications go to Discord
only (per Matt's ``feedback_telegram_vs_discord.md`` rule: Telegram =
critical alerts only). This probe is the single source of Telegram
pages about the gate queue: once it's been at least
``gate_pending_summary_min_age_minutes`` since the OLDEST pending gate
appeared, the operator gets ONE coalesced Telegram ping per
``gate_pending_summary_telegram_dedup_minutes`` window — and a re-ping
inside the dedup window only when the queue grew by strictly more than
``gate_pending_summary_telegram_growth_threshold`` new gates.

Discord gets a low-noise queue-status message every cycle when
``gate_pending_summary_discord_per_cycle=true`` (default). That keeps
the spam channel monitoring useful without adding Telegram noise.

How it runs each brain cycle (~5 min):

1. Read every tunable from ``app_settings`` (DB-configurable, no
   redeploy needed).
2. Internal cadence gate: only do the actual SELECT/notify when the
   last ENTRY into "do real work" was at least
   ``gate_pending_summary_poll_interval_minutes`` ago. Default 60 min
   matches the issue spec ("once per hour"). The Discord per-cycle
   ping IS still emitted on cycles in between (cheap status, no
   Telegram).
3. ``SELECT count + oldest pending gate`` from ``post_approval_gates``.
4. If count > 0 AND oldest is older than ``min_age_minutes`` AND the
   dedup gate allows (last ping was outside the dedup window OR queue
   grew > growth threshold) → fire ONE coalesced Telegram via
   ``notify_operator``.
5. If ``discord_per_cycle=true`` → emit a low-noise Discord queue-status
   on every cycle (count + oldest age + "queue is empty" when zero).

Design parity with the rest of the brain:

- DB-configurable through ``app_settings`` — see migration
  ``20260506_134100_seed_gate_pending_summary_app_settings.py``.
- Standalone module: only stdlib + asyncpg. No ``SiteConfig`` import —
  brain reads settings via direct ``pool.fetchval`` calls (matches
  ``brain/backup_watcher.py`` and ``brain/gate_auto_expire_probe.py``).
- Mirrors those probes' lifecycle: a ``run_gate_pending_summary_probe``
  entry point with injectable ``now_fn``/``notify_fn``/``discord_fn``
  seams for unit tests; module-level dedup state with a
  ``_reset_state()`` test hook.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified for tests
    from brain.operator_notifier import notify_operator

logger = logging.getLogger("brain.gate_pending_summary")


# ---------------------------------------------------------------------------
# App_settings keys — every tunable lives in the DB so an operator can
# adjust without redeploying the brain. Defaults below match the
# 20260506_134100 migration so the probe behaves consistently whether
# the seed migration ran or not (degraded-but-safe defaults).
# ---------------------------------------------------------------------------

ENABLED_KEY = "gate_pending_summary_enabled"
POLL_INTERVAL_MINUTES_KEY = "gate_pending_summary_poll_interval_minutes"
MIN_AGE_MINUTES_KEY = "gate_pending_summary_min_age_minutes"
TELEGRAM_DEDUP_MINUTES_KEY = "gate_pending_summary_telegram_dedup_minutes"
TELEGRAM_GROWTH_THRESHOLD_KEY = "gate_pending_summary_telegram_growth_threshold"
DISCORD_PER_CYCLE_KEY = "gate_pending_summary_discord_per_cycle"

DEFAULT_ENABLED = True
DEFAULT_POLL_INTERVAL_MINUTES = 60
DEFAULT_MIN_AGE_MINUTES = 60
DEFAULT_TELEGRAM_DEDUP_MINUTES = 60
DEFAULT_TELEGRAM_GROWTH_THRESHOLD = 3
DEFAULT_DISCORD_PER_CYCLE = True

# How often the registry-driven probe path should call this probe. Brain
# default cycle is 5 minutes; we run every cycle but the inner cadence
# gate (gate_pending_summary_poll_interval_minutes) decides whether to
# do a "real" pass vs. a Discord-only status ping. So this interval is
# the FLOOR — bumping it higher just delays both kinds of pings.
PROBE_INTERVAL_SECONDS = 5 * 60


# ---------------------------------------------------------------------------
# Module-level state — persists for the lifetime of the brain process so
# the dedup window survives across cycles without needing a new DB table
# or a row mutation. Reset at process restart, which is the right
# behaviour: a brain restart usually accompanies an operator action and
# they probably don't want stale dedup state suppressing the next page.
# Tests call ``_reset_state()`` between scenarios.
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {
    "last_real_pass_at": None,         # datetime — last "do work" cycle
    "last_telegram_at": None,          # datetime — last coalesced Telegram
    "last_telegram_count": 0,          # int — queue size when last paged
}


def _reset_state() -> None:
    """Test hook — clear the dedup memory."""
    _state["last_real_pass_at"] = None
    _state["last_telegram_at"] = None
    _state["last_telegram_count"] = 0


# ---------------------------------------------------------------------------
# app_settings reads — brain is standalone so we hit the DB directly.
# Each helper degrades to its default when the row is missing or the
# fetch raises, mirroring the pattern in brain/backup_watcher.py.
# ---------------------------------------------------------------------------


async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[GATE_SUMMARY] Could not read %s from app_settings: %s "
            "— using default %r",
            key, exc, default,
        )
        return default
    if val is None:
        return default
    return val


def _coerce_bool(val: Any, default: bool) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def _coerce_int(val: Any, default: int) -> int:
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


async def _read_config(pool: Any) -> dict[str, Any]:
    """Pull every probe tunable in one helper.

    Returns a dict with all six settings resolved + coerced.
    """
    enabled = _coerce_bool(
        await _read_setting(pool, ENABLED_KEY, "true"),
        DEFAULT_ENABLED,
    )
    poll_interval_minutes = _coerce_int(
        await _read_setting(pool, POLL_INTERVAL_MINUTES_KEY, DEFAULT_POLL_INTERVAL_MINUTES),
        DEFAULT_POLL_INTERVAL_MINUTES,
    )
    min_age_minutes = _coerce_int(
        await _read_setting(pool, MIN_AGE_MINUTES_KEY, DEFAULT_MIN_AGE_MINUTES),
        DEFAULT_MIN_AGE_MINUTES,
    )
    telegram_dedup_minutes = _coerce_int(
        await _read_setting(pool, TELEGRAM_DEDUP_MINUTES_KEY, DEFAULT_TELEGRAM_DEDUP_MINUTES),
        DEFAULT_TELEGRAM_DEDUP_MINUTES,
    )
    telegram_growth_threshold = _coerce_int(
        await _read_setting(pool, TELEGRAM_GROWTH_THRESHOLD_KEY, DEFAULT_TELEGRAM_GROWTH_THRESHOLD),
        DEFAULT_TELEGRAM_GROWTH_THRESHOLD,
    )
    discord_per_cycle = _coerce_bool(
        await _read_setting(pool, DISCORD_PER_CYCLE_KEY, "true"),
        DEFAULT_DISCORD_PER_CYCLE,
    )

    return {
        "enabled": enabled,
        "poll_interval_minutes": poll_interval_minutes,
        "min_age_minutes": min_age_minutes,
        "telegram_dedup_minutes": telegram_dedup_minutes,
        "telegram_growth_threshold": telegram_growth_threshold,
        "discord_per_cycle": discord_per_cycle,
    }


# ---------------------------------------------------------------------------
# Pending-queue scan
# ---------------------------------------------------------------------------


async def _select_pending_summary(
    pool: Any,
    *,
    now_utc: datetime,
) -> dict[str, Any]:
    """Return ``{count, oldest_created_at, oldest_age_seconds}`` for all
    ``post_approval_gates`` rows in ``state='pending'``.

    Returns ``count=0`` when the queue is empty (oldest fields are
    ``None`` in that case).
    """
    row = await pool.fetchrow(
        """
        SELECT COUNT(*) AS count,
               MIN(created_at) AS oldest_created_at
          FROM post_approval_gates
         WHERE state = 'pending'
        """,
    )
    if row is None:
        return {"count": 0, "oldest_created_at": None, "oldest_age_seconds": None}
    count = int(row["count"] or 0)
    oldest = row["oldest_created_at"]
    oldest_age_seconds: Optional[float] = None
    if isinstance(oldest, datetime) and count > 0:
        try:
            oldest_age_seconds = max(0.0, (now_utc - oldest).total_seconds())
        except Exception:  # noqa: BLE001
            oldest_age_seconds = None
    return {
        "count": count,
        "oldest_created_at": oldest,
        "oldest_age_seconds": oldest_age_seconds,
    }


# ---------------------------------------------------------------------------
# Default Discord-only emitter — wraps brain_daemon.send_discord with the
# discord_ops_webhook_url so the per-cycle status ping lands in the spam
# channel. Tests inject a mock and skip the import dance.
# ---------------------------------------------------------------------------


async def _default_discord_send(message: str, *, pool: Any) -> None:
    """Send to the Discord ops webhook only — no Telegram fan-out.

    Best-effort; never raises. Mirrors the resolution path used by
    ``brain.brain_daemon.notify`` but routes ONLY to ops Discord so the
    per-cycle status ping doesn't double up with the rare Telegram
    coalesced page.
    """
    # Lazy imports so the module is unit-testable without the brain
    # daemon's full dependency chain.
    try:  # flat import — container runtime
        from brain_daemon import _read_app_setting, send_discord  # type: ignore
    except ImportError:  # pragma: no cover — package-qualified for tests
        try:
            from brain.brain_daemon import _read_app_setting, send_discord  # type: ignore
        except ImportError:
            logger.debug(
                "[GATE_SUMMARY] brain_daemon helpers unavailable — "
                "skipping Discord status ping"
            )
            return

    try:
        ops_url = await _read_app_setting(
            pool,
            "discord_ops_webhook_url",
            default="",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[GATE_SUMMARY] discord_ops_webhook_url read failed: %s", exc,
        )
        return

    if not ops_url:
        logger.debug(
            "[GATE_SUMMARY] discord_ops_webhook_url not configured — "
            "skipping Discord status ping"
        )
        return

    try:
        await send_discord(message, webhook_url=ops_url, pool=pool)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[GATE_SUMMARY] Discord status ping failed: %s", exc,
        )


# ---------------------------------------------------------------------------
# Top-level probe entry point — called once per brain cycle.
# ---------------------------------------------------------------------------


NotifyFn = Callable[..., Any]
DiscordFn = Callable[[str], Awaitable[None]]


async def run_gate_pending_summary_probe(
    pool: Any,
    *,
    now_fn: Optional[Callable[[], datetime]] = None,
    notify_fn: Optional[NotifyFn] = None,
    discord_fn: Optional[DiscordFn] = None,
) -> dict[str, Any]:
    """Single execution of the gate-pending-summary probe.

    Args:
        pool: asyncpg pool for app_settings + post_approval_gates.
        now_fn: ``() -> aware datetime`` — defaults to ``datetime.now(UTC)``.
            Tests inject a fixed clock so dedup math is deterministic.
        notify_fn: operator notifier callable. Defaults to
            :func:`brain.operator_notifier.notify_operator`. Used to
            send the single coalesced Telegram message.
        discord_fn: Discord-only emitter for the per-cycle status ping.
            Defaults to a wrapper around
            :func:`brain.brain_daemon.send_discord` with the
            ``discord_ops_webhook_url``. Tests inject a mock to capture
            calls without hitting the network.

    Returns a structured summary suitable for inclusion in the cycle's
    ``probe_results`` map.
    """
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    notify_fn = notify_fn or notify_operator

    config = await _read_config(pool)
    if not config["enabled"]:
        return {
            "ok": True,
            "status": "disabled",
            "count": 0,
            "telegram_sent": False,
            "discord_sent": False,
            "detail": (
                f"Gate pending summary disabled "
                f"(app_settings.{ENABLED_KEY}=false)"
            ),
        }

    now_utc = now_fn()
    poll_interval_minutes = int(config["poll_interval_minutes"])
    min_age_minutes = int(config["min_age_minutes"])
    telegram_dedup_minutes = int(config["telegram_dedup_minutes"])
    telegram_growth_threshold = int(config["telegram_growth_threshold"])
    discord_per_cycle = bool(config["discord_per_cycle"])

    # Internal cadence gate. The brain cycle is ~5 min but the issue
    # spec says "once per hour". When this branch short-circuits we
    # still optionally emit the Discord per-cycle status ping below.
    last_pass = _state.get("last_real_pass_at")
    do_real_pass = True
    if isinstance(last_pass, datetime):
        elapsed = (now_utc - last_pass).total_seconds()
        if elapsed < poll_interval_minutes * 60:
            do_real_pass = False

    if not do_real_pass:
        # Discord per-cycle is the only thing that can fire — but it
        # needs a fresh count, so do a cheap SELECT regardless and
        # short-circuit before the Telegram logic below.
        if not discord_per_cycle:
            return {
                "ok": True,
                "status": "skipped_interval",
                "count": 0,
                "telegram_sent": False,
                "discord_sent": False,
                "detail": (
                    f"Within poll interval "
                    f"({poll_interval_minutes} min) — skipped."
                ),
            }
        # Fall through to the SELECT + Discord-only path. Telegram is
        # blocked for this cycle (do_real_pass=False).

    # SELECT — small, indexed query (the gate spine has a partial index
    # on (post_id, ordinal) WHERE state='pending', see migration 0131).
    try:
        summary = await _select_pending_summary(pool, now_utc=now_utc)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[GATE_SUMMARY] SELECT for pending count failed: %s", exc,
            exc_info=True,
        )
        return {
            "ok": False,
            "status": "select_failed",
            "count": 0,
            "telegram_sent": False,
            "discord_sent": False,
            "detail": f"SELECT failed: {type(exc).__name__}: {str(exc)[:160]}",
        }

    count = int(summary["count"])
    oldest_age_seconds: Optional[float] = summary["oldest_age_seconds"]

    # ----- Discord per-cycle status ping (low-noise) -----
    discord_sent = False
    if discord_per_cycle:
        if discord_fn is None:
            async def _wired_discord(message: str) -> None:
                await _default_discord_send(message, pool=pool)
            effective_discord = _wired_discord
        else:
            effective_discord = discord_fn

        if count == 0:
            status_msg = "[gate-queue] empty (0 posts pending review)."
        else:
            oldest_hours = (oldest_age_seconds or 0) / 3600.0
            status_msg = (
                f"[gate-queue] {count} post"
                f"{'s' if count != 1 else ''} pending review — "
                f"oldest is {oldest_hours:.1f}h old."
            )
        try:
            await effective_discord(status_msg)
            discord_sent = True
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[GATE_SUMMARY] discord status ping failed: %s", exc,
            )

    # If we're inside the cadence window, stop here — the Discord ping
    # (when enabled) was the only deliverable for this cycle.
    if not do_real_pass:
        return {
            "ok": True,
            "status": "discord_only",
            "count": count,
            "telegram_sent": False,
            "discord_sent": discord_sent,
            "detail": (
                f"Within poll interval — Discord status emitted "
                f"({'sent' if discord_sent else 'skipped'}); "
                f"Telegram suppressed."
            ),
        }

    # We're in a "real" pass: record it for the next cadence check
    # regardless of whether we end up firing a Telegram ping.
    _state["last_real_pass_at"] = now_utc

    # ----- Telegram coalesced ping -----
    telegram_sent = False
    telegram_skip_reason = ""

    if count == 0:
        telegram_skip_reason = "queue_empty"
    elif oldest_age_seconds is None or oldest_age_seconds < min_age_minutes * 60:
        # Within the grace window — operator gets to clear the queue
        # themselves before we escalate.
        telegram_skip_reason = "within_grace_window"
    else:
        # Dedup math: ping iff outside dedup window OR queue grew enough.
        last_telegram_at = _state.get("last_telegram_at")
        last_telegram_count = int(_state.get("last_telegram_count") or 0)

        outside_dedup = True
        if isinstance(last_telegram_at, datetime):
            elapsed = (now_utc - last_telegram_at).total_seconds()
            outside_dedup = elapsed >= telegram_dedup_minutes * 60

        growth = count - last_telegram_count
        # "STRICTLY MORE than threshold" — see migration docstring + tests.
        growth_triggered = growth > telegram_growth_threshold

        if not (outside_dedup or growth_triggered):
            telegram_skip_reason = (
                f"deduped(elapsed_within_window, growth={growth} "
                f"<= threshold={telegram_growth_threshold})"
            )
        else:
            oldest_hours = (oldest_age_seconds or 0) / 3600.0
            title = (
                f"📋 {count} post{'s' if count != 1 else ''} "
                "pending review"
            )
            detail_lines = [
                f"{count} post{'s' if count != 1 else ''} pending review "
                f"— oldest is {oldest_hours:.1f} hours old.",
                "Use `poindexter gates list --pending` to triage.",
            ]
            if growth_triggered and not outside_dedup:
                detail_lines.append(
                    f"(Re-paged inside dedup window because the queue grew "
                    f"by {growth} since last ping > threshold "
                    f"{telegram_growth_threshold}.)"
                )
            try:
                notify_fn(
                    title=title,
                    detail="\n".join(detail_lines),
                    source="brain.gate_pending_summary",
                    severity="warning",
                )
                telegram_sent = True
                _state["last_telegram_at"] = now_utc
                _state["last_telegram_count"] = count
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[GATE_SUMMARY] notify_fn failed: %s", exc,
                )
                telegram_skip_reason = f"notify_failed: {type(exc).__name__}"

    if telegram_sent:
        logger.info(
            "[GATE_SUMMARY] coalesced Telegram ping fired (count=%d)", count,
        )
    elif count > 0:
        logger.debug(
            "[GATE_SUMMARY] queue=%d, no Telegram (%s)",
            count, telegram_skip_reason or "see-status",
        )
    else:
        logger.debug("[GATE_SUMMARY] queue empty")

    status = (
        "telegram_fired" if telegram_sent
        else ("queue_empty" if count == 0 else "deduped_or_grace")
    )
    return {
        "ok": True,
        "status": status,
        "count": count,
        "oldest_age_seconds": oldest_age_seconds,
        "telegram_sent": telegram_sent,
        "telegram_skip_reason": telegram_skip_reason,
        "discord_sent": discord_sent,
        "min_age_minutes": min_age_minutes,
        "telegram_dedup_minutes": telegram_dedup_minutes,
        "telegram_growth_threshold": telegram_growth_threshold,
        "detail": (
            f"queue={count}, telegram_sent={telegram_sent}, "
            f"discord_sent={discord_sent}"
        ),
    }


# ---------------------------------------------------------------------------
# Probe-Protocol adapter — for the registry-driven path. Mirrors
# GateAutoExpireProbe so this slots into the same registry.
# ---------------------------------------------------------------------------


class GatePendingSummaryProbe:
    """Probe-Protocol-compatible wrapper around
    :func:`run_gate_pending_summary_probe`.
    """

    name: str = "gate_pending_summary"
    description: str = (
        "Coalesces the per-flip Telegram noise from the HITL gate spine "
        "into ONE 'N posts pending review' page per "
        "gate_pending_summary_telegram_dedup_minutes window (default 60). "
        "Emits a low-noise Discord queue-status every cycle for the spam "
        "channel. Closes the notification-batching half of #338."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult

        summary = await run_gate_pending_summary_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", ""),
            metrics={
                "status": summary.get("status"),
                "count": summary.get("count", 0),
                "telegram_sent": summary.get("telegram_sent", False),
                "discord_sent": summary.get("discord_sent", False),
            },
            severity=(
                "warning" if summary.get("telegram_sent") else "info"
            ),
        )
