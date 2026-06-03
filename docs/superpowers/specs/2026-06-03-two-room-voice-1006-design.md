# Two-Room Voice Architecture — Always-On Voice (#1006)

**Status:** Approved design (2026-06-03) · **Issue:** [glad-labs-stack#1006](https://github.com/Glad-Labs/glad-labs-stack/issues/1006)

## Overview

Split the voice line into **two LiveKit rooms** so the always-on Poindexter ops
assistant and the Claude Code dev agent never double-answer, and make the line
**always-on** — Matt can call in with _no Claude Code session open_ and still
talk to either surface.

- **`poindexter`** room — "Emma", the system ops assistant (local GLM brain + a
  few tools). Already built; the only work is keeping it up.
- **`claude-code`** room — the dev agent (`claude -p`). **Hybrid**: a live
  Claude Code session's per-session bridge takes the mic when present; otherwise
  an always-on `claude -p` fallback bot answers.

## Goals

1. Always-on voice for both surfaces, callable any time with no session open.
2. No two-responder conflict (today both bots land in one room).
3. Conversation continuity for the dev agent — a pinned, resumable session — and
   bounded growth via auto-reset.
4. A live Claude Code session can still take the dev mic when it wants to.

## Non-Goals

- Changing the per-session bridge's transcript/TTS **pipe protocol**
  (`<sid>.in` / `<sid>.out`) — unchanged.
- Multi-user / multi-tenant rooms.
- Changing Emma's brain (stays local GLM + the existing 3 tools).
- The standalone-bridge dev workaround (already superseded by the subprocess
  spawn fix, glad-labs-stack#1010 / #1065).

## Architecture

### Rooms

| Room          | Occupant(s)                                                                       | Brain                 | Always-on                    |
| ------------- | --------------------------------------------------------------------------------- | --------------------- | ---------------------------- |
| `poindexter`  | Emma (`poindexter-voice-agent-livekit`, identity `poindexter-bot`)                | local GLM + ops tools | Yes — container stays up     |
| `claude-code` | always-on fallback (`claude-code-bot`) **+** per-session bridge (`claude-bridge`) | `claude -p`           | Yes (fallback) + per-session |

### Claude-code room — hybrid handoff

- **Always-on fallback bot** = a _second instance of the existing
  `poindexter-voice-agent` image_ (no new Dockerfile/image), configured with
  `brain_mode=claude-code`, `room=claude-code`, identity `claude-code-bot`.
  Shells to `claude -p` with a pinned, resumable, auto-resetting session.
- **Per-session bridge** = the existing `voice_join_room` MCP /
  `livekit_bridge.spawn_bridge_subprocess` path, identity `claude-bridge`, joins
  `claude-code` when a live session takes the mic.
- **Handoff rule (single responder guaranteed):** the fallback bot subscribes to
  LiveKit participant events (it already receives `participant_connected` /
  `participant_disconnected` today). When a participant with identity prefix
  `claude-bridge` is present, the fallback **suspends** its own STT→LLM→TTS loop
  (stops responding). When that participant leaves, the fallback **resumes**.
  The fallback errs toward silence: if the handoff signal is ambiguous, it
  yields to the live session.

## The always-on `claude -p` fallback — the three #1006 gaps

### 1. Auth

Today `docker-compose.local.yml` mounts `~/.claude/` (dir) RW so the in-container
`claude` subprocess picks up skills/plugins/MCP/memory — but **not**
`~/.claude.json` (the OAuth-token file), so `claude -p` is logged-out. Add a
mount of `${USERPROFILE:-${HOME}}/.claude.json` → the container's
`$HOME/.claude.json`, **read-write** (the `claude` CLI may refresh the OAuth
token and write it back; a read-only mount would break refresh).

- **Risk:** this hands the container the host's Claude credentials. Acceptable on
  the operator's own machine; documented, and scoped to the `claude-code`
  fallback service only.

### 2. Continuity (pinned session)

- `app_settings.voice_agent_claude_code_session_id` holds the pinned dev session
  id. The bot runs `claude -p --resume <id>` (or `--session-id <id>` on the
  first turn) so it remembers context across calls — versus today's
  fresh-uuid-per-call amnesia.
- If unset, generate one, persist it to `app_settings`, and use it.

### 3. Auto-reset

Rotate to a fresh session id when **either** threshold trips (whichever first,
both DB-configurable):

- `voice_agent_claude_code_session_token_budget` — cumulative input+output
  tokens since the last reset.
- `voice_agent_claude_code_session_max_age_seconds` — wall-clock age of the
  current session.

Plus a **manual/spoken reset** ("start fresh") that rotates immediately. The new
session id is persisted to `app_settings` so it survives container restarts.

## Routing

- **`/voice/join?room=<name>`** — the join page reads the `room` query param
  (allowlist `poindexter` | `claude-code`, default `poindexter`) and mints a
  LiveKit token scoped to that room. The Tailscale-User-Login gate
  (reference: voice_join tailnet gate, PR #985) is unchanged.
- **Bridge default room flips** `poindexter` → `claude-code`: set
  `app_settings.voice_default_room = claude-code` so a session's
  `voice_join_room` lands in the dev room by default.

## Configuration (DB-driven `app_settings`)

| Key                                               | Default       | Purpose                                   |
| ------------------------------------------------- | ------------- | ----------------------------------------- |
| `voice_agent_room_name`                           | `poindexter`  | Emma's room (unchanged)                   |
| `voice_default_room`                              | `claude-code` | per-session bridge default room (flipped) |
| `voice_agent_claude_code_room`                    | `claude-code` | the fallback bot's room                   |
| `voice_agent_claude_code_session_id`              | (generated)   | pinned `--resume` session                 |
| `voice_agent_claude_code_session_token_budget`    | `200000`      | auto-reset on cumulative tokens           |
| `voice_agent_claude_code_session_max_age_seconds` | `14400` (4h)  | auto-reset on session age                 |

(Both reset thresholds ship with the explicit defaults above and are
DB-tunable — start conservative, adjust from live behaviour.)

A migration seeds the new keys.

## Components touched

- `docker-compose.local.yml` — new `voice-agent-claude-code` service (same image,
  `brain_mode=claude-code`, `room=claude-code`, identity `claude-code-bot`, plus
  the `~/.claude.json` mount). Emma's existing service unchanged except confirming
  it's up + `restart: unless-stopped`.
- `services/voice_agent_claude_code.py` (the `claude -p` brain) — session pinning
  (`--resume`), auto-reset (token/age + manual), and the participant-event
  handoff (suspend while `claude-bridge` present).
- `services/voice_agent_livekit.py` — the participant-event hook that drives the
  handoff (shared transport layer; the suspend behaviour is gated to the
  claude-code instance).
- The `/voice/join` handler (token-minting endpoint) — room-param routing.
- `voice_join_room` MCP / bridge — default room → `claude-code`.
- New migration — seed the `app_settings` keys above.

## Error handling / fail-loud

- **`claude -p` logged-out** (auth missing/expired): the fallback detects the
  auth failure and emits a `finding` (degraded) rather than silently not
  answering — same fail-loud posture as the analytics masking fix (#555).
- **Resume failure:** fall back to a fresh session id, log + emit a finding.
- **Handoff race** (both responders briefly): the fallback yields on
  `claude-bridge` presence; on ambiguity it prefers silence (the live session
  wins the mic).

## Testing

- **Unit:** auto-reset logic (token-budget trip → new id; age trip → new id;
  manual reset → new id) · handoff decision (`claude-bridge` present → suspend;
  absent → active) · room-param allowlist (unknown room → default).
- **Integration:** `/voice/join?room=` mints the correct room-scoped token ·
  fallback suspends when a mock `claude-bridge` participant joins and resumes on
  leave.
- **Manual/ops:** `?room=poindexter` → Emma answers · `?room=claude-code` →
  `claude -p` answers · run `voice_join_room` in a live session → bridge takes
  over, fallback goes quiet · `voice-off` → fallback resumes.

## Build sequence (incremental, each independently shippable)

1. **Emma always-on** — start the container + confirm `restart: unless-stopped`.
   (Quick win, independent of everything else.)
2. **Room routing** — `/voice/join?room=` + flip `voice_default_room`.
3. **claude-code fallback container** — compose service + `.claude.json` auth
   mount; verify `claude -p` answers in the `claude-code` room.
4. **Continuity** — pinned `session_id` + `--resume`.
5. **Auto-reset** — token/age thresholds + manual/spoken reset.
6. **Handoff** — fallback suspends while `claude-bridge` is present.
7. **Tests + docs.**

## Risks

- **Credential exposure:** `.claude.json` in the container = host Claude creds in
  the fallback service. Operator machine, acceptable, documented.
- **`claude -p` voice latency:** each turn spawns/resumes `claude`, slower than
  Emma's local GLM. Mitigate with `--resume` (warm context); consider streaming
  partial output to TTS later.
- **Memory pressure / OOM:** two voice-agent containers + Whisper + GLM is what
  SIGKILL'd Emma earlier (exit 137). Set resource limits + watch GPU/RAM; the
  fallback and Emma may need to share or stagger the Whisper model.
