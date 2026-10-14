"""Migration 20261014_010359_add_creative_drafts_table_for_chat_drafting: add creative drafts table for chat drafting

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

Chat drafting, phase 1 (Matt, 2026-10-14): draft a cast or a screenplay in
the console chat, look at it, revise it by talking, and keep it only when it
is right. A draft is not a persona and not a pipeline task, so it needs its
own home: one row per version (``parent_id`` points at the version it
revises), the request that made it, the payload the card shows, and a status
the card polls (``working`` -> ``ready`` | ``failed``; ``kept`` /
``discarded`` once the operator decides). Drafts are built in the background
because drawing a cast outlasts a chat turn's deadline.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS creative_drafts (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                conversation_id uuid,
                parent_id uuid REFERENCES creative_drafts(id) ON DELETE SET NULL,
                kind text NOT NULL CHECK (kind IN ('cast', 'screenplay')),
                version integer NOT NULL DEFAULT 1,
                status text NOT NULL DEFAULT 'working'
                    CHECK (status IN ('working', 'ready', 'failed', 'kept', 'discarded')),
                request jsonb NOT NULL DEFAULT '{}'::jsonb,
                payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                note text NOT NULL DEFAULT '',
                error text NOT NULL DEFAULT '',
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creative_drafts_conversation "
            "ON creative_drafts (conversation_id, created_at DESC)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_creative_drafts_status "
            "ON creative_drafts (status) WHERE status = 'working'"
        )
    logger.info("Migration add_creative_drafts_table_for_chat_drafting: applied")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS creative_drafts")
