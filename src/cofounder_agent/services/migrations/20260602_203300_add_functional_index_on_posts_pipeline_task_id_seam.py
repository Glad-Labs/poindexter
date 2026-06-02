"""Migration 20260602_203300: add functional index on posts->pipeline_tasks seam

ISSUE: Glad-Labs/poindexter#628

``posts.metadata->>'pipeline_task_id'`` is the canonical seam back from a
published post to its source ``pipeline_tasks`` row (added 2026-05-28).
``scheduled_publisher`` / ``/go-live`` / the promote-existing-approved
path all read this key to keep ``pipeline_tasks.status`` in lockstep with
``posts.status``. Without a functional index, every such lookup is a
sequential scan over the JSONB sidecar. This adds a partial functional
btree index on the expression, scoped to rows where the key is present
(the only rows that participate in the seam). Idempotent
``CREATE INDEX IF NOT EXISTS``; ``down()`` drops it.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Create the functional partial index on the pipeline_task_id seam.

    Safe to re-run — ``IF NOT EXISTS`` no-ops if the index already exists.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_posts_pipeline_task_id
                ON posts ((metadata->>'pipeline_task_id'))
                WHERE metadata ->> 'pipeline_task_id' IS NOT NULL
            """
        )
    logger.info(
        "add_functional_index_on_posts_pipeline_task_id_seam: "
        "created idx_posts_pipeline_task_id (if absent)"
    )


async def down(pool) -> None:
    """Drop the functional index added by up()."""
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_posts_pipeline_task_id")
    logger.info(
        "add_functional_index_on_posts_pipeline_task_id_seam down: "
        "dropped idx_posts_pipeline_task_id"
    )
