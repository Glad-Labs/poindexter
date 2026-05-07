# Voice bridge — voice as a session-agnostic UI surface

The LiveKit MCP bridge lets a _running_ Claude Code session use voice as
a UI surface: voice in becomes the next user input to that session;
session output becomes voice out. No subprocess spawn, no fresh
`claude -p` per turn, no auth coupling — the live session keeps its full
context, full toolchain, full memory.

This is the architecturally-correct alternative to the
`voice_agent_brain_mode=claude-code` subprocess-spawn path that the
always-on `voice-agent-livekit` container supported. The container
itself is unchanged — it stays as the always-on phone-tap-to-join
interface using ollama as the brain. The bridge is _additive_: it
claims a separate participant identity (`claude-bridge`) on the same
LiveKit room when an interactive Claude Code session wants the floor.

## Architecture

```
   ┌─ Claude Code session ──────────────────────────────┐
   │                                                    │
   │  user invokes /voice-on                            │
   │  → MCP tool: voice_join_room(channel_id="ops")     │
   │      ├─ MCP server starts a Pipecat-style worker   │
   │      ├─ Worker joins LiveKit room as agent         │
   │      ├─ Whisper STT → transcripts                  │
   │      ├─ On utterance-end:                          │
   │      │    └─ Append to ~/.poindexter/voice/<sid>.in│
   │      │       (session's Monitor wakes with line)   │
   │      └─ Returns immediately; daemon stays alive    │
   │                                                    │
   │  session generates reply → /voice-speak <text>     │
   │  → MCP tool: voice_speak(text="...", session_id=…) │
   │      ├─ Chunks at sentence boundaries (>500 chars) │
   │      └─ Worker enqueues to Kokoro → plays in room  │
   │                                                    │
   │  /voice-off                                        │
   │  → MCP tool: voice_leave_room(session_id=…)        │
   │      └─ Worker disconnects, idempotent             │
   └────────────────────────────────────────────────────┘
```

Pipe layout under `~/.poindexter/voice/<session_id>.{in,out,lock}`:

| File    | Direction        | Format                       |
| ------- | ---------------- | ---------------------------- |
| `.in`   | bridge → session | One transcript per line      |
| `.out`  | session → bridge | One TTS request per line     |
| `.lock` | bridge → on-disk | Worker's PID; gone when down |

## How to use it from a Claude Code session

```text
> voice-on
voice on, talk to me. session_id=vb-1a2b3c4d
  in_pipe:  C:\Users\mattm\.poindexter\voice\vb-1a2b3c4d.in
  out_pipe: C:\Users\mattm\.poindexter\voice\vb-1a2b3c4d.out
  room:     claude-bridge
  watchdog: 1800s

> [user starts talking; .in pipe fills with transcripts]
> [Claude generates reply, calls voice-speak "..."]

> voice-off
{"status": "stopped", "session_id": "vb-1a2b3c4d"}
```

The slash commands (`voice-on`, `voice-speak`, `voice-off`) live under
`skills/poindexter/voice-*`. They call the MCP tools directly and
manage `~/.poindexter/voice/CURRENT_SESSION` so the operator doesn't
have to retype the session id between calls.

To wire transcripts back as user input automatically, point a Monitor
task at the `.in` pipe:

```bash
tail -F ~/.poindexter/voice/<session_id>.in
```

Each line emitted is one utterance — feed it to the session as the next
user message. The bridge does not auto-feed; that's the slash-command
surface's responsibility (intentional separation of mechanism from
policy — the session may want to chunk multiple utterances together
into one logical turn, gate on a wake word, etc.).

## How it differs from voice-agent-livekit

| Surface               | Brain                           | Trigger                     | When to use                                                                                    |
| --------------------- | ------------------------------- | --------------------------- | ---------------------------------------------------------------------------------------------- |
| `voice-agent-livekit` | ollama                          | Always on                   | "Phone-tap-to-join" — quick status questions, snappy local LLM, no session state               |
| MCP bridge (this doc) | the calling Claude Code session | Operator invokes `voice-on` | "Voice-mode for a real dev pairing" — full repo / MCP / edit access, the session keeps context |

Both can coexist on the same room — the always-on bot uses identity
`poindexter-bot`, the bridge uses `claude-bridge`. If only one is
present, the other's slot is open. If both are present, the operator
talks to whichever picks up the floor first; the always-on bot's tools
are read-only ollama, the bridge has the full live Claude Code session.

## Configuration (DB-first)

All knobs live in `app_settings` (seeded by migration
`20260507_022644_seed_voice_bridge_app_settings`):

| Key                                | Default         | Purpose                                              |
| ---------------------------------- | --------------- | ---------------------------------------------------- |
| `voice_bridge_enabled`             | `true`          | Master switch — when false, `voice_join_room` errors |
| `voice_default_room`               | `claude-bridge` | LiveKit room when `channel_id` is omitted            |
| `voice_bridge_stt_model`           | `base.en`       | faster-whisper model id                              |
| `voice_bridge_tts_voice`           | `af_bella`      | Kokoro voice id                                      |
| `voice_bridge_max_session_seconds` | `1800`          | Hard timeout — bridge auto-leaves                    |
| `voice_bridge_chunk_max_chars`     | `500`           | TTS chunk size for interruptibility                  |

Override at runtime:

```bash
poindexter set voice_default_room ops
poindexter set voice_bridge_chunk_max_chars 300
```

LiveKit credentials (`LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`) are
**not** new settings — the bridge reuses the same env vars the
always-on `voice-agent-livekit` container reads. One source of truth,
one set of creds to rotate.

## Live verification

The control plane (sessions, pipes, chunking, watchdog) is verified by
the unit suite:

```bash
cd mcp-server-voice && python -m pytest tests/test_livekit_bridge.py -v
```

23 tests, runs in <1s, no network or audio. Plus a quick smoke test
that exercises the full control plane end-to-end against the no-op
audio plane:

```bash
python mcp-server-voice/scripts/test_voice_bridge_smoke.py
```

The actual LiveKit + Whisper + Kokoro audio round-trip needs the
voice-agent stack running and a 5090 (or CPU-only Whisper for the
public release). To smoke that path:

1. Bring the stack up: `docker compose -f docker-compose.local.yml up -d livekit voice-agent-livekit`
2. Plug a `PipecatAudioMediaPlane` into the bridge (factored into
   `services/voice_pipecat.py` in PR #2 — see "Deferred" below).
3. Open `https://meet.livekit.io`, plug in `LIVEKIT_URL` + a client
   token (`python -m services.voice_agent_livekit --print-client-token
--room claude-bridge --identity me`), join the room.
4. Run `voice-on`, talk; verify `.in` fills; trigger `voice-speak` from
   the session; verify the room hears the TTS.

## Troubleshooting

### Bridge worker dying

Check the MCP server log for `"Bridge session ... closed"`. Common
causes:

- **Watchdog tripped** (`max_session_seconds` hit). Default 30 minutes;
  bump via `poindexter set voice_bridge_max_session_seconds 7200`.
- **Audio plane connect failed**. The Pipecat plane raises if the
  LiveKit URL is unreachable or the JWT is rejected. Check
  `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` env vars
  match the LiveKit container's compose env.
- **`.lock` file stuck**. If a previous run crashed without cleaning up,
  the `.lock` file lingers. The bridge doesn't currently respect
  stale locks (the registry is in-process, so a "stale lock" only
  matters across MCP server restarts). Just `rm
~/.poindexter/voice/<sid>.lock` and re-`voice-on`.

### Transcripts arriving slowly

`voice_bridge_stt_model` defaults to `base.en` — CPU-friendly but
slower than `large-v3`. If you have GPU headroom:

```bash
poindexter set voice_bridge_stt_model large-v3
```

Also check Silero VAD's silence threshold — Pipecat's default is fine
for "phone call" cadence but cuts off mid-sentence on slower speakers.

### TTS choppy

The bridge chunks long replies at sentence boundaries — each chunk is
one Kokoro request. If chunks land too close together, set
`voice_bridge_chunk_max_chars` higher (up to ~2000) so longer chunks
emit fewer separate TTS requests. The trade-off is interruptibility —
the user can only interrupt at chunk boundaries.

## Deferred to PR #2

The first cut ships the **control plane** (session lifecycle, pipe
plumbing, chunking, watchdog, MCP tool surface, slash commands, tests,
docs). The audio media plane is wrapped behind an interface
(`AudioMediaPlane`) with a `NoopAudioMediaPlane` default — every part
the slash commands and tests touch works against the no-op plane.

PR #2 will land `services/voice_pipecat.py` — a shared module that
factors the Pipecat pipeline (Whisper + Silero + Kokoro + LiveKit
transport) out of `voice_agent_livekit.py` so the bridge can mount one
without duplicating dependency state in the MCP server process. At
that point the bridge swaps `NoopAudioMediaPlane` for
`PipecatAudioMediaPlane` in `start_bridge` and the audio round-trip
goes live.

The split keeps PR #1 under the LOC ceiling and lets the audio plane
get its own focused review — the surface area is non-trivial (RTX 5090
GPU sharing, Pipecat lifecycle, voice cred rotation) and shouldn't be
buried in a 3000-line PR.
