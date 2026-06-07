"""
Stable Audio Open 1.0 inference server — DB-driven, graceful-degradation HTTP service.

Runs on port 9839 as a sidecar, alongside the SDXL server (9836).
Docker callers reach it via host.docker.internal:9839.

The model + config live in poindexter_brain (app_settings) so they can be
changed without restarting the server. On DB failure the server enters DEGRADED
state: /generate returns 503, /health reports the reason, and the server keeps
running for self-healing.

License: Stability AI Community License.
Free for commercial use up to $1M annual revenue.

Endpoints:
    GET  /health     — status, model name, degradation reason
    POST /generate   — generate audio from text prompt
    POST /unload     — free VRAM (called by GPU scheduler)
    POST /reload     — re-read DB config (after changing app_settings)
"""

import asyncio
import gc
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import asyncpg
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

OUTPUT_DIR = Path(os.path.expanduser("~")) / ".poindexter" / "generated-audio"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HOST_DB_URL = os.getenv(
    "POINDEXTER_BRAIN_URL",
    os.getenv(
        "GLADLABS_BRAIN_URL",
        "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain",
    ),
)

PORT = int(os.getenv("STABLE_AUDIO_PORT", "9839"))
IDLE_TIMEOUT = 300        # seconds — unload after 5min idle so other GPU tasks run
DEGRADED_POLL_MIN = 5     # seconds — fast heal on boot race
DEGRADED_POLL_MAX = 60    # seconds — max retry interval
HEALTHY_POLL = 60         # seconds — idle heartbeat
MAX_DURATION_S = 47.0     # model hard cap

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("stable-audio-server")
app = FastAPI(title="Stable Audio Open 1.0 Server", version="1.0")


# ---------------------------------------------------------------------------
# Server state
# ---------------------------------------------------------------------------

class _State:
    def __init__(self):
        self.model: Any = None
        self._generate_fn: Any = None
        self.sample_rate: int = 44100
        self.last_used: float = 0.0
        self.degraded: bool = False
        self.degraded_reason: str | None = None
        self.next_retry_delay: float = DEGRADED_POLL_MIN

    def mark_degraded(self, reason: str):
        self.degraded = True
        self.degraded_reason = reason
        logger.error("[DEGRADED] %s", reason)

    def mark_healthy(self):
        if self.degraded:
            logger.info("[RECOVERED] previous degradation: %s", self.degraded_reason)
        self.degraded = False
        self.degraded_reason = None
        self.next_retry_delay = DEGRADED_POLL_MIN


_state = _State()


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

async def _read_setting(key: str, default: str = "") -> str:
    """Read a single value from app_settings."""
    try:
        conn = await asyncpg.connect(HOST_DB_URL, timeout=5)
        try:
            row = await conn.fetchrow(
                "SELECT value FROM app_settings WHERE key = $1", key
            )
        finally:
            await conn.close()
        if row and row["value"]:
            return row["value"].strip()
        return default
    except Exception:
        raise


async def reload_config() -> None:
    """Re-read app_settings and validate the model is activatable."""
    try:
        engine = await _read_setting("audio_gen_engine", "")
    except Exception as e:
        _state.mark_degraded(f"DB read failed for audio_gen_engine: {e}")
        return

    if not engine or engine.strip().lower() not in ("stable-audio-open-1.0", "stable_audio_open"):
        _state.mark_degraded(
            f"audio_gen_engine={engine!r} — set to 'stable-audio-open-1.0' to activate"
        )
        _unload_model()
        return

    _state.mark_healthy()
    logger.info("[CONFIG] audio_gen_engine=%r — ready", engine)


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------

def _unload_model():
    if _state.model is not None:
        logger.info("[MODEL] Unloading Stable Audio Open model")
        del _state.model
        _state.model = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            logger.info("[MODEL] VRAM freed")


def _load_model() -> bool:
    """Lazy-load the Stable Audio Open 1.0 model."""
    if _state.model is not None:
        return True

    logger.info("[MODEL] Loading Stable Audio Open 1.0 — this takes ~20s on first run")
    try:
        from stable_audio_tools import get_pretrained_model
        from stable_audio_tools.inference.generation import generate_diffusion_cond

        model, sample_rate = get_pretrained_model("stabilityai/stable-audio-open-1.0")
        model = model.eval().cuda()
        _state.model = model
        _state.sample_rate = sample_rate
        _state._generate_fn = generate_diffusion_cond   # cache the fn ref
        logger.info("[MODEL] Loaded. sample_rate=%dHz", sample_rate)
        return True
    except Exception as e:
        _state.mark_degraded(f"Model load failed: {e}")
        logger.exception("[MODEL] Load error")
        return False


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _generate_sync(
    prompt: str,
    duration_s: float,
    output_path: str,
    output_format: str,
) -> float | None:
    """Synchronous inference — runs in a thread via asyncio.to_thread."""
    try:
        import soundfile as sf
        import torch
        from einops import rearrange

        model = _state.model
        generate_fn = _state._generate_fn
        sample_rate = _state.sample_rate

        conditioning = [{
            "prompt": prompt,
            "seconds_start": 0,
            "seconds_total": duration_s,
        }]

        with torch.no_grad():
            output = generate_fn(
                model,
                conditioning=conditioning,
                batch_size=1,
                sample_size=int(sample_rate * duration_s),
                sample_rate=sample_rate,
                device="cuda",
                init_audio=None,
                init_noise_level=1.0,
            )

        audio = rearrange(output, "b d n -> d (b n)")
        audio = audio.cpu().clamp(-1, 1).numpy()

        sf.write(output_path, audio.T, sample_rate, format=output_format.upper())
        rendered = audio.shape[-1] / sample_rate
        logger.info(
            "[GENERATE] wrote %s (%.2fs, %d samples)",
            output_path, rendered, audio.shape[-1],
        )
        return rendered

    except Exception as e:
        logger.exception("[GENERATE] inference failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    duration_s: float = Field(default=5.0, ge=0.1, le=47.0)
    sample_rate: int = Field(default=44100)
    format: str = Field(default="wav")
    model: str = Field(default="stable-audio-open-1.0")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "degraded" if _state.degraded else "healthy",
        "model": "stable-audio-open-1.0",
        "model_loaded": _state.model is not None,
        "degraded_reason": _state.degraded_reason,
        "last_used": _state.last_used,
    }


@app.post("/generate")
async def generate(req: GenerateRequest):
    if _state.degraded:
        raise HTTPException(
            status_code=503,
            detail=f"Stable Audio server degraded: {_state.degraded_reason}",
        )

    if not _load_model():
        raise HTTPException(
            status_code=503,
            detail=f"Model load failed: {_state.degraded_reason}",
        )

    prompt = req.prompt.strip()
    duration_s = min(req.duration_s, MAX_DURATION_S)
    fmt = req.format.lower().lstrip(".")

    suffix = f".{fmt}"
    with tempfile.NamedTemporaryFile(
        dir=OUTPUT_DIR, suffix=suffix, delete=False,
    ) as tmp:
        output_path = tmp.name

    rendered = await asyncio.to_thread(
        _generate_sync, prompt, duration_s, output_path, fmt,
    )

    if rendered is None or not os.path.exists(output_path):
        try:
            os.unlink(output_path)
        except OSError:
            pass
        raise HTTPException(
            status_code=500,
            detail="Audio generation failed — check server logs",
        )

    _state.last_used = time.monotonic()

    return FileResponse(
        output_path,
        media_type=f"audio/{fmt}",
        headers={
            "X-Duration-S": str(rendered),
            "X-Sample-Rate": str(_state.sample_rate),
        },
        background=None,
    )


@app.post("/unload")
async def unload():
    _unload_model()
    return {"status": "unloaded"}


@app.post("/reload")
async def reload():
    await reload_config()
    return {
        "degraded": _state.degraded,
        "reason": _state.degraded_reason,
    }


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

async def _watchdog():
    """Idle-timeout unload + degraded self-heal."""
    while True:
        await asyncio.sleep(10)

        # Idle unload
        if (
            _state.model is not None
            and _state.last_used > 0
            and (time.monotonic() - _state.last_used) > IDLE_TIMEOUT
        ):
            logger.info("[WATCHDOG] Idle timeout — unloading model")
            _unload_model()

        # Self-heal from degraded state
        if _state.degraded:
            await asyncio.sleep(_state.next_retry_delay)
            _state.next_retry_delay = min(
                _state.next_retry_delay * 2, DEGRADED_POLL_MAX
            )
            await reload_config()
        else:
            _state.next_retry_delay = DEGRADED_POLL_MIN


@app.on_event("startup")
async def startup():
    await reload_config()
    asyncio.create_task(_watchdog())
    logger.info("[STARTUP] Stable Audio Open server listening on :%d", PORT)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
