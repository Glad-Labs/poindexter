"""Migration 20260807_154227: bounded budget for the Stage-2 outage un-claim.

ISSUE: Glad-Labs/poindexter#995

``dispatch_media_pipeline`` un-claims a piece (NULLs
``media_pipeline_dispatched_at``) when a run raises AND the post-failure
infra probe is unhealthy, deliberately WITHOUT burning one of the task's
bounded ``media_pipeline_redispatch_count`` attempts — an outage must not
wedge a piece at the cap (six posts did exactly that before 2026-07-03).

The gap that made it unbounded: the post-failure probe cannot tell "infra
died independently" from "this render's own VRAM footprint is why the probe
fails". wan holds 23-27 GB mid-render, so a failure leaves the card below
``media_render_min_free_vram_gb`` and every self-inflicted failure is
classified as an outage victim — re-claimed for free, forever. Task
8faf3617 rode that loop to 40 findings in 24h (27% of all findings) with
``media_pipeline_redispatch_count`` still reading 0.

This column gives that path its own budget so the outage tolerance survives
while the loop terminates. It is deliberately NOT the redispatch counter:
sharing one would re-create the 2026-07-03 wedge, where a genuine multi-hour
outage burns every in-flight piece's cap in minutes.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE pipeline_tasks
                ADD COLUMN IF NOT EXISTS media_pipeline_unclaim_count
                    INTEGER NOT NULL DEFAULT 0
            """
        )
    logger.info("Migration add_media_pipeline_unclaim_count: applied")


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE pipeline_tasks
                DROP COLUMN IF EXISTS media_pipeline_unclaim_count
            """
        )
    logger.info("Migration add_media_pipeline_unclaim_count: reverted")
