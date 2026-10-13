"""Migration: Gate-2 approval rows for videos that have no blog post

ISSUE: Glad-Labs/poindexter#1140 (creative studio phase 6)

A creative-studio cartoon is a video with no article behind it. It persists
as a task-keyed ``media_assets`` row and never gets a post, but
``media_approvals`` was keyed on ``(post_id, medium)`` with ``post_id NOT
NULL``. So the video could not enter the review queue, and nothing could
ever approve or publish it.

This keys a row on its **subject**: the post when there is one, else the
pipeline task. ``task_id`` is new and nullable, ``post_id`` becomes
nullable, and ``subject_id`` is a stored generated column,
``COALESCE(post_id::text, task_id)``, so a single key serves both kinds of
row. The primary key moves to ``(subject_id, medium)``. A ``UNIQUE
(post_id, medium)`` constraint replaces the old primary key, so every
existing ``ON CONFLICT (post_id, medium)`` keeps its arbiter. Existing rows
all carry a post, so their ``subject_id`` is their post id and nothing a
caller already passes changes meaning.

Idempotent: a second run finds ``subject_id`` and does nothing.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_HAS_SUBJECT_SQL = """
    SELECT 1 FROM information_schema.columns
     WHERE table_name = 'media_approvals' AND column_name = 'subject_id'
"""

_UP_STATEMENTS = (
    "ALTER TABLE media_approvals DROP CONSTRAINT IF EXISTS media_approvals_pkey",
    "ALTER TABLE media_approvals ALTER COLUMN post_id DROP NOT NULL",
    "ALTER TABLE media_approvals ADD COLUMN IF NOT EXISTS task_id varchar(255) "
    "REFERENCES pipeline_tasks(task_id) ON DELETE CASCADE",
    "ALTER TABLE media_approvals ADD COLUMN subject_id text "
    "GENERATED ALWAYS AS (COALESCE(post_id::text, task_id)) STORED",
    "ALTER TABLE media_approvals ADD CONSTRAINT media_approvals_pkey "
    "PRIMARY KEY (subject_id, medium)",
    "ALTER TABLE media_approvals ADD CONSTRAINT media_approvals_post_id_medium_key "
    "UNIQUE (post_id, medium)",
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if await conn.fetchval(_HAS_SUBJECT_SQL):
            logger.info("Migration media_approvals_for_standalone_videos: already applied")
            return
        async with conn.transaction():
            for statement in _UP_STATEMENTS:
                await conn.execute(statement)
    logger.info("Migration media_approvals_for_standalone_videos: applied")


async def down(pool) -> None:
    """Back to post-keyed rows. Standalone rows have no post, so they go."""
    async with pool.acquire() as conn:
        if not await conn.fetchval(_HAS_SUBJECT_SQL):
            return
        async with conn.transaction():
            await conn.execute("DELETE FROM media_approvals WHERE post_id IS NULL")
            await conn.execute(
                "ALTER TABLE media_approvals DROP CONSTRAINT IF EXISTS media_approvals_post_id_medium_key",
            )
            await conn.execute("ALTER TABLE media_approvals DROP CONSTRAINT IF EXISTS media_approvals_pkey")
            await conn.execute("ALTER TABLE media_approvals DROP COLUMN subject_id")
            await conn.execute("ALTER TABLE media_approvals DROP COLUMN IF EXISTS task_id")
            await conn.execute("ALTER TABLE media_approvals ALTER COLUMN post_id SET NOT NULL")
            await conn.execute(
                "ALTER TABLE media_approvals ADD CONSTRAINT media_approvals_pkey PRIMARY KEY (post_id, medium)",
            )
