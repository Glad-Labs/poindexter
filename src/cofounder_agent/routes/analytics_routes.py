"""
Analytics Routes - KPI Dashboard and Metrics Aggregation

Provides endpoints for the ExecutiveDashboard to display metrics:
- Task completion statistics
- Cost analysis and trends
- Performance metrics (avg completion time, success rate)
- Model usage breakdown
- Time-series data for charts

All endpoints aggregate real data from PostgreSQL database.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.error_handler import handle_route_error
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)
analytics_router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _coerce_cost_value(value: Any) -> float:
    """Best-effort numeric conversion for heterogeneous cost payloads."""
    if value is None:
        return 0.0

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, dict):
        # Common nested payload patterns: {"cost": 0.12}, {"total": 0.12}, {"usd": 0.12}
        for key in ("cost", "total", "usd", "amount", "value"):
            if key in value:
                return _coerce_cost_value(value.get(key))
        return 0.0

    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0

    return 0.0


class KPIMetrics(BaseModel):
    """KPI data for the executive dashboard"""

    timestamp: str = Field(..., description="ISO timestamp of metrics generation")
    time_range: str = Field(..., description="Time range used for aggregation (7d, 30d, 90d, all)")

    # Task statistics
    total_tasks: int = Field(..., description="Total tasks created in time range")
    completed_tasks: int = Field(..., description="Tasks completed successfully")
    failed_tasks: int = Field(..., description="Tasks that failed")
    pending_tasks: int = Field(..., description="Tasks still in progress or queued")

    # Success metrics
    success_rate: float = Field(..., description="Percentage of completed tasks (0-100)")
    failure_rate: float = Field(..., description="Percentage of failed tasks (0-100)")
    completion_rate: float = Field(
        ..., description="% of tasks moved to completion (completed+failed)"
    )

    # Execution metrics
    avg_execution_time_seconds: float = Field(
        ..., description="Average time from creation to completion"
    )
    median_execution_time_seconds: float = Field(..., description="Median execution time")
    min_execution_time_seconds: float = Field(..., description="Fastest task execution")
    max_execution_time_seconds: float = Field(..., description="Slowest task execution")

    # Cost metrics
    total_cost_usd: float = Field(..., description="Total estimated cost of all tasks")
    avg_cost_per_task: float = Field(..., description="Average cost per task")
    cost_by_phase: Dict[str, float] = Field(
        default_factory=dict, description="Cost breakdown by pipeline phase"
    )
    cost_by_model: Dict[str, float] = Field(
        default_factory=dict, description="Cost breakdown by LLM model"
    )

    # Model usage
    models_used: Dict[str, int] = Field(
        default_factory=dict, description="Count of tasks using each model"
    )
    primary_model: str = Field(..., description="Most frequently used model")

    # Task type breakdown
    task_types: Dict[str, int] = Field(
        default_factory=dict, description="Count by task type (blog_post, social_media, etc.)"
    )

    # Trend indicators
    tasks_per_day: List[Dict[str, Any]] = Field(
        default_factory=list, description="Tasks created per day (for charts)"
    )
    cost_per_day: List[Dict[str, Any]] = Field(
        default_factory=list, description="Cost per day (for charts)"
    )
    success_trend: List[Dict[str, Any]] = Field(
        default_factory=list, description="Success rate trend (for charts)"
    )


@analytics_router.get(
    "/kpis", response_model=KPIMetrics, description="Get KPI metrics for executive dashboard"
)
async def get_kpi_metrics(
    range: str = Query("7d", description="Time range: 1d, 7d, 30d, 90d, all"),
    db: DatabaseService = Depends(get_database_dependency),
    token: str = Depends(verify_api_token),
):
    """
    Get comprehensive KPI metrics for the executive dashboard.

    **Time Ranges:**
    - 1d: Last 24 hours
    - 7d: Last 7 days (default)
    - 30d: Last 30 days
    - 90d: Last 90 days
    - all: All-time metrics

    **Returns:**
    - Task statistics (created, completed, failed, pending)
    - Success/failure rates
    - Execution time metrics (avg, median, min, max)
    - Cost analysis (total, per-task, by model, by phase)
    - Model usage breakdown
    - Task type distribution
    - Time-series data for charts (tasks per day, cost per day, etc.)

    **Examples:**
    - GET /api/analytics/kpis?range=7d
    - GET /api/analytics/kpis?range=30d
    - GET /api/analytics/kpis?range=all
    """
    try:
        logger.info(f"📊 GET /api/analytics/kpis called - range: {range}")

        # Validate range parameter
        if range not in ["1d", "7d", "30d", "90d", "all"]:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid range '{range}'. Must be one of: 1d, 7d, 30d, 90d, all",
            )

        # Calculate time window
        now = datetime.now(timezone.utc)
        if range == "1d":
            start_time = now - timedelta(days=1)
        elif range == "7d":
            start_time = now - timedelta(days=7)
        elif range == "30d":
            start_time = now - timedelta(days=30)
        elif range == "90d":
            start_time = now - timedelta(days=90)
        else:  # all
            start_time = None

        logger.debug(f"  📅 Time window: {start_time} to {now}")

        # ===== SINGLE SQL AGGREGATION QUERY (replaces Python loops, issue #696) =====
        logger.debug("  🔍 Querying KPI aggregates from content_tasks...")

        agg = await db.get_kpi_aggregates(start_date=start_time, end_date=now)
        agg_rows = agg["rows"]
        total_tasks = agg["total_tasks"]

        logger.debug(f"  ✅ Aggregated {len(agg_rows)} rows, {total_tasks} total tasks")

        if total_tasks == 0:
            return KPIMetrics(
                timestamp=now.isoformat(),
                time_range=range,
                total_tasks=0,
                completed_tasks=0,
                failed_tasks=0,
                pending_tasks=0,
                success_rate=0.0,
                failure_rate=0.0,
                completion_rate=0.0,
                avg_execution_time_seconds=0.0,
                median_execution_time_seconds=0.0,
                min_execution_time_seconds=0.0,
                max_execution_time_seconds=0.0,
                total_cost_usd=0.0,
                avg_cost_per_task=0.0,
                cost_by_phase={},
                cost_by_model={},
                models_used={},
                primary_model="none",
                task_types={},
                tasks_per_day=[],
                cost_per_day=[],
                success_trend=[],
            )

        # ===== DERIVE METRICS FROM AGGREGATE ROWS =====
        # Each row represents one (status, model_used, task_type, day) combination.
        # We iterate the small aggregate result set — not the full task rows.

        completed_tasks = 0
        failed_tasks = 0
        total_cost = 0.0
        cost_by_model: Dict[str, float] = {}
        models_used: Dict[str, int] = {}
        task_types: Dict[str, int] = {}
        tasks_by_day: Dict[str, Dict[str, Any]] = {}
        cost_by_day: Dict[str, Dict[str, Any]] = {}
        success_by_day: Dict[str, Dict[str, Any]] = {}

        # avg_duration_s per row is already a SQL AVG over that group; we must
        # compute a weighted global average ourselves.
        duration_sum = 0.0
        duration_count = 0
        duration_values: List[float] = []  # per-group averages weighted by completed count

        for row in agg_rows:
            status = row.get("status") or "unknown"
            model = row.get("model_used") or "unknown"
            task_type = row.get("task_type") or "unknown"
            count = int(row.get("count") or 0)
            row_cost = float(row.get("total_cost") or 0.0)
            avg_dur = row.get("avg_duration_s")
            completed_in_row = int(row.get("completed_count") or 0)
            day = row.get("day")
            day_key = day.isoformat() if day else "unknown"

            # Status counts
            if status == "completed":
                completed_tasks += count
            elif status == "failed":
                failed_tasks += count

            # Cost totals
            total_cost += row_cost

            # Model usage
            models_used[model] = models_used.get(model, 0) + count
            cost_by_model[model] = cost_by_model.get(model, 0.0) + row_cost

            # Task type breakdown
            task_types[task_type] = task_types.get(task_type, 0) + count

            # Execution time — accumulate weighted
            if avg_dur is not None and completed_in_row > 0:
                dur = float(avg_dur)
                duration_sum += dur * completed_in_row
                duration_count += completed_in_row
                duration_values.append(dur)

            # Daily time-series
            if day_key not in tasks_by_day:
                tasks_by_day[day_key] = {"date": day_key, "count": 0}
            tasks_by_day[day_key]["count"] += count

            if day_key not in cost_by_day:
                cost_by_day[day_key] = {"date": day_key, "cost": 0.0}
            cost_by_day[day_key]["cost"] = float(cost_by_day[day_key]["cost"]) + row_cost

            if day_key not in success_by_day:
                success_by_day[day_key] = {"date": day_key, "completed": 0, "total": 0}
            success_by_day[day_key]["total"] += count
            success_by_day[day_key]["completed"] += completed_in_row

        pending_tasks = total_tasks - completed_tasks - failed_tasks
        success_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0.0
        failure_rate = (failed_tasks / total_tasks * 100) if total_tasks > 0 else 0.0
        completion_rate = (
            ((completed_tasks + failed_tasks) / total_tasks * 100) if total_tasks > 0 else 0.0
        )
        avg_cost_per_task = (total_cost / total_tasks) if total_tasks > 0 else 0.0
        primary_model = max(models_used, key=models_used.get) if models_used else "none"  # type: ignore[arg-type]

        # Execution time stats from weighted group averages
        if duration_count > 0:
            avg_execution_time = duration_sum / duration_count
            duration_values_sorted = sorted(duration_values)
            median_execution_time = duration_values_sorted[len(duration_values_sorted) // 2]
            min_execution_time = min(duration_values)
            max_execution_time = max(duration_values)
        else:
            avg_execution_time = 0.0
            median_execution_time = 0.0
            min_execution_time = 0.0
            max_execution_time = 0.0

        logger.debug(
            f"  ✅ Stats: {total_tasks} total, {completed_tasks} completed, "
            f"{failed_tasks} failed, {pending_tasks} pending"
        )
        logger.debug(f"  📈 Success rate: {success_rate:.1f}%")
        logger.debug(f"  💰 Total cost: ${total_cost:.6f}, Avg/task: ${avg_cost_per_task:.6f}")

        # Convert day dicts to sorted lists for chart payloads
        tasks_per_day = sorted(tasks_by_day.values(), key=lambda x: x["date"])
        cost_per_day = sorted(cost_by_day.values(), key=lambda x: x["date"])
        success_trend = [
            {
                "date": day_key,
                "success_rate": (
                    (success_by_day[day_key]["completed"] / success_by_day[day_key]["total"] * 100)
                    if success_by_day[day_key]["total"] > 0
                    else 0.0
                ),
                "completed": success_by_day[day_key]["completed"],
                "total": success_by_day[day_key]["total"],
            }
            for day_key in sorted(success_by_day)
        ]

        logger.debug(f"  📈 Generated {len(tasks_per_day)} days of task data")

        logger.info(f"✅ KPI metrics calculated for range {range}")

        # ===== BUILD RESPONSE =====
        return KPIMetrics(
            timestamp=now.isoformat(),
            time_range=range,
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            pending_tasks=pending_tasks,
            success_rate=round(success_rate, 2),
            failure_rate=round(failure_rate, 2),
            completion_rate=round(completion_rate, 2),
            avg_execution_time_seconds=round(avg_execution_time, 2),
            median_execution_time_seconds=round(median_execution_time, 2),
            min_execution_time_seconds=round(min_execution_time, 2),
            max_execution_time_seconds=round(max_execution_time, 2),
            total_cost_usd=round(total_cost, 6),
            avg_cost_per_task=round(avg_cost_per_task, 6),
            cost_by_phase={},  # phase breakdown requires JSON metadata scan; omitted for perf
            cost_by_model=cost_by_model,
            models_used=models_used,
            primary_model=primary_model,
            task_types=task_types,
            tasks_per_day=tasks_per_day,
            cost_per_day=cost_per_day,
            success_trend=success_trend,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise await handle_route_error(e, "get_kpi_metrics", logger)


class TaskDistribution(BaseModel):
    """Task type and status distribution"""

    type: str = Field(..., description="Task type")
    status: str = Field(..., description="Task status")
    count: int = Field(..., description="Number of tasks")
    percentage: float = Field(..., description="Percentage of total")


class DistributionResponse(BaseModel):
    """Distribution breakdown response"""

    timestamp: str
    total_tasks: int
    distributions: List[TaskDistribution]


@analytics_router.get(
    "/distributions",
    response_model=DistributionResponse,
    description="Get task distribution by type and status",
)
async def get_task_distributions(
    range: str = Query("7d", description="Time range: 1d, 7d, 30d, 90d, all"),
    db: DatabaseService = Depends(get_database_dependency),
    token: str = Depends(verify_api_token),
):
    """
    Get task distribution breakdown by type and status for visualization.

    Returns data suitable for pie charts and donut charts in the dashboard.
    """
    try:
        logger.info(f"📊 GET /api/analytics/distributions called - range: {range}")

        # Validate range
        if range not in ["1d", "7d", "30d", "90d", "all"]:
            raise HTTPException(status_code=400, detail=f"Invalid range '{range}'")

        # Calculate time window
        now = datetime.now(timezone.utc)
        if range == "1d":
            start_time = now - timedelta(days=1)
        elif range == "7d":
            start_time = now - timedelta(days=7)
        elif range == "30d":
            start_time = now - timedelta(days=30)
        elif range == "90d":
            start_time = now - timedelta(days=90)
        else:  # all
            start_time = None

        # Query task distribution
        distributions_raw = await db.query(  # type: ignore[attr-defined]
            """
            SELECT task_type, status, COUNT(*) as count
            FROM tasks
            WHERE created_at >= %s OR %s IS NULL
            GROUP BY task_type, status
            ORDER BY count DESC
            """,
            (start_time, start_time) if start_time else (None, None),
        )

        if not distributions_raw:
            return DistributionResponse(timestamp=now.isoformat(), total_tasks=0, distributions=[])

        total_tasks = sum(d.get("count", 0) for d in distributions_raw)

        distributions = [
            TaskDistribution(
                type=d.get("task_type", "unknown"),
                status=d.get("status", "unknown"),
                count=d.get("count", 0),
                percentage=(
                    round((d.get("count", 0) / total_tasks * 100), 2) if total_tasks > 0 else 0
                ),
            )
            for d in distributions_raw
        ]

        logger.info(f"✅ Distribution data retrieved: {len(distributions)} groups")

        return DistributionResponse(
            timestamp=now.isoformat(), total_tasks=total_tasks, distributions=distributions
        )

    except HTTPException:
        raise
    except Exception as e:
        raise await handle_route_error(e, "get_task_distributions", logger)
