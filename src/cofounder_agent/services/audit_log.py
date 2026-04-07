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
    audit_log_bg("generation_complete", "task_executor", {"model": "ollama/qwen3.5:35b"})
"""

import asyncio
from services.logger_config import get_logger
from datetime import datetime
from typing import Any, Dict, List, Optional

from asyncpg import Pool

logger = get_logger(__name__)

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
    details: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    severity: str = "info",
) -> None:
    """Schedule an audit-log insert as a background task.

    Safe to call even if the global logger has not been initialised yet — the
    event is silently dropped with a debug-level message.
    """
    al = _global_audit_logger
    if al is None:
        logger.debug("audit_log_bg called before global AuditLogger init — dropping event %s", event_type)
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("audit_log_bg: no running event loop — dropping event %s", event_type)
        return

    task = loop.create_task(
        al.log(event_type, source, details, task_id=task_id, severity=severity)
    )
    # Swallow exceptions so a failed log write never propagates
    task.add_done_callback(_handle_audit_task_exception)


def _handle_audit_task_exception(task: asyncio.Task) -> None:
    """Callback to silently log (not raise) audit-write failures."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
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
        details: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
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
            # Never let audit logging crash the caller
            logger.warning(
                "Failed to write audit log event=%s source=%s task=%s",
                event_type, source, task_id,
                exc_info=True,
            )

    # -- read ----------------------------------------------------------------

    async def query(
        self,
        event_type: Optional[str] = None,
        source: Optional[str] = None,
        task_id: Optional[str] = None,
        severity: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
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
        sql = f"SELECT * FROM audit_log{where} ORDER BY timestamp DESC LIMIT ${idx}"
        params.append(limit)

        rows = await self.pool.fetch(sql, *params)
        return [dict(r) for r in rows]
