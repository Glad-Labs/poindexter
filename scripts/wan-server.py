"""Wan 2.1 1.3B Text-to-Video inference server — sidecar mate of sdxl-server.

Loads ``Wan-AI/Wan2.1-T2V-1.3B-Diffusers`` lazily on first request and
exposes a single ``/generate`` endpoint that the
:class:`Wan21Provider <services.video_providers.wan2_1.Wan21Provider>`
plugin POSTs to. Mirrors the request/response shape the SDXL sidecar
uses so one operator runbook covers both:

- POST ``/generate`` with JSON body matching the provider's request
  schema (prompt, negative_prompt, steps, guidance_scale, duration_s,
  width, height, fps).
- Returns either ``video/mp4`` raw bytes (preferred) or
  ``application/json`` with ``{"video_path": "<path>"}`` so the worker
  can fetch via shared filesystem.

Why a sidecar, not in-process: Wan 2.1 1.3B is a full-precision
diffusion model (50 steps default, ~30-60s/clip on a 5090). Loading
it inside the worker container would compete with Ollama / SDXL for
VRAM and serialize requests through the worker's event loop. A
dedicated server with its own GPU lock + idle-timeout unload mirrors
how every other GPU-bound model lives on this host.

Endpoints:
    GET  /health    — status, model, VRAM, degradation reason
    POST /generate  — generate video clip from prompt
    POST /unload    — free VRAM (called by GPU scheduler)

Failure model: matches sdxl-server. Anything wrong (model load fails,
CUDA OOM, etc.) puts the server in DEGRADED state — /generate returns
503 with a useful error string, /health reports the reason, server
keeps running so it can recover.
"""

import argparse
import asyncio
import gc
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

# Default model — matches the Wan21Provider's name="wan2.1-1.3b".
# Operators can override via WAN_MODEL_ID env (no DB roundtrip on this
# server, since model selection here is much narrower than SDXL's
# multi-model registry).
MODEL_ID = os.getenv(
    "WAN_MODEL_ID", "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
)

# Idle unload — Wan model is ~5GB; release it so SDXL / Ollama can
# reclaim VRAM when no video work is queued.
IDLE_TIMEOUT_S = int(os.getenv("WAN_IDLE_TIMEOUT_S", "120"))

# Default native dimensions / framerate per Wan 2.1 1.3B card. Callers
# override per-request, but the server enforces a sane upper bound on
# total frames so a runaway request can't OOM the GPU.
_MAX_FRAMES = 240  # 15s at 16fps; matches the model's documented cap

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("wan-server")
app = FastAPI(title="Wan 2.1 T2V Server", version="1.0")


class ServerState:
    """Mutable singleton state — pipeline cache, idle clock, degraded flag."""

    def __init__(self) -> None:
        self.pipeline: Optional[Any] = None
        self.last_used: float = 0.0
        self.degraded: bool = False
        self.degraded_reason: Optional[str] = None
        # Single GPU lock — concurrent /generate calls would compete
        # for VRAM and produce torch CUDA OOMs. Serialize at the
        # server level rather than relying on every caller to.
        self.gpu_lock = asyncio.Lock()


state = ServerState()


# ============================================================================
# PIPELINE LOAD / UNLOAD
# ============================================================================


def _load_pipeline_blocking() -> Any:
    """Synchronously load WanPipeline; runs in a worker thread.

    Raises on any failure so the caller can flip the server into
    DEGRADED state. Returns the loaded pipeline on success.
    """
    from diffusers import WanPipeline

    logger.info("Loading WanPipeline from %s", MODEL_ID)
    pipe = WanPipeline.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.bfloat16,
    )
    pipe = pipe.to("cuda")
    # Reduces peak VRAM at ~5% throughput cost — easily worth it on a
    # 32GB card sharing with SDXL.
    try:
        pipe.enable_attention_slicing()
    except Exception:
        pass
    logger.info("Wan 2.1 1.3B ready on %s", torch.cuda.get_device_name(0))
    return pipe


async def _ensure_pipeline_loaded() -> Any:
    """Lazy-load the pipeline. Caller must hold the GPU lock."""
    if state.pipeline is not None:
        return state.pipeline
    try:
        state.pipeline = await asyncio.to_thread(_load_pipeline_blocking)
        state.degraded = False
        state.degraded_reason = None
    except Exception as exc:
        state.degraded = True
        state.degraded_reason = f"{type(exc).__name__}: {exc}"
        logger.exception("WanPipeline load failed")
        raise
    return state.pipeline


def _unload_pipeline_blocking() -> None:
    """Release VRAM. Must hold the GPU lock at call time."""
    if state.pipeline is None:
        return
    logger.info("Unloading WanPipeline to free VRAM")
    del state.pipeline
    state.pipeline = None
    torch.cuda.empty_cache()
    gc.collect()
    logger.info(
        "VRAM in use: %d MB",
        torch.cuda.memory_allocated(0) // 1024 // 1024,
    )


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
        "model": "wan2.1-1.3b",
        "model_display_name": "Wan 2.1 T2V 1.3B",
        "model_id": MODEL_ID,
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
    duration_s: int,
    width: int,
    height: int,
    fps: int,
    output_path: str,
) -> tuple[float, int]:
    """Run the diffusion pass + export to MP4. Synchronous; called
    inside ``asyncio.to_thread`` so the FastAPI event loop stays free.

    Returns ``(elapsed_s, frame_count)``.
    """
    from diffusers.utils import export_to_video

    num_frames = max(1, min(_MAX_FRAMES, duration_s * fps))
    started = time.perf_counter()
    output = pipeline(
        prompt=prompt,
        negative_prompt=negative_prompt,
        height=height,
        width=width,
        num_frames=num_frames,
        num_inference_steps=steps,
        guidance_scale=guidance_scale,
    )
    frames = output.frames[0]  # WanPipeline yields list-of-list per batch
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
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"pipeline load failed: {exc}",
            ) from exc

        try:
            elapsed_s, num_frames = await asyncio.to_thread(
                _generate_blocking,
                pipeline,
                prompt=req.prompt,
                negative_prompt=req.negative_prompt,
                steps=req.steps,
                guidance_scale=req.guidance_scale,
                duration_s=req.duration_s,
                width=req.width,
                height=req.height,
                fps=req.fps,
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

    logger.info(
        "[wan] generated %dpx×%dpx, %d frames in %.1fs: %s",
        req.width, req.height, num_frames, elapsed_s, output_filename,
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
            "X-Width": str(req.width),
            "X-Height": str(req.height),
            "X-Fps": str(req.fps),
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
