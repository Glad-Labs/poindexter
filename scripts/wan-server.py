"""Wan 2.2 TI2V-5B Image/Text-to-Video inference server — sidecar mate of sdxl-server.

Loads ``Wan-AI/Wan2.2-TI2V-5B-Diffusers`` lazily on first request and
exposes a single ``/generate`` endpoint that the
:class:`Wan21Provider <services.video_providers.wan2_1.Wan21Provider>`
plugin POSTs to. Mirrors the request/response shape the SDXL sidecar
uses so one operator runbook covers both:

- POST ``/generate`` with JSON body matching the provider's request
  schema (prompt, negative_prompt, steps, guidance_scale, duration_s,
  width, height, fps, **image_b64**).
- Returns either ``video/mp4`` raw bytes (preferred) or
  ``application/json`` with ``{"video_path": "<path>"}`` so the worker
  can fetch via shared filesystem.

**Image-to-video is the primary path** (video-quality spec §3.3, Piece 4).
The hero renderer renders an SDXL still first, then POSTs it as
``image_b64``; the server decodes it and animates it via
``WanImageToVideoPipeline`` (i2v). A text-to-video fallback (no
``image_b64``) shares the loaded components through ``WanPipeline.from_pipe``,
so it costs no extra VRAM and preserves the pre-Piece-4 T2V behaviour for
any caller that doesn't send an init image.

Why a sidecar, not in-process: Wan 2.2 TI2V-5B is a 5B diffusion
transformer with a fp32 high-compression VAE and an UMT5-XXL text
encoder (~34GB of weights on disk). Loading it inside the worker
container would compete with Ollama / SDXL for VRAM and serialize
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

Failure model: matches sdxl-server. Anything wrong (model load fails,
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
import time
import uuid
from pathlib import Path
from typing import Any, Optional

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
# than SDXL's multi-model registry). The provider's swappable
# ``generative_video_model`` seam (spec §3.3) sets this env in compose.
MODEL_ID = os.getenv(
    "WAN_MODEL_ID", "Wan-AI/Wan2.2-TI2V-5B-Diffusers",
)

# Idle unload — the loaded model is large; release it so SDXL / Ollama
# can reclaim VRAM when no video work is queued.
IDLE_TIMEOUT_S = int(os.getenv("WAN_IDLE_TIMEOUT_S", "120"))

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

    logger.info("Loading WanImageToVideoPipeline from %s", MODEL_ID)
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
    ).to("cuda")
    text_encoder = UMT5EncoderModel.from_pretrained(
        MODEL_ID, subfolder="text_encoder", torch_dtype=torch.bfloat16,
    ).to("cuda")
    transformer = WanTransformer3DModel.from_pretrained(
        MODEL_ID, subfolder="transformer", torch_dtype=torch.bfloat16,
    ).to("cuda")
    pipe = WanImageToVideoPipeline.from_pretrained(
        MODEL_ID,
        vae=vae,
        text_encoder=text_encoder,
        transformer=transformer,
        torch_dtype=torch.bfloat16,
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


async def _ensure_pipeline_loaded() -> Any:
    """Lazy-load the i2v pipeline. Caller must hold the GPU lock."""
    if state.pipeline is not None:
        return state.pipeline
    try:
        state.pipeline = await asyncio.to_thread(_load_pipeline_blocking)
        state.degraded = False
        state.degraded_reason = None
    except Exception as exc:
        state.degraded = True
        state.degraded_reason = f"{type(exc).__name__}: {exc}"
        logger.exception("WanImageToVideoPipeline load failed")
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


# ============================================================================
# IMAGE + DIMENSION HELPERS
# ============================================================================


def _decode_image_b64(b64: str) -> Any:
    """Decode a base64 image (the shot's SDXL still) to an RGB PIL image."""
    from PIL import Image

    raw = base64.b64decode(b64)
    return Image.open(io.BytesIO(raw)).convert("RGB")


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
    negative_prompt: str = Field(
        default=(
            "low quality, blurry, distorted, watermark, text, letters, "
            "deformed, glitch"
        ),
    )
    steps: int = Field(default=50, ge=1, le=100)
    guidance_scale: float = Field(default=5.0, ge=0.0, le=20.0)
    duration_s: int = Field(default=5, ge=1, le=15)
    width: int = Field(default=832, ge=256, le=1280)
    height: int = Field(default=480, ge=256, le=1280)
    fps: int = Field(default=16, ge=8, le=30)
    model: str = Field(default="wan2.1-1.3b")  # caller-supplied label, ignored
    # Piece 4 (spec §3.3): base64 init image (the shot's SDXL still). When
    # present the server animates it via i2v; absent → text-to-video.
    image_b64: Optional[str] = Field(default=None)


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
        """Release VRAM after IDLE_TIMEOUT_S of no /generate calls."""
        while True:
            await asyncio.sleep(30)
            if (
                state.pipeline is not None
                and (time.time() - state.last_used) > IDLE_TIMEOUT_S
            ):
                async with state.gpu_lock:
                    _unload_pipeline_blocking()

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


@app.post("/unload")
async def unload() -> dict[str, str]:
    """Manual VRAM release — called by the worker's GPU scheduler when
    Ollama / SDXL needs the card.
    """
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
    image_b64: Optional[str],
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
        # Resize the init still to the snapped output dims so the i2v
        # conditioning frame matches the generated frame geometry.
        kwargs["image"] = _decode_image_b64(image_b64).resize((width, height))

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
