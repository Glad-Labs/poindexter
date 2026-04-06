"""
Unit tests for services/financial_service.py.

Tests cover:
- FinancialService.__init__ — initialization with/without dependencies
- FinancialService.analyze_content_cost — success, error handling
- FinancialService.calculate_roi — math correctness, edge cases, errors
- FinancialService.forecast_budget — projection accuracy, growth, edge cases
- FinancialService.get_service_metadata — structure validation

All external dependencies (FinancialAgent, database, model router) are mocked.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.financial_service import FinancialService


# ---------------------------------------------------------------------------
# Patch helper for the lazy-imported FinancialAgent
# ---------------------------------------------------------------------------

def _patch_financial_agent(mock_agent_cls):
    """Context manager that injects a fake module so the local import succeeds.

    The service does:
        from agents.financial_agent.agents.financial_agent import FinancialAgent

    We create the intermediate module chain in sys.modules so the import
    resolves to our mock.
    """
    fake_leaf = MagicMock()
    fake_leaf.FinancialAgent = mock_agent_cls

    modules = {
        "agents.financial_agent.agents": MagicMock(),
        "agents.financial_agent.agents.financial_agent": fake_leaf,
    }
    return patch.dict(sys.modules, modules)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(database_service=None, model_router=None):
    return FinancialService(
        database_service=database_service,
        model_router=model_router,
    )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInit:
    def test_init_with_defaults(self):
        svc = _make_service()
        assert svc.database_service is None
        assert svc.model_router is None

    def test_init_with_dependencies(self):
        db = MagicMock()
        router = MagicMock()
        svc = _make_service(database_service=db, model_router=router)
        assert svc.database_service is db
        assert svc.model_router is router


# ---------------------------------------------------------------------------
# analyze_content_cost
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnalyzeContentCost:
    @pytest.mark.asyncio
    async def test_success_returns_analysis(self):
        svc = _make_service()
        mock_analysis = {"total_cost": 0.05, "breakdown": {"research": 0.02}}

        mock_cls = MagicMock()
        agent_instance = AsyncMock()
        agent_instance.run = AsyncMock(return_value=mock_analysis)
        mock_cls.return_value = agent_instance

        with _patch_financial_agent(mock_cls):
            result = await svc.analyze_content_cost(
                content_id="test-123",
                topic="AI hardware",
                model_selections={"research": "gpt-4"},
                word_count=2000,
            )

        assert result["phase"] == "financial_analysis"
        assert result["content_id"] == "test-123"
        assert result["analysis"] == mock_analysis
        assert result["source"] == "financial_agent"
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_default_model_selections(self):
        """model_selections defaults to empty dict when None."""
        svc = _make_service()

        mock_cls = MagicMock()
        agent_instance = AsyncMock()
        agent_instance.run = AsyncMock(return_value={})
        mock_cls.return_value = agent_instance

        with _patch_financial_agent(mock_cls):
            await svc.analyze_content_cost(
                content_id="test-456",
                topic="GPUs",
            )

            agent_instance.run.assert_called_once_with(
                content_id="test-456",
                topic="GPUs",
                word_count=1500,
                model_selections={},
            )

    @pytest.mark.asyncio
    async def test_agent_import_error_returns_error_dict(self):
        svc = _make_service()

        # Force the import to fail by injecting None into sys.modules
        modules = {
            "agents.financial_agent.agents": None,
            "agents.financial_agent.agents.financial_agent": None,
        }
        with patch.dict(sys.modules, modules):
            result = await svc.analyze_content_cost(
                content_id="fail-1",
                topic="broken",
            )

        assert result["phase"] == "financial_analysis"
        assert "error" in result
        assert result["content_id"] == "fail-1"

    @pytest.mark.asyncio
    async def test_agent_run_raises(self):
        svc = _make_service()

        mock_cls = MagicMock()
        agent_instance = AsyncMock()
        agent_instance.run = AsyncMock(
            side_effect=RuntimeError("API timeout")
        )
        mock_cls.return_value = agent_instance

        with _patch_financial_agent(mock_cls):
            result = await svc.analyze_content_cost(
                content_id="timeout-1",
                topic="test",
            )

        assert "error" in result
        assert "API timeout" in result["error"]
        assert result["content_id"] == "timeout-1"


# ---------------------------------------------------------------------------
# calculate_roi
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCalculateROI:
    @pytest.mark.asyncio
    async def test_basic_roi_calculation(self):
        svc = _make_service()
        result = await svc.calculate_roi(
            content_id="roi-1",
            generation_cost=1.0,
            estimated_reach=1000,
            conversion_rate=0.02,
            revenue_per_conversion=10.0,
        )

        # 1000 * 0.02 = 20 conversions
        # 20 * 10 = 200 revenue
        # 200 - 1 = 199 profit
        # 199 / 1 * 100 = 19900%
        assert result["content_id"] == "roi-1"
        assert result["generation_cost"] == 1.0
        assert result["expected_conversions"] == pytest.approx(20.0)
        assert result["expected_revenue"] == pytest.approx(200.0)
        assert result["net_profit"] == pytest.approx(199.0)
        assert result["roi_percentage"] == pytest.approx(19900.0)
        assert result["payback_period_days"] > 0

    @pytest.mark.asyncio
    async def test_zero_cost_returns_zero_roi(self):
        svc = _make_service()
        result = await svc.calculate_roi(
            content_id="free-1",
            generation_cost=0.0,
        )

        assert result["roi_percentage"] == 0
        assert result["generation_cost"] == 0.0

    @pytest.mark.asyncio
    async def test_zero_revenue_returns_inf_payback(self):
        svc = _make_service()
        result = await svc.calculate_roi(
            content_id="no-rev",
            generation_cost=5.0,
            estimated_reach=0,
        )

        assert result["expected_revenue"] == 0.0
        assert result["payback_period_days"] == float("inf")

    @pytest.mark.asyncio
    async def test_default_parameters(self):
        svc = _make_service()
        result = await svc.calculate_roi(
            content_id="defaults-1",
            generation_cost=2.0,
        )

        # defaults: reach=1000, conv=0.02, rev_per_conv=10.0
        assert result["estimated_reach"] == 1000
        assert result["expected_conversions"] == pytest.approx(20.0)
        assert result["expected_revenue"] == pytest.approx(200.0)

    @pytest.mark.asyncio
    async def test_payback_period_calculation(self):
        svc = _make_service()
        result = await svc.calculate_roi(
            content_id="payback-1",
            generation_cost=10.0,
            estimated_reach=1000,
            conversion_rate=0.02,
            revenue_per_conversion=10.0,
        )

        # revenue = 200, daily = 200/365
        # payback = 10 / (200/365) = 10 * 365 / 200 = 18.25
        assert result["payback_period_days"] == pytest.approx(18.25, abs=0.01)

    @pytest.mark.asyncio
    async def test_negative_profit(self):
        svc = _make_service()
        result = await svc.calculate_roi(
            content_id="loss-1",
            generation_cost=500.0,
            estimated_reach=10,
            conversion_rate=0.01,
            revenue_per_conversion=1.0,
        )

        # 10 * 0.01 = 0.1 conversions, revenue = 0.1
        # net = 0.1 - 500 = -499.9
        assert result["net_profit"] < 0
        assert result["roi_percentage"] < 0


# ---------------------------------------------------------------------------
# forecast_budget
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestForecastBudget:
    @pytest.mark.asyncio
    async def test_basic_forecast(self):
        svc = _make_service()
        result = await svc.forecast_budget(
            monthly_content_target=30,
            avg_cost_per_piece=1.0,
            growth_rate=0.0,
            months_ahead=3,
        )

        assert result["forecast_months"] == 3
        assert result["monthly_content_target"] == 30
        assert result["avg_cost_per_piece"] == 1.0
        assert result["growth_rate"] == 0.0
        # 30 * 1.0 * 3 = 90 with no growth
        assert result["total_projected_cost"] == pytest.approx(90.0)
        assert len(result["monthly_forecasts"]) == 3

    @pytest.mark.asyncio
    async def test_forecast_with_growth(self):
        svc = _make_service()
        result = await svc.forecast_budget(
            monthly_content_target=10,
            avg_cost_per_piece=2.0,
            growth_rate=0.1,
            months_ahead=2,
        )

        forecasts = result["monthly_forecasts"]

        # Month 1: 10 * (1.1^0) = 10 pieces, cost = 20
        assert forecasts["month_1"]["projected_pieces"] == 10
        assert forecasts["month_1"]["monthly_cost"] == pytest.approx(20.0)

        # Month 2: 10 * (1.1^1) = 11 pieces, cost = 22
        assert forecasts["month_2"]["projected_pieces"] == 11
        assert forecasts["month_2"]["monthly_cost"] == pytest.approx(22.0)

        # Cumulative: 20 + 22 = 42
        assert result["total_projected_cost"] == pytest.approx(42.0)

    @pytest.mark.asyncio
    async def test_cumulative_cost_increases(self):
        svc = _make_service()
        result = await svc.forecast_budget(
            monthly_content_target=5,
            avg_cost_per_piece=3.0,
            months_ahead=4,
        )

        forecasts = result["monthly_forecasts"]
        prev_cumulative = 0.0
        for i in range(1, 5):
            current = forecasts[f"month_{i}"]["cumulative_cost"]
            assert current > prev_cumulative
            prev_cumulative = current

    @pytest.mark.asyncio
    async def test_default_parameters(self):
        svc = _make_service()
        result = await svc.forecast_budget(
            monthly_content_target=10,
            avg_cost_per_piece=1.0,
        )

        # defaults: growth_rate=0.1, months_ahead=12
        assert result["forecast_months"] == 12
        assert result["growth_rate"] == 0.1
        assert len(result["monthly_forecasts"]) == 12

    @pytest.mark.asyncio
    async def test_single_month(self):
        svc = _make_service()
        result = await svc.forecast_budget(
            monthly_content_target=20,
            avg_cost_per_piece=0.5,
            growth_rate=0.0,
            months_ahead=1,
        )

        assert result["total_projected_cost"] == pytest.approx(10.0)
        assert len(result["monthly_forecasts"]) == 1


# ---------------------------------------------------------------------------
# get_service_metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetServiceMetadata:
    def test_returns_expected_structure(self):
        svc = _make_service()
        meta = svc.get_service_metadata()

        assert meta["name"] == "financial_service"
        assert meta["category"] == "financial"
        assert meta["version"] == "1.0"
        assert "description" in meta
        assert isinstance(meta["capabilities"], list)

    def test_capabilities_include_key_features(self):
        svc = _make_service()
        meta = svc.get_service_metadata()
        caps = meta["capabilities"]

        assert "cost_analysis" in caps
        assert "roi_calculation" in caps
        assert "budget_forecasting" in caps
        assert "cost_optimization" in caps
        assert "financial_reporting" in caps
