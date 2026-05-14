# mcp-server-voice — LiveKit voice bridge MCP server

This MCP server exposes three tools (`voice_join_room`, `voice_speak`,
`voice_leave_room`) that let an _already-running_ Claude Code session use
voice as a UI surface — voice in becomes the next user input via per-session
pipes, session output written via `voice_speak` becomes voice out (chunked
at sentence boundaries for interruptibility).

The always-on `voice-agent-livekit` container is **unaffected** — this
server is _additive_. The bridge claims a separate participant identity
(`claude-bridge`) on the same LiveKit room when an interactive session
wants the floor.

## Why a separate MCP server

The voice bridge lives in its own MCP server (rather than sharing the
public `mcp-server/` surface) for four reasons:

1. **Failure isolation** — a livekit/pipecat/kokoro import explosion
   shouldn't take down `create_post`, `check_health`, or the rest of
   the public Poindexter surface. A crashed voice server only takes
   voice down.
2. **Dep weight** — the audio plane drags faster-whisper, livekit-agents,
   silero, and kokoro onto the import path. Public Poindexter installs
   shouldn't pay that GPU+model-download cost when they don't want voice.
3. **Independent restart** — bridge crashes / GPU resets can be
   recovered by restarting only this server, not the worker-facing
   Poindexter MCP tools.
4. **Opt-in distribution** — voice is an extension. Operators who want
   it register this server in `~/.claude.json` separately; the rest get
   the slim Poindexter MCP by default.

## Tools

```python
voice_join_room(channel_id: str = "", session_id: str = "") -> str
# Returns JSON: {status, session_id, room, in_pipe, out_pipe,
#                max_session_seconds, chunk_max_chars, instructions}

voice_speak(text: str, session_id: str) -> str
# Returns JSON: {status: "queued"|"not_running", session_id, chunks}

voice_leave_room(session_id: str) -> str
# Returns JSON: {status: "stopped"|"not_running", session_id}
```

All three pull defaults from `app_settings` (per `feedback_db_first_config`):

| Key                                | Default         | Purpose                                              |
| ---------------------------------- | --------------- | ---------------------------------------------------- |
| `voice_bridge_enabled`             | `true`          | Master switch — when false, `voice_join_room` errors |
| `voice_default_room`               | `claude-bridge` | LiveKit room when `channel_id` omitted               |
| `voice_bridge_stt_model`           | `base.en`       | faster-whisper model id                              |
| `voice_bridge_tts_voice`           | `af_bella`      | Kokoro voice id                                      |
| `voice_bridge_max_session_seconds` | `1800`          | Hard timeout — auto-leave                            |
| `voice_bridge_chunk_max_chars`     | `500`           | TTS chunk size for interruptibility                  |

LiveKit creds (`LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`) are reused from
the existing voice-agent compose env — no new secrets.

## Register in Claude Code

Add to `~/.claude.json` under `mcpServers`. Use a relative path for the
open-source side and an absolute path for Matt's local Windows install.

**Local install (Matt's box):**

```json
{
  "mcpServers": {
    "voice-bridge": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "<path-to-your-poindexter-checkout>/mcp-server-voice",
        "python",
        "-m",
        "server"
      ]
    }
  }
}
```

**Open-source install (relative path from repo root):**

```json
{
  "mcpServers": {
    "voice-bridge": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "./mcp-server-voice",
        "python",
        "-m",
        "server"
      ]
    }
  }
}
```

After saving, restart Claude Code so the new server is picked up.

## Testing

```bash
# Unit tests (23 tests covering chunking, pipe layout, lifecycle, watchdog)
cd mcp-server-voice && python -m pytest tests/ -q

# Control-plane smoke test (~0.6s end-to-end against the no-op audio plane)
python mcp-server-voice/scripts/test_voice_bridge_smoke.py
```

The audio media plane is wrapped behind an `AudioMediaPlane` interface.
PR #1 shipped a `NoopAudioMediaPlane` default for fast tests; PR #2
landed `PipecatAudioMediaPlane` (real Whisper STT + Kokoro TTS over
LiveKit) and made it the default. The shared Pipecat plumbing lives in
`src/cofounder_agent/services/voice_pipecat.py` so the bridge and the
always-on `voice-agent-livekit` container share one closure.

Set `VOICE_BRIDGE_AUDIO_PLANE=noop` to fall back to the silent stub —
useful for control-plane debugging or environments without the audio
deps. Unknown values raise loudly per `feedback_no_silent_defaults`.

Real-audio round-trip:

```bash
docker compose -f docker-compose.local.yml up -d livekit
export LIVEKIT_URL=ws://localhost:7880
export LIVEKIT_API_KEY=devkey LIVEKIT_API_SECRET=devsecret_change_me_change_me_change_me
poetry run python mcp-server-voice/scripts/test_voice_bridge_round_trip.py
```

## Slash commands

The skills under `skills/poindexter/voice-{on,off,speak}/` wire these
tools into Claude Code with `CURRENT_SESSION` tracking so the operator
doesn't retype session ids between calls. The slash commands import
`livekit_bridge` and `server` from this directory directly — they share
in-process state with the MCP server.
