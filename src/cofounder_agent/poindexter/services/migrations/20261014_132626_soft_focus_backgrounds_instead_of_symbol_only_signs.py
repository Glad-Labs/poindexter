"""Migration: soft focus backgrounds instead of symbol-only signs

ISSUE: Glad-Labs/poindexter#1134 (creative studio)

video_reference_text_rule asked the edit model for signs that "show only
glowing shapes and symbols, never letters or words". In the "Midnight Patch"
storyboard re-cut (2026-10-14) every talking-frame draw still had legible
signs. The default now asks for a softly out-of-focus background instead, the
look the cast portraits already have. A changed default never reaches a row
that already exists, so this moves the row, and only while it still holds the
old default: an operator's own wording stays.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

OLD = (
    "Every sign, screen, label and hologram shows only glowing shapes and "
    "symbols, never letters or words."
)
NEW = (
    "The background is softly out of focus, so every sign, screen and "
    "hologram reads as a blurred glow of colour, with no letters or words."
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = now() "
            "WHERE key = 'video_reference_text_rule' AND value = $1",
            OLD, NEW,
        )
    logger.info("Migration soft_focus_backgrounds: %s", result)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = $1, updated_at = now() "
            "WHERE key = 'video_reference_text_rule' AND value = $2",
            OLD, NEW,
        )
