---
name: voice-off
description: Turn off the LiveKit voice bridge for this Claude Code session. Use when the user says "voice off", "/voice off", "stop voice", "leave the voice room", or "end voice".
---

# Voice Off

Tears down the LiveKit ↔ Claude Code bridge for the current session.
Idempotent — safe to call when no bridge is running (returns
`status="not_running"`).

The session id is read from `~/.poindexter/voice/CURRENT_SESSION`
(written by `voice-on`). Override by passing the id explicitly.

## Usage

```bash
scripts/run.sh [session_id]
```

## Parameters

- **session_id** (string, optional): Bridge session id to terminate.
  Defaults to whatever `voice-on` last wrote to
  `~/.poindexter/voice/CURRENT_SESSION`.
