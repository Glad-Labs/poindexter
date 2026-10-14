"""Migration: allow storyboard creative drafts

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

Chat drafting, phase 2: a storyboard is a draft too, made from a script draft
(the scene's voiced lines plus a still per shot), revised by notes and
versioned like the cast and script drafts. The kind check from
20261014_010359 named only cast and screenplay.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE creative_drafts DROP CONSTRAINT IF EXISTS creative_drafts_kind_check")
        await conn.execute(
            "ALTER TABLE creative_drafts ADD CONSTRAINT creative_drafts_kind_check "
            "CHECK (kind IN ('cast', 'screenplay', 'storyboard'))"
        )
    logger.info("Migration allow_storyboard_creative_drafts: applied")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM creative_drafts WHERE kind = 'storyboard'")
        await conn.execute("ALTER TABLE creative_drafts DROP CONSTRAINT IF EXISTS creative_drafts_kind_check")
        await conn.execute(
            "ALTER TABLE creative_drafts ADD CONSTRAINT creative_drafts_kind_check "
            "CHECK (kind IN ('cast', 'screenplay'))"
        )
