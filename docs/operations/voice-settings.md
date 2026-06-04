# Voice settings inventory (`voice_*` app_settings)

There are **three** voice surfaces, each with its own config namespace. Same
key _names_ across them (tts voice, stt model, room) are **not duplicates** —
they belong to different components. This map exists so the next "are these
dupes?" question has an answer (glad-labs-stack#1006).

## 1. Always-on LiveKit agent — `voice_agent_*`

The `poindexter-voice-agent-livekit` container (`services/voice_agent_livekit.py`).
Always-on; Matt taps the join page and talks to it.

| Key                                                                 | Purpose                                                         |
| ------------------------------------------------------------------- | --------------------------------------------------------------- |
| `voice_agent_livekit_enabled`                                       | Master on/off for the container                                 |
| `voice_agent_room_name`                                             | LiveKit room it joins                                           |
| `voice_agent_identity`                                              | Its participant identity                                        |
| `voice_agent_livekit_url`                                           | In-network LiveKit URL (`ws://livekit:7880`)                    |
| `voice_agent_public_join_url`                                       | Human-facing tap-to-join URL (Tailscale Funnel)                 |
| `voice_agent_whisper_model`                                         | STT model for the agent (`medium`)                              |
| `voice_agent_tts_voice` / `_tts_speed`                              | Kokoro voice + speed                                            |
| `voice_agent_vad_stop_secs`                                         | VAD end-of-turn silence                                         |
| `voice_agent_system_prompt`                                         | Emma's persona (used by the ollama brain)                       |
| `voice_agent_brain_mode`                                            | **Canonical** brain selector — `ollama` \| `claude-code`        |
| `voice_agent_llm_model`                                             | GLM model — used **only** when brain = `ollama`                 |
| `voice_agent_ollama_url`                                            | Ollama base URL — used **only** when brain = `ollama`           |
| `voice_agent_recall_k` / `_recall_min_similarity`                   | Memory recall for the ollama brain                              |
| `voice_agent_claude_code_session_id`                                | Pinned `claude -p` session (brain = `claude-code`, #1006)       |
| `voice_agent_claude_code_session_token_budget` / `_max_age_seconds` | Session auto-reset thresholds (#1006)                           |
| `voice_agent_claude_code_host_brain_url`                            | Host-brain daemon URL — empty = run claude in-container (#1006) |
| `voice_agent_claude_code_host_brain_token`                          | Host-brain bearer token (secret, #1006)                         |

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
