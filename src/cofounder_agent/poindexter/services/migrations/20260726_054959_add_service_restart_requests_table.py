"""Migration 20260726_054959_add_service_restart_requests_table: add service_restart_requests table

ISSUE: Glad-Labs/poindexter#909

The operator console's Restart button (Service Health panel) never called
the backend — it was pure client-side theater regardless of live/mock mode.
The worker container has no docker.sock (only poindexter-brain-daemon does,
per the self-healing firefighter's docker_restart_container), so a restart
click can't act directly from the worker's own process. This table is the
intent queue that closes the gap: the worker inserts a ``pending`` row on
``POST /api/services/{container}/restart``, brain's independent poll loop
claims it (``FOR UPDATE SKIP LOCKED``, mirroring pipeline_tasks' claim
pattern), restarts the container via the SAME helper the firefighter's
``restart_container`` remediation action already uses, and writes the
outcome back so the console can show a real result instead of an optimistic
fake one.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS service_restart_requests (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                container TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_by TEXT NOT NULL DEFAULT 'console',
                detail TEXT,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                claimed_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                CONSTRAINT service_restart_requests_status_check
                    CHECK (status IN ('pending', 'claimed', 'done', 'failed'))
            )
            """
        )
        # Brain's claim query filters WHERE status = 'pending' ORDER BY
        # requested_at — a partial index keeps that cheap regardless of how
        # many completed rows accumulate (this table is never pruned by a
        # retention job; rows are small and low-volume — manual restarts,
        # not a high-frequency write path).
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_service_restart_requests_pending
                ON service_restart_requests (requested_at)
                WHERE status = 'pending'
            """
        )
    logger.info("Migration add_service_restart_requests_table: applied")


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS service_restart_requests")
    logger.info("Migration add_service_restart_requests_table: reverted")
