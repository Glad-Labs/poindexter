"""Migration 20260604_060000: seed Discord voice-transcript keys (#1006).

ISSUE: Glad-Labs/glad-labs-stack#1006

The claude-code voice room mirrors each turn (you → reply) to a written
transcript so the operator can scan it while listening. That mirror was on
**Telegram**, which is reserved for critical alerts — routine voice chatter
there is noise. It now posts to **Discord** (the routine channel) instead.

Two keys drive it (both default to the no-extra-setup path):

  * ``voice_agent_claude_code_transcript_enabled`` — master on/off for the
    mirror. Default ``'true'``. Set ``false`` to turn it off without a code
    change (the old Telegram mirror had no gate — this fixes that).
  * ``voice_transcript_discord_webhook_url`` — the dedicated Discord webhook
    (``is_secret``). **Default ``''`` (empty)** = fall back to the already-
    configured ``discord_ops_webhook_url``, so the transcript works out of the
    box on the ops channel; set this to a dedicated #voice-transcripts webhook
    to split it onto its own channel.

``ON CONFLICT DO NOTHING`` so a live value is never clobbered by a re-apply.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Seed the Discord transcript gate + webhook keys, idempotently."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_active, is_secret)
            VALUES
                (
                    'voice_agent_claude_code_transcript_enabled',
                    'true',
                    'voice',
                    'Master on/off for mirroring claude-code voice turns to '
                    'Discord (#1006). false/0/no/off disables the mirror.',
                    true,
                    false
                ),
                (
                    'voice_transcript_discord_webhook_url',
                    '',
                    'voice',
                    'Dedicated Discord webhook for the voice transcript (#1006). '
                    'Empty = fall back to discord_ops_webhook_url so it works '
                    'out of the box; set to a #voice-transcripts webhook to '
                    'split it onto its own channel.',
                    true,
                    true
                )
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info("Migration seed_voice_transcript_discord_keys_1006: applied")


async def down(pool) -> None:
    """Drop the two Discord transcript keys."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'voice_agent_claude_code_transcript_enabled',
                'voice_transcript_discord_webhook_url'
            )
            """
        )
        logger.info(
            "Migration seed_voice_transcript_discord_keys_1006 down: reverted"
        )
