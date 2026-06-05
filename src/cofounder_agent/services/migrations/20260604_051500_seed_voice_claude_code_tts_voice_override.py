"""Migration 20260604_051500: seed claude-code per-room TTS voice override.

ISSUE: Glad-Labs/glad-labs-stack#1006 (two-room voice split follow-up)

Before this, the ``claude-code`` dev room and the public ``poindexter`` room
shared a single Kokoro voice key — ``voice_agent_tts_voice`` — because the
``claude-code`` profile reuses the ``default`` profile's STT/TTS/VAD keys
(see docs/operations/voice-settings.md). So the two rooms could not run
different voices: changing one changed both.

This seeds a per-room override:

  * ``voice_agent_claude_code_tts_voice`` — Kokoro voice id for the
    ``claude-code`` room ONLY. **Default ``''`` (empty)** = fall back to the
    shared ``voice_agent_tts_voice``, so this is a no-op for fresh installs
    and the public room until an operator sets it. ``build_voice_pipeline_task``
    reads it via the ``tts_voice_override`` param (empty/whitespace → shared
    key), so the public ``poindexter`` room is unaffected.

Empty default keeps the OSS install behavior identical (one shared voice);
the Glad Labs operator sets it to e.g. ``bf_isabella`` to give the dev room a
distinct voice while the public room keeps ``bf_emma``.

``ON CONFLICT DO NOTHING`` so a live value is never clobbered by a re-apply.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Seed the claude-code TTS voice override key, idempotently."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_active, is_secret)
            VALUES
                (
                    'voice_agent_claude_code_tts_voice',
                    '',
                    'voice',
                    'Kokoro voice id for the claude-code voice room only '
                    '(#1006). Empty = fall back to the shared '
                    'voice_agent_tts_voice. Lets the dev room run a distinct '
                    'voice (e.g. bf_isabella) while the public poindexter room '
                    'keeps its voice (e.g. bf_emma).',
                    true,
                    false
                )
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info(
            "Migration seed_voice_claude_code_tts_voice_override: applied"
        )


async def down(pool) -> None:
    """Drop the claude-code TTS voice override key."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key = 'voice_agent_claude_code_tts_voice'
            """
        )
        logger.info(
            "Migration seed_voice_claude_code_tts_voice_override down: reverted"
        )
