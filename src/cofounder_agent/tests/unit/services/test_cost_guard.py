"""
Unit tests for services/cost_guard.py

Tests CostGuard budget checking, daily/monthly spend queries,
cost estimation, and graceful handling when DB is unavailable.
All database calls are mocked — no real asyncpg pool required.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.cost_guard import CostGuard, TOKEN_COSTS_PER_1K


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_mock_pool(daily_total: float = 0.0, monthly_total: float = 0.0) -> AsyncMock:
    """Return an AsyncMock that behaves like an asyncpg pool."""
    pool = AsyncMock()

    async def mock_fetchrow(query, *args):
        if "date_trunc('day'" in query:
            return {"total": daily_total}
        if "date_trunc('month'" in query:
            return {"total": monthly_total}
        return {"total": 0.0}

    pool.fetchrow = AsyncMock(side_effect=mock_fetchrow)
    return pool


def make_mock_settings(**overrides) -> AsyncMock:
    """Return an AsyncMock that behaves like a settings service."""
    defaults = {
        "daily_spend_limit": "2.0",
        "monthly_spend_limit": "10.0",
        "cost_alert_threshold_pct": "80",
    }
    defaults.update(overrides)
    settings = AsyncMock()
    settings.get = AsyncMock(side_effect=lambda key: defaults.get(key))
    return settings


@pytest.fixture
def guard():
    """CostGuard with default mock pool (zero spend)."""
    return CostGuard(pool=make_mock_pool())


@pytest.fixture
def guard_no_pool():
    """CostGuard with no pool (DB unavailable)."""
    return CostGuard(pool=None)


# ---------------------------------------------------------------------------
# check_budget — within limits
# ---------------------------------------------------------------------------


class TestCheckBudgetAllowed:
    async def test_within_budget_returns_true(self, guard):
        allowed, reason = await guard.check_budget("anthropic")
        assert allowed is True
        assert reason == "within_budget"

    async def test_ollama_always_allowed(self, guard):
        allowed, reason = await guard.check_budget("ollama")
        assert allowed is True
        assert reason == "local"

    async def test_ollama_prefix_always_allowed(self, guard):
        allowed, reason = await guard.check_budget("ollama/gemma3:27b")
        assert allowed is True
        assert reason == "local"

    async def test_google_still_budget_checked(self, guard):
        """Google (Gemini) should go through budget checks, not bypass."""
        allowed, reason = await guard.check_budget("google")
        assert allowed is True
        assert reason == "within_budget"


# ---------------------------------------------------------------------------
# check_budget — exceeded
# ---------------------------------------------------------------------------


class TestCheckBudgetBlocked:
    async def test_daily_limit_exceeded_returns_false(self):
        pool = make_mock_pool(daily_total=2.50, monthly_total=2.50)
        guard = CostGuard(pool=pool)
        allowed, reason = await guard.check_budget("anthropic")
        assert allowed is False
        assert "Daily spend limit reached" in reason

    async def test_monthly_limit_exceeded_returns_false(self):
        pool = make_mock_pool(daily_total=0.50, monthly_total=10.50)
        guard = CostGuard(pool=pool)
        allowed, reason = await guard.check_budget("anthropic")
        assert allowed is False
        assert "Monthly spend limit reached" in reason

    async def test_monthly_checked_before_daily(self):
        """Monthly limit should block even when daily is OK."""
        pool = make_mock_pool(daily_total=0.50, monthly_total=12.00)
        guard = CostGuard(pool=pool)
        allowed, reason = await guard.check_budget("openai")
        assert allowed is False
        assert "Monthly" in reason

    async def test_exact_daily_limit_is_blocked(self):
        """Spend exactly at daily limit should be blocked (>= comparison)."""
        pool = make_mock_pool(daily_total=2.00, monthly_total=2.00)
        guard = CostGuard(pool=pool)
        allowed, reason = await guard.check_budget("anthropic")
        assert allowed is False

    async def test_exact_monthly_limit_is_blocked(self):
        pool = make_mock_pool(daily_total=0.50, monthly_total=10.00)
        guard = CostGuard(pool=pool)
        allowed, reason = await guard.check_budget("anthropic")
        assert allowed is False


# ---------------------------------------------------------------------------
# Daily / monthly spend calculation
# ---------------------------------------------------------------------------


class TestSpendQueries:
    async def test_get_daily_spend(self):
        pool = make_mock_pool(daily_total=1.23)
        guard = CostGuard(pool=pool)
        assert await guard.get_daily_spend() == 1.23

    async def test_get_monthly_spend(self):
        pool = make_mock_pool(monthly_total=7.89)
        guard = CostGuard(pool=pool)
        assert await guard.get_monthly_spend() == 7.89

    async def test_daily_spend_no_pool(self, guard_no_pool):
        assert await guard_no_pool.get_daily_spend() == 0.0

    async def test_monthly_spend_no_pool(self, guard_no_pool):
        assert await guard_no_pool.get_monthly_spend() == 0.0


# ---------------------------------------------------------------------------
# Graceful handling when DB is unavailable
# ---------------------------------------------------------------------------


class TestDBUnavailable:
    async def test_check_budget_with_no_pool_allows(self, guard_no_pool):
        """With no DB pool, spend is 0 so calls should be allowed."""
        allowed, reason = await guard_no_pool.check_budget("anthropic")
        assert allowed is True
        assert reason == "within_budget"

    async def test_db_exception_returns_zero_daily(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=Exception("connection refused"))
        guard = CostGuard(pool=pool)
        assert await guard.get_daily_spend() == 0.0

    async def test_db_exception_returns_zero_monthly(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=Exception("connection refused"))
        guard = CostGuard(pool=pool)
        assert await guard.get_monthly_spend() == 0.0

    async def test_db_exception_allows_budget(self):
        """When DB fails, spend reads as 0 so budget check passes."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=Exception("timeout"))
        guard = CostGuard(pool=pool)
        allowed, reason = await guard.check_budget("anthropic")
        assert allowed is True


# ---------------------------------------------------------------------------
# Settings service integration
# ---------------------------------------------------------------------------


class TestCustomLimits:
    async def test_custom_daily_limit(self):
        pool = make_mock_pool(daily_total=4.50, monthly_total=4.50)
        settings = make_mock_settings(daily_spend_limit="5.0")
        guard = CostGuard(pool=pool, settings_service=settings)
        allowed, _ = await guard.check_budget("anthropic")
        assert allowed is True

    async def test_custom_monthly_limit_exceeded(self):
        pool = make_mock_pool(daily_total=1.00, monthly_total=6.00)
        settings = make_mock_settings(monthly_spend_limit="5.0")
        guard = CostGuard(pool=pool, settings_service=settings)
        allowed, reason = await guard.check_budget("anthropic")
        assert allowed is False
        assert "Monthly" in reason


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


class TestEstimateCost:
    async def test_estimate_anthropic_haiku(self):
        guard = CostGuard()
        cost = await guard.estimate_cost("anthropic", input_tokens=1000, output_tokens=1000)
        expected = TOKEN_COSTS_PER_1K["anthropic"]["input"] + TOKEN_COSTS_PER_1K["anthropic"]["output"]
        assert abs(cost - expected) < 1e-8

    async def test_estimate_ollama_electricity_cost(self):
        guard = CostGuard()
        cost = await guard.estimate_cost("ollama", input_tokens=10000, output_tokens=10000)
        # Ollama costs electricity: ~$0.000256/1K tokens
        assert cost > 0  # Not free!
        assert cost < 0.01  # But very cheap

    async def test_estimate_unknown_provider_falls_back(self):
        guard = CostGuard()
        cost = await guard.estimate_cost("unknown_provider", input_tokens=1000, output_tokens=1000)
        # Falls back to anthropic rates
        assert cost > 0


# ---------------------------------------------------------------------------
# Budget status
# ---------------------------------------------------------------------------


class TestBudgetStatus:
    async def test_budget_status_shape(self, guard):
        status = await guard.get_budget_status()
        assert "daily" in status
        assert "monthly" in status
        assert "spent" in status["daily"]
        assert "limit" in status["daily"]
        assert "remaining" in status["daily"]
        assert "pct_used" in status["daily"]
