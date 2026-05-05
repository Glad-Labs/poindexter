"""Prometheus metrics exporter — exposes pipeline state via ``/metrics``.

Phase D's additive-first step (GitHub #68). Worker exposes Prometheus
exposition-format metrics at ``/metrics`` alongside the existing
``/api/metrics`` JSON endpoint. Nothing is deleted; this is purely new
surface. Once Alertmanager rules are in place and parallel-run
confidence is high, the brain daemon's own probe loop can start
deleting functions that now have metric counterparts.

## Metrics exposed

Infrastructure:
- ``poindexter_worker_up`` — gauge, always 1 while the worker is
  serving (lets Alertmanager catch scrape failures)
- ``poindexter_postgres_connected`` — gauge, 1 if pool round-trips OK
- ``poindexter_postgres_query_latency_seconds`` — histogram of ``SELECT 1``
  round-trip (Gitea #238 — recovers the latency nuance the deprecated
  ``db_ping`` probe measured)
- ``pg_connections_used`` — gauge, total server-side backends from
  ``pg_stat_activity`` (GH-92 — catches approaching ``max_connections``)
- ``pg_connections_max`` — gauge, server-side ``max_connections`` from
  ``current_setting()`` (GH-92 — denominator for utilization alerts)
- ``poindexter_ollama_reachable`` — gauge, 1 if ``/api/tags`` returns 200
- ``poindexter_ollama_model_count`` — gauge, number of models returned
  by ``/api/tags`` (Gitea #238 — catches "Ollama up but no models")

Content pipeline:
- ``poindexter_tasks_total`` — counter by ``status``
- ``poindexter_embeddings_total`` — gauge by ``source_table``
- ``poindexter_embeddings_missing_posts`` — gauge, published posts
  without a corresponding ``embeddings`` row (Gitea #238 — catches
  "new posts stopped getting embedded" even if overall rate stays up)
- ``poindexter_posts_total`` — gauge by ``status``
- ``poindexter_approval_queue_length`` — gauge, rows in
  ``content_tasks`` with ``status = 'awaiting_approval'`` (Gitea #238
  — used as the ``unless`` cross-check against cost alerts so they
  don't fire while the pipeline is throttling on pending approvals)
- ``poindexter_unapplied_migrations_count`` — gauge, count of ``.py``
  files in ``services/migrations/`` not yet recorded in
  ``schema_migrations`` (GH-227 — catches the "container updated but
  worker not restarted, running on stale schema" failure mode that the
  ``/api/health`` JSON probe already surfaces but Prometheus didn't see)

Cost:
- ``poindexter_daily_spend_usd`` — gauge
- ``poindexter_monthly_spend_usd`` — gauge

All metric values come from the same DB queries the brain probes
already run, so the values stay consistent between the legacy probe
path and the new Prometheus path during the migration.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metric definitions (module-level singletons — Prometheus convention)
# ---------------------------------------------------------------------------


WORKER_UP = Gauge(
    "poindexter_worker_up",
    "1 if the worker is serving requests, 0 otherwise",
)

POSTGRES_CONNECTED = Gauge(
    "poindexter_postgres_connected",
    "1 if the local Postgres round-trip succeeded in the last refresh",
)

# Buckets tuned for "healthy" Postgres SELECT 1: expect most scrapes
# under 10ms, alert-worthy at ~100ms+. 10s is the failure tail.
POSTGRES_QUERY_LATENCY = Histogram(
    "poindexter_postgres_query_latency_seconds",
    "Round-trip latency of a SELECT 1 liveness probe against the pool",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0, 10.0),
)

# GH-92: server-wide connection utilization. These are intentionally
# *not* prefixed ``poindexter_`` because they describe the Postgres
# server as a whole, not the worker. Grafana's standard postgres-exporter
# dashboards use these names, which makes the alert rule reusable if we
# ever swap in a full postgres-exporter sidecar.
PG_CONNECTIONS_USED = Gauge(
    "pg_connections_used",
    "Server-side Postgres backends from pg_stat_activity (all databases, all apps)",
)

PG_CONNECTIONS_MAX = Gauge(
    "pg_connections_max",
    "Postgres max_connections server setting (denominator for utilization alerts)",
)

OLLAMA_REACHABLE = Gauge(
    "poindexter_ollama_reachable",
    "1 if Ollama /api/tags returned 200 in the last refresh",
)

OLLAMA_MODEL_COUNT = Gauge(
    "poindexter_ollama_model_count",
    "Number of models returned by Ollama /api/tags (0 when Ollama is up but empty)",
)

EMBEDDINGS_TOTAL = Gauge(
    "poindexter_embeddings_total",
    "Total embeddings rows, labeled by source_table",
    ["source_table"],
)

EMBEDDINGS_MISSING_POSTS = Gauge(
    "poindexter_embeddings_missing_posts",
    "Published posts without a corresponding embeddings row (source_table='posts')",
)

POSTS_TOTAL = Gauge(
    "poindexter_posts_total",
    "Total posts, labeled by status",
    ["status"],
)

APPROVAL_QUEUE_LENGTH = Gauge(
    "poindexter_approval_queue_length",
    "Content tasks currently in status='awaiting_approval' (pipeline throttle signal)",
)

# GH-227: Count of migration files on disk not yet present in
# schema_migrations. >0 means the worker is on a stale schema (typically
# because a container was rebuilt with new code but the running process
# wasn't restarted). The /api/health JSON probe already surfaces this,
# but it wasn't visible to Prometheus / Alertmanager.
UNAPPLIED_MIGRATIONS_COUNT = Gauge(
    "poindexter_unapplied_migrations_count",
    "Number of migration .py files in services/migrations/ not yet present in "
    "schema_migrations table. >0 means worker is on stale schema.",
)

TASKS_CREATED = Counter(
    "poindexter_tasks_created_total",
    "Tasks created (lifetime counter — scrape-only, resets on restart)",
)

# GH-90: surface stale-sweeper cancellations so operators notice when the
# race-mitigation kicks in a lot (suggests worker heartbeats are missing
# or stale_task_timeout_minutes is tuned too aggressively). The brain
# daemon stamps ``pipeline_tasks.auto_cancelled_at`` in the same UPDATE
# that flips status='failed'; on each scrape we re-read the cumulative
# count into this Gauge so the value is persistent across worker
# restarts (a raw prometheus Counter would reset to 0 every time the
# worker process cycles). Phase 2 of poindexter#366 moved this off
# pipeline_events onto the column.
AUTO_CANCELLED_TOTAL = Gauge(
    "poindexter_pipeline_auto_cancelled_total",
    "Cumulative count of tasks auto-cancelled by the stale-task sweeper "
    "(read from pipeline_tasks.auto_cancelled_at)",
)

DAILY_SPEND_USD = Gauge(
    "poindexter_daily_spend_usd",
    "Total LLM spend today in USD (rolling 24h from cost_logs)",
)

MONTHLY_SPEND_USD = Gauge(
    "poindexter_monthly_spend_usd",
    "Total LLM spend this month in USD (from cost_logs)",
)


# ---------------------------------------------------------------------------
# Metrics migrated from the legacy /api/prometheus hand-built exposer
# (Gitea #269). Label names + metric names match the legacy strings exactly
# so existing Grafana queries don't break when the old exposer is removed.
# ---------------------------------------------------------------------------

# asyncpg pool metrics. Labeled by ``pool`` ("cloud"/"local") — legacy emitted
# the same label values, keep them identical so dashboards keep working.
DB_POOL_SIZE = Gauge(
    "poindexter_db_pool_size",
    "Current number of connections in pool",
    ["pool"],
)

DB_POOL_IDLE = Gauge(
    "poindexter_db_pool_idle",
    "Number of idle connections in pool",
    ["pool"],
)

DB_POOL_MIN_SIZE = Gauge(
    "poindexter_db_pool_min_size",
    "Minimum pool size setting",
    ["pool"],
)

DB_POOL_MAX_SIZE = Gauge(
    "poindexter_db_pool_max_size",
    "Maximum pool size setting",
    ["pool"],
)

# GPU scheduler — mirrors services.gpu_scheduler.gpu.status. These are all
# snapshot gauges including the "total_seconds" paused metric which the legacy
# exposer emitted as a Counter but populated from a float counter that only
# grows → Gauge semantics are accurate, and Counter would require using
# .inc() on each refresh which doesn't match the monotonically-increasing
# scalar already maintained by the scheduler. Kept as Gauge (matches how
# dashboards already query it: absolute value, not rate()).
GPU_GAMING_DETECTED = Gauge(
    "poindexter_gpu_gaming_detected",
    "Whether gaming/external GPU workload is detected (1=yes)",
)

GPU_GAMING_PAUSED_SECONDS = Gauge(
    "poindexter_gpu_gaming_paused_seconds",
    "Current gaming pause duration in seconds",
)

GPU_GAMING_PAUSED_TOTAL_SECONDS = Gauge(
    "poindexter_gpu_gaming_paused_total_seconds",
    "Total time paused for gaming since worker start",
)

GPU_BUSY = Gauge(
    "poindexter_gpu_busy",
    "Whether the GPU lock is held by the pipeline",
)

# Pipeline throttle (GH-89 AC#2) — mirrors services.pipeline_throttle.get_state
PIPELINE_THROTTLE_ACTIVE = Gauge(
    "poindexter_pipeline_throttle_active",
    "Approval queue is full and pipeline is not advancing (1=throttled)",
)

PIPELINE_THROTTLE_SECONDS_TOTAL = Gauge(
    "poindexter_pipeline_throttle_seconds_total",
    "Cumulative seconds the pipeline spent throttled by the approval queue",
)

PIPELINE_THROTTLE_QUEUE_SIZE = Gauge(
    "poindexter_pipeline_throttle_queue_size",
    "Current awaiting_approval queue size as last observed by the throttle check",
)

PIPELINE_THROTTLE_QUEUE_LIMIT = Gauge(
    "poindexter_pipeline_throttle_queue_limit",
    "Configured max_approval_queue as last observed by the throttle check",
)

# Task counts — from DatabaseService.tasks.get_task_counts(). These overlap
# semantically with POSTS_TOTAL above but run against content_tasks and are
# kept separate because dashboards query them by name.
TASKS_PENDING = Gauge(
    "poindexter_tasks_pending",
    "Number of pending content tasks",
)

TASKS_IN_PROGRESS = Gauge(
    "poindexter_tasks_in_progress",
    "Number of in-progress content tasks",
)

TASKS_AWAITING_APPROVAL = Gauge(
    "poindexter_tasks_awaiting_approval",
    "Number of tasks awaiting approval",
)


# ---------------------------------------------------------------------------
# Refresh — reads the DB + Ollama, updates the Gauges
# ---------------------------------------------------------------------------


async def refresh_metrics(
    pool: Any,
    ollama_url: str,
    db_service: Any = None,
) -> None:
    """Update every Gauge by running fresh queries.

    Called by the ``/metrics`` handler before generating exposition
    output. Prometheus scrapes every 15-30s typically, so the extra DB
    roundtrips are fine.

    Each source is wrapped in its own try/except — one slow query or
    one missing table must not make ``/metrics`` fail, or Prometheus
    will alert on the endpoint being down.

    ``db_service`` is the full ``DatabaseService`` (has ``.tasks``,
    ``.local_pool``, etc.) — required for metrics that don't live on
    the cloud pool alone (task counts, per-pool stats). Passed
    optionally so callers that only hand over a raw ``pool`` keep
    working; the extra metrics just skip in that case.
    """
    import time

    WORKER_UP.set(1)

    # Postgres roundtrip — also record latency so alerts can fire on
    # "DB is up but slow" (Gitea #238).
    try:
        async with pool.acquire() as conn:
            _t0 = time.monotonic()
            val = await conn.fetchval("SELECT 1")
            POSTGRES_QUERY_LATENCY.observe(time.monotonic() - _t0)
        POSTGRES_CONNECTED.set(1 if val == 1 else 0)
    except Exception as e:
        logger.debug("refresh_metrics: postgres check failed: %s", e)
        POSTGRES_CONNECTED.set(0)

    # GH-92: server-side connection utilization. pg_stat_activity counts
    # every backend (ours + gitea + pgadmin + ad-hoc psql). max_connections
    # comes from current_setting() so bumping the server config is
    # reflected without a worker restart.
    try:
        async with pool.acquire() as conn:
            used = await conn.fetchval("SELECT COUNT(*) FROM pg_stat_activity")
            max_conn = await conn.fetchval(
                "SELECT current_setting('max_connections')::int"
            )
        PG_CONNECTIONS_USED.set(int(used or 0))
        PG_CONNECTIONS_MAX.set(int(max_conn or 0))
    except Exception as e:
        logger.debug("refresh_metrics: pg_connections query failed: %s", e)

    # Ollama reachability + model count (Gitea #238 — "up but no models"
    # passed the old gauge; OLLAMA_MODEL_COUNT catches that case).
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            resp = await http.get(f"{ollama_url.rstrip('/')}/api/tags")
        OLLAMA_REACHABLE.set(1 if resp.status_code == 200 else 0)
        if resp.status_code == 200:
            try:
                models = (resp.json() or {}).get("models", [])
                OLLAMA_MODEL_COUNT.set(len(models or []))
            except Exception:
                OLLAMA_MODEL_COUNT.set(0)
        else:
            OLLAMA_MODEL_COUNT.set(0)
    except Exception:
        OLLAMA_REACHABLE.set(0)
        OLLAMA_MODEL_COUNT.set(0)

    # Embeddings by source_table + per-post coverage gap.
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT source_table, COUNT(*) AS n FROM embeddings GROUP BY source_table"
            )
        for r in rows:
            EMBEDDINGS_TOTAL.labels(source_table=r["source_table"] or "unknown").set(r["n"])
    except Exception as e:
        logger.debug("refresh_metrics: embeddings query failed: %s", e)

    # Published posts missing an embedding — catches "3 new posts
    # published, 0 got embedded" even when overall throughput looks fine.
    try:
        async with pool.acquire() as conn:
            gap = await conn.fetchval(
                """
                SELECT COUNT(*) FROM posts p
                WHERE p.status = 'published'
                  AND NOT EXISTS (
                    SELECT 1 FROM embeddings e
                    WHERE e.source_table = 'posts'
                      AND e.source_id = p.id::text
                  )
                """
            )
        EMBEDDINGS_MISSING_POSTS.set(int(gap or 0))
    except Exception as e:
        logger.debug("refresh_metrics: embeddings-gap query failed: %s", e)

    # Posts by status.
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*) AS n FROM posts GROUP BY status"
            )
        for r in rows:
            POSTS_TOTAL.labels(status=r["status"] or "unknown").set(r["n"])
    except Exception as e:
        logger.debug("refresh_metrics: posts query failed: %s", e)

    # Approval queue depth — used as the ``unless`` cross-check against
    # cost alerts so they don't fire while the pipeline is throttling.
    try:
        async with pool.acquire() as conn:
            queue_n = await conn.fetchval(
                "SELECT COUNT(*) FROM content_tasks WHERE status = 'awaiting_approval'"
            )
        APPROVAL_QUEUE_LENGTH.set(int(queue_n or 0))
    except Exception as e:
        logger.debug("refresh_metrics: approval queue query failed: %s", e)

    # GH-90: cumulative count of sweeper auto-cancels. Read from
    # pipeline_tasks.auto_cancelled_at so the value survives worker
    # restarts (a raw Counter would reset to 0 every deploy, making
    # rate() useless on short windows). Phase 2 of poindexter#366
    # moved this off pipeline_events; the partial index makes COUNT
    # cheap.
    try:
        async with pool.acquire() as conn:
            cancelled_n = await conn.fetchval(
                "SELECT COUNT(*) FROM pipeline_tasks "
                "WHERE auto_cancelled_at IS NOT NULL"
            )
        AUTO_CANCELLED_TOTAL.set(int(cancelled_n or 0))
    except Exception as e:
        logger.debug("refresh_metrics: auto_cancelled count query failed: %s", e)

    # GH-227: unapplied migration count.
    # Compares the number of .py files in services/migrations/ to the row
    # count in schema_migrations. A positive value means the running worker
    # is on a stale schema — it pulled a container update with new
    # migration files but the migration runner hasn't applied them
    # (typically because the worker wasn't restarted). The brain's URL
    # probe + /api/health migrations block already surface this, but
    # Prometheus didn't see it until now.
    try:
        from pathlib import Path

        from services import migrations as _migrations_pkg

        migrations_dir = Path(_migrations_pkg.__file__).parent
        on_disk = sum(
            1
            for p in migrations_dir.glob("*.py")
            if p.name != "__init__.py"
        )
        async with pool.acquire() as conn:
            applied_n = await conn.fetchval(
                "SELECT COUNT(*) FROM schema_migrations"
            )
        # max() guards against the (legal but odd) case where someone
        # manually inserted rows for migrations that no longer exist on
        # disk — the alert is "stale schema", not "ghost rows", so we
        # never want a negative value here.
        UNAPPLIED_MIGRATIONS_COUNT.set(max(on_disk - int(applied_n or 0), 0))
    except Exception as e:
        logger.debug("refresh_metrics: unapplied_migrations count failed: %s", e)

    # Spend.
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  COALESCE(SUM(CASE WHEN created_at > NOW() - INTERVAL '24 hours'
                                     THEN cost_usd ELSE 0 END), 0) AS daily,
                  COALESCE(SUM(cost_usd), 0) AS monthly
                FROM cost_logs
                WHERE created_at > date_trunc('month', NOW())
                """
            )
        if row:
            DAILY_SPEND_USD.set(float(row["daily"] or 0))
            MONTHLY_SPEND_USD.set(float(row["monthly"] or 0))
    except Exception as e:
        logger.debug("refresh_metrics: cost_logs query failed: %s", e)

    # -----------------------------------------------------------------
    # Metrics migrated from /api/prometheus (Gitea #269).
    # -----------------------------------------------------------------

    # asyncpg pool stats — per-pool so the label schema matches the
    # legacy exposer. The cloud pool is the one already passed in; the
    # local pool (if distinct) is read off db_service.
    try:
        if pool is not None:
            DB_POOL_SIZE.labels(pool="cloud").set(pool.get_size())
            DB_POOL_IDLE.labels(pool="cloud").set(pool.get_idle_size())
            DB_POOL_MIN_SIZE.labels(pool="cloud").set(pool.get_min_size())
            DB_POOL_MAX_SIZE.labels(pool="cloud").set(pool.get_max_size())
        local_pool = getattr(db_service, "local_pool", None) if db_service else None
        if local_pool is not None and local_pool is not pool:
            DB_POOL_SIZE.labels(pool="local").set(local_pool.get_size())
            DB_POOL_IDLE.labels(pool="local").set(local_pool.get_idle_size())
            DB_POOL_MIN_SIZE.labels(pool="local").set(local_pool.get_min_size())
            DB_POOL_MAX_SIZE.labels(pool="local").set(local_pool.get_max_size())
    except Exception as e:
        logger.debug("refresh_metrics: db_pool stats failed: %s", e)

    # GPU scheduler snapshot.
    try:
        from services.gpu_scheduler import gpu  # local import — avoid boot cycles

        s = gpu.status
        GPU_GAMING_DETECTED.set(1 if s.get("gaming_detected") else 0)
        GPU_GAMING_PAUSED_SECONDS.set(float(s.get("gaming_paused_s") or 0))
        GPU_GAMING_PAUSED_TOTAL_SECONDS.set(float(s.get("total_gaming_paused_s") or 0))
        GPU_BUSY.set(1 if s.get("busy") else 0)
    except Exception as e:
        logger.debug("refresh_metrics: gpu_scheduler status failed: %s", e)

    # Pipeline throttle state (GH-89 AC#2).
    try:
        from services.pipeline_throttle import get_state as _throttle_state

        ts = _throttle_state()
        PIPELINE_THROTTLE_ACTIVE.set(1 if ts.get("active") else 0)
        PIPELINE_THROTTLE_SECONDS_TOTAL.set(float(ts.get("total_seconds") or 0))
        PIPELINE_THROTTLE_QUEUE_SIZE.set(int(ts.get("queue_size") or 0))
        PIPELINE_THROTTLE_QUEUE_LIMIT.set(int(ts.get("queue_limit") or 0))
    except Exception as e:
        logger.debug("refresh_metrics: pipeline_throttle state failed: %s", e)

    # Task counts from content_tasks — needs DatabaseService.tasks.
    try:
        if db_service is not None and getattr(db_service, "tasks", None):
            task_counts = await db_service.tasks.get_task_counts()
            TASKS_PENDING.set(int(getattr(task_counts, "pending", 0) or 0))
            TASKS_IN_PROGRESS.set(int(getattr(task_counts, "in_progress", 0) or 0))
            TASKS_AWAITING_APPROVAL.set(
                int(getattr(task_counts, "awaiting_approval", 0) or 0)
            )
    except Exception as e:
        logger.debug("refresh_metrics: task_counts query failed: %s", e)


def render_exposition() -> tuple[bytes, str]:
    """Return ``(body, content_type)`` for the ``/metrics`` HTTP response."""
    return generate_latest(), CONTENT_TYPE_LATEST
