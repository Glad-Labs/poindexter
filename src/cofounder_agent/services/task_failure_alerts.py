"""Task-failure alert dedup + severity routing.

Glad-Labs/poindexter#370 — gate task-failure alerts so a single
fast-failing task can't blast 8 Telegram pages in 35 seconds.

Two responsibilities, one module:

1. **Dedup** — keyed on ``(task_id, sha256(error_message)[:16])``,
   suppress repeats inside ``task_failure_alert_dedup_window_seconds``
   (default 900). Both an in-memory LRU and a persistent
   ``task_failure_alerts`` row are checked, so the suppression survives
   a worker restart (a fast crash-loop on the worker would otherwise
   reset the LRU and re-page on every restart).

2. **Severity routing** — read ``task_failure_alert_severity`` from
   app_settings (``'discord'`` (default) or ``'telegram'``). Routine
   task failures route to Discord per ``feedback_telegram_vs_discord``
   — Telegram is reserved for the explicit critical-alert list (worker
   offline, GPU temp, cost overrun, failure-rate breach). The single
   bad-task case should not light up the operator's phone.

The helper is a tiny class — not a singleton — so callers can pass a
test-construction with a mock pool. ``send_failure_alert`` is the only
public entry point; it never raises.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)

# Module-level LRU. Bounded so a crash-looping pipeline can't blow out
# the worker's RAM. 4096 distinct (task_id, error_hash) pairs is
# generous: the operator will have triaged the storm long before that.
_LRU_MAX = 4096
_LRU: "OrderedDict[tuple[str, str], float]" = OrderedDict()
_LRU_LOCK = asyncio.Lock()


def _hash_error(error_message: str) -> str:
    """Stable short hash for the error string.

    16 hex chars (64 bits) is plenty — collision probability is
    negligible at the volumes a single worker produces, and the short
    form keeps the (task_id, error_hash) primary key index small.
    """
    if error_message is None:
        error_message = ""
    return hashlib.sha256(error_message.encode("utf-8", errors="replace")).hexdigest()[:16]


async def _lru_check_and_record(
    key: tuple[str, str],
    window_seconds: int,
    *,
    now: float | None = None,
) -> bool:
    """Return True if the key is still inside the dedup window.

    Side-effect: when False, records the current timestamp so the next
    call within the window is suppressed. The LRU is bounded; once
    capacity is reached the oldest entry is evicted.
    """
    if window_seconds <= 0:
        return False  # dedup disabled
    ts = now if now is not None else time.monotonic()
    async with _LRU_LOCK:
        last = _LRU.get(key)
        if last is not None and (ts - last) < window_seconds:
            # Touch so it stays MRU for the eviction policy.
            _LRU.move_to_end(key)
            return True
        _LRU[key] = ts
        _LRU.move_to_end(key)
        while len(_LRU) > _LRU_MAX:
            _LRU.popitem(last=False)
        return False


async def _persistent_check_and_record(
    pool: Any,
    task_id: str,
    error_hash: str,
    error_message: str,
    severity: str,
    window_seconds: int,
) -> bool:
    """Persistent dedup mirror — survives worker restarts.

    Returns True if the persistent row says the dedup window is still
    active. Always best-effort: any DB error is swallowed and the
    in-memory LRU result wins. The table is created by migration 0158;
    a missing table just means dedup falls back to the LRU.
    """
    if pool is None or window_seconds <= 0:
        return False
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT last_sent_at,
                       EXTRACT(EPOCH FROM (NOW() - last_sent_at))::float AS age_seconds
                  FROM task_failure_alerts
                 WHERE task_id = $1 AND error_hash = $2
                """,
                task_id, error_hash,
            )
            if row is not None and row["age_seconds"] is not None:
                if row["age_seconds"] < window_seconds:
                    # Still inside the window — bump the count but do
                    # NOT touch last_sent_at (would extend the window).
                    await conn.execute(
                        """
                        UPDATE task_failure_alerts
                           SET alert_count = alert_count + 1,
                               last_error  = $3,
                               last_severity = $4
                         WHERE task_id = $1 AND error_hash = $2
                        """,
                        task_id, error_hash,
                        (error_message or "")[:2000], severity,
                    )
                    return True
            # Either no row, or window expired — UPSERT a fresh marker
            # and let the alert through.
            await conn.execute(
                """
                INSERT INTO task_failure_alerts
                  (task_id, error_hash, last_sent_at, alert_count,
                   last_error, last_severity)
                VALUES ($1, $2, NOW(), 1, $3, $4)
                ON CONFLICT (task_id, error_hash) DO UPDATE
                  SET last_sent_at  = NOW(),
                      alert_count   = task_failure_alerts.alert_count + 1,
                      last_error    = EXCLUDED.last_error,
                      last_severity = EXCLUDED.last_severity
                """,
                task_id, error_hash,
                (error_message or "")[:2000], severity,
            )
            return False
    except Exception as e:
        # Missing table, pool down, etc. Do not fail the alert path.
        logger.debug(
            "[task_failure_alerts] persistent dedup check failed (non-fatal): %s",
            e,
        )
        return False


async def send_failure_alert(
    *,
    task_id: str,
    topic: str,
    error_message: str,
    pool: Any | None,
    get_setting,
) -> dict[str, Any]:
    """Send a deduped, severity-routed task-failure alert.

    Args:
        task_id: The failing task's UUID-as-string.
        topic: The task's topic (truncated for the message).
        error_message: Raw error text. Used to compute the dedup hash
            and shown to the operator.
        pool: asyncpg pool for the persistent dedup mirror. Pass None
            in tests / early-boot to fall back to LRU only.
        get_setting: Async ``(key, default) -> str`` resolver. Use the
            executor's existing ``_get_setting`` so the cache is shared.

    Returns:
        ``{"sent": bool, "channel": str, "deduped": bool, "reason": str}``.
        Never raises — operator notifications are best-effort.
    """
    out: dict[str, Any] = {
        "sent": False,
        "channel": "",
        "deduped": False,
        "reason": "",
    }

    # 1. Read the runtime config. Defaults match migration 0158 seeds.
    try:
        window_raw = await get_setting(
            "task_failure_alert_dedup_window_seconds", "900"
        )
        window_seconds = max(0, int(window_raw))
    except Exception:
        window_seconds = 900
    try:
        severity_raw = await get_setting(
            "task_failure_alert_severity", "discord"
        )
    except Exception:
        severity_raw = "discord"
    severity = (severity_raw or "discord").strip().lower()
    if severity not in ("discord", "telegram"):
        # Fail loud per feedback_no_silent_defaults — a typo here would
        # silently route to one channel; surface it loudly and fall
        # back to the safer choice (Discord, the spam channel).
        logger.error(
            "[task_failure_alerts] Invalid task_failure_alert_severity=%r — "
            "must be 'discord' or 'telegram'. Falling back to 'discord' to "
            "avoid spamming Telegram.",
            severity_raw,
        )
        severity = "discord"
    out["channel"] = severity

    # 2. Dedup check — both layers. Either layer saying "deduped" wins.
    error_hash = _hash_error(error_message or "")
    key = (str(task_id), error_hash)
    in_memory_dup = await _lru_check_and_record(key, window_seconds)
    persistent_dup = await _persistent_check_and_record(
        pool, str(task_id), error_hash, error_message or "",
        severity, window_seconds,
    )
    if in_memory_dup or persistent_dup:
        out["deduped"] = True
        out["reason"] = (
            f"Suppressed: same (task_id, error_hash) seen within "
            f"{window_seconds}s window"
        )
        logger.info(
            "[task_failure_alerts] Suppressed duplicate alert for task=%s "
            "(window=%ds, hash=%s)",
            str(task_id)[:8], window_seconds, error_hash,
        )
        return out

    # 3. Route. Discord = critical=False, Telegram = critical=True.
    msg = (
        f"Failed: \"{topic[:80]}\" - {(error_message or 'Unknown error')[:160]}\n"
        f"task_id: {str(task_id)[:8]}"
    )
    try:
        from services.integrations.operator_notify import notify_operator
        await notify_operator(msg, critical=(severity == "telegram"))
        out["sent"] = True
        out["reason"] = f"Routed to {severity}"
        logger.info(
            "[task_failure_alerts] Sent failure alert for task=%s via %s "
            "(window=%ds)",
            str(task_id)[:8], severity, window_seconds,
        )
    except Exception as e:
        out["reason"] = f"notify_operator raised: {e}"
        logger.warning(
            "[task_failure_alerts] notify_operator failed for task=%s: %s",
            str(task_id)[:8], e,
        )

    return out


def _reset_lru_for_tests() -> None:
    """Clear the module-level LRU. Tests only — never call from prod."""
    _LRU.clear()
