"""OpenAI-compatible /v1/audio/speech shim for ResembleAI Chatterbox (MIT).

Reads the standard OpenAI body plus two non-standard emotion knobs
(`exaggeration`, `cfg_weight`). Long input is split into sentences (Chatterbox
truncates at a ~1000-step / ~40s per-call budget), each is synthesized, and the
waveforms are concatenated with a short silence gap before a single ffmpeg
encode — so the client's loudnorm pass (mp3/aac/opus only) applies once.
Model is loaded lazily on first request, cached, and released again after
`CHATTERBOX_IDLE_TIMEOUT_S` of inactivity (or on demand via `POST /unload`)
so narration doesn't squat VRAM through the video render that follows it.

Releasing the model is not the whole of it (2026-09-25). The first synthesis
creates a CUDA context that only a process exit returns: measured per PID from
the host's nvidia-smi in a throwaway container of this image, 3.9 GB with the
model loaded, 660 MiB after the idle unload (704 MiB on the live server, with
20 MB reserved and 8 MB allocated). So once the idle timeout has run and no
model is loaded, the idle pass exits if the driver still counts VRAM against
this process (NVML, read without a context); Docker's restart policy brings the
server back in under a second. The next request pays only process start and
CUDA init on top of the ~13 s model load every request after an idle unload
already paid (measured: 20.6 s for the first request, 0.7 s warm).

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
import sys as _sys

_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import subprocess
import threading
import time
from typing import Any

import numpy as np
import soundfile as sf
from _voice_paths import contained_voice_path, voice_roots  # noqa: E402
from audio_join import join_segments, trim_edge_silence
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from text_chunking import chunk_text
from truncation_guard import (
    DEFAULT_MAX_RATE_RATIO,
    is_truncated,
    reference_rate,
    split_sentences,
)

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

# Requests in the server, counted from the moment /v1/audio/speech is entered
# until its handler returns, including a wait on _model_lock behind another
# synthesis. /unload and the idle exit decline while there is one: a request
# queued on the lock is about to use the model, and an exit would reset its
# connection (the wan / stable-audio in-flight lesson, poindexter#992).
_inflight = 0
_inflight_lock = threading.Lock()

# Exit floors (2026-09-25): below them an exit returns nothing worth a restart,
# so the process stays up. The process floor applies to the driver's count for
# this process (NVML), which includes the CUDA context; every context measured
# on the render GPU was 498 MiB or more, so any context clears it. The
# reserved-pool floor is the fallback when NVML cannot answer: torch's view,
# which cannot see a context.
_HARD_UNLOAD_MIN_PROCESS_MB = int(os.environ.get("CHATTERBOX_HARD_UNLOAD_MIN_PROCESS_MB", "128"))
_HARD_UNLOAD_MIN_RESERVED_MB = int(os.environ.get("CHATTERBOX_HARD_UNLOAD_MIN_RESERVED_MB", "512"))

# The NVML binding once initialised, and why NVML is unusable once it has
# failed (see _process_vram_mb).
_nvml: Any = None
_nvml_error: str | None = None

# Silence inserted between concatenated sentence chunks, for natural pacing.
# This is now the WHOLE boundary (join_segments trims each chunk's own edge
# silence first); before that it was only a floor under the model's tails.
#
# 0.40 + the joiner's two 30ms keep-margins lands at ~0.46s, the MEASURED median
# of Chatterbox's own intra-chunk sentence pauses (n=95 on a shipped episode:
# p25 0.26 / median 0.46 / p75 0.89). Matching it is the point — a chunk seam
# should be indistinguishable from a sentence break the model made itself. The
# old 0.25 was never the real boundary (tails swamped it), so keeping 0.25 after
# trimming would just swap dead air for an unnaturally clipped seam.
_GAP_SECONDS = float(os.environ.get("CHATTERBOX_GAP_SECONDS", "0.40") or 0.40)

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
            # RESERVED, not allocated. Dropping the model reference sets
            # allocated to ~0 by construction, so the old log printed "VRAM
            # still allocated: 0 MB" no matter how much the process was
            # actually holding — the precise blind spot that let stable-audio
            # squat ~11 GB unnoticed for weeks (poindexter#999). The reserved
            # pool is what survives empty_cache and only a process exit
            # returns, so it is the number worth printing.
            logger.info(
                "Chatterbox model unloaded; VRAM still reserved: %d MB "
                "(allocated: %d MB)",
                torch.cuda.memory_reserved(0) // 1024 // 1024,
                torch.cuda.memory_allocated(0) // 1024 // 1024,
            )
            return True
    except Exception as exc:  # pragma: no cover - torch always present in the image
        # Never let a reclaim failure take the sidecar down; the model
        # reference is already dropped, so Python will free it regardless.
        logger.warning("empty_cache after unload failed: %s", exc)
    logger.info("Chatterbox model unloaded")
    return True


def _reserved_mb() -> int:
    """torch's reserved pool (MB): the exit gate's fallback measure.

    torch is read from ``sys.modules`` and never imported here: until the
    first model load nothing has imported it, and nothing has touched CUDA.
    ``is_available`` and ``memory_reserved`` create no CUDA context.
    """
    torch = _sys.modules.get("torch")
    if torch is None or not torch.cuda.is_available():
        return 0
    return int(torch.cuda.memory_reserved(0) // 1024 // 1024)


def _process_vram_mb() -> int | None:
    """VRAM this process holds (MiB), as the driver counts it, or ``None``
    when NVML cannot say.

    The driver's count includes the CUDA context, and ``memory_reserved``
    does not; after the idle unload this server held 660-704 MiB with 20 MB
    reserved. NVML answers without creating a context. Inside a container it
    lists only that container's processes, under their in-container PIDs
    (measured on driver 595.84), so ``os.getpid()`` finds this server. Summed
    over every GPU NVML can see, because an exit returns all of it. The first
    failure disables NVML for this process and logs once; the exit gate then
    falls back to ``memory_reserved``.
    """
    global _nvml, _nvml_error
    if _nvml_error is not None:
        return None
    try:
        if _nvml is None:
            # nvidia-ml-py. Images built before 2026-09-25 do not have it, and
            # libnvidia-ml.so.1 is only in the container when the NVIDIA
            # runtime grants the `utility` driver capability.
            import pynvml

            pynvml.nvmlInit()
            _nvml = pynvml
        pid = os.getpid()
        used = 0
        for index in range(_nvml.nvmlDeviceGetCount()):
            handle = _nvml.nvmlDeviceGetHandleByIndex(index)
            for proc in _nvml.nvmlDeviceGetComputeRunningProcesses(handle):
                if proc.pid != pid:
                    continue
                if proc.usedGpuMemory is None:
                    raise RuntimeError(
                        "NVML lists this process without its memory "
                        "(per-process accounting unavailable here)",
                    )
                used += int(proc.usedGpuMemory)
        return used // 1024 // 1024
    except Exception as exc:  # noqa: BLE001 — any NVML failure means "use torch's view"
        _nvml = None
        _nvml_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "NVML unusable (%s). The exit gate falls back to torch's reserved "
            "pool, which cannot see a CUDA context, so an idle process keeps "
            "its context (~0.7 GB on the render GPU).",
            _nvml_error,
        )
        return None


def _exit_gate() -> dict[str, Any]:
    """What an exit would return to the card, and whether it clears the floor.

    ``reclaimable`` decides both the hard ``/unload`` and the idle exit. With
    NVML it is the driver's count for this process against
    ``CHATTERBOX_HARD_UNLOAD_MIN_PROCESS_MB``; without it, torch's reserved
    pool against ``CHATTERBOX_HARD_UNLOAD_MIN_RESERVED_MB``.
    ``vram_process_source`` is ``"nvml"`` only when the driver answered.
    """
    process_mb = _process_vram_mb()
    reserved_mb = _reserved_mb()
    if process_mb is not None:
        reclaimable = process_mb >= _HARD_UNLOAD_MIN_PROCESS_MB
    else:
        reclaimable = reserved_mb >= _HARD_UNLOAD_MIN_RESERVED_MB
    return {
        "reclaimable": reclaimable,
        "vram_process_mb": process_mb,
        "vram_process_source": "nvml" if process_mb is not None else None,
        "min_process_mb": _HARD_UNLOAD_MIN_PROCESS_MB,
        "vram_reserved_mb": reserved_mb,
        "min_reserved_mb": _HARD_UNLOAD_MIN_RESERVED_MB,
    }


def _gate_fields(gate: dict[str, Any]) -> dict[str, Any]:
    """The gate's measurements, as a response reports them."""
    return {k: v for k, v in gate.items() if k != "reclaimable"}


def _describe_gate(gate: dict[str, Any]) -> str:
    if gate["vram_process_source"] == "nvml":
        return (
            f"{gate['vram_process_mb']} MiB held per the driver, "
            f"floor {gate['min_process_mb']} MiB"
        )
    return (
        f"{gate['vram_reserved_mb']} MB reserved (NVML unusable), "
        f"floor {gate['min_reserved_mb']} MB"
    )


def _exit_now(reason: str) -> None:
    """Exit so the CUDA context goes back to the card. Docker's restart policy
    brings the server back; the next request loads the model again."""
    logger.warning("%s — exiting to return the CUDA context", reason)
    _sys.stdout.flush()
    _sys.stderr.flush()
    os._exit(0)


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
    # Silence between sentence-chunks. None => CHATTERBOX_GAP_SECONDS/_GAP_SECONDS.
    # Only meaningful with trim_chunk_silence on, which is what makes the gap
    # the ACTUAL boundary length rather than a floor under the model's own tails.
    gap_seconds: float | None = None
    trim_chunk_silence: bool = True
    # Early-stop guard (truncation_guard.py): a chunk whose speaking rate is more
    # than this multiple of the request's median lost text, and is re-generated
    # sentence by sentence up to ``truncation_retries`` times. 0 disables either.
    truncation_max_rate_ratio: float = DEFAULT_MAX_RATE_RATIO
    truncation_retries: int = 2


class UnloadRequest(BaseModel):
    # Exit the process after unloading, so the CUDA context itself returns to
    # the host (torch.cuda.empty_cache() leaves it behind). Docker's
    # `restart: unless-stopped` brings the sidecar back and it lazy-loads on
    # the next request. Gated like wan / stable-audio / RIFE: the exit happens
    # only when the process still holds VRAM (see _exit_gate).
    hard: bool = False


def _is_idle(now: float | None = None) -> bool:
    """True when a model is loaded, untouched past the timeout, and nothing
    is in the server.

    ``_inflight`` catches a narrow window ``_last_used`` cannot: a request
    that has entered ``speech()`` and counted itself in but is still waiting
    for ``_model_lock`` (real synthesis holds that lock for its whole run, so
    this is brief) has not yet re-stamped ``_last_used``. Without the check,
    a drop here would cost that request a cold reload for no reason — it is
    surviving in either case, since only an EXIT (``_context_exit_due``,
    gated the same way) can destroy its connection.
    """
    if _IDLE_TIMEOUT_S <= 0 or _model is None or _inflight > 0:
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


def _context_exit_due(now: float | None = None) -> bool:
    """True when the idle timeout has run, no model is loaded, the process has
    served at least one request, and none is in the server.

    ``_last_used`` 0 means nothing ever ran here, so there is no context to
    give back. The model may have been dropped by the idle unload or by the
    ladder's soft ``/unload``: either way what is left is the context.
    """
    if _IDLE_TIMEOUT_S <= 0 or _model is not None or _last_used <= 0 or _inflight > 0:
        return False
    return ((now or time.time()) - _last_used) > _IDLE_TIMEOUT_S


def _maybe_idle_exit() -> bool:
    """Exit if idle with no model loaded and the process still holds VRAM.

    What is left then is the CUDA context the first synthesis created, and
    only an exit returns it (the module docstring has the measurements).
    Checked again under ``_model_lock``, and ``_inflight`` with it: a request
    counts itself in before it waits for the lock. Returns True only where
    ``os._exit`` is patched out.

    Blocking (takes ``_model_lock``) — call it off the event loop.
    """
    if not _context_exit_due():
        return False
    with _model_lock:
        if not _context_exit_due():
            return False
        gate = _exit_gate()
        if not gate["reclaimable"]:
            return False
        _exit_now(
            f"[IDLE EXIT] idle {_IDLE_TIMEOUT_S}s with no model loaded, "
            f"{_describe_gate(gate)}",
        )
        return True


def _idle_pass() -> None:
    """One idle pass: drop an idle model, then exit for what it leaves."""
    _maybe_idle_unload()
    _maybe_idle_exit()


@app.on_event("startup")
async def _start_idle_unloader():
    """Release VRAM after _IDLE_TIMEOUT_S with no /v1/audio/speech calls."""
    if _IDLE_TIMEOUT_S <= 0:
        logger.info("Idle unload disabled (CHATTERBOX_IDLE_TIMEOUT_S=%s)", _IDLE_TIMEOUT_S)
        return
    # Resolve NVML now, so a missing binding shows up in the boot log rather
    # than as an idle process that never gives its context back. In a worker
    # thread, like every other NVML read here.
    held = await asyncio.to_thread(_process_vram_mb)

    async def idle_unloader():
        while True:
            await asyncio.sleep(_IDLE_POLL_S)
            # In a worker thread: _model_lock is a threading.Lock held across
            # generate, so acquiring it on the event loop would stall every
            # other request for the length of a synthesis.
            try:
                await asyncio.to_thread(_idle_pass)
            except Exception as exc:  # never let the watchdog die silently
                logger.warning("idle unload failed: %s", exc)

    asyncio.create_task(idle_unloader())
    logger.info(
        "Idle unloader started (timeout=%ds); the exit gate measures %s",
        _IDLE_TIMEOUT_S,
        f"this process through NVML ({held} MiB held now, no CUDA context)"
        if held is not None
        else "torch's reserved pool (NVML unusable, see the warning above); it "
        "cannot see a CUDA context",
    )


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
        # Requests in the server (synthesizing, or waiting for the model).
        "inflight": _inflight,
        # What this process holds on the card as the driver counts it, CUDA
        # context included (null when NVML cannot answer). With the model
        # dropped this reads ~0.7 GB while torch's reserved pool reads ~20 MB.
        "vram_process_mb": _process_vram_mb(),
    }


def _decline_unload(hard: bool) -> dict[str, Any]:
    logger.warning(
        "[UNLOAD] declining %s unload — %d synthesis request(s) in flight",
        "hard" if hard else "soft", _inflight,
    )
    return {"status": "busy", "detail": "synthesis in flight; not unloading",
            "inflight": _inflight, "hard": hard}


def _deferred_exit() -> None:
    """The hard unload's exit, run once its response has gone out.

    A request that arrived in between keeps the process up: it is using the
    model (``_model_lock`` held), or counted in and about to (``_inflight``),
    or has already loaded it again. The lock is only tried, never waited
    for, so a synthesis that started meanwhile is never followed by an exit
    the moment it ends.
    """
    if not _model_lock.acquire(blocking=False):
        logger.warning("[HARD UNLOAD] not exiting: a synthesis started after the unload answered")
        return
    try:
        if _inflight > 0 or _model is not None:
            logger.warning("[HARD UNLOAD] not exiting: a request arrived after the unload answered")
            return
        _exit_now("[HARD UNLOAD]")
    finally:
        _model_lock.release()


@app.post("/unload")
def unload(req: UnloadRequest | None = None):
    """Free VRAM on demand (called by the GPU scheduler's reclaim path).

    Declines while a request is in the server rather than waiting out its
    synthesis: the ladder that calls this wants VRAM to start work, and the
    work already running is what the VRAM is for. A hard unload exits only
    when the process still holds VRAM once the model is dropped
    (``_exit_gate``), so a cold server answers ``nothing_to_reclaim`` instead
    of paying a restart for nothing; the exit is deferred so this response is
    delivered first.
    """
    hard = bool(req and req.hard)
    if _inflight > 0:
        return _decline_unload(hard)
    with _model_lock:
        if _inflight > 0:
            return _decline_unload(hard)
        released = _unload_model()
        gate = _exit_gate()
        if hard and gate["reclaimable"]:
            logger.info("Hard unload — exiting so the CUDA context returns (%s)", _describe_gate(gate))
            threading.Timer(0.5, _deferred_exit).start()
            return {"status": "exiting", "released": released, "hard": True,
                    **_gate_fields(gate)}
    return {"status": "nothing_to_reclaim" if hard else "unloaded",
            "released": released, "hard": hard, **_gate_fields(gate)}


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest):
    """Counted in ``_inflight`` for its whole stay, including any wait for the
    model lock, so neither ``/unload`` nor the idle exit acts under it."""
    global _inflight
    with _inflight_lock:
        _inflight += 1
    try:
        return _speech(req)
    finally:
        with _inflight_lock:
            _inflight -= 1


def _speech(req: SpeechRequest) -> Response:
    global _last_used
    if not req.input.strip():
        raise HTTPException(400, "empty input")

    # Validate BEFORE taking the lock / loading the model — a bad path should
    # 400 immediately rather than pay for a model load first.
    voice_ref = (req.audio_prompt_path or "").strip() or _DEFAULT_PROMPT_WAV
    if voice_ref:
        # Request-supplied paths stay inside the voices directories; a path
        # outside them is a 400, not a readable file (CodeQL #228).
        contained = contained_voice_path(voice_ref, voice_roots(default_prompt=_DEFAULT_PROMPT_WAV))
        if contained is None:
            raise HTTPException(400, f"audio_prompt_path not found under the voices directories: {voice_ref}")
        voice_ref = str(contained)

    with _model_lock:
        # Stamp on entry as well as exit: a long synthesis must not look idle
        # to the unloader that is polling while it runs.
        _last_used = time.time()
        try:
            return _synthesize(req, voice_ref)
        finally:
            _last_used = time.time()


def _spoken_seconds(wav: np.ndarray, sample_rate: int) -> float:
    return trim_edge_silence(wav, sample_rate).size / max(1, sample_rate)


def _repair_truncated_chunks(
    chunks: list[str],
    segments: list[np.ndarray],
    sample_rate: int,
    generate: Any,
    *,
    max_ratio: float,
    retries: int,
    gap_seconds: float,
    trim: bool,
) -> list[np.ndarray]:
    """Re-generate any chunk that stopped early (``truncation_guard``).

    A suspect chunk is generated again one sentence at a time and joined, up to
    ``retries`` times; the longest take wins. A chunk still short after the
    retries keeps its best take and logs a WARNING naming the text, since the
    audio is about to ship without part of it. Pure apart from ``generate``.
    """
    if max_ratio <= 0 or retries <= 0 or not chunks:
        return segments
    seconds = [_spoken_seconds(s, sample_rate) for s in segments]
    reference = reference_rate(chunks, seconds)
    out = list(segments)
    for i, (chunk, secs) in enumerate(zip(chunks, seconds, strict=False)):
        if not is_truncated(chunk, secs, reference, max_ratio=max_ratio):
            continue
        best, best_secs = out[i], secs
        for attempt in range(1, retries + 1):
            parts = [generate(p) for p in (split_sentences(chunk) or [chunk])]
            take = parts[0] if len(parts) == 1 else join_segments(
                parts, sample_rate, gap_seconds=gap_seconds, trim=trim,
            )
            take_secs = _spoken_seconds(take, sample_rate)
            if take_secs > best_secs:
                best, best_secs = take, take_secs
            if not is_truncated(chunk, best_secs, reference, max_ratio=max_ratio):
                logger.warning(
                    "chunk %d stopped early (%.1fs for %d chars, %.1f chars/s vs "
                    "median %.1f); repaired on retry %d -> %.1fs",
                    i, secs, len(chunk), len(chunk) / max(secs, 1e-6), reference,
                    attempt, best_secs,
                )
                break
        else:
            logger.warning(
                "chunk %d stopped early and is STILL short after %d retries "
                "(%.1fs for %d chars, median %.1f chars/s) — part of this text "
                "is missing from the audio: %r",
                i, retries, best_secs, len(chunk), reference, chunk[:200],
            )
        out[i] = best
    return out


def _synthesize(req: SpeechRequest, voice_ref: str | None) -> Response:
    """Render one request. Caller MUST hold ``_model_lock``."""
    model = _get_model()
    sample_rate = int(model.sr)

    # voice_ref was resolved and existence-checked by the caller, before the
    # model load — per-request ref > env default > built-in voice (None).
    def generate(text: str) -> np.ndarray:
        # generate(text, audio_prompt_path=, exaggeration=, cfg_weight=) -> torch
        # tensor [1, N] at model.sr. audio_prompt_path=None => default voice.
        wav = model.generate(text, audio_prompt_path=voice_ref,
                             exaggeration=req.exaggeration, cfg_weight=req.cfg_weight)
        return wav.squeeze(0).detach().cpu().numpy().astype(np.float32)

    chunks = chunk_text(req.input)
    segments: list[np.ndarray] = [generate(chunk) for chunk in chunks]

    # Each generation carries its own unpredictable leading/trailing silence, so
    # raw-concatenating with a fixed gap produced (tail + gap + head) — measured
    # up to 3.46s where only 0.25s was ever inserted, on ~40% of boundaries.
    # join_segments trims the edges first so the boundary is exactly gap_seconds.
    gap_seconds = _GAP_SECONDS if req.gap_seconds is None else req.gap_seconds
    segments = _repair_truncated_chunks(
        chunks, segments, sample_rate, generate,
        max_ratio=req.truncation_max_rate_ratio,
        retries=req.truncation_retries,
        gap_seconds=gap_seconds, trim=req.trim_chunk_silence,
    )
    audio_samples = join_segments(
        segments, sample_rate,
        gap_seconds=gap_seconds, trim=req.trim_chunk_silence,
    )
    logger.info(
        "joined %d chunk(s): gap=%.2fs trim=%s -> %.1fs",
        len(chunks), gap_seconds, req.trim_chunk_silence,
        audio_samples.size / max(1, sample_rate),
    )
    audio = _encode(audio_samples, sample_rate, req.response_format.lower())
    media = {"mp3": "audio/mpeg", "wav": "audio/wav",
             "opus": "audio/opus", "aac": "audio/aac"}.get(
        req.response_format.lower(), "application/octet-stream")
    return Response(content=audio, media_type=media)
