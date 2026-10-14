"""Migration: talking frames describe the crop, not the hands

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

story_talking_frame_template said "hands out of frame". Naming the hands drew
them: in the "Midnight Patch" storyboard (2026-10-14, v3) human hands reached
up from the bottom of the talking frames, even under Bolt, a robot mouse. The
default now describes the crop ("the picture cropped just below the
shoulders"). A changed default never reaches an existing row, so this moves
the row, and only while it still holds the old default.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

OLD = (
    "{style}, head-and-shoulders close-up of {name} alone in the frame, "
    "facing the viewer and talking, hands out of frame, in {setting}"
)
NEW = (
    "{style}, head-and-shoulders close-up of {name} alone in the frame, "
    "facing the viewer and talking, the picture cropped just below the "
    "shoulders, in {setting}"
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = now() "
            "WHERE key = 'story_talking_frame_template' AND value = $1",
            OLD, NEW,
        )
    logger.info("Migration talking_frames_describe_the_crop: %s", result)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = $1, updated_at = now() "
            "WHERE key = 'story_talking_frame_template' AND value = $2",
            OLD, NEW,
        )
