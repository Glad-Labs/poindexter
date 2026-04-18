# OpenClaw Configuration (Git-Tracked)

The canonical OpenClaw config lives here as a template. Secrets are stored
in `~/.openclaw/workspace/.env` (never committed to git).

## Files

- `openclaw.json.template` — Full config with `${PLACEHOLDER}` for secrets
- `sync-config.ps1` — Generates `~/.openclaw/openclaw.json` from template + secrets

## Usage

### Sync config after editing the template

```powershell
.\infrastructure\openclaw\sync-config.ps1
```

### Dry run (preview without writing)

```powershell
.\infrastructure\openclaw\sync-config.ps1 -DryRun
```

### Required secrets in `~/.openclaw/workspace/.env`

These are substituted into `openclaw.json` by `sync-config.ps1`:

```
ANTHROPIC_API_KEY=...
GEMINI_API_KEY=...
NOTION_API_KEY=...
ELEVENLABS_API_KEY=...
TELEGRAM_BOT_TOKEN=...
GOPLACES_API_KEY=...
```

`DISCORD_BOT_TOKEN` is read directly by the OpenClaw gateway from
the environment at runtime — it's not in the template file.

## Self-Healing Watchdog

```powershell
# One-shot check
.\scripts\openclaw-watchdog.ps1

# Loop (every 60 seconds)
.\scripts\openclaw-watchdog.ps1 -Loop -IntervalSeconds 60

# Install as Windows Scheduled Task (every 2 minutes)
.\scripts\openclaw-watchdog.ps1 -Install

# Remove Scheduled Task
.\scripts\openclaw-watchdog.ps1 -Uninstall
```

Logs: `~/.openclaw/logs/watchdog.log`
