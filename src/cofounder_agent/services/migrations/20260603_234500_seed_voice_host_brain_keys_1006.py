"""Migration 20260603_234500: seed voice host-brain keys (#1006).

ISSUE: Glad-Labs/poindexter#1006

Host-brain mode for the always-on Claude-Code voice room. The voice container
is a read-only slice of the repo (``/app`` mounted ``:ro``, no ``.git``, no
toolchain) — `claude -p` there can read and talk but cannot do dev work. When a
host-brain daemon URL is configured, the bot instead runs each turn on the
HOST (full repo + git + write + all MCP servers) via ``scripts/voice_brain_host.py``,
and the container only shuttles audio. This seeds the two app_settings keys
that drive it:

  * ``voice_agent_claude_code_host_brain_url`` — daemon URL, e.g.
    ``http://host.docker.internal:8123/turn``. Empty (``''``) = host mode OFF;
    the bot keeps running ``claude -p`` locally in the container (back-compat).
  * ``voice_agent_claude_code_host_brain_token`` — bearer token shared with the
    daemon (``is_secret``). Empty until the operator opts in.

Both empty by default, so local mode stays the default until the operator
turns host mode on. ``ON CONFLICT DO NOTHING`` so a live value is never
clobbered by a re-apply.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Seed the host-brain keys, idempotently (local mode remains default)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_active, is_secret)
            VALUES
                (
                    'voice_agent_claude_code_host_brain_url',
                    '',
                    'voice',
                    'Host-brain daemon URL for the voice room (#1006), e.g. '
                    'http://host.docker.internal:8123/turn. Empty = run claude '
                    'in-container (local mode). Set to run turns on the host '
                    'with full repo/git/write/MCP.',
                    true,
                    false
                ),
                (
                    'voice_agent_claude_code_host_brain_token',
                    '',
                    'voice',
                    'Bearer token shared with the host-brain daemon (#1006). '
                    'Required when host_brain_url is set.',
                    true,
                    true
                )
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info("Migration seed_voice_host_brain_keys_1006: applied")


async def down(pool) -> None:
    """Drop the two host-brain keys."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'voice_agent_claude_code_host_brain_url',
                'voice_agent_claude_code_host_brain_token'
            )
            """
        )
        logger.info("Migration seed_voice_host_brain_keys_1006 down: reverted")
