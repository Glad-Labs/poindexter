"""Cross-process mirror of GPU-lock waiters — the console's queue view.

P0 (observe) of poindexter#914. ``gpu_scheduler`` inserts a ``gpu_queue`` row
when a caller starts a CONTENDED wait (the uncontended fast path stays
zero-I/O) and deletes it when the wait ends — acquire, timeout, or
cancellation alike. Rows are ephemeral state, not history; the release-time
stats (``gpu_lease_stats``) are the durable record.

Crash orphans: a process that dies mid-wait leaves its row behind. Every
``enqueue`` piggybacks a reap of rows older than ``_ORPHAN_HORIZON_S`` —
comfortably past the lock's 900s acquire ceiling, so a live waiter can never
be reaped.

Same lazy-connection, best-effort posture as ``gpu_lease_stats``: the mirror
must never gate or slow the lock lifecycle, and it is hermetic under pytest
(``_connect`` no-ops there — see that module's docstring).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from services.gpu_lease_stats import _connect
from services.logger_config import get_logger

logger = get_logger(__name__)

# > gpu_lock_acquire_timeout_seconds (900) + slack: nothing legitimately
# waits this long, so anything older is a dead process's leftover.
_ORPHAN_HORIZON_S = 1200


async def enqueue(
    owner: str,
    *,
    model: str | None = None,
    phase: str | None = None,
    priority: str = "pipeline",
) -> str | None:
    """Record a waiter; returns the row id (None when mirroring unavailable)."""
    row_id = str(uuid.uuid4())
    try:
        conn = await _connect()
        if conn is None:
            return None
        try:
            await conn.execute(
                "DELETE FROM gpu_queue WHERE enqueued_at < now() - ($1::int * interval '1 second')",
                _ORPHAN_HORIZON_S,
            )
            await conn.execute(
                "INSERT INTO gpu_queue (id, pid, owner, model, phase, priority) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                uuid.UUID(row_id),
                os.getpid(),
                owner,
                model,
                phase,
                priority,
            )
        finally:
            await conn.close()
        return row_id
    except Exception:
        # silent-ok: queue mirroring is observability; a missed row only
        # under-reports the console's waiter list, never affects the lock.
        logger.debug("[gpu_queue_mirror] enqueue failed", exc_info=True)
        return None


async def dequeue(row_id: str | None) -> None:
    """Remove a waiter row. Accepts None (failed enqueue) as a no-op."""
    if not row_id:
        return
    try:
        conn = await _connect()
        if conn is None:
            return
        try:
            await conn.execute("DELETE FROM gpu_queue WHERE id = $1", uuid.UUID(row_id))
        finally:
            await conn.close()
    except Exception:
        # silent-ok: same posture as enqueue; the orphan reap is the backstop.
        logger.debug("[gpu_queue_mirror] dequeue failed", exc_info=True)


async def list_waiters() -> list[dict[str, Any]]:
    """Current waiter rows, oldest first. Empty on any failure (honest-empty)."""
    try:
        conn = await _connect()
        if conn is None:
            return []
        try:
            rows = await conn.fetch(
                "SELECT id, pid, owner, model, phase, priority, enqueued_at, "
                "       EXTRACT(EPOCH FROM (now() - enqueued_at)) AS waiting_s "
                "FROM gpu_queue ORDER BY enqueued_at ASC"
            )
        finally:
            await conn.close()
        return [dict(r) for r in rows]
    except Exception:
        # silent-ok: an unreadable queue renders as empty in the console —
        # the freshness chip there is the staleness signal, not an exception.
        logger.debug("[gpu_queue_mirror] list_waiters failed", exc_info=True)
        return []
