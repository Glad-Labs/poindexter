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

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from routes.auth_unified import get_current_user
from schemas.auth_schemas import UserProfile
from services.database_service import DatabaseService
from utils.error_handler import handle_route_error
from utils.route_utils import get_database_dependency

logger = logging.getLogger(__name__)

analytics_router = APIRouter(prefix="/api/analytics", tags=["analytics"])


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
    current_user: Optional[UserProfile] = Depends(lambda: None),  # Optional auth
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
        now = datetime.utcnow()
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

        # ===== QUERY TASK STATISTICS =====
        logger.debug(f"  🔍 Querying task statistics from content_tasks...")

        # Query tasks from database for this date range
        tasks = await db.get_tasks_by_date_range(start_date=start_time, end_date=now, limit=10000)

        logger.debug(f"  ✅ Retrieved {len(tasks)} tasks from database")

        if not tasks:
            # Return zero metrics if no tasks found
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

        # ===== AGGREGATE STATISTICS =====
        logger.debug(f"  🔄 Aggregating statistics...")

        total_tasks = len(tasks)
        completed_tasks = sum(1 for t in tasks if t.get("status") == "completed")
        failed_tasks = sum(1 for t in tasks if t.get("status") == "failed")
        pending_tasks = total_tasks - completed_tasks - failed_tasks

        # Success rates
        success_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
        failure_rate = (failed_tasks / total_tasks * 100) if total_tasks > 0 else 0
        completion_rate = (
            ((completed_tasks + failed_tasks) / total_tasks * 100) if total_tasks > 0 else 0
        )

        logger.debug(
            f"  ✅ Stats: {total_tasks} total, {completed_tasks} completed, {failed_tasks} failed, {pending_tasks} pending"
        )
        logger.debug(f"  📈 Success rate: {success_rate:.1f}%")

        # ===== EXECUTION TIME METRICS =====
        logger.debug(f"  ⏱️  Calculating execution times...")

        execution_times = []
        for task in tasks:
            created = task.get("created_at")
            completed = task.get("completed_at")

            if created and completed:
                # Parse ISO strings if needed
                if isinstance(created, str):
                    created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if isinstance(completed, str):
                    completed = datetime.fromisoformat(completed.replace("Z", "+00:00"))

                duration = (completed - created).total_seconds()
                if duration >= 0:  # Ignore negative durations
                    execution_times.append(duration)

        if execution_times:
            avg_execution_time = sum(execution_times) / len(execution_times)
            execution_times_sorted = sorted(execution_times)
            median_execution_time = execution_times_sorted[len(execution_times_sorted) // 2]
            min_execution_time = min(execution_times)
            max_execution_time = max(execution_times)
        else:
            avg_execution_time = 0.0
            median_execution_time = 0.0
            min_execution_time = 0.0
            max_execution_time = 0.0

        logger.debug(
            f"  ⏱️  Avg execution: {avg_execution_time:.1f}s, Median: {median_execution_time:.1f}s"
        )

        # ===== COST METRICS =====
        logger.debug(f"  💰 Calculating cost metrics...")

        total_cost = 0.0
        cost_by_model = {}
        cost_by_phase = {}
        models_used = {}
        task_types = {}

        for task in tasks:
            # Cost calculation (convert Decimal to float if needed)
            cost_raw = task.get("estimated_cost") or task.get("actual_cost") or 0.0
            cost = float(cost_raw) if cost_raw else 0.0
            total_cost = float(total_cost) + cost

            # Model tracking
            model = task.get("model_used") or "unknown"
            if model in models_used:
                models_used[model] += 1
            else:
                models_used[model] = 1

            # Cost by model
            if model in cost_by_model:
                cost_by_model[model] = float(cost_by_model[model]) + cost
            else:
                cost_by_model[model] = float(cost)

            # Task type breakdown
            task_type = task.get("task_type") or "unknown"
            if task_type in task_types:
                task_types[task_type] += 1
            else:
                task_types[task_type] = 1

            # Cost by phase (from task_metadata if available)
            if task.get("task_metadata"):
                import json

                metadata = task.get("task_metadata")
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except (json.JSONDecodeError, ValueError):
                        metadata = {}

                # Extract phase costs from metadata.cost_breakdown if present
                phase_costs = metadata.get("cost_breakdown", {})
                for phase, phase_cost in phase_costs.items():
                    phase_cost = float(phase_cost) if phase_cost else 0.0
                    if phase in cost_by_phase:
                        cost_by_phase[phase] = float(cost_by_phase[phase]) + phase_cost
                    else:
                        cost_by_phase[phase] = phase_cost

        avg_cost_per_task = (total_cost / total_tasks) if total_tasks > 0 else 0.0
        primary_model = max(models_used, key=models_used.get) if models_used else "none"

        logger.debug(f"  💰 Total cost: ${total_cost:.6f}, Avg/task: ${avg_cost_per_task:.6f}")
        logger.debug(f"  🤖 Primary model: {primary_model}")

        # ===== TIME-SERIES DATA FOR CHARTS =====
        logger.debug(f"  📈 Generating time-series data...")

        # Group tasks by day for charts
        tasks_by_day = {}
        cost_by_day = {}
        success_by_day = {}

        for task in tasks:
            created = task.get("created_at")
            if isinstance(created, str):
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))

            day_key = created.date().isoformat()

            # Task count per day
            if day_key not in tasks_by_day:
                tasks_by_day[day_key] = {"date": day_key, "count": 0}
            tasks_by_day[day_key]["count"] += 1

            # Cost per day
            cost = task.get("estimated_cost") or task.get("actual_cost") or 0.0
            cost = float(cost) if cost else 0.0
            if day_key not in cost_by_day:
                cost_by_day[day_key] = {"date": day_key, "cost": 0.0}
            cost_by_day[day_key]["cost"] = float(cost_by_day[day_key]["cost"]) + cost

            # Success per day
            if day_key not in success_by_day:
                success_by_day[day_key] = {"date": day_key, "completed": 0, "total": 0}
            success_by_day[day_key]["total"] += 1
            if task.get("status") == "completed":
                success_by_day[day_key]["completed"] += 1

        # Convert to sorted lists
        tasks_per_day = sorted(tasks_by_day.values(), key=lambda x: x["date"])
        cost_per_day = sorted(cost_by_day.values(), key=lambda x: x["date"])

        # Calculate success rate per day
        success_trend = []
        for day_key in sorted(success_by_day.keys()):
            day_data = success_by_day[day_key]
            success_pct = (
                (day_data["completed"] / day_data["total"] * 100) if day_data["total"] > 0 else 0
            )
            success_trend.append(
                {
                    "date": day_key,
                    "success_rate": success_pct,
                    "completed": day_data["completed"],
                    "total": day_data["total"],
                }
            )

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
            cost_by_phase=cost_by_phase,
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
        now = datetime.utcnow()
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
        distributions_raw = await db.query(
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
