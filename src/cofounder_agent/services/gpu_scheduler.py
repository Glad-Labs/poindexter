"""
GPU Scheduler — serializes access to the shared GPU across the stack's own
model consumers (Ollama LLM inference, image-gen image gen, wan video render), and
*optionally* yields to external (non-stack) GPU workloads when sharing the box.

With a single GPU (RTX 5090, 32GB), only one large workload can run at a time.
This module provides an async lock so that:
  - Ollama LLM inference, image-gen image generation, and video render don't fight
    for VRAM
  - Before image-gen / video render starts, any loaded Ollama model is unloaded
  - Before Ollama starts, image-gen pipeline is released (if loaded)
  - If a non-stack app (e.g. a game) shares the GPU, optionally pause (gated;
    see "External-workload detection" below — off by default)

No size-based exemption (poindexter#872):
  Every caller of ``lock()`` serializes regardless of model size. The
  components that DO coexist with a resident model — the embedding path, the
  sentence-transformers reranker — do so by never taking the lock at all.
  That is a known VRAM HAZARD (they stack on the resident writer at the pinch
  point; see
  docs/superpowers/specs/2026-06-22-single-gpu-vram-budget-stability-design.md),
  not a designed exemption. An unread ``SMALL_MODEL_THRESHOLD_GB = 2.0``
  constant advertised the opposite here until 2026-07-17; it was deleted
  rather than implemented, because letting small models stack on an 18 GB
  resident writer is precisely the WDDM sysmem-fallback that freezes the
  desktop. A real coexist path would need a live free-VRAM headroom check,
  not a static size threshold.

Cross-process locking (poindexter#731):
  The in-process ``asyncio.Lock`` only serializes within one Python process.
  When ``poindexter-worker`` and ``poindexter-prefect-worker`` both need the
  GPU they race — image-gen model-loads evict each other's Ollama models.

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
import itertools
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

# Stable int64 constant used as the pg_advisory_lock key.  Chosen to be
# unique across the application (no other caller uses this value).
# int64 range: -9223372036854775808 .. 9223372036854775807
GPU_ADVISORY_LOCK_KEY: int = 7_777_777_777

from services.llm_providers.ollama_unload import unload_loaded_ollama_models
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

# Strong references to in-flight gpu_lease_stats capture tasks (P0,
# poindexter#914). asyncio only weakly references scheduled tasks; without
# this set a capture task can be garbage-collected before it runs.
_stats_capture_tasks: set = set()

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


# Gaming detection defaults — all overridable via app_settings (DB-first config)
_DEFAULT_GPU_BUSY_THRESHOLD = 30  # GPU utilization % to consider "in use"
_DEFAULT_GAMING_CHECK_INTERVAL = 15  # seconds between checks while waiting
_DEFAULT_GAMING_CONFIRM_CHECKS = 2  # consecutive checks above threshold to confirm
_DEFAULT_GAMING_CLEAR_CHECKS = 3  # consecutive checks below threshold to resume

# poindexter#807 — lock acquisition/release must be bounded. 900s tolerates a
# legitimate long holder (a video render holds gpu.lock("video") for its whole
# duration, ~15-30 min worst case) while still guaranteeing a wedged holder
# (zombie process from a force-crashed flow run still holding the pg advisory
# lock) can't block a graph node forever. Operators tune via
# app_settings.gpu_lock_acquire_timeout_seconds; 0 restores the legacy
# unbounded wait.
_DEFAULT_LOCK_ACQUIRE_TIMEOUT_S = 900
_DEFAULT_LOCK_RELEASE_TIMEOUT_S = 15


class GpuLockTimeoutError(TimeoutError):
    """gpu.lock() acquisition exceeded gpu_lock_acquire_timeout_seconds.

    Raised instead of waiting forever behind a wedged lock holder. GPU call
    sites that are fail-soft (image_captioner, vision QA) catch this via
    their existing ``except Exception`` and skip; hard callers (the writer)
    fail the node loudly, which routes into the normal retry path instead of
    an invisible stall that the brain probe has to force-crash.
    """


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


def qa_rail_wait_budget_s() -> float | None:
    """Wait budget (s) for fail-soft QA rails, or ``None`` to stay legacy.

    poindexter#914 P2 caller migration. QA rails are the first group to opt
    into admission because they are the cheapest to skip and the most
    expensive to block: a rail queued behind an image render waits on a
    ~230s p90 hold (``gpu_lease_stats``, 07-26..29 soak) and may burn the
    full 900s lock ceiling, while the rail's own work is seconds — the
    ``qa_ragas_judge`` p90 over 336 samples is 18.4s.

    ``0`` disables the budget (back to the unbounded legacy contract), which
    is also the escape hatch if skipping proves too aggressive in practice.
    The default is deliberately larger than a typical writer hold but smaller
    than an image-render hold, so a rail waits behind ordinary LLM traffic and
    skips behind a render.
    """
    budget = _cfg_float("gpu_sched_qa_rail_max_wait_s", 45.0)
    return budget if budget > 0 else None


def media_wait_budget_s() -> float | None:
    """Wait budget (s) for the fail-soft media stages, or ``None`` for legacy.

    poindexter#914 P2 caller migration, group 2. Same contract as
    :func:`qa_rail_wait_budget_s`, different number: these stages hold the
    GPU far longer than a QA rail, so the budget sits higher.

    All three media stages degrade without failing the post — the scripts
    stage logs "non-fatal" and continues, both video stages return ``None``
    and ship the post without a shot list — so skipping one under contention
    costs a nice-to-have, while blocking burns the 900s lock ceiling on an
    article that is otherwise finished.

    The default sits in the measured gap (``gpu_lease_stats``, 07-26..30):
    ABOVE ordinary LLM traffic they should queue behind (``generate_content``
    p90 105.3s) and BELOW every long holder they should skip behind —
    ``qa_rewrite`` 210.5s, ``featured_image`` 228.7s, ``inline_image_batch``
    240.0s, ``media_render`` 383.5s. ``0`` restores the unbounded legacy
    contract.
    """
    budget = _cfg_float("gpu_sched_media_max_wait_s", 120.0)
    return budget if budget > 0 else None


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
            severity="warn",
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
        # silent-ok: this IS the finding path (site_config_read_failed above) —
        # a failure emitting it can't be surfaced without recursing, and the
        # observability path must never gate the scheduler.
        logger.debug(
            "[gpu_scheduler] emit_finding for site_config_read_failed unavailable",
            exc_info=True,
        )


# Priority classes for the in-process wait queue (poindexter#914 P1, spec §2):
# pipeline (graph nodes) > operator (console/MCP-triggered) > background
# (scheduled jobs: taps, SEO, newsletter). Lower rank wakes first. Unknown
# strings map to the pipeline rank — a typo must never demote a graph node.
_PRIORITY_RANKS: dict[str, int] = {"pipeline": 0, "operator": 1, "background": 2}


def _default_aging_seconds() -> int:
    return _cfg_int("gpu_sched_aging_seconds", 300)


@dataclass
class _GateWaiter:
    rank: int
    seq: int
    enqueued: float
    fut: "asyncio.Future[bool]" = field(compare=False)


class _PriorityGate:
    """asyncio.Lock-compatible gate with priority-class wakeup + aging.

    Surface-identical to ``asyncio.Lock`` (``acquire``/``release``/``locked``)
    because ``lock()`` and several tests use exactly that trio — including
    direct ``gpu._lock.acquire()`` pokes, which enter at the default (pipeline)
    rank. **Neutrality proof (P1):** every legacy caller acquires at one rank,
    so the wake order is ``(rank, seq)`` with a constant rank — strict FIFO,
    behaviorally identical to ``asyncio.Lock``. Priority only reorders once
    callers opt into ``gpu.lock(..., priority=...)`` classes.

    Aging (spec §2): a parked waiter's effective rank drops by 1 per full
    ``gpu_sched_aging_seconds`` waited (floored at 0), so a ``background``
    waiter is eventually promoted past a stream of later ``pipeline`` arrivals
    — starvation-proof without ever jumping a same-rank elder (ties break on
    enqueue sequence). The aging window is read lazily at each wake so the DB
    setting applies without a restart; tests inject their own callable.
    """

    def __init__(self, *, aging_seconds: Callable[[], int] | None = None):
        self._held = False
        self._waiters: list[_GateWaiter] = []
        self._seq = itertools.count()
        self._aging_seconds = aging_seconds or _default_aging_seconds

    def locked(self) -> bool:
        return self._held

    async def acquire(self, *, rank: int = 0) -> bool:
        if not self._held and not self._waiters:
            self._held = True
            return True
        waiter = _GateWaiter(
            rank=rank,
            seq=next(self._seq),
            enqueued=time.monotonic(),
            fut=asyncio.get_running_loop().create_future(),
        )
        self._waiters.append(waiter)
        try:
            await waiter.fut
        except asyncio.CancelledError:
            if waiter.fut.done() and not waiter.fut.cancelled():
                # The grant landed concurrently with our cancellation (the
                # classic asyncio.Lock race): we technically held the gate for
                # an instant — hand it straight to the next waiter so the
                # grant chain never wedges.
                self._held = False
                self._wake_next()
            else:
                self._discard(waiter)
            raise
        return True

    def release(self) -> None:
        if not self._held:
            raise RuntimeError("_PriorityGate.release() called on an unheld gate")
        self._held = False
        self._wake_next()

    def _discard(self, waiter: _GateWaiter) -> None:
        try:
            self._waiters.remove(waiter)
        except ValueError:
            # silent-ok: already dropped by a concurrent _wake_next sweep —
            # double-removal is the expected race, not a fault.
            pass

    def _effective_rank(self, waiter: _GateWaiter, now: float, aging_s: int) -> int:
        if aging_s <= 0 or waiter.rank <= 0:
            return waiter.rank
        promoted = int((now - waiter.enqueued) // aging_s)
        return max(0, waiter.rank - promoted)

    def _wake_next(self) -> None:
        # Sweep waiters whose future is already done (cancelled while parked).
        self._waiters = [w for w in self._waiters if not w.fut.done()]
        if self._held or not self._waiters:
            return
        try:
            aging_s = int(self._aging_seconds())
        except Exception:
            # silent-ok: a broken cfg read degrades to no aging (strict class
            # order); the cfg reader itself already emits the operator finding.
            aging_s = 0
        now = time.monotonic()
        winner = min(
            self._waiters,
            key=lambda w: (self._effective_rank(w, now, aging_s), w.seq),
        )
        self._waiters.remove(winner)
        self._held = True
        winner.fut.set_result(True)


class GPUScheduler:
    """Async-safe GPU resource coordinator with gaming detection.

    Cross-process locking (poindexter#731):
        ``_lock`` (asyncio.Lock) serializes within one Python process.
        ``_pg_lock_conn`` holds a dedicated asyncpg connection that holds
        ``pg_advisory_lock(GPU_ADVISORY_LOCK_KEY)`` for the duration of a
        GPU session.  Two containers sharing one physical GPU will block on
        this Postgres-level lock even though they run in separate processes.
    """

    def __init__(self) -> None:
        # _PriorityGate is asyncio.Lock-surface-compatible; all legacy callers
        # enter at one rank so ordering stays strict FIFO (poindexter#914 P1).
        self._lock = _PriorityGate()
        self._current_owner: str | None = None  # "ollama", "image_gen", or "video"
        self._current_model: str | None = None
        self._current_phase: str | None = None
        # Lazily-built GPURegistry for admission's free/evictable VRAM reads.
        self._registry: Any = None
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
        # (nvidia-smi exporter, Ollama /api/ps, image-gen /unload) — that's
        # TCP handshake + httpx-init overhead amortised over a single
        # request. With a shared client the underlying connection pool
        # reuses keep-alive sockets across the scheduler's ~5s-cadence
        # ticks, which matters because all four hot-path callers talk
        # to localhost services (the nvidia-smi exporter, Ollama, image-gen
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
                # silent-ok: teardown close — the reference is dropped to None
                # immediately below regardless, so a failed close leaks no
                # usable handle and there is nothing for an operator to act on.
                pass
            self._pg_lock_conn = None

    # ------------------------------------------------------------------
    # Cross-process pg_advisory_lock helpers (poindexter#731)
    # ------------------------------------------------------------------

    async def _acquire_pg_advisory_lock(self, timeout_s: float | None = None) -> None:
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

        ``timeout_s`` (poindexter#807) bounds the wait for the advisory lock.
        A timeout means another SESSION HOLDS the lock (not that Postgres is
        down), so falling back to the local lock would break cross-process
        mutual exclusion — instead the connection is terminated and
        :class:`GpuLockTimeoutError` is raised for the caller to handle.
        ``None``/0 keeps the legacy unbounded wait.
        """
        try:
            import asyncpg  # type: ignore[import-untyped]
            from brain.bootstrap import resolve_database_url  # type: ignore[import-untyped]
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
            if timeout_s and timeout_s > 0:
                conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=timeout_s)
                await asyncio.wait_for(
                    conn.execute("SELECT pg_advisory_lock($1)", GPU_ADVISORY_LOCK_KEY),
                    timeout=timeout_s,
                )
            else:
                conn = await asyncpg.connect(dsn)
                await conn.execute("SELECT pg_advisory_lock($1)", GPU_ADVISORY_LOCK_KEY)
            self._pg_lock_conn = conn
            logger.debug("[GPU] pg_advisory_lock acquired (key=%d)", GPU_ADVISORY_LOCK_KEY)
        except TimeoutError:
            # terminate() (not close()) — the session is mid-`pg_advisory_lock`
            # wait, so a graceful close would block behind the same wait.
            # Dropping the socket makes Postgres abandon the lock request.
            if conn is not None:
                try:
                    conn.terminate()
                except Exception:
                    logger.warning(
                        "[GPU] terminate() after pg acquire timeout failed", exc_info=True
                    )
            raise GpuLockTimeoutError(
                f"pg_advisory_lock wait exceeded {timeout_s}s — another "
                "process holds the GPU lock (wedged holder or long render)"
            ) from None
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
                    # silent-ok: cleanup of the connection whose lock-acquire
                    # failure the outer handler ALREADY logged as a warning
                    # above — the operator-facing signal is emitted; this is
                    # just tidying up after it.
                    pass

    async def _release_pg_advisory_lock(self) -> None:
        """Release the session-level GPU advisory lock and close the dedicated
        connection.

        Idempotent — safe to call when no connection is held.

        Bounded (poindexter#807): a hung ``pg_advisory_unlock`` here used to
        hang the lock's ``finally`` block — which meant even a stage-level
        ``asyncio.wait_for`` timeout could never complete its cancellation
        and the node blocked forever. On timeout the connection is
        terminated instead; Postgres releases session advisory locks when
        the session disconnects, so terminate-on-timeout is safe.
        """
        conn = self._pg_lock_conn
        self._pg_lock_conn = None
        if conn is None:
            return
        release_timeout = _cfg_int(
            "gpu_lock_release_timeout_seconds", _DEFAULT_LOCK_RELEASE_TIMEOUT_S
        )
        try:
            if release_timeout > 0:
                await asyncio.wait_for(
                    conn.execute("SELECT pg_advisory_unlock($1)", GPU_ADVISORY_LOCK_KEY),
                    timeout=release_timeout,
                )
            else:
                await conn.execute(
                    "SELECT pg_advisory_unlock($1)", GPU_ADVISORY_LOCK_KEY
                )
            logger.debug("[GPU] pg_advisory_lock released (key=%d)", GPU_ADVISORY_LOCK_KEY)
        except TimeoutError:
            logger.warning(
                "[GPU] pg_advisory_unlock timed out after %ss — terminating "
                "connection (server releases session advisory locks on "
                "disconnect)",
                release_timeout,
            )
            try:
                conn.terminate()
            except Exception:
                logger.warning(
                    "[GPU] terminate() after pg release timeout failed", exc_info=True
                )
            return
        except Exception as exc:
            logger.warning(
                "[GPU] pg_advisory_unlock failed (%s: %s) — closing connection anyway",
                type(exc).__name__, exc,
            )
        try:
            await asyncio.wait_for(
                conn.close(), timeout=release_timeout if release_timeout > 0 else None
            )
        except Exception:
            try:
                conn.terminate()
            except Exception:
                logger.warning(
                    "[GPU] terminate() after close failure failed", exc_info=True
                )

    def _emit_lock_timeout_finding(
        self,
        *,
        owner: str,
        stage: str,
        timeout_s: float,
        holder: str | None,
    ) -> None:
        """Emit a warn ``gpu_lock_timeout`` finding. Never raises.

        Routed via the seeded ``findings.gpu_lock_timeout.delivery`` policy
        so lock-wait exhaustion is operator-visible (poindexter#807 — the
        stall→crash→requeue loop was previously silent).
        """
        try:
            from utils.findings import emit_finding

            emit_finding(
                source="gpu_scheduler",
                kind="gpu_lock_timeout",
                title=f"GPU lock wait timed out ({owner}, {stage})",
                body=(
                    f"gpu.lock({owner!r}) gave up after {timeout_s}s at the "
                    f"{stage} step"
                    + (f" — in-process holder was {holder!r}" if holder else "")
                    + ". A wedged cross-process holder (e.g. a zombie from a "
                    "force-crashed flow run) or an unusually long render is "
                    "monopolising the GPU. The caller received "
                    "GpuLockTimeoutError instead of blocking forever."
                ),
                severity="warn",
                dedup_key=f"gpu-lock-timeout:{owner}",
                extra={"owner": owner, "stage": stage, "timeout_s": timeout_s},
            )
        except Exception:
            logger.warning("[GPU] emit gpu_lock_timeout finding failed", exc_info=True)

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
        max_wait_s: float | None = None,
        priority: str = "pipeline",
    ):
        """Acquire exclusive GPU access.

        Two-tier locking (poindexter#731):
          1. _PriorityGate (asyncio.Lock-equivalent for legacy callers) —
             in-process serialization (cheap, fast).
          2. pg_advisory_lock — cross-process serialization via Postgres
             (blocks a second container from acquiring the GPU while the
             first holds it).  Held on a dedicated asyncpg connection for
             the full duration of the GPU session.

        Waits for any gaming/external workload to finish before acquiring.

        Wait contracts (poindexter#914 P1 — spec: docs/superpowers/specs/
        2026-07-26-gpu-scheduler-queue-admission-design.md):
          - ``max_wait_s`` opts this call into ADMISSION: before any wait, the
            pure calculator (``services.gpu_admission.decide``) estimates the
            holder's remaining time from its ``gpu_lease_stats`` p90 and
            checks VRAM fit; a hopeless wait raises :class:`GpuBusyError`
            IMMEDIATELY (honest skip) instead of burning the budget, and the
            budget also caps the actual lock wait. ``None`` (the default) is
            the legacy unbounded-contract path — admission never runs.
          - ``priority`` orders the in-process wait queue: ``pipeline`` >
            ``operator`` > ``background``, FIFO within a class, background
            aged upward after ``gpu_sched_aging_seconds``.
          - Both are DOUBLY inert until ``app_settings.gpu_sched_enabled``
            is true AND a caller passes a budget — no production call site
            does yet (P2 migrates them group by group).

        Args:
            owner: "ollama" or "image_gen"
            model: model name (for logging/tracking)
            task_id: optional pipeline task UUID — when set, a row is
                written to ``gpu_task_sessions`` on release so the
                feedback loop (internal tracker Phase 3.A3) can attribute GPU
                utilisation + electricity cost to the originating task.
            phase: optional pipeline phase label (e.g. "generate_content",
                "featured_image"). Defaults to ``owner`` when unset.
            max_wait_s: this caller's wait budget in seconds (None = legacy).
            priority: wait-queue class — "pipeline" | "operator" | "background".
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

        # Admission (poindexter#914 P1) — BEFORE any wait, including the
        # gaming check. Doubly gated: the caller must declare a budget AND
        # the operator must have flipped gpu_sched_enabled. A reject raises
        # GpuBusyError here; "grant_after_unload" defers its eviction until
        # the lock is actually held (racing an unload against the current
        # holder would evict the model it is mid-inference on).
        evict_before_yield = False
        if max_wait_s is not None and _cfg_bool("gpu_sched_enabled", False):
            decision = await self._admission_check(
                owner=owner, model=model, phase=phase, max_wait_s=max_wait_s
            )
            evict_before_yield = decision.action == "grant_after_unload"

        # Wait for gaming to stop before acquiring lock
        await self._wait_for_gaming_clear()

        waited = False
        queue_row_id: str | None = None
        if self._lock.locked():
            logger.info(
                "GPU busy — waiting",
                waiting_for=owner,
                current_owner=self._current_owner,
                current_model=self._current_model,
            )
            waited = True
            # GPU-scheduler P0 (poindexter#914): mirror this waiter into the
            # gpu_queue table so the console/Grafana can show the queue
            # cross-process. Contended branch only — the uncontended fast
            # path stays zero-I/O. Best-effort: enqueue returns None on any
            # failure and the wait proceeds regardless. (pg-advisory-stage
            # waits are not mirrored in P0 — in practice all observed
            # contention is in-process within prefect-worker; revisit with
            # the P4 lease-table gate if cross-process waits become common.)
            try:
                from services.gpu_queue_mirror import enqueue as _queue_enqueue

                queue_row_id = await _queue_enqueue(
                    owner, model=model, phase=phase, priority=priority
                )
            except Exception:
                # silent-ok: mirroring is observability — the wait itself is
                # untouched; the orphan reap covers anything half-written.
                queue_row_id = None

        # poindexter#807 — bounded acquisition. An unbounded wait here let a
        # graph node block forever behind a wedged holder; the brain probe
        # then force-crashed the whole flow run and the sweep requeued the
        # task into the same wall (an invisible crash→requeue loop). Timing
        # out raises a typed error the caller can handle instead.
        # The outer try/finally dequeues the P0 queue-mirror row on EVERY
        # wait outcome — acquire, timeout, or cancellation (poindexter#914).
        try:
            acquire_timeout: float = _cfg_int(
                "gpu_lock_acquire_timeout_seconds", _DEFAULT_LOCK_ACQUIRE_TIMEOUT_S
            )
            # An admission-contract caller's budget also CAPS the real wait
            # (min with the operator ceiling) — poindexter#914 P1. Only under
            # the same double gate as admission itself, so legacy behavior is
            # untouched while gpu_sched_enabled is false.
            if (
                max_wait_s is not None
                and max_wait_s > 0
                and _cfg_bool("gpu_sched_enabled", False)
            ):
                acquire_timeout = (
                    min(acquire_timeout, max_wait_s) if acquire_timeout > 0 else max_wait_s
                )
            rank = _PRIORITY_RANKS.get(priority, 0)
            acquire_started = time.monotonic()

            # Acquire in-process lock first (fast path for same-process callers)
            if acquire_timeout > 0:
                try:
                    await asyncio.wait_for(
                        self._lock.acquire(rank=rank), timeout=acquire_timeout
                    )
                except TimeoutError:
                    self._emit_lock_timeout_finding(
                        owner=owner,
                        stage="in_process",
                        timeout_s=acquire_timeout,
                        holder=self._current_owner,
                    )
                    raise GpuLockTimeoutError(
                        f"gpu.lock({owner!r}) timed out after {acquire_timeout}s "
                        f"waiting for in-process holder "
                        f"{self._current_owner!r} ({self._current_model!r})"
                    ) from None
            else:
                await self._lock.acquire(rank=rank)

            # Then acquire the cross-process pg advisory lock so a second
            # container blocks here until we release. Spend whatever remains of
            # the acquire budget (floor 1s so a slow in-process wait can't turn
            # the pg step into an instant failure).
            pg_timeout: float | None = None
            if acquire_timeout > 0:
                pg_timeout = max(
                    acquire_timeout - (time.monotonic() - acquire_started), 1.0
                )
            try:
                await self._acquire_pg_advisory_lock(timeout_s=pg_timeout)
            except GpuLockTimeoutError:
                # Never hold the in-process lock after a failed acquire — that
                # would wedge every later caller in THIS process too.
                self._lock.release()
                self._emit_lock_timeout_finding(
                    owner=owner,
                    stage="pg_advisory",
                    timeout_s=acquire_timeout,
                    holder=None,
                )
                raise
        finally:
            if queue_row_id is not None:
                try:
                    from services.gpu_queue_mirror import dequeue as _queue_dequeue

                    await _queue_dequeue(queue_row_id)
                except Exception:
                    # silent-ok: a leaked row is reaped by the orphan horizon;
                    # the wait outcome itself must propagate untouched.
                    logger.debug("gpu_queue mirror dequeue failed")

        wait_msg = " (waited)" if waited else ""
        logger.info("GPU acquired%s", wait_msg, owner=owner, model=model)

        self._current_owner = owner
        self._current_model = model
        self._current_phase = phase or owner
        self._acquired_at = time.monotonic()
        session_start = datetime.now(UTC)

        # Mark the GPU session active so nested gpu.lock() calls within this
        # async chain (e.g. dispatch_complete inside a stage) are no-ops.
        token = _gpu_session_active.set(True)
        try:
            # Prepare GPU for the new owner. The video render is a wan + image-gen
            # consumer (no Ollama of its own), so it evicts Ollama exactly like
            # image-gen does to free VRAM for the render — validation finding 4b: the
            # render path never went through the lock, so the 18GB writer/director
            # stayed resident and starved wan+image-gen, failing the render.
            if owner in ("image_gen", "video"):
                await self._unload_ollama_models()
            elif evict_before_yield:
                # Admission said the model only fits with the resident Ollama
                # models evicted (grant_after_unload) — load→compute→unload
                # doctrine, poindexter#914 P1. Safe here: the lock is held, so
                # nothing is mid-inference on what we evict.
                await self._unload_ollama_models()
            yield
        finally:
            duration = time.monotonic() - self._acquired_at
            logger.info("GPU released", owner=owner, model=model, duration_s=round(duration, 1))
            self._current_owner = None
            self._current_model = None
            self._current_phase = None
            # Release pg advisory lock BEFORE releasing the in-process lock
            # so that the cross-process barrier stays up until we are done.
            await self._release_pg_advisory_lock()
            self._lock.release()
            _gpu_session_active.reset(token)
            # GPU-scheduler P0 (poindexter#914): fold this hold's duration
            # into the per-(owner, phase) rolling stats that feed the P1
            # admission ETA. Fires on EVERY release — including task-less
            # background jobs, which gpu_task_sessions below deliberately
            # skips — and observability is unconditional (never flag-gated).
            # Fire-and-forget with a strong reference held in
            # _stats_capture_tasks: a bare create_task can be GC'd mid-flight
            # ("Task was destroyed but it is pending"), and the write must
            # never extend the release path's latency, let alone gate it.
            try:
                import services.gpu_lease_stats as _lease_stats

                _t = asyncio.get_running_loop().create_task(
                    _lease_stats.record_release(owner, phase or owner, duration * 1000.0)
                )
                _stats_capture_tasks.add(_t)
                _t.add_done_callback(_stats_capture_tasks.discard)
            except Exception:
                # silent-ok: observability capture must never gate the lock
                # lifecycle; a missed sample only delays ETA convergence.
                logger.debug("gpu_lease_stats capture scheduling failed")
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
                    # Best-effort economics — never gate the lock lifecycle on
                    # it — but a dropped row is invisible data loss for the
                    # gpu_task_sessions table with no other signal, so surface
                    # it as a non-paging finding rather than a debug log the
                    # prod level never ships.
                    from utils.findings import emit_finding
                    try:
                        emit_finding(
                            source="gpu_scheduler",
                            kind="gpu_task_session_write_failed",
                            severity="info",
                            title="GPU task-session economics row not recorded",
                            body=(
                                f"Recording the gpu_task_sessions row for task "
                                f"{task_id} (phase {phase or owner}, model {model}) "
                                f"raised {type(exc).__name__}: {exc}. The GPU lock "
                                "released normally; only the per-task compute/power "
                                "economics sample was lost."
                            ),
                            dedup_key="gpu_task_session_write_failed",
                            extra={"task_id": task_id, "error_type": type(exc).__name__},
                        )
                    except Exception:
                        # silent-ok: the finding IS the observability path; a
                        # failure here can't itself be surfaced without
                        # recursing, and must never gate the lock release.
                        logger.debug("gpu_task_sessions write failed", error=str(exc))

    # ------------------------------------------------------------------
    # Admission (poindexter#914 P1)
    # ------------------------------------------------------------------

    def _get_registry(self) -> Any:
        """Lazily-built GPURegistry sharing the scheduler's SiteConfig seam."""
        if getattr(self, "_registry", None) is None:
            from services.gpu_registry import GPURegistry

            self._registry = GPURegistry(site_config=_sc())
        return self._registry

    async def _admission_check(self, *, owner: str, model: str | None,
                               phase: str | None, max_wait_s: float):
        """Assemble live telemetry, run the pure calculator, act on a reject.

        Every read here is individually fail-open (None / 0.0) so admission
        can only ever be as strict as its data is real — a Prometheus blip or
        missing stats row degrades to "grant", never to a false reject.
        Returns the AdmissionDecision; raises GpuBusyError on reject.
        """
        from services import gpu_admission

        inputs = await self._assemble_admission_inputs(
            model=model, max_wait_s=max_wait_s
        )
        decision = gpu_admission.decide(inputs)
        if decision.action == "reject":
            self._emit_admission_rejected_finding(
                owner=owner,
                phase=phase or owner,
                reason=decision.reason or "unknown",
                eta_seconds=decision.eta_seconds,
                max_wait_s=max_wait_s,
            )
            logger.info(
                "GPU admission rejected",
                owner=owner,
                phase=phase or owner,
                reason=decision.reason,
                eta_seconds=decision.eta_seconds,
                max_wait_s=max_wait_s,
            )
            raise gpu_admission.GpuBusyError(
                decision.reason or "unknown", decision.eta_seconds
            )
        return decision

    async def _assemble_admission_inputs(self, *, model: str | None,
                                         max_wait_s: float):
        from services.gpu_admission import AdmissionInputs

        holder_key = holder_elapsed = holder_stats = None
        if self._lock.locked() and self._current_owner is not None:
            h_owner = self._current_owner
            h_phase = self._current_phase or h_owner
            holder_key = (h_owner, h_phase)
            holder_elapsed = time.monotonic() - self._acquired_at
            try:
                from services import gpu_lease_stats as _lease_stats

                holder_stats = await _lease_stats.read_stats(h_owner, h_phase)
            except Exception:
                # silent-ok: stats degrade to the fallback ETA inside decide().
                holder_stats = None

        free_gb: float | None = None
        evictable_gb = 0.0
        try:
            registry = self._get_registry()
            gpu_index = _cfg_int("pipeline_gpu_index", 0)
            free_gb = await registry.free_gb(gpu_index)
            evictable_gb = await registry.evictable_ollama_gb(gpu_index)
        except Exception:
            # silent-ok: missing VRAM telemetry skips the fit gate (fail-open);
            # a persistent outage already pages via nvidia_exporter_unreachable.
            free_gb, evictable_gb = None, 0.0

        estimate_gb: float | None = None
        if model:
            try:
                from services.llm_providers.dispatcher import _read_arch_for_budget
                from services.vram_budget import estimate_model_vram_gb

                arch = await _read_arch_for_budget(model)
                if arch is not None:
                    # Floor estimate: weights + fixed overhead, no KV term (the
                    # caller's num_ctx isn't known at admission time). Under-
                    # estimating errs toward "grant" — consistent with fail-open;
                    # the dispatcher's own num_ctx clamp remains the load-time
                    # backstop. Non-Ollama models (image_gen/video) have no
                    # /api/show arch → None → fit gate skipped.
                    estimate_gb = estimate_model_vram_gb(arch, 0.0)
            except Exception:
                # silent-ok: unknown model size skips the fit gate (fail-open).
                estimate_gb = None

        return AdmissionInputs(
            max_wait_s=max_wait_s,
            holder_key=holder_key,
            holder_elapsed_s=holder_elapsed,
            holder_stats=holder_stats,
            eta_fallback_s=_cfg_float("gpu_sched_eta_fallback_seconds", 120.0),
            free_gpu0_gb=free_gb,
            evictable_gpu0_gb=evictable_gb,
            headroom_gb=_cfg_float("gpu0_headroom_gb", 6.0),
            model_estimate_gb=estimate_gb,
        )

    def _emit_admission_rejected_finding(
        self, *, owner: str, phase: str, reason: str,
        eta_seconds: float | None, max_wait_s: float,
    ) -> None:
        """Emit an info ``gpu_admission_rejected`` finding. Never raises.

        Info, not warn: a reject is the mechanism WORKING (an honest skip
        instead of a doomed wait). Dedup folds repeats per (owner, phase,
        reason) so a busy render window produces one row, not one per rail.
        """
        try:
            from utils.findings import emit_finding

            eta_txt = (
                f"holder ETA ~{eta_seconds:.0f}s vs budget {max_wait_s:.0f}s"
                if eta_seconds is not None
                else f"budget {max_wait_s:.0f}s"
            )
            emit_finding(
                source="gpu_scheduler",
                kind="gpu_admission_rejected",
                severity="info",
                title=f"GPU admission rejected ({owner}, {phase}): {reason}",
                body=(
                    f"gpu.lock({owner!r}, phase={phase!r}) was admission-rejected "
                    f"({reason}; {eta_txt}) and raised GpuBusyError before "
                    "waiting. The caller skips honestly this cycle instead of "
                    "burning its budget behind the current holder "
                    "(poindexter#914 P1)."
                ),
                dedup_key=f"gpu-admission:{owner}:{phase}:{reason}",
                extra={
                    "owner": owner,
                    "phase": phase,
                    "reason": reason,
                    "eta_seconds": eta_seconds,
                    "max_wait_s": max_wait_s,
                },
            )
        except Exception:
            # silent-ok: the finding IS the observability path; a failure
            # emitting it can't be surfaced without recursing, and must never
            # gate admission.
            logger.debug("emit gpu_admission_rejected finding failed", exc_info=True)

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
            # silent-ok: asyncpg + brain.bootstrap are core deps — an import
            # failure here is a systemic, constant condition (the worker is
            # already broken), not a per-task event worth a finding. The
            # economics row is skipped; the caller's finally treats a raise the
            # same way (gpu_task_session_write_failed) if it gets that far.
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
                severity="warn",
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
            # silent-ok: this IS the finding path (nvidia_exporter_unreachable
            # above) — a failure emitting it can't be surfaced without
            # recursing, and observability must never gate the scheduler.
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
        """Current power draw (watts) of the pipeline GPU, via Prometheus.

        Targets the pipeline/display GPU explicitly (``pipeline_gpu_index``,
        default 0) rather than an unlabelled metric. The exporter emits one
        ``nvidia_gpu_*`` series per GPU, so once a second card is in the box an
        unlabelled query resolves to a nondeterministic ``result[0]`` — it could
        read the idle 3090 instead of the 5090 the pipeline actually runs on.
        """
        idx = _cfg_int("pipeline_gpu_index", 0)
        return await self._query_prometheus_scalar(
            f'nvidia_gpu_power_draw_watts{{gpu="{idx}"}}'
        )

    async def _get_gpu_utilization(self) -> float | None:
        """Current utilization (%) of the pipeline GPU, via Prometheus."""
        idx = _cfg_int("pipeline_gpu_index", 0)
        return await self._query_prometheus_scalar(
            f'nvidia_gpu_utilization_percent{{gpu="{idx}"}}'
        )

    async def _wait_for_gaming_clear(self) -> None:
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

    async def _unload_ollama_models(self) -> None:
        """Unload all Ollama models to free VRAM for image-gen / the video render.

        Delegates to the unified ``unload_loaded_ollama_models`` so the
        eviction is *confirmed*: it re-polls ``/api/ps`` until the model is
        gone before returning, rather than firing ``keep_alive:0`` and hoping.
        The caller (the ``image_gen`` / ``video`` lock owner) loads its diffusion /
        video model the instant we return; on a single 32 GB GPU shared with
        the desktop, returning while the 18 GB writer is still resident
        overlaps the two models, exhausts VRAM, and freezes WDDM. Tunable via
        the ``pipeline_writer_unload_confirm_*`` app_settings.
        """
        try:
            await unload_loaded_ollama_models(
                site_config=_sc(),
                confirm=_cfg_bool("pipeline_writer_unload_confirm_enabled", True),
                confirm_timeout_seconds=_cfg_int(
                    "pipeline_writer_unload_confirm_timeout_seconds", 15,
                ),
                poll_interval_seconds=_cfg_float(
                    "pipeline_writer_unload_poll_interval_seconds", 0.5,
                ),
            )
        except Exception as exc:  # noqa: BLE001 — eviction is best-effort; a
            # failure here must never wedge the GPU lock or fail the task.
            # (unload_loaded_ollama_models is already non-raising; this is a
            # belt-and-suspenders guard preserving the pre-delegation contract.)
            logger.warning("Failed to unload Ollama models: %s", exc)

    async def prepare_mode(self, mode: str):
        """Actively prepare GPU for a specific workload mode.

        Call this BEFORE a pipeline stage that needs a different GPU workload.
        The pipeline knows what's coming next — no idle timeouts needed.

        Modes:
            "ollama"    — unload image_gen, Ollama auto-loads on next request
            "image_gen" — unload Ollama models, image-gen server loads on next /generate
            "idle"      — unload everything, free all VRAM
        """
        if mode == "image_gen":
            await self._unload_ollama_models()
            logger.info("[GPU] Prepared for image_gen — Ollama models unloaded")
        elif mode == "ollama":
            await self._unload_image_gen()
            logger.info("[GPU] Prepared for Ollama — image-gen unloaded")
        elif mode == "idle":
            await self._unload_ollama_models()
            await self._unload_image_gen()
            logger.info("[GPU] All models unloaded — VRAM freed")

    async def _unload_image_gen(self, hard: bool = False):
        """Tell the image-gen server to unload its model and free VRAM immediately.

        ``hard=True`` (the render-GPU VRAM reclaim path — 2026-07-12
        desktop-lockup fix, PR 2) asks the server to exit its process after
        unloading, since ``torch.cuda.empty_cache()`` alone does not return
        the CUDA context to the host under WSL2. Docker's
        ``restart: unless-stopped`` brings it back; it lazy-loads on the next
        ``/generate``. Default stays soft (no body) for the pre-existing
        ``prepare_mode('ollama'/'idle')`` callers.
        """
        from services.bootstrap_defaults import DEFAULT_IMAGE_GEN_URL
        image_gen_url = _sc_get("image_gen_server_url", DEFAULT_IMAGE_GEN_URL)
        try:
            client = self._get_http_client()
            kwargs: dict = {"timeout": 10}
            if hard:
                kwargs["json"] = {"hard": True}
            resp = await client.post(f"{image_gen_url}/unload", **kwargs)
            if resp.status_code == 200:
                logger.info(
                    "[GPU] image-gen model unloaded via /unload endpoint%s",
                    " (hard)" if hard else "",
                )
        except Exception as exc:
            # silent-ok: poindexter#455 — used to be `except: pass`. Log at
            # debug because the image-gen server being offline is the common
            # case (it's only running when image-gen phase is active), not a
            # genuine bug. A persistent failure would surface via the
            # nvidia-exporter finding when image-gen is supposed to be up.
            #
            # For hard=True this exception is the EXPECTED path: os._exit(0)
            # kills the process before uvicorn can flush the response, so the
            # connection resets — that's the reclaim working, not a failure.
            logger.debug(
                "[GPU] image-gen /unload call failed (%s): %s: %s",
                "expected — hard unload exits before responding" if hard
                else "server likely offline",
                type(exc).__name__, exc,
            )

    async def _unload_chatterbox(self, hard: bool = False):
        """Tell the chatterbox TTS sidecar to release its model's VRAM.

        Chatterbox holds VRAM outside this scheduler entirely — it isn't a
        lock owner, it just caches its model after narrating. Before the
        idle-unload existed it squatted through the whole subsequent video
        render (Glad-Labs/poindexter#940: dispatch_media_pipeline deferring on
        "free VRAM 24.0 GB < 25 GB required"). The sidecar unloads itself on
        an idle timer; this is the on-demand lever for the reclaim path, for
        when the render can't wait out the timeout.

        Unlike image-gen, a hard unload here DOES answer before exiting (the
        sidecar defers its ``os._exit`` briefly), so a reset connection is a
        real failure rather than the expected path.

        Best-effort: the `tts-hq` profile is opt-in, so the sidecar being
        absent is the common case, not a bug.
        """
        from services.bootstrap_defaults import DEFAULT_CHATTERBOX_URL

        # One source of truth for where chatterbox lives: the provider's
        # base_url, minus the OpenAI-shaped `/v1` suffix that /unload isn't
        # under. `or` (not a get-default) because the app_settings unset
        # sentinel is '', which would otherwise yield a bare "/unload".
        base = _sc_get(
            "plugin.tts_provider.chatterbox.base_url", DEFAULT_CHATTERBOX_URL,
        ) or DEFAULT_CHATTERBOX_URL
        root = base.rstrip("/").removesuffix("/v1")
        try:
            client = self._get_http_client()
            resp = await client.post(
                f"{root}/unload", timeout=15, json={"hard": hard},
            )
            if resp.status_code == 200:
                logger.info(
                    "[GPU] chatterbox model unloaded via /unload%s (%s)",
                    " (hard)" if hard else "", resp.text[:120],
                )
            else:
                logger.warning(
                    "[GPU] chatterbox /unload returned %d: %s",
                    resp.status_code, (getattr(resp, "text", "") or "")[:200],
                )
        except Exception as exc:
            # silent-ok: a transport failure here means the sidecar isn't
            # listening, and `tts-hq` is an opt-in compose profile — on most
            # installs it is never running, so warning would be pure noise on
            # every reclaim. A chatterbox that IS up but failing to unload
            # answers with a non-200 and takes the warning branch above, and
            # a persistent leak shows up as VRAM on the Hardware & Power
            # dashboard. Same posture as _unload_image_gen (poindexter#455).
            logger.debug(
                "[GPU] chatterbox /unload call failed (sidecar likely not "
                "running — tts-hq is opt-in): %s: %s",
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
