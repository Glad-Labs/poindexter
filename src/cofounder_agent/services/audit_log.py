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


def init_global_audit_logger(pool: Pool) -> "AuditLogger":
    """Initialise (or replace) the module-level AuditLogger singleton."""
    global _global_audit_logger
    _global_audit_logger = AuditLogger(pool)
    logger.info("Global AuditLogger initialised")
    return _global_audit_logger


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

        details_json = json.dumps(details or {})
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
