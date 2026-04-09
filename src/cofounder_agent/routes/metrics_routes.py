"""
Metrics and Analytics Routes
Provides endpoints for budget tracking and operational metrics.

All endpoints require JWT authentication.
"""

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from middleware.api_token_auth import verify_api_token

from services.cost_aggregation_service import CostAggregationService
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)
# Create metrics router
metrics_router = APIRouter(prefix="/api/metrics", tags=["metrics"])

_start_time = datetime.now()


@metrics_router.get("/costs/budget")
async def get_budget_status(
    token: str = Depends(verify_api_token),
    monthly_budget: float = Query(150.0, ge=10, le=10000),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> Dict[str, Any]:
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
    summary="Operational gauges — task queue depth, executor health, WebSocket connections",
    description=(
        "Returns in-memory operational metrics without requiring a database query. "
        "Designed for external monitoring scraping (Datadog, Loki, etc.). "
        "Requires authentication for consistency with other metrics endpoints."
    ),
    include_in_schema=True,
)
async def get_operational_metrics(
    request: Request,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> Dict[str, Any]:
    """
    Return structured operational gauges for alerting and dashboards.

    Emits a structured INFO log alongside the JSON response so that
    log-based metric pipelines (Loki, etc.) can create dashboards
    without a Prometheus scraper.

    Metrics included:
    - task_queue_depth: pending + in_progress task counts from DB
    - executor: task_count, success_count, error_count from TaskExecutor
    - websocket_connections: total active WebSocket connections
    - uptime_seconds: process uptime
    - timestamp: ISO 8601 UTC
    """
    try:
        now = datetime.now()
        uptime = (now - _start_time).total_seconds()

        # --- Task queue depth from DB ---
        task_counts: Dict[str, int] = {}
        try:
            if db_service and db_service.tasks:
                raw = await db_service.tasks.get_task_counts()
                # raw may be a Pydantic model or a dict
                if hasattr(raw, "model_dump"):
                    task_counts = {str(k): int(v) for k, v in raw.model_dump().items() if isinstance(v, (int, float))}
                elif isinstance(raw, dict):
                    task_counts = {str(k): int(v) for k, v in raw.items()}
                else:
                    task_counts = {}
        except Exception as db_err:
            logger.warning(
                "[operational_metrics] DB task count unavailable: %s", db_err, exc_info=True
            )

        pending = task_counts.get("pending", 0)
        in_progress = task_counts.get("in_progress", 0)
        failed = task_counts.get("failed", 0)
        completed = task_counts.get("completed", 0)

        # --- Task executor in-memory stats ---
        executor_stats: Dict[str, Any] = {}
        try:
            from services.service_container import (
                get_service_container,  # type: ignore[import-untyped]
            )

            container = get_service_container()
            if container and hasattr(container, "task_executor") and container.task_executor:
                executor_stats = container.task_executor.get_stats()
        except Exception as ex_err:
            logger.debug("[operational_metrics] Executor stats unavailable: %s", ex_err)

        # --- WebSocket connections ---
        ws_total = 0
        try:
            from services.websocket_manager import websocket_manager

            ws_total = sum(len(conns) for conns in websocket_manager.active_connections.values())
        except Exception as ws_err:
            logger.debug("[operational_metrics] WebSocket count unavailable: %s", ws_err)

        metrics: Dict[str, Any] = {
            "timestamp": now.isoformat(),
            "uptime_seconds": round(uptime, 1),
            "task_queue": {
                "pending": pending,
                "in_progress": in_progress,
                "failed": failed,
                "completed": completed,
            },
            "executor": {
                "task_count": executor_stats.get("task_count", 0),
                "success_count": executor_stats.get("success_count", 0),
                "error_count": executor_stats.get("error_count", 0),
                "is_running": executor_stats.get("is_running", False),
            },
            "websocket_connections": ws_total,
        }

        # Emit a structured log line so log-based alerting can detect queue build-up.
        # Log at WARNING if queue is deep to make it filter-friendly.
        queue_depth = pending + in_progress
        if queue_depth > 50:
            logger.warning(
                "[operational_metrics] queue_depth=%d pending=%d in_progress=%d errors=%d ws_connections=%d",
                queue_depth,
                pending,
                in_progress,
                executor_stats.get('error_count', 0),
                ws_total,
            )
        else:
            logger.info(
                "[operational_metrics] queue_depth=%d pending=%d in_progress=%d errors=%d ws_connections=%d",
                queue_depth,
                pending,
                in_progress,
                executor_stats.get('error_count', 0),
                ws_total,
            )

        return metrics

    except Exception as e:
        logger.error("[get_operational_metrics] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve operational metrics") from e
