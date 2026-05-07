"""Migration 20260507_022644: seed voice bridge app_settings.

Adds the six knobs read by the LiveKit MCP bridge — the architecturally-
correct alternative to the ``voice_agent_brain_mode=claude-code``
subprocess-spawn path. The bridge lets an *already-running* Claude Code
session hijack a LiveKit room: voice in → next user input via per-session
pipe; voice_speak output → TTS into the room.

The always-on ``voice-agent-livekit`` container is unaffected — these
settings configure the *additive* MCP-driven bridge spawned by
``voice_join_room``. See ``mcp-server/livekit_bridge.py`` and
``docs/operations/voice-bridge.md``.

Settings seeded:

- ``voice_bridge_enabled`` (bool, default ``true``): master switch the
  ``voice_join_room`` tool checks before spinning up. When false, the
  tool returns a structured error pointing the operator at this key —
  no silent fallback per ``feedback_no_silent_defaults``.

- ``voice_default_room`` (str, default ``"claude-bridge"``): LiveKit
  room name used when ``voice_join_room`` is called without an explicit
  ``channel_id``. Distinct from ``voice_agent_room_name`` (the always-on
  bot's room) so the bridge doesn't accidentally collide with the
  always-on ollama agent. Operators on a custom deployment override at
  runtime: ``poindexter set voice_default_room ops``.

- ``voice_bridge_stt_model`` (str, default ``"base.en"``): faster-
  whisper model id the future Pipecat audio plane will load. Defaults
  to ``base.en`` (CPU-friendly, ~140 MB) so the public Poindexter
  release works on hardware without a GPU. Operators with a 5090 can
  flip to ``"large-v3"`` for accuracy.

- ``voice_bridge_tts_voice`` (str, default ``"af_bella"``): Kokoro
  voice id. Same default as the always-on ``voice-agent-livekit``
  container so the operator's "voice mental model" is consistent
  whether they're talking to ollama-Emma or claude-via-bridge.

- ``voice_bridge_max_session_seconds`` (int, default ``1800``):
  hard timeout — bridge worker auto-leaves after this many seconds.
  Half-hour ceiling keeps an orphaned bridge from hogging the LiveKit
  room indefinitely if the slash command crashes without sending
  ``voice_leave_room``.

- ``voice_bridge_chunk_max_chars`` (int, default ``500``): max
  characters per TTS chunk. ``voice_speak`` chunks long replies at
  sentence boundaries so the user can interrupt mid-reply — 500 chars
  ≈ 30 seconds of natural speech, matches the empty-room grace
  window so the operator can take the floor between chunks.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — operator-set values are
preserved on re-run.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SEEDS: list[tuple[str, str, str, str]] = [
    (
        "voice_bridge_enabled",
        "true",
        "voice",
        "Master switch for the LiveKit MCP bridge — the architecturally-"
        "correct alternative to the subprocess-spawn voice_agent_brain_"
        "mode=claude-code path. When true (default), voice_join_room "
        "spins up a per-session bridge worker that lets the live Claude "
        "Code session claim voice in/out for itself. When false, "
        "voice_join_room returns a structured error pointing the "
        "operator at this key (no silent fallback per "
        "feedback_no_silent_defaults). The always-on voice-agent-"
        "livekit container is unaffected by this setting — it has its "
        "own voice_agent_livekit_enabled toggle.",
    ),
    (
        "voice_default_room",
        "claude-bridge",
        "voice",
        "Default LiveKit room name when voice_join_room is called "
        "without an explicit channel_id. Distinct from voice_agent_"
        "room_name (the always-on ollama agent's room) so the bridge "
        "and the agent don't accidentally collide on the same room. "
        "Operators on custom deployments override at runtime: "
        "`poindexter set voice_default_room ops`.",
    ),
    (
        "voice_bridge_stt_model",
        "base.en",
        "voice",
        "faster-whisper model id loaded by the future Pipecat audio "
        "plane in the bridge worker. Defaults to base.en (CPU-friendly "
        "~140 MB) so the public Poindexter release works on hardware "
        "without a GPU. Operators with a 5090 can flip to large-v3 for "
        "accuracy at higher VRAM cost.",
    ),
    (
        "voice_bridge_tts_voice",
        "af_bella",
        "voice",
        "Kokoro voice id used by the bridge worker's TTS path. Matches "
        "the always-on voice-agent-livekit container default so the "
        "operator's auditory mental model stays consistent across the "
        "two surfaces — no jarring voice switch when flipping from "
        "ollama-Emma to claude-via-bridge.",
    ),
    (
        "voice_bridge_max_session_seconds",
        "1800",
        "voice",
        "Hard upper bound on a single bridge session, in seconds. The "
        "worker auto-leaves the LiveKit room after this many seconds "
        "regardless of activity. Defaults to 1800 (30 minutes) — long "
        "enough for a real dev pairing session, short enough that an "
        "orphaned bridge doesn't squat on the room forever if the slash "
        "command crashes without calling voice_leave_room.",
    ),
    (
        "voice_bridge_chunk_max_chars",
        "500",
        "voice",
        "Maximum characters per TTS chunk emitted by voice_speak. Long "
        "replies are split at sentence boundaries so the operator can "
        "interrupt mid-reply — 500 chars is ~30 seconds of natural "
        "speech, matching the bridge's empty-room grace window. Lower "
        "this if Kokoro stutters on long inputs; raise it (up to ~2000) "
        "for monologue-style replies where interruption is rare.",
    ),
]


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing — skipping migration "
                "20260507_022644 (voice_bridge_* seed)"
            )
            return

        inserted = 0
        for key, value, category, description in _SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, FALSE, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "Migration 20260507_022644: seeded %d/%d voice bridge settings "
            "(remaining were already set by an operator)",
            inserted, len(_SEEDS),
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        for key, _value, _category, _description in _SEEDS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                key,
            )
        logger.info(
            "Migration 20260507_022644 rolled back: removed %d voice "
            "bridge settings",
            len(_SEEDS),
        )
