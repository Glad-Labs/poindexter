"""Migration: seed audio_gen_engine settings for StableAudioOpenProvider.

Wires services/audio_gen_service.py + StableAudioOpenProvider into the
generate_media_scripts stage (glad-labs-stack#621). Default-off
(audio_gen_engine='') until the Stable Audio Open inference server is
running at port 9839 and the operator activates it.

To activate:
    poindexter settings set audio_gen_engine stable-audio-open-1.0

StableAudioOpenProvider is already a core sample in plugins/registry.py —
no entry-point registration needed. The inference server is a separate
process (not in docker-compose.local.yml yet); it runs at port 9839 next
to the SDXL sidecar (9836) and WAN server.

Idempotent — ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KEYS = [
    (
        "audio_gen_engine",
        "",
        "media",
        "Active audio-generation engine for music/SFX (ambient beds, "
        "intro stings). Empty = disabled. Set to 'stable-audio-open-1.0' "
        "when the Stable Audio Open inference server is running at port 9839.",
    ),
    (
        "stable_audio_open_server_url",
        "http://host.docker.internal:9839",
        "media",
        "Stable Audio Open inference server URL. Default port 9839. "
        "Use 'http://host.docker.internal:9839' from inside Docker, "
        "'http://localhost:9839' from the native Python process.",
    ),
    (
        "stable_audio_open_default_duration_s",
        "5.0",
        "media",
        "Default audio clip duration in seconds for Stable Audio Open. "
        "Capped at 47s (model maximum). 5s is typical for intro stings.",
    ),
    (
        "stable_audio_open_output_format",
        "wav",
        "media",
        "Output format for Stable Audio Open clips: wav, mp3, ogg, flac. "
        "wav is lossless and preferred for video muxing.",
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
        "Migration seed_audio_gen_engine_settings: seeded %d keys", len(_KEYS),
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            [k for k, *_ in _KEYS],
        )
    logger.info("Migration seed_audio_gen_engine_settings down: removed")
