"""
Unit tests for services/financial_service.py (FinancialService).

Tests cover:
- FinancialService initialization (default and with deps)
- analyze_content_cost — agent available (mocked), ImportError path (swallowed)
- calculate_roi — positive cost, zero cost (no divide-by-zero), default params
- forecast_budget — 1 month, 12 months, growth rate compound, zero growth
- get_service_metadata — structure validation

No real LLM calls, no DB.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.financial_service import FinancialService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service(**kwargs) -> FinancialService:
    return FinancialService(**kwargs)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestFinancialServiceInit:
    def test_default_init(self):
        svc = FinancialService()
        assert svc.database_service is None
        assert svc.model_router is None

    def test_init_with_deps(self):
        db = MagicMock()
        router = MagicMock()
        svc = FinancialService(database_service=db, model_router=router)
        assert svc.database_service is db
        assert svc.model_router is router


# ---------------------------------------------------------------------------
# analyze_content_cost
# ---------------------------------------------------------------------------


class TestAnalyzeContentCost:
    @pytest.mark.asyncio
    async def test_success_via_agent(self):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value={"cost": 0.05})
        mock_agent_class = MagicMock(return_value=mock_agent)

        with patch.dict(
            "sys.modules",
            {
                "agents.financial_agent.agents.financial_agent": MagicMock(
                    FinancialAgent=mock_agent_class
                )
            },
        ):
            svc = _service()
            result = await svc.analyze_content_cost(
                content_id="c1",
                topic="AI trends",
                word_count=1500,
            )

        assert result["phase"] == "financial_analysis"
        assert result["content_id"] == "c1"
        assert result["source"] == "financial_agent"
        assert "analysis" in result
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_import_error_returns_error_dict(self):
        """When the financial_agent module is missing, returns error dict without raising."""
        svc = _service()

        with patch("builtins.__import__", side_effect=ImportError("no module")):
            result = await svc.analyze_content_cost("c1", "topic")

        assert "error" in result
        assert result["content_id"] == "c1"

    @pytest.mark.asyncio
    async def test_model_selections_passed_as_empty_dict_when_none(self):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value={})
        mock_agent_class = MagicMock(return_value=mock_agent)

        with patch.dict(
            "sys.modules",
            {
                "agents.financial_agent.agents.financial_agent": MagicMock(
                    FinancialAgent=mock_agent_class
                )
            },
        ):
            svc = _service()
            await svc.analyze_content_cost("c1", "topic", model_selections=None)

        # Verify run was called with model_selections={}
        call_kwargs = mock_agent.run.call_args[1]
        assert call_kwargs["model_selections"] == {}


# ---------------------------------------------------------------------------
# calculate_roi
# ---------------------------------------------------------------------------


class TestCalculateRoi:
    @pytest.mark.asyncio
    async def test_positive_roi(self):
        svc = _service()
        result = await svc.calculate_roi(
            content_id="c1",
            generation_cost=5.0,
            estimated_reach=1000,
            conversion_rate=0.02,
            revenue_per_conversion=10.0,
        )
        # expected_revenue = 1000 * 0.02 * 10 = 200
        # net_profit = 200 - 5 = 195
        # roi = (195 / 5) * 100 = 3900%
        assert result["expected_revenue"] == pytest.approx(200.0)
        assert result["net_profit"] == pytest.approx(195.0)
        assert result["roi_percentage"] == pytest.approx(3900.0)

    @pytest.mark.asyncio
    async def test_zero_cost_returns_zero_roi(self):
        svc = _service()
        result = await svc.calculate_roi(
            content_id="c1",
            generation_cost=0.0,
        )
        assert result["roi_percentage"] == 0

    @pytest.mark.asyncio
    async def test_zero_revenue_returns_infinite_payback(self):
        """If expected_revenue is zero, payback_period_days should be infinity."""
        svc = _service()
        result = await svc.calculate_roi(
            content_id="c1",
            generation_cost=10.0,
            estimated_reach=0,  # → zero revenue
            conversion_rate=0.02,
            revenue_per_conversion=10.0,
        )
        assert result["payback_period_days"] == float("inf")

    @pytest.mark.asyncio
    async def test_result_structure(self):
        svc = _service()
        result = await svc.calculate_roi("c1", 5.0)
        for key in (
            "content_id",
            "generation_cost",
            "estimated_reach",
            "expected_conversions",
            "expected_revenue",
            "net_profit",
            "roi_percentage",
            "payback_period_days",
        ):
            assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# forecast_budget
# ---------------------------------------------------------------------------


class TestForecastBudget:
    @pytest.mark.asyncio
    async def test_one_month_forecast(self):
        svc = _service()
        result = await svc.forecast_budget(
            monthly_content_target=10,
            avg_cost_per_piece=0.50,
            growth_rate=0.0,
            months_ahead=1,
        )
        assert result["total_projected_cost"] == pytest.approx(5.0)
        assert "month_1" in result["monthly_forecasts"]
        assert result["monthly_forecasts"]["month_1"]["projected_pieces"] == 10

    @pytest.mark.asyncio
    async def test_growth_rate_compounds_correctly(self):
        svc = _service()
        result = await svc.forecast_budget(
            monthly_content_target=10,
            avg_cost_per_piece=1.0,
            growth_rate=0.1,
            months_ahead=2,
        )
        # month 1: 10 * 1.0 = 10
        # month 2: 10 * 1.1 * 1.0 = 11
        m1 = result["monthly_forecasts"]["month_1"]["monthly_cost"]
        m2 = result["monthly_forecasts"]["month_2"]["monthly_cost"]
        assert m1 == pytest.approx(10.0)
        assert m2 == pytest.approx(11.0)
        assert result["total_projected_cost"] == pytest.approx(21.0)

    @pytest.mark.asyncio
    async def test_twelve_months_forecast_structure(self):
        svc = _service()
        result = await svc.forecast_budget(10, 1.0, months_ahead=12)
        assert len(result["monthly_forecasts"]) == 12
        assert result["forecast_months"] == 12

    @pytest.mark.asyncio
    async def test_zero_content_target(self):
        svc = _service()
        result = await svc.forecast_budget(0, 1.0, months_ahead=3)
        assert result["total_projected_cost"] == 0.0


# ---------------------------------------------------------------------------
# get_service_metadata
# ---------------------------------------------------------------------------


class TestGetServiceMetadata:
    def test_metadata_structure(self):
        svc = _service()
        meta = svc.get_service_metadata()
        assert meta["name"] == "financial_service"
        assert "capabilities" in meta
        assert isinstance(meta["capabilities"], list)
        assert len(meta["capabilities"]) > 0
