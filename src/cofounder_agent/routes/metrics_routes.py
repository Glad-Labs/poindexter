"""
Metrics and Analytics Routes
Provides endpoints for tracking AI model usage, costs, and performance metrics

All endpoints require JWT authentication

Integrates with UsageTracker service for real-time metrics collection.
"""

from services.logger_config import get_logger
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from routes.auth_unified import get_current_user
from schemas.auth_schemas import UserProfile
from services.cost_aggregation_service import CostAggregationService
from services.database_service import DatabaseService
from services.usage_tracker import get_usage_tracker
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)
# Create metrics router
metrics_router = APIRouter(prefix="/api/metrics", tags=["metrics"])

# In-memory storage for metrics (replace with database in production)
_cost_metrics = {
    "total": 0.0,
    "models": {
        "ollama": {"tokens": 0, "cost": 0.0},
        "neural-chat": {"tokens": 5043, "cost": 0.0},
        "mistral": {"tokens": 2862, "cost": 0.0},
        "llama2": {"tokens": 2146, "cost": 0.0},
        "qwen2.5": {"tokens": 1511, "cost": 0.0},
    },
    "providers": {
        "local": 0.0,
        "openai": 0.0,
        "anthropic": 0.0,
        "google": 0.0,
    },
}

_start_time = datetime.now()
_task_stats = {
    "active": 0,
    "completed": 1,  # Blog post generation task
    "failed": 0,
}


@metrics_router.get("/usage", response_model=Dict[str, Any])
async def get_usage_metrics(
    current_user: UserProfile = Depends(get_current_user),
    period: str = Query("last_24h", description="Time period: last_1h, last_24h, last_7d, all"),
) -> Dict[str, Any]:
    """
    Get comprehensive usage metrics from UsageTracker.

    **Authentication:** Requires valid JWT token

    **Parameters:**
    - period: Time period to aggregate (last_1h, last_24h, last_7d, all)

    **Returns:**
    - Token usage (input/output breakdown)
    - Cost analysis by model and operation type
    - Success/failure rates
    - Performance metrics (duration, throughput)
    """
    try:
        tracker = get_usage_tracker()
        completed_ops = tracker.completed_operations

        if not completed_ops:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "period": period,
                "total_operations": 0,
                "tokens": {"total": 0, "input": 0, "output": 0, "avg_per_operation": 0.0},
                "costs": {
                    "total": 0.0,
                    "avg_per_operation": 0.0,
                    "by_model": {},
                    "projected_monthly": 0.0,
                },
                "operations": {"total": 0, "successful": 0, "failed": 0, "success_rate": 0.0},
                "by_model": {},
                "by_operation_type": {},
            }

        # Calculate metrics
        total_input = sum(op.input_tokens for op in completed_ops)
        total_output = sum(op.output_tokens for op in completed_ops)
        total_tokens = total_input + total_output
        total_cost = sum(op.total_cost_usd for op in completed_ops)
        total_ops = len(completed_ops)
        successful_ops = sum(1 for op in completed_ops if op.success)
        failed_ops = total_ops - successful_ops

        # Group by model
        by_model: dict[str, dict[str, Any]] = {}
        for op in completed_ops:
            model = op.model_name or "unknown"
            if model not in by_model:
                by_model[model] = {"operations": 0, "tokens": 0, "cost": 0.0}
            by_model[model]["operations"] += 1
            by_model[model]["tokens"] += op.input_tokens + op.output_tokens
            by_model[model]["cost"] += op.total_cost_usd

        # Group by operation type
        by_operation: dict[str, dict[str, Any]] = {}
        for op in completed_ops:
            op_type = op.operation_type or "unknown"
            if op_type not in by_operation:
                by_operation[op_type] = {"count": 0, "cost": 0.0, "success": 0}
            by_operation[op_type]["count"] += 1
            by_operation[op_type]["cost"] += op.total_cost_usd
            if op.success:
                by_operation[op_type]["success"] += 1

        # Projections
        first_op_time = datetime.fromisoformat(completed_ops[0].created_at)
        days_active = max(1, (datetime.now(first_op_time.tzinfo) - first_op_time).days or 1)
        projected_monthly = (total_cost / days_active * 30) if days_active > 0 else 0

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "period": period,
            "total_operations": total_ops,
            "tokens": {
                "total": int(total_tokens),
                "input": int(total_input),
                "output": int(total_output),
                "avg_per_operation": float(total_tokens / total_ops) if total_ops > 0 else 0.0,
            },
            "costs": {
                "total": round(total_cost, 4),
                "avg_per_operation": round(total_cost / total_ops, 6) if total_ops > 0 else 0.0,
                "by_model": {model: round(by_model[model]["cost"], 4) for model in by_model},
                "projected_monthly": round(projected_monthly, 2),
            },
            "operations": {
                "total": total_ops,
                "successful": successful_ops,
                "failed": failed_ops,
                "success_rate": round(
                    (successful_ops / total_ops * 100) if total_ops > 0 else 0, 2
                ),
            },
            "by_model": by_model,
            "by_operation_type": by_operation,
        }

    except Exception as e:
        logger.error(f"Error retrieving usage metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve metrics")


@metrics_router.get("/costs")
async def get_cost_metrics(
    current_user: UserProfile = Depends(get_current_user),
    use_db: bool = Query(True, description="Use PostgreSQL database for costs (recommended)"),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> Dict[str, Any]:
    """
    Get AI model usage and cost metrics from database.

    **Authentication:** Requires valid JWT token

    **Fallback:** If use_db=false, falls back to in-memory usage tracker

    **Returns:**
    - Total cost and budget status
    - Cost breakdown by model and phase
    - Cost trends and projections
    - Daily/weekly/monthly aggregations

    **New Fields (from database):**
    - costs: Advanced metrics including breakdown by phase
    - budget: Budget tracking and status
    - trend: Cost trends (up/down/stable)
    """
    try:
        # Try database-backed aggregation first
        if use_db:
            try:
                cost_service = CostAggregationService(db_service=db_service)

                # Get comprehensive cost summary from database
                summary = await cost_service.get_summary()
                phase_breakdown = await cost_service.get_breakdown_by_phase(period="month")
                model_breakdown = await cost_service.get_breakdown_by_model(period="month")
                budget_status = await cost_service.get_budget_status(monthly_budget=150.0)

                return {
                    # Summary metrics
                    "total_cost": summary.get("month_cost", 0.0),
                    "total_tokens": 0,  # Not tracked per-token in database
                    "period": "month",
                    # Advanced breakdown
                    "costs": {
                        "today": summary.get("today_cost", 0.0),
                        "week": summary.get("week_cost", 0.0),
                        "month": summary.get("month_cost", 0.0),
                        "by_phase": phase_breakdown.get("phases", []),
                        "by_model": model_breakdown.get("models", []),
                    },
                    # Budget tracking
                    "budget": {
                        "monthly_limit": budget_status.get("monthly_budget", 150.0),
                        "current_spent": budget_status.get("amount_spent", 0.0),
                        "remaining": budget_status.get("amount_remaining", 150.0),
                        "percent_used": budget_status.get("percent_used", 0.0),
                        "projected_monthly": summary.get("projected_monthly", 0.0),
                        "status": budget_status.get("status", "healthy"),
                        "alerts": budget_status.get("alerts", []),
                    },
                    # Task metrics
                    "tasks": {
                        "completed": summary.get("tasks_completed", 0),
                        "avg_cost_per_task": summary.get("avg_cost_per_task", 0.0),
                    },
                    # Response metadata
                    "updated_at": summary.get("last_updated"),
                    "source": "postgresql",
                    # Backward compatibility fields
                    "by_model": [
                        {"model": m["model"], "cost": m["total_cost"]}
                        for m in model_breakdown.get("models", [])
                    ],
                    "by_provider": {},
                }
            except Exception as db_error:
                logger.warning(f"Database costs failed, falling back to tracker: {db_error}", exc_info=True)
                use_db = False

        # Fallback to legacy usage tracker
        if not use_db:
            tracker = get_usage_tracker()
            completed_ops = tracker.completed_operations

            if not completed_ops:
                return {
                    "total_cost": 0.0,
                    "total_tokens": 0,
                    "by_model": [],
                    "by_provider": {},
                    "period": "all_time",
                    "updated_at": datetime.now().isoformat(),
                    "source": "tracker",
                }

            # Calculate totals
            total_cost = sum(op.total_cost_usd for op in completed_ops)
            total_tokens = sum(op.input_tokens + op.output_tokens for op in completed_ops)

            # Group by model
            by_model: dict[str, dict[str, Any]] = {}
            for op in completed_ops:
                model = op.model_name or "unknown"
                if model not in by_model:
                    by_model[model] = {"tokens": 0, "cost": 0.0, "provider": "unknown"}
                by_model[model]["tokens"] += op.input_tokens + op.output_tokens
                by_model[model]["cost"] += op.total_cost_usd

                # Infer provider from model name
                if "ollama" in model.lower() or model == "mistral" or model == "llama2":
                    by_model[model]["provider"] = "ollama"
                elif "gpt" in model.lower():
                    by_model[model]["provider"] = "openai"
                elif "claude" in model.lower():
                    by_model[model]["provider"] = "anthropic"

            by_model_list = [
                {
                    "model": name,
                    "tokens": metrics["tokens"],
                    "cost": round(metrics["cost"], 4),
                    "provider": metrics["provider"],
                }
                for name, metrics in by_model.items()
            ]

            # Group by provider
            by_provider = {}
            for model_data in by_model_list:
                provider = model_data["provider"]
                if provider not in by_provider:
                    by_provider[provider] = 0.0
                by_provider[provider] += model_data["cost"]

            return {
                "total_cost": round(total_cost, 4),
                "total_tokens": int(total_tokens),
                "by_model": by_model_list,
                "by_provider": {provider: round(cost, 4) for provider, cost in by_provider.items()},
                "period": "all_time",
                "updated_at": datetime.now().isoformat(),
                "source": "tracker",
            }

    except Exception as e:
        logger.error(f"Error retrieving cost metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve metrics")


@metrics_router.get("")
async def get_metrics(current_user: UserProfile = Depends(get_current_user)) -> Dict[str, Any]:
    """
    Get aggregated application metrics and health status.

    **Authentication:** Requires valid JWT token

    **Returns:**
    - System health and status
    - Active and completed operations
    - API version and service status
    """
    try:
        tracker = get_usage_tracker()
        completed_ops = tracker.completed_operations
        active_ops = len(tracker.active_operations)

        # Calculate uptime
        uptime = (datetime.now() - _start_time).total_seconds()
        failed_ops = sum(1 for op in completed_ops if not op.success)

        return {
            "status": "healthy",
            "uptime_seconds": uptime,
            "active_tasks": active_ops,
            "completed_tasks": len(completed_ops),
            "failed_tasks": failed_ops,
            "api_version": "2.0.0",
            "timestamp": datetime.now().isoformat(),
            "services": {
                "database": "healthy",
                "ollama": "healthy",
                "cache": "healthy",
                "usage_tracker": "healthy",
            },
            "latest_operations": [
                {
                    "id": op.operation_id,
                    "type": op.operation_type,
                    "model": op.model_name,
                    "success": op.success,
                    "timestamp": op.created_at,
                }
                for op in completed_ops[-5:]  # Last 5 operations
            ],
        }

    except Exception as e:
        logger.error(f"Error retrieving metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve metrics")


@metrics_router.get("/summary")
async def get_metrics_summary(
    current_user: UserProfile = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Get a summary of all metrics
    Requires: Valid JWT authentication

    Returns:
        Summary of costs, performance, and health
    """
    # Calculate uptime
    uptime = (datetime.now() - _start_time).total_seconds()
    total_cost = sum(m["cost"] for m in _cost_metrics["models"].values())
    total_tokens = sum(m["tokens"] for m in _cost_metrics["models"].values())

    return {
        "costs": {
            "total_cost_usd": total_cost,
            "total_tokens": total_tokens,
            "avg_cost_per_1k_tokens": (
                (total_cost / (total_tokens / 1000)) if total_tokens > 0 else 0
            ),
        },
        "performance": {
            "avg_response_time_ms": 250,
            "requests_per_minute": 10,
            "error_rate": 0.0,
            "cache_hit_rate": 0.85,
        },
        "health": {
            "status": "healthy",
            "uptime_hours": uptime / 3600,
            "active_tasks": _task_stats["active"],
            "completed_tasks": _task_stats["completed"],
            "failed_tasks": _task_stats["failed"],
        },
        "timestamp": datetime.now().isoformat(),
    }


class TrackUsageRequest(BaseModel):
    """Request body for POST /api/metrics/track-usage."""

    model: str = Field(..., description="AI model identifier")
    tokens: int = Field(..., ge=0, description="Number of tokens consumed")
    cost: float = Field(..., ge=0.0, description="Cost in USD")


@metrics_router.post("/track-usage")
async def track_usage(
    body: TrackUsageRequest,
    current_user: UserProfile = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Track AI model usage for cost analysis.

    Accepts a JSON request body (not query parameters) so that sensitive
    cost/token data is not written to access logs or proxy caches.
    Requires: Valid JWT authentication.
    """
    if body.model not in _cost_metrics["models"]:
        _cost_metrics["models"][body.model] = {"tokens": 0, "cost": 0.0}

    _cost_metrics["models"][body.model]["tokens"] += body.tokens
    _cost_metrics["models"][body.model]["cost"] += body.cost
    _cost_metrics["total"] += body.cost

    logger.info(f"Tracked usage: {body.model} - {body.tokens} tokens, ${body.cost}")

    return {
        "success": True,
        "message": f"Tracked usage for {body.model}",
    }


# ============================================================================
# NEW Week 2 Cost Analytics Endpoints (Database-Backed)
# ============================================================================


@metrics_router.get("/costs/breakdown/phase")
async def get_costs_by_phase(
    current_user: UserProfile = Depends(get_current_user),
    period: str = Query("week", regex="^(today|week|month)$"),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> Dict[str, Any]:
    """
    Get cost breakdown by pipeline phase for Week 2 dashboard

    **Returns:**
    - Costs per phase (research, outline, draft, assess, refine, finalize)
    - Task counts and averages
    - Percentage of total spend

    **Example:** Shows research costs $0.50, outline $0.75, draft $2.00, etc.
    """
    try:
        cost_service = CostAggregationService(db_service=db_service)

        result = await cost_service.get_breakdown_by_phase(period=period)

        return result
    except Exception as e:
        logger.error(f"Error getting phase breakdown: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred")


@metrics_router.get("/costs/breakdown/model")
async def get_costs_by_model(
    current_user: UserProfile = Depends(get_current_user),
    period: str = Query("week", regex="^(today|week|month)$"),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> Dict[str, Any]:
    """
    Get cost breakdown by AI model for Week 2 dashboard

    **Returns:**
    - Costs per model (ollama, gpt-3.5, gpt-4, claude)
    - Provider information
    - Average cost per task
    - Percentage of total spend

    **Example:** Shows gpt-4 costs $1.50, gpt-3.5 $0.45, ollama $0.00
    """
    try:
        cost_service = CostAggregationService(db_service=db_service)

        result = await cost_service.get_breakdown_by_model(period=period)

        return result
    except Exception as e:
        logger.error(f"Error getting model breakdown: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred")


@metrics_router.get("/costs/history")
async def get_cost_history(
    current_user: UserProfile = Depends(get_current_user),
    period: str = Query("week", regex="^(week|month)$"),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> Dict[str, Any]:
    """
    Get cost history and trends for Week 2 dashboard

    **Returns:**
    - Daily cost data for past 7 or 30 days
    - Weekly average
    - Trend direction (up/down/stable)

    **Use case:** Visualize spending patterns, detect anomalies
    """
    try:
        cost_service = CostAggregationService(db_service=db_service)

        result = await cost_service.get_history(period=period)

        return result
    except Exception as e:
        logger.error(f"Error getting cost history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred")


@metrics_router.get("/costs/budget")
async def get_budget_status(
    current_user: UserProfile = Depends(get_current_user),
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
        logger.error(f"Error getting budget status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred")


# NOTE: GET /api/metrics/analytics/kpis was removed (duplicate of GET /api/analytics/kpis).
# Use GET /api/analytics/kpis for executive KPI dashboard data.
# See: src/cofounder_agent/routes/analytics_routes.py
#
# Tombstone retained for 2 releases; delete after 2026-06-01.
@metrics_router.get(
    "/analytics/kpis",
    deprecated=True,
    summary="[Deprecated] Use GET /api/analytics/kpis instead",
    include_in_schema=False,
)
async def get_kpi_analytics_deprecated(
    current_user: UserProfile = Depends(get_current_user),
):
    """Deprecated. Redirects callers to the canonical endpoint."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/api/analytics/kpis", status_code=308)




@metrics_router.get("/performance", response_model=Dict[str, Any])
async def get_performance_metrics(
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Get real-time performance metrics from the running application.

    **Authentication:** Requires valid JWT token

    **Data sources:**
    - Request latency (p50/p95/p99) from ProfilingMiddleware in-process telemetry.
      Tracks the last 1,000 requests across all endpoints.
    - Model router decisions from the in-process ModelRouter metrics counter.
    - Redis cache stats are omitted (the Redis client does not track hit rates).

    **Response shape** (consumed by PerformanceDashboard.jsx):
    ```json
    {
      "overall_stats": {
        "total_requests": 3420,
        "avg_latency_ms": 212.5,
        "p95_latency_ms": 890.0,
        "p99_latency_ms": 1250.0,
        "error_rate_pct": 0.8
      },
      "route_latencies": {
        "/api/tasks": {"p50": 180, "p95": 750, "p99": 1100, "cache_hit_rate": 0},
        ...
      },
      "model_router_decisions": {
        "budget": 3200,
        "premium": 220
      },
      "cache_stats": {}
    }
    ```
    """
    try:
        # --- latency data from ProfilingMiddleware ---
        profiling_mw = getattr(request.app.state, "profiling_middleware", None)

        route_latencies: Dict[str, Any] = {}
        overall_total = 0
        overall_durations: list = []

        if profiling_mw is not None:
            endpoint_stats = profiling_mw.get_endpoint_stats()

            for endpoint, stats in endpoint_stats.items():
                # Reconstruct a sorted duration list to compute p50.
                # ProfilingMiddleware stores all ProfileData objects; we have
                # pre-computed p95/p99 but p50 is the median we approximate
                # from avg when individual samples are not available.
                durations_for_ep = [
                    p.duration_ms
                    for p in profiling_mw.profiles
                    if p.endpoint == endpoint
                ]
                if durations_for_ep:
                    sorted_d = sorted(durations_for_ep)
                    n = len(sorted_d)
                    p50 = round(sorted_d[n // 2], 2)
                else:
                    p50 = stats.get("avg_duration_ms", 0)

                route_latencies[endpoint] = {
                    "p50": p50,
                    "p95": stats.get("p95_duration_ms", 0),
                    "p99": stats.get("p99_duration_ms", 0),
                    # Cache hit rate not tracked at request level; 0 indicates unavailable
                    "cache_hit_rate": 0,
                    "total_requests": stats.get("total_requests", 0),
                    "error_count": stats.get("error_count", 0),
                }

                overall_total += stats.get("total_requests", 0)
                overall_durations.extend(durations_for_ep)

        # Compute overall stats
        if overall_durations:
            sorted_all = sorted(overall_durations)
            n_all = len(sorted_all)
            avg_latency = round(sum(sorted_all) / n_all, 2)
            p95_all = round(sorted_all[int(n_all * 0.95)], 2) if n_all >= 2 else sorted_all[-1]
            p99_all = round(sorted_all[int(n_all * 0.99)], 2) if n_all >= 2 else sorted_all[-1]
            errors_all = sum(s.get("error_count", 0) for s in route_latencies.values())
            error_rate = round((errors_all / n_all * 100) if n_all > 0 else 0, 2)
        else:
            avg_latency = 0.0
            p95_all = 0.0
            p99_all = 0.0
            error_rate = 0.0

        overall_stats = {
            "total_requests": overall_total,
            "avg_latency_ms": avg_latency,
            "p95_latency_ms": p95_all,
            "p99_latency_ms": p99_all,
            "error_rate_pct": error_rate,
            "profiling_available": profiling_mw is not None,
        }

        # --- model router decisions ---
        from services.model_router import get_model_router

        router = get_model_router()
        if router is not None:
            router_metrics = router.get_metrics()
            model_router_decisions = {
                "budget": router_metrics.get("budget_model_uses", 0),
                "premium": router_metrics.get("premium_model_uses", 0),
                "total": router_metrics.get("total_requests", 0),
                "budget_pct": router_metrics.get("budget_model_percentage", 0),
                "estimated_cost_saved_usd": router_metrics.get("estimated_cost_saved", 0),
            }
        else:
            model_router_decisions = {}

        return {
            "overall_stats": overall_stats,
            "route_latencies": route_latencies,
            "model_router_decisions": model_router_decisions,
            # Redis does not expose hit-rate counters; omit rather than fabricate
            "cache_stats": {},
            "timestamp": datetime.now().isoformat(),
            "note": (
                "Latency data is from in-process ProfilingMiddleware (last 1,000 requests). "
                "Resets on server restart."
            ),
        }

    except Exception as e:
        logger.error(f"[get_performance_metrics] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve performance metrics")


@metrics_router.get(
    "/operational",
    summary="Operational gauges — task queue depth, executor health, WebSocket connections",
    description=(
        "Returns in-memory operational metrics without requiring a database query. "
        "Designed for external monitoring scraping (Railway, Datadog, Loki). "
        "Requires authentication for consistency with other metrics endpoints."
    ),
    include_in_schema=True,
)
async def get_operational_metrics(
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> Dict[str, Any]:
    """
    Return structured operational gauges for alerting and dashboards.

    Emits a structured INFO log alongside the JSON response so that
    log-based metric pipelines (Railway/Loki) can create dashboards
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
                task_counts = {str(k): int(v) for k, v in (raw or {}).items()}
        except Exception as db_err:
            logger.warning(f"[operational_metrics] DB task count unavailable: {db_err}", exc_info=True)

        pending = task_counts.get("pending", 0)
        in_progress = task_counts.get("in_progress", 0)
        failed = task_counts.get("failed", 0)
        completed = task_counts.get("completed", 0)

        # --- Task executor in-memory stats ---
        executor_stats: Dict[str, Any] = {}
        try:
            from services.service_container import get_service_container  # type: ignore[import-untyped]

            container = get_service_container()
            if container and hasattr(container, "task_executor") and container.task_executor:
                executor_stats = container.task_executor.get_stats()
        except Exception as ex_err:
            logger.debug(f"[operational_metrics] Executor stats unavailable: {ex_err}")

        # --- WebSocket connections ---
        ws_total = 0
        try:
            from services.websocket_manager import websocket_manager

            ws_total = sum(
                len(conns) for conns in websocket_manager.active_connections.values()
            )
        except Exception as ws_err:
            logger.debug(f"[operational_metrics] WebSocket count unavailable: {ws_err}")

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
                f"[operational_metrics] queue_depth={queue_depth} "
                f"pending={pending} in_progress={in_progress} "
                f"errors={executor_stats.get('error_count', 0)} "
                f"ws_connections={ws_total}",
            )
        else:
            logger.info(
                f"[operational_metrics] queue_depth={queue_depth} "
                f"pending={pending} in_progress={in_progress} "
                f"errors={executor_stats.get('error_count', 0)} "
                f"ws_connections={ws_total}",
            )

        return metrics

    except Exception as e:
        logger.error(f"[get_operational_metrics] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve operational metrics")
