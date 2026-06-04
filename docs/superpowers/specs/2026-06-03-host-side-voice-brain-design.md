# Host-Side Voice Brain — full dev-on-the-go (#1006)

**Status:** Approved design (2026-06-03) · **Issue:** [glad-labs-stack#1006](https://github.com/Glad-Labs/glad-labs-stack/issues/1006)

## Problem

The always-on voice bot runs `claude -p` **inside** the voice container (cwd `/app`, mounted `:ro`, only `src/cofounder_agent`, no `.git`, no toolchain). It can read and talk but **cannot do dev work** — no edits, no tests, no commits, no PRs, no access to the rest of the repo. The "dev on the go from your phone" promise in `voice_agent_claude_code.py`'s docstring is aspirational; the container is a read-only slice.

## Decision

Move the **brain to the host**. The container keeps the audio pipeline (LiveKit + Whisper + Kokoro + VAD); the brain stage stops spawning `claude -p` locally and instead calls a small **always-on host daemon** that runs `claude -p` on the host with cwd = repo root. A host `claude` has everything an interactive session has — full repo + write + git, **all** MCP servers (Poindexter KB included, natively — no mounting/wiring), the project memory + CLAUDE.md. One move fixes dev-power + KB + memory together.

The pinned session, auto-reset, resume-recovery, markdown-for-TTS, and Telegram mirror **stay in the container** — only the _exec_ moves to the host.

## Architecture

```
phone (Tailscale-gated)
  → LiveKit room → voice-agent container
      Whisper STT → ClaudeCodeBridgeLLMService → Kokoro TTS
                          │ (host-exec mode)
                          ▼  HTTP POST /turn  (Bearer token)
              host.docker.internal:<port>  ← voice_brain_host daemon (HOST)
                          runs: claude -p --permission-mode dontAsk
                                [--session-id|--resume] <uuid>
                                --output-format json  (cwd = repo root)
                          → { returncode, stdout, stderr }
```

Transport is HTTP over `host.docker.internal` — already wired for the container and already used for host Ollama (`OLLAMA_URL: http://host.docker.internal:11434`), so this is a proven path, not new plumbing.

## Host daemon (`scripts/voice_brain_host.py`)

- **Stdlib only** (`http.server` + `subprocess`) so it runs on the host with bare `python`, no poetry env / extra deps / import-chain risk.
- `POST /turn` body `{ text, session_id, first_turn, permission_mode, extra_args }` → builds the claude argv (mirrors the container's `_build_argv`), runs it with `cwd = <repo root>`, returns `{ returncode, stdout, stderr }`. **Stateless** — runs exactly what each call says; the container owns session state + recovery.
- `GET /healthz` → liveness.

### Security (this is a voice→host-RCE endpoint — treat it as one)

- **Bearer token** required on every request; constant-time compare; 401 otherwise. Token is a generated secret, stored in `app_settings` (`is_secret`) for the container to read and passed to the daemon via its launch env. Never logged.
- **No shell.** `subprocess` with a **list** argv, `shell=False`. The prompt text is fed via **stdin**, never the command line.
- **Argument validation.** `session_id` must match a UUID regex (prevents argv injection); `permission_mode` from a fixed allowlist; `extra_args` accepted only as a list of strings (operator-controlled via DB config).
- **Binding.** Listens on the host so `host.docker.internal` reaches it; the port is host-local + docker-network only (not forwarded externally), and Windows firewall blocks inbound by default. The real entry surface is the LiveKit room, which is Tailscale-gated.
- **Permission posture: `--permission-mode dontAsk`** (operator-approved). The bot can edit/run/commit/push/publish on the host with no prompt — intended, so dev-on-the-go works hands-free. Guardrails are the Tailscale room gate + token + in-network.

## Container change (`voice_agent_claude_code.py`)

- Abstract the exec: `_exec_claude(user_text) → (rc, stdout, stderr)` with two impls — **local** (existing `create_subprocess_exec`) and **host** (HTTP POST to the daemon). `_spawn_claude` calls `_exec_claude` and keeps the existing recovery (#431 + #1006) by re-issuing with the flipped flag.
- **Back-compat:** host mode is opt-in. If `voice_agent_claude_code_host_brain_url` is unset/empty → local subprocess (today's behavior). If set → host exec. (Per the backcompat rule.)
- Wiring in `voice_agent_livekit.py`: read `voice_agent_claude_code_host_brain_url` + the token (secret) and pass into the service.

## Config (DB-driven `app_settings`)

| Key                                        | Default             | Purpose                                                  |
| ------------------------------------------ | ------------------- | -------------------------------------------------------- |
| `voice_agent_claude_code_host_brain_url`   | `''` (= local mode) | Daemon URL, e.g. `http://host.docker.internal:8123/turn` |
| `voice_agent_claude_code_host_brain_token` | `''` (secret)       | Bearer token shared with the daemon                      |

A migration seeds both empty (local mode stays the default until the operator opts in).

## Session / memory namespace

Host `claude -p` runs with cwd = repo root, so it loads the repo's memory namespace + CLAUDE.md and all host MCP servers. The pinned session id is re-created in this namespace on first turn via the resume-recovery (#1090) — continuity + auto-reset unchanged.

## Host process management

The daemon is started hidden (no popup — `pythonw` / `-WindowStyle Hidden` / `CREATE_NO_WINDOW`) and kept up (Windows scheduled task or the operator's startup), launched with `VOICE_BRAIN_TOKEN`, `VOICE_BRAIN_CWD` (repo root), `VOICE_BRAIN_PORT` in its env.

## Testing

- **Unit:** host-exec path (mock HTTP → asserts argv params + returns rc/stdout/stderr); recovery still fires over host exec (no-conversation-found → re-POST with create); local mode unchanged (back-compat); token sent + redacted from logs.
- **Daemon:** argv builder mirrors the container; UUID validation rejects bad session_id; missing/wrong token → 401; text goes via stdin.
- **Manual:** opt in via app_settings, start daemon, call into the room, confirm an edit/commit actually lands on the host.

## Build sequence

1. Host daemon (`scripts/voice_brain_host.py`) + its tests.
2. Container exec abstraction + host impl + recovery over host (back-compat preserved) + tests.
3. `voice_agent_livekit.py` wiring + migration.
4. Operational: generate token → app_settings, launch daemon hidden, opt the container into host mode, restart, verify a real edit/commit from voice.
