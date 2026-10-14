"""Migration: talking shots rotate camera setups

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

Every talking frame was one head-and-shoulders close-up facing the viewer,
so a dialogue cut portrait to portrait (Matt on the Midnight Patch v2
storyboard, 2026-10-14: "It feels flat just going from portrait to
portrait"). story_talking_frame_template now takes {framing}, one of
story_talking_framings, rotating through each speaker's lines. A changed
default never reaches an existing row, so this moves the row, and only while
it still holds the old default.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

OLD = (
    "{style}, head-and-shoulders close-up of {name} alone in the frame, "
    "facing the viewer and talking, the picture cropped just below the "
    "shoulders, in {setting}"
)
NEW = "{style}, {framing}, alone in the frame and talking, in {setting}"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = now() "
            "WHERE key = 'story_talking_frame_template' AND value = $1",
            OLD, NEW,
        )
    logger.info("Migration talking_shots_rotate_camera_setups: %s", result)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = $1, updated_at = now() "
            "WHERE key = 'story_talking_frame_template' AND value = $2",
            OLD, NEW,
        )
