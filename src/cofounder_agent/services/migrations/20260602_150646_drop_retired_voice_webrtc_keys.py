"""Migration: drop retired voice-WebRTC + Discord-voice app_settings keys.

Legacy-deletion #936 batch C (the clearly-safe slice). The WebRTC voice path
and the Discord voice bot were both retired 2026-05-08 (LiveKit is the voice
surface now). Their config rows linger with no live code reader:

  - voice_agent_webrtc_enabled / _host / _port — settings_defaults.py:302
    already documents these as retired; only docstring/table mentions remain
    in services/voice_agent.py. No ``site_config.get(...)`` reader.
  - discord_voice_bot_token — encrypted secret for the retired Discord voice
    bot; no Python reference.

Verified: grep finds no live reader (only docstrings). These are not in
settings_defaults' seed map, so they will not be re-created. Idempotent
``DELETE`` (0 rows on a fresh DB). ``down()`` is a no-op — dead config for
retired surfaces with no meaningful prior state to restore (the secret value
is for a decommissioned bot).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_RETIRED_KEYS = (
    "voice_agent_webrtc_enabled",
    "voice_agent_webrtc_host",
    "voice_agent_webrtc_port",
    "discord_voice_bot_token",
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            list(_RETIRED_KEYS),
        )
    logger.info("drop_retired_voice_webrtc_keys: removed app_settings rows (%s)", result)


async def down(pool) -> None:
    # No-op: dead config for surfaces retired 2026-05-08 (WebRTC voice +
    # Discord voice bot). Nothing meaningful to restore.
    logger.info("drop_retired_voice_webrtc_keys down: no-op (retired-surface config)")
