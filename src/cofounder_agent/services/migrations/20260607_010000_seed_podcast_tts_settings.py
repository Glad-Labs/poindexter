"""Migration: seed podcast TTS settings for Speaches narration.

Wires services/tts_service.py into the generate_media_scripts stage
(glad-labs-stack#621). Default-off (podcast_tts_enabled=false) so
existing installs are unaffected until an operator opts in.

The Speaches container (poindexter-speaches) must be running and
reachable at the configured URL. The compose-internal URL
(http://speaches:8000/v1) works from the worker container; the
host-side URL (http://host.docker.internal:8001/v1) works from the
local Python process.

Idempotent — ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KEYS = [
    (
        "podcast_tts_enabled",
        "false",
        "media",
        "Enable TTS narration for podcast scripts via Speaches. Converts "
        "the LLM-generated podcast script to a .wav file using Kokoro. "
        "Requires poindexter-speaches container to be running.",
    ),
    (
        "podcast_tts_base_url",
        "http://speaches:8000/v1",
        "media",
        "Speaches OpenAI-compatible base URL for podcast TTS. Compose-internal "
        "URL by default. Use http://host.docker.internal:8001/v1 if running "
        "the worker natively on the host.",
    ),
    (
        "podcast_tts_voice",
        "bf_emma",
        "media",
        "Kokoro voice id for podcast narration. Options: bf_emma, bf_isabella, "
        "am_michael, etc. (matches voice_agent_tts_voice ids).",
    ),
    (
        "podcast_tts_model",
        "speaches-ai/Kokoro-82M-v1.0-ONNX",
        "media",
        "Kokoro model id passed to Speaches for podcast TTS. Keep in sync "
        "with voice_agent_tts_model unless a different model is preferred "
        "for long-form narration.",
    ),
    (
        "podcast_tts_format",
        "wav",
        "media",
        "Output audio format for podcast narration files. Options: wav, mp3, "
        "opus, flac. wav is lossless and universally playable.",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value, category, description in _KEYS:
            await conn.execute(
                """
                INSERT INTO app_settings
                  (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, false, true)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
    logger.info(
        "Migration seed_podcast_tts_settings: seeded %d keys", len(_KEYS),
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            [k for k, *_ in _KEYS],
        )
    logger.info("Migration seed_podcast_tts_settings down: removed")
