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

Pipe layout under `~/.poindexter/voice/<session_id>.{in,out,lock,status,log}`:

| File      | Direction         | Format                                                     |
| --------- | ----------------- | ---------------------------------------------------------- |
| `.in`     | bridge → session  | One transcript per line                                    |
| `.out`    | session → bridge  | One TTS request per line                                   |
| `.lock`   | bridge → on-disk  | Worker's PID; gone when down (the leave-by-PID handle)     |
| `.status` | bridge → launcher | Readiness: `connecting` → `ready` → `stopped` / `error: …` |
| `.log`    | launcher → disk   | Captured worker stdout/stderr (error tails read from here) |

## Process model — subprocess-spawned worker (#1010)

Since [Glad-Labs/glad-labs-stack#1010](https://github.com/Glad-Labs/glad-labs-stack/issues/1010)
the bridge worker runs as a **separate Python subprocess**
(`mcp-server-voice/bridge_worker.py`), not as an `asyncio` task inside the
long-lived MCP server. `voice_join_room` is a thin launcher:

1. `livekit_bridge.spawn_bridge_subprocess` `Popen`-launches
   `bridge_worker.py` **detached + hidden** (no console window on Windows,
   `start_new_session` on POSIX), passing the session id + the
   `BridgeConfig` JSON via env. LiveKit creds + `DATABASE_URL` are
   inherited from the MCP server's environment.
2. The child imports **fresh on-disk** `livekit_bridge` /
   `audio_plane_pipecat` / `services.voice_pipecat` code and runs the worker
   (`start_bridge` → `_bridge_main`), writing its **PID to `<sid>.lock`**
   and its readiness to **`<sid>.status`** (`connecting` → `ready`, or
   `error: <repr>` if the audio plane fails to connect).
3. The launcher polls `<sid>.status` (up to 30s — covers a cold
   faster-whisper model load) and returns the PID on `ready`, or raises a
   loud `RuntimeError` with the `<sid>.log` tail on error / process-death /
   timeout.
4. `voice_leave_room` tries the in-process registry first, then falls back
   to **leave-by-PID** (`terminate_bridge_process` reads `<sid>.lock` and
   signals the subprocess cross-platform).

**Why a subprocess:** the MCP server is long-lived, so an in-process worker
binds to whatever modules that process loaded at startup. After any code
change (e.g. the Pipecat migration) the running server keeps using **stale
cached modules** until restarted — and a mobile operator can't restart it.
The visible symptom was a deaf bridge (the `<sid>.in` pipe never filled)
while a standalone process on fresh code worked. Spawning a fresh
interpreter per `voice_join_room` sidesteps module staleness entirely.

**Escape hatch:** set `VOICE_BRIDGE_INPROCESS=1` to run the worker
in-process via the legacy `start_bridge` path (unit tests + interactive
debugging). Default is subprocess.

## How to use it from a Claude Code session

```text
> voice-on
voice on, talk to me. session_id=vb-1a2b3c4d
  in_pipe:  <USERPROFILE>\.poindexter\voice\vb-1a2b3c4d.in
  out_pipe: <USERPROFILE>\.poindexter\voice\vb-1a2b3c4d.out
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
2. The bridge selects the `PipecatAudioMediaPlane` by default now (shared
   `services/voice_pipecat.py`); no manual wiring needed.
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
- **`.lock` file stuck**. The `.lock` file holds the worker subprocess's
  PID and is the leave-by-PID handle (`terminate_bridge_process` reads it).
  `voice_leave_room` unlinks it on a clean leave; if a worker is SIGKILLed
  out-of-band the lock can linger with a dead PID. A fresh `voice-on`
  starts a new session id (and `ensure_session_pipes` clears any stale
  `.status`), so a stale lock under an old id is harmless — but you can
  `rm ~/.poindexter/voice/<sid>.lock ~/.poindexter/voice/<sid>.status` to
  tidy up.
- **Worker never reaches `ready`**. `voice_join_room` now returns an
  explicit error containing the tail of `~/.poindexter/voice/<sid>.log`
  (the captured worker stdout/stderr) when the subprocess crashes, reports
  `error: …` in `<sid>.status`, or times out. Read that tail first — it
  usually names the missing audio extra or the bad LiveKit cred directly.

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

## Audio plane

PR #1 shipped the **control plane** (session lifecycle, pipe plumbing,
chunking, watchdog, MCP tool surface, slash commands, tests, docs)
behind an `AudioMediaPlane` interface defaulting to `NoopAudioMediaPlane`
— silent stub for fast tests and CI.

PR #2 (this section) lands the real audio: `PipecatAudioMediaPlane` in
`mcp-server-voice/audio_plane_pipecat.py`. When the bridge worker spins
up it now picks the Pipecat plane by default, joins the LiveKit room as
`claude-bridge-<sid>`, runs faster-whisper STT on inbound audio (firing
the bridge's pipe-write closure on every Silero VAD utterance-end), and
TTS-publishes Kokoro audio for every line written to `.out`. The
shared Pipecat plumbing lives in
`src/cofounder_agent/services/voice_pipecat.py`, used by both the
always-on `voice-agent-livekit` container and this bridge so the two
surfaces can never drift on Pipecat / Whisper / Kokoro version state.

### What's now real vs. what was no-op

| Path                                     | PR #1 (no-op)            | PR #2 (Pipecat plane)                                           |
| ---------------------------------------- | ------------------------ | --------------------------------------------------------------- |
| `voice_join_room` → bridge worker        | Logs "would join room X" | Joins LiveKit room with a JWT, subscribes to participants       |
| Inbound audio → `<sid>.in` pipe          | Only via test hook       | Real Whisper STT + Silero VAD, transcripts append on speech-end |
| `voice_speak` → outbound audio in room   | Logs "would TTS-speak"   | Real Kokoro TTS published to the room's audio track             |
| `voice_leave_room` → graceful disconnect | Registry cleanup only    | Cancels Pipecat runner + TTS pump, closes LiveKit transport     |

### Latency expectations

Measured on Matt's 5090 box with `voice_bridge_stt_model=base.en` and
`voice_bridge_tts_voice=af_bella`:

| Phase                             | Warm path   | Cold first call                    |
| --------------------------------- | ----------- | ---------------------------------- |
| STT (end-of-speech → `.in`)       | ~600ms-1.2s | ~2-3s (Whisper model load)         |
| TTS (`.out` line → audio in room) | ~400-800ms  | ~1.5-2.5s (Kokoro voice pack load) |

CPU-only deployments (public Poindexter on a laptop) trend ~5x slower
on STT and ~3x slower on TTS — manageable for a quick check-in but
noticeably laggy for back-and-forth dev pairing. Bump
`voice_bridge_stt_model` to `tiny.en` if a CPU-only operator wants
speed over accuracy.

### Fallback to the no-op plane

Every audio plane is configurable via env var, so an operator with a
broken audio driver / CUDA mismatch / missing model can keep the
control plane working without uninstalling deps:

```bash
VOICE_BRIDGE_AUDIO_PLANE=noop uv run --directory mcp-server-voice python -m server
```

In this mode `voice_join_room` still spins up a worker, the `.in` /
`.out` pipes still work, but the bridge logs `[noop-media]` instead of
moving audio bytes. Useful for unit-testing slash commands when the
GPU is busy or in CI where Pipecat / livekit aren't installed.

Per `feedback_no_silent_defaults`, an unknown value (anything other
than `pipecat`, blank, or `noop`) raises a `RuntimeError` at start-up
rather than silently falling back. The env var is the only switch —
the bridge does not consult `app_settings` for plane selection (audio
hardware availability is a deploy-time fact, not a runtime
configuration).

### Local smoke

The control-plane smoke (no GPU, no LiveKit) still ships:

```bash
python mcp-server-voice/scripts/test_voice_bridge_smoke.py
```

For the full audio round-trip against a running LiveKit + Whisper +
Kokoro stack:

```bash
# Bring the LiveKit container up
docker compose -f docker-compose.local.yml up -d livekit

# Set the LiveKit creds (same values as docker-compose.local.yml)
export LIVEKIT_URL=ws://localhost:7880
export LIVEKIT_API_KEY=devkey
export LIVEKIT_API_SECRET=devsecret_change_me_change_me_change_me

# Run the round-trip — joins the room as a second participant, publishes
# a Whisper-decodable Kokoro clip, asserts STT lands on .in, writes a
# reply to .out, asserts TTS leaves the bridge worker.
poetry run python mcp-server-voice/scripts/test_voice_bridge_round_trip.py
```

The script prints latency readings for STT and TTS at the bottom —
useful when tuning `voice_bridge_stt_model` / `voice_bridge_tts_voice`
or evaluating a new Pipecat upgrade.
