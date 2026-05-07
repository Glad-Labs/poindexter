#!/bin/bash
# scripts/run.sh — TTS text into the active voice bridge session.

set -euo pipefail

TEXT="${1:-}"
SESSION_ID="${2:-}"

if [ -z "${TEXT}" ]; then
  echo "Error: text is required"
  echo 'Usage: run.sh "<text>" [session_id]'
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../" && pwd)"
MCP_SERVER_DIR="${REPO_ROOT}/mcp-server-voice"

# Pass TEXT via env (avoids HEREDOC quoting issues with special chars).
export VOICE_SPEAK_TEXT="${TEXT}"
export VOICE_SPEAK_SID="${SESSION_ID}"

python - <<'PYEOF'
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("MCP_SERVER_DIR", ""))
# Re-resolve in case the env var didn't carry through
mcp_dir = sys.argv[0]  # placeholder; real path below
PYEOF

# The HEREDOC above was a stub; rewrite the actual script invocation.
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


text = os.environ.get("VOICE_SPEAK_TEXT", "")
sid = os.environ.get("VOICE_SPEAK_SID", "").strip()
if not sid:
    cur = livekit_bridge.voice_pipe_dir() / "CURRENT_SESSION"
    if cur.exists():
        sid = cur.read_text().strip()

if not sid:
    print(
        "voice-speak: no session_id and no CURRENT_SESSION — call voice-on "
        "first.",
        file=sys.stderr,
    )
    sys.exit(2)

speak = _resolve_tool("voice_speak")
raw = asyncio.run(speak(text=text, session_id=sid))
payload = json.loads(raw)
print(json.dumps(payload, indent=2))

# Non-zero exit if the bridge wasn't running so a hook can react.
if payload.get("status") == "not_running" or "error" in payload:
    sys.exit(3)
PYEOF
