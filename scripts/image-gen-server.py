"""
Image Generation Server — DB-driven, graceful-degradation HTTP service.

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
import sys
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

def _resolve_db_url() -> str:
    """Resolve the brain DSN and force IPv4 (mirrors scripts/gpu-scraper.py, #1796).

    Order: ``POINDEXTER_BRAIN_URL`` → ``GLADLABS_BRAIN_URL`` → bootstrap.toml
    (canonical, #198) → local default. IPv4 because on Windows ``localhost``
    resolves to ``::1`` first and Docker Desktop's IPv6 port-proxy silently
    drops connections — this host process talks to the local postgres.
    """
    for env_key in ("POINDEXTER_BRAIN_URL", "GLADLABS_BRAIN_URL", "DATABASE_URL"):
        val = os.getenv(env_key)
        if val:
            return val.replace("@localhost:", "@127.0.0.1:")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from brain.bootstrap import resolve_database_url  # type: ignore

        dsn = resolve_database_url()
    except Exception as exc:  # bootstrap is best-effort on the host
        print(f"[dsn] bootstrap resolution failed ({exc}); using default", file=sys.stderr)
        dsn = None
    default = "postgresql://poindexter:poindexter-brain-local@localhost:5433/poindexter_brain"
    return (dsn or default).replace("@localhost:", "@127.0.0.1:")


HOST_DB_URL = _resolve_db_url()
MODEL_SETTING_KEY = "image_generation_model"

IDLE_TIMEOUT = 60  # seconds — unload after idle so Ollama can use VRAM

# Self-heal watchdog bounds (see degraded_watchdog / next_retry_delay below).
# reload_config() latches `degraded` on any failure and is otherwise only
# re-run on an explicit POST /reload. On a host reboot Docker's restart policy
# brings image-gen-server + postgres-local up in PARALLEL (compose `depends_on` is
# honored only by `docker compose up`, NOT by restart-policy restarts), so image-gen server
# can read app_settings while Postgres is still in startup (57P03 "the database
# system is starting up") and then stay degraded forever. The watchdog turns
# that permanent latch into a few seconds of self-healing.
DEGRADED_POLL_MIN_SECONDS = 5    # floor — heal fast after a boot race
DEGRADED_POLL_MAX_SECONDS = 60   # ceiling — don't spam the DB/logs forever
HEALTHY_POLL_SECONDS = 30        # cadence when not degraded (cheap idle check)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("image-gen-server")
app = FastAPI(title="image-gen Server", version="2.0")


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
    lora_repo: str | None = None
    lora_weight_name: str | None = None
    scheduler_trailing: bool = False
    # pipeline_kind selects the diffusers pipeline class + call convention.
    # "sdxl"  -> StableDiffusionXLPipeline (fp16 variant, negative prompt, LoRA)
    # "zimage" -> ZImagePipeline (bf16, guidance-distilled: no negative prompt)
    pipeline_kind: str = "sdxl"
    torch_dtype: str = "float16"  # "float16" or "bfloat16"
    use_fp16_variant: bool = True  # Stable Diffusion XL repos ship an fp16 variant; Z-Image does not
    supports_negative_prompt: bool = True
    notes: str = ""


REGISTRY: Dict[str, ModelConfig] = {
    "sdxl_lightning": ModelConfig(
        friendly_name="sdxl_lightning",
        display_name="Stable Diffusion XL Lightning (4-step LoRA)",
        model_id="stabilityai/stable-diffusion-xl-base-1.0",
        default_steps=4,
        default_guidance_scale=0.0,
        lora_repo="ByteDance/SDXL-Lightning",
        lora_weight_name="sdxl_lightning_4step_lora.safetensors",
        scheduler_trailing=True,
        notes="Distilled 4-step LoRA on top of Stable Diffusion XL base. Requires guidance_scale=0.",
    ),
    "sdxl_turbo": ModelConfig(
        friendly_name="sdxl_turbo",
        display_name="Stable Diffusion XL Turbo",
        model_id="stabilityai/sdxl-turbo",
        default_steps=4,
        default_guidance_scale=0.0,
        notes="Single-pass turbo distillation. Lower quality than Lightning.",
    ),
    "sdxl_base": ModelConfig(
        friendly_name="sdxl_base",
        display_name="Stable Diffusion XL Base 1.0",
        model_id="stabilityai/stable-diffusion-xl-base-1.0",
        default_steps=30,
        default_guidance_scale=7.5,
        notes="Original Stable Diffusion XL — high quality, slower.",
    ),
    "z_image_turbo": ModelConfig(
        friendly_name="z_image_turbo",
        display_name="Z-Image-Turbo (Tongyi-MAI, 6B)",
        model_id="Tongyi-MAI/Z-Image-Turbo",
        default_steps=9,
        default_guidance_scale=0.0,
        pipeline_kind="zimage",
        torch_dtype="bfloat16",
        use_fp16_variant=False,
        supports_negative_prompt=False,
        notes=(
            "Apache-2.0 6B guidance-distilled turbo. Runs at 9 steps / "
            "guidance_scale=0 / bf16, no negative prompt. ~13GB VRAM. "
            "Bake-off winner 2026-06-19: sharper than Lightning, less garbled "
            "text, ~3x fewer steps than DreamShaper."
        ),
    ),
}


# ============================================================================
# SERVER STATE
# ============================================================================

class ServerState:
    def __init__(self):
        self.pipeline: Any | None = None
        self.config: ModelConfig | None = None
        self.last_used: float = 0.0
        self.degraded: bool = False
        self.degraded_reason: str | None = None

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

async def read_model_setting() -> str | None:
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
    lazy-loads the new model. Enters degraded state on any failure.

    DB-unreachable (exception) vs bad-config (wrong model name) are handled
    differently (stack#1152): a transient DB failure preserves the current
    config + pipeline — the model didn't change, only the DB went away.
    Clearing them on a 30-second Postgres restart caused a 2-minute model reload delay after recovery (the Postgres-restart cascade).
    Bad config (unknown model, setting removed) still unloads because we
    have definitive information the current config is wrong.
    """
    try:
        friendly = await read_model_setting()
    except Exception as e:
        # DB is temporarily unreachable. Mark degraded so /generate returns
        # 503, but preserve state.config + pipeline — the model hasn't
        # changed. When the DB recovers the pipeline is immediately available
        # without a model reload. See stack#1152.
        state.mark_degraded(f"DB read failed for {MODEL_SETTING_KEY!r}: {e}")
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
    """Best-effort: ask Ollama to free VRAM before we load the model.
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
    _ask_ollama_to_unload()
    dtype = torch.bfloat16 if config.torch_dtype == "bfloat16" else torch.float16
    logger.info(
        "Loading %s (%s, kind=%s, %s) on %s (%d MB VRAM)",
        config.display_name, config.model_id, config.pipeline_kind,
        config.torch_dtype,
        torch.cuda.get_device_name(0),
        torch.cuda.get_device_properties(0).total_memory // 1024 // 1024,
    )

    if config.pipeline_kind == "zimage":
        # Z-Image-Turbo: a 6B guidance-distilled model with its own pipeline
        # class. low_cpu_mem_usage defaults to True so the checkpoint streams
        # onto the meta-device target (~13GB peak) instead of materializing the
        # full model in CPU RAM (~24GB, which OOM-kills the container). No fp16
        # variant, no LoRA, no scheduler override, no attention-slicing knobs.
        from diffusers import ZImagePipeline

        pipe = ZImagePipeline.from_pretrained(config.model_id, torch_dtype=dtype)
        pipe = pipe.to("cuda")
        logger.info("%s ready", config.display_name)
        return pipe

    from diffusers import EulerDiscreteScheduler, StableDiffusionXLPipeline

    from_kwargs: dict[str, Any] = {"torch_dtype": dtype, "use_safetensors": True}
    if config.use_fp16_variant:
        from_kwargs["variant"] = "fp16"
    pipe = StableDiffusionXLPipeline.from_pretrained(config.model_id, **from_kwargs)

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
    steps: int | None = Field(default=None, ge=1, le=50)
    guidance_scale: float | None = Field(default=None, ge=0, le=20)
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
# SELF-HEAL WATCHDOG
# ============================================================================

def next_retry_delay(attempt: int) -> float:
    """Seconds the watchdog waits before its next poll of reload_config().

    `attempt` is the count of consecutive degraded polls so far:
      * attempt == 0  -> server is healthy; idle keep-alive cadence
      * attempt >= 1  -> server is degraded; the Nth recovery retry

    >>> CONTRIBUTION POINT — implement the cadence/backoff policy here. <<<

    Contract pinned by tests/unit/scripts/test_image_gen_self_heal.py:
      * always return a positive float
      * a degraded poll heals at least as fast as the idle cadence:
        next_retry_delay(1) <= next_retry_delay(0)

    Levers to weigh: heal latency after a boot race (Postgres is ready within
    seconds) vs. opening a DB connection every poll; and that a *permanent*
    misconfig (unknown model name) will keep retrying — pick a cadence that
    recovers promptly without spamming the DB/logs for hours.

    Bounds available: DEGRADED_POLL_MIN_SECONDS / DEGRADED_POLL_MAX_SECONDS /
    HEALTHY_POLL_SECONDS.

    Policy: exponential backoff while degraded — heals within ~5s after a boot
    race, then slows toward the 60s cap if a permanent misconfig keeps it
    degraded so we don't hammer the DB or spam the logs for hours.
    """
    if attempt <= 0:
        return float(HEALTHY_POLL_SECONDS)
    delay = DEGRADED_POLL_MIN_SECONDS * (2 ** (attempt - 1))
    return float(min(delay, DEGRADED_POLL_MAX_SECONDS))


async def degraded_watchdog() -> None:
    """Background loop: while the server is degraded, re-run reload_config()
    so a transient boot-time failure recovers on its own instead of latching
    until the next manual /reload. Runs for the life of the process; mirrors
    the idle_unloader() create_task pattern in startup()."""
    attempt = 0
    while True:
        if state.degraded:
            attempt += 1
            logger.info(
                "[WATCHDOG] degraded (attempt %d): %s — retrying reload_config()",
                attempt, state.degraded_reason,
            )
            await reload_config()
            if not state.degraded:
                logger.info("[WATCHDOG] recovered after %d attempt(s)", attempt)
                attempt = 0
        else:
            attempt = 0
        await asyncio.sleep(next_retry_delay(attempt))


# ============================================================================
# LIFECYCLE
# ============================================================================

@app.on_event("startup")
async def startup():
    logger.info("image-gen server starting — DB: %s", HOST_DB_URL.split("@")[-1])
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
    asyncio.create_task(degraded_watchdog())


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
            detail=f"image-gen server degraded: {state.degraded_reason}",
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
    elif config.friendly_name == "z_image_turbo":
        # Guidance-distilled: CFG>0 reintroduces the artifacts distillation
        # removed. Clamp to 0 regardless of what the caller sent.
        if guidance_scale != 0.0:
            logger.info("Clamping guidance_scale %.2f -> 0 for z_image_turbo",
                        guidance_scale)
            guidance_scale = 0.0

    seed = req.seed if req.seed >= 0 else int(torch.randint(0, 2**32, (1,)).item())
    generator = torch.Generator(device="cuda").manual_seed(seed)
    filename = f"img_{uuid.uuid4().hex[:8]}.png"
    output_path = OUTPUT_DIR / filename

    logger.info(
        "Generating: %s... (%dx%d, %d steps, cfg=%.1f, model=%s)",
        req.prompt[:60], req.width, req.height, steps, guidance_scale,
        config.friendly_name,
    )
    start = time.time()
    try:
        gen_kwargs: Dict[str, Any] = dict(
            prompt=req.prompt,
            width=req.width,
            height=req.height,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            generator=generator,
        )
        # Guidance-distilled models (Z-Image) run at CFG 0, where a negative
        # prompt has no effect and the pipeline doesn't accept the kwarg.
        if config.supports_negative_prompt:
            gen_kwargs["negative_prompt"] = req.negative_prompt
        result = pipe(**gen_kwargs)
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
    logger.info("image-gen server listening on %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
