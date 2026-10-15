"""Migration: a character in a shared shot appears exactly once

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

"The Battery Drop" storyboard (2026-10-15) drew two Cinders beside Bolt in
the closing shot, in two draws running. The rule the edit model gets for a
shot with two or more characters said each keeps their own look; it now
also says each appears exactly once, and the redraw with it drew one. A
changed default never reaches an existing row, so this moves the row only
while it still holds the old default.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

KEY = "video_reference_identity_rule"
OLD = 'Each character keeps exactly the face, skin, markings and clothes of their own picture; nothing on one character appears on another.'
NEW = 'Each character appears exactly once in the picture, and keeps exactly the face, skin, markings and clothes of their own picture; nothing on one character appears on another.'


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE app_settings SET value = $3, updated_at = now() WHERE key = $1 AND value = $2",
            KEY, OLD, NEW,
        )
        logger.info("Migration a_character_in_a_shared_shot_appears_exactly_once: %s", result)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = now() WHERE key = $1 AND value = $3",
            KEY, OLD, NEW,
        )
