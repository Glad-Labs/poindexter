"""
Health probes — exercise each service with real inputs to verify they work.

Unlike basic HTTP checks, these probes send actual requests and validate responses.
Each probe runs on its own schedule (tracked by last-run time).
Results are stored in brain_knowledge for trend analysis.

Standalone: only depends on asyncpg + urllib (no FastAPI imports).
"""

import asyncio
import inspect
import json
import logging
import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC
from typing import Any

try:
    # When brain/ is on sys.path directly (container runtime), import bare.
    from docker_utils import localize_url, resolve_url
except ImportError:
    # When imported as ``brain.health_probes`` (tests, notebooks), use
    # package-qualified path.
    from brain.docker_utils import localize_url, resolve_url

try:  # secret_reader decrypts secret app_settings (the recovery token).
    from secret_reader import read_app_setting as _read_app_setting
except ImportError:  # pragma: no cover — package-qualified path (tests)
    from brain.secret_reader import read_app_setting as _read_app_setting

try:  # httpx is pinned in the brain image; guard for minimal dev envs.
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger("brain.probes")


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` when notify_fn returned a coroutine; pass through otherwise.

    brain's production ``notify`` (``brain_daemon.notify``) is async since #344,
    but legacy tests pass a sync ``MagicMock`` / lambda. Without this shim the
    async call site emits ``RuntimeWarning: coroutine 'notify' was never
    awaited`` and the probe-failure page silently dies. Mirrors the identical
    helper already shipped in business_probes / post_performance.
    """
    if inspect.isawaitable(value):
        return await value
    return value


# OpenTelemetry is optional — health probes work with or without it.
# When the opentelemetry SDK isn't installed, ``_tracer`` is a no-op
# implementation that matches the real API's ``start_as_current_span``
# contract. Same shape as src/cofounder_agent/services/llm_providers/dispatcher.py
# so behavior stays uniform across the codebase.
try:
    from opentelemetry import trace as _otel_trace  # type: ignore[import-untyped]

    _tracer = _otel_trace.get_tracer("poindexter.brain.probes")
except ImportError:  # pragma: no cover - exercised in minimal dev envs
    from contextlib import contextmanager

    @contextmanager
    def _noop_span(_name: str, **_kwargs: Any):
        class _NoopSpan:
            def set_attribute(self, *_a: Any, **_k: Any) -> None:
                pass

            def record_exception(self, *_a: Any, **_k: Any) -> None:
                pass

            def set_status(self, *_a: Any, **_k: Any) -> None:
                pass

        yield _NoopSpan()

    class _NoopTracer:
        start_as_current_span = staticmethod(_noop_span)

    _tracer = _NoopTracer()

# Bootstrap defaults — overridden from app_settings on first probe run.
# localize_url rewrites `localhost` to `host.docker.internal` when running
# inside a container, so the same DB value works in both environments.
API_URL = localize_url(os.getenv("API_URL") or "http://localhost:8002")
LOCAL_OLLAMA = localize_url(os.getenv("OLLAMA_URL") or "http://localhost:11434")

# Cross-process GPU arbitration key. MUST stay in sync, BY VALUE, with
# ``services.gpu_scheduler.GPU_ADVISORY_LOCK_KEY`` (same int64). The brain runs
# in its own container (stdlib + asyncpg only) and cannot import the worker's
# gpu_scheduler, so the key is duplicated here rather than imported. The
# worker's GPUScheduler holds ``pg_advisory_lock(this key)`` on a dedicated
# connection for the whole of every GPU session (Ollama inference, SDXL image
# gen, wan video render); the brain's writer-model probe takes the same lock
# NON-BLOCKINGLY (``pg_try_advisory_lock``) so it never loads the ~19GB writer
# into VRAM mid-render (would oversubscribe the 32GB card → SDXL CUDA-OOM →
# degraded video; observed 2026-06-21).
GPU_ADVISORY_LOCK_KEY: int = 7_777_777_777
# Where Alertmanager is reachable from the brain. Used to decide whether the
# PROMETHEUS_COVERED suppression is safe (#304): if Alertmanager is down, the
# brain must NOT defer covered-probe alerts to it (that would be a double-blind).
ALERTMANAGER_URL = localize_url(
    os.getenv("ALERTMANAGER_URL") or "http://alertmanager:9093"
)

_config_synced = False


async def _sync_config_from_db(pool):
    """Pull URL/connection config from app_settings so probes use the
    canonical values instead of potentially stale env var defaults.
    Runs once on first probe cycle.

    Env vars take priority (Docker sets them correctly for the container
    network), DB values are fallback for local dev where env vars may
    not be set.
    """
    global API_URL, LOCAL_OLLAMA, ALERTMANAGER_URL, _config_synced
    if _config_synced:
        return
    try:
        # URLs: shared resolver handles env-wins-over-DB + localize_url in one call.
        API_URL = await resolve_url(
            pool, "internal_api_base_url", "api_url",
            default=API_URL, env_var="API_URL",
        )
        LOCAL_OLLAMA = await resolve_url(
            pool, "ollama_base_url",
            default=LOCAL_OLLAMA, env_var="OLLAMA_URL",
        )
        ALERTMANAGER_URL = await resolve_url(
            pool, "alertmanager_url",
            default=ALERTMANAGER_URL, env_var="ALERTMANAGER_URL",
        )
        _config_synced = True
        logger.info("[PROBES] Config synced: API=%s, Ollama=%s (env wins over DB; URLs localized)",
                     API_URL, LOCAL_OLLAMA)
    except Exception as e:
        logger.warning("[PROBES] Failed to sync config from DB, using env defaults: %s", e)

# Note: a previous version of this file had a `_create_gitea_issue` helper
# that auto-filed Gitea tickets on 3-consecutive probe failures. Gitea was
# decommissioned 2026-04-30 (see CLAUDE.md "Deployment" section); the
# helper was removed in the same PR that wrapped these probes for async-
# correctness. The `_created_issues` dedupe set used to live here too —
# see `_failure_counts` below for the still-live consecutive-failure
# tracking that drives notification escalation, which goes through the
# brain's `notify_operator` (Telegram + Discord) instead now.

# Probe schedules (seconds between runs)
PROBE_SCHEDULES = {
    "db_ping": 300,            # 5 min
    "ollama_models": 300,      # 5 min
    "ollama_embedding": 300,   # 5 min — tests the embed endpoint specifically
    "quality_score": 1800,     # 30 min
    "content_gen": 1800,       # 30 min
    "research_service": 3600,  # 1 hour
    "image_search": 3600,      # 1 hour
    "grafana_datasources": 300,  # 5 min
    "public_site": 300,          # 5 min
    "scheduled_tasks": 3600,     # 1 hour
    "disk_space": 3600,          # 1 hour
    "gpu_temperature": 300,      # 5 min — temp can spike fast under SDXL + LLM
    # P0 — pipeline health
    "stuck_tasks": 1800,         # 30 min
    "approval_queue": 3600,      # 1 hour
    "failed_task_spike": 3600,   # 1 hour
    "worker_error_rate": 300,    # 5 min — catches 100% failure rate fast
    # P1 — business continuity
    "publish_rate": 21600,       # 6 hours
    "cost_freshness": 3600,      # 1 hour
    "podcast_health": 21600,     # 6 hours
    "newsletter_health": 21600,  # 6 hours
    # P2 — quality & reliability
    "embeddings_freshness": 21600,  # 6 hours
    "r2_connectivity": 3600,        # 1 hour
    "traffic_anomaly": 21600,       # 6 hours
    "quality_trend": 21600,         # 6 hours
    "topic_quality": 21600,          # 6 hours
    "pipeline_throughput": 21600,    # 6 hours
    "cadence_slo": 3600,             # 1 hour — catch a cadence shortfall within hours, not days
}

# Track last run times
_last_run: dict[str, float] = {}


def _is_due(probe_name: str) -> bool:
    """Check if a probe is due to run based on its schedule."""
    last = _last_run.get(probe_name, 0)
    interval = PROBE_SCHEDULES.get(probe_name, 3600)
    return (time.time() - last) >= interval


def _mark_run(probe_name: str):
    """Mark a probe as having just run."""
    _last_run[probe_name] = time.time()


def _http_json(url: str, method: str = "GET", data: dict | None = None,
               timeout: int = 15) -> tuple[bool, dict]:
    """Make an HTTP request and parse JSON response. Returns (ok, data_or_error)."""
    try:
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        # Vercel's edge protection (BotID + bot-blocking ruleset) returns 403
        # for the default Python-urllib UA. Identify as the brain probe so the
        # public site treats us like any other infra check.
        req.add_header("User-Agent", "brain-probe")
        if body:
            req.add_header("Content-Type", "application/json")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return True, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return False, {"error": f"HTTP {e.code}: {str(e.reason)[:100]}"}
    except Exception as e:
        return False, {"error": str(e)[:200]}


async def probe_db_ping(pool) -> dict:
    """Probe: SELECT 1 — verify DB is responsive and measure latency."""
    start = time.time()
    try:
        row = await pool.fetchrow("SELECT 1 AS ok")
        latency_ms = (time.time() - start) * 1000
        ok = row is not None and row["ok"] == 1
        return {
            "ok": ok,
            "latency_ms": round(latency_ms, 1),
            "detail": "responsive" if ok else "unexpected result",
        }
    except Exception as e:
        return {"ok": False, "latency_ms": -1, "detail": str(e)[:200]}


async def probe_ollama_models(_pool) -> dict:
    """Probe: List Ollama models — verify expected models are loaded."""
    ok, result = await asyncio.to_thread(_http_json, f"{LOCAL_OLLAMA}/api/tags", timeout=5)
    if not ok:
        return {"ok": False, "detail": f"Ollama unreachable: {result.get('error', 'unknown')}", "models": []}

    models = [m.get("name", "") for m in result.get("models", [])]
    # Check for at least one model present
    has_models = len(models) > 0
    return {
        "ok": has_models,
        "model_count": len(models),
        "models": models[:10],  # Cap for storage
        "detail": "models loaded" if has_models else "no models found",
    }


async def probe_ollama_embedding(_pool) -> dict:
    """Probe: call /api/embed and validate the response contains a float vector.

    ``probe_ollama_models`` only checks ``/api/tags`` (model list). That misses
    the "chat works, embed refuses" failure mode: under GPU-heavy load Ollama
    can serve inference requests while the embedding endpoint backs up and refuses
    new connections. This probe exercises the actual embedding path so the brain
    detects RAG-grounding outages before the writer produces low-quality drafts.

    Uses ``nomic-embed-text`` — the model baked into the embeddings table schema.
    The model should be CPU-pinned (``num_gpu 0`` Modelfile) so it doesn't
    compete for VRAM with the writer or SDXL during inference.
    """
    model = "nomic-embed-text"

    def _call_embed() -> tuple[bool, dict]:
        return _http_json(
            f"{LOCAL_OLLAMA}/api/embed",
            method="POST",
            data={"model": model, "input": "ping"},
            timeout=30,  # CPU-only embedding may be slow on first call
        )

    ok, result = await asyncio.to_thread(_call_embed)
    if not ok:
        return {
            "ok": False,
            "model": model,
            "detail": f"Ollama embed endpoint failed: {result.get('error', 'unknown')}",
        }

    embeddings = result.get("embeddings", [])
    has_vector = (
        isinstance(embeddings, list)
        and len(embeddings) > 0
        and isinstance(embeddings[0], list)
        and len(embeddings[0]) > 0
    )
    dim = len(embeddings[0]) if has_vector else 0
    return {
        "ok": has_vector,
        "model": model,
        "vector_dim": dim,
        "detail": f"embed ok, dim={dim}" if has_vector else "no embeddings returned",
    }


async def probe_quality_score(pool) -> dict:
    """Probe: Score a known-good snippet — verify quality service returns sane scores."""
    # Check that quality scoring is functional by verifying recent scored tasks

    # The real check: can we reach the quality evaluation endpoint?
    # Since there's no standalone quality endpoint, we verify the API is healthy
    # and check that quality_service data exists in recent tasks
    try:
        row = await pool.fetchrow("""
            SELECT quality_score FROM pipeline_tasks_view
            WHERE quality_score IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1
        """)
        if row:
            score = float(row["quality_score"])
            return {
                "ok": 50 <= score <= 100,
                "last_score": score,
                "detail": f"last quality score: {score:.0f}",
            }
        return {"ok": True, "detail": "no scored tasks yet (pipeline idle)"}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


# Final safety literal for the content-gen probe — only used when neither
# app_settings nor /api/tags yields a usable model. gemma3:27b is the prod
# default writer (settings_defaults.pipeline_writer_model), so it's the most
# likely-installed fallback if every dynamic source comes up empty.
_CONTENT_GEN_FALLBACK_MODEL = "gemma3:27b"


def _strip_ollama_prefix(model: str) -> str:
    """Drop a leading ``ollama/`` LiteLLM routing prefix.

    LiteLLM stores the writer model with an ``ollama/`` provider prefix
    (e.g. ``ollama/gemma3:27b``), but Ollama's native ``/api/generate``
    404s on a prefixed name — it wants the bare tag. Strip it here so the
    probe queries the model Ollama actually knows about.
    """
    model = (model or "").strip()
    if model.startswith("ollama/"):
        return model[len("ollama/"):]
    return model


def _first_installed_generative_model() -> str:
    """Return the first non-embedding model from Ollama's ``/api/tags``.

    Best-effort, sync (called via ``asyncio.to_thread``): returns ``""``
    if Ollama is unreachable or only embedding models are installed.
    Embedding models can't serve ``/api/generate`` (they 400/500), so we
    skip names that look like embedders.
    """
    ok, result = _http_json(f"{LOCAL_OLLAMA}/api/tags", timeout=5)
    if not ok:
        return ""
    for m in result.get("models", []):
        name = (m.get("name") or "").strip()
        if not name:
            continue
        lowered = name.lower()
        if "embed" in lowered or "bge" in lowered:
            continue
        return name
    return ""


async def _resolve_content_gen_model(pool) -> str:
    """Resolve the model the content-gen probe should exercise.

    Resolution order (first non-empty wins):

    1. ``app_settings.pipeline_writer_model`` — the configured writer
       (``ollama/`` prefix stripped).
    2. ``app_settings.default_ollama_model``.
    3. The first installed non-embedding model from ``/api/tags``.
    4. ``_CONTENT_GEN_FALLBACK_MODEL`` — a safe literal.

    Best-effort — never raises; a DB hiccup just falls through to the
    next source.
    """
    for key in ("pipeline_writer_model", "default_ollama_model"):
        try:
            raw = await pool.fetchval(
                "SELECT value FROM app_settings WHERE key = $1", key
            )
        except Exception as exc:
            logger.warning(
                "[content_gen] could not read %s from app_settings: %s", key, exc
            )
            raw = None
        model = _strip_ollama_prefix(str(raw) if raw is not None else "")
        if model:
            return model

    # Neither setting resolved — ask Ollama what's actually installed.
    installed = await asyncio.to_thread(_first_installed_generative_model)
    if installed:
        return installed

    return _CONTENT_GEN_FALLBACK_MODEL


async def _probe_content_gen_inner(pool) -> dict:
    """Exercise the writer model (resolve + ``/api/generate``).

    Split out from ``probe_content_gen`` so the latter stays a thin GPU-lock
    wrapper. Runs ONLY while the caller holds the GPU advisory lock — it loads
    the ~19GB writer into VRAM, so it must never run concurrently with a render.
    """
    model = await _resolve_content_gen_model(pool)
    ok, result = await asyncio.to_thread(
        _http_json,
        f"{LOCAL_OLLAMA}/api/generate",
        method="POST",
        data={
            "model": model,
            "prompt": "Respond with exactly one sentence: What is FastAPI?",
            "stream": False,
            "options": {"num_predict": 50},
        },
        timeout=30,
    )

    if not ok:
        return {
            "ok": False,
            "model": model,
            "detail": (
                f"Ollama generate failed for model {model!r}: "
                f"{result.get('error', 'unknown')}"
            ),
        }

    response_text = result.get("response", "")
    has_content = len(response_text.strip()) > 10
    return {
        "ok": has_content,
        "model": model,
        "response_length": len(response_text),
        "detail": "generation working" if has_content else "empty response",
    }


async def probe_content_gen(pool) -> dict:
    """Probe: Check Ollama can generate text — 1-sentence test.

    GPU arbitration (2026-06-21): exercising the writer loads the ~19GB model
    into VRAM. Firing during a media render (wan + SDXL already near the 32GB
    ceiling) oversubscribes the GPU → SDXL CUDA-OOM → degraded video. The brain
    can't import ``services.gpu_scheduler`` (separate stdlib+asyncpg container),
    but it shares Postgres, so it takes the SAME cross-process GPU advisory lock
    NON-BLOCKINGLY: if a render/LLM job holds it, the probe SKIPS this cycle
    with a non-alerting status (NOT a writer-down failure — that would fire a
    false writer/Ollama page). Lock and unlock run on one pinned connection
    (advisory locks are session-scoped) and the release is in a ``finally`` so a
    crash can't leak the lock and wedge the worker's real GPU scheduler.

    Uses the DB-configured writer model (``pipeline_writer_model``, then
    ``default_ollama_model``) rather than a hardcoded tag, so the probe
    exercises a model that's actually installed. A hardcoded model that
    isn't pulled makes ``/api/generate`` 404 and the probe falsely
    reports content generation broken (#228 follow-up).
    """
    async with pool.acquire() as conn:
        acquired = bool(
            await conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", GPU_ADVISORY_LOCK_KEY
            )
        )
        if not acquired:
            # A render or LLM job holds the GPU. The skip is observable (this
            # log line + the ``skipped_gpu_busy`` status persisted to
            # brain_knowledge) and non-alerting (ok=True), so monitoring is
            # never blinded and we never report the writer DOWN just because
            # the GPU is legitimately busy.
            logger.info(
                "[content_gen] GPU advisory lock (key=%d) is held — a media "
                "render or LLM job is using the GPU; skipping the writer-model "
                "probe this cycle to avoid loading the ~19GB writer into VRAM "
                "during contention (deferred, NOT a writer-down condition).",
                GPU_ADVISORY_LOCK_KEY,
            )
            return {
                "ok": True,
                "status": "skipped_gpu_busy",
                "detail": (
                    "skipped — GPU busy (advisory lock held by an active "
                    "render or LLM job); writer-model probe deferred to the "
                    "next cycle to avoid VRAM oversubscription"
                ),
            }
        try:
            return await _probe_content_gen_inner(pool)
        finally:
            await conn.execute(
                "SELECT pg_advisory_unlock($1)", GPU_ADVISORY_LOCK_KEY
            )


async def probe_research_service(pool) -> dict:
    """Probe: Verify research service endpoint responds."""
    api_reachable = True
    ok, result = await asyncio.to_thread(_http_json, f"{API_URL}/api/health", timeout=5)
    if not ok:
        # API unreachable is degraded, not a hard failure — the DB check still matters
        api_reachable = False

    # Check that published posts exist (research service uses these for internal links)
    try:
        row = await pool.fetchrow(
            "SELECT COUNT(*) as c FROM posts WHERE status = 'published'"
        )
        count = row["c"] if row else 0
        if not api_reachable:
            return {
                "ok": True,
                "status": "degraded",
                "published_posts": count,
                "detail": f"API unreachable (degraded) but DB has {count} posts for internal linking",
            }
        return {
            "ok": count > 0,
            "published_posts": count,
            "detail": f"{count} posts available for internal linking",
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_image_search(_pool) -> dict:
    """Probe: Check Pexels image search is available (env var or API)."""
    import os
    pexels_key = os.getenv("PEXELS_API_KEY", "")
    if pexels_key:
        return {"ok": True, "detail": "Pexels API key configured (env)"}
    # No env var — check if API endpoint works with a test query
    ok, result = await asyncio.to_thread(_http_json, f"{API_URL}/api/health", timeout=5)
    if not ok:
        return {"ok": True, "detail": "Pexels key not set but not critical — using fallback images"}
    return {"ok": True, "detail": "Pexels key not set — image search will use fallback"}


def _check_grafana_datasources_sync(grafana_url: str, grafana_user: str, grafana_pass: str) -> dict:
    """Sync helper: list Grafana datasources + health-check each. Called via
    asyncio.to_thread from the async probe so the loop doesn't block on
    several urllib.urlopen calls in series.
    """
    try:
        import base64
        auth = base64.b64encode(f"{grafana_user}:{grafana_pass}".encode()).decode()
        # List datasources
        req = urllib.request.Request(
            f"{grafana_url}/api/datasources",
            headers={"Authorization": f"Basic {auth}", "User-Agent": "brain-probe"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        datasources = json.loads(resp.read())

        # Health check each datasource
        broken = []
        for ds in datasources:
            uid = ds.get("uid", "")
            name = ds.get("name", "")
            try:
                hc_req = urllib.request.Request(
                    f"{grafana_url}/api/datasources/uid/{uid}/health",
                    headers={"Authorization": f"Basic {auth}", "User-Agent": "brain-probe"},
                )
                hc_resp = urllib.request.urlopen(hc_req, timeout=10)
                hc_data = json.loads(hc_resp.read())
                if hc_data.get("status") != "OK":
                    broken.append(f"{name}: {hc_data.get('message', 'unhealthy')}")
            except Exception as e:
                broken.append(f"{name}: {str(e)[:80]}")

        if broken:
            return {"ok": False, "detail": f"{len(broken)} datasource(s) broken: {'; '.join(broken[:3])}", "broken": broken}
        return {"ok": True, "detail": f"all {len(datasources)} datasources healthy"}
    except Exception as e:
        return {"ok": False, "detail": f"Grafana unreachable: {str(e)[:100]}"}


async def probe_grafana_datasources(_pool) -> dict:
    """Probe: Check all Grafana datasources can connect."""
    return await asyncio.to_thread(
        _check_grafana_datasources_sync,
        os.getenv("GRAFANA_URL", "http://localhost:3000"),
        os.getenv("GRAFANA_USER", "admin"),
        os.getenv("GRAFANA_PASSWORD", "admin"),
    )


async def probe_public_site(_pool) -> dict:
    """Probe: Check the public site returns content (not just 200)."""
    try:
        site_url = os.getenv("SITE_URL", "http://localhost:3000")
        ok, data = await asyncio.to_thread(_http_json, f"{site_url}/api/posts?limit=1", timeout=10)
        if not ok:
            return {"ok": False, "detail": f"API unreachable: {data.get('error', 'unknown')}"}
        # Check that posts are actually returned
        items = data.get("items", [])
        total = data.get("total", 0)
        if total == 0 or (isinstance(items, list) and len(items) == 0):
            return {"ok": False, "detail": "Site returns 0 posts — DB connection may be broken"}
        return {"ok": True, "detail": f"{total} posts available", "sample": items[0].get("title", "")[:50] if items else ""}
    except Exception as e:
        return {"ok": False, "detail": f"Public site check failed: {str(e)[:100]}"}


# ---------------------------------------------------------------------------
# Scheduled-tasks probe — host self-heal task liveness via the Recovery Agent.
#
# The brain runs in a Linux container and can't enumerate the host's Windows
# Task Scheduler (the long-standing "needs migration" gap, #704). Instead it
# asks the host Recovery Agent (GET /tasks — see scripts/recovery-agent.py) for
# the status of an operator-configured watch list, then pages when a watched
# self-heal task is disabled, missing, or its last run failed. The watch list +
# agent URL/token live in app_settings (config-in-DB), so the agent stays a
# generic reflector with no operator task names baked into the mirrored script.
# ---------------------------------------------------------------------------

# Shared with mcp_http_probe + compose_drift_probe — same physical agent.
RECOVERY_URL_KEY = "mcp_http_probe_recovery_url"
RECOVERY_TOKEN_KEY = "mcp_http_probe_recovery_token"  # noqa: S105 — setting key, not a secret
SCHED_TASKS_WATCH_KEY = "scheduled_tasks_probe_watch_tasks"

# Windows "Last Result" codes that are NOT failures: 0 = success, 1 =
# running/queued, 267009 = SCHED_S_TASK_RUNNING, 267011 = SCHED_S_TASK_HAS_NOT_RUN
# (the same set the legacy direct-schtasks probe treated as healthy).
_OK_LAST_RESULT_CODES: frozenset[int] = frozenset({0, 1, 267009, 267011})


def _derive_tasks_url(recovery_url: str) -> str:
    """Turn the agent's ``/recover`` URL into its ``/tasks`` sibling.

    ``mcp_http_probe_recovery_url`` points at the agent's POST ``/recover``
    endpoint; the read-only status endpoint lives at ``/tasks`` on the same
    host:port. Swaps the final path segment.
    """
    base = recovery_url.rstrip("/").rsplit("/", 1)[0]
    return base + "/tasks"


def _task_problem(task: dict) -> str | None:
    """Return a one-line problem for an unhealthy task, or None when healthy.

    Single source of truth for the page decision: a task is unhealthy if it's
    missing on the host, disabled (``Settings.Enabled=False`` or
    ``State=Disabled`` — the state ``Set-ScheduledTask -Action`` silently leaves
    it in), or its last run returned a non-OK result code.
    """
    name = str(task.get("name", "?"))
    if not task.get("exists", True):
        return f"{name}: not found on host"
    state = task.get("state")
    if task.get("enabled") is False or (
        isinstance(state, str) and state.lower() == "disabled"
    ):
        return f"{name}: DISABLED"
    lrr = task.get("last_run_result")
    if lrr is not None:
        try:
            code: int | None = int(lrr)
        except (TypeError, ValueError):
            code = None  # unparseable → don't page on it
        if code is not None and code not in _OK_LAST_RESULT_CODES:
            return f"{name}: last run failed (exit {code})"
    return None


def _evaluate_scheduled_task_health(tasks: list[dict]) -> tuple[bool, str]:
    """Reduce a list of task-status dicts to an ``(ok, detail)`` page decision."""
    problems = [p for t in tasks if (p := _task_problem(t)) is not None]
    if problems:
        return False, (
            f"{len(problems)} watched task(s) unhealthy: " + "; ".join(problems[:5])
        )
    return True, f"all {len(tasks)} watched scheduled task(s) healthy"


async def probe_scheduled_tasks(
    pool,
    *,
    http_client_factory: Callable[..., Any] | None = None,
) -> dict:
    """Probe: verify host self-heal Scheduled Tasks are enabled + last-run-ok.

    The brain can't see the host Windows Task Scheduler from inside its Linux
    container, so it asks the host Recovery Agent's ``GET /tasks`` endpoint for
    the status of the tasks named in ``scheduled_tasks_probe_watch_tasks``, then
    pages when one is disabled, missing, or last-run-failed.

    Fail-open (advisory ``ok=True``) when the agent URL/token are unset or the
    watch list is empty — an un-configured operator (e.g. a non-Windows OSS
    install with no agent) never pages, mirroring the host-recover fall-through
    in compose_drift_probe. Debounce + escalation is the framework's job
    (``run_health_probes`` pages after ``ALERT_AFTER_FAILURES`` consecutive
    fails), so this probe only reports a single-cycle ``ok``/``detail``.
    """
    watch_csv = await _read_app_setting(pool, SCHED_TASKS_WATCH_KEY, "")
    watch = [t.strip() for t in watch_csv.split(",") if t.strip()]
    recovery_url = (await _read_app_setting(pool, RECOVERY_URL_KEY, "")).strip()
    recovery_token = (await _read_app_setting(pool, RECOVERY_TOKEN_KEY, "")).strip()

    if not recovery_url or not recovery_token:
        return {
            "ok": True,
            "detail": (
                "recovery agent URL/token unset — host scheduled-task liveness "
                "not checked (advisory; set mcp_http_probe_recovery_url + "
                "mcp_http_probe_recovery_token to enable)"
            ),
        }
    if not watch:
        return {
            "ok": True,
            "detail": (
                "no watched tasks configured — set scheduled_tasks_probe_watch_tasks "
                "to a CSV of host Scheduled Task names (advisory)"
            ),
        }

    if httpx is None and http_client_factory is None:
        return {
            "ok": True,
            "detail": (
                "httpx unavailable in brain image — scheduled-task liveness "
                "not checked (advisory)"
            ),
        }

    tasks_url = _derive_tasks_url(recovery_url)
    _httpx: Any = httpx
    factory = http_client_factory or (lambda: _httpx.AsyncClient(timeout=10))

    try:
        async with factory() as client:
            response = await client.get(
                tasks_url,
                params={"name": watch},
                headers={"Authorization": f"Bearer {recovery_token}"},
            )
            status_code = response.status_code
            if not (200 <= status_code < 300):
                return {
                    "ok": False,
                    "detail": (
                        f"recovery agent GET {tasks_url} returned HTTP {status_code} "
                        f"— can't verify host scheduled-task health"
                    ),
                }
            body = response.json()
    except Exception as exc:  # noqa: BLE001 — any transport error is a real signal
        return {
            "ok": False,
            "detail": (
                f"recovery agent unreachable at {tasks_url}: {type(exc).__name__}: "
                f"{exc} — the Recovery Agent task itself may be down"
            ),
        }

    if not isinstance(body, dict) or not body.get("ok", False):
        return {
            "ok": False,
            "detail": f"recovery agent /tasks returned an error: {str(body)[:200]}",
        }

    tasks = body.get("tasks") or []
    ok, detail = _evaluate_scheduled_task_health(tasks)
    result: dict = {"ok": ok, "detail": detail}
    if not ok:
        result["failed"] = [
            str(t.get("name")) for t in tasks if _task_problem(t) is not None
        ][:5]
    return result


async def probe_gpu_temperature(pool) -> dict:
    """Probe: alert when GPU core temperature crosses the operator-tuned
    threshold.

    Reads ``gpu_temperature_high_threshold_c`` from app_settings (default
    85 — see baseline seed). The 5-minute brain cycle samples the latest
    row in ``gpu_metrics``; any temperature above the threshold flips the
    probe to ``ok=False`` so the alert dispatcher pages on Telegram.

    The threshold setting was seeded by the 2026-02-07 baseline but had
    no probe consumer until #236 — operators tuning the row got no
    behavior change. Wired here so the setting becomes load-bearing.
    """
    try:
        row = await pool.fetchrow(
            "SELECT temperature, timestamp FROM gpu_metrics "
            "ORDER BY timestamp DESC LIMIT 1"
        )
        if row is None or row["temperature"] is None:
            # No GPU metrics yet (fresh install, no GPU exporter wired).
            # Return ok=True with a detail describing the absence so the
            # operator can debug — never a hard fail when GPU is simply
            # not present. The integer comparison below short-circuits.
            return {
                "ok": True,
                "detail": "no gpu_metrics rows yet — exporter not wired or no GPU",
            }
        # Freshness gate (#536): a live exporter that stopped WRITING leaves a
        # stale row with a normal temperature, so the threshold check below
        # would falsely report healthy. Distinguish "process alive" from
        # "writing fresh data" — a frozen feed means GPU monitoring is blind.
        stale_row = await pool.fetchrow(
            "SELECT value FROM app_settings "
            "WHERE key = 'gpu_metrics_staleness_threshold_minutes'"
        )
        try:
            stale_minutes = int(stale_row["value"]) if stale_row else 15
        except (TypeError, ValueError):
            stale_minutes = 15
        ts = row["timestamp"]
        if ts is not None and stale_minutes > 0:
            from datetime import datetime
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            age_min = (datetime.now(UTC) - ts).total_seconds() / 60.0
            if age_min > stale_minutes:
                return {
                    "ok": False,
                    "detail": (
                        f"GPU metrics STALE: newest gpu_metrics row is "
                        f"{age_min:.0f}min old (> {stale_minutes}min threshold). "
                        f"Exporter is likely alive but not writing — GPU "
                        f"monitoring is blind. Check the nvidia metrics exporter."
                    ),
                    "stale_minutes": round(age_min),
                    "threshold_minutes": stale_minutes,
                }
        threshold_row = await pool.fetchrow(
            "SELECT value FROM app_settings "
            "WHERE key = 'gpu_temperature_high_threshold_c'"
        )
        try:
            threshold_c = int(threshold_row["value"]) if threshold_row else 85
        except (TypeError, ValueError):
            threshold_c = 85
        try:
            temp_c = int(row["temperature"])
        except (TypeError, ValueError):
            return {
                "ok": True,
                "detail": f"gpu_metrics.temperature unparseable: {row['temperature']!r}",
            }
        if temp_c > threshold_c:
            return {
                "ok": False,
                "detail": (
                    f"GPU at {temp_c}C exceeds threshold "
                    f"{threshold_c}C (gpu_temperature_high_threshold_c). "
                    f"Most consumer NVIDIA cards throttle ~90C — check "
                    f"airflow / fan curve / current workload."
                ),
                "temperature_c": temp_c,
                "threshold_c": threshold_c,
            }
        return {
            "ok": True,
            "detail": f"GPU {temp_c}C / {threshold_c}C threshold",
            "temperature_c": temp_c,
            "threshold_c": threshold_c,
        }
    except Exception as e:
        return {"ok": False, "detail": f"gpu_temperature probe failed: {str(e)[:150]}"}


async def probe_disk_space(_pool) -> dict:
    """Probe: Alert if any drive drops below 10% free space."""
    try:
        warnings = []
        if platform.system() == "Windows":
            # Check all lettered drives
            for letter in "CDEFGH":
                drive = f"{letter}:\\"
                try:
                    usage = shutil.disk_usage(drive)
                    pct_free = (usage.free / usage.total) * 100
                    if pct_free < 10:
                        gb_free = usage.free / (1024 ** 3)
                        warnings.append(f"{drive} {pct_free:.1f}% free ({gb_free:.1f} GB)")
                except (FileNotFoundError, OSError):
                    pass  # Drive doesn't exist
        else:
            # Linux/Mac — check root
            usage = shutil.disk_usage("/")
            pct_free = (usage.free / usage.total) * 100
            if pct_free < 10:
                gb_free = usage.free / (1024 ** 3)
                warnings.append(f"/ {pct_free:.1f}% free ({gb_free:.1f} GB)")

        if warnings:
            return {
                "ok": False,
                "low_drives": warnings,
                "detail": f"Low disk space: {', '.join(warnings)}",
            }
        return {"ok": True, "detail": "all drives above 10% free"}
    except Exception as e:
        return {"ok": False, "detail": f"disk check failed: {str(e)[:150]}"}


# ---------------------------------------------------------------------------
# P0 — Pipeline health probes
# ---------------------------------------------------------------------------


async def probe_stuck_tasks(pool) -> dict:
    """Probe: Detect tasks stuck in_progress for more than 4 hours."""
    try:
        rows = await pool.fetch("""
            SELECT task_id, topic, updated_at
            FROM pipeline_tasks_view
            WHERE status = 'in_progress'
              AND updated_at < NOW() - INTERVAL '4 hours'
            ORDER BY updated_at ASC LIMIT 5
        """)
        if rows:
            stuck = [f"{r['task_id'][:8]} ({r.get('topic', 'unknown')[:30]})" for r in rows]
            return {
                "ok": False,
                "stuck_count": len(rows),
                "stuck_tasks": stuck,
                "detail": f"{len(rows)} task(s) stuck in_progress > 4h: {', '.join(stuck[:3])}",
            }
        return {"ok": True, "detail": "no stuck tasks"}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_approval_queue(pool) -> dict:
    """Probe: Alert if approval queue backs up beyond threshold."""
    try:
        row = await pool.fetchrow("""
            SELECT COUNT(*) as c FROM pipeline_tasks_view
            WHERE status = 'awaiting_approval'
        """)
        count = row["c"] if row else 0
        threshold = 5
        return {
            "ok": count <= threshold,
            "queue_size": count,
            "threshold": threshold,
            "detail": f"{count} tasks awaiting approval" + (f" (exceeds {threshold})" if count > threshold else ""),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_failed_task_spike(pool) -> dict:
    """Probe: Detect spike in task failures (24h vs 7d average)."""
    try:
        row = await pool.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM pipeline_tasks_view
                 WHERE status = 'failed' AND updated_at > NOW() - INTERVAL '24 hours') as recent_failures,
                (SELECT COUNT(*) FROM pipeline_tasks_view
                 WHERE status = 'failed' AND updated_at > NOW() - INTERVAL '7 days') as week_failures
        """)
        recent = row["recent_failures"] if row else 0
        week = row["week_failures"] if row else 0
        daily_avg = week / 7.0 if week > 0 else 0
        spike = recent > max(daily_avg * 2, 3)  # 2x average or 3+ failures
        return {
            "ok": not spike,
            "recent_24h": recent,
            "weekly_total": week,
            "daily_avg": round(daily_avg, 1),
            "detail": f"{recent} failures in 24h (avg {daily_avg:.1f}/day)" + (" — SPIKE" if spike else ""),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_worker_error_rate(pool) -> dict:
    """Probe: Check worker task executor error rate via /api/health.

    Catches the case where the worker process is running and healthy
    but every task silently fails (e.g., 100% error rate).  Runs every
    5 minutes so a total pipeline outage is detected within 15 minutes
    (3 consecutive failures → Telegram alert).
    """
    try:
        ok, data = await asyncio.to_thread(_http_json, f"{API_URL}/api/health")
        if not ok:
            return {"ok": False, "detail": f"Worker API unreachable: {data.get('error', 'unknown')}"}

        executor = data.get("components", {}).get("task_executor", {})
        if isinstance(executor, str):
            # executor might be "unavailable"
            return {"ok": False, "detail": f"Task executor: {executor}"}

        running = executor.get("running", False)
        if not running:
            return {"ok": False, "detail": "Task executor is not running"}

        success = executor.get("success_count", 0)
        errors = executor.get("error_count", 0)
        total = success + errors

        if total == 0:
            return {"ok": True, "detail": "No tasks processed yet", "success": 0, "errors": 0}

        error_rate = errors / total
        # Critical: >50% error rate with at least 3 tasks processed
        critical = error_rate > 0.5 and total >= 3
        # Warning: 100% error rate even with few tasks
        total_failure = success == 0 and errors > 0

        return {
            "ok": not (critical or total_failure),
            "error_rate": round(error_rate * 100, 1),
            "success": success,
            "errors": errors,
            "total": total,
            "detail": (
                f"{success}✓ {errors}✗ ({error_rate * 100:.0f}% errors)"
                + (" — CRITICAL: 100% failure" if total_failure else "")
                + (" — HIGH error rate" if critical and not total_failure else "")
            ),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


# ---------------------------------------------------------------------------
# P1 — Business continuity probes
# ---------------------------------------------------------------------------


async def probe_publish_rate(pool) -> dict:
    """Probe: Alert if no posts published in 3 days."""
    try:
        row = await pool.fetchrow("""
            SELECT COUNT(*) as c, MAX(published_at) as last_published
            FROM posts
            WHERE published_at > NOW() - INTERVAL '3 days' AND status = 'published'
        """)
        count = row["c"] if row else 0
        last = row["last_published"] if row else None
        if count == 0:
            return {
                "ok": False,
                "posts_3d": 0,
                "last_published": str(last) if last else "never",
                "detail": f"0 posts published in 3 days (last: {last or 'never'})",
            }
        return {
            "ok": True,
            "posts_3d": count,
            "last_published": str(last),
            "detail": f"{count} post(s) published in last 3 days",
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_cost_freshness(pool) -> dict:
    """Probe: Alert if cost_logs haven't been written in 24 hours. Correlates with approval queue."""
    try:
        # Try with cost_type column; fall back if migration hasn't run yet
        try:
            row = await pool.fetchrow("""
                SELECT
                    (SELECT MAX(created_at) FROM cost_logs WHERE cost_type IS NULL OR cost_type = 'inference') as last_inference,
                    (SELECT MAX(created_at) FROM cost_logs) as last_any,
                    (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'awaiting_approval') as approval_queue
            """)
        except Exception:
            row = await pool.fetchrow("""
                SELECT
                    (SELECT MAX(created_at) FROM cost_logs) as last_inference,
                    (SELECT MAX(created_at) FROM cost_logs) as last_any,
                    (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'awaiting_approval') as approval_queue
            """)
        if not row or not row["last_any"]:
            return {"ok": True, "detail": "no cost_logs entries yet (pipeline idle)"}
        from datetime import datetime
        # Check inference costs specifically (electricity logs every 5 min)
        last = row["last_inference"] or row["last_any"]
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        age_hours = (datetime.now(UTC) - last).total_seconds() / 3600
        approval_queue = row["approval_queue"] or 0
        stale = age_hours > 24

        # Correlate: if stale AND approval queue is full, explain the root cause
        if stale and approval_queue >= 3:
            return {
                "ok": True,  # Not a real failure — root cause is approval queue
                "status": "expected_idle",
                "age_hours": round(age_hours, 1),
                "approval_queue": approval_queue,
                "detail": f"inference idle {age_hours:.0f}h — approval queue full ({approval_queue}/3), pipeline throttled",
            }
        return {
            "ok": not stale,
            "age_hours": round(age_hours, 1),
            "detail": f"inference costs last entry {age_hours:.1f}h ago" + (" — STALE" if stale else ""),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_podcast_health(pool) -> dict:
    """Probe: Check podcast generation is active (episode in last 7 days or no episodes expected)."""
    try:
        # Check if any podcast episodes exist at all
        row = await pool.fetchrow("""
            SELECT COUNT(*) as total,
                   MAX(updated_at) as last_gen
            FROM pipeline_tasks_view
            WHERE task_type = 'podcast' OR topic ILIKE '%podcast%'
        """)
        total = row["total"] if row else 0
        if total == 0:
            return {"ok": True, "detail": "no podcast tasks found (feature may not be active)"}
        last = row["last_gen"]
        if last:
            from datetime import datetime
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            age_days = (datetime.now(UTC) - last).total_seconds() / 86400
            stale = age_days > 7
            return {
                "ok": not stale,
                "total_episodes": total,
                "last_activity_days": round(age_days, 1),
                "detail": f"podcast last activity {age_days:.1f}d ago" + (" — stale" if stale else ""),
            }
        return {"ok": True, "total_episodes": total, "detail": f"{total} podcast tasks, activity ongoing"}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_newsletter_health(pool) -> dict:
    """Probe: Check newsletter delivery is working if subscribers exist."""
    try:
        # Check subscriber count
        sub_row = await pool.fetchrow("""
            SELECT COUNT(*) as c FROM newsletter_subscribers
            WHERE confirmed = true
        """)
        subs = sub_row["c"] if sub_row else 0
        if subs == 0:
            return {"ok": True, "subscribers": 0, "detail": "no confirmed subscribers yet"}

        # Check last send
        send_row = await pool.fetchrow("""
            SELECT MAX(sent_at) as last_sent FROM campaign_email_logs
        """)
        if not send_row or not send_row["last_sent"]:
            return {
                "ok": False,
                "subscribers": subs,
                "detail": f"{subs} subscribers but no sends recorded — check newsletter service",
            }
        from datetime import datetime
        last = send_row["last_sent"]
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        age_days = (datetime.now(UTC) - last).total_seconds() / 86400
        stale = age_days > 7
        return {
            "ok": not stale,
            "subscribers": subs,
            "last_send_days": round(age_days, 1),
            "detail": f"{subs} subs, last send {age_days:.1f}d ago" + (" — overdue" if stale else ""),
        }
    except Exception as e:
        # Table might not exist yet
        if "does not exist" in str(e):
            return {"ok": True, "detail": "newsletter tables not created yet"}
        return {"ok": False, "detail": str(e)[:200]}


# ---------------------------------------------------------------------------
# P2 — Quality & reliability probes
# ---------------------------------------------------------------------------


async def probe_embeddings_freshness(pool) -> dict:
    """Probe: Check embeddings are up to date with published posts."""
    try:
        row = await pool.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM posts WHERE status = 'published') as total_posts,
                (SELECT COUNT(DISTINCT source_id) FROM embeddings WHERE source_type = 'post') as embedded_posts,
                (SELECT MAX(created_at) FROM embeddings) as last_embedding
        """)
        total = row["total_posts"] if row else 0
        embedded = row["embedded_posts"] if row else 0
        last = row["last_embedding"]
        gap = total - embedded
        stale = gap > 3  # Allow small buffer
        detail = f"{embedded}/{total} posts embedded"
        if last:
            from datetime import datetime
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            age_hours = (datetime.now(UTC) - last).total_seconds() / 3600
            detail += f", last {age_hours:.0f}h ago"
        if stale:
            detail += f" — {gap} posts missing embeddings"
        return {
            "ok": not stale,
            "total_posts": total,
            "embedded_posts": embedded,
            "gap": gap,
            "detail": detail,
        }
    except Exception as e:
        if "does not exist" in str(e):
            return {"ok": True, "detail": "embeddings table not created yet"}
        return {"ok": False, "detail": str(e)[:200]}


def _check_r2_sync(r2_url: str) -> dict:
    """Sync helper: GET-with-Range to verify R2 CDN reachability. Called via
    asyncio.to_thread from the async probe so the urllib call doesn't
    block the event loop."""
    try:
        # Use GET with Range header to minimize data transfer (R2 blocks HEAD)
        req = urllib.request.Request(
            f"{r2_url}/podcast/feed.xml",
            headers={"User-Agent": "brain-probe"},
        )
        req.add_header("Range", "bytes=0-64")
        resp = urllib.request.urlopen(req, timeout=10)
        ok = resp.status in (200, 206)
        return {
            "ok": ok,
            "status_code": resp.status,
            "detail": f"R2 CDN reachable (HTTP {resp.status})",
        }
    except urllib.error.HTTPError as e:
        # 404 is OK — means R2 is reachable, feed just doesn't exist yet
        # 403 with GET likely means the file doesn't exist on public bucket
        if e.code in (404, 403):
            return {"ok": True, "status_code": e.code, "detail": f"R2 CDN reachable (feed.xml: HTTP {e.code})"}
        return {"ok": False, "status_code": e.code, "detail": f"R2 CDN error: HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "detail": f"R2 CDN unreachable: {str(e)[:100]}"}


async def probe_r2_connectivity(_pool) -> dict:
    """Probe: Verify R2 CDN is reachable by fetching the podcast feed."""
    return await asyncio.to_thread(
        _check_r2_sync,
        os.getenv("R2_PUBLIC_URL", "https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev"),
    )


async def probe_traffic_anomaly(pool) -> dict:
    """Probe: Alert if daily traffic drops >60% vs 7-day average."""
    try:
        row = await pool.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM page_views
                 WHERE created_at >= date_trunc('day', NOW())) as today,
                (SELECT COUNT(*) / 7.0 FROM page_views
                 WHERE created_at >= NOW() - INTERVAL '7 days') as daily_avg
        """)
        today = row["today"] if row else 0
        avg = float(row["daily_avg"]) if row and row["daily_avg"] else 0
        if avg < 10:
            return {"ok": True, "today": today, "avg": round(avg, 1), "detail": "not enough history for anomaly detection"}
        drop_pct = ((avg - today) / avg * 100) if avg > 0 else 0
        anomaly = drop_pct > 60
        return {
            "ok": not anomaly,
            "today": today,
            "daily_avg": round(avg, 1),
            "drop_pct": round(drop_pct, 1),
            "detail": f"{today} views today vs {avg:.0f}/day avg ({drop_pct:.0f}% {'drop — ANOMALY' if anomaly else 'change'})",
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_quality_trend(pool) -> dict:
    """Probe: Detect declining quality scores (7d vs prior 7d)."""
    try:
        row = await pool.fetchrow("""
            SELECT
                (SELECT AVG(quality_score) FROM pipeline_tasks_view
                 WHERE quality_score IS NOT NULL AND updated_at > NOW() - INTERVAL '7 days') as recent_avg,
                (SELECT AVG(quality_score) FROM pipeline_tasks_view
                 WHERE quality_score IS NOT NULL
                   AND updated_at BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days') as prior_avg
        """)
        recent = float(row["recent_avg"]) if row and row["recent_avg"] else None
        prior = float(row["prior_avg"]) if row and row["prior_avg"] else None
        if recent is None:
            return {"ok": True, "detail": "no quality scores in last 7 days (pipeline idle)"}
        if prior is None:
            return {"ok": True, "recent_avg": round(recent, 1), "detail": f"recent avg {recent:.1f}, no prior data to compare"}
        decline = prior - recent
        declining = decline > 10
        return {
            "ok": not declining,
            "recent_avg": round(recent, 1),
            "prior_avg": round(prior, 1),
            "decline": round(decline, 1),
            "detail": f"quality {recent:.1f} (was {prior:.1f})" + (f" — declining {decline:.0f}pts" if declining else ""),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_topic_quality(pool) -> dict:
    """Probe: Monitor rejection rate and attribute the dominant driver (7d).

    Rejections come from multiple upstream stages, not just low quality.
    Reporting just "72% rejected — topics too low quality" when 0% of
    tasks failed the quality threshold was misleading (issue #235) —
    the actual driver in that case was semantic dedup. The probe now
    breaks down rejections by audit-log category and names the winner
    in the detail message.
    """
    try:
        row = await pool.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'rejected') as rejected,
                COUNT(*) FILTER (WHERE quality_score IS NOT NULL AND quality_score < 70) as low_quality
            FROM pipeline_tasks_view
            WHERE created_at > NOW() - INTERVAL '7 days'
        """)
        total = row["total"] if row else 0
        rejected = row["rejected"] if row else 0
        low_quality = row["low_quality"] if row else 0
        if total == 0:
            return {"ok": True, "detail": "no tasks created in last 7 days (pipeline idle)"}
        rejection_rate = rejected / total * 100
        low_quality_rate = low_quality / total * 100

        # Attribute rejections to their actual drivers by counting the
        # rejection-category audit events in the same 7d window. If a
        # task hits multiple gates, the first-written event wins — that
        # matches the operator-facing reality (what stopped it first).
        driver_rows = await pool.fetch("""
            SELECT event_type, COUNT(*) AS n
            FROM audit_log
            WHERE event_type IN (
                'semantic_dedup_rejected',
                'qa_rejected',
                'topic_rejected',
                'title_not_original',
                'content_validation_rejected'
            )
              AND timestamp > NOW() - INTERVAL '7 days'
            GROUP BY event_type
            ORDER BY n DESC
            LIMIT 3
        """)
        drivers = {r["event_type"]: r["n"] for r in driver_rows}
        top_driver = next(iter(drivers), None)

        healthy = rejection_rate <= 30
        if healthy:
            suffix = ""
        elif top_driver:
            # Be specific about the cause: "dedup" or "quality", not a
            # generic "low quality" claim when quality wasn't the issue.
            label = {
                "semantic_dedup_rejected": "duplicate topics (feeds re-surfacing covered ground)",
                "qa_rejected": "QA threshold (tighten writer or loosen gate)",
                "topic_rejected": "topic selector rejecting (filters too strict)",
                "title_not_original": "titles colliding with web content",
                "content_validation_rejected": "content validator failing",
            }.get(top_driver, top_driver)
            suffix = f" — driver: {label} ({drivers[top_driver]}/{rejected})"
        else:
            suffix = " — cause unknown (no matching audit events)"

        return {
            "ok": healthy,
            "total_tasks": total,
            "rejected": rejected,
            "rejection_rate": round(rejection_rate, 1),
            "low_quality_count": low_quality,
            "low_quality_rate": round(low_quality_rate, 1),
            "drivers": drivers,
            "top_driver": top_driver,
            "detail": (
                f"{rejection_rate:.0f}% rejected, "
                f"{low_quality_rate:.0f}% below 70 quality ({total} tasks)"
                + suffix
            ),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_pipeline_throughput(pool) -> dict:
    """Probe: Compare 7-day publish throughput vs prior 7 days."""
    try:
        row = await pool.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM posts
                 WHERE status = 'published' AND published_at > NOW() - INTERVAL '7 days') as recent,
                (SELECT COUNT(*) FROM posts
                 WHERE status = 'published'
                   AND published_at BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days') as prior
        """)
        recent = row["recent"] if row else 0
        prior = row["prior"] if row else 0
        if prior == 0:
            return {"ok": True, "recent_7d": recent, "prior_7d": prior,
                    "detail": f"{recent} posts this week, no prior data to compare"}
        change_pct = (recent - prior) / prior * 100
        concerning = change_pct < -50
        return {
            "ok": not concerning,
            "recent_7d": recent,
            "prior_7d": prior,
            "change_pct": round(change_pct, 1),
            "detail": f"{recent} posts (was {prior}), {change_pct:+.0f}% change"
            + ("" if not concerning else " — throughput dropped >50%"),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_cadence_slo(pool) -> dict:
    """Probe: page when ACTUAL publish output falls materially below the
    operator-CONFIGURED cadence target — within hours, not days.

    Why this exists (issue #525)
    ----------------------------
    On 2026-05-28 a cadence change quietly slowed the pipeline and NO probe
    caught it: ``probe_publish_rate`` only fires on 0 posts in 3 days, and
    ``probe_pipeline_throughput`` only fires on a >50% drop vs the prior
    7 days. Both are too coarse to notice "producing materially less than
    the target" promptly.

    This probe compares actual output against a CONFIGURED cadence stored
    in app_settings — deliberately NOT derived from
    ``prefect_content_flow_cron`` (that cron is the flow's tick/drain rate,
    ~every 2 min, not the content *production target*).

    Settings (all DB-first per project rule; defaults seeded in
    ``services/settings_defaults.py`` + a seed migration):

    * ``cadence_slo_enabled`` (default ``true``) — skip cleanly when false.
    * ``cadence_slo_expected_posts_per_day`` (default ``1``) — the target.
    * ``cadence_slo_window_hours`` (default ``24``) — trailing window.
    * ``cadence_slo_shortfall_ratio`` (default ``0.5``) — page when
      ``actual < ratio * expected_for_window``.

    expected_for_window = expected_posts_per_day * (window_hours / 24)
    """
    try:
        # Load the four settings in one round-trip. Missing rows fall back to
        # the documented defaults (a fresh DB seeds them via
        # settings_defaults + the seed migration, but a brain that races
        # ahead of the seeder still behaves predictably).
        rows = await pool.fetch(
            "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])",
            [
                "cadence_slo_enabled",
                "cadence_slo_expected_posts_per_day",
                "cadence_slo_window_hours",
                "cadence_slo_shortfall_ratio",
            ],
        )
        settings = {r["key"]: r["value"] for r in rows}

        def _bool(val: str | None, default: bool) -> bool:
            if val is None or val == "":
                return default
            return str(val).strip().lower() in ("1", "true", "yes", "on")

        def _float(val: str | None, default: float) -> float:
            if val is None or val == "":
                return default
            try:
                return float(val)
            except (TypeError, ValueError):
                return default

        enabled = _bool(settings.get("cadence_slo_enabled"), True)
        if not enabled:
            return {
                "ok": True,
                "status": "disabled",
                "detail": "cadence SLO disabled (cadence_slo_enabled=false)",
            }

        expected_per_day = _float(
            settings.get("cadence_slo_expected_posts_per_day"), 1.0
        )
        window_hours = _float(settings.get("cadence_slo_window_hours"), 24.0)
        shortfall_ratio = _float(settings.get("cadence_slo_shortfall_ratio"), 0.5)

        expected_for_window = expected_per_day * (window_hours / 24.0)
        threshold = shortfall_ratio * expected_for_window

        # Count posts actually published in the trailing window.
        # window_hours is interpolated (not a bind param) because asyncpg
        # can't parameterise an INTERVAL literal cleanly; it's float-coerced
        # above so no injection surface (matches sibling probes' INTERVAL use).
        row = await pool.fetchrow(
            f"""
            SELECT COUNT(*) AS c, MAX(published_at) AS last_published
            FROM posts
            WHERE status = 'published'
              AND published_at >= NOW() - INTERVAL '{window_hours} hours'
            """
        )
        actual = row["c"] if row else 0
        last = row["last_published"] if row else None

        breach = actual < threshold
        detail = (
            f"cadence SLO breach: {actual} posts in {window_hours:g}h, "
            f"expected ~{expected_for_window:.1f} "
            f"(target {expected_per_day:g}/day)"
            if breach
            else (
                f"cadence OK: {actual} posts in {window_hours:g}h "
                f"(expected ~{expected_for_window:.1f}, "
                f"target {expected_per_day:g}/day)"
            )
        )
        return {
            "ok": not breach,
            "actual": actual,
            "expected_for_window": round(expected_for_window, 2),
            "expected_posts_per_day": expected_per_day,
            "window_hours": window_hours,
            "shortfall_ratio": shortfall_ratio,
            "threshold": round(threshold, 2),
            "last_published": str(last) if last else "never",
            "detail": detail,
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


# All probes in execution order
PROBES = {
    # Infrastructure
    "db_ping": probe_db_ping,
    "ollama_models": probe_ollama_models,
    "ollama_embedding": probe_ollama_embedding,
    "content_gen": probe_content_gen,
    "grafana_datasources": probe_grafana_datasources,
    "public_site": probe_public_site,
    "scheduled_tasks": probe_scheduled_tasks,
    "disk_space": probe_disk_space,
    "gpu_temperature": probe_gpu_temperature,
    "r2_connectivity": probe_r2_connectivity,
    # Pipeline health (P0)
    "stuck_tasks": probe_stuck_tasks,
    "approval_queue": probe_approval_queue,
    "failed_task_spike": probe_failed_task_spike,
    "worker_error_rate": probe_worker_error_rate,
    # Content quality
    "quality_score": probe_quality_score,
    "quality_trend": probe_quality_trend,
    # Business continuity (P1)
    "publish_rate": probe_publish_rate,
    "cost_freshness": probe_cost_freshness,
    "podcast_health": probe_podcast_health,
    "newsletter_health": probe_newsletter_health,
    # Data health
    "research_service": probe_research_service,
    "image_search": probe_image_search,
    "embeddings_freshness": probe_embeddings_freshness,
    # Analytics
    "traffic_anomaly": probe_traffic_anomaly,
    # Topic & throughput monitoring
    "topic_quality": probe_topic_quality,
    "pipeline_throughput": probe_pipeline_throughput,
    # Cadence SLO — actual output vs operator-configured target (issue #525)
    "cadence_slo": probe_cadence_slo,
}

# Track consecutive failures for alerting
_failure_counts: dict[str, int] = {}
ALERT_AFTER_FAILURES = 3  # Alert on Telegram after 3 consecutive failures

# Probes whose alerts are now owned by Prometheus + Alertmanager
# (Phase D cutover). They still RUN — their results land in brain_knowledge
# for observability and trigger remediation — they just don't fire duplicate
# Telegram alerts. Moving them fully out of this file is tracked as a
# follow-up; this set is the "one source of truth for pages" boundary.
#
# Keep in sync with infrastructure/prometheus/alerts/infrastructure.yml
# + app_settings prometheus.rule.* (see services/prometheus_rule_builder.py).
PROMETHEUS_COVERED_PROBES: frozenset[str] = frozenset({
    "db_ping",                # → PoindexterPostgresDown
    "ollama_models",          # → PoindexterOllamaDown
    "embeddings_freshness",   # → EmbeddingsStale
    "cost_freshness",         # → DailySpend*/MonthlySpend*
    "publish_rate",           # → NoPublishedPostsRecently
})


def _alertmanager_healthy_blocking(base_url: str, timeout: float = 3.0) -> bool:
    """GET ``/-/healthy``; True only on HTTP 200. Blocking (urllib)."""
    url = f"{base_url.rstrip('/')}/-/healthy"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310 — fixed internal scheme
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


async def _alertmanager_healthy() -> bool:
    """Is Alertmanager up and able to deliver alerts?

    Fail-safe (#304): any inability to CONFIRM health returns False, so the
    PROMETHEUS_COVERED suppression is disabled and the brain pages directly
    rather than deferring to an Alertmanager that may be down. Never raises.
    """
    try:
        return await asyncio.to_thread(_alertmanager_healthy_blocking, ALERTMANAGER_URL)
    except Exception:  # pragma: no cover — to_thread plumbing
        return False


async def run_health_probes(pool, notify_fn=None):
    """Run all due health probes, store results in brain_knowledge, alert on failures."""
    await _sync_config_from_db(pool)
    results = {}

    # Whether Prometheus/Alertmanager can actually deliver right now. When it
    # can't, the brain must NOT suppress alerts for PROMETHEUS_COVERED probes
    # (otherwise a real outage + Alertmanager outage = double-blind). Checked
    # once per cycle; only consulted for covered-probe suppression below.
    am_healthy = await _alertmanager_healthy()
    if not am_healthy:
        logger.warning(
            "[PROBES] Alertmanager unhealthy/unreachable at %s — disabling "
            "PROMETHEUS_COVERED alert suppression for this cycle (brain will "
            "page covered-probe failures directly)", ALERTMANAGER_URL,
        )

    for name, probe_fn in PROBES.items():
        if not _is_due(name):
            continue

        _mark_run(name)
        # Per-probe child span — gives Tempo a flame-graph entry per probe so
        # slow/failing probes are visible instead of aggregating under the
        # parent run_health_probes span (issue #176).
        with _tracer.start_as_current_span(
            f"brain.probe.{name}",
            attributes={"probe.name": name},
        ) as span:
            start = time.monotonic()
            try:
                try:
                    result = await probe_fn(pool)
                except Exception as e:
                    # ``crashed`` distinguishes a bug in the PROBE itself (the
                    # monitoring code threw) from a clean ``ok=False`` meaning
                    # the monitored SERVICE is down. They need different
                    # operator responses (#304) and a crash always pages.
                    result = {
                        "ok": False,
                        "detail": f"probe crashed: {e}",
                        "crashed": True,
                    }
                    span.record_exception(e)
                span.set_attribute("probe.ok", bool(result.get("ok", False)))
            finally:
                span.set_attribute(
                    "probe.duration_s", time.monotonic() - start,
                )

        results[name] = result
        ok = result.get("ok", False)

        # Store in brain_knowledge
        try:
            await pool.execute("""
                INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, tags)
                VALUES ($1, $2, $3, $4, 'health_probe', $5)
                ON CONFLICT (entity, attribute)
                DO UPDATE SET value = EXCLUDED.value, confidence = EXCLUDED.confidence,
                             updated_at = NOW(), tags = EXCLUDED.tags
            """,
                f"probe.{name}",
                "health_status",
                json.dumps(result),
                1.0 if ok else 0.3,
                ["health", "probe", name],
            )
        except Exception as e:
            logger.debug("[PROBES] Failed to store result for %s: %s", name, e)

        # Track failures and alert.
        # Probes in PROMETHEUS_COVERED_PROBES no longer Telegram-alert —
        # Prometheus + Alertmanager own human-visible alerts for those
        # signals. We still track failure counts so remediation logic
        # fires and Gitea issues get filed.
        prom_covered = name in PROMETHEUS_COVERED_PROBES
        crashed = bool(result.get("crashed"))
        # Suppress brain-side paging ONLY when Prometheus/Alertmanager truly
        # owns this signal AND can deliver. A probe CRASH is never suppressed
        # — it means the monitoring code is broken, which Prometheus does not
        # cover. And when Alertmanager is down, suppression would be a
        # double-blind, so we page directly (#304).
        suppress = prom_covered and am_healthy and not crashed
        if ok:
            if (
                _failure_counts.get(name, 0) >= ALERT_AFTER_FAILURES
                and notify_fn
                and not suppress
            ):
                await _maybe_await(
                    notify_fn(f"✅ Probe '{name}' recovered: {result.get('detail', '')}")
                )
            _failure_counts[name] = 0
        else:
            _failure_counts[name] = _failure_counts.get(name, 0) + 1
            logger.warning("[PROBES] %s %s (%d consecutive): %s",
                           name, "CRASHED" if crashed else "FAILED",
                           _failure_counts[name], result.get("detail", ""))
            if _failure_counts[name] == ALERT_AFTER_FAILURES:
                detail = result.get('detail', 'unknown error')
                if notify_fn and not suppress:
                    if crashed:
                        await _maybe_await(notify_fn(
                            f"⚠️ Probe '{name}' ERRORED {ALERT_AFTER_FAILURES}x "
                            f"(bug in the probe — monitoring is BLIND for this "
                            f"check, not necessarily a service outage): {detail}"
                        ))
                    elif prom_covered and not am_healthy:
                        await _maybe_await(notify_fn(
                            f"🔴 Probe '{name}' failed {ALERT_AFTER_FAILURES}x "
                            f"AND Alertmanager is unreachable — Prometheus "
                            f"coverage is BLIND, brain is paging directly: {detail}"
                        ))
                    else:
                        await _maybe_await(notify_fn(
                            f"🔴 Probe '{name}' failed {ALERT_AFTER_FAILURES}x: {detail}"
                        ))
                # The Gitea-issue auto-create paper trail was removed when
                # Gitea was decommissioned (2026-04-30). The notify_operator
                # call above is now the only escalation; the brain's
                # alert_dispatcher writes a row into alert_events for the
                # broader monitoring view.

    # --- Self-healing: execute remediation actions for persistent failures ---
    for name, count in _failure_counts.items():
        if count >= ALERT_AFTER_FAILURES and name in REMEDIATIONS:
            await _try_remediation(name, results.get(name, {}), notify_fn, pool=pool)

    if results:
        passed = sum(1 for r in results.values() if r.get("ok"))
        total = len(results)
        logger.info("[PROBES] Ran %d probes: %d passed, %d failed", total, passed, total - passed)

    return results


# ---------------------------------------------------------------------------
# Self-healing: remediation actions
# ---------------------------------------------------------------------------

# Track remediation cooldowns (prevent restart loops)
_last_remediation: dict[str, float] = {}
REMEDIATION_COOLDOWN = 900  # 15 minutes between remediation attempts per probe


async def _call_agent_recovery(pool, service: str) -> tuple[bool, str]:
    """POST to the host recovery agent to restart a host-side service.

    Container-safe: the agent runs on the Windows host at the URL stored in
    ``mcp_http_probe_recovery_url`` and is reachable via ``host.docker.internal``
    from the brain container. Mirrors ``mcp_http_probe._try_http_recovery`` but
    reads the URL/token from the DB and is called from health_probes remediation.
    """
    if pool is None:
        return False, "pool unavailable — cannot read recovery agent config"

    recovery_url = (await _read_app_setting(pool, RECOVERY_URL_KEY, "")).strip()
    recovery_token = (await _read_app_setting(pool, RECOVERY_TOKEN_KEY, "")).strip()

    if not recovery_url or not recovery_token:
        return False, "recovery agent URL/token not configured in app_settings"

    if httpx is None:
        return False, "httpx not installed in brain image"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                recovery_url,
                json={"service": service},
                headers={"Authorization": f"Bearer {recovery_token}"},
            )
            ok = 200 <= response.status_code < 300
            detail = f"HTTP {response.status_code}"
            if not ok:
                try:
                    detail += f" — {response.json().get('detail', '')}"
                except Exception:
                    pass
            return ok, detail
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _restart_container(container_name: str) -> tuple[bool, str]:
    """Restart a Docker container. Returns (success, message)."""
    try:
        result = subprocess.run(
            ["docker", "restart", container_name],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return True, f"Restarted {container_name}"
        return False, f"docker restart failed: {result.stderr[:200]}"
    except Exception as e:
        return False, f"restart error: {str(e)[:200]}"


async def _try_remediation(probe_name: str, result: dict, notify_fn=None, *, pool=None):
    """Execute a remediation action if cooldown has elapsed."""
    last = _last_remediation.get(probe_name, 0)
    if (time.time() - last) < REMEDIATION_COOLDOWN:
        return  # Too soon since last attempt

    action = REMEDIATIONS.get(probe_name)
    if not action:
        return

    logger.info("[SELF-HEAL] Attempting remediation for '%s': %s",
                probe_name, action.get("description", "unknown"))

    ok, msg = False, "no action taken"
    action_type = action.get("type")

    if action_type == "restart_container":
        ok, msg = _restart_container(action["container"])
    elif action_type == "restart_multiple":
        msgs = []
        for container in action["containers"]:
            c_ok, c_msg = _restart_container(container)
            msgs.append(c_msg)
            ok = ok or c_ok
        msg = "; ".join(msgs)
    elif action_type == "recover_via_agent":
        ok, msg = await _call_agent_recovery(pool, action["service"])

    _last_remediation[probe_name] = time.time()

    detail = result.get("detail", "")
    if notify_fn:
        emoji = "🔧" if ok else "⚠️"
        await _maybe_await(notify_fn(
            f"{emoji} Self-heal '{probe_name}': {msg}\n"
            f"Trigger: {detail}"
        ))
    logger.info("[SELF-HEAL] %s — %s (probe: %s)",
                "SUCCESS" if ok else "FAILED", msg, probe_name)


# Map probe names to remediation actions.
# Only non-destructive, data-safe actions — restart services, never delete data.
REMEDIATIONS = {
    "worker_error_rate": {
        "type": "restart_container",
        "container": "poindexter-worker",
        "description": "Restart worker when error rate is critical",
    },
    "stuck_tasks": {
        "type": "restart_container",
        "container": "poindexter-worker",
        "description": "Restart worker when tasks are stuck",
    },
    "grafana_datasources": {
        "type": "restart_container",
        "container": "poindexter-grafana",
        "description": "Restart Grafana when datasources are unhealthy",
    },
    "public_site": {
        "type": "restart_container",
        "container": "poindexter-worker",
        "description": "Restart worker when public site API is down",
    },
    # Ollama is a host process (not a Docker container) — cannot docker-restart
    # it. Both probes target the same service: ollama_models detects total
    # Ollama outage; ollama_embedding detects the embed-refuses-while-chat-works
    # failure mode. Both route through the host recovery agent.
    "ollama_models": {
        "type": "recover_via_agent",
        "service": "ollama",
        "description": "Restart Ollama via recovery agent when model list is unreachable",
    },
    "ollama_embedding": {
        "type": "recover_via_agent",
        "service": "ollama",
        "description": "Restart Ollama via recovery agent when embed endpoint fails",
    },
}
