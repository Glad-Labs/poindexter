"""OpenAI-compatible /v1/audio/speech shim for ResembleAI Chatterbox (MIT).

Reads the standard OpenAI body plus two non-standard emotion knobs
(`exaggeration`, `cfg_weight`). Long input is split into sentences (Chatterbox
truncates at a ~1000-step / ~40s per-call budget), each is synthesized, and the
waveforms are concatenated with a short silence gap before a single ffmpeg
encode — so the client's loudnorm pass (mp3/aac/opus only) applies once.
Model is loaded lazily on first request, cached, and released again after
`CHATTERBOX_IDLE_TIMEOUT_S` of inactivity (or on demand via `POST /unload`)
so narration doesn't squat VRAM through the video render that follows it.

Device is `TTS_DEVICE` (default `cuda`); the bake-off runs it CPU-only when
there's no spare VRAM. Build/verify is hardware-gated (first-run model
download from HF):
    docker compose --profile tts-hq up -d chatterbox
    curl -fsS http://localhost:8011/health
"""

from __future__ import annotations

import asyncio
import gc
import io
import logging
import os
import subprocess
import threading
import time

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from text_chunking import chunk_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chatterbox-server")

app = FastAPI()
_model = None

# Idle unload — mirrors wan-server's WAN_IDLE_TIMEOUT_S. Without this the model
# stays resident after narration and starves the video render that follows it
# (observed 2026-07-29: dispatch_media_pipeline deferring on "free VRAM 24.0 GB
# < 25 GB required"). 0 disables, for an operator who would rather keep the
# model hot than reclaim the VRAM.
_IDLE_TIMEOUT_S = int(os.environ.get("CHATTERBOX_IDLE_TIMEOUT_S", "120"))
_IDLE_POLL_S = 30

# Guards _model across load / generate / unload.
#
# MUST be a threading.Lock, not asyncio.Lock: `speech()` is a SYNC def, so
# FastAPI runs it in a threadpool, and the idle unloader is an async task on
# the event loop. An asyncio.Lock would not exclude the threadpool worker at
# all — the unloader could free the model out from under an in-flight
# generate. Holding it across generate also serializes concurrent requests,
# which is correct anyway for one GPU (same reasoning as wan-server's lock).
_model_lock = threading.Lock()
_last_used = 0.0

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
    """Load-or-return the cached model. Caller MUST hold ``_model_lock``."""
    global _model
    if _model is None:
        from chatterbox.tts import ChatterboxTTS  # heavy import, defer
        _model = ChatterboxTTS.from_pretrained(device=os.environ.get("TTS_DEVICE", "cuda"))
        logger.info("Chatterbox model loaded (device=%s)", os.environ.get("TTS_DEVICE", "cuda"))
    return _model


def _unload_model() -> bool:
    """Drop the cached model and return its VRAM. Caller MUST hold ``_model_lock``.

    Returns True if a loaded model was actually released.
    """
    global _model
    if _model is None:
        return False
    _model = None
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            logger.info(
                "Chatterbox model unloaded; VRAM still allocated: %d MB",
                torch.cuda.memory_allocated(0) // 1024 // 1024,
            )
            return True
    except Exception as exc:  # pragma: no cover - torch always present in the image
        # Never let a reclaim failure take the sidecar down; the model
        # reference is already dropped, so Python will free it regardless.
        logger.warning("empty_cache after unload failed: %s", exc)
    logger.info("Chatterbox model unloaded")
    return True


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


class UnloadRequest(BaseModel):
    # Exit the process after unloading, so the CUDA context itself returns to
    # the host (torch.cuda.empty_cache() leaves it behind). Docker's
    # `restart: unless-stopped` brings the sidecar back and it lazy-loads on
    # the next request. Same contract image-gen's /unload already implements.
    hard: bool = False


def _is_idle(now: float | None = None) -> bool:
    """True when a model is loaded and has gone untouched past the timeout."""
    if _IDLE_TIMEOUT_S <= 0 or _model is None:
        return False
    return ((now or time.time()) - _last_used) > _IDLE_TIMEOUT_S


def _maybe_idle_unload() -> bool:
    """Unload the model if it has been idle. Returns True if it unloaded.

    Blocking (takes ``_model_lock``) — call it off the event loop.
    """
    if not _is_idle():
        return False
    with _model_lock:
        # Re-check under the lock: a request may have claimed the model
        # between the cheap check above and acquiring it.
        if not _is_idle():
            return False
        return _unload_model()


@app.on_event("startup")
async def _start_idle_unloader():
    """Release VRAM after _IDLE_TIMEOUT_S with no /v1/audio/speech calls."""
    if _IDLE_TIMEOUT_S <= 0:
        logger.info("Idle unload disabled (CHATTERBOX_IDLE_TIMEOUT_S=%s)", _IDLE_TIMEOUT_S)
        return

    async def idle_unloader():
        while True:
            await asyncio.sleep(_IDLE_POLL_S)
            # In a worker thread: _model_lock is a threading.Lock held across
            # generate, so acquiring it on the event loop would stall every
            # other request for the length of a synthesis.
            try:
                await asyncio.to_thread(_maybe_idle_unload)
            except Exception as exc:  # never let the watchdog die silently
                logger.warning("idle unload failed: %s", exc)

    asyncio.create_task(idle_unloader())
    logger.info("Idle unloader started (timeout=%ds)", _IDLE_TIMEOUT_S)


@app.get("/health")
def health():
    # `status` stays "ok" whenever the server can serve — the model is
    # lazy-loaded, so "not loaded" is a normal resting state, not ill health.
    # Docker's healthcheck greps this endpoint; reporting anything else while
    # idle would flap the container.
    return {
        "status": "ok",
        "model_loaded": _model is not None,
        "idle_timeout_s": _IDLE_TIMEOUT_S,
        "seconds_since_last_use": (
            round(time.time() - _last_used, 1) if _last_used else None
        ),
    }


@app.post("/unload")
def unload(req: UnloadRequest | None = None):
    """Free VRAM on demand (called by the GPU scheduler's reclaim path)."""
    hard = bool(req and req.hard)
    with _model_lock:
        released = _unload_model()
    if hard:
        # Defer the exit so this response is actually delivered; the caller
        # treats 200 as "reclaim accepted", and Docker restarts us.
        logger.info("Hard unload requested — exiting so the CUDA context returns")
        threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"status": "unloaded", "released": released, "hard": hard}


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest):
    global _last_used
    if not req.input.strip():
        raise HTTPException(400, "empty input")

    # Validate BEFORE taking the lock / loading the model — a bad path should
    # 400 immediately rather than pay for a model load first.
    voice_ref = (req.audio_prompt_path or "").strip() or _DEFAULT_PROMPT_WAV
    if voice_ref and not os.path.exists(voice_ref):
        raise HTTPException(400, f"audio_prompt_path not found: {voice_ref}")

    with _model_lock:
        # Stamp on entry as well as exit: a long synthesis must not look idle
        # to the unloader that is polling while it runs.
        _last_used = time.time()
        try:
            return _synthesize(req, voice_ref)
        finally:
            _last_used = time.time()


def _synthesize(req: SpeechRequest, voice_ref: str | None) -> Response:
    """Render one request. Caller MUST hold ``_model_lock``."""
    model = _get_model()
    sample_rate = int(model.sr)
    gap = np.zeros(int(sample_rate * _GAP_SECONDS), dtype=np.float32)

    # voice_ref was resolved and existence-checked by the caller, before the
    # model load — per-request ref > env default > built-in voice (None).
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
