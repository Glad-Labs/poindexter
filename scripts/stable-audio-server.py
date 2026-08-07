"""
Stable Audio Open 1.0 inference server — DB-driven, graceful-degradation HTTP service.

Runs on port 9839 as a sidecar, alongside the image-gen server (9836).
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
import sys
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
# Output formats soundfile can write here; also guards the request-supplied
# format from reaching the filesystem as anything but a known extension.
_ALLOWED_FORMATS = frozenset({"wav", "flac", "ogg", "mp3"})

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

PORT = int(os.getenv("STABLE_AUDIO_PORT", "9839"))
IDLE_TIMEOUT = 300        # seconds — unload after 5min idle so other GPU tasks run
# Reserved-pool floor for the process-exit reclaim (poindexter#999). Mirrors
# WAN_HARD_UNLOAD_MIN_RESERVED_MB / image_gen_hard_unload_min_reserved_mb —
# env, not app_settings, because this server must be able to reclaim VRAM
# without a reachable DB (same posture as wan).
HARD_UNLOAD_MIN_RESERVED_MB = int(
    os.getenv("STABLE_AUDIO_HARD_UNLOAD_MIN_RESERVED_MB", "512")
)
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
        self.native_sample_size: int = 0
        self.last_used: float = 0.0
        self.degraded: bool = False
        self.degraded_reason: str | None = None
        self.next_retry_delay: float = DEGRADED_POLL_MIN
        # Requests currently executing (or streaming their response). The
        # hard unload must never exit the process while one is in flight —
        # poindexter#992 cost every hero clip of 2026-08-06/-07 exactly that
        # way on the wan-server.
        self.inflight: int = 0

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
    """Read a single value from app_settings, decrypting ``enc:v1:`` secrets.

    Secrets are pgcrypto-encrypted at rest (plugins/secrets.py contract):
    the ciphertext is base64 in the row and Postgres itself decrypts via
    ``pgp_sym_decrypt`` with the symmetric key from POINDEXTER_SECRET_KEY —
    so this standalone sidecar needs no Python crypto, just the same env
    the worker already receives. A missing key on an encrypted row raises,
    which reload_config converts into a DEGRADED reason (loud, not silent).
    """
    try:
        conn = await asyncpg.connect(HOST_DB_URL, timeout=5)
        try:
            row = await conn.fetchrow(
                "SELECT value FROM app_settings WHERE key = $1", key
            )
            if row and row["value"] and row["value"].startswith("enc:v1:"):
                secret_key = os.getenv("POINDEXTER_SECRET_KEY", "")
                if not secret_key:
                    raise RuntimeError(
                        f"app_settings.{key} is encrypted but "
                        "POINDEXTER_SECRET_KEY is not set in this container"
                    )
                plaintext = await conn.fetchval(
                    "SELECT pgp_sym_decrypt(decode($1, 'base64'), $2)::text",
                    row["value"][len("enc:v1:"):],
                    secret_key,
                )
                return (plaintext or "").strip() or default
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

    # DB-first HF auth (#198): the gated-model token lives in app_settings as
    # the ``huggingface_token`` secret. Stashed on _state here (async, DB in
    # reach) so the sync _load_model can use it without an event loop; env
    # HF_TOKEN / a mounted cache token remain fallbacks.
    try:
        _state.hf_token = await _read_setting("huggingface_token", "")
    except Exception:
        _state.hf_token = ""

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


def _hard_exit_if_reserved_pool(*, quiet_skip: bool = False) -> dict[str, Any]:
    """Floor-gated process exit (poindexter#999). Caller must have already
    soft-unloaded, so ``memory_allocated`` is ~0 by construction and the gate
    measures ``memory_reserved`` — the caching-allocator pool that ONLY a
    process exit returns to the host.

    This is the whole fix. ``_unload_model`` drops the model objects and calls
    ``empty_cache()``, which returns nothing: measured 2026-08-07, this server
    sat at **10,952 MiB on the render GPU with ``model_loaded: false``**, a
    soft ``/unload`` freed **3 MiB**, and a process restart freed **10.96 GiB**.
    Because the reclaim ladder had never heard of this service, that 11 GiB was
    invisible to every lever in the system — wan needed 25.4 GiB on a 31.8 GiB
    card and OOM'd against a ghost.

    Below the floor: no exit (``quiet_skip`` silences the log for the 10s
    watchdog cadence). At or above: flush + ``os._exit(0)``; Docker's restart
    policy revives the server and it lazy-loads on the next ``/generate``.
    """
    if not torch.cuda.is_available():
        return {"status": "nothing_to_reclaim", "vram_reserved_mb": 0,
                "min_reserved_mb": HARD_UNLOAD_MIN_RESERVED_MB}
    reserved_mb = torch.cuda.memory_reserved(0) // 1024 // 1024
    if reserved_mb < HARD_UNLOAD_MIN_RESERVED_MB:
        if not quiet_skip:
            logger.info(
                "[HARD UNLOAD] skipped — %d MB reserved is below the %d MB "
                "floor; exiting would reclaim nothing and cost a cold reload "
                "on the next render",
                reserved_mb, HARD_UNLOAD_MIN_RESERVED_MB,
            )
        return {
            "status": "nothing_to_reclaim",
            "vram_reserved_mb": reserved_mb,
            "min_reserved_mb": HARD_UNLOAD_MIN_RESERVED_MB,
        }
    logger.warning(
        "[HARD UNLOAD] exiting process to return the CUDA context to the host "
        "(vram_reserved=%d MB >= %d MB floor); Docker restart policy brings "
        "it back", reserved_mb, HARD_UNLOAD_MIN_RESERVED_MB,
    )
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
    return {"status": "exiting"}  # unreachable outside tests


def _load_model() -> bool:
    """Lazy-load the Stable Audio Open 1.0 model."""
    if _state.model is not None:
        return True

    logger.info("[MODEL] Loading Stable Audio Open 1.0 — this takes ~20s on first run")
    token = getattr(_state, "hf_token", "") or os.getenv("HF_TOKEN", "")
    if token:
        os.environ["HF_TOKEN"] = token
    else:
        logger.warning(
            "[MODEL] no huggingface_token in app_settings and no HF_TOKEN env "
            "— the gated model download will 401 until one is set "
            "(poindexter settings set huggingface_token <hf_...>)"
        )
    try:
        from stable_audio_tools import get_pretrained_model
        from stable_audio_tools.inference.generation import generate_diffusion_cond

        # get_pretrained_model returns (model, model_config) — the second
        # element is the CONFIG DICT, not the sample rate.
        model, model_config = get_pretrained_model("stabilityai/stable-audio-open-1.0")
        model = model.eval().cuda()
        _state.model = model
        _state.sample_rate = int(model_config["sample_rate"])
        _state.native_sample_size = int(model_config["sample_size"])
        _state._generate_fn = generate_diffusion_cond   # cache the fn ref
        logger.info(
            "[MODEL] Loaded. sample_rate=%dHz native_window=%d samples",
            _state.sample_rate, _state.native_sample_size,
        )
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

        # The DiT generates its full native window (~47.5s); seconds_total
        # conditioning places the content in the head, and we trim to the
        # requested duration afterwards (official stable-audio-tools usage).
        with torch.no_grad():
            output = generate_fn(
                model,
                conditioning=conditioning,
                batch_size=1,
                sample_size=_state.native_sample_size,
                sample_rate=sample_rate,
                device="cuda",
                init_audio=None,
                init_noise_level=1.0,
            )

        audio = rearrange(output, "b d n -> d (b n)")
        audio = audio.to(torch.float32).cpu().numpy()
        want = int(sample_rate * duration_s)
        if audio.shape[-1] > want:
            audio = audio[:, :want]

        # Raw diffusion output is NOT full-scale — official stable-audio-tools
        # usage peak-normalizes before writing; without this the WAV is
        # near-silent (measured max -54 dBFS). Normalize to -1 dBFS headroom
        # so the compositor's dBFS mix targeting sees real signal.
        peak = float(abs(audio).max())
        if peak > 0:
            audio = audio * (0.891 / peak)
        audio = audio.clip(-1, 1)

        sf.write(
            output_path, audio.T, sample_rate,
            format=output_format.upper(), subtype="PCM_16",
        )
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
        "inflight": _state.inflight,
        # The number that mattered and wasn't visible (poindexter#999): this
        # server reported model_loaded=false while holding 10,952 MiB, so
        # "is it holding VRAM?" was unanswerable without nvidia-smi and a PID
        # lookup. vram_reserved_mb is what the hard-unload floor gates on.
        "vram_reserved_mb": (
            torch.cuda.memory_reserved(0) // 1024 // 1024
            if torch.cuda.is_available() else 0
        ),
        "hard_unload_min_reserved_mb": HARD_UNLOAD_MIN_RESERVED_MB,
    }


@app.post("/generate")
async def generate(req: GenerateRequest):
    """In-flight bracket around the real handler (poindexter#999).

    Wraps rather than inlines so the counter covers the FileResponse stream
    too: FastAPI returns from the handler before the body is sent, so a
    counter decremented inside would let a hard unload exit mid-stream and
    hand the caller a dropped connection on a render that actually succeeded.
    ``last_used`` is stamped on the way OUT for the same reason — the idle
    window must start when the response is done, not when inference ended.
    """
    _state.inflight += 1
    try:
        return await _generate_inner(req)
    finally:
        _state.last_used = time.monotonic()
        _state.inflight -= 1


async def _generate_inner(req: GenerateRequest):
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
    # fmt is request-controlled and becomes a filename suffix — allowlist it
    # so it can't smuggle path separators (CodeQL py/path-injection).
    if fmt not in _ALLOWED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"format must be one of {sorted(_ALLOWED_FORMATS)}",
        )

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


class UnloadRequest(BaseModel):
    """Body for ``POST /unload``. ``hard`` opts into the process-exit path."""

    hard: bool = False


@app.post("/unload")
async def unload(req: UnloadRequest | None = None) -> dict[str, Any]:
    """Manual VRAM release — called by the worker's GPU reclaim ladder.

    Soft (default, no body or ``{"hard": false}``): drop the model +
    ``empty_cache()`` — the pre-existing behaviour, unchanged.

    Hard (``{"hard": true}``, poindexter#999): also exit the process. This is
    the only thing that actually returns the reserved pool + CUDA context;
    measured on this server, a soft unload freed 3 MiB where a process exit
    freed 10.96 GiB. Gated on the reserved pool clearing
    ``STABLE_AUDIO_HARD_UNLOAD_MIN_RESERVED_MB`` so repeat reclaims are cheap
    (the image-gen lesson: ~24 consecutive no-op exits before its gate).
    """
    # Never unload out from under a live generation. The reclaim ladder calls
    # this with hard=True whenever the render-GPU gate looks unhealthy — and a
    # generation in progress IS part of that unhealthy-looking state. Obeying
    # would kill the very GPU work the reclaim exists to make room for, which
    # is precisely how the wan-server discarded every hero clip on
    # 2026-08-06/-07 (poindexter#992). Declining is strictly better: the
    # reclaim wants VRAM to START work, and the work already running is what
    # the VRAM is for.
    if _state.inflight > 0:
        logger.warning(
            "[UNLOAD] declining %s unload — %d generation(s) in flight",
            "hard" if (req and req.hard) else "soft", _state.inflight,
        )
        return {"status": "busy_generation_in_flight",
                "inflight": _state.inflight}

    _unload_model()
    if req is not None and req.hard:
        return {"status": "unloaded", **_hard_exit_if_reserved_pool()}
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

        # Idle unload. The second stage is what actually returns VRAM to the
        # card (poindexter#999): without it this server sat at 10,952 MiB with
        # model_loaded=false because dropping the objects leaves the reserved
        # pool behind — invisible to every reclaim lever and, since nothing
        # else could touch it, a permanent 11 GiB tax on the render GPU.
        # Self-driven so it heals without waiting for a consumer to notice.
        if (
            _state.inflight == 0
            and _state.last_used > 0
            and (time.monotonic() - _state.last_used) > IDLE_TIMEOUT
        ):
            if _state.model is not None:
                logger.info("[WATCHDOG] Idle timeout — unloading model")
                _unload_model()
            _hard_exit_if_reserved_pool(quiet_skip=True)

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
