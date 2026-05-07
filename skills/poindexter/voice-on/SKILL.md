---
name: voice-on
description: Turn on the LiveKit voice bridge for this Claude Code session. Use when the user says "voice on", "/voice on", "let me talk to you", "voice mode", "join the voice room", or "start voice".
---

# Voice On

Spins up the LiveKit ↔ Claude Code bridge for the current session: voice
in becomes the next user input via a per-session pipe; whatever Claude
generates can be sent back as voice via the `voice-speak` skill.

The always-on `voice-agent-livekit` container is unaffected — this
skill claims a separate participant identity (`claude-bridge`) on the
configured LiveKit room so both agents can coexist if needed.

## Usage

```bash
scripts/run.sh [room]
```

## Parameters

- **room** (string, optional): LiveKit room name. Defaults to
  `app_settings.voice_default_room` (seeded as `"claude-bridge"`).

## What happens

1. Calls the `voice_join_room` MCP tool — it spins up a per-session
   bridge worker and creates two pipe files under
   `~/.poindexter/voice/<session_id>.{in,out}`.
2. Returns the `session_id` so subsequent skills (`voice-speak`,
   `voice-off`) know which bridge to talk to.
3. Prints "voice on, talk to me" plus the session id.

The session id is also stored in `~/.poindexter/voice/CURRENT_SESSION`
so a Monitor watcher / hook can pick up new transcript lines from the
right `.in` pipe without the user retyping the id.

## After enabling

To consume voice transcripts as user input, point a Monitor task at
the `.in` pipe:

```
Monitor: tail -F ~/.poindexter/voice/<session_id>.in
```

Each line emitted by the watcher is one utterance — feed it back to
the session as the next user message.

To send the assistant's reply as voice, call:

```
voice-speak "<text>"
```

When done: `voice-off`.
