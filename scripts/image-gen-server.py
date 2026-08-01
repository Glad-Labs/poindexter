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

OCR text-leakage gate (2026-07-13): guidance-distilled models (Z-Image-Turbo)
run at guidance_scale=0, where negative_prompt has no effect — the 2026-07
bake-off (glad-labs-stack#2386) measured z_image_turbo leaking ~57x more
readable text into images than the best-scoring alternative even with a
textless-composition clause in the positive prompt. Since Matt's call was to
keep z_image_turbo for its aesthetic (chroma1_flash reads as more obviously
AI-generated), /generate now OCR-scans every render and retries with a fresh
seed (bounded, keep-best) when leaked text exceeds a threshold — deterministic
check-and-retry, not a model swap. Tunable via app_settings (image_ocr_gate_*);
see settings_defaults.py.

Endpoints:
    GET  /health              — status, model, degradation reason, gate config
    POST /generate            — generate image from prompt (OCR-gated, see above)
    POST /reload              — re-read DB config (call after changing setting)
    POST /unload              — free VRAM (called by GPU scheduler)
    GET  /images/{filename}   — serve generated image
"""
import argparse
import asyncio
import gc
import json
import logging
import os
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    # Pinned commit SHA for model_id. An unpinned load resolves to whatever the
    # repo's `main` points at on the day of a cold start, so the weights behind
    # a "known-good" deploy can change with no commit here. Every SHA below was
    # verified against BOTH the local HF cache snapshot and upstream `main` at
    # pin time (2026-07-17), so pinning re-downloads nothing and changes no
    # behaviour — it only stops future drift. A LoRA is a separate repo and gets
    # its own pin. Both are required: see
    # tests/unit/scripts/test_hf_revision_pinning.py (bandit's B615 does not
    # cover diffusers, so it cannot catch a missing pin here).
    revision: str | None = None
    lora_repo: str | None = None
    lora_weight_name: str | None = None
    lora_revision: str | None = None
    scheduler_trailing: bool = False
    # pipeline_kind selects the diffusers pipeline class + call convention.
    # "sdxl"  -> StableDiffusionXLPipeline (fp16 variant, negative prompt, LoRA)
    # "zimage" -> ZImagePipeline (bf16, guidance-distilled: no negative prompt)
    pipeline_kind: str = "sdxl"
    torch_dtype: str = "float16"  # "float16" or "bfloat16"
    use_fp16_variant: bool = True  # Stable Diffusion XL repos ship an fp16 variant; Z-Image does not
    supports_negative_prompt: bool = True
    notes: str = ""


REGISTRY: dict[str, ModelConfig] = {
    "sdxl_lightning": ModelConfig(
        friendly_name="sdxl_lightning",
        display_name="Stable Diffusion XL Lightning (4-step LoRA)",
        model_id="stabilityai/stable-diffusion-xl-base-1.0",
        revision="462165984030d82259a11f4367a4eed129e94a7b",
        default_steps=4,
        default_guidance_scale=0.0,
        lora_repo="ByteDance/SDXL-Lightning",
        lora_weight_name="sdxl_lightning_4step_lora.safetensors",
        lora_revision="c9a24f48e1c025556787b0c58dd67a091ece2e44",
        scheduler_trailing=True,
        notes="Distilled 4-step LoRA on top of Stable Diffusion XL base. Requires guidance_scale=0.",
    ),
    "sdxl_turbo": ModelConfig(
        friendly_name="sdxl_turbo",
        display_name="Stable Diffusion XL Turbo",
        model_id="stabilityai/sdxl-turbo",
        # Not present in this host's HF cache (config has never been selected),
        # so this SHA is upstream `main` at pin time rather than a
        # cache-verified running revision.
        revision="71153311d3dbb46851df1931d3ca6e939de83304",
        default_steps=4,
        default_guidance_scale=0.0,
        notes="Single-pass turbo distillation. Lower quality than Lightning.",
    ),
    "sdxl_base": ModelConfig(
        friendly_name="sdxl_base",
        display_name="Stable Diffusion XL Base 1.0",
        model_id="stabilityai/stable-diffusion-xl-base-1.0",
        revision="462165984030d82259a11f4367a4eed129e94a7b",
        default_steps=30,
        default_guidance_scale=7.5,
        notes="Original Stable Diffusion XL — high quality, slower.",
    ),
    "z_image_turbo": ModelConfig(
        friendly_name="z_image_turbo",
        display_name="Z-Image-Turbo (Tongyi-MAI, 6B)",
        model_id="Tongyi-MAI/Z-Image-Turbo",
        revision="f332072aa78be7aecdf3ee76d5c247082da564a6",
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
# OCR TEXT-LEAKAGE GATE
# Deterministic check-and-retry, not a model swap — see module docstring.
# ============================================================================

@dataclass(frozen=True)
class OcrGateConfig:
    enabled: bool = True
    max_chars: int = 6
    max_attempts: int = 3
    min_confidence: float = 0.3


DEFAULT_OCR_GATE_CONFIG = OcrGateConfig()

OCR_GATE_SETTING_KEYS = (
    "image_ocr_gate_enabled",
    "image_ocr_gate_max_chars",
    "image_ocr_gate_max_attempts",
    "image_ocr_gate_min_confidence",
)


@dataclass
class GenAttempt:
    seed: int
    text_chars: int
    path: Path


def pick_best_attempt(attempts: list[GenAttempt]) -> GenAttempt:
    """Lowest leaked-text-char count wins; earliest attempt breaks ties.

    ``min()`` is stable — the first element with the minimum key wins on a
    tie, so the caller's requested/derived seed (attempt 1) is preferred
    over a later re-roll when both score identically (usually both zero).
    """
    return min(attempts, key=lambda a: a.text_chars)


def count_leaked_text_chars(image_path: Path | str, reader: Any, *, min_confidence: float) -> int:
    """Sum character counts of confident OCR text detections in one image.

    Same methodology as ``scripts/image_bakeoff.py::count_text_chars``, so
    the numbers this gate logs stay comparable to the 2026-07 bake-off
    results (chroma1_flash 0.44, z_image_turbo 25.33 avg chars/image).
    Synchronous/CPU-bound — callers must run it off the event loop
    (``asyncio.to_thread``).
    """
    detections = reader.readtext(str(image_path), detail=1)
    return sum(len(text) for _box, text, conf in detections if conf >= min_confidence)


def _parse_ocr_gate_config(rows: dict[str, str]) -> OcrGateConfig:
    """Parse the OCR-gate app_settings rows, falling back per-field on any
    missing/malformed value so one bad setting degrades that field only,
    never the whole gate."""

    def _bool(key: str, default: bool) -> bool:
        raw = rows.get(key)
        if not raw:
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on")

    def _int(key: str, default: int) -> int:
        raw = rows.get(key)
        try:
            return int(raw) if raw else default
        except (TypeError, ValueError):
            return default

    def _float(key: str, default: float) -> float:
        raw = rows.get(key)
        try:
            return float(raw) if raw else default
        except (TypeError, ValueError):
            return default

    return OcrGateConfig(
        enabled=_bool("image_ocr_gate_enabled", DEFAULT_OCR_GATE_CONFIG.enabled),
        max_chars=max(0, _int("image_ocr_gate_max_chars", DEFAULT_OCR_GATE_CONFIG.max_chars)),
        max_attempts=max(
            1, _int("image_ocr_gate_max_attempts", DEFAULT_OCR_GATE_CONFIG.max_attempts),
        ),
        min_confidence=max(
            0.0, min(1.0, _float(
                "image_ocr_gate_min_confidence", DEFAULT_OCR_GATE_CONFIG.min_confidence,
            )),
        ),
    )


async def read_ocr_gate_settings() -> dict[str, str]:
    """Best-effort read of the OCR-gate app_settings rows.

    Returns {} on any DB failure — reload_ocr_gate_config() then falls back
    to DEFAULT_OCR_GATE_CONFIG, so a transient DB hiccup degrades to "gate
    runs at defaults", never to "gate silently disabled" or a crash. Kept as
    its own connection (separate from read_model_setting()) rather than a
    combined query so the two settle independently and existing tests that
    patch read_model_setting in isolation keep working.
    """
    try:
        conn = await asyncpg.connect(HOST_DB_URL, timeout=5)
    except Exception as e:
        logger.warning("[OCR-GATE] settings read failed (DB connect): %s — using defaults", e)
        return {}
    try:
        rows = await conn.fetch(
            "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])",
            list(OCR_GATE_SETTING_KEYS),
        )
    except Exception as e:
        logger.warning("[OCR-GATE] settings read failed (query): %s — using defaults", e)
        return {}
    finally:
        await conn.close()
    return {r["key"]: r["value"] for r in rows if r["value"] is not None}


HARD_UNLOAD_MIN_RESERVED_MB_KEY = "image_gen_hard_unload_min_reserved_mb"
DEFAULT_HARD_UNLOAD_MIN_RESERVED_MB = 512


async def read_hard_unload_min_reserved_mb() -> int:
    """Reserved-VRAM floor below which a hard unload is not worth the exit.

    Best-effort, same posture as :func:`read_ocr_gate_settings`: any DB
    failure returns the default rather than raising, so a transient hiccup
    degrades to "threshold at default", never to a crash in the reclaim path.

    Read per call rather than cached at startup because the whole point of
    the endpoint is that it runs while VRAM pressure is changing — an
    operator retuning the floor should not have to restart the server.
    """
    try:
        conn = await asyncpg.connect(HOST_DB_URL, timeout=5)
    except Exception as e:
        logger.warning(
            "[HARD UNLOAD] threshold read failed (DB connect): %s — using %d MB",
            e, DEFAULT_HARD_UNLOAD_MIN_RESERVED_MB,
        )
        return DEFAULT_HARD_UNLOAD_MIN_RESERVED_MB
    try:
        raw = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            HARD_UNLOAD_MIN_RESERVED_MB_KEY,
        )
    except Exception as e:
        logger.warning(
            "[HARD UNLOAD] threshold read failed (query): %s — using %d MB",
            e, DEFAULT_HARD_UNLOAD_MIN_RESERVED_MB,
        )
        return DEFAULT_HARD_UNLOAD_MIN_RESERVED_MB
    finally:
        await conn.close()
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        # '' is the unset sentinel (app_settings.value is NOT NULL), so this
        # is the ordinary "key seeded but blank" path, not a bug.
        return DEFAULT_HARD_UNLOAD_MIN_RESERVED_MB


async def reload_ocr_gate_config() -> None:
    """Re-read + re-parse the OCR-gate settings into state.ocr_gate."""
    raw = await read_ocr_gate_settings()
    state.ocr_gate = _parse_ocr_gate_config(raw)
    logger.info(
        "[OCR-GATE] config: enabled=%s max_chars=%d max_attempts=%d min_confidence=%.2f",
        state.ocr_gate.enabled, state.ocr_gate.max_chars,
        state.ocr_gate.max_attempts, state.ocr_gate.min_confidence,
    )


_ocr_reader_lock = asyncio.Lock()


async def ensure_ocr_reader() -> Any:
    """Lazy-load the singleton EasyOCR reader.

    CPU-only (``gpu=False``) deliberately — VRAM here belongs to the
    diffusion pipeline, and this gate must never compete with it for GPU
    memory. A 1024x1024 scan is ~1-2s on CPU, acceptable next to the
    multi-second diffusion render it's checking. Model weights persist in
    the mounted ~/.EasyOCR cache dir (see docker-compose.local.yml), so this
    download only happens once across container restarts.
    """
    if state.ocr_reader is not None:
        return state.ocr_reader
    async with _ocr_reader_lock:
        if state.ocr_reader is None:
            import easyocr

            logger.info("[OCR-GATE] loading EasyOCR reader (first use)...")
            state.ocr_reader = await asyncio.to_thread(
                easyocr.Reader, ["en"], gpu=False, verbose=False,
            )
            logger.info("[OCR-GATE] EasyOCR reader ready")
    return state.ocr_reader


async def write_ocr_gate_audit_log(
    *, model: str, task_id: str | None, attempts: list[GenAttempt],
    best: GenAttempt, threshold: int, gate_passed: bool,
) -> None:
    """Best-effort audit_log row per gated generation.

    Powers a Pipeline-dashboard panel the same way multi_model_qa's
    qa_pass_completed rows power the QA Rails board (audit_log WHERE
    event_type=...). Never raises — a telemetry hiccup must not fail the
    image response the pipeline is waiting on.
    """
    try:
        conn = await asyncpg.connect(HOST_DB_URL, timeout=5)
        try:
            await conn.execute(
                "INSERT INTO audit_log (event_type, source, task_id, details, severity) "
                "VALUES ($1, $2, $3, $4::jsonb, $5)",
                "image_ocr_gate_result",
                "image_gen_server",
                str(task_id) if task_id else None,
                json.dumps({
                    "model": model,
                    "text_chars": best.text_chars,
                    "threshold": threshold,
                    "attempts": len(attempts),
                    "attempt_scores": [a.text_chars for a in attempts],
                    "passed": gate_passed,
                }),
                "info" if gate_passed else "warning",
            )
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("[OCR-GATE] audit_log write skipped: %s", e)


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
        self.ocr_gate: OcrGateConfig = DEFAULT_OCR_GATE_CONFIG
        self.ocr_reader: Any | None = None

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

        pipe = ZImagePipeline.from_pretrained(
            config.model_id, torch_dtype=dtype, revision=config.revision,
        )
        pipe = pipe.to("cuda")
        logger.info("%s ready", config.display_name)
        return pipe

    from diffusers import EulerDiscreteScheduler, StableDiffusionXLPipeline

    from_kwargs: dict[str, Any] = {"torch_dtype": dtype, "use_safetensors": True}
    if config.use_fp16_variant:
        from_kwargs["variant"] = "fp16"
    pipe = StableDiffusionXLPipeline.from_pretrained(
        config.model_id, revision=config.revision, **from_kwargs,
    )

    if config.lora_repo:
        logger.info("Loading LoRA from %s (%s, revision=%s)",
                    config.lora_repo, config.lora_weight_name, config.lora_revision)
        pipe.load_lora_weights(
            config.lora_repo, weight_name=config.lora_weight_name,
            revision=config.lora_revision,
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


async def _idle_unloader_tick() -> None:
    """One idle-unloader pass: unload the pipeline after IDLE_TIMEOUT of no
    generates, then refresh the OCR-gate settings (piggybacked on the same
    60s cadence). Factored out of the startup loop so the loop can wrap it
    in a blanket except — and so tests can drive a single pass directly."""
    if state.pipeline is not None and (time.time() - state.last_used) > IDLE_TIMEOUT:
        unload_pipeline()
    await reload_ocr_gate_config()


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
    task_id: str | None = Field(
        default=None,
        description="Originating pipeline_tasks id, threaded through to the "
                     "image_ocr_gate_result audit_log row for correlation.",
    )


class GenerateResponse(BaseModel):
    image_path: str
    filename: str
    width: int
    height: int
    model: str
    generation_time_ms: int
    seed: int
    ocr_text_chars: int = 0
    ocr_gate_attempts: int = 1
    ocr_gate_passed: bool = True


class UnloadRequest(BaseModel):
    hard: bool = False


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
        try:
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
        except Exception:
            # Same failure mode as the idle unloader: an exception escaping
            # this body kills self-heal for the life of the process. Log loud,
            # keep the cadence (attempt already advanced, so backoff applies).
            logger.exception("[WATCHDOG] poll failed; retrying on cadence")
        else:
            if not state.degraded:
                attempt = 0
        await asyncio.sleep(next_retry_delay(attempt))


# ============================================================================
# LIFECYCLE
# ============================================================================

@app.on_event("startup")
async def startup():
    logger.info("image-gen server starting — DB: %s", HOST_DB_URL.split("@")[-1])
    await reload_config()
    await reload_ocr_gate_config()
    if state.degraded:
        logger.warning(
            "Started DEGRADED: %s. /generate returns 503 until /reload succeeds.",
            state.degraded_reason,
        )
    else:
        logger.info("Configured: %s. Pipeline lazy-loads on first /generate.",
                    state.config.friendly_name)

    async def idle_unloader():
        # Exception-proof: one failed tick must not kill the loop. This loop
        # died silently on 2026-07-30 (a stray exception escaping the bare
        # body — the tick's DB read is one candidate) and the loaded pipeline
        # then squatted 19 GB through the night of 2026-07-31, OOMing every
        # Ollama load on the shared GPU until a manual restart.
        while True:
            await asyncio.sleep(60)
            try:
                await _idle_unloader_tick()
            except Exception:
                logger.exception("[IDLE] unloader tick failed; loop continues")
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
        "ocr_gate_enabled": state.ocr_gate.enabled,
        "ocr_gate_max_chars": state.ocr_gate.max_chars,
        "ocr_gate_max_attempts": state.ocr_gate.max_attempts,
    }


@app.post("/reload")
async def reload():
    """Re-read DB config. Call after changing image_generation_model or an
    image_ocr_gate_* setting."""
    await reload_config()
    await reload_ocr_gate_config()
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
        raise HTTPException(status_code=503, detail=str(e)) from e

    config = state.config
    # Explicit numeric coercion: these are request-controlled and get logged,
    # so pin them to int/float before any log line (CodeQL py/log-injection).
    steps = int(req.steps if req.steps is not None else config.default_steps)
    guidance_scale = float(
        req.guidance_scale if req.guidance_scale is not None
        else config.default_guidance_scale,
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

    base_seed = req.seed if req.seed >= 0 else int(torch.randint(0, 2**32, (1,)).item())
    gate = state.ocr_gate
    max_attempts = gate.max_attempts if gate.enabled else 1

    logger.info(
        "Generating: %s... (%dx%d, %d steps, cfg=%.1f, model=%s, ocr_gate=%s)",
        # Scrub newlines so a request prompt can't forge extra log lines.
        req.prompt[:60].replace("\r", " ").replace("\n", " "),
        int(req.width), int(req.height), steps, guidance_scale,
        config.friendly_name, "on" if gate.enabled else "off",
    )

    attempts: list[GenAttempt] = []
    start = time.time()
    for attempt_num in range(1, max_attempts + 1):
        # Re-roll the seed on every retry — a fresh seed samples a different
        # specific output, which is the whole point of the retry (the
        # positive-prompt "textless" clause alone doesn't move z_image_turbo's
        # leakage rate, see module docstring).
        seed = base_seed if attempt_num == 1 else int(torch.randint(0, 2**32, (1,)).item())
        generator = torch.Generator(device="cuda").manual_seed(seed)
        attempt_filename = f"img_{uuid.uuid4().hex[:8]}.png"
        attempt_path = OUTPUT_DIR / attempt_filename
        try:
            gen_kwargs: dict[str, Any] = dict(
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
            result.images[0].save(str(attempt_path))
        except torch.cuda.OutOfMemoryError as e:
            torch.cuda.empty_cache()
            if not attempts:
                # No usable image at all yet — this is a hard failure.
                raise HTTPException(status_code=503, detail="GPU OOM") from e
            # A retry is a bonus, not a requirement — keep the best attempt
            # already in hand rather than failing a request that already
            # produced a usable (if imperfect) image.
            logger.warning(
                "[OCR-GATE] retry attempt %d/%d hit GPU OOM — keeping best-so-far",
                attempt_num, max_attempts,
            )
            break
        except Exception as e:
            if not attempts:
                logger.error(
                    "Generation failed (attempt %d/%d): %s", attempt_num, max_attempts, e,
                    exc_info=True,
                )
                raise HTTPException(status_code=500, detail=str(e)) from e
            logger.warning(
                "[OCR-GATE] retry attempt %d/%d failed (%s) — keeping best-so-far",
                attempt_num, max_attempts, e,
            )
            break

        text_chars = 0
        if gate.enabled:
            try:
                reader = await ensure_ocr_reader()
                text_chars = await asyncio.to_thread(
                    count_leaked_text_chars, attempt_path, reader,
                    min_confidence=gate.min_confidence,
                )
            except Exception as e:
                # OCR failure must never block the image the pipeline is
                # waiting on — treat as "can't verify", not "leaked text".
                logger.warning("[OCR-GATE] scoring failed (attempt %d): %s", attempt_num, e)
                text_chars = 0

        attempts.append(GenAttempt(seed=seed, text_chars=text_chars, path=attempt_path))
        logger.info(
            "  [OCR-GATE] attempt %d/%d: seed=%d text_chars=%d (threshold=%d)",
            attempt_num, max_attempts, seed, text_chars, gate.max_chars,
        )
        if not gate.enabled or text_chars <= gate.max_chars:
            break

    best = pick_best_attempt(attempts)
    for a in attempts:
        if a is not best:
            with suppress(OSError):
                a.path.unlink()
    filename = best.path.name
    output_path = best.path
    seed = best.seed
    gate_passed = (not gate.enabled) or (best.text_chars <= gate.max_chars)

    elapsed_ms = int((time.time() - start) * 1000)
    logger.info(
        "Generated: %s (%d ms, %d attempt(s), ocr_text_chars=%d, gate_passed=%s)",
        filename, elapsed_ms, len(attempts), best.text_chars, gate_passed,
    )
    if gate.enabled and (len(attempts) > 1 or not gate_passed):
        logger.warning(
            "[OCR-GATE] %s: best of %d attempt(s) had %d leaked chars (threshold %d) — %s",
            config.friendly_name, len(attempts), best.text_chars, gate.max_chars,
            "passed after retry" if gate_passed else "still over threshold, returning best effort",
        )

    if gate.enabled:
        await write_ocr_gate_audit_log(
            model=config.friendly_name, task_id=req.task_id, attempts=attempts,
            best=best, threshold=gate.max_chars, gate_passed=gate_passed,
        )

    return GenerateResponse(
        image_path=str(output_path),
        filename=filename,
        width=req.width,
        height=req.height,
        model=config.friendly_name,
        generation_time_ms=elapsed_ms,
        seed=seed,
        ocr_text_chars=best.text_chars,
        ocr_gate_attempts=len(attempts),
        ocr_gate_passed=gate_passed,
    )


@app.post("/unload")
async def unload(req: UnloadRequest | None = None):
    """Explicit VRAM free, called by the GPU scheduler.

    Soft (default, no body or ``{"hard": false}``): drop the pipeline +
    ``torch.cuda.empty_cache()`` — the pre-existing behavior, used when
    switching the GPU over to Ollama.

    Hard (``{"hard": true}``): also exit the process. ``empty_cache()`` does
    NOT return the CUDA context/reserved pool to the host under WSL2
    (confirmed 2026-07-12: soft /unload freed 0 GB; a container restart freed
    ~7 GB) — only a process exit does. Docker's ``restart: unless-stopped``
    on ``poindexter-image-gen-server`` brings it back; it lazy-loads on the
    next /generate. Used by the render-GPU VRAM reclaim path
    (``dispatch_media_pipeline``) before a video dispatch. Exits even if
    ``state.pipeline`` is already None — the idle unloader may have already
    dropped the pipeline object while the CUDA context itself (which is what
    actually holds the reserved pool) is still live.

    The exit is gated on there actually being something to reclaim
    (``image_gen_hard_unload_min_reserved_mb``). Exiting is not free: the
    process is down for a cold start + lazy model reload, and any
    ``/generate`` landing in that window fails, which silently downgrades
    article images to stock. Before this gate the caller hard-unloaded on a
    5-minute cadence whether or not the reclaim could help — observed
    2026-07-27, ~24 consecutive exits that each freed nothing.
    """
    if req and req.hard:
        unload_pipeline()
        # NOT memory_allocated: unload_pipeline() just dropped every live
        # tensor, so allocated is 0 here by construction and can never tell
        # us whether exiting is worthwhile (it logged a misleading
        # "vram_used=0 MB" on every one of those 24 pointless exits). The
        # multi-GB block a process exit actually returns to the host is the
        # caching allocator's RESERVED pool, so that is what we measure.
        reserved_mb = torch.cuda.memory_reserved(0) // 1024 // 1024
        min_reserved_mb = await read_hard_unload_min_reserved_mb()
        if reserved_mb < min_reserved_mb:
            logger.info(
                "[HARD UNLOAD] skipped — %d MB reserved is below the %d MB "
                "threshold, so exiting would reclaim nothing and would open a "
                "cold-start window that downgrades article images",
                reserved_mb, min_reserved_mb,
            )
            return {
                "status": "nothing_to_reclaim",
                "vram_reserved_mb": reserved_mb,
                "min_reserved_mb": min_reserved_mb,
            }
        logger.warning(
            "[HARD UNLOAD] exiting process to return the CUDA context to the "
            "host (vram_reserved=%d MB >= %d MB threshold); Docker restart "
            "policy brings it back",
            reserved_mb, min_reserved_mb,
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
        return {"status": "exiting"}  # unreachable outside tests

    if state.pipeline is None:
        return {"status": "already_unloaded"}
    unload_pipeline()
    return {
        "status": "unloaded",
        "vram_used_mb": torch.cuda.memory_allocated(0) // 1024 // 1024,
    }


@app.get("/images/{filename}")
async def get_image(filename: str):
    # filename is request-controlled: resolve it and require the result to
    # stay inside OUTPUT_DIR so `../` can't walk out of the images dir.
    path = (OUTPUT_DIR / filename).resolve()
    if not path.is_relative_to(OUTPUT_DIR.resolve()) or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(str(path), media_type="image/png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9836)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    logger.info("image-gen server listening on %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
