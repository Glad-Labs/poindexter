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
from typing import Any

import httpx

from services.logger_config import get_logger

logger = get_logger(__name__)

# Module-level defaults used only when a caller builds a GPUScheduler
# with no site_config (pre-lifespan paths or tests). The module singleton
# constructed at the bottom of this file binds these values directly so
# import-time does not touch site_config.
_DEFAULT_OLLAMA_BASE_URL = "http://host.docker.internal:11434"
_DEFAULT_NVIDIA_EXPORTER_URL = "http://host.docker.internal:9835/metrics"

# Backwards-compat module-level attributes — left in place because
# services/gpu_scheduler.py consumers historically imported them. Once
# Phase H step 6 lands every caller will read these off the instance
# (``gpu.OLLAMA_BASE_URL`` / ``gpu.NVIDIA_EXPORTER_URL``) instead.
OLLAMA_BASE_URL = _DEFAULT_OLLAMA_BASE_URL
NVIDIA_EXPORTER_URL = _DEFAULT_NVIDIA_EXPORTER_URL

# Models under this VRAM threshold (in GB) skip the lock — they can coexist.
SMALL_MODEL_THRESHOLD_GB = 2.0

# Gaming detection defaults — all overridable via app_settings (DB-first config).
#
# Mode dispatch (`gpu_gaming_detection_mode`):
#
# - ``"off"`` (default) — no detection. The pipeline trusts that
#   ollama / wan-server / sdxl-server manage their own VRAM, and that
#   the in-process GPU lock serializes our own workloads. Picked as
#   default after Glad-Labs/poindexter#144: the original threshold-based
#   detector tripped on our own sidecars' idle VRAM and on ollama's
#   model-loading bursts, blocking pipeline runs while wan-server was
#   up. The Windows-host nvidia-smi can't see Docker/WSL2 process
#   names cleanly, so per-process whitelisting isn't viable.
# - ``"manual"`` — reads ``pipeline_paused_for_gaming`` (bool). Operator
#   flips it on before a long game session, off after. Reliable, no
#   guessing.
# - ``"auto"`` — the legacy threshold-based detector. Default threshold
#   bumped 30%→90% so brief inference bursts (which sustain ~70-80%
#   for seconds) don't trigger; only real gaming (sustained 90%+ for
#   minutes) does.
_DEFAULT_GAMING_MODE = "off"
_DEFAULT_GPU_BUSY_THRESHOLD = 90  # GPU utilization % to consider "in use" (auto mode)
_DEFAULT_GAMING_CHECK_INTERVAL = 15  # seconds between checks while waiting
_DEFAULT_GAMING_CONFIRM_CHECKS = 2  # consecutive checks above threshold to confirm
_DEFAULT_GAMING_CLEAR_CHECKS = 3  # consecutive checks below threshold to resume

# Cooperative sidecar-unload protocol (Glad-Labs/poindexter#160 —
# supersedes the #144 sample-runner workaround). Before claiming the GPU
# lock for a competing operation, the scheduler asks the listed sidecars
# to release VRAM via their /unload endpoints. Sidecars lazy-reload on
# their next /generate request, so no explicit reload is needed.
_DEFAULT_UNLOAD_TIMEOUT_S = 60  # per-sidecar HTTP timeout for the /unload POST
_DEFAULT_UNLOAD_FAILURE_POLICY = "proceed"  # "proceed" | "abort"


def _cfg_int(site_config: Any, key: str, default: int) -> int:
    """Read an int from the injected site_config.

    Phase H (GH#95) — site_config is now a required arg. Previously
    this function lazy-imported the module singleton.
    """
    if site_config is None:
        return default
    return site_config.get_int(key, default)


def _cfg_float(site_config: Any, key: str, default: float) -> float:
    """Read a float from the injected site_config.

    Phase H (GH#95) — site_config is now a required arg. Previously
    this function lazy-imported the module singleton.
    """
    if site_config is None:
        return default
    return site_config.get_float(key, default)


def _cfg_str(site_config: Any, key: str, default: str) -> str:
    """Read a str from the injected site_config (Phase H DI seam)."""
    if site_config is None:
        return default
    value = site_config.get(key, default)
    return str(value) if value is not None else default


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    """Read a bool from the injected site_config (Phase H DI seam).

    Tolerates string-shaped values ("true"/"false"/"1"/"0") since
    operators set settings via SQL where everything's text.
    """
    if site_config is None:
        return default
    value = site_config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


class GPUScheduler:
    """Async-safe GPU resource coordinator with gaming detection."""

    def __init__(self, *, site_config: Any = None):
        """Initialise the GPU scheduler.

        Args:
            site_config: SiteConfig instance (DI — Phase H, GH#95).
                Optional because the module-level singleton ``gpu`` is
                constructed at import time, before the app lifespan has
                a SiteConfig ready. main.py lifespan binds the DB-loaded
                instance via ``gpu.set_site_config(...)`` once ready.
                Instance methods that read DB-backed tunables handle the
                None case by returning shipped defaults.
        """
        self._lock = asyncio.Lock()
        self._current_owner: str | None = None  # "ollama" or "sdxl"
        self._current_model: str | None = None
        self._acquired_at: float = 0
        self._gaming_detected: bool = False
        self._gaming_paused_since: float = 0
        self._total_gaming_paused_s: float = 0  # cumulative for metrics
        self._site_config = site_config
        # Per-instance URL overrides — read once at set_site_config()
        # time so instance methods don't need repeated DB hits.
        self._ollama_base_url = _DEFAULT_OLLAMA_BASE_URL
        self._nvidia_exporter_url = _DEFAULT_NVIDIA_EXPORTER_URL
        if site_config is not None:
            self._refresh_urls()

    def set_site_config(self, site_config: Any) -> None:
        """Rebind site_config after construction — Phase H (GH#95).

        main.py lifespan calls this once ``app.state.site_config`` is
        ready so subsequent lock() / status() calls read DB-backed
        tunables (thresholds, nvidia_exporter_url, ollama_base_url).
        """
        self._site_config = site_config
        self._refresh_urls()

    def _refresh_urls(self) -> None:
        """Re-resolve OLLAMA_BASE_URL / NVIDIA_EXPORTER_URL from site_config."""
        if self._site_config is None:
            return
        self._ollama_base_url = (
            self._site_config.get("ollama_base_url")
            or self._site_config.get("ollama_host")
            or _DEFAULT_OLLAMA_BASE_URL
        )
        self._nvidia_exporter_url = self._site_config.get(
            "nvidia_exporter_url", _DEFAULT_NVIDIA_EXPORTER_URL,
        )

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
        Then runs the cooperative sidecar-unload protocol (#160) — the
        scheduler asks any sidecars listed in
        ``gpu_competing_sidecars_for_<phase>`` to release VRAM before the
        new workload starts.

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

        # Cooperative sidecar-unload protocol (#160). Reads
        # ``gpu_competing_sidecars_for_<phase>`` (or ``..._for_<owner>``
        # when phase is unset) — a comma-separated list of sidecar names
        # registered in ``_SIDECAR_REGISTRY``. When the list is empty
        # (default), this is a no-op so existing behavior is preserved.
        # When ``failure_policy="abort"`` and any sidecar fails to
        # unload, the lock claim is aborted via RuntimeError — the
        # caller's stage / provider sees the failure and can retry or
        # skip. Default policy is "proceed" so existing pipelines stay
        # backwards-compatible.
        phase_label = phase or owner
        sidecar_names = self._resolve_competing_sidecars(phase_label)
        if sidecar_names:
            await self._cooperative_unload_or_abort(
                sidecars=sidecar_names,
                phase=phase_label,
                task_id=task_id,
            )

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
            import asyncpg
            from brain.bootstrap import resolve_database_url
        except Exception:
            return
        dsn = resolve_database_url()
        if not dsn:
            return

        # Sample utilisation / power in parallel with the close path.
        util_pct = await self._get_gpu_utilization()
        power_w = await self._get_gpu_power_watts()
        electricity_rate = _cfg_float(self._site_config,
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
                resp = await client.get(self._nvidia_exporter_url, timeout=5)
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
                resp = await client.get(self._nvidia_exporter_url, timeout=5)
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

        Mode-dispatch on ``gpu_gaming_detection_mode``:

        - ``"off"`` (default): no-op. We trust the in-process GPU lock
          to serialize our own workloads and the model-loaders
          (ollama, wan, sdxl) to manage their own VRAM. See
          Glad-Labs/poindexter#144 for the rationale.
        - ``"manual"``: pause iff ``pipeline_paused_for_gaming``
          (operator-controlled flag) is true.
        - ``"auto"``: legacy threshold-based detector. Default
          threshold raised to 90% so only sustained real-game load
          trips it; brief inference bursts don't.
        """
        mode = _cfg_str(self._site_config, "gpu_gaming_detection_mode", _DEFAULT_GAMING_MODE).lower()

        if mode == "off":
            return

        if mode == "manual":
            paused = _cfg_bool(self._site_config, "pipeline_paused_for_gaming", False)
            if not paused:
                if self._gaming_detected:
                    pause_duration = time.monotonic() - self._gaming_paused_since
                    self._total_gaming_paused_s += pause_duration
                    logger.info(
                        "[GPU] Manual gaming flag cleared — resuming pipeline (paused %.0fs)",
                        pause_duration,
                    )
                    self._gaming_detected = False
                return
            # Operator says "I'm gaming, hold off." Poll the flag at
            # the same cadence the auto-mode polls util.
            check_interval = _cfg_int(self._site_config, "gpu_gaming_check_interval", _DEFAULT_GAMING_CHECK_INTERVAL)
            if not self._gaming_detected:
                self._gaming_detected = True
                self._gaming_paused_since = time.monotonic()
                logger.info("[GPU] Manual gaming flag set — pausing pipeline")
            while True:
                await asyncio.sleep(check_interval)
                if not _cfg_bool(self._site_config, "pipeline_paused_for_gaming", False):
                    break
            pause_duration = time.monotonic() - self._gaming_paused_since
            self._total_gaming_paused_s += pause_duration
            logger.info(
                "[GPU] Manual gaming flag cleared — resuming pipeline (paused %.0fs)",
                pause_duration,
            )
            self._gaming_detected = False
            return

        # mode == "auto" — legacy threshold-based check.
        threshold = _cfg_int(self._site_config, "gpu_busy_threshold_percent", _DEFAULT_GPU_BUSY_THRESHOLD)
        check_interval = _cfg_int(self._site_config, "gpu_gaming_check_interval", _DEFAULT_GAMING_CHECK_INTERVAL)
        confirm_checks = _cfg_int(self._site_config, "gpu_gaming_confirm_checks", _DEFAULT_GAMING_CONFIRM_CHECKS)
        clear_checks = _cfg_int(self._site_config, "gpu_gaming_clear_checks", _DEFAULT_GAMING_CLEAR_CHECKS)

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
                resp = await client.get(f"{self._ollama_base_url}/api/ps", timeout=10)
                if resp.status_code != 200:
                    return
                data = resp.json()
                for model in data.get("models", []):
                    name = model["name"]
                    logger.info("Unloading Ollama model for SDXL", model=name)
                    await client.post(
                        f"{self._ollama_base_url}/api/generate",
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
        sdxl_url = self._resolve_sidecar_url("sdxl")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
                resp = await client.post(f"{sdxl_url}/unload", timeout=10)
                if resp.status_code == 200:
                    logger.info("[GPU] SDXL model unloaded via /unload endpoint")
        except Exception:
            pass  # SDXL server not running — nothing to unload

    # ------------------------------------------------------------------
    # Cooperative sidecar-unload protocol — Glad-Labs/poindexter#160
    # ------------------------------------------------------------------

    def _resolve_sidecar_url(self, sidecar: str) -> str:
        """Resolve the base URL for a registered sidecar.

        Reads ``<sidecar>_server_url`` from site_config, falling back to
        the canonical bootstrap default. Centralised here so the
        ``request_sidecar_unload`` protocol and the existing
        ``_unload_sdxl`` shortcut share one resolver.
        """
        from services.bootstrap_defaults import DEFAULT_SDXL_URL, DEFAULT_WAN_URL

        defaults = {
            "sdxl": DEFAULT_SDXL_URL,
            "wan": DEFAULT_WAN_URL,
        }
        default_url = defaults.get(sidecar, "")
        if self._site_config is None:
            return default_url
        # Try the canonical flat key first, then the plugin-namespaced
        # key (used by the wan video provider).
        flat_key = f"{sidecar}_server_url"
        url = self._site_config.get(flat_key, "") or ""
        if url:
            return str(url)
        if sidecar == "wan":
            nested = self._site_config.get(
                "plugin.video_provider.wan2.1-1.3b.server_url", "",
            ) or ""
            if nested:
                return str(nested)
        return default_url

    def _resolve_competing_sidecars(self, phase: str) -> list[str]:
        """Look up the sidecars that must yield before ``phase`` starts.

        Reads ``gpu_competing_sidecars_for_<phase>`` from site_config —
        a comma-separated list of sidecar names. Empty / unset means
        "no cooperative unload needed", preserving the pre-#160
        behavior.
        """
        if self._site_config is None or not phase:
            return []
        key = f"gpu_competing_sidecars_for_{phase}"
        try:
            raw = self._site_config.get(key, "") or ""
        except Exception:
            return []
        return [
            s.strip().lower() for s in str(raw).split(",") if s.strip()
        ]

    async def request_sidecar_unload(
        self,
        sidecars: list[str],
        timeout_s: float | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Ask each named sidecar to release its model from VRAM.

        Issues concurrent ``POST /unload`` calls to the resolved URL for
        every sidecar. A "successful" unload means a 2xx response within
        ``timeout_s``; anything else (timeout, connection refused, 5xx)
        is recorded as a failure but does not raise — the caller decides
        what to do via ``gpu_unload_failure_policy``.

        Args:
            sidecars: list of registered sidecar names ("wan", "sdxl").
                Names not in the registry are recorded as ``unknown``
                and skipped.
            timeout_s: per-sidecar HTTP timeout. Defaults to
                ``gpu_unload_timeout_s`` (60s).

        Returns:
            dict keyed by sidecar name, each value:
                ``{"ok": bool, "status_code": int|None, "url": str,
                   "elapsed_s": float, "error": str|None,
                   "vram_freed_mb": int|None}``
        """
        if not sidecars:
            return {}

        if timeout_s is None:
            timeout_s = float(_cfg_int(
                self._site_config,
                "gpu_unload_timeout_s",
                _DEFAULT_UNLOAD_TIMEOUT_S,
            ))

        async def _unload_one(name: str) -> tuple[str, dict[str, Any]]:
            started = time.monotonic()
            url = self._resolve_sidecar_url(name)
            if not url:
                return name, {
                    "ok": False,
                    "status_code": None,
                    "url": "",
                    "elapsed_s": 0.0,
                    "error": "unknown sidecar",
                    "vram_freed_mb": None,
                }
            try:
                # connect timeout kept tight (5s) so a dead sidecar
                # doesn't dominate the per-call budget; the read budget
                # gets the rest of timeout_s.
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        timeout_s, connect=min(5.0, timeout_s),
                    ),
                ) as client:
                    resp = await client.post(f"{url}/unload")
            except Exception as exc:
                elapsed = time.monotonic() - started
                logger.warning(
                    "[GPU] sidecar %s /unload failed: %s",
                    name, exc,
                )
                return name, {
                    "ok": False,
                    "status_code": None,
                    "url": url,
                    "elapsed_s": round(elapsed, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                    "vram_freed_mb": None,
                }
            elapsed = time.monotonic() - started
            ok = 200 <= resp.status_code < 300
            vram_freed: int | None = None
            try:
                payload = resp.json()
                if isinstance(payload, dict):
                    raw_vram = payload.get("vram_freed_mb")
                    if raw_vram is None:
                        # Some sidecars only emit vram_used_mb on /unload —
                        # we don't know the delta but at least surface it.
                        raw_vram = payload.get("vram_used_mb")
                    if raw_vram is not None:
                        vram_freed = int(raw_vram)
            except Exception:
                pass
            if ok:
                logger.info(
                    "[GPU] sidecar %s unloaded in %.2fs (status=%s)",
                    name, elapsed, resp.status_code,
                )
            else:
                logger.warning(
                    "[GPU] sidecar %s /unload returned %s (body=%s)",
                    name, resp.status_code, (resp.text or "")[:160],
                )
            return name, {
                "ok": ok,
                "status_code": resp.status_code,
                "url": url,
                "elapsed_s": round(elapsed, 3),
                "error": None if ok else f"http {resp.status_code}",
                "vram_freed_mb": vram_freed,
            }

        results = await asyncio.gather(
            *[_unload_one(name) for name in sidecars],
            return_exceptions=False,
        )
        return dict(results)

    async def _cooperative_unload_or_abort(
        self,
        *,
        sidecars: list[str],
        phase: str,
        task_id: str | None,
    ) -> None:
        """Run ``request_sidecar_unload`` and apply the failure policy.

        Reads ``gpu_unload_failure_policy`` (``"proceed"`` or
        ``"abort"``, default ``"proceed"``). On ``"abort"``, raises a
        ``RuntimeError`` so the lock context never enters and the caller
        is forced to handle the GPU contention upstream. On
        ``"proceed"``, logs a warning + emits an audit event and the
        lock claim continues — this is the backwards-compat path that
        matches the pre-#160 best-effort behavior.
        """
        results = await self.request_sidecar_unload(sidecars=sidecars)
        failed = {n: r for n, r in results.items() if not r.get("ok")}
        if not failed:
            return

        policy = _cfg_str(
            self._site_config,
            "gpu_unload_failure_policy",
            _DEFAULT_UNLOAD_FAILURE_POLICY,
        ).lower().strip()
        if policy not in ("proceed", "abort"):
            policy = _DEFAULT_UNLOAD_FAILURE_POLICY

        # Best-effort audit so operators can see contention events
        # without scraping logs. Avoid blocking the lock path on the
        # audit write; audit_log_bg is fire-and-forget.
        try:
            from services.audit_log import audit_log_bg
            audit_log_bg(
                event_type="gpu_sidecar_unload_failed",
                source="gpu_scheduler",
                details={
                    "phase": phase,
                    "policy": policy,
                    "results": results,
                    "failed": list(failed.keys()),
                },
                task_id=task_id,
                severity="warning" if policy == "proceed" else "error",
            )
        except Exception:
            pass

        if policy == "abort":
            failed_summary = ", ".join(
                f"{name}: {info.get('error') or 'unknown'}"
                for name, info in failed.items()
            )
            raise RuntimeError(
                f"gpu_scheduler: cooperative unload failed for phase={phase!r}; "
                f"policy=abort; sidecars: {failed_summary}",
            )
        logger.warning(
            "[GPU] cooperative unload had failures (policy=proceed) "
            "phase=%s failed=%s — claiming lock anyway",
            phase, list(failed.keys()),
        )

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
                "threshold_percent": _cfg_int(self._site_config, "gpu_busy_threshold_percent", _DEFAULT_GPU_BUSY_THRESHOLD),
                "check_interval_s": _cfg_int(self._site_config, "gpu_gaming_check_interval", _DEFAULT_GAMING_CHECK_INTERVAL),
                "confirm_checks": _cfg_int(self._site_config, "gpu_gaming_confirm_checks", _DEFAULT_GAMING_CONFIRM_CHECKS),
                "clear_checks": _cfg_int(self._site_config, "gpu_gaming_clear_checks", _DEFAULT_GAMING_CLEAR_CHECKS),
            },
        }


# Module-level singleton
gpu = GPUScheduler()
