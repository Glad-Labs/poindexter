"""
Cost Aggregation Service

Provides advanced cost analytics by querying the cost_logs PostgreSQL table:
- Aggregate costs by phase, model, provider
- Calculate daily/weekly/monthly trends
- Project monthly spend based on usage patterns
- Generate budget alerts

Built on top of DatabaseService's log_cost() and get_task_costs() methods.

Module-level helpers (no class instantiation required):
    :func:`get_spend_totals` — monthly + daily spend for operator dashboards,
    split into honest api / electricity axes via the ``cost_ledger`` seam.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from services import cost_ledger
from services.logger_config import get_logger

logger = get_logger(__name__)


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
        # Unset sentinel — NOT a hardcoded budget. The real monthly cap is read
        # per call from app_settings.monthly_spend_limit_usd (the single
        # cost_guard cap); 0.0 means "no budget configured" so get_summary /
        # get_budget_status never display or alert against a fake $150.
        self.monthly_budget = 0.0

    async def get_summary(self) -> dict[str, Any]:
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
                now = datetime.now(timezone.utc)
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                week_start = now - timedelta(days=7)
                month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

                # Consolidate 4 sequential queries into 1 using FILTER aggregates
                # (issue #492). A single sequential scan covers all three time windows
                # simultaneously, avoiding repeated full-table reads.
                summary_row = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(SUM(cost_usd) FILTER (WHERE created_at >= $1 AND success = true), 0)
                            AS today_cost,
                        COALESCE(SUM(cost_usd) FILTER (WHERE created_at >= $2 AND success = true), 0)
                            AS week_cost,
                        COALESCE(SUM(cost_usd) FILTER (WHERE created_at >= $3 AND success = true), 0)
                            AS month_cost,
                        COUNT(DISTINCT task_id) FILTER (WHERE created_at >= $3 AND success = true)
                            AS tasks_count
                    FROM cost_logs
                    WHERE created_at >= $2
                    """,
                    today_start,
                    week_start,
                    month_start,
                )

                today_cost = float(summary_row["today_cost"] or 0.0)
                week_cost = float(summary_row["week_cost"] or 0.0)
                month_cost = float(summary_row["month_cost"] or 0.0)
                tasks_count = int(summary_row["tasks_count"] or 0)

                # Honest budget for the display: the operator's real cap from
                # app_settings, not a hardcoded $150 (0.0 = unconfigured).
                monthly_budget = await self._read_monthly_budget_cap(conn)

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
                    (month_cost / monthly_budget * 100) if monthly_budget > 0 else 0
                )

                return {
                    "total_spent": round(month_cost, 2),
                    "today_cost": round(today_cost, 2),
                    "week_cost": round(week_cost, 2),
                    "month_cost": round(month_cost, 2),
                    "monthly_budget": monthly_budget,
                    "budget_used_percent": round(budget_used_percent, 2),
                    "projected_monthly": round(projected_monthly, 2),
                    "tasks_completed": tasks_count,
                    "avg_cost_per_task": round(avg_cost_per_task, 4),
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            logger.error("[_get_summary] Error getting cost summary: %s", e, exc_info=True)
            return self._get_empty_summary()

    async def get_breakdown_by_phase(
        self, period: str = "week"
    ) -> dict[str, Any]:
        """
        Get cost breakdown by pipeline phase

        Args:
            period: "today", "week", or "month"

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

                # Compute total cost from GROUP BY results (no redundant query)
                total_cost = sum(float(row["total_cost"] or 0.0) for row in rows)

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
            logger.error(
                "[_get_breakdown_by_phase] Error getting cost breakdown by phase: %s", e,
                exc_info=True,
            )
            return self._get_empty_breakdown_by_phase(period)

    async def get_breakdown_by_model(
        self, period: str = "week"
    ) -> dict[str, Any]:
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

                # Compute total cost from GROUP BY results (no redundant query)
                total_cost = sum(float(row["total_cost"] or 0.0) for row in rows)

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
            logger.error(
                "[_get_breakdown_by_model] Error getting cost breakdown by model: %s", e,
                exc_info=True,
            )
            return self._get_empty_breakdown_by_model(period)

    async def get_history(self, period: str = "week") -> dict[str, Any]:
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

                daily_data: list[dict[str, Any]] = []
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
            logger.error("[_get_history] Error getting cost history: %s", e, exc_info=True)
            return self._get_empty_history(period)

    async def get_budget_status(
        self, monthly_budget: float | None = None
    ) -> dict[str, Any]:
        """
        Get current budget status and alerts — ADVISORY / observability only.

        Enforcement lives solely in ``cost_guard`` (the dispatch-time gate);
        this is the dashboard/pre-flight *display*. ``monthly_budget=None``
        reads the operator's real cap from ``app_settings.monthly_spend_limit_usd``
        (no hardcoded $150); an explicit value still wins. ``amount_spent`` is
        the ledger's **api axis** (genuinely-paid cloud), since that cap is an
        API cap and the P1 write invariant keeps local rows at $0.

        Returns: {
            "monthly_budget": 10.0,
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
                return self._get_empty_budget_status(monthly_budget or 0.0)

            # Budget from app_settings (the single cost_guard cap), NOT a
            # hardcoded $150. An explicit caller-supplied budget still wins
            # (the metrics route + tests pass one); None reads the cap.
            if monthly_budget is None:
                monthly_budget = await self._read_monthly_budget_cap(self.db.pool)

            # month_start is still needed below for days_elapsed / the burn-rate
            # projection; only the spend SUM moved to the ledger.
            month_start = datetime.now(timezone.utc).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )

            # Spend from the one honest ledger seam. amount_spent is the api
            # axis (genuinely-paid cloud): monthly_spend_limit_usd is an API
            # cap, electricity has its own throttle ceiling (P3), and the P1
            # write invariant keeps local rows at $0 so the api axis is clean.
            month = await cost_ledger.get_spend(self.db.pool, window="month")
            amount_spent = month.api_usd

            # Calculate days
            now = datetime.now(timezone.utc)
            days_elapsed = (now - month_start).days + 1

            # Assume 30-day months for simplicity
            days_in_month = 30
            days_remaining = max(0, days_in_month - days_elapsed)

            # Warm-up window before the linear projection is trustworthy
            # enough to *alert* on. In the first day or two of a month,
            # month-to-date spend divided by 1-2 elapsed days wildly
            # over-extrapolates (a single batch job looks like a $300/mo
            # trend), which would fire a false-positive budget alert every
            # month-start. Must stay below the smallest days_elapsed any
            # legitimate projection alert needs to fire at.
            projection_warmup_days = 3

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

            # Add projection alert if trending high — but only once enough
            # of the month has elapsed for the extrapolation to be meaningful
            # (see projection_warmup_days above). This suppresses the
            # divide-by-few-days false positive at month-start.
            if days_elapsed >= projection_warmup_days and projected_final_cost > monthly_budget * 1.1:
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
            logger.error("[_get_budget_status] Error getting budget status: %s", e, exc_info=True)
            return self._get_empty_budget_status(monthly_budget or 0.0)

    async def recalculate_all(self) -> dict[str, Any]:
        """Force recalculation of all metrics"""
        logger.info("Recalculating all cost metrics...")
        return await self.get_summary()

    # ========================================================================
    # Helper methods for empty/default responses
    # ========================================================================

    async def _read_monthly_budget_cap(self, executor: Any) -> float:
        """Read the operator's monthly spend cap from app_settings.

        The single budget source of truth — ``monthly_spend_limit_usd`` (the
        cost_guard cap) — so get_summary / get_budget_status stop disagreeing on
        a hardcoded $150. Returns ``0.0`` when unset (the ``''`` sentinel) or
        unreadable, per ``feedback_no_silent_defaults`` (no fake default).
        ``executor`` is anything with an async ``fetchval`` — a pool or an
        already-acquired connection.
        """
        try:
            raw = await executor.fetchval(
                "SELECT value FROM app_settings WHERE key = 'monthly_spend_limit_usd'"
            )
        except Exception as e:
            # Advisory-only read — don't crash the dashboard/pre-flight — but
            # surface it (no silent swallow per feedback_no_silent_defaults): an
            # unreadable app_settings degrades the budget advisory to $0.
            logger.warning(
                "[cost] monthly_spend_limit_usd cap unreadable; budget advisory "
                "falls back to $0 (unconfigured): %s", e,
            )
            return 0.0
        try:
            return float(raw) if raw not in (None, "") else 0.0
        except (TypeError, ValueError):
            logger.warning(
                "[cost] monthly_spend_limit_usd has a non-numeric value %r; "
                "budget advisory falls back to $0", raw,
            )
            return 0.0

    def _get_empty_summary(self) -> dict[str, Any]:
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

    def _get_empty_breakdown_by_phase(self, period: str) -> dict[str, Any]:
        return {
            "period": period,
            "phases": [],
            "total_cost": 0.0,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def _get_empty_breakdown_by_model(self, period: str) -> dict[str, Any]:
        return {
            "period": period,
            "models": [],
            "total_cost": 0.0,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def _get_empty_history(self, period: str) -> dict[str, Any]:
        return {
            "period": period,
            "daily_data": [],
            "weekly_average": 0.0,
            "trend": "stable",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def _get_empty_budget_status(self, monthly_budget: float) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Module-level helper — no class instantiation required.
# ---------------------------------------------------------------------------

async def get_spend_totals(
    pool: Any, *, site_config: Any = None
) -> dict[str, Any]:
    """Return current month + day spend from the cost ledger (honest split).

    Backward-compatible superset: ``monthly_total_usd`` / ``daily_total_usd``
    stay (now the ledger's ``total_usd``) so the MCP ``get_budget`` tool keeps
    serializing the same keys, while the api/electricity split and
    ``electricity_source`` are added for the phone/dashboard. The single
    ``cost_ledger.get_spend`` seam relies on the P1 write invariant (local rows
    ``cost_usd=0``), so the api axis sums only genuinely-paid cloud spend.

    ``site_config`` (optional) supplies the electricity coverage/rate knobs to
    the ledger's estimate fallback; ``None`` uses the documented defaults.
    """
    month = await cost_ledger.get_spend(pool, window="month", site_config=site_config)
    day = await cost_ledger.get_spend(pool, window="day", site_config=site_config)
    return {
        "monthly_total_usd": month.total_usd,
        "daily_total_usd": day.total_usd,
        "monthly_api_usd": month.api_usd,
        "monthly_electricity_usd": month.electricity_usd,
        "daily_api_usd": day.api_usd,
        "daily_electricity_usd": day.electricity_usd,
        "electricity_source": month.electricity_source,
    }
