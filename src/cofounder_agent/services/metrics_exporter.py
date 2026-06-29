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
- ``poindexter_brain_cycle_heartbeat_timestamp_seconds`` — gauge, Unix
  epoch of the most recent ``brain.cycle_heartbeat`` audit_log row
  (poindexter#524). Deliberately CLEARED (absent from exposition) on
  no-row / DB-error so the static ``BrainDeliveryDeadMansSwitch`` alert
  can fire via ``absent()`` — the one metric whose absence is alertable.

Content pipeline:
- ``poindexter_embeddings_total`` — gauge by ``source_table``
- ``poindexter_embeddings_missing_posts`` — gauge, published posts
  without a corresponding ``embeddings`` row (Gitea #238 — catches
  "new posts stopped getting embedded" even if overall rate stays up)
- ``poindexter_posts_total`` — gauge by ``status``
- ``poindexter_posts_published`` — gauge, posts with
  ``status = 'published'`` (poindexter#576 — the unambiguous
  published-post count for the Cost & Analytics dashboard; mirrors
  ``poindexter_posts_total{status="published"}`` but as a single
  label-free series so a panel can't accidentally select the wrong
  ``status`` label — the dashboard was reading ``archived`` (23)
  instead of ``published`` (91+))
- ``poindexter_approval_queue_length`` — gauge, rows in
  ``content_tasks`` with ``status = 'awaiting_approval'`` (Gitea #238
  — used as the ``unless`` cross-check against cost alerts so they
  don't fire while the pipeline is throttling on pending approvals)
- ``poindexter_unapplied_migrations_count`` — gauge, count of ``.py``
  files in ``services/migrations/`` not yet recorded in
  ``schema_migrations`` (GH-227 — catches the "container updated but
  worker not restarted, running on stale schema" failure mode that the
  ``/api/health`` JSON probe already surfaces but Prometheus didn't see)
- ``poindexter_qa_rail_skip_ratio`` — gauge by ``reviewer``, fraction of
  the last N QA passes a rail was skipped (poindexter#553). 1.0 = the rail
  is skipping every pass (empty research_context / disabled master flag /
  unresolvable judge). Drives the ``QaRailFullySkipped`` alert; only
  currently-skipping rails emit a series, so a healthy rail is absent.

Cost:
- ``poindexter_daily_spend_usd`` — gauge
- ``poindexter_monthly_spend_usd`` — gauge

Self-monitoring:
- ``poindexter_metrics_refresh_errors_total`` — counter by ``phase`` (audit
  H2b). Each ``refresh_metrics`` block has its own try/except so one failing
  query can't fail the whole scrape, but a failing phase leaves its gauge
  frozen at the last value (stale). This counts per-phase failures so a
  persistently stale gauge is visible (``PoindexterMetricsRefreshErrors``)
  instead of dying at DEBUG.

Scheduler:
- ``poindexter_scheduler_job_last_run_age_seconds`` — gauge by ``job_name``,
  seconds since each scheduled plugin job last fired (from ``job_run_state``;
  relocated out of ``app_settings``). Absent for never-run jobs.
- ``poindexter_scheduler_job_last_run_ok`` — gauge by ``job_name``, 1/0 from
  the job's most recent ``last_status``. Absent for never-run jobs.

All metric values come from the same DB queries the brain probes
already run, so the values stay consistent between the legacy probe
path and the new Prometheus path during the migration.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Imported for its side effect: ``social_drafts`` builds its draft Counter
# singletons at module import, so pulling them in here registers the series on
# the default prometheus REGISTRY as soon as ``/metrics`` is first served — not
# only after the first Postiz draft. (The legacy ``social_poster`` adapter
# counters were removed 2026-06-29 with the direct social_adapters path.)
from services.social_drafts import (  # noqa: F401 — imported for metric registration
    SOCIAL_DRAFT_CREATED_TOTAL,
    SOCIAL_DRAFT_FAILED_TOTAL,
    SOCIAL_DRAFT_POSTED_TOTAL,
)

# Imported for its side effect: template_runner registers NODE_DURATION_SECONDS
# (poindexter#652) at module import. Without this, the histogram only lands on
# the default prometheus registry after the first pipeline run — leaving the
# Pipeline dashboard panels "No Data" on a fresh worker restart.
from services.template_runner import (  # noqa: F401 — imported for metric registration
    NODE_DURATION_SECONDS as _PIPELINE_NODE_DURATION_SECONDS,
)

logger = logging.getLogger(__name__)


# Lifespan-bound shared httpx.AsyncClient — main.py wires this via
# set_http_client() at startup. Fallback to a per-call client below
# preserves /metrics scrapes that fire before the lifespan completes.
http_client: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient | None) -> None:
    """Wire the lifespan-bound shared httpx.AsyncClient."""
    global http_client
    http_client = client


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

# Delivery-plane dead-man's switch heartbeat (Glad-Labs/poindexter#524).
# Unix epoch (seconds) of the most recent ``brain.cycle_heartbeat`` row in
# ``audit_log``. The brain daemon writes one such row at the end of every
# cycle (CYCLE_SECONDS=300; see brain/brain_daemon.py). The static
# Prometheus rule ``BrainDeliveryDeadMansSwitch`` fires on BOTH:
#   - ``absent(...)`` for 10m   (gauge never emitted → DB unreachable or
#     no heartbeat row ever written), and
#   - ``time() - <gauge> > 900`` (heartbeat is stale by >15 min).
#
# This is the ONE metric whose ABSENCE must be alertable, so it is given a
# single constant label and ``.clear()``-ed on every no-row / DB-error
# refresh — that drops the series from the exposition entirely so
# ``absent()`` can fire. A plain unlabeled Gauge would always emit (last
# value or 0), which would defeat ``absent()`` and freeze the staleness
# check at the last-good timestamp forever. Matching the exporter's
# error posture: on failure we surface NOTHING rather than a stale/0 value.
BRAIN_CYCLE_HEARTBEAT_TIMESTAMP = Gauge(
    "poindexter_brain_cycle_heartbeat_timestamp_seconds",
    "Unix epoch of the most recent brain.cycle_heartbeat audit_log row. "
    "Absent when the brain has never written one or the DB is unreachable.",
    ["source"],
)

OLLAMA_REACHABLE = Gauge(
    "poindexter_ollama_reachable",
    "1 if Ollama /api/tags returned 200 in the last refresh",
)

OLLAMA_MODEL_COUNT = Gauge(
    "poindexter_ollama_model_count",
    "Number of models returned by Ollama /api/tags (0 when Ollama is up but empty)",
)

# Cloudflare page-views beacon reachability. Unlike OLLAMA_REACHABLE (probed
# inline here on every scrape because Ollama is local), the beacon is an
# external-internet Worker, so it is probed out-of-band by
# ``services.jobs.probe_cloudflare_beacon.ProbeCloudflareBeaconJob`` every 5
# min — that job sets THIS gauge (it runs in the same worker process, so the
# value is exposed on the next scrape). The static rule
# ``PoindexterCloudflareBeaconDown`` alerts on ``== 0``. Initialised to 1 so
# the first ~5 min before the job's first run reads healthy rather than
# "no data", and so an install that never configures a beacon never alerts
# (the job also re-asserts 1 when ``cloudflare_beacon_url`` is unset).
CLOUDFLARE_BEACON_REACHABLE = Gauge(
    "poindexter_cloudflare_beacon_reachable",
    "1 if the Cloudflare page-views beacon Worker returned 2xx to the last "
    "out-of-band probe (every 5m); 0 if unreachable. Page-view analytics "
    "ingestion stalls while this is 0.",
)
CLOUDFLARE_BEACON_REACHABLE.set(1)

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

# poindexter#576: an unlabeled, unambiguous published-post count. The
# labeled POSTS_TOTAL above emits one series per status string
# (published / draft / rejected / archived), and the Cost & Analytics
# dashboard's "posts published" stat was selecting the wrong label —
# it read poindexter_posts_total{status="archived"} (23) instead of
# {status="published"} (91+). This single label-free series can't be
# mis-selected and is the source of truth a dashboard/alert should use.
# Definitionally equals SELECT COUNT(*) FROM posts WHERE status='published'
# (the same predicate get_post_count and the brain health probe use).
POSTS_PUBLISHED = Gauge(
    "poindexter_posts_published",
    "Posts with status='published' (the live count rendered on gladlabs.io)",
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

# poindexter#553 — per-QA-rail skip ratio over the last N passes. A value of
# 1.0 means the rail was skipped in EVERY one of the last N QA passes (a rail
# that's structurally not contributing — empty research_context for grounding
# rails, a disabled master flag, or an unresolvable judge model). Labeled by
# reviewer; only rails that actually skipped in the window emit a series, so a
# healthy rail is absent (no false alert). The QaRailFullySkipped Prometheus
# rule fires on ``>= 1``. Both event types are emitted on the graph_def QA
# path: qa.aggregate writes qa_pass_completed (the denominator), the rail
# methods write qa_reviewer_skipped (the numerator).
QA_RAIL_SKIP_RATIO = Gauge(
    "poindexter_qa_rail_skip_ratio",
    "Fraction of the last N QA passes in which this rail was skipped "
    "(1.0 = skipped every recent pass). Labeled by reviewer.",
    ["reviewer"],
)

# Default number of recent QA passes to measure the skip ratio over. Operators
# tune via app_settings.qa_rail_skip_window_passes (sensible-default tunable
# per the config-in-DB principle; '' sentinel / absent row → this default).
_QA_RAIL_SKIP_WINDOW_DEFAULT = 20

# Per-rail skip ratio = (skips of that rail since the Nth-newest pass) / N.
# recent_passes pins the window to the last N qa_pass_completed rows so the
# ratio is "last N passes" (count-based), not a wall-clock window. audit_log's
# time column is the quoted "timestamp" (NOT created_at).
_QA_RAIL_SKIP_SQL = """
WITH recent_passes AS (
    SELECT "timestamp" AS ts
    FROM audit_log
    WHERE event_type = 'qa_pass_completed'
    ORDER BY "timestamp" DESC
    LIMIT $1
),
win AS (
    SELECT MIN(ts) AS start_ts, COUNT(*) AS n FROM recent_passes
)
SELECT s.details->>'reviewer' AS reviewer,
       COUNT(*)::float        AS skips,
       (SELECT n FROM win)    AS passes
FROM audit_log s, win
WHERE s.event_type = 'qa_reviewer_skipped'
  AND (SELECT n FROM win) > 0
  AND s."timestamp" >= win.start_ts
  -- Exclude intentional skips so a rail that is deliberately off
  -- (master_flag_off) or structurally non-applicable for this pass
  -- (conditional_skip — e.g. no research_sources) does not read as a
  -- silently-broken rail and drive QaRailFullySkipped (#1181). Filters on
  -- the structured ``skip_type`` field emitted by
  -- modules.content.multi_model_qa._surface_reviewer_skip — the literals
  -- here MUST match SKIP_TYPES_EXCLUDED_FROM_RATIO there (drift-guarded by
  -- test_metrics_exporter). COALESCE keeps legacy rows (no skip_type) in
  -- the count; genuine breakage (misconfig, the default) still counts so
  -- the alert can fire. The legacy prose LIKE remains as a transitional
  -- fallback for pre-taxonomy master-flag-off events still inside the
  -- trailing-N window — it ages out as those events leave the window.
  AND COALESCE(s.details->>'skip_type', '')
      NOT IN ('master_flag_off', 'conditional_skip')
  AND s.details->>'reason' NOT LIKE '%master rail flag off%'
GROUP BY s.details->>'reviewer'
"""


# Per-scheduler-job freshness — relocated out of app_settings into the
# job_run_state table (see plugins/scheduler.py). Cleared + repopulated each
# scrape so a removed job's series drops out; never-run jobs (last_run_at IS
# NULL) emit no series. Bounded cardinality: ~one pair per registered job.
SCHEDULER_JOB_LAST_RUN_AGE_SECONDS = Gauge(
    "poindexter_scheduler_job_last_run_age_seconds",
    "Seconds since each scheduled plugin job last fired (from job_run_state). "
    "Absent for never-run jobs.",
    ["job_name"],
)
SCHEDULER_JOB_LAST_RUN_OK = Gauge(
    "poindexter_scheduler_job_last_run_ok",
    "1 if the job's most recent fire returned ok, 0 if it errored "
    "(from job_run_state.last_status). Absent for never-run jobs.",
    ["job_name"],
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
# HTTP RED metrics (Rate / Errors / Duration) — the worker is a FastAPI app
# but had NO request-level observability (request rate, 5xx rate, latency per
# route). These are recorded PER-REQUEST by middleware.prometheus_metrics
# (an ASGI middleware), NOT in refresh_metrics() — they are event-driven, not
# scrape-time snapshots. The middleware updates them on every request; the
# /metrics handler just serializes the accumulated values.
#
# ## Cardinality discipline (the one thing that matters here)
#
# The ``route`` label is the matched ROUTE TEMPLATE (e.g. ``/api/posts/{slug}``)
# — never the concrete path (``/api/posts/my-actual-post``). Labeling by raw
# path would mint one time series per slug / task-id / page, which is the
# classic Prometheus cardinality bomb: unbounded series, ballooning memory,
# slow queries. Requests that never matched a route (404s, bot path-scans)
# collapse to a single ``unmatched`` series so a fuzzer can't create series at
# will. ``http_route_label()`` derives the template from the ASGI scope. With
# those bounds, total series ≈ routes × methods × status-codes-seen — a few
# hundred at most for this app.
HTTP_REQUESTS_TOTAL = Counter(
    "poindexter_http_requests_total",
    "Total HTTP requests handled by the worker, by method, route template and status",
    ["method", "route", "status"],
)

# Buckets tuned for a local FastAPI app: most JSON routes answer in <100ms;
# LLM-backed / pipeline routes can run into seconds. 30s is the failure tail
# (matches typical client / reverse-proxy timeouts).
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "poindexter_http_request_duration_seconds",
    "HTTP request latency in seconds, by method and route template",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)


def http_route_label(scope: Mapping[str, Any]) -> str:
    """Return the matched route *template* for an ASGI ``http`` scope.

    Starlette (0.49) populates ``scope['endpoint']`` and
    ``scope['path_params']`` on a successful match but does NOT set
    ``scope['route']`` — so we cannot read a ``.path`` template directly.
    Instead we reconstruct the template by replacing each path segment whose
    value equals a captured path-param with ``{param_name}``:

        /api/posts/my-slug  +  path_params={'slug': 'my-slug'}
          -> /api/posts/{slug}

    This keeps the label's cardinality bounded by the number of routes rather
    than the number of distinct URLs. Whole-segment matching (not substring)
    avoids corrupting static segments that happen to contain a param value.

    Returns ``"unmatched"`` when no route matched (no ``endpoint`` in scope) —
    collapsing 404s / bot path-scans into one series instead of one-per-URL.
    """
    if scope.get("endpoint") is None:
        # No route matched (404, or a raw-ASGI path) — do not let arbitrary
        # paths each become their own series.
        return "unmatched"

    path = scope.get("path") or "/"
    path_params = scope.get("path_params") or {}
    if not path_params:
        # Static route (e.g. /api/health) — the path IS already the template.
        return path

    # Map each captured value -> its param name, then swap matching segments.
    value_to_name = {
        str(v): name for name, v in path_params.items() if v is not None
    }
    segments = [
        "{" + value_to_name[seg] + "}" if seg in value_to_name else seg
        for seg in path.split("/")
    ]
    return "/".join(segments)


# ---------------------------------------------------------------------------
# Refresh — reads the DB + Ollama, updates the Gauges
# ---------------------------------------------------------------------------


# Per-phase refresh-failure counter (audit H2b). Every block in
# refresh_metrics has its own try/except so one failing query can't fail the
# whole /metrics scrape — but those excepts logged only at DEBUG, making a
# partial failure invisible. When a phase throws, its gauge(s) are NOT updated
# and hold their last value (frozen/stale), so a panel or alert reading them is
# silently looking at old data — e.g. an informational gauge that quietly stops
# moving. This counter makes each such failure countable + alertable per phase
# (PoindexterMetricsRefreshErrors). Named without the ``_total`` suffix per
# prometheus_client convention; the exposed series is
# ``poindexter_metrics_refresh_errors_total``.
METRICS_REFRESH_ERRORS = Counter(
    "poindexter_metrics_refresh_errors",
    "refresh_metrics per-phase failures. A rising count for a phase means its "
    "gauge(s) are stale — the query threw and the value is frozen at its last "
    "good reading.",
    ["phase"],
)


def _note_refresh_error(phase: str, exc: BaseException) -> None:
    """Count + debug-log a per-phase refresh failure (audit H2b).

    Replaces the bare ``logger.debug("refresh_metrics: <phase> failed", e)``
    swallows so a stale gauge no longer hides its own cause: keeps the debug
    log (full context for anyone already tailing) and adds the per-phase
    counter increment (the operator-visible, alertable signal).
    """
    METRICS_REFRESH_ERRORS.labels(phase=phase).inc()
    logger.debug("refresh_metrics: %s failed: %s", phase, exc)


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
        _note_refresh_error("postgres", e)
        POSTGRES_CONNECTED.set(0)

    # #524: brain cycle heartbeat freshness — the delivery-plane dead-man's
    # switch. Read the epoch of the most recent ``brain.cycle_heartbeat``
    # audit_log row and publish it as a gauge. If there's no row or the
    # query fails, CLEAR the series so it is absent from the exposition and
    # ``absent(poindexter_brain_cycle_heartbeat_timestamp_seconds)`` can
    # fire — we deliberately do NOT emit a stale/zero value here (that would
    # freeze the ``time() - gauge`` staleness check and mask a dead brain).
    try:
        async with pool.acquire() as conn:
            epoch = await conn.fetchval(
                # audit_log's timestamp column is "timestamp" (quoted — it's
                # also a type name); there is NO created_at column here.
                'SELECT EXTRACT(EPOCH FROM MAX("timestamp")) '
                "FROM audit_log WHERE event_type = 'brain.cycle_heartbeat'"
            )
        if epoch is None:
            # No heartbeat row yet — let absent() fire rather than emit 0.
            BRAIN_CYCLE_HEARTBEAT_TIMESTAMP.clear()
        else:
            BRAIN_CYCLE_HEARTBEAT_TIMESTAMP.labels(source="audit_log").set(
                float(epoch)
            )
    except Exception as e:
        _note_refresh_error("brain_heartbeat", e)
        # DB error → drop the series so the dead-man's switch can fire.
        BRAIN_CYCLE_HEARTBEAT_TIMESTAMP.clear()

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
        _note_refresh_error("pg_connections", e)

    # Ollama reachability + model count (Gitea #238 — "up but no models"
    # passed the old gauge; OLLAMA_MODEL_COUNT catches that case).
    try:
        if http_client is not None:
            resp = await http_client.get(
                f"{ollama_url.rstrip('/')}/api/tags", timeout=3.0,
            )
        else:
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
        _note_refresh_error("embeddings", e)

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
        _note_refresh_error("embeddings_gap", e)

    # Posts by status. ``.clear()`` first so a status that drops to zero
    # rows (or a renamed status string) doesn't leave a stale child series
    # frozen at its last value — labeled Gauges are never auto-reset
    # between refreshes (poindexter#576).
    #
    # Also set the dedicated ``poindexter_posts_published`` gauge from the
    # observed ``published`` count so the Cost & Analytics dashboard has an
    # unambiguous, label-free series to read (the published-status stat was
    # mis-reading the ``archived`` label off ``poindexter_posts_total``).
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*) AS n FROM posts GROUP BY status"
            )
        POSTS_TOTAL.clear()
        published_n = 0
        for r in rows:
            status = r["status"] or "unknown"
            POSTS_TOTAL.labels(status=status).set(r["n"])
            if status == "published":
                published_n = r["n"]
        POSTS_PUBLISHED.set(int(published_n))
    except Exception as e:
        _note_refresh_error("posts", e)

    # Approval queue depth — used as the ``unless`` cross-check against
    # cost alerts so they don't fire while the pipeline is throttling.
    try:
        async with pool.acquire() as conn:
            queue_n = await conn.fetchval(
                "SELECT COUNT(*) FROM content_tasks WHERE status = 'awaiting_approval'"
            )
        APPROVAL_QUEUE_LENGTH.set(int(queue_n or 0))
    except Exception as e:
        _note_refresh_error("approval_queue", e)

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
        _note_refresh_error("auto_cancelled", e)

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
        _note_refresh_error("unapplied_migrations", e)

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
        _note_refresh_error("cost_logs", e)

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
        _note_refresh_error("db_pool", e)

    # GPU scheduler snapshot.
    try:
        from services.gpu_scheduler import gpu  # local import — avoid boot cycles

        s = gpu.status
        GPU_GAMING_DETECTED.set(1 if s.get("gaming_detected") else 0)
        GPU_GAMING_PAUSED_SECONDS.set(float(s.get("gaming_paused_s") or 0))
        GPU_GAMING_PAUSED_TOTAL_SECONDS.set(float(s.get("total_gaming_paused_s") or 0))
        GPU_BUSY.set(1 if s.get("busy") else 0)
    except Exception as e:
        _note_refresh_error("gpu_scheduler", e)

    # Pipeline throttle state (GH-89 AC#2).
    try:
        from services.pipeline_throttle import get_state as _throttle_state

        ts = _throttle_state()
        PIPELINE_THROTTLE_ACTIVE.set(1 if ts.get("active") else 0)
        PIPELINE_THROTTLE_SECONDS_TOTAL.set(float(ts.get("total_seconds") or 0))
        PIPELINE_THROTTLE_QUEUE_SIZE.set(int(ts.get("queue_size") or 0))
        PIPELINE_THROTTLE_QUEUE_LIMIT.set(int(ts.get("queue_limit") or 0))
    except Exception as e:
        _note_refresh_error("pipeline_throttle", e)

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
        _note_refresh_error("task_counts", e)

    # Module v1 metric contributions (Glad-Labs/poindexter#490 lifecycle).
    # Each business module MAY expose an optional ``refresh_module_metrics``
    # hook that updates its own prometheus_client singletons from the DB at
    # scrape time — the same lifecycle shape as register_routes /
    # register_probes. This loop is deliberately generic: the substrate
    # exporter knows nothing about any specific module, so a private module's
    # metric surface stays entirely inside its own package. A module without
    # the hook is skipped; a hook that raises is logged and never fails
    # /metrics.
    await _refresh_module_metrics(pool)

    # poindexter#553: per-rail skip ratio over the last N QA passes. Self-
    # contained (own connection + own try/except) so it can't disturb the
    # blocks above. Powers the QaRailFullySkipped alert.
    await refresh_qa_rail_skip_ratio(pool)

    # Per-job scheduler freshness from job_run_state (relocated from
    # app_settings). Self-contained; powers the System Health scheduler panels.
    await refresh_scheduler_job_state(pool)


async def _refresh_module_metrics(pool: Any) -> None:
    """Invoke each registered Module's optional ``refresh_module_metrics``.

    Kept separate from :func:`refresh_metrics` so it's independently
    testable and so the module-discovery import stays lazy (modules pull in
    heavier closures we don't want loaded on every substrate scrape path).
    The hook may be sync-returning-awaitable or plain async; we await
    whatever is awaitable.
    """
    import inspect

    try:
        from plugins.registry import get_modules
    except Exception as e:  # noqa: BLE001 — registry import must never break /metrics
        _note_refresh_error("module_registry", e)
        return

    try:
        modules = get_modules()
    except Exception as e:  # noqa: BLE001
        _note_refresh_error("get_modules", e)
        return

    for mod in modules:
        hook = getattr(mod, "refresh_module_metrics", None)
        if not callable(hook):
            continue
        try:
            result = hook(pool)
            if inspect.isawaitable(result):
                await result
        except Exception as e:  # noqa: BLE001 — one module must not break /metrics
            mod_name = type(mod).__name__
            logger.warning(
                "refresh_metrics: %s.refresh_module_metrics failed: %s",
                mod_name, e,
            )


async def refresh_qa_rail_skip_ratio(
    pool: Any,
    *,
    window_passes: int | None = None,
) -> None:
    """Recompute the per-rail skip ratio gauge from ``audit_log``.

    For each QA rail that was skipped at least once in the last
    ``window_passes`` QA passes, set ``poindexter_qa_rail_skip_ratio`` to
    ``skips / passes`` (capped at 1.0). The gauge is CLEARED first, so a
    rail that has recovered loses its series and the alert resolves; a
    healthy rail never gets a series (no skip rows → no alert).

    Skips with ``reason LIKE '%master rail flag off%'`` are excluded from
    the count. These are intentional operator disables (``ragas_enabled=false``
    etc.) — an alert for a rail the operator deliberately turned off is noise,
    not signal. The alert should only fire for rails that are enabled but
    failing to run (empty research_context, unresolvable judge model, etc.).

    ``window_passes`` defaults to ``app_settings.qa_rail_skip_window_passes``
    (then ``_QA_RAIL_SKIP_WINDOW_DEFAULT``). Best-effort: any failure leaves
    the gauge cleared rather than raising — ``/metrics`` must never 500.
    """
    QA_RAIL_SKIP_RATIO.clear()
    try:
        async with pool.acquire() as conn:
            n = window_passes
            if n is None:
                try:
                    raw = await conn.fetchval(
                        "SELECT value FROM app_settings "
                        "WHERE key = 'qa_rail_skip_window_passes'"
                    )
                    # app_settings.value is NOT NULL; '' is the unset
                    # sentinel — guard int() against it.
                    n = int(raw) if raw else _QA_RAIL_SKIP_WINDOW_DEFAULT
                except (TypeError, ValueError):
                    n = _QA_RAIL_SKIP_WINDOW_DEFAULT
            if n < 1:
                n = _QA_RAIL_SKIP_WINDOW_DEFAULT
            rows = await conn.fetch(_QA_RAIL_SKIP_SQL, n)
    except Exception as e:  # noqa: BLE001
        _note_refresh_error("qa_rail_skip_ratio", e)
        return

    for r in rows:
        reviewer = r["reviewer"]
        passes = float(r["passes"] or 0)
        if not reviewer or passes <= 0:
            continue
        ratio = float(r["skips"] or 0) / passes
        QA_RAIL_SKIP_RATIO.labels(reviewer=reviewer).set(min(ratio, 1.0))


async def refresh_scheduler_job_state(pool: Any) -> None:
    """Recompute the per-job scheduler freshness gauges from ``job_run_state``.

    For each job with a non-NULL ``last_run_at`` set
    ``poindexter_scheduler_job_last_run_age_seconds`` to ``now() - last_run_at``
    and ``poindexter_scheduler_job_last_run_ok`` to 1/0 from ``last_status``.
    Both gauges are CLEARED first so a removed job's series disappears and a
    never-run job (NULL ``last_run_at``) emits nothing. Best-effort: any failure
    leaves the gauges cleared rather than raising — ``/metrics`` must never 500.
    """
    SCHEDULER_JOB_LAST_RUN_AGE_SECONDS.clear()
    SCHEDULER_JOB_LAST_RUN_OK.clear()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT job_name, "
                "EXTRACT(EPOCH FROM (now() - last_run_at)) AS age_s, "
                "last_status "
                "FROM job_run_state WHERE last_run_at IS NOT NULL"
            )
    except Exception as e:  # noqa: BLE001 — /metrics must never 500
        _note_refresh_error("job_run_state", e)
        return
    for r in rows:
        job = r["job_name"]
        if not job:
            continue
        age = r["age_s"]
        if age is not None:
            SCHEDULER_JOB_LAST_RUN_AGE_SECONDS.labels(job_name=job).set(float(age))
        status = r["last_status"]
        if status is not None:
            SCHEDULER_JOB_LAST_RUN_OK.labels(job_name=job).set(
                1 if status == "ok" else 0
            )


def render_exposition() -> tuple[bytes, str]:
    """Return ``(body, content_type)`` for the ``/metrics`` HTTP response."""
    return generate_latest(), CONTENT_TYPE_LATEST
