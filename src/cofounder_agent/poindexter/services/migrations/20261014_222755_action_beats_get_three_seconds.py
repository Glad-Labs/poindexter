"""Migration: action beats get three seconds

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

story_action_beat_seconds is the silence the voiced track leaves for an
action beat, so the shot showing it has screen time. At 1.5 s the Midnight
Patch payoff (Mara's spell turning the jumpsuit into a gown, the hover-coach
landing) was a flash (2026-10-14); each action is now its own animated shot
and gets 3.0 s. A changed default never reaches an existing row, so this
moves the row, and only while it still holds the old default.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

OLD = "1.5"
NEW = "3.0"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = now() "
            "WHERE key = 'story_action_beat_seconds' AND value = $1",
            OLD, NEW,
        )
    logger.info("Migration action_beats_get_three_seconds: %s", result)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = $1, updated_at = now() "
            "WHERE key = 'story_action_beat_seconds' AND value = $2",
            OLD, NEW,
        )
