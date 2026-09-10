"""Migration 20260808_033456: social draft scheduling — scheduled_at + index.

Social promos could only go out NOW: ``SocialDraftsService.approve_draft``
calls Postiz with ``{"type": "now"}``, so the only way to time a post was the
Postiz UI's own scheduler. That queue lives outside Poindexter — invisible to
the console, to Grafana, and to the CLI, and un-cancellable from any of them.

This adds the local queue: ``scheduled_at`` holds the fire time and a new
``scheduled`` status marks a draft as decided-but-waiting.

Why the queue stays on OUR side rather than pushing ``type: schedule`` to
Postiz: ``approve_draft`` gates on the promoted post actually being live
(``posts.status='published'``) and repairs the URL against the live row
first. Handing a future-dated post to Postiz would run that gate at schedule
time, hours before it fires — a post whose publish slipped or that got pulled
would still send its promo, at a 404. Firing locally re-checks the gate at
the moment of posting.

INDEX WIDENING (the load-bearing half — do not drop it):
``ux_social_post_drafts_active_key`` and its sibling guards in
``create_draft`` / ``existing_draft_keys`` treat a key as live only while
status is ``pending``/``failed`` (plus ``posted`` in the query predicates).
A ``scheduled`` draft is very much live, so leaving it out of the partial
index means a finalize re-run — a preview_gate regen loop, a checkpoint
restore, a task retry — would see the key as free, insert a SECOND draft,
and both would eventually post. That is exactly the duplicate-promo bug
poindexter#833 fixed for ``pending``; adding a status without widening the
index re-opens it.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE social_post_drafts
            ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ
            """
        )
        # Partial index over the still-live statuses. Recreated rather than
        # altered — Postgres has no ALTER INDEX ... SET WHERE.
        await conn.execute(
            "DROP INDEX IF EXISTS ux_social_post_drafts_active_key"
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_social_post_drafts_active_key
            ON social_post_drafts (
                pipeline_task_id,
                platform,
                COALESCE(platform_config->>'subreddit', '')
            )
            WHERE status IN ('pending', 'scheduled', 'failed')
            """
        )
        # Due-sweep support: ScheduleSocialDraftsJob polls for
        # status='scheduled' AND scheduled_at <= now() every minute.
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_social_post_drafts_scheduled_at
            ON social_post_drafts (scheduled_at)
            WHERE status = 'scheduled'
            """
        )
        logger.info(
            "Migration %s: social_post_drafts.scheduled_at ready; active-key "
            "index now covers 'scheduled'.",
            __name__,
        )


async def down(pool) -> None:
    """Revert to the pre-scheduling shape.

    Scheduled drafts are returned to ``pending`` BEFORE the column drops, so
    a rollback surfaces them in the operator's action inbox for a manual
    decision rather than stranding rows in a status nothing fires.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE social_post_drafts SET status = 'pending' "
            "WHERE status = 'scheduled'"
        )
        await conn.execute(
            "DROP INDEX IF EXISTS idx_social_post_drafts_scheduled_at"
        )
        await conn.execute(
            "DROP INDEX IF EXISTS ux_social_post_drafts_active_key"
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_social_post_drafts_active_key
            ON social_post_drafts (
                pipeline_task_id,
                platform,
                COALESCE(platform_config->>'subreddit', '')
            )
            WHERE status IN ('pending', 'failed')
            """
        )
        await conn.execute(
            "ALTER TABLE social_post_drafts DROP COLUMN IF EXISTS scheduled_at"
        )
