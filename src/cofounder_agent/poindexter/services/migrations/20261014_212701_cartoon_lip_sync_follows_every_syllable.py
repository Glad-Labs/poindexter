"""Migration: cartoon lip sync follows every syllable

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

video_cast_render_prompt asked for "small natural mouth movements". On the
Midnight Patch render (2026-10-14) a robot mouse's tiny mouth barely moved
while he talked, and his lens eyes grew pupils that came and went. An A/B on
two of his lines held his eyes steady once his looks were named (the new
video_cast_appearance_template) and moved his mouth with "mouth movements that
follow every syllable of the speech". A changed default never reaches an
existing row, so this moves the row, and only while it still holds the old
default.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

OLD = (
    "{display_name} talks in the scene with a natural, lively expression, "
    "gentle head movement, hands relaxed and still, small natural mouth "
    "movements synchronized with the speech, consistent character design"
)
NEW = (
    "{display_name} talks in the scene with a natural, lively expression, "
    "gentle head movement, hands relaxed and still, mouth movements that "
    "follow every syllable of the speech, consistent character design"
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = now() "
            "WHERE key = 'video_cast_render_prompt' AND value = $1",
            OLD, NEW,
        )
    logger.info("Migration cartoon_lip_sync_follows_every_syllable: %s", result)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = $1, updated_at = now() "
            "WHERE key = 'video_cast_render_prompt' AND value = $2",
            OLD, NEW,
        )
