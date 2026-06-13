# Voice settings inventory (`voice_*` app_settings)

There are **three** voice surfaces, each with its own config namespace. Same
key _names_ across them (tts voice, stt model, room) are **not duplicates** —
they belong to different components. This map exists so the next "are these
dupes?" question has an answer (glad-labs-stack#1006).

## 1. Always-on LiveKit agent — `voice_agent_*`

`services/voice_agent_livekit.py`, run as the always-on Docker daemon
(`--service`). As of the two-room split (#1006) it runs **one container per
room profile** (`--service-profile`, see `_SERVICE_PROFILES`):

| Profile       | Container                            | Room          | Brain                                    |
| ------------- | ------------------------------------ | ------------- | ---------------------------------------- |
| `default`     | `poindexter-voice-agent-livekit`     | `poindexter`  | from `voice_agent_brain_mode` (Emma/GLM) |
| `claude-code` | `poindexter-voice-agent-claude-code` | `claude-code` | **pinned** to `claude-code` (dev brain)  |

A LiveKit identity is in exactly one room, so two rooms = two processes. The
`claude-code` profile pins its brain so a flip of `voice_agent_brain_mode`
(the poindexter room's selector) can never silently turn the dev room into an
ollama bot.

### `default` profile (poindexter room) keys

| Key                                               | Purpose                                                             |
| ------------------------------------------------- | ------------------------------------------------------------------- |
| `voice_agent_livekit_enabled`                     | Master on/off for the **default** profile container                 |
| `voice_agent_room_name`                           | LiveKit room it joins (`poindexter`)                                |
| `voice_agent_identity`                            | Its participant identity                                            |
| `voice_agent_livekit_url`                         | In-network LiveKit URL (`ws://livekit:7880`)                        |
| `voice_agent_public_join_url`                     | Human-facing tap-to-join URL (Tailscale Serve, tailnet-only)        |
| `voice_agent_whisper_model`                       | STT model for the agent (`medium`)                                  |
| `voice_agent_tts_voice` / `_tts_speed`            | Kokoro voice + speed                                                |
| `voice_agent_vad_stop_secs`                       | VAD end-of-turn silence                                             |
| `voice_agent_system_prompt`                       | Emma's persona (used by the ollama brain)                           |
| `voice_agent_brain_mode`                          | Brain selector for the **default** room — `ollama` \| `claude-code` |
| `voice_agent_llm_model`                           | GLM model — used **only** when brain = `ollama`                     |
| `voice_agent_ollama_url`                          | Ollama base URL — used **only** when brain = `ollama`               |
| `voice_agent_recall_k` / `_recall_min_similarity` | Memory recall for the ollama brain                                  |

### `claude-code` profile (dev room) keys

The `voice_agent_claude_code_*` namespace. The room/identity/enabled trio
(seeded by migration `20260604_030000`) mirrors the poindexter room's so the
operator can turn the dev room on/off and rename it from the DB — no
hardcoded values in compose. The session + host-brain keys drive the
`claude -p` dev brain.

| Key                                                                 | Purpose                                                                  |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `voice_agent_claude_code_enabled`                                   | Master on/off for the **claude-code** profile container (#1006)          |
| `voice_agent_claude_code_room_name`                                 | LiveKit room it joins (`claude-code`; must match `_ALLOWED_VOICE_ROOMS`) |
| `voice_agent_claude_code_identity`                                  | Its participant identity (`claude-code-bot`)                             |
| `voice_agent_claude_code_session_id`                                | Pinned `claude -p` session (#1006)                                       |
| `voice_agent_claude_code_session_token_budget` / `_max_age_seconds` | Session auto-reset thresholds (#1006)                                    |
| `voice_agent_claude_code_host_brain_url`                            | Host-brain daemon URL — empty = run claude in-container (#1006)          |
| `voice_agent_claude_code_host_brain_token`                          | Host-brain bearer token (secret, #1006)                                  |

> The `claude-code` profile reuses the STT/TTS/VAD keys from the `default`
> set above (same Whisper/Kokoro pipeline) — it adds only the room/brain keys
> listed here.

## 2. Per-session MCP bridge — `voice_bridge_*` + `voice_default_room`

The `voice_join_room` / `voice_speak` MCP path — pipes voice into a _live_
Claude Code session (`services/voice_pipecat.py`, the `voice-bridge` plugin).
Separate component from the always-on agent.

| Key                                | Purpose                                                            |
| ---------------------------------- | ------------------------------------------------------------------ |
| `voice_bridge_enabled`             | Master switch for the MCP bridge                                   |
| `voice_default_room`               | Room a session's bridge joins by default                           |
| `voice_bridge_stt_model`           | STT for the bridge (`base.en` — lighter than the agent's `medium`) |
| `voice_bridge_tts_voice`           | Kokoro voice for the bridge                                        |
| `voice_bridge_chunk_max_chars`     | TTS chunk size                                                     |
| `voice_bridge_max_session_seconds` | Bridge session lifetime cap                                        |

## 3. WebRTC agent — `services/voice_agent.py`

A separate standalone agent (`poindexter-voice-webrtc` in the Pyroscope tags).
Reads several `voice_agent_*` keys above. Audit target for any further dead
keys.

## Retired

- **`voice_agent_brain`** — the original (pre-`_mode`) brain key. Superseded by
  `voice_agent_brain_mode`; the fallback was removed and the key dropped
  (migration `20260604_020000`, #1006).
