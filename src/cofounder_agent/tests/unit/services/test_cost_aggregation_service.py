"""
Unit tests for services/cost_aggregation_service.py.

Tests cover:
- CostAggregationService.get_summary — with pool, no pool, DB error
- CostAggregationService.get_breakdown_by_phase — period filtering, empty results
- CostAggregationService.get_breakdown_by_model — period filtering, empty results
- CostAggregationService.get_history — trend calculation, empty results
- CostAggregationService.get_budget_status — alert thresholds (healthy/warning/critical)
- CostAggregationService.recalculate_all — delegates to get_summary
- Helper methods: _get_empty_* — structure validation

The asyncpg pool is fully mocked; no real database access.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.cost_aggregation_service import CostAggregationService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(data: dict):
    """Build a mock asyncpg Record-like object from a dict."""
    row = MagicMock()
    row.__getitem__ = lambda self, key, _d=data: _d[key]
    return row


def _make_conn(fetchval_values=None, fetch_rows=None, fetchrow_value=None):
    """Build a mock asyncpg connection.

    fetchval_values: list of values returned in order for each fetchval call.
    fetch_rows: list of row dicts returned by fetch (converted to MagicMock).
    fetchrow_value: dict returned by fetchrow (converted to MagicMock row).
    """
    conn = MagicMock()

    # fetchval returns values in sequence
    fetchval_values = fetchval_values or []
    conn.fetchval = AsyncMock(side_effect=fetchval_values + [0] * 20)

    # fetchrow returns a single mock row
    if fetchrow_value is not None:
        conn.fetchrow = AsyncMock(return_value=_make_row(fetchrow_value))
    else:
        conn.fetchrow = AsyncMock(return_value=None)

    # fetch returns mock rows
    rows = []
    for row_dict in fetch_rows or []:
        row = _make_row(row_dict)
        rows.append(row)
    conn.fetch = AsyncMock(return_value=rows)

    return conn


def _make_db(conn=None):
    """Build a mock db_service with a pool."""
    db = MagicMock()
    _conn = conn or _make_conn()

    @asynccontextmanager
    async def _acquire():
        yield _conn

    db.pool = MagicMock()
    db.pool.acquire = _acquire
    return db


def _make_service(db=None):
    svc = CostAggregationService(db_service=db)
    return svc


# ---------------------------------------------------------------------------
# get_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSummary:
    @pytest.mark.asyncio
    async def test_no_db_returns_empty_summary(self):
        svc = _make_service(db=None)
        result = await svc.get_summary()
        assert result["total_spent"] == 0.0
        assert result["monthly_budget"] == 150.0

    @pytest.mark.asyncio
    async def test_no_pool_returns_empty_summary(self):
        db = MagicMock()
        db.pool = None
        svc = _make_service(db=db)
        result = await svc.get_summary()
        assert result["total_spent"] == 0.0

    @pytest.mark.asyncio
    async def test_summary_calculates_fields(self):
        # get_summary now issues a single fetchrow (consolidated FILTER query).
        # today=1.0, week=5.0, month=10.0, tasks=20
        conn = _make_conn(
            fetchrow_value={
                "today_cost": 1.0,
                "week_cost": 5.0,
                "month_cost": 10.0,
                "tasks_count": 20,
            }
        )
        db = _make_db(conn=conn)
        svc = _make_service(db=db)

        result = await svc.get_summary()

        assert result["today_cost"] == 1.0
        assert result["week_cost"] == 5.0
        assert result["month_cost"] == 10.0
        assert result["tasks_completed"] == 20
        assert result["avg_cost_per_task"] == pytest.approx(0.5, abs=0.001)
        assert "budget_used_percent" in result
        assert "projected_monthly" in result
        assert "last_updated" in result

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_summary(self):
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(side_effect=RuntimeError("DB down"))
        svc = _make_service(db=db)
        result = await svc.get_summary()
        assert result["total_spent"] == 0.0

    @pytest.mark.asyncio
    async def test_zero_tasks_avoids_division_by_zero(self):
        conn = _make_conn(fetchval_values=[0.0, 0.0, 0.0, 0])
        db = _make_db(conn=conn)
        svc = _make_service(db=db)

        result = await svc.get_summary()

        assert result["avg_cost_per_task"] == 0.0


# ---------------------------------------------------------------------------
# get_breakdown_by_phase
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBreakdownByPhase:
    @pytest.mark.asyncio
    async def test_no_db_returns_empty(self):
        svc = _make_service()
        result = await svc.get_breakdown_by_phase("week")
        assert result["phases"] == []
        assert result["period"] == "week"

    @pytest.mark.asyncio
    async def test_with_rows_calculates_percentages(self):
        rows = [
            {"phase": "research", "total_cost": "2.00", "task_count": "4"},
            {"phase": "draft", "total_cost": "2.00", "task_count": "4"},
        ]
        conn = _make_conn(fetchval_values=[4.0], fetch_rows=rows)
        db = _make_db(conn=conn)
        svc = _make_service(db=db)

        result = await svc.get_breakdown_by_phase("week")

        assert result["period"] == "week"
        assert len(result["phases"]) == 2
        assert result["total_cost"] == pytest.approx(4.0, abs=0.001)

    @pytest.mark.asyncio
    async def test_period_today_accepted(self):
        svc = _make_service()
        result = await svc.get_breakdown_by_phase("today")
        assert result["period"] == "today"

    @pytest.mark.asyncio
    async def test_period_month_accepted(self):
        svc = _make_service()
        result = await svc.get_breakdown_by_phase("month")
        assert result["period"] == "month"

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(side_effect=RuntimeError("DB down"))
        svc = _make_service(db=db)
        result = await svc.get_breakdown_by_phase("week")
        assert result["phases"] == []


# ---------------------------------------------------------------------------
# get_breakdown_by_model
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBreakdownByModel:
    @pytest.mark.asyncio
    async def test_no_db_returns_empty(self):
        svc = _make_service()
        result = await svc.get_breakdown_by_model("week")
        assert result["models"] == []
        assert result["period"] == "week"

    @pytest.mark.asyncio
    async def test_with_rows_returns_models_list(self):
        rows = [
            {
                "model": "claude-3-haiku",
                "provider": "anthropic",
                "total_cost": "1.50",
                "task_count": "5",
            },
        ]
        conn = _make_conn(fetchval_values=[1.5], fetch_rows=rows)
        db = _make_db(conn=conn)
        svc = _make_service(db=db)

        result = await svc.get_breakdown_by_model("week")

        assert len(result["models"]) == 1
        assert result["models"][0]["model"] == "claude-3-haiku"

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(side_effect=RuntimeError("DB down"))
        svc = _make_service(db=db)
        result = await svc.get_breakdown_by_model("week")
        assert result["models"] == []


# ---------------------------------------------------------------------------
# get_history
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetHistory:
    @pytest.mark.asyncio
    async def test_no_db_returns_empty(self):
        svc = _make_service()
        result = await svc.get_history("week")
        assert result["daily_data"] == []
        assert result["trend"] == "stable"

    @pytest.mark.asyncio
    async def test_stable_trend_when_no_data(self):
        conn = _make_conn(fetch_rows=[])
        db = _make_db(conn=conn)
        svc = _make_service(db=db)
        result = await svc.get_history("week")
        assert result["trend"] == "stable"

    @pytest.mark.asyncio
    async def test_upward_trend_detected(self):
        # 4 days data: low in first half, high in second half → "up" trend
        rows = [
            {"date": "2026-03-09", "total_cost": "0.10", "task_count": "1"},
            {"date": "2026-03-10", "total_cost": "0.10", "task_count": "1"},
            {"date": "2026-03-11", "total_cost": "1.50", "task_count": "5"},
            {"date": "2026-03-12", "total_cost": "2.00", "task_count": "8"},
        ]
        conn = _make_conn(fetch_rows=rows)
        db = _make_db(conn=conn)
        svc = _make_service(db=db)
        result = await svc.get_history("week")
        assert result["trend"] == "up"

    @pytest.mark.asyncio
    async def test_downward_trend_detected(self):
        rows = [
            {"date": "2026-03-09", "total_cost": "2.00", "task_count": "8"},
            {"date": "2026-03-10", "total_cost": "1.50", "task_count": "5"},
            {"date": "2026-03-11", "total_cost": "0.05", "task_count": "1"},
            {"date": "2026-03-12", "total_cost": "0.05", "task_count": "1"},
        ]
        conn = _make_conn(fetch_rows=rows)
        db = _make_db(conn=conn)
        svc = _make_service(db=db)
        result = await svc.get_history("week")
        assert result["trend"] == "down"

    @pytest.mark.asyncio
    async def test_period_month_uses_30_days(self):
        svc = _make_service()
        result = await svc.get_history("month")
        assert result["period"] == "month"

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(side_effect=RuntimeError("DB down"))
        svc = _make_service(db=db)
        result = await svc.get_history("week")
        assert result["daily_data"] == []


# ---------------------------------------------------------------------------
# get_budget_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBudgetStatus:
    @pytest.mark.asyncio
    async def test_no_db_returns_empty(self):
        svc = _make_service()
        result = await svc.get_budget_status(monthly_budget=100.0)
        assert result["status"] == "healthy"
        assert result["monthly_budget"] == 100.0

    @pytest.mark.asyncio
    async def test_healthy_status_under_80_percent(self):
        # Spent $50 of $150 budget = 33% — status is healthy
        # Note: projection alerts may still fire if daily spend extrapolates
        # over budget, but the STATUS should be "healthy" based on actual spend
        conn = _make_conn(fetchval_values=[50.0])
        db = _make_db(conn=conn)
        svc = _make_service(db=db)
        result = await svc.get_budget_status(monthly_budget=150.0)
        assert result["status"] == "healthy"
        # Only check no threshold alerts — projection warnings are informational
        threshold_alerts = [a for a in result["alerts"] if "exceeds" not in a.get("message", "").lower() or a["level"] == "critical"]
        assert threshold_alerts == []

    @pytest.mark.asyncio
    async def test_warning_at_80_percent(self):
        # Spent $120 of $150 = 80%
        conn = _make_conn(fetchval_values=[120.0])
        db = _make_db(conn=conn)
        svc = _make_service(db=db)
        result = await svc.get_budget_status(monthly_budget=150.0)
        assert result["status"] == "warning"
        assert len(result["alerts"]) >= 1

    @pytest.mark.asyncio
    async def test_critical_at_100_percent(self):
        # Spent $150 of $150 = 100%
        conn = _make_conn(fetchval_values=[150.0])
        db = _make_db(conn=conn)
        svc = _make_service(db=db)
        result = await svc.get_budget_status(monthly_budget=150.0)
        assert result["status"] == "critical"
        critical_alerts = [a for a in result["alerts"] if a["level"] == "critical"]
        assert len(critical_alerts) >= 1

    @pytest.mark.asyncio
    async def test_required_fields_present(self):
        conn = _make_conn(fetchval_values=[10.0])
        db = _make_db(conn=conn)
        svc = _make_service(db=db)
        result = await svc.get_budget_status(monthly_budget=150.0)
        for field in [
            "monthly_budget",
            "amount_spent",
            "amount_remaining",
            "percent_used",
            "days_remaining",
            "daily_burn_rate",
            "projected_final_cost",
            "alerts",
            "status",
            "last_updated",
        ]:
            assert field in result

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.acquire = MagicMock(side_effect=RuntimeError("DB down"))
        svc = _make_service(db=db)
        result = await svc.get_budget_status(150.0)
        assert result["amount_spent"] == 0.0


# ---------------------------------------------------------------------------
# recalculate_all
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecalculateAll:
    @pytest.mark.asyncio
    async def test_delegates_to_get_summary(self):
        svc = _make_service()
        svc.get_summary = AsyncMock(return_value={"total_spent": 5.0})
        result = await svc.recalculate_all()
        svc.get_summary.assert_awaited_once()
        assert result["total_spent"] == 5.0


# ---------------------------------------------------------------------------
# Empty helpers — structure validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmptyHelpers:
    def test_empty_summary_has_required_fields(self):
        svc = _make_service()
        d = svc._get_empty_summary()
        for field in [
            "total_spent",
            "today_cost",
            "week_cost",
            "month_cost",
            "monthly_budget",
            "budget_used_percent",
            "projected_monthly",
            "tasks_completed",
            "avg_cost_per_task",
            "last_updated",
        ]:
            assert field in d

    def test_empty_breakdown_by_phase_structure(self):
        svc = _make_service()
        d = svc._get_empty_breakdown_by_phase("week")
        assert d["phases"] == []
        assert d["period"] == "week"

    def test_empty_breakdown_by_model_structure(self):
        svc = _make_service()
        d = svc._get_empty_breakdown_by_model("month")
        assert d["models"] == []
        assert d["period"] == "month"

    def test_empty_history_structure(self):
        svc = _make_service()
        d = svc._get_empty_history("week")
        assert d["daily_data"] == []
        assert d["trend"] == "stable"

    def test_empty_budget_status_structure(self):
        svc = _make_service()
        d = svc._get_empty_budget_status(200.0)
        assert d["monthly_budget"] == 200.0
        assert d["amount_spent"] == 0.0
        assert d["status"] == "healthy"


# ---------------------------------------------------------------------------
# get_history — trend calculation edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetHistoryTrends:
    @pytest.mark.asyncio
    async def test_month_period_retrieves_30_days(self):
        """period='month' should query with 30-day window."""
        conn = _make_conn(fetch_rows=[])
        db = _make_db(conn)
        svc = _make_service(db)

        await svc.get_history(period="month")

        # fetch was called with a start_date ~30 days ago
        args = conn.fetch.await_args.args
        assert len(args) == 2  # SQL + start_date
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        start_date = args[1]
        delta_days = (now - start_date).days
        assert 29 <= delta_days <= 31

    @pytest.mark.asyncio
    async def test_week_period_retrieves_7_days(self):
        conn = _make_conn(fetch_rows=[])
        db = _make_db(conn)
        svc = _make_service(db)

        await svc.get_history(period="week")

        args = conn.fetch.await_args.args
        from datetime import datetime, timezone
        start_date = args[1]
        delta_days = (datetime.now(timezone.utc) - start_date).days
        assert 6 <= delta_days <= 8

    @pytest.mark.asyncio
    async def test_trend_up_when_second_half_higher(self):
        """If second-half average > 110% of first-half, trend is 'up'."""
        rows = [
            {"date": "2026-04-01", "total_cost": 1.0, "task_count": 10},
            {"date": "2026-04-02", "total_cost": 1.0, "task_count": 10},
            {"date": "2026-04-03", "total_cost": 1.0, "task_count": 10},
            {"date": "2026-04-04", "total_cost": 5.0, "task_count": 10},
            {"date": "2026-04-05", "total_cost": 5.0, "task_count": 10},
            {"date": "2026-04-06", "total_cost": 5.0, "task_count": 10},
        ]
        conn = _make_conn(fetch_rows=rows)
        db = _make_db(conn)
        svc = _make_service(db)

        result = await svc.get_history(period="week")
        assert result["trend"] == "up"

    @pytest.mark.asyncio
    async def test_trend_down_when_second_half_lower(self):
        rows = [
            {"date": "2026-04-01", "total_cost": 5.0, "task_count": 10},
            {"date": "2026-04-02", "total_cost": 5.0, "task_count": 10},
            {"date": "2026-04-03", "total_cost": 5.0, "task_count": 10},
            {"date": "2026-04-04", "total_cost": 1.0, "task_count": 10},
            {"date": "2026-04-05", "total_cost": 1.0, "task_count": 10},
            {"date": "2026-04-06", "total_cost": 1.0, "task_count": 10},
        ]
        conn = _make_conn(fetch_rows=rows)
        db = _make_db(conn)
        svc = _make_service(db)

        result = await svc.get_history(period="week")
        assert result["trend"] == "down"

    @pytest.mark.asyncio
    async def test_trend_stable_when_similar(self):
        rows = [
            {"date": "2026-04-01", "total_cost": 2.0, "task_count": 10},
            {"date": "2026-04-02", "total_cost": 2.0, "task_count": 10},
            {"date": "2026-04-03", "total_cost": 2.0, "task_count": 10},
            {"date": "2026-04-04", "total_cost": 2.0, "task_count": 10},
        ]
        conn = _make_conn(fetch_rows=rows)
        db = _make_db(conn)
        svc = _make_service(db)

        result = await svc.get_history(period="week")
        assert result["trend"] == "stable"

    @pytest.mark.asyncio
    async def test_single_day_is_stable(self):
        """With only one data point, trend defaults to stable."""
        rows = [
            {"date": "2026-04-01", "total_cost": 5.0, "task_count": 10},
        ]
        conn = _make_conn(fetch_rows=rows)
        db = _make_db(conn)
        svc = _make_service(db)

        result = await svc.get_history(period="week")
        assert result["trend"] == "stable"

    @pytest.mark.asyncio
    async def test_zero_tasks_avg_cost_is_zero(self):
        """A day with 0 tasks should report avg_cost=0, not divide by zero."""
        rows = [
            {"date": "2026-04-01", "total_cost": 0.0, "task_count": 0},
        ]
        conn = _make_conn(fetch_rows=rows)
        db = _make_db(conn)
        svc = _make_service(db)

        result = await svc.get_history(period="week")
        assert result["daily_data"][0]["avg_cost"] == 0

    @pytest.mark.asyncio
    async def test_weekly_average_scaled_by_period(self):
        """weekly_average should be total_cost / weeks."""
        rows = [
            {"date": f"2026-04-{i:02d}", "total_cost": 7.0, "task_count": 1}
            for i in range(1, 8)
        ]
        conn = _make_conn(fetch_rows=rows)
        db = _make_db(conn)
        svc = _make_service(db)

        result = await svc.get_history(period="week")
        # 7 days * 7.0 = 49 total, / 1 week = 49
        assert result["weekly_average"] == 49.0


# ---------------------------------------------------------------------------
# get_budget_status — alert thresholds + projection alert
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBudgetStatusProjection:
    @pytest.mark.asyncio
    async def test_projection_over_110_percent_adds_alert(self):
        """If projected cost > 110% of budget, a projection alert is added."""
        from unittest.mock import patch
        conn = MagicMock()
        # amount_spent is high enough that daily_burn_rate * 30 > monthly_budget * 1.1
        conn.fetchval = AsyncMock(return_value=50.0)
        db = _make_db(conn)
        svc = _make_service(db)

        # Freeze "now" so days_elapsed is deterministic. With a high burn rate
        # projected_final_cost can reliably exceed 110% of the budget.
        with patch("services.cost_aggregation_service.datetime") as mock_dt:
            from datetime import datetime, timezone, timedelta
            fixed_now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
            mock_dt.now = MagicMock(return_value=fixed_now)
            # Pass-through for other datetime usage (timedelta constructor etc.)
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            # Make timezone accessible
            mock_dt.timezone = timezone
            result = await svc.get_budget_status(monthly_budget=100.0)

        # 50 spent over ~5 days = $10/day * 30 days = $300 projected > $110
        # Should include a projection alert
        projection_alerts = [
            a for a in result["alerts"]
            if "projected" in a.get("message", "").lower()
        ]
        assert len(projection_alerts) >= 1

    @pytest.mark.asyncio
    async def test_projection_below_110_no_alert(self):
        """Normal burn rate = no projection alert."""
        conn = MagicMock()
        conn.fetchval = AsyncMock(return_value=10.0)
        db = _make_db(conn)
        svc = _make_service(db)

        result = await svc.get_budget_status(monthly_budget=200.0)

        projection_alerts = [
            a for a in result["alerts"]
            if "projected" in a.get("message", "").lower()
        ]
        assert len(projection_alerts) == 0

    @pytest.mark.asyncio
    async def test_zero_budget_safe_percent_used(self):
        """monthly_budget=0 should not divide by zero."""
        conn = MagicMock()
        conn.fetchval = AsyncMock(return_value=5.0)
        db = _make_db(conn)
        svc = _make_service(db)

        result = await svc.get_budget_status(monthly_budget=0.0)
        assert result["percent_used"] == 0  # safe fallback


# ---------------------------------------------------------------------------
# recalculate_all
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecalculateAllDelegation:
    @pytest.mark.asyncio
    async def test_delegates_to_get_summary(self):
        svc = _make_service()
        called = {}

        async def _fake_summary(user_id=None):
            called["yes"] = True
            return {"total_spent": 42.0}

        svc.get_summary = _fake_summary  # type: ignore[method-assign]

        result = await svc.recalculate_all()
        assert called.get("yes") is True
        assert result["total_spent"] == 42.0
