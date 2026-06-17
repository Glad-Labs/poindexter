"""Migration 20260617_161119: add pipeline_tasks.media_pipeline_redispatch_count.

ISSUE: Glad-Labs/poindexter#689   (video-side cutover, glad-labs-stack#1460)

The video-side cutover makes the Stage-2 pipeline the sole video producer. The
media_reconciliation watchdog therefore stops generating video directly and
instead re-dispatches Stage-2 for a drifted post by clearing the source task's
``media_pipeline_dispatched_at``. This integer caps how many times a single task
may be re-dispatched so a permanently-failing render can't loop forever; the
reconciliation re-dispatch only fires while the count is below
``app_settings.media_pipeline_redispatch_max`` (default 3) and increments it on
each clear.

Metadata-only on PG11+ (NOT NULL + constant DEFAULT, no table rewrite).
Idempotent (IF NOT EXISTS) + light-env safe; a no-op on a fresh baseline DB
other than adding the column.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_UP = """
ALTER TABLE pipeline_tasks
    ADD COLUMN IF NOT EXISTS media_pipeline_redispatch_count integer NOT NULL DEFAULT 0
"""

_DOWN = """
ALTER TABLE pipeline_tasks
    DROP COLUMN IF EXISTS media_pipeline_redispatch_count
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_UP)
    logger.info(
        "Migration 20260617_161119: added pipeline_tasks.media_pipeline_redispatch_count"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DOWN)
    logger.info(
        "Migration 20260617_161119: dropped pipeline_tasks.media_pipeline_redispatch_count"
    )
