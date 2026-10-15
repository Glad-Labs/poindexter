"""Migration: talking frames get the place and full-body views

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

"The Signal Glitch" storyboard (2026-10-15) drew most talking frames on a
street instead of the story's workshop: the frame prompt said only "in The
hub...", and the art style's "rain-slick streets" filled the gap. Its wider
setups drew Bolt's body from guesswork (gold, dark, plain), since the frames
used only the head-and-shoulders portrait. story_talking_frame_template now
takes {place} (the location's description), and each story_talking_framings
entry says whether it draws from the model sheet's full-body view. A changed
default never reaches an existing row, so this moves each row, and only
while it still holds the old default.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ROWS = (
    ("story_talking_frame_template", '{style}, {framing}, alone in the frame and talking, in {setting}', '{style}, {framing}, alone in the frame and talking, in {place}; {setting}'),
    ("story_talking_framings", '["head-and-shoulders close-up of {name}, facing the viewer, the picture cropped just below the shoulders", "medium shot of {name} from the waist up, turned three-quarters to one side", "medium-wide shot of {name} standing in the scene from the knees up, turned slightly to one side"]', '[{"framing": "head-and-shoulders close-up of {name}, facing the viewer, the picture cropped just below the shoulders", "full_body": false}, {"framing": "medium shot of {name} from the waist up, turned three-quarters to one side", "full_body": true}, {"framing": "medium-wide shot of {name} standing in the scene from the knees up, turned slightly to one side", "full_body": true}]'),
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, old, new in ROWS:
            result = await conn.execute(
                "UPDATE app_settings SET value = $3, updated_at = now() WHERE key = $1 AND value = $2",
                key, old, new,
            )
            logger.info("Migration talking_frames_get_the_place (%s): %s", key, result)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for key, old, new in ROWS:
            await conn.execute(
                "UPDATE app_settings SET value = $2, updated_at = now() WHERE key = $1 AND value = $3",
                key, old, new,
            )
