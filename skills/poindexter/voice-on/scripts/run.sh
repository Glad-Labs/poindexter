#!/bin/bash
# scripts/run.sh — Turn on the LiveKit MCP voice bridge for this Claude Code session.
#
# Calls the in-process bridge worker via livekit_bridge directly (the
# bridge state is in-process on the MCP server, so we don't go over
# HTTP — the slash command and the MCP tool share state through the
# Python module's _registry).

set -euo pipefail

ROOM="${1:-}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../" && pwd)"
MCP_SERVER_DIR="${REPO_ROOT}/mcp-server-voice"

if [ ! -d "${MCP_SERVER_DIR}" ]; then
  echo "Error: mcp-server-voice/ not found at ${MCP_SERVER_DIR}"
  exit 1
fi

# Run the join via Python — the bridge module handles app_settings
# lookup, pipe creation, and worker spawn. The script captures the
# returned session_id and writes it to CURRENT_SESSION so subsequent
# voice-speak / voice-off invocations don't need the operator to retype.
python - <<PYEOF
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "${MCP_SERVER_DIR}")
import server  # noqa: E402

room = "${ROOM}".strip()


def _resolve_tool(name):
    obj = getattr(server, name)
    for attr in ("fn", "func", "callable"):
        impl = getattr(obj, attr, None)
        if callable(impl):
            return impl
    return obj


join = _resolve_tool("voice_join_room")
raw = asyncio.run(join(channel_id=room))
payload = json.loads(raw)

if "error" in payload:
    print(f"voice-on FAILED: {payload['error']}", file=sys.stderr)
    sys.exit(1)

sid = payload["session_id"]
print(f"voice on, talk to me. session_id={sid}")
print(f"  in_pipe:  {payload['in_pipe']}")
print(f"  out_pipe: {payload['out_pipe']}")
print(f"  room:     {payload['room']}")
print(f"  watchdog: {payload['max_session_seconds']}s")

# Stash the current session id so voice-speak / voice-off can pick it up
voice_dir = Path(payload["in_pipe"]).parent
voice_dir.mkdir(parents=True, exist_ok=True)
(voice_dir / "CURRENT_SESSION").write_text(sid + "\n")
PYEOF
