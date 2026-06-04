"""Migration 20260604_030000: seed claude-code voice-room keys (#1006).

ISSUE: Glad-Labs/poindexter#1006

The two-room split. The always-on voice surface now runs ONE container per
room profile (``services.voice_agent_livekit --service --service-profile X``):

  * ``default``     — the poindexter room (Emma/GLM ops bot). Reads the
    existing ``voice_agent_livekit_enabled`` / ``voice_agent_room_name`` /
    ``voice_agent_identity`` keys; brain resolved from
    ``voice_agent_brain_mode``.
  * ``claude-code`` — the dev-on-the-go room. Brain pinned to ``claude-code``.
    Reads its own ``voice_agent_claude_code_*`` namespace (which already holds
    the pinned-session + host-brain keys seeded by ``20260603_212236`` and
    ``20260603_234500``).

This seeds the three operational knobs the ``claude-code`` profile needs,
mirroring the poindexter room's keys so the operator can turn the room on/off
and rename it from the DB (phone-tunable), per the DB-first config posture —
no hardcoded room/identity/enabled in compose:

  * ``voice_agent_claude_code_enabled``   — master on/off for the container
    (default ``'true'`` — the room ships enabled; with host_brain_url empty it
    runs ``claude -p`` read-only in-container until the host brain is wired).
  * ``voice_agent_claude_code_room_name`` — LiveKit room to join
    (default ``'claude-code'``; must match ``_ALLOWED_VOICE_ROOMS`` in
    ``routes/voice_routes.py`` so ``/voice/join?room=claude-code`` reaches it).
  * ``voice_agent_claude_code_identity``  — bot identity in the room
    (default ``'claude-code-bot'``; distinct from the poindexter bot so both
    can coexist if ever placed in one room).

``ON CONFLICT DO NOTHING`` so a live value is never clobbered by a re-apply.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Seed the claude-code room keys, idempotently."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_active, is_secret)
            VALUES
                (
                    'voice_agent_claude_code_enabled',
                    'true',
                    'voice',
                    'Master on/off for the claude-code voice room container '
                    '(#1006). false/0/no/off = the container exits 0 and docker '
                    'leaves it stopped under unless-stopped.',
                    true,
                    false
                ),
                (
                    'voice_agent_claude_code_room_name',
                    'claude-code',
                    'voice',
                    'LiveKit room the claude-code voice bot joins (#1006). Must '
                    'match an allowed room in routes/voice_routes.py so '
                    '/voice/join?room= can reach it.',
                    true,
                    false
                ),
                (
                    'voice_agent_claude_code_identity',
                    'claude-code-bot',
                    'voice',
                    'Participant identity for the claude-code voice bot (#1006). '
                    'Distinct from the poindexter bot so both can coexist.',
                    true,
                    false
                )
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info("Migration seed_voice_claude_code_room_keys_1006: applied")


async def down(pool) -> None:
    """Drop the three claude-code room keys."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'voice_agent_claude_code_enabled',
                'voice_agent_claude_code_room_name',
                'voice_agent_claude_code_identity'
            )
            """
        )
        logger.info(
            "Migration seed_voice_claude_code_room_keys_1006 down: reverted"
        )
