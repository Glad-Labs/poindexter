"""
GPU Scheduler — serializes access to the shared GPU across the stack's own
model consumers (Ollama LLM inference, SDXL image gen, wan video render), and
*optionally* yields to external (non-stack) GPU workloads when sharing the box.

With a single GPU (RTX 5090, 32GB), only one large workload can run at a time.
This module provides an async lock so that:
  - Ollama LLM inference, SDXL image generation, and video render don't fight
    for VRAM
  - Before SDXL / video render starts, any loaded Ollama model is unloaded
  - Before Ollama starts, SDXL pipeline is released (if loaded)
  - Small models (embeddings) can coexist and skip the lock
  - If a non-stack app (e.g. a game) shares the GPU, optionally pause (gated;
    see "External-workload detection" below — off by default)

Cross-process locking (poindexter#731):
  The in-process ``asyncio.Lock`` only serializes within one Python process.
  When ``poindexter-worker`` and ``poindexter-prefect-worker`` both need the
  GPU they race — SDXL model-loads evict each other's Ollama models.

  The fix: a PostgreSQL ``pg_advisory_lock`` held on a DEDICATED connection
  (not a pool checkout) acts as the cross-process barrier.  Session-level
  advisory locks are tied to the connection — returning a pooled connection
  to the pool while the lock is held would silently release it and let a
  second process acquire it.  A dedicated connection is opened before each
  ``pg_advisory_lock`` call and closed in the ``finally`` block.

  The ``asyncio.Lock`` is retained as an in-process guard so coroutines
  within the same event loop still serialize cheaply without hitting PG.

External-workload detection (off by default):
  Queries the nvidia-smi prometheus exporter (host.docker.internal:9835) for GPU
  utilization. If utilization is above the threshold and we don't hold the lock,
  a NON-STACK app (e.g. a game on the same box) may be using the GPU — we wait
  until it drops. The stack is normally the only thing running models, so this is
  gated behind ``gpu_external_workload_wait_enabled`` (default false): all
  stack-internal contention is already serialized by the pg_advisory_lock +
  asyncio.Lock, and treating a sibling process's legitimate GPU use as "gaming"
  only causes phantom pauses (validation finding 4a — a genuine 99% reading from
  the stack's own non-pipeline process was mislabelled external). Operators who
  share the GPU with a game set the flag true.

Usage:
    from services.gpu_scheduler import gpu
    async with gpu.lock("ollama", model="glm-4.7-5090"):
        result = await ollama.generate(...)
"""

import asyncio
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

import httpx

# Stable int64 constant used as the pg_advisory_lock key.  Chosen to be
# unique across the application (no other caller uses this value).
# int64 range: -9223372036854775808 .. 9223372036854775807
GPU_ADVISORY_LOCK_KEY: int = 7_777_777_777

from services.logger_config import get_logger
from services.site_config import SiteConfig

logger = get_logger(__name__)

# Reentrancy guard for ``GPUScheduler.lock`` (GPU-serialize fix). Records
# whether the current async call chain already holds the GPU session, so a
# nested ``gpu.lock()`` acquire is a pass-through no-op instead of deadlocking
# the non-reentrant ``asyncio.Lock``. A ContextVar (not an instance attribute)
# because generator-based context managers run in the caller's context and
# distinct asyncio tasks get distinct context copies — preserving cross-task
# serialization while making same-chain nesting reentrant.
_gpu_session_active: ContextVar[bool] = ContextVar("gpu_session_active", default=False)

# Process-wide empty-SiteConfig fallback (#272 capstone). When no
# AppContainer has been registered (CLI early paths, import time, tests
# that never bootstrap), ``_sc()`` returns this empty instance — behaving
# exactly like the old per-module ``site_config`` global did before its
# lifespan setter fired. Never crashes when the container is unset.
_FALLBACK_SITE_CONFIG = SiteConfig()


def _sc() -> SiteConfig:
    """Return the active container's SiteConfig, or the empty fallback.

    #272 capstone: sources SiteConfig from the process-wide
    ``AppContainer`` registered by ``bootstrap.build_container`` instead
    of a module-level global wired via the retired ``set_site_config``.
    Crash-safe — returns ``_FALLBACK_SITE_CONFIG`` (an empty SiteConfig)
    when no container has been registered yet.
    """
    from services.container_registry import get_container

    container = get_container()
    return container.site_config if container is not None else _FALLBACK_SITE_CONFIG


def _sc_get(key: str, default: str = "") -> str:
    return _sc().get(key, default)


def _ollama_base_url() -> str:
    """Lazy resolve so post-lifespan changes take effect."""
    return _sc_get("ollama_base_url") or _sc_get("ollama_host") or "http://host.docker.internal:11434"


def _prometheus_query_url() -> str:
    """Base URL for Prometheus instant queries of GPU metrics.

    GPU power/util are read from Prometheus — which already scrapes and
    caches the nvidia-smi exporter — rather than hitting the exporter
    directly. Prometheus serves the last scrape instantly and never blocks
    on a slow ``nvidia-smi`` under render load (the 2026-06-21
    RemoteDisconnected), and querying it over container-internal DNS
    (``prometheus:9090``) sidesteps the Windows Docker host-port-forward
    wedge that made the direct ``host.docker.internal:9835`` read flap.
    Lazy resolve so post-lifespan settings changes take effect.
    """
    return _sc_get("gpu_metrics_prometheus_url") or "http://prometheus:9090"


# Models under this VRAM threshold (in GB) skip the lock — they can coexist.
SMALL_MODEL_THRESHOLD_GB = 2.0

# Gaming detection defaults — all overridable via app_settings (DB-first config)
_DEFAULT_GPU_BUSY_THRESHOLD = 30  # GPU utilization % to consider "in use"
_DEFAULT_GAMING_CHECK_INTERVAL = 15  # seconds between checks while waiting
_DEFAULT_GAMING_CONFIRM_CHECKS = 2  # consecutive checks above threshold to confirm
_DEFAULT_GAMING_CLEAR_CHECKS = 3  # consecutive checks below threshold to resume


def _cfg_int(key: str, default: int) -> int:
    """Read an int from site_config (DB) with fallback.

    poindexter#485 fail-loud sweep: previously a bare
    ``except Exception: return default`` silently masked SiteConfig
    failures (DB pool exhausted, missing column, etc.) as "using
    defaults". The scheduler still falls back so the lock lifecycle
    never breaks, but operators now see a warning log + persistent
    finding row for the outage. Dedup key folds repeats into one
    dispatcher notification per key.
    """
    try:
        return _sc().get_int(key, default)
    except Exception as exc:
        _emit_cfg_fetch_finding("int", key, default, exc)
        return default


def _cfg_float(key: str, default: float) -> float:
    """Read a float from site_config (DB) with fallback.

    Same fail-loud-but-recover pattern as :func:`_cfg_int`.
    """
    try:
        return _sc().get_float(key, default)
    except Exception as exc:
        _emit_cfg_fetch_finding("float", key, default, exc)
        return default


def _cfg_bool(key: str, default: bool) -> bool:
    """Read a bool from site_config (DB) with fallback.

    Same fail-loud-but-recover pattern as :func:`_cfg_int`.
    """
    try:
        return _sc().get_bool(key, default)
    except Exception as exc:
        _emit_cfg_fetch_finding("bool", key, default, exc)
        return default


def _emit_cfg_fetch_finding(
    kind: str, key: str, default: Any, exc: BaseException,
) -> None:
    """Log + emit a finding when SiteConfig.get_{int,float} raises.

    Called from the scheduler's hot path so this function never raises
    or blocks. Dedup key is keyed on (kind, key) so a transient
    SiteConfig outage during a 5s scheduler tick produces one
    operator-visible finding per affected setting rather than one per
    tick.
    """
    logger.warning(
        "[gpu_scheduler] SiteConfig.get_%s(%r) raised %s: %s — "
        "falling back to default %r",
        kind, key, type(exc).__name__, exc, default,
    )
    try:
        from utils.findings import emit_finding
        emit_finding(
            source="gpu_scheduler.cfg_fetch",
            kind="site_config_read_failed",
            severity="warning",
            title=f"gpu_scheduler cannot read {key} from SiteConfig",
            body=(
                f"SiteConfig.get_{kind}({key!r}) raised "
                f"{type(exc).__name__}: {exc}. The scheduler fell back "
                f"to its hardcoded safety default ({default!r}) so the "
                "GPU lock lifecycle stays intact, but the operator's "
                "tuned value is not in effect. Investigate the DB pool "
                "/ app_settings cache + restart the worker if site_config "
                "drift persists."
            ),
            dedup_key=f"gpu_scheduler_cfg_{kind}_{key}",
        )
    except Exception:
        # Observability path must never gate the scheduler — log + move on.
        logger.debug(
            "[gpu_scheduler] emit_finding for site_config_read_failed unavailable",
            exc_info=True,
        )


class GPUScheduler:
    """Async-safe GPU resource coordinator with gaming detection.

    Cross-process locking (poindexter#731):
        ``_lock`` (asyncio.Lock) serializes within one Python process.
        ``_pg_lock_conn`` holds a dedicated asyncpg connection that holds
        ``pg_advisory_lock(GPU_ADVISORY_LOCK_KEY)`` for the duration of a
        GPU session.  Two containers sharing one physical GPU will block on
        this Postgres-level lock even though they run in separate processes.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._current_owner: str | None = None  # "ollama", "sdxl", or "video"
        self._current_model: str | None = None
        self._acquired_at: float = 0
        self._gaming_detected: bool = False
        self._gaming_paused_since: float = 0
        self._total_gaming_paused_s: float = 0  # cumulative for metrics
        # Dedicated asyncpg connection that holds the cross-process
        # pg_advisory_lock for the duration of each GPU session.
        # None when the lock is not held.  Must NOT be a pool checkout —
        # session-level advisory locks are released when the connection
        # is returned to the pool.
        self._pg_lock_conn: "asyncpg.Connection | None" = None  # type: ignore[name-defined]  # noqa: UP037, F821
        # Lazily-initialised shared httpx client. Every public-API call
        # used to spin up a fresh ``httpx.AsyncClient(...)`` for one GET
        # (nvidia-smi exporter, Ollama /api/ps, SDXL /unload) — that's
        # TCP handshake + httpx-init overhead amortised over a single
        # request. With a shared client the underlying connection pool
        # reuses keep-alive sockets across the scheduler's ~5s-cadence
        # ticks, which matters because all four hot-path callers talk
        # to localhost services (the nvidia-smi exporter, Ollama, SDXL
        # server) on a single host port each.
        self._http_client: httpx.AsyncClient | None = None

    def _get_http_client(self) -> httpx.AsyncClient:
        """Return the shared httpx client, building it on first use.

        Per-request timeouts override the conservative default (30s)
        when callers pass ``timeout=`` explicitly, so this single
        client serves the quick health-check and slow Ollama-unload
        paths alike.
        """
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
        return self._http_client

    async def aclose(self) -> None:
        """Close shared resources. Idempotent. Called from main.py on app
        shutdown; safe to call when no client was ever built.

        If the pg advisory-lock connection is still open (e.g. shutdown
        during an active GPU session), it is closed here — Postgres will
        automatically release any session-level advisory locks held by a
        closing connection.
        """
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._http_client = None
        if self._pg_lock_conn is not None:
            try:
                await self._pg_lock_conn.close()
            except Exception:
                pass
            self._pg_lock_conn = None

    # ------------------------------------------------------------------
    # Cross-process pg_advisory_lock helpers (poindexter#731)
    # ------------------------------------------------------------------

    async def _acquire_pg_advisory_lock(self) -> None:
        """Open a dedicated asyncpg connection and acquire the session-level
        GPU advisory lock.

        A DEDICATED connection (not a pool checkout) is required because
        session-level advisory locks are tied to the connection lifetime.
        Returning a connection to a pool while holding an advisory lock
        silently releases the lock — another process could then acquire it
        while our session still believes it holds the lock.

        The connection is stored on ``self._pg_lock_conn`` so
        ``_release_pg_advisory_lock`` can unlock + close it.

        If Postgres is unavailable (DSN not resolved, network error) this
        logs a warning and falls back to the in-process asyncio.Lock only —
        the scheduler must remain functional in test environments and on
        first-boot before the DB is reachable.
        """
        try:
            import asyncpg
            from brain.bootstrap import resolve_database_url
        except ImportError:
            logger.debug("[GPU] asyncpg/brain.bootstrap unavailable — skipping pg advisory lock")
            return

        dsn = resolve_database_url()
        if not dsn:
            logger.warning(
                "[GPU] database_url not resolved — cross-process GPU lock unavailable; "
                "two containers may race. Configure database_url in bootstrap.toml."
            )
            return

        conn = None
        try:
            conn = await asyncpg.connect(dsn)
            await conn.execute("SELECT pg_advisory_lock($1)", GPU_ADVISORY_LOCK_KEY)
            self._pg_lock_conn = conn
            logger.debug("[GPU] pg_advisory_lock acquired (key=%d)", GPU_ADVISORY_LOCK_KEY)
        except Exception as exc:
            logger.warning(
                "[GPU] pg_advisory_lock acquire failed (%s: %s) — "
                "falling back to process-local lock only",
                type(exc).__name__, exc,
            )
            # If we opened a connection before the lock call failed, close it
            # so it doesn't leak.
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass

    async def _release_pg_advisory_lock(self) -> None:
        """Release the session-level GPU advisory lock and close the dedicated
        connection.

        Idempotent — safe to call when no connection is held.
        """
        conn = self._pg_lock_conn
        self._pg_lock_conn = None
        if conn is None:
            return
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", GPU_ADVISORY_LOCK_KEY)
            logger.debug("[GPU] pg_advisory_lock released (key=%d)", GPU_ADVISORY_LOCK_KEY)
        except Exception as exc:
            logger.warning(
                "[GPU] pg_advisory_unlock failed (%s: %s) — closing connection anyway",
                type(exc).__name__, exc,
            )
        finally:
            try:
                await conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public lock context manager
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def lock(
        self,
        owner: str,
        model: str | None = None,
        task_id: str | None = None,
        phase: str | None = None,
    ):
        """Acquire exclusive GPU access.

        Two-tier locking (poindexter#731):
          1. asyncio.Lock — in-process serialization (cheap, fast).
          2. pg_advisory_lock — cross-process serialization via Postgres
             (blocks a second container from acquiring the GPU while the
             first holds it).  Held on a dedicated asyncpg connection for
             the full duration of the GPU session.

        Waits for any gaming/external workload to finish before acquiring.

        Args:
            owner: "ollama" or "sdxl"
            model: model name (for logging/tracking)
            task_id: optional pipeline task UUID — when set, a row is
                written to ``gpu_task_sessions`` on release so the
                feedback loop (internal tracker Phase 3.A3) can attribute GPU
                utilisation + electricity cost to the originating task.
            phase: optional pipeline phase label (e.g. "generate_content",
                "featured_image"). Defaults to ``owner`` when unset.
        """
        # Reentrancy (GPU-serialize fix): if this async call chain already
        # holds the GPU session, a nested acquire is a pass-through no-op — no
        # second asyncio.Lock / pg_advisory_lock acquire, no second Ollama
        # eviction. This lets dispatch_complete wrap every local LLM call in
        # gpu.lock("ollama") even inside content stages that already hold it,
        # without deadlocking on the non-reentrant asyncio.Lock. Distinct
        # asyncio tasks get distinct context copies, so cross-task
        # serialization is preserved.
        if _gpu_session_active.get():
            yield
            return

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

        # Acquire in-process lock first (fast path for same-process callers)
        await self._lock.acquire()

        # Then acquire the cross-process pg advisory lock so a second
        # container blocks here until we release.
        await self._acquire_pg_advisory_lock()

        wait_msg = " (waited)" if waited else ""
        logger.info("GPU acquired%s", wait_msg, owner=owner, model=model)

        self._current_owner = owner
        self._current_model = model
        self._acquired_at = time.monotonic()
        session_start = datetime.now(timezone.utc)

        # Mark the GPU session active so nested gpu.lock() calls within this
        # async chain (e.g. dispatch_complete inside a stage) are no-ops.
        token = _gpu_session_active.set(True)
        try:
            # Prepare GPU for the new owner. The video render is a wan + SDXL
            # consumer (no Ollama of its own), so it evicts Ollama exactly like
            # SDXL does to free VRAM for the render — validation finding 4b: the
            # render path never went through the lock, so the 18GB writer/director
            # stayed resident and starved wan+SDXL, failing the render.
            if owner in ("sdxl", "video"):
                await self._unload_ollama_models()
            yield
        finally:
            duration = time.monotonic() - self._acquired_at
            logger.info("GPU released", owner=owner, model=model, duration_s=round(duration, 1))
            self._current_owner = None
            self._current_model = None
            # Release pg advisory lock BEFORE releasing the in-process lock
            # so that the cross-process barrier stays up until we are done.
            await self._release_pg_advisory_lock()
            self._lock.release()
            _gpu_session_active.reset(token)
            # internal tracker Phase 3.A3 — record the session so model/phase
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
        """Insert a row into gpu_task_sessions for internal tracker Phase 3.A3.

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

    async def _emit_exporter_finding(self, metric: str, detail: str) -> None:
        """Surface an nvidia-smi-exporter unreachability finding so the
        operator hears about persistently-broken telemetry instead of
        the scheduler silently treating it as "GPU idle".

        Dedup key folds repeated identical failures into one alert per
        operator cycle; the brain dispatcher applies its own dedup on
        top so this is upper-bound noise control.
        """
        try:
            from utils.findings import emit_finding
            emit_finding(
                source="gpu_scheduler",
                kind="nvidia_exporter_unreachable",
                severity="warning",
                title=(
                    f"GPU scheduler cannot read {metric} from Prometheus"
                ),
                body=(
                    f"Prometheus instant query for {metric} failed: {detail} "
                    f"(GET {_prometheus_query_url()}/api/v1/query). The "
                    "scheduler treats the missing reading as 'idle' and "
                    "proceeds (poindexter#455 — fail-loud, not silent). "
                    "Check the poindexter-prometheus container and that the "
                    "nvidia-smi-host scrape target is up."
                ),
                dedup_key=f"nvidia_exporter_unreachable_{metric}",
            )
        except Exception:
            # findings emission is observability — never gate the scheduler
            # path on a failed alert write. Log + move on.
            logger.debug(
                "emit_finding unavailable in gpu_scheduler", exc_info=True,
            )

    async def _query_prometheus_scalar(self, metric: str) -> float | None:
        """Return the latest scalar value of ``metric`` from Prometheus, or None.

        Runs an instant query against the Prometheus HTTP API and reads
        ``data.result[0].value[1]``. A genuine connectivity / non-200 failure
        emits the operator finding (telemetry is broken); an empty result
        (Prometheus is up but has no recent scrape of the metric) returns None
        quietly — a transient scrape gap is not a pageable outage.
        """
        url = f"{_prometheus_query_url()}/api/v1/query"
        try:
            client = self._get_http_client()
            resp = await client.get(url, params={"query": metric}, timeout=5)
            if resp.status_code != 200:
                logger.warning(
                    "[GPU] Prometheus query %s returned HTTP %s — reading unavailable",
                    metric, resp.status_code,
                )
                await self._emit_exporter_finding(metric, f"HTTP {resp.status_code}")
                return None
            payload = resp.json()
            result = (payload.get("data") or {}).get("result") or []
            if not result:
                logger.debug(
                    "[GPU] Prometheus has no series for %s yet (no recent scrape)",
                    metric,
                )
                return None
            # Instant-vector sample: value = [<unix_ts>, "<scalar as string>"].
            return float(result[0]["value"][1])
        except Exception as exc:
            logger.warning(
                "[GPU] Prometheus unreachable for %s: %s: %s",
                metric, type(exc).__name__, exc,
            )
            await self._emit_exporter_finding(metric, f"{type(exc).__name__}: {exc}")
            return None

    async def _get_gpu_power_watts(self) -> float | None:
        """Current GPU power draw (watts), via Prometheus."""
        return await self._query_prometheus_scalar("nvidia_gpu_power_draw_watts")

    async def _get_gpu_utilization(self) -> float | None:
        """Current GPU utilization (%), via Prometheus."""
        return await self._query_prometheus_scalar("nvidia_gpu_utilization_percent")

    async def _wait_for_gaming_clear(self):
        """Block until GPU is not being used by an external workload (gaming).

        Uses consecutive checks to avoid false positives from brief GPU spikes.
        All thresholds are DB-configurable via app_settings.

        Guard: if the pipeline already holds the GPU lock (self._current_owner
        is set), any high utilization is ours — not a game.  Without this check
        a queued task would see the running task's Ollama inference as "gaming"
        and stall for confirm_checks + clear_checks intervals (poindexter#579).
        """
        if self._current_owner is not None:
            return

        # The stack is the only thing running models on this GPU, so all
        # cross-process contention is already serialized by the pg_advisory_lock
        # (and the in-process asyncio.Lock for same-process callers). A sibling
        # stack process holding the GPU is NOT an external workload — treating its
        # high utilisation as "gaming" here is what produced the 407s phantom
        # pause (validation finding 4a). The util-based wait below only makes
        # sense when the operator SHARES this GPU with a non-stack app (e.g. a
        # game on the same box); gated off by default.
        if not _cfg_bool("gpu_external_workload_wait_enabled", False):
            return

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
                logger.info("[GPU] External GPU workload cleared — resuming pipeline (paused %.0fs)", pause_duration)
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
            logger.info("[GPU] External/unowned GPU workload detected (util=%.0f%%) — pausing pipeline", util)

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
        logger.info("[GPU] External GPU workload cleared — resuming pipeline (paused %.0fs)", pause_duration)
        self._gaming_detected = False

    async def _unload_ollama_models(self):
        """Unload all Ollama models to free VRAM for SDXL."""
        try:
            client = self._get_http_client()
            resp = await client.get(f"{_ollama_base_url()}/api/ps", timeout=10)
            if resp.status_code != 200:
                return
            data = resp.json()
            for model in data.get("models", []):
                name = model["name"]
                logger.info("Unloading Ollama model for SDXL", model=name)
                await client.post(
                    f"{_ollama_base_url()}/api/generate",
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
        from services.bootstrap_defaults import DEFAULT_SDXL_URL
        sdxl_url = _sc_get("sdxl_server_url", DEFAULT_SDXL_URL)
        try:
            client = self._get_http_client()
            resp = await client.post(f"{sdxl_url}/unload", timeout=10)
            if resp.status_code == 200:
                logger.info("[GPU] SDXL model unloaded via /unload endpoint")
        except Exception as exc:
            # poindexter#455 — used to be `except: pass`. Log at debug
            # because the SDXL server being offline is the common case
            # (it's only running when SDXL phase is active), not a
            # genuine bug. A persistent failure would surface via the
            # nvidia-exporter finding when SDXL is supposed to be up.
            logger.debug(
                "[GPU] SDXL /unload call failed (server likely offline): %s: %s",
                type(exc).__name__, exc,
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
            # poindexter#731 — cross-process lock observability
            "pg_advisory_lock_held": self._pg_lock_conn is not None,
            "pg_advisory_lock_key": GPU_ADVISORY_LOCK_KEY,
            "config": {
                "threshold_percent": _cfg_int("gpu_busy_threshold_percent", _DEFAULT_GPU_BUSY_THRESHOLD),
                "check_interval_s": _cfg_int("gpu_gaming_check_interval", _DEFAULT_GAMING_CHECK_INTERVAL),
                "confirm_checks": _cfg_int("gpu_gaming_confirm_checks", _DEFAULT_GAMING_CONFIRM_CHECKS),
                "clear_checks": _cfg_int("gpu_gaming_clear_checks", _DEFAULT_GAMING_CLEAR_CHECKS),
            },
        }


# Module-level singleton
gpu = GPUScheduler()
