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
