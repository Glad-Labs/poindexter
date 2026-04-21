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

TASKS_CREATED = Counter(
    "poindexter_tasks_created_total",
    "Tasks created (lifetime counter — scrape-only, resets on restart)",
)

# GH-90: surface stale-sweeper cancellations so operators notice when the
# race-mitigation kicks in a lot (suggests worker heartbeats are missing
# or stale_task_timeout_minutes is tuned too aggressively). The brain
# daemon writes one pipeline_events row per cancelled task; on each
# scrape we re-read the cumulative event count into this Gauge so the
# value is persistent across worker restarts (a raw prometheus Counter
# would reset to 0 every time the worker process cycles).
AUTO_CANCELLED_TOTAL = Gauge(
    "poindexter_pipeline_auto_cancelled_total",
    "Cumulative count of tasks auto-cancelled by the stale-task sweeper "
    "(read from pipeline_events where event_type='task.auto_cancelled')",
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
# Refresh — reads the DB + Ollama, updates the Gauges
# ---------------------------------------------------------------------------


async def refresh_metrics(pool: Any, ollama_url: str) -> None:
    """Update every Gauge by running fresh queries.

    Called by the ``/metrics`` handler before generating exposition
    output. Prometheus scrapes every 15-30s typically, so the extra DB
    roundtrips are fine.

    Each source is wrapped in its own try/except — one slow query or
    one missing table must not make ``/metrics`` fail, or Prometheus
    will alert on the endpoint being down.
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
    # pipeline_events so the value survives worker restarts (a raw
    # Counter would reset to 0 every deploy, making rate() useless on
    # short windows).
    try:
        async with pool.acquire() as conn:
            cancelled_n = await conn.fetchval(
                "SELECT COUNT(*) FROM pipeline_events "
                "WHERE event_type = 'task.auto_cancelled'"
            )
        AUTO_CANCELLED_TOTAL.set(int(cancelled_n or 0))
    except Exception as e:
        logger.debug("refresh_metrics: auto_cancelled count query failed: %s", e)

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


def render_exposition() -> tuple[bytes, str]:
    """Return ``(body, content_type)`` for the ``/metrics`` HTTP response."""
    return generate_latest(), CONTENT_TYPE_LATEST
