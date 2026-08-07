"""Wan 2.2 TI2V-5B Image/Text-to-Video inference server — sidecar mate of image-gen-server.

Loads ``Wan-AI/Wan2.2-TI2V-5B-Diffusers`` lazily on first request and
exposes a single ``/generate`` endpoint that the
:class:`Wan21Provider <services.video_providers.wan2_1.Wan21Provider>`
plugin POSTs to. Mirrors the request/response shape the image-gen sidecar
uses so one operator runbook covers both:

- POST ``/generate`` with JSON body matching the provider's request
  schema (prompt, negative_prompt, steps, guidance_scale, duration_s,
  width, height, fps, **image_b64**).
- Returns either ``video/mp4`` raw bytes (preferred) or
  ``application/json`` with ``{"video_path": "<path>"}`` so the worker
  can fetch via shared filesystem.

**Image-to-video is the primary path** (video-quality spec §3.3, Piece 4).
The hero renderer renders an image-gen still first, then POSTs it as
``image_b64``; the server decodes it and animates it via
``WanImageToVideoPipeline`` (i2v). A text-to-video fallback (no
``image_b64``) shares the loaded components through ``WanPipeline.from_pipe``,
so it costs no extra VRAM and preserves the pre-Piece-4 T2V behaviour for
any caller that doesn't send an init image.

Why a sidecar, not in-process: Wan 2.2 TI2V-5B is a 5B diffusion
transformer with a fp32 high-compression VAE and an UMT5-XXL text
encoder (~34GB of weights on disk). Loading it inside the worker
container would compete with Ollama / image-gen for VRAM and serialize
requests through the worker's event loop. A dedicated server with its
own GPU lock + idle-timeout unload mirrors how every other GPU-bound
model lives on this host. The model loads component-by-component
straight to the GPU (~24GB resident in bf16, fits the 32GB card) rather
than ``enable_model_cpu_offload`` — offload would hold all ~34GB of
weights in CPU RAM, which OOM-kills on this host's ~23GB WSL backend.
The worker's GPU scheduler evicts Ollama's writer model before a render
(``gpu.lock("video")``, poindexter#1766) so the card has room.

Endpoints:
    GET  /health    — status, model, VRAM, degradation reason
    POST /generate  — generate video clip from prompt (+ optional init image)
    POST /unload    — free VRAM (called by GPU scheduler)

Failure model: matches image-gen-server. Anything wrong (model load fails,
CUDA OOM, etc.) puts the server in DEGRADED state — /generate returns
503 with a useful error string, /health reports the reason, server
keeps running so it can recover.
"""

import argparse
import asyncio
import base64
import gc
import io
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

OUTPUT_DIR = Path(os.path.expanduser("~")) / ".poindexter" / "generated-videos"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Default model — Wan 2.2 TI2V-5B, the unified text+image-to-video 5B
# model (Apache-2.0). Operators override via WAN_MODEL_ID env (no DB
# roundtrip on this server, since model selection here is much narrower
# than image-gen's multi-model registry). The provider's swappable
# ``generative_video_model`` seam (spec §3.3) sets this env in compose.
_DEFAULT_MODEL_ID = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
MODEL_ID = os.getenv("WAN_MODEL_ID", _DEFAULT_MODEL_ID)

# Pinned commit for the default model. An unpinned load resolves to whatever
# the repo's `main` points at on the day of a cold start, so the weights behind
# an unchanged deploy can move without a commit here — a supply-chain exposure
# and a reproducibility hole. This SHA was upstream `main` at pin time
# (2026-07-17) and matches this host's cached snapshot, so pinning re-downloads
# nothing and changes no behaviour today; it only stops future drift.
#
# The pin is deliberately conditional. A SHA is only meaningful inside the repo
# it came from, so applying it to an operator's custom WAN_MODEL_ID would
# request a commit that does not exist there — a guaranteed hard failure at
# load. A custom model therefore either supplies WAN_MODEL_REVISION to pin
# itself (recommended, and warned about below when absent) or tracks upstream.
#
# bandit's B615 only understands transformers/huggingface_hub, not diffusers,
# so it cannot see three of the four loads below. The real guard is
# tests/unit/scripts/test_hf_revision_pinning.py.
_DEFAULT_MODEL_REVISION = "b8fff7315c768468a5333511427288870b2e9635"
MODEL_REVISION: str | None = os.getenv("WAN_MODEL_REVISION", "").strip() or None
if MODEL_REVISION is None and MODEL_ID == _DEFAULT_MODEL_ID:
    MODEL_REVISION = _DEFAULT_MODEL_REVISION

# Idle unload — the loaded model is large; release it so image-gen / Ollama
# can reclaim VRAM when no video work is queued.
IDLE_TIMEOUT_S = int(os.getenv("WAN_IDLE_TIMEOUT_S", "120"))

# Hard-unload floor (poindexter#962): reserved-VRAM below which a hard unload
# declines to exit. The idle unloader drops the pipeline OBJECTS, but torch's
# caching allocator keeps the multi-GB reserved pool + CUDA context until the
# process exits — observed 10,240 MiB still held ~6.5h after the last render,
# pinning the render GPU under dispatch_media_pipeline's free-VRAM gate all
# night. Env (not app_settings) because this server is deliberately DB-free;
# 512 MB mirrors image_gen_hard_unload_min_reserved_mb's default.
HARD_UNLOAD_MIN_RESERVED_MB = int(
    os.getenv("WAN_HARD_UNLOAD_MIN_RESERVED_MB", "512")
)

# Frame cap so a runaway request can't OOM the GPU. Wan's temporal VAE
# compresses by 4, so valid frame counts are 4k+1 (81, 121, …).
_MAX_FRAMES = 121  # 5s at 24fps; TI2V-5B's documented working range

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("wan-server")
app = FastAPI(title="Wan 2.2 TI2V Server", version="2.0")


class ServerState:
    """Mutable singleton state — pipeline cache, idle clock, degraded flag."""

    def __init__(self) -> None:
        self.pipeline: Any | None = None  # WanImageToVideoPipeline (i2v + shared)
        self.t2v_pipeline: Any | None = None  # WanPipeline.from_pipe (shares VRAM)
        self.mod_value: int = 32  # dim alignment; recomputed from the pipe on load
        self.last_used: float = 0.0
        # Requests currently executing (or about to). The idle-unloader must
        # never exit the process while one is in flight — see _idle_unload_tick.
        self.inflight: int = 0
        self.degraded: bool = False
        self.degraded_reason: str | None = None
        # Single GPU lock — concurrent /generate calls would compete
        # for VRAM and produce torch CUDA OOMs. Serialize at the
        # server level rather than relying on every caller to.
        self.gpu_lock = asyncio.Lock()


state = ServerState()


# ============================================================================
# PIPELINE LOAD / UNLOAD
# ============================================================================


def _load_pipeline_blocking() -> Any:
    """Synchronously load the i2v pipeline; runs in a worker thread.

    Raises on any failure so the caller can flip the server into
    DEGRADED state. Returns the loaded pipeline on success.
    """
    from diffusers import (
        AutoencoderKLWan,
        WanImageToVideoPipeline,
        WanTransformer3DModel,
    )
    from transformers import UMT5EncoderModel

    if MODEL_REVISION is None:
        logger.warning(
            "WAN_MODEL_ID=%s is a custom model with no WAN_MODEL_REVISION — "
            "loading whatever that repo's `main` points at right now, which can "
            "change under you between cold starts. Set WAN_MODEL_REVISION=<commit-sha> "
            "to pin it.",
            MODEL_ID,
        )
    logger.info(
        "Loading WanImageToVideoPipeline from %s @ %s",
        MODEL_ID, MODEL_REVISION or "<upstream default>",
    )
    # Load each component and place it on the GPU as it loads — NOT
    # enable_model_cpu_offload. Offload keeps all ~34GB of weights in CPU
    # RAM, but this host's WSL backend has only ~23GB, so offload gets
    # SIGKILLed by the Linux OOM-killer mid-render. The model is only
    # ~24GB resident in VRAM (the fp32 transformer halves to bf16 on load,
    # the bf16 UMT5-XXL encoder stays ~11GB, the fp32 VAE ~3GB), which fits
    # the 32GB card with headroom — *once the worker's GPU scheduler has
    # evicted Ollama's writer model before the render* (gpu.lock("video"),
    # poindexter#1766). Loading component-by-component keeps the CPU-RAM
    # high-water mark to a single component (~12GB) instead of the whole
    # model, so the load itself also stays under the WSL limit.
    #
    # Wan's high-compression VAE must run in fp32 — bf16 produces NaN
    # latents on the decode pass (per the diffusers Wan model card).
    vae = AutoencoderKLWan.from_pretrained(
        MODEL_ID, subfolder="vae", torch_dtype=torch.float32,
        revision=MODEL_REVISION,
    ).to("cuda")
    text_encoder = UMT5EncoderModel.from_pretrained(
        MODEL_ID, subfolder="text_encoder", torch_dtype=torch.bfloat16,
        revision=MODEL_REVISION,
    ).to("cuda")
    transformer = WanTransformer3DModel.from_pretrained(
        MODEL_ID, subfolder="transformer", torch_dtype=torch.bfloat16,
        revision=MODEL_REVISION,
    ).to("cuda")
    pipe = WanImageToVideoPipeline.from_pretrained(
        MODEL_ID,
        vae=vae,
        text_encoder=text_encoder,
        transformer=transformer,
        torch_dtype=torch.bfloat16,
        revision=MODEL_REVISION,
    )
    # Tile the VAE decode so a multi-frame 720p clip doesn't spike VRAM on
    # the final fp32 decode pass.
    try:
        pipe.vae.enable_tiling()
    except Exception:
        pass
    # Dim alignment: output height/width must be multiples of the VAE
    # spatial scale × the transformer patch size (32 for TI2V-5B's
    # high-compression VAE). Compute it from the loaded pipe rather than
    # hardcoding, so a model swap stays correct.
    try:
        state.mod_value = int(
            pipe.vae_scale_factor_spatial * pipe.transformer.config.patch_size[1]
        )
    except Exception:
        state.mod_value = 32
    logger.info(
        "Wan 2.2 TI2V-5B ready on %s (dim mod=%d, resident %d MB)",
        torch.cuda.get_device_name(0), state.mod_value,
        torch.cuda.memory_allocated(0) // 1024 // 1024,
    )
    return pipe


def _is_retryable_load_failure(exc: BaseException) -> bool:
    """Whether a failed load is worth retrying on the next /generate.

    An OOM is a statement about the card AT THAT MOMENT, not about the model
    or this server — the crowding process (image-gen holding ~25 GB) will have
    unloaded by the next attempt. Latching degraded on it turns a transient
    contention loss into a permanent outage that only a container restart
    clears. Anything else (a bad model id, a missing revision, a broken
    install) really is persistent and keeps the latch.
    """
    name = type(exc).__name__
    if "OutOfMemory" in name:
        return True
    text = str(exc).lower()
    return "out of memory" in text or "cuda error: out of memory" in text


def _release_partial_load() -> None:
    """Drop whatever a failed load left resident.

    ``_load_pipeline_blocking`` moves components to CUDA one at a time, so an
    OOM part-way through leaves the already-moved ones resident with no handle
    to them (measured 2026-07-26: 14.9 GB still held by the process after a
    failed load). Without this the leak compounds across attempts — each retry
    starts with less room than the last and fails sooner.
    """
    state.pipeline = None
    try:
        gc.collect()
        torch.cuda.empty_cache()
        logger.warning(
            "Released partial load after failure; VRAM still allocated: %d MB",
            torch.cuda.memory_allocated(0) // 1024 // 1024,
        )
    except Exception as exc:  # noqa: BLE001  # silent-ok: cleanup is best-effort
        # by definition — we are already on the failure path, and a cleanup
        # error must not mask the original load exception the caller re-raises.
        logger.warning("empty_cache after failed load failed: %s", exc)


async def _ensure_pipeline_loaded() -> Any:
    """Lazy-load the i2v pipeline. Caller must hold the GPU lock."""
    if state.pipeline is not None:
        return state.pipeline
    try:
        state.pipeline = await asyncio.to_thread(_load_pipeline_blocking)
        state.degraded = False
        state.degraded_reason = None
    except Exception as exc:
        # poindexter#907 defect 1. Two things went wrong here before: the
        # partial load stayed resident (leak), and `degraded` latched for
        # every cause including a transient OOM (no self-recovery). Together
        # they turned one unlucky render into a permanent 503 until someone
        # restarted the container.
        _release_partial_load()
        retryable = _is_retryable_load_failure(exc)
        state.degraded = not retryable
        state.degraded_reason = None if retryable else f"{type(exc).__name__}: {exc}"
        logger.exception(
            "WanImageToVideoPipeline load failed (%s)",
            "retryable — will re-attempt on next /generate" if retryable
            else "persistent — latching degraded",
        )
        raise
    return state.pipeline


async def _ensure_t2v_loaded(i2v_pipe: Any) -> Any:
    """Lazy-build the T2V fallback pipeline. Caller must hold the GPU lock.

    ``WanPipeline.from_pipe`` reuses the i2v pipeline's already-loaded
    components (transformer / VAE / text encoder / scheduler), so the
    T2V path costs no extra VRAM or load time — it just exposes the
    no-init-image call signature.
    """
    if state.t2v_pipeline is not None:
        return state.t2v_pipeline
    from diffusers import WanPipeline

    state.t2v_pipeline = await asyncio.to_thread(
        lambda: WanPipeline.from_pipe(i2v_pipe)
    )
    return state.t2v_pipeline


def _unload_pipeline_blocking() -> None:
    """Release VRAM. Must hold the GPU lock at call time."""
    if state.pipeline is None and state.t2v_pipeline is None:
        return
    logger.info("Unloading Wan pipelines to free VRAM")
    state.t2v_pipeline = None
    state.pipeline = None
    torch.cuda.empty_cache()
    gc.collect()
    logger.info(
        "VRAM in use: %d MB",
        torch.cuda.memory_allocated(0) // 1024 // 1024,
    )


def _hard_exit_if_reserved_pool(*, quiet_skip: bool = False) -> dict[str, Any]:
    """Floor-gated process exit (poindexter#962). Caller must hold the GPU
    lock and have already soft-unloaded — allocated is ~0 by construction, so
    the gate measures ``memory_reserved``, the pool only a process exit
    returns. Below the floor: no exit (``quiet_skip`` silences the log for
    the 30s idle cadence). At or above: flush + ``os._exit(0)``; Docker's
    restart policy revives the server and it lazy-loads on next /generate.

    Shared by the ``POST /unload {"hard": true}`` endpoint (consumer-driven
    reclaim) and the idle unloader (self-driven — 2026-08-01: the post-render
    soft unload left a 10.3 GB reserved pool squatting for hours because no
    render was pending to trigger the reclaim rung, OOMing every Ollama load
    on the card)."""
    reserved_mb = torch.cuda.memory_reserved(0) // 1024 // 1024
    if reserved_mb < HARD_UNLOAD_MIN_RESERVED_MB:
        if not quiet_skip:
            logger.info(
                "[HARD UNLOAD] skipped — %d MB reserved is below the "
                "%d MB floor; exiting would reclaim nothing and cost a "
                "cold reload on the next hero shot",
                reserved_mb, HARD_UNLOAD_MIN_RESERVED_MB,
            )
        return {
            "status": "nothing_to_reclaim",
            "vram_reserved_mb": reserved_mb,
            "min_reserved_mb": HARD_UNLOAD_MIN_RESERVED_MB,
        }
    logger.warning(
        "[HARD UNLOAD] exiting process to return the CUDA context to "
        "the host (vram_reserved=%d MB >= %d MB floor); Docker "
        "restart policy brings it back",
        reserved_mb, HARD_UNLOAD_MIN_RESERVED_MB,
    )
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
    return {"status": "exiting"}  # unreachable outside tests


async def _idle_unload_tick() -> None:
    """One idle-unloader pass: after IDLE_TIMEOUT_S of no /generate calls,
    soft-unload the pipelines, then hard-exit if the leftover reserved pool
    clears the floor. The second stage is what actually returns VRAM to the
    card: without it an idle wan squats ~10 GB between renders until some
    consumer event (a render gate reclaim) happens to notice, starving
    ollama-primary on the shared GPU (2026-08-01 incident)."""
    # In-flight guard (2026-08-07): a generation can run LONGER than
    # IDLE_TIMEOUT_S (a 480p i2v hero takes ~147s vs the 120s default), and
    # last_used is only stamped when it finishes. Without this check the tick
    # reads a stale last_used mid-generation, blocks on gpu_lock for the rest
    # of the run, and then hard-exits the process the instant the lock frees —
    # BEFORE FastAPI sends the response. The clip was written to disk, but the
    # worker saw a dropped connection, logged "wan provider returned no
    # result", and fell back to a Ken Burns still. Every hero of the 2026-08-06
    # /-07 renders was lost this way, ~147 GPU-seconds each.
    if state.inflight > 0:
        return
    if (time.time() - state.last_used) <= IDLE_TIMEOUT_S:
        return
    # Cheap pre-check outside the lock: nothing loaded and no reserved pool
    # means nothing to do — the common cold-idle case, every 30s.
    if (
        state.pipeline is None
        and state.t2v_pipeline is None
        and torch.cuda.memory_reserved(0) // 1024 // 1024
        < HARD_UNLOAD_MIN_RESERVED_MB
    ):
        return
    async with state.gpu_lock:
        # Re-check under the lock: a request may have arrived (and finished)
        # while we waited for it. Exiting now would kill its response.
        if state.inflight > 0 or (time.time() - state.last_used) <= IDLE_TIMEOUT_S:
            return
        _unload_pipeline_blocking()
        _hard_exit_if_reserved_pool(quiet_skip=True)


# ============================================================================
# IMAGE + DIMENSION HELPERS
# ============================================================================


def _decode_image_b64(b64: str) -> Any:
    """Decode a base64 image (the shot's image-gen still) to an RGB PIL image."""
    from PIL import Image

    raw = base64.b64decode(b64)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _fit_init_image(img: Any, width: int, height: int) -> Any:
    """Aspect-preserving center-crop of the i2v init still to the output dims.

    A bare ``resize((w, h))`` (the old behaviour) stretches non-matching
    aspects — the worker historically sent 1024×1024 stills, so every
    832×480 hero clip animated a 42%-vertically-squashed frame. ``fit``
    scales-then-crops around the center instead, so geometry survives any
    caller/server aspect mismatch.
    """
    from PIL import Image, ImageOps

    if img.size == (width, height):
        return img
    return ImageOps.fit(
        img, (width, height), method=Image.Resampling.LANCZOS,
    )


def _snap_dim(x: int, mod: int) -> int:
    """Snap a dimension down to the nearest multiple of ``mod`` (>= mod)."""
    return max(mod, (int(x) // mod) * mod)


def _snap_frames(n: int) -> int:
    """Snap a frame count to Wan's required 4k+1 (temporal VAE compresses ×4)."""
    n = max(5, min(_MAX_FRAMES, int(n)))
    return ((n - 1) // 4) * 4 + 1


# ============================================================================
# REQUEST / RESPONSE MODELS — match Wan21Provider's POST shape
# ============================================================================


class GenerateRequest(BaseModel):
    prompt: str
    # The canonical Wan negative prompt (the model card's English list) —
    # Wan quality leans heavily on it, and "static / still picture" directly
    # fights the barely-animates i2v failure mode. Callers that configure
    # their own negative override it; the provider OMITS the field when the
    # operator hasn't configured one, so this default actually applies.
    negative_prompt: str = Field(
        default=(
            "bright tones, overexposed, static, blurred details, subtitles, "
            "style, works, paintings, images, overall gray, worst quality, "
            "low quality, JPEG compression residue, ugly, incomplete, "
            "extra fingers, poorly drawn hands, poorly drawn faces, "
            "deformed, disfigured, misshapen limbs, fused fingers, "
            "still picture, cluttered background, three legs, "
            "many people in the background, walking backwards"
        ),
    )
    steps: int = Field(default=50, ge=1, le=100)
    guidance_scale: float = Field(default=5.0, ge=0.0, le=20.0)
    duration_s: int = Field(default=5, ge=1, le=15)
    # Wan 2.2 TI2V-5B's documented 720P@24fps working range. (The old
    # 832×480@16 defaults were Wan 2.1 1.3B's profile — off the 5B's
    # training distribution.)
    width: int = Field(default=1280, ge=256, le=1280)
    height: int = Field(default=704, ge=256, le=1280)
    fps: int = Field(default=24, ge=8, le=30)
    model: str = Field(default="wan2.1-1.3b")  # caller-supplied label, ignored
    # Piece 4 (spec §3.3): base64 init image (the shot's image-gen still). When
    # present the server animates it via i2v; absent → text-to-video.
    image_b64: str | None = Field(default=None)


# ============================================================================
# LIFECYCLE
# ============================================================================


@app.on_event("startup")
async def on_startup() -> None:
    gpu_ok = torch.cuda.is_available()
    if not gpu_ok:
        state.degraded = True
        state.degraded_reason = "CUDA not available"
        logger.warning(
            "Started DEGRADED: CUDA not available. /generate will 503.",
        )
    else:
        logger.info(
            "Wan server starting; GPU=%s, model=%s. Pipeline lazy-loads on "
            "first /generate.",
            torch.cuda.get_device_name(0), MODEL_ID,
        )

    async def idle_unloader() -> None:
        """Drive _idle_unload_tick() every 30s, forever. Exception-proof: one
        failed tick must not kill the loop — image-gen's identical bare loop
        died on a stray exception (last idle unload 2026-07-30 13:04Z) and
        its pipeline squatted 19 GB through the following night, OOMing every
        Ollama load on the card."""
        while True:
            await asyncio.sleep(30)
            try:
                await _idle_unload_tick()
            except Exception:
                logger.exception("[IDLE] unload tick failed; loop continues")

    asyncio.create_task(idle_unloader())


# ============================================================================
# ENDPOINTS
# ============================================================================


@app.get("/health")
async def health() -> dict[str, Any]:
    gpu_ok = torch.cuda.is_available()
    if state.degraded:
        status = "degraded"
    elif state.pipeline is not None:
        status = "ready"
    else:
        status = "idle"
    return {
        "status": status,
        "degraded": state.degraded,
        "degraded_reason": state.degraded_reason,
        "model": "wan2.2-ti2v-5b",
        "model_display_name": "Wan 2.2 TI2V 5B",
        "model_id": MODEL_ID,
        "i2v": True,
        "gpu": torch.cuda.get_device_name(0) if gpu_ok else None,
        "vram_total_mb": (
            torch.cuda.get_device_properties(0).total_memory // 1024 // 1024
            if gpu_ok else 0
        ),
        "vram_used_mb": (
            torch.cuda.memory_allocated(0) // 1024 // 1024
            if gpu_ok else 0
        ),
        "gpu_available": gpu_ok,
        "idle_timeout_s": IDLE_TIMEOUT_S,
    }


class UnloadRequest(BaseModel):
    """Body for ``POST /unload``. ``hard`` opts into the process-exit path."""

    hard: bool = False


@app.post("/unload")
async def unload(req: UnloadRequest | None = None) -> dict[str, Any]:
    """Manual VRAM release — called by the worker's GPU scheduler when
    Ollama / image-gen needs the card.

    Soft (default, no body or ``{"hard": false}``): drop the pipelines +
    ``torch.cuda.empty_cache()`` — the pre-existing behavior.

    Hard (``{"hard": true}``, poindexter#962): also exit the process.
    ``empty_cache()`` does NOT return the caching allocator's reserved pool
    or the CUDA context to the host — only a process exit does (the same
    physics as image-gen's hard unload; observed here as ~10 GB still held
    hours after the idle unloader had dropped the pipeline objects). Docker's
    ``restart: unless-stopped`` brings the server back; it lazy-loads on the
    next ``/generate``. Used by ``dispatch_media_pipeline``'s render-GPU
    VRAM reclaim.

    The exit is gated on there being something worth reclaiming
    (``WAN_HARD_UNLOAD_MIN_RESERVED_MB``): measured on ``memory_reserved``,
    NOT ``memory_allocated`` — the unload above just dropped every live
    tensor, so allocated is ~0 by construction; the reserved pool is what a
    process exit actually returns. Below the floor the endpoint answers
    ``nothing_to_reclaim`` instead of paying a pointless cold-start window
    (the image-gen lesson: ~24 consecutive no-op exits before its gate).
    """
    if req and req.hard:
        async with state.gpu_lock:
            _unload_pipeline_blocking()
            return _hard_exit_if_reserved_pool()

    async with state.gpu_lock:
        _unload_pipeline_blocking()
    return {"status": "unloaded"}


def _generate_blocking(
    pipeline: Any,
    *,
    prompt: str,
    negative_prompt: str,
    steps: int,
    guidance_scale: float,
    num_frames: int,
    width: int,
    height: int,
    fps: int,
    image_b64: str | None,
    output_path: str,
) -> tuple[float, int]:
    """Run the diffusion pass + export to MP4. Synchronous; called
    inside ``asyncio.to_thread`` so the FastAPI event loop stays free.

    When ``image_b64`` is set, animates the decoded still via i2v
    (``image=`` kwarg); otherwise runs text-to-video. Returns
    ``(elapsed_s, frame_count)``.
    """
    from diffusers.utils import export_to_video

    kwargs: dict[str, Any] = dict(
        prompt=prompt,
        negative_prompt=negative_prompt,
        height=height,
        width=width,
        num_frames=num_frames,
        num_inference_steps=steps,
        guidance_scale=guidance_scale,
    )
    if image_b64:
        # Fit the init still to the snapped output dims so the i2v
        # conditioning frame matches the generated frame geometry —
        # aspect-preserving center-crop, never a stretch.
        kwargs["image"] = _fit_init_image(
            _decode_image_b64(image_b64), width, height,
        )

    started = time.perf_counter()
    output = pipeline(**kwargs)
    frames = output.frames[0]  # pipeline yields list-of-list per batch
    export_to_video(frames, output_path, fps=fps)
    elapsed = time.perf_counter() - started
    return elapsed, num_frames


@app.post("/generate")
async def generate(req: GenerateRequest) -> Response:
    if state.degraded:
        raise HTTPException(
            status_code=503,
            detail=f"server degraded: {state.degraded_reason}",
        )
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is empty")

    state.inflight += 1
    try:
        return await _generate_inner(req)
    finally:
        # Stamp on the way out too: the idle tick's window must start when the
        # RESPONSE is done, not when inference ended, or a long FileResponse
        # stream could still race the hard exit.
        state.last_used = time.time()
        state.inflight -= 1


async def _generate_inner(req: GenerateRequest) -> Response:
    """The actual generate path — wrapped by ``generate`` so the in-flight
    counter brackets the whole request including response streaming."""
    output_filename = f"wan_{uuid.uuid4().hex}.mp4"
    output_path = OUTPUT_DIR / output_filename

    async with state.gpu_lock:
        try:
            pipeline = await _ensure_pipeline_loaded()
            if not req.image_b64:
                # Text-to-video fallback shares the i2v components.
                pipeline = await _ensure_t2v_loaded(pipeline)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"pipeline load failed: {exc}",
            ) from exc

        width = _snap_dim(req.width, state.mod_value)
        height = _snap_dim(req.height, state.mod_value)
        num_frames = _snap_frames(req.duration_s * req.fps)

        try:
            elapsed_s, num_frames = await asyncio.to_thread(
                _generate_blocking,
                pipeline,
                prompt=req.prompt,
                negative_prompt=req.negative_prompt,
                steps=req.steps,
                guidance_scale=req.guidance_scale,
                num_frames=num_frames,
                width=width,
                height=height,
                fps=req.fps,
                image_b64=req.image_b64,
                output_path=str(output_path),
            )
        except Exception as exc:
            logger.exception("[wan] generation failed")
            raise HTTPException(
                status_code=500,
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        state.last_used = time.time()

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise HTTPException(
            status_code=500,
            detail="generation succeeded but output file is missing",
        )

    mode = "i2v" if req.image_b64 else "t2v"
    logger.info(
        "[wan] generated %s %dpx×%dpx, %d frames in %.1fs: %s",
        mode, width, height, num_frames, elapsed_s, output_filename,
    )

    # Return the raw MP4 — matches Wan21Provider's preferred response
    # format. The X-Elapsed-Seconds header is logged into cost_logs by
    # the provider for sanity checking the energy estimate.
    return FileResponse(
        path=str(output_path),
        media_type="video/mp4",
        filename=output_filename,
        headers={
            "X-Elapsed-Seconds": f"{elapsed_s:.2f}",
            "X-Frame-Count": str(num_frames),
            "X-Width": str(width),
            "X-Height": str(height),
            "X-Fps": str(req.fps),
            "X-Mode": mode,
        },
    )


# ============================================================================
# ENTRY POINT
# ============================================================================


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9840)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
