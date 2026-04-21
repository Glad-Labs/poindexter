"""
GPU Scheduler — serializes access to the shared GPU between Ollama and SDXL,
and automatically yields to gaming/external GPU workloads.

With a single GPU (RTX 5090, 32GB), only one large workload can run at a time.
This module provides an async lock so that:
  - Ollama LLM inference and SDXL image generation don't fight for VRAM
  - Before SDXL starts, any loaded Ollama model is unloaded
  - Before Ollama starts, SDXL pipeline is released (if loaded)
  - Small models (embeddings) can coexist and skip the lock
  - If a game or external app is using the GPU, the pipeline pauses automatically

Gaming detection:
  Queries the nvidia-smi prometheus exporter (host.docker.internal:9835) for GPU
  utilization. If utilization is above the threshold and we don't hold the lock,
  something external (a game) is using the GPU — we wait until it drops.

Usage:
    from services.gpu_scheduler import gpu
    async with gpu.lock("ollama", model="glm-4.7-5090"):
        result = await ollama.generate(...)
"""

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx

from services.logger_config import get_logger

logger = get_logger(__name__)

def _sc_get(key: str, default: str = "") -> str:
    from services.site_config import site_config
    return site_config.get(key, default)

OLLAMA_BASE_URL = _sc_get("ollama_base_url") or _sc_get("ollama_host") or "http://host.docker.internal:11434"

# nvidia-smi prometheus exporter on the host
NVIDIA_EXPORTER_URL = _sc_get("nvidia_exporter_url", "http://host.docker.internal:9835/metrics")

# Models under this VRAM threshold (in GB) skip the lock — they can coexist.
SMALL_MODEL_THRESHOLD_GB = 2.0

# Gaming detection defaults — all overridable via app_settings (DB-first config)
_DEFAULT_GPU_BUSY_THRESHOLD = 30  # GPU utilization % to consider "in use"
_DEFAULT_GAMING_CHECK_INTERVAL = 15  # seconds between checks while waiting
_DEFAULT_GAMING_CONFIRM_CHECKS = 2  # consecutive checks above threshold to confirm
_DEFAULT_GAMING_CLEAR_CHECKS = 3  # consecutive checks below threshold to resume


def _cfg_int(key: str, default: int) -> int:
    """Read an int from site_config (DB) with fallback."""
    try:
        from services.site_config import site_config
        return site_config.get_int(key, default)
    except Exception:
        return default


def _cfg_float(key: str, default: float) -> float:
    """Read a float from site_config (DB) with fallback."""
    try:
        from services.site_config import site_config
        return site_config.get_float(key, default)
    except Exception:
        return default


class GPUScheduler:
    """Async-safe GPU resource coordinator with gaming detection."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._current_owner: str | None = None  # "ollama" or "sdxl"
        self._current_model: str | None = None
        self._acquired_at: float = 0
        self._gaming_detected: bool = False
        self._gaming_paused_since: float = 0
        self._total_gaming_paused_s: float = 0  # cumulative for metrics

    @asynccontextmanager
    async def lock(
        self,
        owner: str,
        model: str | None = None,
        task_id: str | None = None,
        phase: str | None = None,
    ):
        """Acquire exclusive GPU access.

        Waits for any gaming/external workload to finish before acquiring.

        Args:
            owner: "ollama" or "sdxl"
            model: model name (for logging/tracking)
            task_id: optional pipeline task UUID — when set, a row is
                written to ``gpu_task_sessions`` on release so the
                feedback loop (gitea#271 Phase 3.A3) can attribute GPU
                utilisation + electricity cost to the originating task.
            phase: optional pipeline phase label (e.g. "generate_content",
                "featured_image"). Defaults to ``owner`` when unset.
        """
        # Wait for gaming to stop before acquiring lock
        await self._wait_for_gaming_clear()

        waited = False
        if self._lock.locked():
            logger.info(
                "GPU busy — waiting",
                waiting_for=owner,
                current_owner=self._current_owner,
                current_model=self._current_model,
            )
            waited = True

        await self._lock.acquire()
        wait_msg = " (waited)" if waited else ""
        logger.info("GPU acquired%s", wait_msg, owner=owner, model=model)

        self._current_owner = owner
        self._current_model = model
        self._acquired_at = time.monotonic()
        session_start = datetime.now(timezone.utc)

        try:
            # Prepare GPU for the new owner
            if owner == "sdxl":
                await self._unload_ollama_models()
            yield
        finally:
            duration = time.monotonic() - self._acquired_at
            logger.info("GPU released", owner=owner, model=model, duration_s=round(duration, 1))
            self._current_owner = None
            self._current_model = None
            self._lock.release()
            # gitea#271 Phase 3.A3 — record the session so model/phase
            # compute economics are queryable per task. Best-effort; a
            # write failure never breaks the GPU lock lifecycle.
            if task_id:
                try:
                    await self._record_task_session(
                        task_id=task_id,
                        phase=phase or owner,
                        model=model,
                        started_at=session_start,
                        duration_seconds=duration,
                    )
                except Exception as exc:
                    logger.debug("gpu_task_sessions write failed", error=str(exc))

    async def _record_task_session(
        self,
        *,
        task_id: str,
        phase: str,
        model: str | None,
        started_at: datetime,
        duration_seconds: float,
    ) -> None:
        """Insert a row into gpu_task_sessions for gitea#271 Phase 3.A3.

        Samples current GPU utilisation + power once at release time. A
        future enhancement can take a rolling average over the window via
        the nvidia-smi exporter's range queries; one sample is enough to
        start populating the table with directional signal.
        """
        # Lazy DB connection — the scheduler shouldn't carry a pool
        # reference; resolve via brain.bootstrap so it works the same in
        # worker + test environments.
        try:
            from brain.bootstrap import resolve_database_url
            import asyncpg
        except Exception:
            return
        dsn = resolve_database_url()
        if not dsn:
            return

        # Sample utilisation / power in parallel with the close path.
        util_pct = await self._get_gpu_utilization()
        power_w = await self._get_gpu_power_watts()
        electricity_rate = _cfg_float(
            "electricity_rate_kwh_usd", 0.12,
        )
        kwh = 0.0
        cost_usd = 0.0
        if power_w and duration_seconds > 0:
            kwh = (power_w / 1000.0) * (duration_seconds / 3600.0)
            cost_usd = kwh * electricity_rate

        conn = None
        try:
            conn = await asyncpg.connect(dsn)
            await conn.execute(
                """
                INSERT INTO gpu_task_sessions (
                    task_id, phase, started_at, ended_at,
                    duration_seconds, gpu_model, avg_utilization_pct,
                    avg_power_watts, peak_power_watts, kwh_consumed,
                    electricity_rate_kwh, electricity_cost_usd, model_name
                )
                VALUES ($1, $2, $3, NOW(), $4, $5, $6, $7, $7, $8, $9, $10, $11)
                """,
                str(task_id),
                phase,
                started_at,
                float(duration_seconds),
                "RTX 5090",
                float(util_pct) if util_pct is not None else None,
                float(power_w) if power_w is not None else None,
                float(kwh) if kwh else None,
                float(electricity_rate),
                float(cost_usd) if cost_usd else None,
                model,
            )
        finally:
            if conn is not None:
                await conn.close()

    async def _get_gpu_power_watts(self) -> float | None:
        """Query nvidia-smi exporter for current GPU power draw (watts)."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                resp = await client.get(NVIDIA_EXPORTER_URL, timeout=5)
                if resp.status_code != 200:
                    return None
                for line in resp.text.splitlines():
                    if line.startswith("nvidia_gpu_power_usage_watts{"):
                        return float(line.split("}")[1].strip())
        except Exception:
            return None
        return None

    async def _get_gpu_utilization(self) -> float | None:
        """Query nvidia-smi exporter for current GPU utilization %."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                resp = await client.get(NVIDIA_EXPORTER_URL, timeout=5)
                if resp.status_code != 200:
                    return None
                for line in resp.text.splitlines():
                    if line.startswith("nvidia_gpu_utilization_percent{"):
                        return float(line.split("}")[1].strip())
        except Exception:
            return None
        return None

    async def _wait_for_gaming_clear(self):
        """Block until GPU is not being used by an external workload (gaming).

        Uses consecutive checks to avoid false positives from brief GPU spikes.
        All thresholds are DB-configurable via app_settings.
        """
        threshold = _cfg_int("gpu_busy_threshold_percent", _DEFAULT_GPU_BUSY_THRESHOLD)
        check_interval = _cfg_int("gpu_gaming_check_interval", _DEFAULT_GAMING_CHECK_INTERVAL)
        confirm_checks = _cfg_int("gpu_gaming_confirm_checks", _DEFAULT_GAMING_CONFIRM_CHECKS)
        clear_checks = _cfg_int("gpu_gaming_clear_checks", _DEFAULT_GAMING_CLEAR_CHECKS)

        # Quick check — if GPU is idle, proceed immediately
        util = await self._get_gpu_utilization()
        if util is None or util < threshold:
            if self._gaming_detected:
                pause_duration = time.monotonic() - self._gaming_paused_since
                self._total_gaming_paused_s += pause_duration
                logger.info("[GPU] Gaming ended — resuming pipeline (paused %.0fs)", pause_duration)
                self._gaming_detected = False
            return

        # GPU is busy — confirm it's sustained (not a brief spike)
        busy_count = 1
        while busy_count < confirm_checks:
            await asyncio.sleep(check_interval)
            util = await self._get_gpu_utilization()
            if util is not None and util >= threshold:
                busy_count += 1
            else:
                return  # Was just a spike, proceed

        # Confirmed: external workload detected
        if not self._gaming_detected:
            self._gaming_detected = True
            self._gaming_paused_since = time.monotonic()
            logger.info("[GPU] Gaming/external workload detected (util=%.0f%%) — pausing pipeline", util)

        # Wait until GPU usage drops for clear_checks consecutive checks
        clear_count = 0
        while clear_count < clear_checks:
            await asyncio.sleep(check_interval)
            util = await self._get_gpu_utilization()
            if util is None or util < threshold:
                clear_count += 1
            else:
                clear_count = 0  # Reset — still gaming

        pause_duration = time.monotonic() - self._gaming_paused_since
        self._total_gaming_paused_s += pause_duration
        logger.info("[GPU] Gaming ended — resuming pipeline (paused %.0fs)", pause_duration)
        self._gaming_detected = False

    async def _unload_ollama_models(self):
        """Unload all Ollama models to free VRAM for SDXL."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=3.0)) as client:
                resp = await client.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=10)
                if resp.status_code != 200:
                    return
                data = resp.json()
                for model in data.get("models", []):
                    name = model["name"]
                    logger.info("Unloading Ollama model for SDXL", model=name)
                    await client.post(
                        f"{OLLAMA_BASE_URL}/api/generate",
                        json={"model": name, "keep_alive": 0},
                        timeout=30,
                    )
        except Exception as e:
            logger.warning("Failed to unload Ollama models: %s", e)

    async def prepare_mode(self, mode: str):
        """Actively prepare GPU for a specific workload mode.

        Call this BEFORE a pipeline stage that needs a different GPU workload.
        The pipeline knows what's coming next — no idle timeouts needed.

        Modes:
            "ollama"  — unload SDXL, Ollama auto-loads on next request
            "sdxl"    — unload Ollama models, SDXL server loads on next /generate
            "idle"    — unload everything, free all VRAM
        """
        if mode == "sdxl":
            await self._unload_ollama_models()
            logger.info("[GPU] Prepared for SDXL — Ollama models unloaded")
        elif mode == "ollama":
            await self._unload_sdxl()
            logger.info("[GPU] Prepared for Ollama — SDXL unloaded")
        elif mode == "idle":
            await self._unload_ollama_models()
            await self._unload_sdxl()
            logger.info("[GPU] All models unloaded — VRAM freed")

    async def _unload_sdxl(self):
        """Tell the SDXL server to unload its model and free VRAM immediately."""
        sdxl_url = _sc_get("sdxl_server_url", "http://localhost:9836")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
                resp = await client.post(f"{sdxl_url}/unload", timeout=10)
                if resp.status_code == 200:
                    logger.info("[GPU] SDXL model unloaded via /unload endpoint")
        except Exception:
            pass  # SDXL server not running — nothing to unload

    @property
    def is_busy(self) -> bool:
        return self._lock.locked()

    @property
    def is_gaming(self) -> bool:
        return self._gaming_detected

    @property
    def status(self) -> dict:
        current_pause = round(time.monotonic() - self._gaming_paused_since, 1) if self._gaming_detected else 0
        return {
            "busy": self._lock.locked(),
            "owner": self._current_owner,
            "model": self._current_model,
            "duration_s": round(time.monotonic() - self._acquired_at, 1) if self._lock.locked() else 0,
            "gaming_detected": self._gaming_detected,
            "gaming_paused_s": current_pause,
            "total_gaming_paused_s": round(self._total_gaming_paused_s + current_pause, 1),
            "config": {
                "threshold_percent": _cfg_int("gpu_busy_threshold_percent", _DEFAULT_GPU_BUSY_THRESHOLD),
                "check_interval_s": _cfg_int("gpu_gaming_check_interval", _DEFAULT_GAMING_CHECK_INTERVAL),
                "confirm_checks": _cfg_int("gpu_gaming_confirm_checks", _DEFAULT_GAMING_CONFIRM_CHECKS),
                "clear_checks": _cfg_int("gpu_gaming_clear_checks", _DEFAULT_GAMING_CLEAR_CHECKS),
            },
        }


# Module-level singleton
gpu = GPUScheduler()
