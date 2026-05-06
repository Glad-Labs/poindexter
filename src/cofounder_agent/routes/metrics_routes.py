"""
Metrics and Analytics Routes
Provides endpoints for budget tracking and operational metrics.

All endpoints require JWT authentication.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from middleware.api_token_auth import verify_api_token
from services.cost_aggregation_service import CostAggregationService
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)
# Create metrics router
metrics_router = APIRouter(prefix="/api/metrics", tags=["metrics"])

_start_time = datetime.now(timezone.utc)


@metrics_router.get("/costs/budget")
async def get_budget_status(
    token: str = Depends(verify_api_token),
    monthly_budget: float = Query(150.0, ge=10, le=10000),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """
    Get budget status and alerts for Week 2 dashboard

    **Parameters:**
    - monthly_budget: Monthly limit in USD (default $150 for solopreneurs)

    **Returns:**
    - Amount spent vs remaining
    - Percent used with color coding
    - Daily burn rate
    - Projected final month cost
    - Alerts (80%, 100% thresholds)

    **Example:** Shows spent $12.50, remaining $137.50, 8.33% used, status: healthy
    """
    try:
        cost_service = CostAggregationService(db_service=db_service)

        result = await cost_service.get_budget_status(monthly_budget=monthly_budget)

        return result
    except Exception as e:
        logger.error("Error getting budget status: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred") from e


@metrics_router.get(
    "/operational",
    summary="Operational gauges — pipeline_tasks queue depth, scheduler health, WebSocket connections",
    description=(
        "Returns live operational metrics for the active pipeline. "
        "Reads from ``pipeline_tasks`` (the current queue) and the running "
        "``PluginScheduler`` (the active background-job runner). The legacy "
        "``content_tasks`` + ``TaskExecutor`` surfaces these used to read "
        "(and which were stuck on stale numbers) were retired in poindexter#395. "
        "Designed for external monitoring scraping (Datadog, Loki, etc.). "
        "Requires authentication for consistency with other metrics endpoints."
    ),
    include_in_schema=True,
)
async def get_operational_metrics(
    request: Request,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """
    Return structured operational gauges for alerting and dashboards.

    Emits a structured INFO log alongside the JSON response so that
    log-based metric pipelines (Loki, etc.) can create dashboards
    without a Prometheus scraper.

    Metrics included:
    - task_queue: pending/in_progress (open) + failed/completed in the
      last ``operational_metrics_window_hours`` (default 24h) read from
      ``pipeline_tasks``
    - scheduler: PluginScheduler health (is_running, last_tick_epoch,
      jobs_run/succeeded/failed)
    - executor: same fields as ``scheduler`` repeated under the legacy
      key for backwards compat with the Grafana panels and
      ``poindexter costs operational`` CLI; will be dropped in a future
      release once consumers migrate to ``scheduler``
    - websocket_connections: total active WebSocket connections
    - uptime_seconds: process uptime
    - timestamp: ISO 8601 UTC
    """
    try:
        now = datetime.now(timezone.utc)
        uptime = (now - _start_time).total_seconds()

        # --- Recent-window for failed/completed counts. Pending/in_progress
        # are queue depth — those are "all open" and don't take a window.
        window_hours = 24
        try:
            site_config = getattr(request.app.state, "site_config", None)
            if site_config is not None:
                window_hours = int(site_config.get("operational_metrics_window_hours", "24") or 24)
        except Exception as cfg_err:
            logger.debug("[operational_metrics] Window config unavailable: %s", cfg_err)
        if window_hours <= 0:
            window_hours = 24

        # --- Task queue depth from pipeline_tasks (the LIVE table). ---
        # Pending/in_progress: all-time open. Failed/completed: bounded to
        # the recent window so the gauge reflects recent activity, not
        # months of accumulated history.
        pending = 0
        in_progress = 0
        failed = 0
        completed = 0
        try:
            pool = getattr(db_service, "pool", None)
            if pool is not None:
                async with pool.acquire() as conn:
                    open_rows = await conn.fetch(
                        """
                        SELECT status, COUNT(*) AS n
                          FROM pipeline_tasks
                         WHERE status IN ('pending', 'in_progress')
                         GROUP BY status
                        """
                    )
                    recent_rows = await conn.fetch(
                        """
                        SELECT status, COUNT(*) AS n
                          FROM pipeline_tasks
                         WHERE status IN ('failed', 'completed')
                           AND COALESCE(completed_at, updated_at, created_at)
                               >= NOW() - ($1 || ' hours')::interval
                         GROUP BY status
                        """,
                        str(window_hours),
                    )
                for row in open_rows:
                    if row["status"] == "pending":
                        pending = int(row["n"])
                    elif row["status"] == "in_progress":
                        in_progress = int(row["n"])
                for row in recent_rows:
                    if row["status"] == "failed":
                        failed = int(row["n"])
                    elif row["status"] == "completed":
                        completed = int(row["n"])
        except Exception as db_err:
            logger.warning(
                "[operational_metrics] pipeline_tasks count unavailable: %s",
                db_err,
                exc_info=True,
            )

        # --- Scheduler health (replaces the dead TaskExecutor probe). ---
        scheduler_stats: dict[str, Any] = {
            "is_running": False,
            "registered_job_count": 0,
            "jobs_run": 0,
            "jobs_succeeded": 0,
            "jobs_failed": 0,
            "last_tick_epoch": None,
            "next_run_epoch": None,
        }
        try:
            plugin_scheduler = getattr(request.app.state, "plugin_scheduler", None)
            if plugin_scheduler is not None and hasattr(plugin_scheduler, "get_stats"):
                scheduler_stats = plugin_scheduler.get_stats()
        except Exception as ex_err:
            logger.debug("[operational_metrics] Scheduler stats unavailable: %s", ex_err)

        # --- WebSocket connections ---
        ws_total = 0
        try:
            from services.websocket_manager import websocket_manager

            ws_total = sum(len(conns) for conns in websocket_manager.active_connections.values())
        except Exception as ws_err:
            logger.debug("[operational_metrics] WebSocket count unavailable: %s", ws_err)

        metrics: dict[str, Any] = {
            "timestamp": now.isoformat(),
            "uptime_seconds": round(uptime, 1),
            "task_queue": {
                "pending": pending,
                "in_progress": in_progress,
                "failed": failed,
                "completed": completed,
                "window_hours": window_hours,
                "source": "pipeline_tasks",
            },
            # Live scheduler health — first-class block.
            "scheduler": scheduler_stats,
            # Backwards-compat shim for the Grafana panel + ``poindexter
            # costs operational`` CLI. Maps the new scheduler fields onto
            # the field names the legacy TaskExecutor exposed so existing
            # dashboards keep resolving without a coordinated cutover.
            # New consumers should read ``scheduler`` instead.
            "executor": {
                "is_running": bool(scheduler_stats.get("is_running", False)),
                "task_count": int(scheduler_stats.get("jobs_run", 0)),
                "success_count": int(scheduler_stats.get("jobs_succeeded", 0)),
                "error_count": int(scheduler_stats.get("jobs_failed", 0)),
                "_deprecated_legacy": (
                    "Mirrors fields from ``scheduler`` for backwards "
                    "compatibility with pre-#395 consumers. Read "
                    "``scheduler`` for canonical values."
                ),
            },
            "websocket_connections": ws_total,
        }

        # Emit a structured log line so log-based alerting can detect queue build-up.
        # Log at WARNING if queue is deep to make it filter-friendly.
        queue_depth = pending + in_progress
        log_msg = (
            "[operational_metrics] queue_depth=%d pending=%d in_progress=%d "
            "failed_%dh=%d completed_%dh=%d scheduler_running=%s "
            "scheduler_failed=%d ws_connections=%d"
        )
        log_args = (
            queue_depth,
            pending,
            in_progress,
            window_hours,
            failed,
            window_hours,
            completed,
            scheduler_stats.get("is_running", False),
            int(scheduler_stats.get("jobs_failed", 0)),
            ws_total,
        )
        if queue_depth > 50:
            logger.warning(log_msg, *log_args)
        else:
            logger.info(log_msg, *log_args)

        return metrics

    except Exception as e:
        logger.error("[get_operational_metrics] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve operational metrics") from e
