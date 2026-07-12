# TTS sidecar assets

## `stock_speaker_16k.wav` (required by the CosyVoice2 sidecar)

CosyVoice2 has no fixed built-in "voice" — it speaks in the voice of a short
**reference clip** (its native zero-shot mechanism). The `cosyvoice2` bake-off
sidecar uses one bundled clip as its stock speaker; `instruct` then drives the
emotion/style on top of it.

**You must supply this file before building the CosyVoice2 sidecar** — it is
intentionally not committed (it's an audio asset, and the right clip is an
operator choice).

Requirements:

- **Format:** mono WAV, **16 kHz** sample rate.
- **Length:** ~3–10 seconds of clean speech.
- **Content:** a neutral, clearly-recorded voice reading any sentence. This is
  the timbre every CosyVoice2 render will adopt, so pick a voice you'd be happy
  narrating the podcast — e.g. record yourself, or use a clip you have the
  rights to (avoid copyrighted/voice-actor material).

Create one quickly from any audio with ffmpeg:

    ffmpeg -i your_voice.mp3 -ac 1 -ar 16000 -t 8 \
      scripts/tts_sidecars/assets/stock_speaker_16k.wav

Or bootstrap one from the already-running Kokoro/Speaches (no external audio
needed — the reference is Kokoro-derived and freely swappable later):

    curl -fsS -X POST http://localhost:8001/v1/audio/speech \
      -H 'Authorization: Bearer speaches' -H 'Content-Type: application/json' \
      -d '{"model":"speaches-ai/Kokoro-82M-v1.0-ONNX","voice":"bf_emma",
           "input":"A neutral reference voice for narration.","response_format":"wav"}' \
      -o /tmp/ref.wav
    ffmpeg -y -i /tmp/ref.wav -ac 1 -ar 16000 \
      scripts/tts_sidecars/assets/stock_speaker_16k.wav

The Chatterbox sidecar does **not** need this — it ships its own default voice.

## Pinning a Chatterbox production voice (optional)

Chatterbox also clones zero-shot from a reference clip, but — unlike
CosyVoice2 — this is **optional**: leave it unset and every render uses
Chatterbox's own built-in default voice. Pin one only if you want a specific
voice identity for the live podcast pipeline (`podcast_tts_engine=chatterbox`).

The reference clip is **never committed** here either — same reasoning as
`stock_speaker_16k.wav` above, and it doesn't even live in this directory: it's
a host-mounted operator asset, kept out of git entirely (like `bootstrap.toml`).

1. Convert your clip to a clean mono reference (24 kHz suits Chatterbox's
   native sample rate; 7–10s is the sweet spot):

       ffmpeg -i your_voice.mp3 -ac 1 -ar 24000 -t 10 \
         ~/.poindexter/tts-voices/podcast-voice.wav

2. `docker-compose.local.yml` / `docker-compose.consumer.yml` already mount
   `~/.poindexter/tts-voices` (read-only) into the chatterbox container at
   `/app/voices` — restart the sidecar to pick up a new/changed file:

       docker compose --profile tts-hq up -d --force-recreate chatterbox

3. Point the pipeline at it:

       poindexter settings set plugin.tts_provider.chatterbox.audio_prompt_path /app/voices/podcast-voice.wav
       poindexter settings set podcast_tts_engine chatterbox

Verify with `poindexter media tts-bakeoff --engines chatterbox` — the
rendered take should carry your reference voice's identity.

## Multi-GPU hosts: pinning which card Chatterbox uses (optional)

The chatterbox sidecar requests one GPU without specifying which — Docker
hands it whatever it enumerates as device 0. On a host with more than one
GPU, that's not necessarily the GPU you want: if device 0 is newer than the
installed torch build has compiled kernels for, model load fails with

    RuntimeError: CUDA error: no kernel image is available for execution on the device

If you hit this, add to `~/.poindexter/bootstrap.toml`:

    chatterbox_gpu_device_id = "1"   # whichever index nvidia-smi shows as compatible

then recreate the sidecar:

    docker compose --profile tts-hq up -d --force-recreate chatterbox

`nvidia-smi --query-gpu=index,name,compute_cap --format=csv` lists each
GPU's index and compute capability — pick one your torch version supports.
