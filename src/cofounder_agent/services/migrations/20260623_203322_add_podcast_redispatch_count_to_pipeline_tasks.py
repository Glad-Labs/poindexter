"""Migration 20260623_203322_add_podcast_redispatch_count_to_pipeline_tasks: add podcast_redispatch_count to pipeline_tasks

The media-drift watchdog (``services/jobs/media_reconciliation.py``) is being
reworked so that, on a genuinely-missing podcast, it re-dispatches the gated
``podcast_pipeline`` (by clearing ``podcast_dispatched_at``) instead of
authoring a duplicate episode straight to R2. That re-dispatch needs a per-task
attempt cap so a permanently-failing podcast cannot loop forever.

This adds ``podcast_redispatch_count`` — the podcast twin of the existing
``media_pipeline_redispatch_count`` (video) column — defaulting to 0. The
watchdog bumps it on each re-dispatch and refuses to clear the marker once it
reaches ``app_settings.podcast_redispatch_max``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration — add the podcast re-dispatch attempt counter.

    Idempotent: ``ADD COLUMN IF NOT EXISTS`` no-ops on prod (and on the
    Phase-F baseline, which already ships ``media_pipeline_redispatch_count``
    but not this one) while adding the column on a fresh DB.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks "
            "ADD COLUMN IF NOT EXISTS podcast_redispatch_count integer NOT NULL DEFAULT 0"
        )
    logger.info(
        "Migration add_podcast_redispatch_count_to_pipeline_tasks: applied"
    )


async def down(pool) -> None:
    """Revert — drop the column added by ``up()``."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS podcast_redispatch_count"
        )
    logger.info(
        "Migration add_podcast_redispatch_count_to_pipeline_tasks: reverted"
    )
