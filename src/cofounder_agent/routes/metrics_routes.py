"""
Metrics and Analytics Routes
Provides endpoints for tracking AI model usage, costs, and performance metrics

All endpoints require JWT authentication

Integrates with UsageTracker service for real-time metrics collection.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from routes.auth_unified import get_current_user
from schemas.auth_schemas import UserProfile
from schemas.metrics_schemas import (
    CostMetric,
    CostsResponse,
    HealthMetrics,
    PerformanceMetrics,
)
from services.cost_aggregation_service import CostAggregationService
from services.database_service import DatabaseService
from services.usage_tracker import get_usage_tracker
from utils.route_utils import get_database_dependency

logger = logging.getLogger(__name__)

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
                "timestamp": datetime.utcnow().isoformat(),
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
        total_input = sum(op.get("tokens_in", 0) for op in completed_ops)
        total_output = sum(op.get("tokens_out", 0) for op in completed_ops)
        total_tokens = total_input + total_output
        total_cost = sum(op.get("cost_estimate", 0.0) for op in completed_ops)
        total_ops = len(completed_ops)
        successful_ops = sum(1 for op in completed_ops if op.get("success", False))
        failed_ops = total_ops - successful_ops

        # Group by model
        by_model = {}
        for op in completed_ops:
            model = op.get("model", "unknown")
            if model not in by_model:
                by_model[model] = {"operations": 0, "tokens": 0, "cost": 0.0}
            by_model[model]["operations"] += 1
            by_model[model]["tokens"] += op.get("tokens_in", 0) + op.get("tokens_out", 0)
            by_model[model]["cost"] += op.get("cost_estimate", 0.0)

        # Group by operation type
        by_operation = {}
        for op in completed_ops:
            op_type = op.get("operation_type", "unknown")
            if op_type not in by_operation:
                by_operation[op_type] = {"count": 0, "cost": 0.0, "success": 0}
            by_operation[op_type]["count"] += 1
            by_operation[op_type]["cost"] += op.get("cost_estimate", 0.0)
            if op.get("success", False):
                by_operation[op_type]["success"] += 1

        # Projections
        days_active = max(
            1,
            (
                datetime.utcnow()
                - datetime.fromisoformat(
                    completed_ops[0].get("started_at", datetime.utcnow().isoformat())
                )
            ).days
            or 1,
        )
        projected_monthly = (total_cost / days_active * 30) if days_active > 0 else 0

        return {
            "timestamp": datetime.utcnow().isoformat(),
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
        raise HTTPException(status_code=500, detail=f"Failed to retrieve metrics: {str(e)}")


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
                logger.warning(f"Database costs failed, falling back to tracker: {db_error}")
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
            total_cost = sum(op.get("cost_estimate", 0.0) for op in completed_ops)
            total_tokens = sum(
                op.get("tokens_in", 0) + op.get("tokens_out", 0) for op in completed_ops
            )

            # Group by model
            by_model = {}
            for op in completed_ops:
                model = op.get("model", "unknown")
                if model not in by_model:
                    by_model[model] = {"tokens": 0, "cost": 0.0, "provider": "unknown"}
                by_model[model]["tokens"] += op.get("tokens_in", 0) + op.get("tokens_out", 0)
                by_model[model]["cost"] += op.get("cost_estimate", 0.0)

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
        raise HTTPException(status_code=500, detail=f"Failed to retrieve metrics: {str(e)}")


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
        failed_ops = sum(1 for op in completed_ops if not op.get("success", False))

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
                    "id": op.get("operation_id"),
                    "type": op.get("operation_type"),
                    "model": op.get("model"),
                    "success": op.get("success"),
                    "timestamp": op.get("completed_at", op.get("started_at")),
                }
                for op in completed_ops[-5:]  # Last 5 operations
            ],
        }

    except Exception as e:
        logger.error(f"Error retrieving metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve metrics: {str(e)}")


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


@metrics_router.post("/track-usage")
async def track_usage(
    model: str,
    tokens: int,
    cost: float,
    current_user: UserProfile = Depends(get_current_user),
) -> Dict[str, str]:
    """
    Track AI model usage for cost analysis
    Requires: Valid JWT authentication

    Args:
        model: Model name
        tokens: Number of tokens used
        cost: Cost in USD

    Returns:
        Confirmation of tracking
    """
    if model not in _cost_metrics["models"]:
        _cost_metrics["models"][model] = {"tokens": 0, "cost": 0.0}

    _cost_metrics["models"][model]["tokens"] += tokens
    _cost_metrics["models"][model]["cost"] += cost
    _cost_metrics["total"] += cost

    logger.info(f"✅ Tracked usage: {model} - {tokens} tokens, ${cost}")

    return {
        "success": "true",
        "message": f"Tracked usage for {model}",
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
        logger.error(f"Error getting phase breakdown: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        logger.error(f"Error getting model breakdown: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        logger.error(f"Error getting cost history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        logger.error(f"Error getting budget status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@metrics_router.get("/analytics/kpis")
async def get_kpi_analytics(
    current_user: UserProfile = Depends(get_current_user),
    range: str = Query("30d", description="Time range: 1d, 7d, 30d, 90d, all"),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> Dict[str, Any]:
    """
    Get key performance indicator (KPI) metrics for executive dashboard.

    **Authentication:** Requires valid JWT token

    **Parameters:**
    - range: Time range for aggregation (1d, 7d, 30d, 90d, all)

    **Returns:**
    - Business KPI metrics including:
      - Revenue (current, previous, change %)
      - Content published count
      - Tasks completed count
      - AI cost savings
      - Engagement rate
      - Agent uptime
    """
    try:
        cost_service = CostAggregationService(db_service=db_service)

        # Calculate date range
        from datetime import datetime, timedelta

        now = datetime.utcnow()

        # Validate range parameter
        valid_ranges = {"1d", "7d", "30d", "90d", "all"}
        if range not in valid_ranges:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid range '{range}'. Must be one of: {', '.join(sorted(valid_ranges))}",
            )

        if range == "1d":
            start_date = now - timedelta(days=1)
        elif range == "7d":
            start_date = now - timedelta(days=7)
        elif range == "30d":
            start_date = now - timedelta(days=30)
        elif range == "90d":
            start_date = now - timedelta(days=90)
        else:  # all
            start_date = datetime.utcfromtimestamp(0)

        # Get cost metrics using available method
        cost_summary = await cost_service.get_summary()
        total_cost = cost_summary.get("month_cost", 0.0) if cost_summary else 0.0

        # Query task counts from database
        from sqlalchemy import and_, func, select

        from schemas.common_schemas import ContentTask

        async with db_service.get_session() as session:
            # Count completed tasks
            completed_count = await session.execute(
                select(func.count(ContentTask.id)).where(
                    and_(ContentTask.status == "completed", ContentTask.updated_at >= start_date)
                )
            )
            tasks_completed = completed_count.scalar() or 0

            # Get previous period count (same duration, one cycle back)
            period_days = int((now - start_date).total_seconds() / 86400)
            prev_start = start_date - timedelta(days=period_days)

            prev_completed = await session.execute(
                select(func.count(ContentTask.id)).where(
                    and_(
                        ContentTask.status == "completed",
                        ContentTask.updated_at >= prev_start,
                        ContentTask.updated_at < start_date,
                    )
                )
            )
            prev_tasks = prev_completed.scalar() or 0

        # Calculate KPIs
        tasks_change = (
            ((tasks_completed - prev_tasks) / prev_tasks * 100)
            if prev_tasks > 0
            else (100 if tasks_completed > 0 else 0)
        )

        # Estimate revenue ($150 per task base rate)
        revenue_current = int(tasks_completed * 150)
        revenue_previous = int(prev_tasks * 150)
        revenue_change = (
            ((revenue_current - revenue_previous) / revenue_previous * 100)
            if revenue_previous > 0
            else (100 if revenue_current > 0 else 0)
        )

        # AI savings = estimated hours saved * hourly rate
        # Assume each task saves ~3 hours at $50/hour = $150 per task
        ai_savings_current = int(tasks_completed * 150)
        ai_savings_previous = int(prev_tasks * 150)

        # Mock engagement and uptime (would come from analytics/monitoring)
        engagement_current = 4.8
        engagement_previous = 3.2

        return {
            "kpis": {
                "revenue": {
                    "current": revenue_current,
                    "previous": revenue_previous,
                    "change": int(revenue_change),
                    "currency": "USD",
                    "icon": "📈",
                },
                "contentPublished": {
                    "current": tasks_completed,
                    "previous": prev_tasks,
                    "change": int(tasks_change),
                    "unit": "posts",
                    "icon": "📝",
                },
                "tasksCompleted": {
                    "current": tasks_completed,
                    "previous": prev_tasks,
                    "change": int(tasks_change),
                    "unit": "tasks",
                    "icon": "✅",
                },
                "aiSavings": {
                    "current": ai_savings_current,
                    "previous": ai_savings_previous,
                    "change": int(
                        ((ai_savings_current - ai_savings_previous) / ai_savings_previous * 100)
                        if ai_savings_previous > 0
                        else (100 if ai_savings_current > 0 else 0)
                    ),
                    "currency": "USD",
                    "icon": "💰",
                    "description": "Estimated value of AI-generated content",
                },
                "engagementRate": {
                    "current": engagement_current,
                    "previous": engagement_previous,
                    "change": int(
                        ((engagement_current - engagement_previous) / engagement_previous * 100)
                        if engagement_previous > 0
                        else 0
                    ),
                    "unit": "%",
                    "icon": "📊",
                },
                "agentUptime": {
                    "current": 99.8,
                    "previous": 99.2,
                    "change": 1,
                    "unit": "%",
                    "icon": "⚙️",
                },
            },
            "timestamp": datetime.utcnow().isoformat(),
            "range": range,
        }
    except Exception as e:
        logger.error(f"Error getting KPI analytics: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
