#!/bin/bash
# scripts/run.sh — Tear down the LiveKit MCP voice bridge.
#
# Idempotent — safe to call when no bridge is running.

set -euo pipefail

SESSION_ID="${1:-}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../" && pwd)"
MCP_SERVER_DIR="${REPO_ROOT}/mcp-server-voice"

python - <<PYEOF
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "${MCP_SERVER_DIR}")
import server  # noqa: E402
import livekit_bridge  # noqa: E402


def _resolve_tool(name):
    obj = getattr(server, name)
    for attr in ("fn", "func", "callable"):
        impl = getattr(obj, attr, None)
        if callable(impl):
            return impl
    return obj


sid = "${SESSION_ID}".strip()
if not sid:
    cur = livekit_bridge.voice_pipe_dir() / "CURRENT_SESSION"
    if cur.exists():
        sid = cur.read_text().strip()

if not sid:
    print(
        "voice-off: no session_id given and no CURRENT_SESSION file — "
        "nothing to do.",
        file=sys.stderr,
    )
    sys.exit(0)

leave = _resolve_tool("voice_leave_room")
raw = asyncio.run(leave(session_id=sid))
payload = json.loads(raw)
print(json.dumps(payload, indent=2))

# Clear CURRENT_SESSION so the next voice-on starts cleanly
cur = livekit_bridge.voice_pipe_dir() / "CURRENT_SESSION"
try:
    cur.unlink()
except FileNotFoundError:
    pass
PYEOF
