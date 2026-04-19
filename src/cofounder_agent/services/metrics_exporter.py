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
- ``poindexter_ollama_reachable`` — gauge, 1 if ``/api/tags`` returns 200

Content pipeline:
- ``poindexter_tasks_total`` — counter by ``status``
- ``poindexter_embeddings_total`` — gauge by ``source_table``
- ``poindexter_posts_total`` — gauge by ``status``

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

OLLAMA_REACHABLE = Gauge(
    "poindexter_ollama_reachable",
    "1 if Ollama /api/tags returned 200 in the last refresh",
)

EMBEDDINGS_TOTAL = Gauge(
    "poindexter_embeddings_total",
    "Total embeddings rows, labeled by source_table",
    ["source_table"],
)

POSTS_TOTAL = Gauge(
    "poindexter_posts_total",
    "Total posts, labeled by status",
    ["status"],
)

TASKS_CREATED = Counter(
    "poindexter_tasks_created_total",
    "Tasks created (lifetime counter — scrape-only, resets on restart)",
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
    WORKER_UP.set(1)

    # Postgres roundtrip.
    try:
        async with pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1")
        POSTGRES_CONNECTED.set(1 if val == 1 else 0)
    except Exception as e:
        logger.debug("refresh_metrics: postgres check failed: %s", e)
        POSTGRES_CONNECTED.set(0)

    # Ollama reachability (only runs if OLLAMA_URL looks valid).
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            resp = await http.get(f"{ollama_url.rstrip('/')}/api/tags")
        OLLAMA_REACHABLE.set(1 if resp.status_code == 200 else 0)
    except Exception:
        OLLAMA_REACHABLE.set(0)

    # Embeddings by source_table.
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT source_table, COUNT(*) AS n FROM embeddings GROUP BY source_table"
            )
        for r in rows:
            EMBEDDINGS_TOTAL.labels(source_table=r["source_table"] or "unknown").set(r["n"])
    except Exception as e:
        logger.debug("refresh_metrics: embeddings query failed: %s", e)

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
