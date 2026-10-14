"""Migration: allow youtube package creative drafts

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

Chat drafting, phase 7: a finished cartoon's YouTube package (title,
description, tags and composed thumbnail) is drafted, revised by notes and
versioned like the cast, script and storyboard drafts, then kept onto the
video. The kind check from 20261014_014442 named only cast, screenplay and
storyboard.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE creative_drafts DROP CONSTRAINT IF EXISTS creative_drafts_kind_check")
        await conn.execute(
            "ALTER TABLE creative_drafts ADD CONSTRAINT creative_drafts_kind_check "
            "CHECK (kind IN ('cast', 'screenplay', 'storyboard', 'package'))"
        )
    logger.info("Migration allow_youtube_package_creative_drafts: applied")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM creative_drafts WHERE kind = 'package'")
        await conn.execute("ALTER TABLE creative_drafts DROP CONSTRAINT IF EXISTS creative_drafts_kind_check")
        await conn.execute(
            "ALTER TABLE creative_drafts ADD CONSTRAINT creative_drafts_kind_check "
            "CHECK (kind IN ('cast', 'screenplay', 'storyboard'))"
        )
