"""Migration 20260505_135518: seed always-on voice agent container settings

ISSUE: Glad-Labs/poindexter#383

Adds the operator-tunable knobs the new ``voice-agent-livekit`` and
``voice-agent-webrtc`` Docker services read at startup. Pairs with
``services/voice_agent.py``, ``services/voice_agent_livekit.py`` and
``services/voice_agent_webrtc.py`` — same pipeline modules as before,
now wrapped in ``unless-stopped`` containers so Matt can talk to
Poindexter without launching ``scripts/start-livekit-voice-bot.sh``
every time.

The earlier migrations (0104, 0107, 0108) already cover model / voice /
prompt / VAD / WebRTC bind-host knobs. This migration adds:

- ``voice_agent_room_name`` — LiveKit room the always-on bot joins on
  boot (default ``poindexter`` so the operator's client can ``join``
  by name without a config dance).
- ``voice_agent_identity`` — bot identity inside the room. Multiple
  bots in one room need distinct identities; default ``poindexter-bot``.
- ``voice_agent_brain`` — LLM stage. ``ollama`` (snappy, local writer
  model + read-only Poindexter tools) or ``claude-code`` (Max-sub
  subprocess bridge with full repo access). Default ``ollama`` so the
  always-on container doesn't burn Claude rate-window minutes idling.
- ``voice_agent_livekit_url`` — internal LiveKit URL for the bot
  inside the docker network. Default ``ws://livekit:7880`` (compose
  service name). Operators on a hosted LiveKit instance override.
- ``voice_agent_livekit_enabled`` / ``voice_agent_webrtc_enabled``
  — boolean toggles so the operator can flip individual surfaces off
  without editing compose. Default both true.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` so a pre-set custom value
survives a re-run.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SEEDS: list[tuple[str, str, str]] = [
    (
        "voice_agent_room_name",
        "poindexter",
        "LiveKit room the always-on voice-agent-livekit container joins on "
        "boot. Operator clients (https://meet.livekit.io, mobile LiveKit "
        "apps) join the same room name to talk to the bot. Change at "
        "runtime; container picks up on next restart.",
    ),
    (
        "voice_agent_identity",
        "poindexter-bot",
        "Bot identity inside the LiveKit room. Multiple bots in one room "
        "need distinct identities. Defaults to 'poindexter-bot' so the "
        "human attendee is always the named participant.",
    ),
    (
        "voice_agent_brain",
        "ollama",
        "LLM stage the always-on voice agent uses. 'ollama' (default) "
        "wires the local glm-4.7-5090 + three read-only Poindexter tools "
        "(snappy, zero incremental cost). 'claude-code' shells every "
        "turn out to `claude -p` under the operator's Max OAuth sub — "
        "slower but has full repo / MCP / edit access. The always-on "
        "container defaults to ollama so it doesn't burn Claude rate-"
        "window minutes idling.",
    ),
    (
        "voice_agent_livekit_url",
        "ws://livekit:7880",
        "WebSocket URL the in-network voice bot uses to reach the "
        "LiveKit SFU. 'livekit' is the docker-compose service name; "
        "override to ws(s)://<host>:<port> for a remote LiveKit "
        "instance. Voice clients (phone, browser) reach the SFU at "
        "http(s)://<host>:7880 — that URL is for the human, not the "
        "bot.",
    ),
    (
        "voice_agent_livekit_enabled",
        "true",
        "Toggle for the always-on voice-agent-livekit container. "
        "'true' (default) keeps the bot joined to the configured room. "
        "Set to 'false' to take the LiveKit surface offline without "
        "editing docker-compose.local.yml — the container reads this "
        "at startup and exits 0 if disabled, then docker leaves it "
        "stopped under the unless-stopped restart policy.",
    ),
    (
        "voice_agent_webrtc_enabled",
        "true",
        "Toggle for the always-on voice-agent-webrtc container. "
        "'true' (default) serves the SmallWebRTC prebuilt UI on "
        "voice_agent_webrtc_port (8003). Set to 'false' to disable the "
        "browser-direct surface without editing compose. Same exit-0 "
        "pattern as the LiveKit toggle.",
    ),
]


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        inserted = 0
        for key, value, description in _SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, description, is_active)
                VALUES ($1, $2, $3, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "20260505_135518: seeded %d/%d voice-agent container settings "
            "(remaining were already set)",
            inserted, len(_SEEDS),
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        for key, _value, _description in _SEEDS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                key,
            )
        logger.info(
            "20260505_135518: removed %d voice-agent container seeds",
            len(_SEEDS),
        )
