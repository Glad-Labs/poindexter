"""Migration 20260603_212236: seed voice claude-code pinned-session keys (#1006).

ISSUE: Glad-Labs/poindexter#1006

Increment 1 of the always-on Claude-Code voice room. The LiveKit bot's
claude-code brain pins a single ``claude -p`` session so context survives
container restarts, and auto-resets that session when it ages out or burns
through its token budget. This migration seeds the three app_settings keys
that drive that behaviour:

  * ``voice_agent_claude_code_session_id`` — the pinned session UUID. Seeded
    empty (``''`` is the established "unset" sentinel — NOT NULL); the bot
    mints + persists a real UUID on first boot. ``ON CONFLICT DO NOTHING`` so
    a live, bot-written value is never clobbered by a re-run.
  * ``voice_agent_claude_code_session_token_budget`` — rotate the session once
    cumulative input+output tokens exceed this (default 200000).
  * ``voice_agent_claude_code_session_max_age_seconds`` — rotate the session
    once it's older than this (default 14400 = 4h).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Seed the pinned-session keys, idempotently.

    ``ON CONFLICT (key) DO NOTHING`` so a live session id the bot already
    persisted is never overwritten by a re-apply. See
    ``docs/operations/migrations.md``.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_active)
            VALUES
                (
                    'voice_agent_claude_code_session_id',
                    '',
                    'voice',
                    'Pinned claude -p voice session for the always-on '
                    'claude-code room (#1006). Empty = unset; the bot mints '
                    'and persists a UUID on first boot.',
                    true
                ),
                (
                    'voice_agent_claude_code_session_token_budget',
                    '200000',
                    'voice',
                    'Rotate the pinned claude -p voice session once cumulative '
                    'input+output tokens exceed this (#1006).',
                    true
                ),
                (
                    'voice_agent_claude_code_session_max_age_seconds',
                    '14400',
                    'voice',
                    'Rotate the pinned claude -p voice session once it is older '
                    'than this many seconds (#1006). 14400 = 4h.',
                    true
                )
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info(
            "Migration seed_voice_claude_code_pinned_session_keys_1006: applied"
        )


async def down(pool) -> None:
    """Drop the three pinned-session keys."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'voice_agent_claude_code_session_id',
                'voice_agent_claude_code_session_token_budget',
                'voice_agent_claude_code_session_max_age_seconds'
            )
            """
        )
        logger.info(
            "Migration seed_voice_claude_code_pinned_session_keys_1006 down: reverted"
        )
