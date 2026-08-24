"""Migration 20260824_142853: add media_pipeline_cap_reset_count to pipeline_tasks

ISSUE: Glad-Labs/poindexter#1021

The media re-dispatch cap-reset self-heal (media_reconciliation, 2026-07-03)
re-arms a cap-wedged video task whenever render infra probes healthy, at most
once per cooldown (default 24h) — bounded per day but unbounded across days.
A permanently-failing render (the 2026-08-24 case: an ambient-bed path frozen
as a per-container /tmp tempfile) therefore re-wedged and got re-armed daily
for five straight days, and the repeated model-load churn helped push the
host into global OOM. This column counts lifetime resets per task so
media_reconciliation can stop re-arming at
``media_redispatch_cap_reset_max_resets`` and surface the task as permanently
wedged instead.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Add the lifetime cap-reset counter (idempotent)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE pipeline_tasks
                ADD COLUMN IF NOT EXISTS media_pipeline_cap_reset_count
                    integer NOT NULL DEFAULT 0
            """
        )
    logger.info(
        "Migration add_media_pipeline_cap_reset_count_to_pipeline_tasks: applied"
    )


async def down(pool) -> None:
    """Drop the counter column."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE pipeline_tasks
                DROP COLUMN IF EXISTS media_pipeline_cap_reset_count
            """
        )
    logger.info(
        "Migration add_media_pipeline_cap_reset_count_to_pipeline_tasks: reverted"
    )
