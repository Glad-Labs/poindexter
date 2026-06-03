"""Subprocess entrypoint for the voice bridge worker.

The MCP server (``server.py``) is a long-lived process. If the bridge
worker ran inside it as an ``asyncio.create_task``, it would bind to
whatever Python modules that process loaded at startup — so any code
change (e.g. the Pipecat 1.2 migration) leaves the running MCP server
using STALE cached modules until restarted, which a mobile operator
cannot do. The visible symptom was a deaf bridge: every MCP-spawned
bridge's ``<sid>.in`` transcript pipe stayed empty while a standalone
subprocess running fresh on-disk code worked perfectly.

The fix (Glad-Labs/glad-labs-stack#1010): ``voice_join_room`` spawns
*this* module as a separate Python subprocess via
``livekit_bridge.spawn_bridge_subprocess``. Because the OS starts a fresh
interpreter, this process imports the *current on-disk* ``livekit_bridge``
/ ``audio_plane_pipecat`` / ``services.voice_pipecat`` code every time —
no staleness, no restart needed.

Contract (set by the launcher):

- ``POINDEXTER_VOICE_BRIDGE_SESSION_ID`` — the session id to run.
- ``POINDEXTER_VOICE_BRIDGE_CONFIG`` — JSON of ``asdict(BridgeConfig)``.
- ``POINDEXTER_VOICE_DIR`` — optional pipe-dir override (passed through).
- LiveKit creds (``LIVEKIT_API_KEY`` / ``_SECRET`` / ``_URL``) and
  ``DATABASE_URL`` are inherited from the launcher's environment — this
  module does NOT re-read a ``.env`` file.

This process logs to stdout/stderr; the launcher captures both to
``<sid>.log`` and reads the tail into error messages. Readiness is
signalled via the ``<sid>.status`` file (``connecting`` → ``ready`` →
``stopped`` / ``error: ...``), written by ``_bridge_main``.

Run indirectly via the MCP tool; for manual debugging::

    POINDEXTER_VOICE_BRIDGE_SESSION_ID=vb-dbg0001 \
    POINDEXTER_VOICE_BRIDGE_CONFIG='{"room":"claude-bridge"}' \
    python bridge_worker.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Ensure ``import livekit_bridge`` resolves whether this is launched by
# the MCP server (cwd = mcp-server-voice) or directly. Mirrors the
# standalone sys.path pattern in audio_plane_pipecat.py / server.py.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("voice-bridge-worker")


async def _run() -> int:
    """Reconstruct the config, start the bridge, block until it exits."""
    from livekit_bridge import BridgeConfig, start_bridge  # noqa: PLC0415

    session_id = (os.environ.get("POINDEXTER_VOICE_BRIDGE_SESSION_ID") or "").strip()
    if not session_id:
        # Fail loud — the launcher always sets this. A missing id means a
        # mis-invocation; no silent default per feedback_no_silent_defaults.
        logger.error(
            "POINDEXTER_VOICE_BRIDGE_SESSION_ID is unset — cannot start "
            "the bridge worker. The launcher must inject it.",
        )
        return 2

    config_json = os.environ.get("POINDEXTER_VOICE_BRIDGE_CONFIG") or ""
    if not config_json.strip():
        logger.error(
            "POINDEXTER_VOICE_BRIDGE_CONFIG is unset for session %s — "
            "cannot reconstruct BridgeConfig.",
            session_id,
        )
        return 2
    try:
        config_dict = json.loads(config_json)
    except json.JSONDecodeError as exc:
        logger.error(
            "POINDEXTER_VOICE_BRIDGE_CONFIG for session %s is not valid "
            "JSON: %r",
            session_id, exc,
        )
        return 2
    try:
        config = BridgeConfig(**config_dict)
    except TypeError as exc:
        logger.error(
            "POINDEXTER_VOICE_BRIDGE_CONFIG for session %s has fields that "
            "do not match BridgeConfig: %r",
            session_id, exc,
        )
        return 2

    # Default the audio plane to the real Pipecat plane unless the
    # operator explicitly opted into the noop stub. The fresh interpreter
    # means this picks up current audio_plane_pipecat code.
    os.environ.setdefault("VOICE_BRIDGE_AUDIO_PLANE", "pipecat")

    logger.info(
        "Starting bridge worker session=%s room=%s identity=%s "
        "(audio_plane=%s)",
        session_id, config.room, config.identity,
        os.environ.get("VOICE_BRIDGE_AUDIO_PLANE"),
    )

    # start_bridge writes the .status handshake the launcher polls
    # (connecting → ready, or error: ... on connect failure). It raises
    # if the audio plane fails to connect — we let that propagate so the
    # process exits non-zero and the launcher's poll sees the dead PID +
    # the error status + the traceback in the log.
    state = await start_bridge(session_id=session_id, config=config)
    if state.task is None:  # pragma: no cover — start_bridge always sets it
        logger.error("start_bridge returned a state with no task — aborting.")
        return 1
    # Block until the worker loop exits (watchdog, leave_event, or kill).
    await state.task
    logger.info("Bridge worker session=%s exited cleanly.", session_id)
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:  # pragma: no cover — clean Ctrl-C / SIGTERM
        logger.info("Bridge worker interrupted — exiting.")
        return 0
    except Exception:  # noqa: BLE001 — top-level guard, log full traceback
        logger.exception("Bridge worker crashed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
