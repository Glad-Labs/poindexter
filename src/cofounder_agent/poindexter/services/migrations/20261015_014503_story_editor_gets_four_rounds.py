"""Migration: story editor gets four rounds

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

story_edit_rounds was 2, and "The Boy Who Cried Wolf" adaptation
(2026-10-14) ended its second round with a first-time reader still lost at
five moments. The rounds stop as soon as the reader follows it and the
editor calls it ready, so a higher cap only costs a round where one is
needed. A changed default never reaches an existing row, so this moves the
row, and only while it still holds the old default.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

OLD = "2"
NEW = "4"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = now() "
            "WHERE key = 'story_edit_rounds' AND value = $1",
            OLD, NEW,
        )
    logger.info("Migration story_editor_gets_four_rounds: %s", result)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = $1, updated_at = now() "
            "WHERE key = 'story_edit_rounds' AND value = $2",
            OLD, NEW,
        )
