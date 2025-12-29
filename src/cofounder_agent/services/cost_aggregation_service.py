"""
Cost Aggregation Service

Provides advanced cost analytics by querying the cost_logs PostgreSQL table:
- Aggregate costs by phase, model, provider
- Calculate daily/weekly/monthly trends
- Project monthly spend based on usage patterns
- Generate budget alerts

Built on top of DatabaseService's log_cost() and get_task_costs() methods.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from decimal import Decimal

logger = logging.getLogger(__name__)


class CostAggregationService:
    """
    Cost analytics service using PostgreSQL cost_logs table
    """

    def __init__(self, db_service=None):
        """
        Initialize cost aggregation service

        Args:
            db_service: DatabaseService instance (injected)
        """
        self.db = db_service
        self.monthly_budget = 150.0  # Default solopreneur budget

    async def get_summary(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get cost summary for current month

        Returns: {
            "total_spent": 12.50,
            "today_cost": 0.50,
            "week_cost": 4.25,
            "month_cost": 12.50,
            "monthly_budget": 150.0,
            "budget_used_percent": 8.33,
            "projected_monthly": 45.00,
            "tasks_completed": 42,
            "avg_cost_per_task": 0.30,
            "last_updated": "2025-12-19T..."
        }
        """
        try:
            if not self.db or not self.db.pool:
                return self._get_empty_summary()

            async with self.db.pool.acquire() as conn:
                # Get today's costs
                today_start = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                today_row = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(cost_usd), 0) as total
                    FROM cost_logs
                    WHERE created_at >= $1 AND success = true
                    """,
                    today_start,
                )
                today_cost = float(today_row or 0.0)

                # Get this week's costs (last 7 days)
                week_start = datetime.now(timezone.utc) - timedelta(days=7)
                week_row = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(cost_usd), 0) as total
                    FROM cost_logs
                    WHERE created_at >= $1 AND success = true
                    """,
                    week_start,
                )
                week_cost = float(week_row or 0.0)

                # Get this month's costs
                month_start = datetime.now(timezone.utc).replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )
                month_row = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(cost_usd), 0) as total
                    FROM cost_logs
                    WHERE created_at >= $1 AND success = true
                    """,
                    month_start,
                )
                month_cost = float(month_row or 0.0)

                # Get task count
                tasks_row = await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT task_id) as count
                    FROM cost_logs
                    WHERE created_at >= $1 AND success = true
                    """,
                    month_start,
                )
                tasks_count = int(tasks_row or 0)

                # Calculate average cost per task
                avg_cost_per_task = month_cost / tasks_count if tasks_count > 0 else 0.0

                # Project monthly spend (extrapolate from weekly average)
                days_elapsed = (datetime.now(timezone.utc) - month_start).days
                days_in_month = 30
                if days_elapsed > 0:
                    daily_avg = month_cost / days_elapsed
                    projected_monthly = daily_avg * days_in_month
                else:
                    projected_monthly = month_cost

                budget_used_percent = (
                    (month_cost / self.monthly_budget * 100) if self.monthly_budget > 0 else 0
                )

                return {
                    "total_spent": round(month_cost, 2),
                    "today_cost": round(today_cost, 2),
                    "week_cost": round(week_cost, 2),
                    "month_cost": round(month_cost, 2),
                    "monthly_budget": self.monthly_budget,
                    "budget_used_percent": round(budget_used_percent, 2),
                    "projected_monthly": round(projected_monthly, 2),
                    "tasks_completed": tasks_count,
                    "avg_cost_per_task": round(avg_cost_per_task, 4),
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            logger.error(f"Error getting cost summary: {e}")
            return self._get_empty_summary()

    async def get_breakdown_by_phase(
        self, period: str = "week", user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get cost breakdown by pipeline phase

        Args:
            period: "today", "week", or "month"
            user_id: Filter by user (optional)

        Returns: {
            "period": "week",
            "phases": [
                {"phase": "research", "total_cost": 0.50, "task_count": 5, "avg_cost": 0.10, "percent_of_total": 12.5},
                ...
            ],
            "total_cost": 4.00,
            "last_updated": "2025-12-19T..."
        }
        """
        try:
            if not self.db or not self.db.pool:
                return self._get_empty_breakdown_by_phase(period)

            # Determine time filter
            if period == "today":
                date_filter = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            elif period == "week":
                date_filter = datetime.now(timezone.utc) - timedelta(days=7)
            else:  # month
                date_filter = datetime.now(timezone.utc).replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )

            async with self.db.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT phase, 
                           COALESCE(SUM(cost_usd), 0) as total_cost,
                           COUNT(*) as task_count
                    FROM cost_logs
                    WHERE created_at >= $1 AND success = true
                    GROUP BY phase
                    ORDER BY total_cost DESC
                    """,
                    date_filter,
                )

                # Get total cost for percentage calculation
                total_cost_row = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(cost_usd), 0)
                    FROM cost_logs
                    WHERE created_at >= $1 AND success = true
                    """,
                    date_filter,
                )
                total_cost = float(total_cost_row or 0.0)

                phases = []
                for row in rows:
                    cost = float(row["total_cost"] or 0.0)
                    count = int(row["task_count"] or 0)
                    percent = (cost / total_cost * 100) if total_cost > 0 else 0

                    phases.append(
                        {
                            "phase": row["phase"],
                            "total_cost": round(cost, 4),
                            "task_count": count,
                            "avg_cost": round(cost / count, 4) if count > 0 else 0,
                            "percent_of_total": round(percent, 2),
                        }
                    )

                return {
                    "period": period,
                    "phases": phases,
                    "total_cost": round(total_cost, 4),
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            logger.error(f"Error getting cost breakdown by phase: {e}")
            return self._get_empty_breakdown_by_phase(period)

    async def get_breakdown_by_model(
        self, period: str = "week", user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get cost breakdown by AI model

        Returns: {
            "period": "week",
            "models": [
                {"model": "gpt-4", "total_cost": 2.00, "task_count": 10, "avg_cost_per_task": 0.20, ...},
                ...
            ],
            "total_cost": 4.00,
            "last_updated": "2025-12-19T..."
        }
        """
        try:
            if not self.db or not self.db.pool:
                return self._get_empty_breakdown_by_model(period)

            # Determine time filter
            if period == "today":
                date_filter = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            elif period == "week":
                date_filter = datetime.now(timezone.utc) - timedelta(days=7)
            else:  # month
                date_filter = datetime.now(timezone.utc).replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )

            async with self.db.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT model, provider,
                           COALESCE(SUM(cost_usd), 0) as total_cost,
                           COUNT(*) as task_count
                    FROM cost_logs
                    WHERE created_at >= $1 AND success = true
                    GROUP BY model, provider
                    ORDER BY total_cost DESC
                    """,
                    date_filter,
                )

                # Get total cost for percentage calculation
                total_cost_row = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(cost_usd), 0)
                    FROM cost_logs
                    WHERE created_at >= $1 AND success = true
                    """,
                    date_filter,
                )
                total_cost = float(total_cost_row or 0.0)

                models = []
                for row in rows:
                    cost = float(row["total_cost"] or 0.0)
                    count = int(row["task_count"] or 0)
                    percent = (cost / total_cost * 100) if total_cost > 0 else 0

                    models.append(
                        {
                            "model": row["model"],
                            "total_cost": round(cost, 4),
                            "task_count": count,
                            "avg_cost_per_task": round(cost / count, 4) if count > 0 else 0,
                            "provider": row["provider"],
                            "percent_of_total": round(percent, 2),
                        }
                    )

                return {
                    "period": period,
                    "models": models,
                    "total_cost": round(total_cost, 4),
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            logger.error(f"Error getting cost breakdown by model: {e}")
            return self._get_empty_breakdown_by_model(period)

    async def get_history(self, period: str = "week") -> Dict[str, Any]:
        """
        Get daily cost history and trends

        Returns: {
            "period": "week",
            "daily_data": [
                {"date": "2025-12-19", "cost": 0.50, "tasks": 5, "avg_cost": 0.10},
                ...
            ],
            "weekly_average": 0.50,
            "trend": "up",
            "last_updated": "2025-12-19T..."
        }
        """
        try:
            if not self.db or not self.db.pool:
                return self._get_empty_history(period)

            # Determine days to retrieve
            days = 7 if period == "week" else 30
            start_date = datetime.now(timezone.utc) - timedelta(days=days)

            async with self.db.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DATE(created_at AT TIME ZONE 'UTC') as date,
                           COALESCE(SUM(cost_usd), 0) as total_cost,
                           COUNT(DISTINCT task_id) as task_count
                    FROM cost_logs
                    WHERE created_at >= $1 AND success = true
                    GROUP BY DATE(created_at AT TIME ZONE 'UTC')
                    ORDER BY date ASC
                    """,
                    start_date,
                )

                daily_data = []
                total_cost = 0.0
                task_count = 0

                for row in rows:
                    cost = float(row["total_cost"] or 0.0)
                    tasks = int(row["task_count"] or 0)

                    daily_data.append(
                        {
                            "date": str(row["date"]),
                            "cost": round(cost, 4),
                            "tasks": tasks,
                            "avg_cost": round(cost / tasks, 4) if tasks > 0 else 0,
                        }
                    )

                    total_cost += cost
                    task_count += tasks

                # Calculate weekly average
                weeks = max(1, days // 7)
                weekly_avg = total_cost / weeks

                # Determine trend: compare first half vs second half
                midpoint = len(daily_data) // 2
                if midpoint > 0:
                    first_half_avg = sum(d["cost"] for d in daily_data[:midpoint]) / midpoint
                    second_half_avg = sum(d["cost"] for d in daily_data[midpoint:]) / (
                        len(daily_data) - midpoint
                    )

                    if second_half_avg > first_half_avg * 1.1:
                        trend = "up"
                    elif second_half_avg < first_half_avg * 0.9:
                        trend = "down"
                    else:
                        trend = "stable"
                else:
                    trend = "stable"

                return {
                    "period": period,
                    "daily_data": daily_data,
                    "weekly_average": round(weekly_avg, 4),
                    "trend": trend,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            logger.error(f"Error getting cost history: {e}")
            return self._get_empty_history(period)

    async def get_budget_status(
        self, monthly_budget: float = 150.0, user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get current budget status and alerts

        Returns: {
            "monthly_budget": 150.0,
            "amount_spent": 12.50,
            "amount_remaining": 137.50,
            "percent_used": 8.33,
            "days_in_month": 31,
            "days_remaining": 12,
            "daily_burn_rate": 0.42,
            "projected_final_cost": 45.00,
            "alerts": [...],
            "status": "healthy",
            "last_updated": "2025-12-19T..."
        }
        """
        try:
            if not self.db or not self.db.pool:
                return self._get_empty_budget_status(monthly_budget)

            # Get this month's costs
            month_start = datetime.now(timezone.utc).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )

            async with self.db.pool.acquire() as conn:
                cost_row = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(cost_usd), 0)
                    FROM cost_logs
                    WHERE created_at >= $1 AND success = true
                    """,
                    month_start,
                )
                amount_spent = float(cost_row or 0.0)

            # Calculate days
            now = datetime.now(timezone.utc)
            days_elapsed = (now - month_start).days + 1

            # Assume 30-day months for simplicity
            days_in_month = 30
            days_remaining = max(0, days_in_month - days_elapsed)

            # Calculate burn rate
            daily_burn_rate = amount_spent / days_elapsed if days_elapsed > 0 else 0

            # Project final cost
            projected_final_cost = daily_burn_rate * days_in_month

            # Calculate budget metrics
            amount_remaining = monthly_budget - amount_spent
            percent_used = (amount_spent / monthly_budget * 100) if monthly_budget > 0 else 0

            # Generate alerts
            alerts = []

            if percent_used >= 100:
                alerts.append(
                    {
                        "level": "critical",
                        "message": f"Budget exceeded! Spent ${amount_spent:.2f} of ${monthly_budget:.2f}",
                        "threshold_percent": 100,
                        "current_percent": percent_used,
                    }
                )
                status = "critical"
            elif percent_used >= 90:
                alerts.append(
                    {
                        "level": "warning",
                        "message": f"90% of monthly budget used (${amount_spent:.2f})",
                        "threshold_percent": 90,
                        "current_percent": percent_used,
                    }
                )
                status = "warning"
            elif percent_used >= 80:
                alerts.append(
                    {
                        "level": "warning",
                        "message": f"Approaching budget limit at {percent_used:.1f}%",
                        "threshold_percent": 80,
                        "current_percent": percent_used,
                    }
                )
                status = "warning"
            else:
                status = "healthy"

            # Add projection alert if trending high
            if projected_final_cost > monthly_budget * 1.1:
                alerts.append(
                    {
                        "level": "warning",
                        "message": f"Projected monthly cost ${projected_final_cost:.2f} exceeds budget",
                        "threshold_percent": 100,
                        "current_percent": (
                            (projected_final_cost / monthly_budget * 100)
                            if monthly_budget > 0
                            else 0
                        ),
                    }
                )

            return {
                "monthly_budget": monthly_budget,
                "amount_spent": round(amount_spent, 2),
                "amount_remaining": round(amount_remaining, 2),
                "percent_used": round(percent_used, 2),
                "days_in_month": days_in_month,
                "days_remaining": days_remaining,
                "daily_burn_rate": round(daily_burn_rate, 4),
                "projected_final_cost": round(projected_final_cost, 2),
                "alerts": alerts,
                "status": status,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.error(f"Error getting budget status: {e}")
            return self._get_empty_budget_status(monthly_budget)

    async def recalculate_all(self) -> Dict[str, Any]:
        """Force recalculation of all metrics"""
        logger.info("Recalculating all cost metrics...")
        return await self.get_summary()

    # ========================================================================
    # Helper methods for empty/default responses
    # ========================================================================

    def _get_empty_summary(self) -> Dict[str, Any]:
        return {
            "total_spent": 0.0,
            "today_cost": 0.0,
            "week_cost": 0.0,
            "month_cost": 0.0,
            "monthly_budget": self.monthly_budget,
            "budget_used_percent": 0.0,
            "projected_monthly": 0.0,
            "tasks_completed": 0,
            "avg_cost_per_task": 0.0,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def _get_empty_breakdown_by_phase(self, period: str) -> Dict[str, Any]:
        return {
            "period": period,
            "phases": [],
            "total_cost": 0.0,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def _get_empty_breakdown_by_model(self, period: str) -> Dict[str, Any]:
        return {
            "period": period,
            "models": [],
            "total_cost": 0.0,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def _get_empty_history(self, period: str) -> Dict[str, Any]:
        return {
            "period": period,
            "daily_data": [],
            "weekly_average": 0.0,
            "trend": "stable",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def _get_empty_budget_status(self, monthly_budget: float) -> Dict[str, Any]:
        return {
            "monthly_budget": monthly_budget,
            "amount_spent": 0.0,
            "amount_remaining": monthly_budget,
            "percent_used": 0.0,
            "days_in_month": 30,
            "days_remaining": 30,
            "daily_burn_rate": 0.0,
            "projected_final_cost": 0.0,
            "alerts": [],
            "status": "healthy",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
