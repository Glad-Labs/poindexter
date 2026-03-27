"""
Unit tests for agents/financial_agent/cost_tracking.py

Tests for CostTrackingService, BudgetAlert, BudgetAlertLevel,
and initialize_cost_tracking factory function.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.financial_agent.cost_tracking import (
    BudgetAlert,
    BudgetAlertLevel,
    CostTrackingService,
    initialize_cost_tracking,
)

# ---------------------------------------------------------------------------
# BudgetAlertLevel enum
# ---------------------------------------------------------------------------


class TestBudgetAlertLevel:
    def test_values(self):
        assert BudgetAlertLevel.INFO == "info"
        assert BudgetAlertLevel.WARNING == "warning"
        assert BudgetAlertLevel.URGENT == "urgent"
        assert BudgetAlertLevel.CRITICAL == "critical"

    def test_is_string_enum(self):
        assert isinstance(BudgetAlertLevel.WARNING, str)


# ---------------------------------------------------------------------------
# CostTrackingService.__init__
# ---------------------------------------------------------------------------


class TestCostTrackingServiceInit:
    def test_default_initialization(self):
        svc = CostTrackingService()
        assert svc.api_url == "http://localhost:8000"
        assert svc.pubsub_client is None
        assert svc.enable_notifications is True
        assert svc.monthly_spent == 0.0
        assert svc.alert_history == []
        assert svc.last_alert_level is None

    def test_custom_url(self):
        svc = CostTrackingService(cofounder_api_url="http://staging:8000")
        assert svc.api_url == "http://staging:8000"

    def test_disable_notifications(self):
        svc = CostTrackingService(enable_notifications=False)
        assert svc.enable_notifications is False

    def test_pubsub_client_stored(self):
        mock_client = MagicMock()
        svc = CostTrackingService(pubsub_client=mock_client)
        assert svc.pubsub_client is mock_client

    def test_monthly_budget_constant(self):
        assert CostTrackingService.MONTHLY_BUDGET == 100.0

    def test_alert_thresholds_defined(self):
        thresholds = CostTrackingService.ALERT_THRESHOLDS
        assert BudgetAlertLevel.WARNING in thresholds
        assert BudgetAlertLevel.URGENT in thresholds
        assert BudgetAlertLevel.CRITICAL in thresholds
        assert thresholds[BudgetAlertLevel.WARNING] == 0.75
        assert thresholds[BudgetAlertLevel.URGENT] == 0.90
        assert thresholds[BudgetAlertLevel.CRITICAL] == 1.00


# ---------------------------------------------------------------------------
# check_monthly_reset
# ---------------------------------------------------------------------------


class TestCheckMonthlyReset:
    def test_no_reset_same_month(self):
        svc = CostTrackingService()
        svc.monthly_spent = 50.0
        original_month = svc.current_month
        # Same month/year — no reset
        svc.check_monthly_reset()
        assert svc.monthly_spent == 50.0
        assert svc.current_month == original_month

    def test_reset_on_new_month(self):
        svc = CostTrackingService()
        svc.monthly_spent = 75.0
        svc.current_month = 1  # Force January
        svc.current_year = 2025  # Force past year
        svc.alert_history = [MagicMock()]
        svc.last_alert_level = BudgetAlertLevel.WARNING

        svc.check_monthly_reset()

        assert svc.monthly_spent == 0.0
        assert svc.alert_history == []
        assert svc.last_alert_level is None


# ---------------------------------------------------------------------------
# _check_budget_thresholds
# ---------------------------------------------------------------------------


class TestCheckBudgetThresholds:
    def setup_method(self):
        self.svc = CostTrackingService()

    def test_below_warning_returns_none(self):
        result = self.svc._check_budget_thresholds(spent=50.0, budget=100.0, percentage=50.0)
        assert result is None

    def test_exactly_at_warning_threshold(self):
        result = self.svc._check_budget_thresholds(spent=75.0, budget=100.0, percentage=75.0)
        assert result is not None
        assert result.level == BudgetAlertLevel.WARNING

    def test_warning_alert_fields(self):
        result = self.svc._check_budget_thresholds(spent=78.0, budget=100.0, percentage=78.0)
        assert result is not None
        assert result.amount_spent == 78.0
        assert result.amount_remaining == 22.0
        assert len(result.recommendations) > 0

    def test_urgent_alert_at_90_percent(self):
        result = self.svc._check_budget_thresholds(spent=92.0, budget=100.0, percentage=92.0)
        assert result is not None
        assert result.level == BudgetAlertLevel.URGENT

    def test_critical_alert_at_100_percent(self):
        result = self.svc._check_budget_thresholds(spent=105.0, budget=100.0, percentage=105.0)
        assert result is not None
        assert result.level == BudgetAlertLevel.CRITICAL

    def test_no_duplicate_alert_same_level(self):
        # First call creates WARNING alert
        result1 = self.svc._check_budget_thresholds(spent=78.0, budget=100.0, percentage=78.0)
        assert result1 is not None
        # Second call at same level should be suppressed
        result2 = self.svc._check_budget_thresholds(spent=80.0, budget=100.0, percentage=80.0)
        assert result2 is None

    def test_last_alert_level_updated(self):
        self.svc._check_budget_thresholds(spent=78.0, budget=100.0, percentage=78.0)
        assert self.svc.last_alert_level == BudgetAlertLevel.WARNING

    def test_alert_added_to_history(self):
        self.svc._check_budget_thresholds(spent=78.0, budget=100.0, percentage=78.0)
        assert len(self.svc.alert_history) == 1

    def test_alert_message_contains_level(self):
        result = self.svc._check_budget_thresholds(spent=78.0, budget=100.0, percentage=78.0)
        assert result is not None
        assert "WARNING" in result.message.upper() or "warning" in result.message.lower()

    def test_critical_recommendations_are_urgent(self):
        result = self.svc._check_budget_thresholds(spent=105.0, budget=100.0, percentage=105.0)
        assert result is not None
        # Critical alerts should have actionable recommendations
        assert any(
            "IMMEDIATE" in r.upper() or "budget" in r.lower() for r in result.recommendations
        )


# ---------------------------------------------------------------------------
# _calculate_projections
# ---------------------------------------------------------------------------


class TestCalculateProjections:
    def setup_method(self):
        self.svc = CostTrackingService()

    def test_returns_dict_with_expected_keys(self):
        result = self.svc._calculate_projections(current_spent=30.0)
        assert "projected_monthly_total" in result
        assert "projected_overage" in result
        assert "daily_rate" in result
        assert "days_elapsed" in result
        assert "days_remaining" in result

    def test_zero_spent_no_overage(self):
        result = self.svc._calculate_projections(current_spent=0.0)
        assert result["projected_overage"] == 0.0

    def test_high_spend_has_overage(self):
        # If we've spent $90 in 10 days, projected for 30 days = $270 → overage = $170
        svc = CostTrackingService()
        with patch("agents.financial_agent.cost_tracking.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 10)  # day 10
            result = svc._calculate_projections(current_spent=90.0)
        # projected_total = (90/10) * 30 = 270
        assert result["projected_monthly_total"] == 270.0
        assert result["projected_overage"] == 170.0

    def test_daily_rate_computed(self):
        svc = CostTrackingService()
        with patch("agents.financial_agent.cost_tracking.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 15)  # day 15
            result = svc._calculate_projections(current_spent=30.0)
        assert result["daily_rate"] == 2.0  # 30/15 = 2.0

    def test_values_rounded(self):
        result = self.svc._calculate_projections(current_spent=33.333)
        # Values should be rounded to 2 decimal places
        assert result["projected_monthly_total"] == round(result["projected_monthly_total"], 2)


# ---------------------------------------------------------------------------
# get_monthly_summary
# ---------------------------------------------------------------------------


class TestGetMonthlySummary:
    def test_returns_expected_keys(self):
        svc = CostTrackingService()
        summary = svc.get_monthly_summary()
        assert "period" in summary
        assert "budget" in summary
        assert "spent" in summary
        assert "remaining" in summary
        assert "percentage_used" in summary
        assert "alerts_triggered" in summary
        assert "last_alert_level" in summary
        assert "projections" in summary

    def test_initial_state(self):
        svc = CostTrackingService()
        summary = svc.get_monthly_summary()
        assert summary["budget"] == 100.0
        assert summary["spent"] == 0.0
        assert summary["remaining"] == 100.0
        assert summary["percentage_used"] == 0.0
        assert summary["alerts_triggered"] == 0
        assert summary["last_alert_level"] is None

    def test_after_spending(self):
        svc = CostTrackingService()
        svc.monthly_spent = 40.0
        summary = svc.get_monthly_summary()
        assert summary["spent"] == 40.0
        assert summary["remaining"] == 60.0
        assert summary["percentage_used"] == 40.0

    def test_period_format(self):
        svc = CostTrackingService()
        svc.current_year = 2026
        svc.current_month = 3
        summary = svc.get_monthly_summary()
        assert summary["period"] == "2026-03"

    def test_last_alert_level_reported(self):
        svc = CostTrackingService()
        svc.last_alert_level = BudgetAlertLevel.WARNING
        summary = svc.get_monthly_summary()
        assert summary["last_alert_level"] == "warning"


# ---------------------------------------------------------------------------
# fetch_cost_metrics (async)
# ---------------------------------------------------------------------------


class TestFetchCostMetrics:
    @pytest.mark.asyncio
    async def test_returns_costs_on_success(self):
        svc = CostTrackingService()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"costs": {"budget": {"current_spent": 5.0}}}

        with patch("agents.financial_agent.cost_tracking.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_response

            result = await svc.fetch_cost_metrics()

        assert result == {"budget": {"current_spent": 5.0}}

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self):
        import httpx

        svc = CostTrackingService()

        with patch("agents.financial_agent.cost_tracking.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.side_effect = httpx.HTTPError("connection refused")

            result = await svc.fetch_cost_metrics()

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_unexpected_error(self):
        svc = CostTrackingService()

        with patch("agents.financial_agent.cost_tracking.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.side_effect = RuntimeError("unexpected")

            result = await svc.fetch_cost_metrics()

        assert result is None


# ---------------------------------------------------------------------------
# _publish_alert (async)
# ---------------------------------------------------------------------------


class TestPublishAlert:
    @pytest.mark.asyncio
    async def test_no_pubsub_skips_silently(self):
        svc = CostTrackingService(pubsub_client=None)
        alert = BudgetAlert(
            level=BudgetAlertLevel.WARNING,
            percentage=80.0,
            amount_spent=80.0,
            amount_remaining=20.0,
            threshold=0.75,
            message="Warning",
            timestamp=datetime.now(),
            recommendations=["Reduce usage"],
        )
        # Should not raise
        await svc._publish_alert(alert)

    @pytest.mark.asyncio
    async def test_pubsub_publish_called(self):
        mock_pubsub = AsyncMock()
        mock_pubsub.publish = AsyncMock()
        svc = CostTrackingService(pubsub_client=mock_pubsub)

        alert = BudgetAlert(
            level=BudgetAlertLevel.CRITICAL,
            percentage=105.0,
            amount_spent=105.0,
            amount_remaining=-5.0,
            threshold=1.0,
            message="Critical alert",
            timestamp=datetime.now(),
            recommendations=["Stop immediately"],
        )
        await svc._publish_alert(alert)
        mock_pubsub.publish.assert_called_once()
        call_kwargs = mock_pubsub.publish.call_args
        assert call_kwargs[1]["topic"] == "financial-alerts" or "financial-alerts" in str(
            call_kwargs
        )


# ---------------------------------------------------------------------------
# initialize_cost_tracking factory
# ---------------------------------------------------------------------------


class TestInitializeCostTracking:
    def test_returns_cost_tracking_service(self):
        svc = initialize_cost_tracking()
        assert isinstance(svc, CostTrackingService)

    def test_custom_url(self):
        svc = initialize_cost_tracking(cofounder_api_url="http://prod:8000")
        assert svc.api_url == "http://prod:8000"

    def test_notifications_disabled(self):
        svc = initialize_cost_tracking(enable_notifications=False)
        assert svc.enable_notifications is False

    def test_pubsub_client_passed_through(self):
        mock_client = MagicMock()
        svc = initialize_cost_tracking(pubsub_client=mock_client)
        assert svc.pubsub_client is mock_client
