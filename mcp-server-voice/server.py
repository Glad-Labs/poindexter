"""Poindexter Voice Bridge MCP Server — voice as a session-agnostic UI surface.

This server exposes three MCP tools (``voice_join_room``, ``voice_speak``,
``voice_leave_room``) that let an *already-running* Claude Code session
hijack a LiveKit room: voice in becomes the next user input via a
per-session pipe, session output written via ``voice_speak`` becomes
voice out (chunked at sentence boundaries for interruptibility).

Architecturally, the voice bridge lives in its OWN MCP server rather
than the public ``mcp-server/`` for four reasons:

1. **Failure isolation** — a livekit/pipecat/kokoro import explosion
   shouldn't take down ``create_post`` / ``check_health`` / the rest of
   the public Poindexter surface.
2. **Dep weight** — the audio plane drags faster-whisper, livekit-agents,
   silero, kokoro onto the import path. Public Poindexter installs
   shouldn't pay that GPU+model-download cost when they don't want voice.
3. **Independent restart** — bridge crashes / GPU resets should not
   require restarting the worker-facing MCP tools.
4. **Opt-in distribution** — voice is an extension point. Operators
   who want it register this server in ``~/.claude.json`` separately;
   the rest get the slim Poindexter MCP by default.

The always-on ``voice-agent-livekit`` container (Pipecat-driven, ollama
brain) is unaffected — this server is *additive*. The bridge claims a
separate participant identity (``claude-bridge``) on the same LiveKit
room when an interactive Claude Code session wants the floor.

Sibling shape: ``mcp-server-gladlabs/`` is the reference for the
"focused MCP server" pattern (single FastMCP instance, lazy pool,
``stdio`` entry).

Usage:
    uv --directory mcp-server-voice run python -m server
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid as _uuid
from pathlib import Path

import asyncpg
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-bridge-mcp")

# Local DB DSN — voice_bridge_* settings live in app_settings, same DB
# the rest of the Poindexter stack reads from.
#
# Accept DATABASE_URL (canonical, per feedback_db_first_config / CLAUDE.md
# "Only DATABASE_URL as env var") OR LOCAL_DATABASE_URL (legacy alias the
# rest of the brain still uses). Default port is 15432 — the canonical
# poindexter-postgres-local container port (host port 5433 was retired in
# the container-rename pass; that mismatch silently routed every
# voice_join_room call into a ConnectionRefusedError).
LOCAL_DB_DSN = (
    os.getenv("DATABASE_URL")
    or os.getenv("LOCAL_DATABASE_URL")
    or "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain"
)

# Lazy-initialized connection pool.
_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    """Return the shared local-DB pool, creating it on first call."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(LOCAL_DB_DSN, min_size=1, max_size=3)
    return _pool


# Ensure the local module dir is on sys.path so ``import livekit_bridge``
# resolves whether the server is run via ``python -m server`` or ``uv run``.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _format_tool_error(tool_name: str, e: Exception) -> str:
    """Return a user-visible error string and log the full traceback."""
    rid = _uuid.uuid4().hex[:8]
    logger.exception("[voice-tool] %s failed [rid=%s]", tool_name, rid)
    return f"{tool_name} failed (rid={rid}): {type(e).__name__}: {e}"


mcp = FastMCP("VoiceBridge", instructions="""
Poindexter voice bridge MCP server — exposes voice as a session-agnostic
UI surface. Use these tools to claim a LiveKit room for the current
Claude Code session: voice in arrives as transcripts on a per-session
.in pipe, voice out is TTS-spoken into the room via voice_speak.

Tools:
  - voice_join_room(channel_id?, session_id?) — spawn a bridge worker
    subprocess (fresh on-disk code; blocks until it reports ready)
  - voice_speak(text, session_id) — TTS reply into the room (chunked)
  - voice_leave_room(session_id) — tear it down (idempotent; leave-by-PID)

The bridge worker runs as a detached subprocess (bridge_worker.py) so a
code change takes effect on the next voice_join_room without restarting
this server (Glad-Labs/poindexter#1010). The always-on
voice-agent-livekit container is unaffected; this server is additive and
runs side-by-side with it.
""")


# ============================================================================
# LIVEKIT BRIDGE TOOLS — voice as a session-agnostic UI surface
# ============================================================================
#
# All three tools pull defaults from app_settings (per
# feedback_db_first_config):
#   voice_bridge_enabled            — master switch (boolean string)
#   voice_default_room              — LiveKit room name when caller omits
#   voice_bridge_stt_model          — faster-whisper model id (e.g. base.en)
#   voice_bridge_tts_voice          — Kokoro voice (e.g. af_bella)
#   voice_bridge_max_session_seconds — hard timeout, default 1800
#   voice_bridge_chunk_max_chars     — TTS chunk size, default 500
#
# Seeded by migration 20260507_*_seed_voice_bridge_app_settings.

# Import the bridge worker — kept module-level so unit tests can import
# both ``server.voice_join_room`` and ``server.livekit_bridge`` and
# patch the registry / pipe directory consistently.
try:  # pragma: no cover — import shape only
    from livekit_bridge import (  # noqa: E402
        BridgeConfig,
        ensure_session_pipes,
        new_session_id,
        session_pipe_paths,
        spawn_bridge_subprocess,
        speak_into_bridge,
        start_bridge,
        stop_bridge,
        terminate_bridge_process,
    )
    _BRIDGE_AVAILABLE = True
except Exception:  # noqa: BLE001 — bridge is optional in CI shape
    _BRIDGE_AVAILABLE = False


def _inprocess_mode() -> bool:
    """Whether to run the bridge worker in-process instead of a subprocess.

    Default is subprocess (Glad-Labs/poindexter#1010): a long-lived
    MCP server binds to the modules it loaded at startup, so an in-process
    worker uses STALE cached code after any change — a deaf bridge a mobile
    operator can't fix by restarting. Spawning a fresh subprocess imports
    current on-disk code every time.

    The ``VOICE_BRIDGE_INPROCESS=1`` escape hatch keeps the old in-process
    path for unit tests + interactive debugging (feedback_backcompat).
    """
    return (os.environ.get("VOICE_BRIDGE_INPROCESS", "") or "").strip() == "1"


async def _bridge_settings(pool: asyncpg.Pool) -> dict[str, str]:
    """Return the bridge's app_settings keys as a dict in one round-trip."""
    rows = await pool.fetch(
        """
        SELECT key, value FROM app_settings
        WHERE key IN (
            'voice_bridge_enabled',
            'voice_default_room',
            'voice_bridge_stt_model',
            'voice_bridge_tts_voice',
            'voice_bridge_max_session_seconds',
            'voice_bridge_chunk_max_chars'
        )
        """,
    )
    return {r["key"]: (r["value"] or "") for r in rows}


def _parse_bool(value: str, *, default: bool) -> bool:
    """Forgiving boolean parser for app_settings string values."""
    if not value:
        return default
    return value.strip().lower() in {"true", "1", "yes", "on", "y"}


@mcp.tool()
async def voice_join_room(
    channel_id: str = "",
    session_id: str = "",
) -> str:
    """Spin up the LiveKit bridge so this Claude Code session can use voice.

    Voice in becomes the next user input to *this* session via a per-session
    pipe (``~/.poindexter/voice/<session_id>.in``); session output written
    via ``voice_speak`` becomes voice out. The always-on
    ``voice-agent-livekit`` container is unaffected — this tool joins the
    same LiveKit room as a separate participant identity (default
    ``claude-bridge``).

    **Process model (Glad-Labs/poindexter#1010):** by default the
    bridge worker is launched as a *separate Python subprocess*
    (``bridge_worker.py``) that imports fresh on-disk code, NOT as a task
    inside this long-lived MCP server. The MCP server is a thin launcher.
    This is what makes a Pipecat / STT code change take effect on the next
    ``voice_join_room`` without restarting the MCP server (which a mobile
    operator can't do). The worker's PID lands in ``<sid>.lock`` and its
    readiness in ``<sid>.status`` (``connecting`` → ``ready`` /
    ``error: ...``); this tool blocks on that handshake (up to ~30s, which
    covers a cold faster-whisper model load) before returning.

    Set ``VOICE_BRIDGE_INPROCESS=1`` to use the legacy in-process
    ``start_bridge`` path instead (unit tests + interactive debugging).

    Args:
        channel_id: LiveKit room name. Defaults to
            ``app_settings.voice_default_room`` (seeded as
            ``"claude-bridge"`` by the bridge migration). Pass ``"ops"``
            etc. to claim a non-default room.
        session_id: Optional explicit id. When empty, one is generated and
            returned in the response. The slash command uses this id for
            subsequent ``voice_speak`` / ``voice_leave_room`` calls AND to
            point a Monitor watcher at the right ``.in`` pipe.

    Returns: JSON string with ``session_id``, ``room``, ``in_pipe``,
        ``out_pipe``, ``pid`` (the worker subprocess), and ``status``.
        ``status`` is always either ``"started"`` or an explicit error
        (with the worker log tail on a launch failure) — no silent ok=True
        per feedback_no_silent_defaults.
    """
    try:
        if not _BRIDGE_AVAILABLE:
            return json.dumps({
                "error": (
                    "livekit_bridge module failed to import — bridge "
                    "is unavailable. Check the MCP server logs for "
                    "the import-time traceback."
                ),
            })
        pool = await _get_pool()
        settings = await _bridge_settings(pool)

        if not _parse_bool(settings.get("voice_bridge_enabled", ""), default=True):
            return json.dumps({
                "error": (
                    "voice_bridge_enabled=false in app_settings — bridge "
                    "is administratively disabled. Set it to true to "
                    "re-enable: `poindexter set voice_bridge_enabled true`."
                ),
                "missing_setting": None,
                "disabled": True,
            })

        room = (channel_id or settings.get("voice_default_room", "")).strip()
        if not room:
            return json.dumps({
                "error": (
                    "No room — pass channel_id or seed "
                    "voice_default_room in app_settings (migration "
                    "20260507_seed_voice_bridge_app_settings)."
                ),
                "missing_setting": "voice_default_room",
            })

        try:
            chunk_max = int(settings.get("voice_bridge_chunk_max_chars") or "500")
        except ValueError:
            chunk_max = 500
        try:
            max_session_seconds = int(
                settings.get("voice_bridge_max_session_seconds") or "1800",
            )
        except ValueError:
            max_session_seconds = 1800

        sid = (session_id or "").strip() or new_session_id()
        ensure_session_pipes(sid)
        stt_model = (settings.get("voice_bridge_stt_model") or "base").strip() or "base"
        tts_voice = (settings.get("voice_bridge_tts_voice") or "af_bella").strip() or "af_bella"
        try:
            vad_stop_secs = float(settings.get("voice_bridge_vad_stop_secs") or "0.5")
        except ValueError:
            vad_stop_secs = 0.5
        try:
            user_speech_timeout = float(
                settings.get("voice_bridge_user_speech_timeout") or "1.5"
            )
        except ValueError:
            user_speech_timeout = 1.5
        config = BridgeConfig(
            room=room,
            chunk_max_chars=chunk_max,
            max_session_seconds=max_session_seconds,
            stt_model=stt_model,
            tts_voice=tts_voice,
            vad_stop_secs=vad_stop_secs,
            user_speech_timeout=user_speech_timeout,
        )

        if _inprocess_mode():
            # Legacy in-process path — escape hatch for tests + debugging.
            state = await start_bridge(session_id=sid, config=config)
            pid = os.getpid()
            resolved_sid = state.session_id
        else:
            # Default: spawn a detached subprocess that imports fresh
            # on-disk code, so MCP-server module staleness can't make the
            # bridge go deaf (Glad-Labs/poindexter#1010). Popen + the
            # readiness poll are blocking, so run them off the event loop.
            try:
                pid = await asyncio.to_thread(
                    spawn_bridge_subprocess, sid, config,
                )
            except RuntimeError as e:
                # The launcher fails loud with the worker log tail when the
                # subprocess never reaches "ready" (crash / error / timeout).
                return json.dumps({"error": str(e)})
            resolved_sid = sid

        paths = session_pipe_paths(resolved_sid)

        return json.dumps({
            "status": "started",
            "session_id": resolved_sid,
            "pid": pid,
            "room": room,
            "in_pipe": str(paths["in"]),
            "out_pipe": str(paths["out"]),
            "max_session_seconds": max_session_seconds,
            "chunk_max_chars": chunk_max,
            "instructions": (
                "Watch in_pipe with `tail -F` (or a Monitor task) for "
                "transcripts. Write text to out_pipe (or call "
                "voice_speak) to TTS into the room."
            ),
        })
    except Exception as e:
        return json.dumps({"error": _format_tool_error("voice_join_room", e)})


@mcp.tool()
async def voice_speak(text: str, session_id: str) -> str:
    """TTS ``text`` into the bridge session's LiveKit room.

    Long replies (over ``voice_bridge_chunk_max_chars`` chars) are
    chunked at sentence boundaries so the user can interrupt mid-reply
    — the bridge emits one TTS request per chunk. Returns the chunk
    count plus any error.

    Args:
        text: The reply to speak. Empty / whitespace-only text is a no-op
            (returns ``chunks=0``) — explicit so accidental empty calls
            don't ping the room.
        session_id: The bridge session id returned by ``voice_join_room``.

    Returns: JSON string with ``status``, ``session_id``, and ``chunks``
        (number of TTS requests enqueued). ``status="not_running"`` if
        the bridge isn't up — caller should re-issue ``voice_join_room``.
    """
    try:
        if not _BRIDGE_AVAILABLE:
            return json.dumps({"error": "livekit_bridge module unavailable"})
        if not session_id or not session_id.strip():
            return json.dumps({"error": "session_id is required"})
        sid = session_id.strip()
        try:
            chunks = await speak_into_bridge(sid, text or "")
        except RuntimeError as e:
            # speak_into_bridge raises with a precise message when the
            # session isn't registered — surface as a structured error.
            return json.dumps({
                "error": str(e),
                "status": "not_running",
                "session_id": sid,
            })
        return json.dumps({
            "status": "queued",
            "session_id": sid,
            "chunks": chunks,
        })
    except Exception as e:
        return json.dumps({"error": _format_tool_error("voice_speak", e)})


@mcp.tool()
async def voice_leave_room(session_id: str) -> str:
    """Disconnect the bridge worker for ``session_id``. Idempotent.

    Tries the in-process registry first (``stop_bridge`` — covers the
    ``VOICE_BRIDGE_INPROCESS`` / test path). If nothing was registered in
    *this* process, falls back to leave-by-PID
    (``terminate_bridge_process``) which reads the worker PID from
    ``<sid>.lock`` and signals the subprocess — the normal path since
    Glad-Labs/poindexter#1010 made the worker a separate process.

    Calling twice on the same id returns ``status="stopped"`` the first
    time and ``status="not_running"`` the second — never raises in the
    happy path. The ``.in`` / ``.out`` pipe files are intentionally left
    in place so the slash command's Monitor can read final lines after
    the bridge exits.
    """
    try:
        if not _BRIDGE_AVAILABLE:
            return json.dumps({"error": "livekit_bridge module unavailable"})
        if not session_id or not session_id.strip():
            return json.dumps({"error": "session_id is required"})
        sid = session_id.strip()
        # In-process registry (tests + VOICE_BRIDGE_INPROCESS) first; if
        # there's no entry here, the worker is a detached subprocess —
        # leave-by-PID via the .lock file.
        stopped = await stop_bridge(sid)
        if not stopped:
            stopped = await asyncio.to_thread(terminate_bridge_process, sid)
        return json.dumps({
            "status": "stopped" if stopped else "not_running",
            "session_id": sid,
        })
    except Exception as e:
        return json.dumps({"error": _format_tool_error("voice_leave_room", e)})


if __name__ == "__main__":
    mcp.run(transport="stdio")
