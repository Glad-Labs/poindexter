---
name: voice-speak
description: Speak text into the active LiveKit voice bridge — TTS the assistant's reply into the room. Use when voice mode is on and the assistant wants to vocalize a reply, or when the user says "say this out loud".
---

# Voice Speak

TTS the given text into the active bridge session's LiveKit room. Long
replies (>500 chars by default — see `voice_bridge_chunk_max_chars`)
are chunked at sentence boundaries so the user can interrupt
mid-reply.

The session id is read from `~/.poindexter/voice/CURRENT_SESSION`
(written by `voice-on`). Override by passing the id explicitly as the
second argument.

## Usage

```bash
scripts/run.sh "<text to speak>" [session_id]
```

## Parameters

- **text** (string, required): What to TTS into the room.
- **session_id** (string, optional): Bridge session id. Defaults to
  whatever `voice-on` last wrote to
  `~/.poindexter/voice/CURRENT_SESSION`.

## Output

JSON object with:

- `status`: `"queued"` (success) or `"not_running"` (caller needs to re-`voice-on`)
- `session_id`: the bridge session that received the text
- `chunks`: number of TTS chunks emitted (sentence-bounded for interruptibility)
