"""Add ``pipeline_tasks.media_pipeline_dispatched_at`` — Stage-2 trigger marker.

The ``dispatch_media_pipeline`` scheduled job (the Gate-1 → Stage-2 trigger,
#689 Plan 7) claims an approved piece by stamping this column BEFORE running
``media_pipeline``, so a concurrent cycle or a worker restart never
re-dispatches the same piece. ``NULL`` = not yet dispatched; a timestamp = a
``media_pipeline`` run was kicked off for that task.

This is distinct from the per-asset ``media_approvals`` "dispatched" tracking
(Gate-2 platform delivery) — this column tracks Stage-2 *generation* kickoff.

Additive + idempotent (``ADD COLUMN IF NOT EXISTS``) — safe on prod where the
table already exists, and a no-op on re-run. Imports only stdlib so the
migrations-smoke CI step can apply it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks "
            "ADD COLUMN IF NOT EXISTS media_pipeline_dispatched_at "
            "timestamp with time zone"
        )
    logger.info(
        "Migration add_pipeline_tasks_media_pipeline_dispatched_at up: "
        "ensured pipeline_tasks.media_pipeline_dispatched_at column (#689 Plan 7)."
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks "
            "DROP COLUMN IF EXISTS media_pipeline_dispatched_at"
        )
    logger.info(
        "Migration add_pipeline_tasks_media_pipeline_dispatched_at down: "
        "dropped pipeline_tasks.media_pipeline_dispatched_at column."
    )
