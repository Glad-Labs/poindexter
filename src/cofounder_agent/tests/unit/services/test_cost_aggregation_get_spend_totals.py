"""Unit tests for ``cost_aggregation_service.get_spend_totals``."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.cost_aggregation_service import get_spend_totals


def _pool(monthly=12.5, daily=1.0):
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=[monthly, daily])
    return pool


class TestGetSpendTotals:
    async def test_returns_monthly_and_daily(self):
        result = await get_spend_totals(_pool(12.5, 1.0))
        assert result["monthly_total_usd"] == pytest.approx(12.5)
        assert result["daily_total_usd"] == pytest.approx(1.0)

    async def test_none_coerced_to_zero(self):
        result = await get_spend_totals(_pool(None, None))
        assert result["monthly_total_usd"] == 0.0
        assert result["daily_total_usd"] == 0.0

    async def test_calls_fetchval_twice(self):
        pool = _pool()
        await get_spend_totals(pool)
        assert pool.fetchval.await_count == 2

    async def test_monthly_query_uses_date_trunc_month(self):
        pool = _pool()
        await get_spend_totals(pool)
        monthly_sql = pool.fetchval.await_args_list[0].args[0]
        assert "month" in monthly_sql.lower()
        assert "cost_logs" in monthly_sql

    async def test_daily_query_uses_date_trunc_day(self):
        pool = _pool()
        await get_spend_totals(pool)
        daily_sql = pool.fetchval.await_args_list[1].args[0]
        assert "day" in daily_sql.lower()
        assert "cost_logs" in daily_sql
