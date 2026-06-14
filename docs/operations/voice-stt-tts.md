# Voice STT to TTS — always-live conversational interface

The Pipecat-based voice agent (Whisper -> Ollama / Claude -> Kokoro) runs
as two always-on Docker services so you can talk to Poindexter at any
time without launching anything by hand.

| Service               | Container                        | Surface                                               | Use this when...                                                                                                                                 |
| --------------------- | -------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `voice-agent-livekit` | `poindexter-voice-agent-livekit` | Joins a LiveKit room and stays joined                 | You want to talk from a phone or another device on your tailnet — open the LiveKit hosted client (or a mobile LiveKit app), join the room, talk. |
| `voice-agent-webrtc`  | `poindexter-voice-agent-webrtc`  | Serves the Pipecat prebuilt SmallWebRTC UI on `:8003` | You want a "click a button in a browser, talk to the bot" flow with no SFU in the loop. Local-only / quick test path.                            |

Both share one image (`scripts/Dockerfile.voice-agent`) and one pipeline
(`services.voice_agent.build_voice_pipeline_task`) — only the transport
differs.

> **Two-room split (#1006).** The always-on LiveKit agent now runs one
> container per room profile — `poindexter-voice-agent-livekit` (the
> `poindexter` room) and `poindexter-voice-agent-claude-code` (the dev
> room). This page covers the shared STT→TTS pipeline; see
> [`voice-settings.md`](voice-settings) for the per-room key inventory.

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
poindexter settings set voice_agent_livekit_enabled false
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
     you've put the SFU behind Tailscale Serve / a reverse proxy)
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
internet, add a token gate at the WebRTC transport layer in
`services/voice_agent.py` (the shared `build_voice_pipeline_task`).

### Local-only mode (override the public bind)

`voice_agent_webrtc_host` defaults to `0.0.0.0` so the agent is
reachable from any Tailscale device on the tailnet. To restrict the
WebRTC service to localhost only:

```
poindexter settings set voice_agent_webrtc_host 127.0.0.1 --allow-new  # --allow-new: this key isn't seeded by default
docker restart poindexter-voice-agent-webrtc
```

The container picks up the new bind on the next start. Bandit's B104
"binding to all interfaces" warnings on the `0.0.0.0` defaults are
suppressed in code with `# nosec B104` because the tailnet exposure is
intentional — see Glad-Labs/poindexter#402 for the audit.

## Configuration (DB-first)

Every knob is an `app_settings` row. Edit at runtime:

```
poindexter settings set <key> <value>
```

Container picks up changes on next restart.

| Key                           | Default                     | Purpose                               |
| ----------------------------- | --------------------------- | ------------------------------------- |
| `voice_agent_livekit_enabled` | `true`                      | Toggle the LiveKit container          |
| `voice_agent_webrtc_enabled`  | `true`                      | Toggle the WebRTC container           |
| `voice_agent_room_name`       | `poindexter`                | Room the bot joins on boot            |
| `voice_agent_identity`        | `poindexter-bot`            | Bot's identity in the room            |
| `voice_agent_brain_mode`      | `ollama`                    | LLM stage — `ollama` or `claude-code` |
| `voice_agent_livekit_url`     | `ws://livekit:7880`         | In-network LiveKit URL the bot uses   |
| `voice_agent_llm_model`       | `glm-4.7-5090:latest`       | Ollama model (when brain = ollama)    |
| `voice_agent_ollama_url`      | `http://localhost:11434/v1` | Ollama base URL                       |
| `voice_agent_tts_voice`       | `bf_emma`                   | Kokoro voice id                       |
| `voice_agent_tts_speed`       | `1.0`                       | Kokoro playback speed                 |
| `voice_agent_whisper_model`   | `base`                      | faster-whisper model size             |
| `voice_agent_vad_stop_secs`   | `0.2`                       | End-of-speech silence window          |
| `voice_agent_system_prompt`   | (Emma persona)              | Agent personality                     |
| `voice_agent_webrtc_host`     | `0.0.0.0`                   | WebRTC bind host                      |
| `voice_agent_webrtc_port`     | `8003`                      | WebRTC bind port                      |

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

STT and TTS are **mode-aware** as of #1088 (see "Warm STT/TTS sidecar"
below): `voice_agent_{stt,tts}_mode = inprocess` runs the models in the
voice container (the values above apply); `= sidecar` makes the voice
agents thin clients of the warm Speaches container (faster-whisper +
Kokoro), which is the path that kills the ~12s per-restart cold-start.

### Brain

`voice_agent_brain_mode = ollama` (default) — local glm-4.7-5090 with three
read-only Poindexter tools (`check_pipeline_health`,
`get_published_post_count`, `get_ai_spending_status`). Snappy, zero
incremental cost.

`voice_agent_brain_mode = claude-code` — every voice turn shells out to
`claude -p` under your Max OAuth subscription. Slower (~3-8s warm,
~10s cold first turn) but has full repo access, MCP tools, and
edit/bash powers. Best for "dev on the go" — ask Claude to fix a bug
and it reads the file, edits it, runs tests, commits.

## Warm STT/TTS sidecar (Speaches, #1088)

One GPU container (`poindexter-speaches`, `ghcr.io/speaches-ai/speaches:latest-cuda`,
in-network `http://speaches:8000`, host port `8001`) serves **both** OpenAI-compatible
endpoints: `POST /v1/audio/transcriptions` (faster-whisper) and `POST /v1/audio/speech`
(Kokoro, the same `bf_emma` / `bf_isabella` voices). `WHISPER__TTL=-1` keeps the model
resident, so a **voice-container restart no longer pays the ~12s in-process Whisper
cold-start** — the model load moves to Speaches' own (rare) boot. Both rooms share the
one container. Pipecat's `OpenAITTSService` requests `response_format=pcm` (24 kHz);
Speaches returns `audio/pcm`, so no transcoding.

`build_voice_pipeline_task` builds STT/TTS via the mode-aware `_build_stt` / `_build_tts`
seams in `services/voice_agent.py`:

| Key                        | Default                            | Notes                                                                                   |
| -------------------------- | ---------------------------------- | --------------------------------------------------------------------------------------- |
| `voice_agent_stt_mode`     | `inprocess`                        | `sidecar` → Pipecat `OpenAISTTService` at the base url; else in-process Whisper         |
| `voice_agent_stt_base_url` | `http://speaches:8000/v1`          |                                                                                         |
| `voice_agent_stt_model`    | `Systran/faster-whisper-medium`    | HF id — **not** the in-process Pipecat enum (`voice_agent_whisper_model`)               |
| `voice_agent_tts_mode`     | `inprocess`                        | `sidecar` → `OpenAITTSService` (honors `voice_agent_tts_speed`); else in-process Kokoro |
| `voice_agent_tts_base_url` | `http://speaches:8000/v1`          | same Speaches service by default; separate key so STT/TTS can split later               |
| `voice_agent_tts_model`    | `speaches-ai/Kokoro-82M-v1.0-ONNX` | voice id still comes from `voice_agent_tts_voice` / `voice_agent_claude_code_tts_voice` |

### First-time setup per machine (IMPORTANT)

Speaches' lazy model path downloads only the weight files, not the HF **model card** —
and its `stt.py` has `assert model_card_data is not None`, so the _first_ transcription
500s on a fresh cache. Pre-pull each model once (this fetches the card AND warms VRAM):

```bash
docker exec poindexter-speaches sh -c \
  "curl -s -X POST http://localhost:8000/v1/models/Systran/faster-whisper-medium; echo; \
   curl -s -X POST http://localhost:8000/v1/models/speaches-ai/Kokoro-82M-v1.0-ONNX; echo"
```

The card then persists in the bind-mounted HF cache, so **subsequent Speaches restarts
read it from cache** — the pre-pull is one-time per machine, not per restart.

### Cutover

1. `docker compose -f docker-compose.local.yml up -d speaches`; wait for `(healthy)`.
2. Pre-pull both models (above) — once per machine.
3. Set `voice_agent_stt_mode` + `voice_agent_tts_mode` = `sidecar`.
4. `docker restart poindexter-voice-agent-livekit poindexter-voice-agent-claude-code`.
5. Confirm each bot logs `Voice pipeline — ... stt_mode=sidecar tts_mode=sidecar` and
   `Connected to room '<room>'`, with **no** `Loading Whisper model...` line.

### Pre-warm after a Speaches restart

A Speaches restart clears VRAM; the first STT/TTS call reloads the model (~13–35s). To
keep the first live turn snappy, warm both after any Speaches restart (voice-agent
restarts do **not** need this — they never touch the model in VRAM):

```bash
docker exec poindexter-speaches python -c "import wave,struct;w=wave.open('/tmp/t.wav','wb');w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\\x00'*32000);w.close()"
docker exec poindexter-speaches sh -c "curl -s -o /dev/null http://localhost:8000/v1/audio/transcriptions -F file=@/tmp/t.wav -F model=Systran/faster-whisper-medium"
docker exec poindexter-speaches sh -c "curl -s -o /dev/null http://localhost:8000/v1/audio/speech -H 'Content-Type: application/json' -d '{\"model\":\"speaches-ai/Kokoro-82M-v1.0-ONNX\",\"voice\":\"bf_emma\",\"input\":\"warm\",\"response_format\":\"pcm\"}'"
```

### Rollback

Set both `*_mode` back to `inprocess` and restart the two voice containers. Instant; no
schema change — the bot reloads in-process Whisper exactly as before #1088.

### VRAM

Net new continuous VRAM ≈ 2 GB (faster-whisper medium + Kokoro). Confirm headroom on the
**Hardware & Power** dashboard (`nvidia_gpu_*`). If VRAM-pressured, run Speaches' Kokoro
on CPU (Speaches config) — the escape hatch for the exit-137/SIGKILL history.

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
  `services/voice_agent_livekit.py`, `services/voice_pipecat.py`)
  are unchanged from the pre-#383 state — they were already
  production-quality. #383 only added:
  - A `--service` daemon entrypoint on the LiveKit module that reads
    everything from `app_settings` and exits 0 when the surface is
    disabled.
  - The same enabled-flag pattern on the WebRTC module's `_serve()`.
  - The `voice_agent_room_name` / `voice_agent_identity` /
    `voice_agent_brain_mode` / `voice_agent_livekit_url` /
    `voice_agent_*_enabled` `app_settings` rows
    (migration `20260505_135518_seed_voice_agent_container_settings`).
  - `scripts/Dockerfile.voice-agent` — one image, two services.
  - The two compose services in `docker-compose.local.yml`.

- The legacy `voice-bot` container (`scripts/discord-voice-bot.py`)
  is **not** replaced. It's a different surface (Discord voice channel
  with slash commands), uses py-cord rather than Pipecat, and stays
  running for the operators who joined the Discord workflow before
  Pipecat existed.

- LiveKit credentials default to env (`LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`),
  in lockstep with the SFU container that consumes the same pair via
  `LIVEKIT_KEYS`. As of #1000 (2026-06-04) they can also be stored encrypted
  in `app_settings` (empty there = fall back to env), collapsing the
  scattered env/file copies behind a single rotation point.
