"""Migration: enable Speaches TTS for podcast narration (flip podcast_tts_enabled=true).

edge-tts (GPL-3.0) was removed as a dependency. Speaches/Kokoro is the
sole TTS path. Enable podcast_tts_enabled=true so existing installs
start generating podcast audio immediately.

Operators that don't run poindexter-speaches should set
podcast_tts_enabled=false after applying this migration.
"""

from __future__ import annotations
import logging
logger = logging.getLogger(__name__)

async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = 'true' WHERE key = 'podcast_tts_enabled'"
        )
    logger.info("Migration enable_podcast_tts_speaches: flipped podcast_tts_enabled=true")

async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = 'false' WHERE key = 'podcast_tts_enabled'"
        )
    logger.info("Migration enable_podcast_tts_speaches down: reset podcast_tts_enabled=false")
