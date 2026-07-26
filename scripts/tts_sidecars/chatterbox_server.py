"""OpenAI-compatible /v1/audio/speech shim for ResembleAI Chatterbox (MIT).

Reads the standard OpenAI body plus two non-standard emotion knobs
(`exaggeration`, `cfg_weight`). Long input is split into sentences (Chatterbox
truncates at a ~1000-step / ~40s per-call budget), each is synthesized, and the
waveforms are concatenated with a short silence gap before a single ffmpeg
encode — so the client's loudnorm pass (mp3/aac/opus only) applies once.
Model is loaded lazily on first request and cached.

Device is `TTS_DEVICE` (default `cuda`); the bake-off runs it CPU-only when
there's no spare VRAM. Build/verify is hardware-gated (first-run model
download from HF):
    docker compose --profile tts-hq up -d chatterbox
    curl -fsS http://localhost:8011/health
"""

from __future__ import annotations

import io
import os
import subprocess

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from text_chunking import chunk_text

app = FastAPI()
_model = None

# Silence inserted between concatenated sentence chunks, for natural pacing.
_GAP_SECONDS = 0.25

# Optional voice reference. Chatterbox has ONE built-in default voice and clones
# any other voice zero-shot from a short reference clip (audio_prompt_path). Pin
# a voice for the pipeline via CHATTERBOX_PROMPT_WAV (a path inside the
# container), or override per-request with the `audio_prompt_path` body field.
# Empty/unset => the built-in default voice.
_DEFAULT_PROMPT_WAV = os.environ.get("CHATTERBOX_PROMPT_WAV", "").strip() or None

# Bitrate for lossy encodes. MUST be explicit: ffmpeg's libmp3lame default for a
# mono 24 kHz input (Chatterbox's native rate) resolves to 32 kbps, which is
# audibly artifacty on speech — and the client then re-encodes that already-
# damaged stream to its own delivery bitrate, so the loss is permanent
# (measured 2026-07-26: every cloned-voice episode shipped through a 32 kbps
# first pass). 128 kbps mono is transparent for speech and keeps the
# sidecar->client hop from being the weak link. Chatterbox is 24 kHz natively;
# deliberately NOT resampling here — the client's loudnorm pass already
# resamples to its delivery rate, and upsampling twice adds nothing.
_ENCODE_BITRATE = os.environ.get("CHATTERBOX_ENCODE_BITRATE", "128k").strip() or "128k"


def _get_model():
    global _model
    if _model is None:
        from chatterbox.tts import ChatterboxTTS  # heavy import, defer
        _model = ChatterboxTTS.from_pretrained(device=os.environ.get("TTS_DEVICE", "cuda"))
    return _model


def _encode(samples, sample_rate: int, fmt: str) -> bytes:
    """WAV samples -> requested container via ffmpeg (mp3/wav/opus/aac).

    Lossy formats carry an explicit ``-b:a`` (see ``_ENCODE_BITRATE``) —
    without it ffmpeg picks a bitrate off the input geometry and lands at
    32 kbps for mono 24 kHz, wrecking the audio before the client ever
    sees it.
    """
    wav_buf = io.BytesIO()
    sf.write(wav_buf, samples, sample_rate, format="WAV")
    if fmt == "wav":
        return wav_buf.getvalue()
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", "pipe:0", "-b:a", _ENCODE_BITRATE, "-f", fmt, "pipe:1"],
        input=wav_buf.getvalue(), capture_output=True,
    )
    if proc.returncode != 0:
        raise HTTPException(500, f"ffmpeg encode failed: {proc.stderr[:300]!r}")
    return proc.stdout


class SpeechRequest(BaseModel):
    model: str | None = None
    input: str
    voice: str | None = None
    response_format: str = "mp3"
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    # Path (inside the container) to a ~7-10s reference clip to clone. Overrides
    # CHATTERBOX_PROMPT_WAV; None/"" => built-in default voice.
    audio_prompt_path: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest):
    if not req.input.strip():
        raise HTTPException(400, "empty input")
    model = _get_model()
    sample_rate = int(model.sr)
    gap = np.zeros(int(sample_rate * _GAP_SECONDS), dtype=np.float32)

    # Resolve the voice: per-request ref > env default > built-in voice (None).
    voice_ref = (req.audio_prompt_path or "").strip() or _DEFAULT_PROMPT_WAV
    if voice_ref and not os.path.exists(voice_ref):
        raise HTTPException(400, f"audio_prompt_path not found: {voice_ref}")

    segments: list[np.ndarray] = []
    chunks = chunk_text(req.input)
    for i, chunk in enumerate(chunks):
        # generate(text, audio_prompt_path=, exaggeration=, cfg_weight=) -> torch
        # tensor [1, N] at model.sr. audio_prompt_path=None => default voice.
        wav = model.generate(chunk, audio_prompt_path=voice_ref,
                             exaggeration=req.exaggeration, cfg_weight=req.cfg_weight)
        segments.append(wav.squeeze(0).detach().cpu().numpy().astype(np.float32))
        if i < len(chunks) - 1:
            segments.append(gap)

    audio_samples = np.concatenate(segments) if segments else np.zeros(1, dtype=np.float32)
    audio = _encode(audio_samples, sample_rate, req.response_format.lower())
    media = {"mp3": "audio/mpeg", "wav": "audio/wav",
             "opus": "audio/opus", "aac": "audio/aac"}.get(
        req.response_format.lower(), "application/octet-stream")
    return Response(content=audio, media_type=media)
