"""
SDXL Image Generation Server — DB-driven, graceful-degradation HTTP service.

Single host process, single GPU, multiple Docker callers reach it via
host.docker.internal:9836. The choice of model lives in poindexter_brain
(app_settings.image_generation_model) so it can be changed in one place
without touching env vars or restarting code.

Failure model: if anything goes wrong (DB unreachable, unknown model name,
model load failure) the server enters DEGRADED state — /generate returns 503,
/health reports the reason, and the server keeps running so it can recover
when the underlying issue is fixed (e.g., DB comes back, setting is corrected).
The pipeline never crashes the post pipeline; callers fall back to Pexels.

Endpoints:
    GET  /health              — status, model, degradation reason
    POST /generate            — generate image from prompt
    POST /reload              — re-read DB config (call after changing setting)
    POST /unload              — free VRAM (called by GPU scheduler)
    GET  /images/{filename}   — serve generated image
"""
import argparse
import asyncio
import gc
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import asyncpg
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

OUTPUT_DIR = Path(os.path.expanduser("~")) / ".poindexter" / "generated-images"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HOST_DB_URL = os.getenv(
    "POINDEXTER_BRAIN_URL",
    os.getenv(
        "GLADLABS_BRAIN_URL",
        "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain",
    ),
)
MODEL_SETTING_KEY = "image_generation_model"

IDLE_TIMEOUT = 60  # seconds — unload after idle so Ollama can use VRAM

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sdxl-server")
app = FastAPI(title="SDXL Image Server", version="2.0")


# ============================================================================
# MODEL REGISTRY
# Mirrors src/cofounder_agent/services/image_service.py IMAGE_MODEL_REGISTRY.
# Keep these in sync — both reference the same friendly names.
# ============================================================================

@dataclass(frozen=True)
class ModelConfig:
    friendly_name: str
    display_name: str
    model_id: str
    default_steps: int
    default_guidance_scale: float
    lora_repo: Optional[str] = None
    lora_weight_name: Optional[str] = None
    scheduler_trailing: bool = False
    notes: str = ""


REGISTRY: Dict[str, ModelConfig] = {
    "sdxl_lightning": ModelConfig(
        friendly_name="sdxl_lightning",
        display_name="SDXL Lightning (4-step LoRA)",
        model_id="stabilityai/stable-diffusion-xl-base-1.0",
        default_steps=4,
        default_guidance_scale=0.0,
        lora_repo="ByteDance/SDXL-Lightning",
        lora_weight_name="sdxl_lightning_4step_lora.safetensors",
        scheduler_trailing=True,
        notes="Distilled 4-step LoRA on top of SDXL base. Requires guidance_scale=0.",
    ),
    "sdxl_turbo": ModelConfig(
        friendly_name="sdxl_turbo",
        display_name="SDXL Turbo",
        model_id="stabilityai/sdxl-turbo",
        default_steps=4,
        default_guidance_scale=0.0,
        notes="Single-pass turbo distillation. Lower quality than Lightning.",
    ),
    "sdxl_base": ModelConfig(
        friendly_name="sdxl_base",
        display_name="SDXL Base 1.0",
        model_id="stabilityai/stable-diffusion-xl-base-1.0",
        default_steps=30,
        default_guidance_scale=7.5,
        notes="Original SDXL — high quality, slower.",
    ),
}


# ============================================================================
# SERVER STATE
# ============================================================================

class ServerState:
    def __init__(self):
        self.pipeline: Optional[Any] = None
        self.config: Optional[ModelConfig] = None
        self.last_used: float = 0.0
        self.degraded: bool = False
        self.degraded_reason: Optional[str] = None

    def mark_degraded(self, reason: str):
        self.degraded = True
        self.degraded_reason = reason
        logger.error("[DEGRADED] %s", reason)

    def mark_healthy(self):
        if self.degraded:
            logger.info("[RECOVERED] previous reason: %s", self.degraded_reason)
        self.degraded = False
        self.degraded_reason = None


state = ServerState()


# ============================================================================
# CONFIG LOADING
# ============================================================================

async def read_model_setting() -> Optional[str]:
    """Read the configured model friendly name from app_settings.

    Returns None if the row exists but is empty. Raises on connection failure.
    """
    conn = await asyncpg.connect(HOST_DB_URL, timeout=5)
    try:
        row = await conn.fetchrow(
            "SELECT value FROM app_settings WHERE key = $1", MODEL_SETTING_KEY
        )
    finally:
        await conn.close()
    if row is None or not row["value"]:
        return None
    return row["value"].strip()


async def reload_config() -> None:
    """Read DB, resolve config, drop active pipeline so the next request
    lazy-loads the new model. Enters degraded state on any failure."""
    try:
        friendly = await read_model_setting()
    except Exception as e:
        state.mark_degraded(f"DB read failed for {MODEL_SETTING_KEY!r}: {e}")
        state.config = None
        unload_pipeline()
        return

    if friendly is None:
        state.mark_degraded(
            f"setting {MODEL_SETTING_KEY!r} not set in app_settings — "
            f"image generation disabled until configured"
        )
        state.config = None
        unload_pipeline()
        return

    config = REGISTRY.get(friendly)
    if config is None:
        state.mark_degraded(
            f"unknown image model {friendly!r} — known: {sorted(REGISTRY.keys())}"
        )
        state.config = None
        unload_pipeline()
        return

    if state.config is not None and state.config.friendly_name == config.friendly_name:
        logger.info("Config unchanged: %s", config.friendly_name)
        state.mark_healthy()
        return

    logger.info("Config: %s -> %s",
                state.config.friendly_name if state.config else "<none>",
                config.friendly_name)
    state.config = config
    unload_pipeline()
    state.mark_healthy()


# ============================================================================
# PIPELINE LOADING
# ============================================================================

def _ask_ollama_to_unload() -> None:
    """Best-effort: ask Ollama to free VRAM before we load SDXL.
    Ignored if Ollama isn't reachable."""
    try:
        import httpx
        httpx.post(
            "http://localhost:11434/api/generate",
            json={"model": "", "keep_alive": 0},
            timeout=5,
        )
        logger.info("Asked Ollama to unload models")
    except Exception:
        pass


def load_pipeline(config: ModelConfig):
    """Build a diffusers pipeline for the given config. Raises on failure."""
    from diffusers import EulerDiscreteScheduler, StableDiffusionXLPipeline

    _ask_ollama_to_unload()
    logger.info(
        "Loading %s (%s) on %s (%d MB VRAM)",
        config.display_name, config.model_id,
        torch.cuda.get_device_name(0),
        torch.cuda.get_device_properties(0).total_memory // 1024 // 1024,
    )

    pipe = StableDiffusionXLPipeline.from_pretrained(
        config.model_id,
        torch_dtype=torch.float16,
        variant="fp16",
        use_safetensors=True,
    )

    if config.lora_repo:
        logger.info("Loading LoRA from %s (%s)",
                    config.lora_repo, config.lora_weight_name)
        pipe.load_lora_weights(
            config.lora_repo, weight_name=config.lora_weight_name
        )
        pipe.fuse_lora()
        logger.info("LoRA fused")

    if config.scheduler_trailing:
        logger.info("Switching scheduler -> EulerDiscreteScheduler (trailing)")
        pipe.scheduler = EulerDiscreteScheduler.from_config(
            pipe.scheduler.config, timestep_spacing="trailing"
        )

    pipe = pipe.to("cuda")
    pipe.enable_attention_slicing()
    try:
        pipe.enable_xformers_memory_efficient_attention()
    except Exception:
        pass

    logger.info("%s ready", config.display_name)
    return pipe


def unload_pipeline() -> None:
    if state.pipeline is None:
        return
    name = state.config.display_name if state.config else "?"
    logger.info("Unloading %s to free VRAM", name)
    del state.pipeline
    state.pipeline = None
    torch.cuda.empty_cache()
    gc.collect()
    logger.info("VRAM in use: %d MB",
                torch.cuda.memory_allocated(0) // 1024 // 1024)


def ensure_pipeline_loaded():
    """Lazy-load configured pipeline. Raises if no config or load fails."""
    if state.pipeline is not None:
        return state.pipeline
    if state.config is None:
        raise RuntimeError("no model configured (see /health for reason)")
    state.pipeline = load_pipeline(state.config)
    state.last_used = time.time()
    return state.pipeline


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = Field(
        default="text, words, letters, watermark, face, person, hands, "
                "blurry, low quality, deformed"
    )
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    steps: Optional[int] = Field(default=None, ge=1, le=50)
    guidance_scale: Optional[float] = Field(default=None, ge=0, le=20)
    seed: int = Field(default=-1)


class GenerateResponse(BaseModel):
    image_path: str
    filename: str
    width: int
    height: int
    model: str
    generation_time_ms: int
    seed: int


# ============================================================================
# LIFECYCLE
# ============================================================================

@app.on_event("startup")
async def startup():
    logger.info("SDXL server starting — DB: %s", HOST_DB_URL.split("@")[-1])
    await reload_config()
    if state.degraded:
        logger.warning(
            "Started DEGRADED: %s. /generate returns 503 until /reload succeeds.",
            state.degraded_reason,
        )
    else:
        logger.info("Configured: %s. Pipeline lazy-loads on first /generate.",
                    state.config.friendly_name)

    async def idle_unloader():
        while True:
            await asyncio.sleep(60)
            if state.pipeline is not None and (time.time() - state.last_used) > IDLE_TIMEOUT:
                unload_pipeline()
    asyncio.create_task(idle_unloader())


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/health")
async def health():
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
        "model": state.config.friendly_name if state.config else None,
        "model_display_name": state.config.display_name if state.config else None,
        "model_id": state.config.model_id if state.config else None,
        "gpu": torch.cuda.get_device_name(0) if gpu_ok else None,
        "vram_total_mb": (
            torch.cuda.get_device_properties(0).total_memory // 1024 // 1024
            if gpu_ok else 0
        ),
        "gpu_available": gpu_ok,
    }


@app.post("/reload")
async def reload():
    """Re-read DB config. Call after changing image_generation_model."""
    await reload_config()
    return await health()


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    if state.degraded:
        raise HTTPException(
            status_code=503,
            detail=f"SDXL server degraded: {state.degraded_reason}",
        )

    state.last_used = time.time()
    try:
        pipe = ensure_pipeline_loaded()
    except Exception as e:
        state.mark_degraded(f"pipeline load failed: {e}")
        raise HTTPException(status_code=503, detail=str(e))

    config = state.config
    steps = req.steps if req.steps is not None else config.default_steps
    guidance_scale = (
        req.guidance_scale if req.guidance_scale is not None
        else config.default_guidance_scale
    )

    # Distilled models REQUIRE the model-recommended values. Clamp aggressively
    # rather than honor caller mistakes — silent garbage is how we ended up
    # here in the first place (Turbo+steps=8+cfg=2 produced bad images for weeks).
    if config.friendly_name == "sdxl_lightning":
        if steps != 4:
            logger.info("Clamping steps %d -> 4 for sdxl_lightning", steps)
            steps = 4
        if guidance_scale != 0.0:
            logger.info("Clamping guidance_scale %.2f -> 0 for sdxl_lightning",
                        guidance_scale)
            guidance_scale = 0.0
    elif config.friendly_name == "sdxl_turbo":
        if guidance_scale > 1.0:
            logger.info("Clamping guidance_scale %.2f -> 0 for sdxl_turbo",
                        guidance_scale)
            guidance_scale = 0.0

    seed = req.seed if req.seed >= 0 else int(torch.randint(0, 2**32, (1,)).item())
    generator = torch.Generator(device="cuda").manual_seed(seed)
    filename = f"sdxl_{uuid.uuid4().hex[:8]}.png"
    output_path = OUTPUT_DIR / filename

    logger.info(
        "Generating: %s... (%dx%d, %d steps, cfg=%.1f, model=%s)",
        req.prompt[:60], req.width, req.height, steps, guidance_scale,
        config.friendly_name,
    )
    start = time.time()
    try:
        result = pipe(
            prompt=req.prompt,
            negative_prompt=req.negative_prompt,
            width=req.width,
            height=req.height,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            generator=generator,
        )
        result.images[0].save(str(output_path))
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        raise HTTPException(status_code=503, detail="GPU OOM")
    except Exception as e:
        logger.error("Generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    elapsed_ms = int((time.time() - start) * 1000)
    logger.info("Generated: %s (%d ms)", filename, elapsed_ms)
    return GenerateResponse(
        image_path=str(output_path),
        filename=filename,
        width=req.width,
        height=req.height,
        model=config.friendly_name,
        generation_time_ms=elapsed_ms,
        seed=seed,
    )


@app.post("/unload")
async def unload():
    """Explicit VRAM free, called by the GPU scheduler when switching to Ollama."""
    if state.pipeline is None:
        return {"status": "already_unloaded"}
    unload_pipeline()
    return {
        "status": "unloaded",
        "vram_used_mb": torch.cuda.memory_allocated(0) // 1024 // 1024,
    }


@app.get("/images/{filename}")
async def get_image(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(path), media_type="image/png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9836)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    logger.info("SDXL server listening on %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
