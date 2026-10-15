"""Migration: a keyframe's second drawing adds no one and keeps the light

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

The Circuit and the Song storyboard (2026-10-15): "she walks further away"
drew a second Cinder walking beside her, twice, and "the warm room" turned a
blue workshop orange. story_keyframe_after_template now ends by saying no one
is added and the light stays. A changed default never reaches an existing
row, so this moves the row only while it still holds the old default.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

KEY = "story_keyframe_after_template"
OLD = 'Redraw this exact picture with the same framing, characters, place, light, colours and art style. The only change: {change}.'
NEW = 'Redraw this exact picture with the same framing, characters, place, light, colours and art style. The only change: {change}. Everyone in the picture appears once, as before, and no one is added; the light and colours stay exactly as they are.'


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE app_settings SET value = $3, updated_at = now() WHERE key = $1 AND value = $2",
            KEY, OLD, NEW,
        )
        logger.info("Migration keyframe_second_drawing_adds_no_one: %s", result)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = now() WHERE key = $1 AND value = $3",
            KEY, OLD, NEW,
        )
