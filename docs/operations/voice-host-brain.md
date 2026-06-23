# Voice host-brain — dev-on-the-go for the claude-code room (#1006)

> **Production status (2026-06-22):** `voice_agent_claude_code_enabled=false`,
> container removed. The poindexter room (`poindexter-voice-agent-livekit`) is
> unaffected. To re-enable: seed the token, start the daemon (step 1–2 below),
> flip `voice_agent_claude_code_enabled=true`, and `docker compose up -d` the
> `claude-code` profile.

The `claude-code` voice room lets you talk to a **full dev-capable** Claude Code
session by voice. Its brain runs `claude -p` on the **host** (full repo + git +
write + every host MCP server), not inside the read-only voice container. This
doc covers how it's wired, how to keep it running, and how to turn it off.

## Architecture

```
phone/browser ──LiveKit──▶ poindexter-voice-agent-claude-code ──HTTP POST /turn──▶ voice_brain_host.py (HOST)
                            (audio only: Whisper + Kokoro)        host.docker.internal:8123        │
                                                                                                   ▼
                                                                                         claude -p  (full repo,
                                                                                         git, write, MCP, dontAsk)
```

The container owns the audio pipeline; each transcribed turn is POSTed to the
host daemon, which runs `claude -p` and returns text the container speaks back.
The container keeps all session state (pinned id, auto-reset, create/resume
recovery); the daemon is **stateless** and runs exactly what each call says.

## Security model

This is a **voice → host-RCE** endpoint, treated as one:

- **Bearer token** on every `/turn` (constant-time compared; never logged).
- **No shell** — `claude` is invoked with a list argv; the prompt text only ever
  travels on stdin. `session_id` must be a UUID, `permission_mode` is allow-listed.
- **Not externally reachable** — the daemon binds the host + docker network only
  (`host.docker.internal:8123`); the real entry surface is the LiveKit room,
  which is **Tailscale-gated** (`Tailscale-User-Login`, tailnet-only).
- `permission_mode=dontAsk` is deliberate (voice can't answer permission
  prompts) and is what makes this host-RCE — keep the room gated.

## Components

| Piece                                      | Where                 | What                                                                              |
| ------------------------------------------ | --------------------- | --------------------------------------------------------------------------------- |
| `scripts/voice_brain_host.py`              | host                  | stdlib HTTP daemon, `POST /turn` + `GET /healthz`                                 |
| `scripts/voice-brain-host.ps1`             | host                  | install/control the daemon as a hidden, self-restarting scheduled task            |
| `~/.poindexter/voice_brain_token`          | host                  | the bearer token (48 chars). Same value the container sends.                      |
| `~/.poindexter/voice_brain_host.log`       | host                  | the hidden daemon's log (pythonw has no console)                                  |
| `voice_agent_claude_code_host_brain_url`   | app_settings          | `http://host.docker.internal:8123/turn`. **Empty = read-only in-container mode.** |
| `voice_agent_claude_code_host_brain_token` | app_settings (secret) | bearer the container sends; must equal the token file                             |

## Activation

1. **Token** — generate once if absent (never echo it):

   ```powershell
   if (-not (Test-Path "$env:USERPROFILE\.poindexter\voice_brain_token")) {
     [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(36)) `
       | Set-Content -NoNewline "$env:USERPROFILE\.poindexter\voice_brain_token"
   }
   ```

2. **Start the daemon (durable)** — registers a hidden, logon-triggered,
   self-restarting scheduled task and starts it:

   ```powershell
   .\scripts\voice-brain-host.ps1 -Install
   .\scripts\voice-brain-host.ps1 -Status   # task state + /healthz probe
   ```

   The daemon is self-configuring: it reads the token from
   `~/.poindexter/voice_brain_token` and derives the repo root from its own
   location, so the task definition carries **no secret and no hardcoded path**.

3. **Point the container at it** — set the two app_settings. The URL is plain;
   the token is a secret, so it MUST be written through `plugins.secrets.set_secret`
   (which encrypts via `POINDEXTER_SECRET_KEY`) — a raw `settings set` stores it
   plaintext:

   ```python
   from plugins.secrets import set_secret
   await set_secret(conn, "voice_agent_claude_code_host_brain_token", token)  # encrypted
   await conn.execute(
       "UPDATE app_settings SET value=$1 WHERE key='voice_agent_claude_code_host_brain_url'",
       "http://host.docker.internal:8123/turn",
   )
   ```

4. **Restart the container** so it re-reads the URL and switches modes:

   ```bash
   docker compose -f docker-compose.local.yml restart voice-agent-claude-code
   docker logs poindexter-voice-agent-claude-code | grep HOST-BRAIN
   # -> "HOST-BRAIN mode: turns execute on the host via http://host.docker.internal:8123/turn"
   ```

## Container-mode is deprecated (host-brain is the supported path)

When `voice_agent_claude_code_host_brain_url` is **empty**, the bot falls back to
running `claude -p` **inside the voice container** — a **deprecated, degraded**
mode (#1006):

- `/app` is mounted `:ro` with no `.git`/toolchain → **no coding ability**.
- The container has no `~/.claude.json` → the CLI may not even start.

It's kept only as graceful degradation (it can still read + talk if the host
daemon is unreachable), and the service logs a loud `CONTAINER mode (DEPRECATED)`
warning so a missing `host_brain_url` never passes silently. **Always run
host-brain for the claude-code room.** To intentionally drop to the degraded
fallback, set `voice_agent_claude_code_host_brain_url=''` and restart the
container; optionally stop the daemon (`.\scripts\voice-brain-host.ps1 -Stop`).

## Memory & context

Host-brain `claude` runs with `cwd=<repo root>`, so Claude Code namespaces its
auto-memory under `~/.claude/projects/C--Users-mattm-glad-labs-website/memory/`.
To give the voice bot the **full operator brain** (preferences, feedback,
project decisions — the same memory an interactive session sees), that
namespace's `memory` dir is a **junction** to the canonical operator memory:

```
~/.claude/projects/C--Users-mattm-glad-labs-website/memory  ──▶ (junction)
~/.claude/projects/C--Users-mattm/memory
```

Create/repair it with:

```powershell
$op = "$env:USERPROFILE\.claude\projects\C--Users-mattm\memory"
$gl = "$env:USERPROFILE\.claude\projects\C--Users-mattm-glad-labs-website\memory"
if (Test-Path $gl) { Remove-Item -Recurse -Force $gl }   # back up first if it holds unmerged notes
New-Item -ItemType Junction -Path $gl -Target $op | Out-Null
```

A **junction** (not a symlink) is used so it resolves natively for the host
`claude` without elevation. The repo-specific notes that used to live in the
gl-website namespace were folded into the operator memory (one unified brain).

## The persistence task

`-Install` registers **"Glad Labs - Voice Host Brain"** with:

- **AtLogOn** trigger (current user) — the daemon needs the operator's
  interactive session so `claude` runs under the user's OAuth, not SYSTEM.
- **Hidden** + `pythonw` — no console window (hidden-background-job policy).
  Because pythonw has no stderr, the daemon auto-logs to
  `~/.poindexter/voice_brain_host.log`.
- **Restart-on-crash** (`RestartCount`/`RestartInterval`) and **no execution
  time limit** — it's a long-lived daemon.

So it comes back after reboot/logoff/crash on its own. The container's
`restart: unless-stopped` policy re-joins the room on Docker start, so the whole
path self-heals.

## Cost

Host-brain turns run the full interactive model (opus-4-8, large context), so a
single turn can cost **~$0.40+** and burns into the API rate-limit window. This
is dev-on-the-go convenience, not a cheap chatbot — use the `poindexter` room
(Emma/GLM, local + free) for routine ops questions.

## Troubleshooting

| Symptom                                                  | Check                                                                                                                                                               |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Container logs "read-only in-container" not "HOST-BRAIN" | `voice_agent_claude_code_host_brain_url` empty, or container not restarted since it was set                                                                         |
| `/turn` → 401                                            | token mismatch — `voice_agent_claude_code_host_brain_token` (decrypted) must equal `~/.poindexter/voice_brain_token`                                                |
| Container can't reach daemon                             | `docker exec poindexter-voice-agent-claude-code python -c "import urllib.request;print(urllib.request.urlopen('http://host.docker.internal:8123/healthz').read())"` |
| Daemon not running                                       | `.\scripts\voice-brain-host.ps1 -Status`; tail `~/.poindexter/voice_brain_host.log`                                                                                 |
| `claude -p exited 1`                                     | check the daemon log; usually a session-id collision (handled by the container's create→resume recovery) or a logged-out host `claude`                              |
