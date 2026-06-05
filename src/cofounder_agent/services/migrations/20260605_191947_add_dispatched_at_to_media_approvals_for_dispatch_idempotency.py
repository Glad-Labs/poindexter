"""Migration 20260605_191947: add dispatched_at + dispatch_success to media_approvals.

Closes Glad-Labs/poindexter#558 — 59 approved videos were stranded because
backfill_videos.py only called _dispatch_video_publishers() for freshly-
generated videos (not for videos that already existed on disk). Without any
dispatch-tracking column, a replay pass couldn't distinguish "approved but
never sent" from "approved and already uploaded" — re-running the dispatch
would re-upload on every cycle.

Fix: add two columns:
  - dispatched_at   TIMESTAMPTZ  — when the first successful dispatch fired
  - dispatch_success BOOLEAN     — whether the last dispatch attempt succeeded

NULL dispatched_at = never dispatched (needs delivery).
Non-null dispatched_at = delivered; skip in future cycles.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE media_approvals
                ADD COLUMN IF NOT EXISTS dispatched_at      TIMESTAMPTZ DEFAULT NULL,
                ADD COLUMN IF NOT EXISTS dispatch_success   BOOLEAN     DEFAULT NULL;

            COMMENT ON COLUMN media_approvals.dispatched_at IS
                'Timestamp of the first successful platform dispatch (NULL = not yet dispatched).';
            COMMENT ON COLUMN media_approvals.dispatch_success IS
                'Result of the last dispatch attempt (NULL if never attempted, true = succeeded).';
            """
        )
        logger.info("Migration add_dispatched_at_to_media_approvals: applied")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE media_approvals
                DROP COLUMN IF EXISTS dispatched_at,
                DROP COLUMN IF EXISTS dispatch_success;
            """
        )
        logger.info("Migration add_dispatched_at_to_media_approvals down: reverted")
