# Voice STT to TTS — always-live conversational interface

The Pipecat-based voice agent (Whisper -> Ollama / Claude -> Kokoro) runs
as two always-on Docker services so you can talk to Poindexter at any
time without launching anything by hand.

| Service | Container | Surface | Use this when... |
| --- | --- | --- | --- |
| `voice-agent-livekit` | `poindexter-voice-agent-livekit` | Joins a LiveKit room and stays joined | You want to talk from a phone or another device on your tailnet — open the LiveKit hosted client (or a mobile LiveKit app), join the room, talk. |
| `voice-agent-webrtc` | `poindexter-voice-agent-webrtc` | Serves the Pipecat prebuilt SmallWebRTC UI on `:8003` | You want a "click a button in a browser, talk to the bot" flow with no SFU in the loop. Local-only / quick test path. |

Both share one image (`scripts/Dockerfile.voice-agent`) and one pipeline
(`services.voice_agent.build_voice_pipeline_task`) — only the transport
differs.

## Start / stop

The services come up with the rest of the stack:

```
docker compose -f docker-compose.local.yml up -d voice-agent-livekit voice-agent-webrtc
```

Restart picks up new settings (room name, brain choice, enabled flag):

```
docker restart poindexter-voice-agent-livekit
docker restart poindexter-voice-agent-webrtc
```

Take either surface offline without editing compose:

```
poindexter set voice_agent_livekit_enabled false
docker restart poindexter-voice-agent-livekit
# container exits 0; unless-stopped leaves it stopped (no crash-loop)
```

Re-enable the same way (`true`).

## How to connect

### LiveKit (the conference-call surface)

Default room is `poindexter`; default identity for the bot is
`poindexter-bot`. Both are app_settings keys
(`voice_agent_room_name`, `voice_agent_identity`).

1. Mint a client token for yourself:

   ```
   bash scripts/start-livekit-voice-bot.sh --print-client-token \
       --room poindexter --identity matt-phone
   ```

   (The script prints a JWT — that's the token.)

2. Open <https://meet.livekit.io>, paste:
   - LiveKit URL: `ws://<your-tailnet-host>:7880` (or `wss://...` if
     you've put the SFU behind Tailscale Funnel / a reverse proxy)
   - Token: the JWT from step 1

3. Click connect, allow mic, talk. The bot is already in the room.

### WebRTC prebuilt UI (the browser surface)

```
http://<your-host>:8003
```

The root URL redirects to `/client/`, which is Pipecat's prebuilt
mic-button UI. Click "Connect", allow mic, talk.

Bind host / port live in app_settings (`voice_agent_webrtc_host` =
`0.0.0.0`, `voice_agent_webrtc_port` = `8003`). The default `0.0.0.0`
binding relies on Tailscale to gate access — only your tailnet devices
can reach the host port. If you ever expose the port to the open
internet, add a token gate to the `/api/offer` handler in
`services/voice_agent_webrtc.py`.

## Configuration (DB-first)

Every knob is an `app_settings` row. Edit at runtime:

```
poindexter set <key> <value>
```

Container picks up changes on next restart.

| Key | Default | Purpose |
| --- | --- | --- |
| `voice_agent_livekit_enabled` | `true` | Toggle the LiveKit container |
| `voice_agent_webrtc_enabled`  | `true` | Toggle the WebRTC container |
| `voice_agent_room_name`       | `poindexter` | Room the bot joins on boot |
| `voice_agent_identity`        | `poindexter-bot` | Bot's identity in the room |
| `voice_agent_brain`           | `ollama` | LLM stage — `ollama` or `claude-code` |
| `voice_agent_livekit_url`     | `ws://livekit:7880` | In-network LiveKit URL the bot uses |
| `voice_agent_llm_model`       | `glm-4.7-5090:latest` | Ollama model (when brain = ollama) |
| `voice_agent_ollama_url`      | `http://localhost:11434/v1` | Ollama base URL |
| `voice_agent_tts_voice`       | `bf_emma` | Kokoro voice id |
| `voice_agent_tts_speed`       | `1.0` | Kokoro playback speed |
| `voice_agent_whisper_model`   | `base` | faster-whisper model size |
| `voice_agent_vad_stop_secs`   | `0.2` | End-of-speech silence window |
| `voice_agent_system_prompt`   | (Emma persona) | Agent personality |
| `voice_agent_webrtc_host`     | `0.0.0.0` | WebRTC bind host |
| `voice_agent_webrtc_port`     | `8003` | WebRTC bind port |

## Swap providers

### STT

Edit `voice_agent_whisper_model`. Valid values come from Pipecat's
`Model` enum:

```
tiny, base, small, medium, large-v3,
deepdml/faster-whisper-large-v3-turbo-ct2,        # LARGE_V3_TURBO
Systran/faster-distil-whisper-large-v2,           # DISTIL_LARGE_V2
Systran/faster-distil-whisper-medium.en           # DISTIL_MEDIUM_EN
```

`base` is the latency / accuracy default. Drop to `tiny` if turn-taking
feels slow; bump to `DISTIL_MEDIUM_EN` for English-optimised accuracy on
a 5090.

A typo or model id Pipecat doesn't recognise raises a loud
`ValueError` listing every valid value (no silent fallback to a
different model — see `feedback_no_silent_defaults`).

### TTS

Edit `voice_agent_tts_voice`. Top-graded UK voices in the Kokoro-82M
catalog: `bf_emma` (B-, default), `bf_isabella` (C), `bf_alice` (D),
`bf_lily` (D). Many other languages / accents available — see the
Kokoro docs.

Switching to a different TTS engine entirely (Piper, Coqui, edge-tts)
needs a code change in `build_voice_pipeline_task` — the TTS stage is
hardcoded to `KokoroTTSService` today. Tracked as a follow-up of #383.

### Brain

`voice_agent_brain = ollama` (default) — local glm-4.7-5090 with three
read-only Poindexter tools (`check_pipeline_health`,
`get_published_post_count`, `get_ai_spending_status`). Snappy, zero
incremental cost.

`voice_agent_brain = claude-code` — every voice turn shells out to
`claude -p` under your Max OAuth subscription. Slower (~3-8s warm,
~10s cold first turn) but has full repo access, MCP tools, and
edit/bash powers. Best for "dev on the go" — ask Claude to fix a bug
and it reads the file, edits it, runs tests, commits.

## Debugging

### "I don't hear the bot"

1. Check the container is running:
   ```
   docker ps --filter name=poindexter-voice-agent-livekit
   ```
2. Tail the logs — Pipecat is verbose, you'll see VAD / STT / LLM /
   TTS frame events:
   ```
   docker logs -f poindexter-voice-agent-livekit
   ```
3. First boot downloads ~470 MB of model weights (Silero VAD + Whisper
   base + Kokoro 82M). That can take 30-60s on first start; subsequent
   restarts are warm.

### "The room exists but the bot isn't in it"

- The bot reads `voice_agent_room_name` at boot. Did you change it
  without restarting?
  ```
  docker restart poindexter-voice-agent-livekit
  ```
- The bot uses `voice_agent_livekit_url` (default `ws://livekit:7880`,
  the in-network compose URL). Don't override that to your host's
  external URL — the bot connects from inside the docker network.

### "claude-code brain hangs"

The subprocess bridge times out a turn at 90s. If you hit it
repeatedly, ask shorter questions or switch back to `ollama` for
quick chats.

### "Healthcheck reports unhealthy"

- LiveKit container: probes PID 1. An "unhealthy" flag means the
  python process died — check the logs for an unhandled exception.
- WebRTC container: probes `GET /healthz`. `wget` is bundled in the
  base image; if /healthz returns non-`ok`, FastAPI failed to start
  (usually a config / DB connection issue).

## Failure routing

Voice-agent crashes (container crash-loop, healthcheck failure) route
through the brain daemon's compose-drift probe and surface in
**Discord** (the `discord_lab_logs_webhook_url` channel) — not
Telegram. Telegram is reserved for critical alerts; voice-agent
hiccups are noisy and operational.

If a container crashes mid-conversation, the LiveKit room stays open
and the human stays connected — the bot just goes silent. Restart the
container and it rejoins:

```
docker restart poindexter-voice-agent-livekit
```

A persistent multi-restart failure is the case where the brain daemon
escalates to Telegram (per the standard alert-after-N-failures
pattern, not a separate code path).

## Architecture notes

- The Pipecat pipeline modules (`services/voice_agent.py`,
  `services/voice_agent_livekit.py`, `services/voice_agent_webrtc.py`)
  are unchanged from the pre-#383 state — they were already
  production-quality. #383 only added:
  - A `--service` daemon entrypoint on the LiveKit module that reads
    everything from `app_settings` and exits 0 when the surface is
    disabled.
  - The same enabled-flag pattern on the WebRTC module's `_serve()`.
  - The `voice_agent_room_name` / `voice_agent_identity` /
    `voice_agent_brain` / `voice_agent_livekit_url` /
    `voice_agent_*_enabled` `app_settings` rows
    (migration `20260505_135518_seed_voice_agent_container_settings`).
  - `scripts/Dockerfile.voice-agent` — one image, two services.
  - The two compose services in `docker-compose.local.yml`.

- The legacy `voice-bot` container (`scripts/discord-voice-bot.py`)
  is **not** replaced. It's a different surface (Discord voice channel
  with slash commands), uses py-cord rather than Pipecat, and stays
  running for the operators who joined the Discord workflow before
  Pipecat existed.

- LiveKit credentials live in env (`LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`)
  to keep them in lockstep with the SFU container that consumes the
  same pair via `LIVEKIT_KEYS`. Moving these to `bootstrap.toml`
  follows the same path the rest of the project secrets are taking;
  tracked as a follow-up.
