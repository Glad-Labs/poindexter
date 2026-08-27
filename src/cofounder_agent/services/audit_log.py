"""
Pipeline Audit Log Service

Lightweight, fire-and-forget audit logging for every pipeline state change,
decision, and event.  All writes are non-blocking (asyncio.create_task) so
the audit log never slows down the content pipeline.

Usage:
    from services.audit_log import AuditLogger, audit_log_bg

    # With an explicit instance (preferred when you have the pool):
    audit = AuditLogger(local_pool)
    await audit.log("task_created", "content_router", {"topic": "AI trends"}, task_id=tid)

    # Fire-and-forget helper (uses the global singleton):
    audit_log_bg("generation_complete", "content_generation_flow", {"model": "ollama/qwen3.5:35b"})
"""

import asyncio
import functools
import math
from datetime import datetime
from typing import Any, Optional

from asyncpg import Pool

from services.logger_config import get_logger

logger = get_logger(__name__)

# Severities that MUST NOT vanish silently (#303). A warn/critical finding's
# audit_log row IS the signal the findings_alert_router later reads, so a
# dropped write would silently kill the downstream alert. These escalate to
# error-level (Sentry-visible) instead of debug/warning.
_LOUD_SEVERITIES = frozenset({"warn", "warning", "critical"})


def _is_loud(severity: str | None) -> bool:
    return (severity or "").lower() in _LOUD_SEVERITIES

# ---------------------------------------------------------------------------
# Global singleton — set once via init_global_audit_logger()
# ---------------------------------------------------------------------------
_global_audit_logger: Optional["AuditLogger"] = None

# In-flight fire-and-forget writes scheduled by audit_log_bg(). Tracked so a
# short-lived pool owner (a Prefect flow subprocess that builds+closes its own
# pool per run) can flush them via drain_pending_writes() BEFORE closing the
# pool — otherwise a write scheduled moments before teardown races pool.close()
# and dies with InterfaceError('pool is closing'), losing the finding the #303
# loud-drop path exists to protect (GlitchTip #863). Holding the reference also
# stops the task being garbage-collected mid-flight (asyncio best practice).
_pending_writes: set[asyncio.Task] = set()


def init_global_audit_logger(pool: Pool, *, quiet: bool = False) -> "AuditLogger":
    """Initialise (or replace) the module-level AuditLogger singleton.

    ``quiet=True`` logs the init at debug instead of info — for short-lived
    CLI contexts where an info line on every ``poindexter <cmd>`` invocation
    would be stderr noise (worker/daemon startup keeps the info line).
    """
    global _global_audit_logger
    _global_audit_logger = AuditLogger(pool)
    if quiet:
        logger.debug("Global AuditLogger initialised")
    else:
        logger.info("Global AuditLogger initialised")
    return _global_audit_logger


def reset_global_audit_logger(pool: Pool | None = None) -> bool:
    """Clear the module-level AuditLogger singleton.

    With ``pool`` given, only clears when the current global logger writes to
    that exact pool — so a teardown seam (e.g. ``close_cli_pool``) can't
    clobber a logger some other context re-initialised with its own pool in
    the meantime (``cli/pipeline.py`` builds a full ``DatabaseService``, which
    re-inits the global with the service's pool). With ``pool=None`` the reset
    is unconditional (test cleanup).

    Returns True when the global was cleared.
    """
    global _global_audit_logger
    if _global_audit_logger is None:
        return False
    if pool is not None and _global_audit_logger.pool is not pool:
        return False
    _global_audit_logger = None
    return True


def get_audit_logger() -> Optional["AuditLogger"]:
    """Return the global AuditLogger, or None if not yet initialised."""
    return _global_audit_logger


# ---------------------------------------------------------------------------
# Fire-and-forget convenience wrapper
# ---------------------------------------------------------------------------

def audit_log_bg(
    event_type: str,
    source: str,
    details: dict[str, Any] | None = None,
    task_id: str | None = None,
    severity: str = "info",
) -> None:
    """Schedule an audit-log insert as a background task.

    Safe to call even if the global logger has not been initialised yet — the
    event is silently dropped with a debug-level message.
    """
    al = _global_audit_logger
    if al is None:
        _log_dropped_event("global AuditLogger not initialised", event_type, source, severity)
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _log_dropped_event("no running event loop", event_type, source, severity)
        return

    task = loop.create_task(
        al.log(event_type, source, details, task_id=task_id, severity=severity)
    )
    # Register the in-flight write so drain_pending_writes() can flush it before
    # a short-lived pool closes (GlitchTip #863), and so the task can't be GC'd
    # mid-flight. The discard callback keeps the set from leaking completed tasks.
    _pending_writes.add(task)
    task.add_done_callback(_pending_writes.discard)
    # Swallow exceptions so a failed log write never propagates, but keep
    # warn/critical drops LOUD (#303) — bind the context the callback needs.
    task.add_done_callback(
        functools.partial(
            _handle_audit_task_exception,
            event_type=event_type,
            source=source,
            severity=severity,
        )
    )


def _log_dropped_event(reason: str, event_type: str, source: str, severity: str) -> None:
    """Log an audit event that could not even be scheduled.

    info-severity drops stay quiet (debug); warn/critical drops escalate to
    error so they surface in Sentry rather than vanishing (#303).
    """
    if _is_loud(severity):
        logger.error(
            "DROPPED %s finding (%s): event=%s source=%s — will NOT reach the alert pipeline",
            severity, reason, event_type, source,
        )
    else:
        logger.debug("audit_log_bg dropped event %s (%s)", event_type, reason)


def _handle_audit_task_exception(
    task: asyncio.Task,
    *,
    event_type: str = "",
    source: str = "",
    severity: str = "info",
) -> None:
    """Callback to log (not raise) audit-write failures that escaped log()."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    if _is_loud(severity):
        logger.error(
            "Audit background write FAILED for %s finding event=%s source=%s: %s",
            severity, event_type, source, exc, exc_info=exc,
        )
    else:
        logger.warning("Audit log background write failed: %s", exc)


async def drain_pending_writes(timeout: float = 5.0) -> None:
    """Flush in-flight ``audit_log_bg`` writes before their pool is closed.

    A ``warn``/``critical`` finding emitted fire-and-forget moments before a
    short-lived pool's teardown (e.g. the spend-throttle engage finding in a
    Prefect flow subprocess that builds+closes its own pool per run) is only
    *scheduled* — ``loop.create_task`` does not run it synchronously. If the
    owner closes the pool before the task runs, the write dies with
    ``asyncpg InterfaceError('pool is closing')`` and the finding is lost —
    exactly the loss the #303 loud-drop path exists to prevent (GlitchTip #863).

    Owners that close a pool the audit logger writes to (``DatabaseService.close``)
    call this FIRST so the writes land while the pool is still open. Best-effort
    and bounded: waits up to ``timeout`` seconds for the current in-flight set,
    then cancels any stragglers so teardown never hangs on a wedged write. Never
    raises — task exceptions are already retrieved by the per-task done-callback.
    """
    pending = [t for t in _pending_writes if not t.done()]
    if not pending:
        return
    _, still_pending = await asyncio.wait(pending, timeout=timeout)
    for task in still_pending:
        task.cancel()


def _null_non_finite(value: Any) -> Any:
    """Recursively replace non-finite floats (NaN / ±inf) with None.

    ``json.dumps`` defaults to ``allow_nan=True`` and emits the literals
    ``NaN`` / ``Infinity`` — valid Python, invalid JSON (RFC 8259) — which
    Postgres's ``::jsonb`` cast rejects with InvalidTextRepresentationError,
    losing the whole audit row (the ragas_score write failures). ``None``
    serializes as JSON ``null``, which jsonb accepts and downstream
    ``(details->>'key')::float`` reads surface as SQL NULL.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _null_non_finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_null_non_finite(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Core AuditLogger class
# ---------------------------------------------------------------------------

class AuditLogger:
    """Async audit logger backed by the local PostgreSQL ``audit_log`` table."""

    INSERT_SQL = """
        INSERT INTO audit_log (event_type, source, task_id, details, severity)
        VALUES ($1, $2, $3, $4::jsonb, $5)
    """

    def __init__(self, pool: Pool):
        self.pool = pool

    # -- write ---------------------------------------------------------------

    async def log(
        self,
        event_type: str,
        source: str,
        details: dict[str, Any] | None = None,
        task_id: str | None = None,
        severity: str = "info",
    ) -> None:
        """Insert a single audit-log row.

        This is intentionally ``async`` so callers can ``await`` when they need
        guaranteed delivery, or wrap in ``audit_log_bg()`` for fire-and-forget.
        """
        import json

        try:
            details_json = json.dumps(details or {}, allow_nan=False)
        except ValueError:
            # Non-finite floats (NaN/±inf) are legal Python but illegal JSON —
            # without allow_nan=False, json.dumps emits the literal ``NaN``
            # and Postgres's ::jsonb cast rejects the whole row. Degrade the
            # bad leaves to null so the event still lands, and say so: the
            # emitter has a bug worth fixing at the source.
            logger.warning(
                "audit details for event=%s source=%s contained non-finite "
                "floats — sanitized to null",
                event_type, source,
            )
            details_json = json.dumps(_null_non_finite(details or {}), allow_nan=False)
        try:
            await self.pool.execute(
                self.INSERT_SQL,
                event_type,
                source,
                task_id,
                details_json,
                severity,
            )
        except Exception:
            # Never let audit logging crash the caller — but never let a
            # warn/critical finding vanish silently either (#303). The
            # audit_log row IS the signal findings_alert_router reads, so a
            # dropped write kills the downstream alert.
            sev = (severity or "").lower()
            if sev == "critical":
                logger.error(
                    "CRITICAL finding lost on audit write: event=%s source=%s "
                    "task=%s — paging operator out-of-band",
                    event_type, source, task_id,
                    exc_info=True,
                )
                # The router/dispatcher chain depends on this same DB, so page
                # via the notify chain (Telegram->Discord->alerts.log->stderr),
                # which is independent of the audit DB that just failed.
                await self._page_operator_out_of_band(event_type, source, details)
            elif sev in ("warn", "warning"):
                logger.error(
                    "WARN finding lost on audit write: event=%s source=%s task=%s",
                    event_type, source, task_id,
                    exc_info=True,
                )
            else:
                logger.warning(
                    "Failed to write audit log event=%s source=%s task=%s",
                    event_type, source, task_id,
                    exc_info=True,
                )

    async def _page_operator_out_of_band(
        self,
        event_type: str,
        source: str,
        details: dict[str, Any] | None,
    ) -> None:
        """Last-resort page when a CRITICAL finding's audit row failed to persist.

        Best-effort and self-contained: imported lazily (operator_notify does
        not depend on this module, but keep the import local to avoid any boot
        ordering surprises) and never raises.
        """
        try:
            from services.integrations.operator_notify import notify_operator

            title = details.get("title") if isinstance(details, dict) else None
            message = (
                "[audit-write-failed] A CRITICAL finding could not be persisted "
                "and will NOT reach the alert pipeline.\n"
                f"event={event_type} source={source}"
                + (f"\n{title}" if title else "")
            )
            await notify_operator(message, critical=True)
        except Exception:
            logger.error(
                "Out-of-band operator page ALSO failed for lost critical finding "
                "event=%s source=%s", event_type, source,
                exc_info=True,
            )

    # -- read ----------------------------------------------------------------

    async def query(
        self,
        event_type: str | None = None,
        source: str | None = None,
        task_id: str | None = None,
        severity: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query the audit log with optional filters.

        Returns rows as plain dicts, ordered newest-first.
        """
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if event_type is not None:
            conditions.append(f"event_type = ${idx}")
            params.append(event_type)
            idx += 1
        if source is not None:
            conditions.append(f"source = ${idx}")
            params.append(source)
            idx += 1
        if task_id is not None:
            conditions.append(f"task_id = ${idx}")
            params.append(task_id)
            idx += 1
        if severity is not None:
            conditions.append(f"severity = ${idx}")
            params.append(severity)
            idx += 1
        if since is not None:
            conditions.append(f"timestamp >= ${idx}")
            params.append(since)
            idx += 1

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM audit_log{where} ORDER BY timestamp DESC LIMIT ${idx}"  # nosec B608  # conditions built from local literals; values use $N params
        params.append(limit)

        rows = await self.pool.fetch(sql, *params)
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Module-level helper — no class instantiation required.
# ---------------------------------------------------------------------------

_AUDIT_SUMMARY_SQL = """
SELECT event_type, severity, COUNT(*) AS count
FROM audit_log
WHERE timestamp > NOW() - $1 * INTERVAL '1 hour'
GROUP BY event_type, severity
ORDER BY count DESC
"""


async def query_summary(pool: Any, hours: int = 24) -> list[dict[str, Any]]:
    """Return aggregate audit-log event counts grouped by (event_type, severity).

    ``hours`` specifies the look-back window in hours (default 24).
    """
    rows = await pool.fetch(_AUDIT_SUMMARY_SQL, hours)
    return [dict(r) for r in rows]
