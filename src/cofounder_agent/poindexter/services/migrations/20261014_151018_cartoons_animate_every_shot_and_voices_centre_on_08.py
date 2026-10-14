"""Migration: cartoons animate every shot, and their voices centre on 0.8

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

Matt, 2026-10-14: a cartoon should be animated "start to finish", not still
pans for the shots between the talking ones; and of Bolt's voice, "0.9 is
too high, 0.7 too low, maybe 0.8 is the right setting for these cartoon
voices". The defaults moved; a changed default never reaches a row that
already exists, so this moves each row, and only while it still holds the
old default: an operator's own value stays.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MOVES = (
    ("story_min_animated_shot_seconds", "2.5", "0"),
    ("story_line_intensity_offsets", "low=-0.2,medium=0,high=0.2", "low=-0.1,medium=0,high=0.1"),
    ("story_cast_energy_exaggeration", "low=0.65,medium=0.85,high=1.0", "low=0.7,medium=0.8,high=0.85"),
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, old, new in MOVES:
            result = await conn.execute(
                "UPDATE app_settings SET value = $3, updated_at = now() WHERE key = $1 AND value = $2",
                key, old, new,
            )
            logger.info("Migration cartoons_animate_every_shot: %s %s", key, result)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for key, old, new in MOVES:
            await conn.execute(
                "UPDATE app_settings SET value = $2, updated_at = now() WHERE key = $1 AND value = $3",
                key, old, new,
            )
