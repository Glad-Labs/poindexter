"""Migration: close-up talking frames draw from the model sheet

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

"The Battery Drop" storyboard (2026-10-15): both of Bolt's close-ups drew a
dark-faced mouse in a sweater. The close-up was the one talking setup still
drawn from the portrait, and Bolt's portrait shows him in his car, so the
model guessed the rest of him. The model sheet's front view is the design
itself, facing the viewer, so every setup now draws from it ("full_body"
names that reference). A character with no sheet still uses the portrait.
A changed default never reaches an existing row, so this moves the row only
while it still holds the old default.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

KEY = "story_talking_framings"
OLD = '[{"framing": "head-and-shoulders close-up of {name}, facing the viewer, the picture cropped just below the shoulders", "full_body": false}, {"framing": "medium shot of {name} from the waist up, turned three-quarters to one side", "full_body": true}, {"framing": "medium-wide shot of {name} standing in the scene from the knees up, turned slightly to one side", "full_body": true}]'
NEW = '[{"framing": "head-and-shoulders close-up of {name}, facing the viewer, the picture cropped just below the shoulders", "full_body": true}, {"framing": "medium shot of {name} from the waist up, turned three-quarters to one side", "full_body": true}, {"framing": "medium-wide shot of {name} standing in the scene from the knees up, turned slightly to one side", "full_body": true}]'


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE app_settings SET value = $3, updated_at = now() WHERE key = $1 AND value = $2",
            KEY, OLD, NEW,
        )
        logger.info("Migration close_up_talking_frames_draw_from_the_model_sheet: %s", result)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = now() WHERE key = $1 AND value = $3",
            KEY, OLD, NEW,
        )
