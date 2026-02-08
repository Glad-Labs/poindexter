"""
Comprehensive tests for CostTrackingService.

Tests budget monitoring, alert thresholds, monthly reset, projections,
and cost optimization recommendations.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
from typing import Dict, Any

from ...financial_agent.cost_tracking import (
    CostTrackingService,
    BudgetAlertLevel,
    BudgetAlert,
    initialize_cost_tracking
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def cost_tracking_service():
    """Create CostTrackingService instance for testing."""
    return CostTrackingService(
        cofounder_api_url="http://localhost:8000",
        pubsub_client=None,
        enable_notifications=False
    )


@pytest.fixture
def mock_cost_metrics():
    """Mock cost metrics response from /metrics/costs endpoint."""
    return {
        'budget': {
            'monthly_limit': 100.0,
            'current_spent': 45.67,
            'remaining': 54.33,
            'percentage_used': 45.7
        },
        'ai_cache': {
            'total_requests': 1000,
            'cache_hits': 750,
            'cache_misses': 250,
            'hit_rate_percentage': 75.0,
            'estimated_savings_usd': 12.34
        },
        'model_router': {
            'total_requests': 1000,
            'budget_model_uses': 800,
            'premium_model_uses': 200,
            'budget_model_percentage': 80.0,
            'estimated_savings_usd': 15.67
        },
        'summary': {
            'total_estimated_savings_usd': 28.01,
            'optimization_enabled': True
        }
    }


@pytest.fixture
def mock_httpx_response(mock_cost_metrics):
    """Mock httpx response for cost metrics."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_cost_metrics
    return mock_response


@pytest.fixture
def mock_pubsub_client():
    """Mock Pub/Sub client."""
    mock_client = Mock()
    mock_client.topic.return_value = Mock()
    return mock_client


# ============================================================================
# TEST INITIALIZATION
# ============================================================================

@pytest.mark.unit
class TestCostTrackingInitialization:
    """Test CostTrackingService initialization."""
    
    def test_default_initialization(self):
        """Test service with default settings."""
        service = CostTrackingService()
        
        assert service.MONTHLY_BUDGET == 100.0
        assert service.monthly_spent == 0.0
        assert service.current_month == datetime.now().month
        assert service.current_year == datetime.now().year
        assert service.alert_history == []
        assert service.last_alert_level is None
    
    def test_custom_initialization(self):
        """Test service with custom settings."""
        mock_pubsub = Mock()
        service = CostTrackingService(
            cofounder_api_url="http://custom:9000",
            pubsub_client=mock_pubsub,
            enable_notifications=True
        )
        
        assert service.api_url == "http://custom:9000"
        assert service.pubsub_client == mock_pubsub
        assert service.enable_notifications is True
    
    def test_factory_initialization(self):
        """Test initialize_cost_tracking factory function."""
        service = initialize_cost_tracking(
            cofounder_api_url="http://localhost:8000",
            enable_notifications=False
        )
        
        assert isinstance(service, CostTrackingService)
        assert service.api_url == "http://localhost:8000"


# ============================================================================
# TEST MONTHLY RESET
# ============================================================================

@pytest.mark.unit
class TestMonthlyReset:
    """Test monthly billing period reset functionality."""
    
    def test_no_reset_same_month(self, cost_tracking_service):
        """Test no reset when still in same month."""
        initial_spent = 50.0
        cost_tracking_service.monthly_spent = initial_spent
        cost_tracking_service.alert_history = [Mock()]
        
        cost_tracking_service.check_monthly_reset()
        
        # Should not reset
        assert cost_tracking_service.monthly_spent == initial_spent
        assert len(cost_tracking_service.alert_history) == 1
    
    def test_reset_new_month(self, cost_tracking_service):
        """Test reset when entering new month."""
        # Set to previous month
        now = datetime.now()
        previous_month = (now.month - 1) if now.month > 1 else 12
        previous_year = now.year if now.month > 1 else now.year - 1
        
        cost_tracking_service.current_month = previous_month
        cost_tracking_service.current_year = previous_year
        cost_tracking_service.monthly_spent = 75.0
        cost_tracking_service.alert_history = [Mock(), Mock()]
        cost_tracking_service.last_alert_level = BudgetAlertLevel.WARNING
        
        cost_tracking_service.check_monthly_reset()
        
        # Should reset all counters
        assert cost_tracking_service.monthly_spent == 0.0
        assert cost_tracking_service.current_month == now.month
        assert cost_tracking_service.current_year == now.year
        assert cost_tracking_service.alert_history == []
        assert cost_tracking_service.last_alert_level is None
    
    def test_reset_new_year(self, cost_tracking_service):
        """Test reset when entering new year."""
        # Set to previous year, December
        now = datetime.now()
        cost_tracking_service.current_month = 12
        cost_tracking_service.current_year = now.year - 1
        cost_tracking_service.monthly_spent = 100.0
        
        cost_tracking_service.check_monthly_reset()
        
        # Should reset
        assert cost_tracking_service.monthly_spent == 0.0
        assert cost_tracking_service.current_year == now.year


# ============================================================================
# TEST FETCH COST METRICS
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestFetchCostMetrics:
    """Test fetching cost metrics from API."""
    
    async def test_fetch_success(self, cost_tracking_service, mock_httpx_response):
        """Test successful metrics fetch."""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_httpx_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = AsyncMock()
            mock_client_class.return_value = mock_client
            
            metrics = await cost_tracking_service.fetch_cost_metrics()
            
            assert metrics is not None
            assert 'budget' in metrics
            assert 'ai_cache' in metrics
            assert metrics['budget']['current_spent'] == 45.67
    
    async def test_fetch_connection_error(self, cost_tracking_service):
        """Test fetch with connection error."""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = Exception("Connection refused")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = AsyncMock()
            mock_client_class.return_value = mock_client
            
            metrics = await cost_tracking_service.fetch_cost_metrics()
            
            assert metrics is None
    
    async def test_fetch_non_200_status(self, cost_tracking_service):
        """Test fetch with non-200 status code."""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_response = AsyncMock()
            mock_response.status_code = 500
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = AsyncMock()
            mock_client_class.return_value = mock_client
            
            metrics = await cost_tracking_service.fetch_cost_metrics()
            
            assert metrics is None


# ============================================================================
# TEST BUDGET THRESHOLD CHECKS
# ============================================================================

@pytest.mark.unit
class TestBudgetThresholds:
    """Test budget threshold alert triggering."""
    
    def test_no_alert_under_75_percent(self, cost_tracking_service):
        """Test no alert when under 75% of budget."""
        alert = cost_tracking_service._check_budget_thresholds(
            spent=50.0,
            budget=100.0,
            percentage=50.0
        )
        
        assert alert is None
    
    def test_warning_alert_75_percent(self, cost_tracking_service):
        """Test WARNING alert at 75% threshold."""
        alert = cost_tracking_service._check_budget_thresholds(
            spent=75.0,
            budget=100.0,
            percentage=75.0
        )
        
        assert alert is not None
        assert alert.level == BudgetAlertLevel.WARNING
        assert alert.percentage == 75.0
        assert alert.amount_spent == 75.0
        assert alert.amount_remaining == 25.0
        assert "75%" in alert.message.lower()
        assert len(alert.recommendations) > 0
    
    def test_urgent_alert_90_percent(self, cost_tracking_service):
        """Test URGENT alert at 90% threshold."""
        alert = cost_tracking_service._check_budget_thresholds(
            spent=90.0,
            budget=100.0,
            percentage=90.0
        )
        
        assert alert is not None
        assert alert.level == BudgetAlertLevel.URGENT
        assert alert.percentage == 90.0
        assert "urgent" in alert.message.lower()
        assert any("URGENT" in rec for rec in alert.recommendations)
    
    def test_critical_alert_100_percent(self, cost_tracking_service):
        """Test CRITICAL alert at 100% threshold."""
        alert = cost_tracking_service._check_budget_thresholds(
            spent=100.0,
            budget=100.0,
            percentage=100.0
        )
        
        assert alert is not None
        assert alert.level == BudgetAlertLevel.CRITICAL
        assert alert.percentage == 100.0
        assert "critical" in alert.message.lower()
        assert any("IMMEDIATE ACTION" in rec for rec in alert.recommendations)
    
    def test_critical_alert_over_budget(self, cost_tracking_service):
        """Test CRITICAL alert when over budget."""
        alert = cost_tracking_service._check_budget_thresholds(
            spent=110.0,
            budget=100.0,
            percentage=110.0
        )
        
        assert alert is not None
        assert alert.level == BudgetAlertLevel.CRITICAL
        assert alert.amount_remaining == -10.0
    
    def test_no_duplicate_alert_same_level(self, cost_tracking_service):
        """Test no duplicate alert at same severity level."""
        # First alert at WARNING
        alert1 = cost_tracking_service._check_budget_thresholds(
            spent=75.0,
            budget=100.0,
            percentage=75.0
        )
        assert alert1 is not None
        
        # Second alert at WARNING - should be None
        alert2 = cost_tracking_service._check_budget_thresholds(
            spent=80.0,
            budget=100.0,
            percentage=80.0
        )
        assert alert2 is None
    
    def test_escalate_to_higher_level(self, cost_tracking_service):
        """Test alert escalation to higher severity."""
        # First alert at WARNING
        alert1 = cost_tracking_service._check_budget_thresholds(
            spent=75.0,
            budget=100.0,
            percentage=75.0
        )
        assert alert1.level == BudgetAlertLevel.WARNING
        
        # Escalate to URGENT - should create new alert
        alert2 = cost_tracking_service._check_budget_thresholds(
            spent=90.0,
            budget=100.0,
            percentage=90.0
        )
        assert alert2 is not None
        assert alert2.level == BudgetAlertLevel.URGENT
    
    def test_alert_history_tracking(self, cost_tracking_service):
        """Test alerts are added to history."""
        initial_count = len(cost_tracking_service.alert_history)
        
        cost_tracking_service._check_budget_thresholds(
            spent=75.0,
            budget=100.0,
            percentage=75.0
        )
        
        assert len(cost_tracking_service.alert_history) == initial_count + 1


# ============================================================================
# TEST PROJECTIONS
# ============================================================================

@pytest.mark.unit
class TestProjections:
    """Test end-of-month spending projections."""
    
    def test_projection_midmonth(self, cost_tracking_service):
        """Test projection calculation mid-month."""
        # Mock day 15 with $50 spent
        with patch('agents.financial_agent.cost_tracking.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 10, 15)
            
            projections = cost_tracking_service._calculate_projections(
                current_spent=50.0
            )
            
            # Daily rate = 50 / 15 = 3.33
            # Projected = 3.33 * 30 = ~100
            assert projections['daily_rate'] > 3.0
            assert projections['daily_rate'] < 4.0
            assert projections['projected_monthly_total'] > 95.0
            assert projections['projected_monthly_total'] < 105.0
            assert projections['days_elapsed'] == 15
            assert projections['days_remaining'] == 15
    
    def test_projection_early_month(self, cost_tracking_service):
        """Test projection early in month."""
        with patch('agents.financial_agent.cost_tracking.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 10, 5)
            
            projections = cost_tracking_service._calculate_projections(
                current_spent=10.0
            )
            
            # Daily rate = 10 / 5 = 2.0
            # Projected = 2.0 * 30 = 60
            assert projections['daily_rate'] == 2.0
            assert projections['projected_monthly_total'] == 60.0
            assert projections['projected_overage'] == 0.0
    
    def test_projection_overspending(self, cost_tracking_service):
        """Test projection when overspending detected."""
        with patch('agents.financial_agent.cost_tracking.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 10, 10)
            
            projections = cost_tracking_service._calculate_projections(
                current_spent=50.0
            )
            
            # Daily rate = 50 / 10 = 5.0
            # Projected = 5.0 * 30 = 150
            # Overage = 150 - 100 = 50
            assert projections['daily_rate'] == 5.0
            assert projections['projected_monthly_total'] == 150.0
            assert projections['projected_overage'] == 50.0
    
    def test_projection_first_day(self, cost_tracking_service):
        """Test projection on first day of month."""
        with patch('agents.financial_agent.cost_tracking.datetime') as mock_datetime:
            # Day 0 should be handled gracefully
            mock_now = Mock()
            mock_now.day = 0
            mock_datetime.now.return_value = mock_now
            
            projections = cost_tracking_service._calculate_projections(
                current_spent=5.0
            )
            
            # Should return zeros to avoid division by zero
            assert projections['projected_monthly_total'] == 0.0
            assert projections['daily_rate'] == 0.0


# ============================================================================
# TEST RECOMMENDATIONS
# ============================================================================

@pytest.mark.unit
class TestRecommendations:
    """Test cost optimization recommendation generation."""
    
    def test_recommendations_info_level(self, cost_tracking_service, mock_cost_metrics):
        """Test recommendations at INFO level (< 75%)."""
        recommendations = cost_tracking_service._generate_recommendations(
            metrics=mock_cost_metrics,
            budget_percentage=50.0,
            alert_level=BudgetAlertLevel.INFO
        )
        
        assert isinstance(recommendations, list)
        assert len(recommendations) > 0
        # Should include general optimization tips
        assert any("optimization" in rec.lower() or "cache" in rec.lower() 
                  for rec in recommendations)
    
    def test_recommendations_warning_level(self, cost_tracking_service, mock_cost_metrics):
        """Test recommendations at WARNING level (75%)."""
        recommendations = cost_tracking_service._generate_recommendations(
            metrics=mock_cost_metrics,
            budget_percentage=75.0,
            alert_level=BudgetAlertLevel.WARNING
        )
        
        # Should include monitoring advice
        assert any("monitor" in rec.lower() for rec in recommendations)
    
    def test_recommendations_urgent_level(self, cost_tracking_service, mock_cost_metrics):
        """Test recommendations at URGENT level (90%)."""
        recommendations = cost_tracking_service._generate_recommendations(
            metrics=mock_cost_metrics,
            budget_percentage=90.0,
            alert_level=BudgetAlertLevel.URGENT
        )
        
        # Should include priority/critical task guidance
        assert any("prioritize" in rec.lower() or "critical" in rec.lower() 
                  for rec in recommendations)
    
    def test_recommendations_critical_level(self, cost_tracking_service, mock_cost_metrics):
        """Test recommendations at CRITICAL level (100%)."""
        recommendations = cost_tracking_service._generate_recommendations(
            metrics=mock_cost_metrics,
            budget_percentage=100.0,
            alert_level=BudgetAlertLevel.CRITICAL
        )
        
        # Should include immediate action items
        assert any("disable" in rec.lower() or "immediate" in rec.lower() 
                  for rec in recommendations)
    
    def test_recommendations_with_projections(self, cost_tracking_service, mock_cost_metrics):
        """Test recommendations include projection warnings."""
        with patch('agents.financial_agent.cost_tracking.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 10, 10)
            
            # Set monthly_spent to trigger projection warning
            cost_tracking_service.monthly_spent = 50.0
            
            recommendations = cost_tracking_service._generate_recommendations(
                metrics=mock_cost_metrics,
                budget_percentage=60.0,
                alert_level=BudgetAlertLevel.INFO
            )
            
            # Should include projection warning (50/10*30 = $150 projected)
            assert any("projected" in rec.lower() or "month end" in rec.lower() 
                      for rec in recommendations)


# ============================================================================
# TEST ANALYZE COSTS
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestAnalyzeCosts:
    """Test comprehensive cost analysis."""
    
    async def test_analyze_success(self, cost_tracking_service, mock_httpx_response):
        """Test successful cost analysis."""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_httpx_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = AsyncMock()
            mock_client_class.return_value = mock_client
            
            analysis = await cost_tracking_service.analyze_costs()
            
            assert analysis['status'] == 'success'
            assert 'monthly_budget' in analysis
            assert 'optimization_performance' in analysis
            assert 'recommendations' in analysis
            assert 'projections' in analysis
            assert 'timestamp' in analysis
    
    async def test_analyze_with_alert(self, cost_tracking_service, mock_httpx_response):
        """Test cost analysis triggers alert."""
        # Modify mock to return 80% budget usage
        mock_response = mock_httpx_response
        mock_response.json.return_value['budget']['current_spent'] = 80.0
        mock_response.json.return_value['budget']['percentage_used'] = 80.0
        
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = AsyncMock()
            mock_client_class.return_value = mock_client
            
            analysis = await cost_tracking_service.analyze_costs()
            
            assert analysis['alert'] is not None
            assert analysis['alert']['level'] == BudgetAlertLevel.WARNING.value
    
    async def test_analyze_fetch_failure(self, cost_tracking_service):
        """Test analyze when metrics fetch fails."""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = Exception("API error")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = AsyncMock()
            mock_client_class.return_value = mock_client
            
            analysis = await cost_tracking_service.analyze_costs()
            
            assert analysis['status'] == 'error'
            assert 'Failed to fetch' in analysis['message']
    
    async def test_analyze_calls_monthly_reset(self, cost_tracking_service, mock_httpx_response):
        """Test analyze calls monthly reset check."""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_httpx_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = AsyncMock()
            mock_client_class.return_value = mock_client
            
            with patch.object(cost_tracking_service, 'check_monthly_reset') as mock_reset:
                await cost_tracking_service.analyze_costs()
                
                mock_reset.assert_called_once()


# ============================================================================
# TEST MONTHLY SUMMARY
# ============================================================================

@pytest.mark.unit
class TestMonthlySummary:
    """Test monthly spending summary."""
    
    def test_summary_structure(self, cost_tracking_service):
        """Test summary returns correct structure."""
        cost_tracking_service.monthly_spent = 45.67
        
        summary = cost_tracking_service.get_monthly_summary()
        
        assert 'period' in summary
        assert 'budget' in summary
        assert 'spent' in summary
        assert 'remaining' in summary
        assert 'percentage_used' in summary
        assert 'alerts_triggered' in summary
        assert 'projections' in summary
    
    def test_summary_calculations(self, cost_tracking_service):
        """Test summary calculations are accurate."""
        cost_tracking_service.monthly_spent = 60.0
        
        summary = cost_tracking_service.get_monthly_summary()
        
        assert summary['budget'] == 100.0
        assert summary['spent'] == 60.0
        assert summary['remaining'] == 40.0
        assert summary['percentage_used'] == 60.0
    
    def test_summary_with_alerts(self, cost_tracking_service):
        """Test summary includes alert count."""
        cost_tracking_service.alert_history = [Mock(), Mock(), Mock()]
        cost_tracking_service.last_alert_level = BudgetAlertLevel.WARNING
        
        summary = cost_tracking_service.get_monthly_summary()
        
        assert summary['alerts_triggered'] == 3
        assert summary['last_alert_level'] == 'warning'
    
    def test_summary_no_alerts(self, cost_tracking_service):
        """Test summary with no alerts."""
        summary = cost_tracking_service.get_monthly_summary()
        
        assert summary['alerts_triggered'] == 0
        assert summary['last_alert_level'] is None


# ============================================================================
# TEST PUB/SUB ALERTS
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestPubSubAlerts:
    """Test Pub/Sub alert publishing."""
    
    async def test_publish_alert_success(self, mock_pubsub_client):
        """Test successful alert publishing."""
        service = CostTrackingService(
            pubsub_client=mock_pubsub_client,
            enable_notifications=True
        )
        
        alert = BudgetAlert(
            level=BudgetAlertLevel.WARNING,
            percentage=75.0,
            amount_spent=75.0,
            amount_remaining=25.0,
            threshold=0.75,
            message="Test alert",
            timestamp=datetime.now(),
            recommendations=["Test recommendation"]
        )
        
        await service._publish_alert(alert)
        
        # Verify Pub/Sub called
        assert mock_pubsub_client.topic.called
    
    async def test_publish_alert_disabled(self, mock_pubsub_client):
        """Test alert not published when notifications disabled."""
        service = CostTrackingService(
            pubsub_client=mock_pubsub_client,
            enable_notifications=False
        )
        
        alert = BudgetAlert(
            level=BudgetAlertLevel.WARNING,
            percentage=75.0,
            amount_spent=75.0,
            amount_remaining=25.0,
            threshold=0.75,
            message="Test alert",
            timestamp=datetime.now(),
            recommendations=[]
        )
        
        await service._publish_alert(alert)
        
        # Should not call Pub/Sub
        assert not mock_pubsub_client.topic.called
    
    async def test_publish_alert_no_client(self):
        """Test alert publishing without Pub/Sub client."""
        service = CostTrackingService(
            pubsub_client=None,
            enable_notifications=True
        )
        
        alert = BudgetAlert(
            level=BudgetAlertLevel.WARNING,
            percentage=75.0,
            amount_spent=75.0,
            amount_remaining=25.0,
            threshold=0.75,
            message="Test alert",
            timestamp=datetime.now(),
            recommendations=[]
        )
        
        # Should not raise error
        await service._publish_alert(alert)


# ============================================================================
# TEST INTEGRATION SCENARIOS
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestIntegrationScenarios:
    """Integration tests for cost tracking workflows."""
    
    async def test_full_cost_analysis_workflow(self, cost_tracking_service, mock_httpx_response):
        """Test complete cost analysis workflow."""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_httpx_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = AsyncMock()
            mock_client_class.return_value = mock_client
            
            # Perform analysis
            analysis = await cost_tracking_service.analyze_costs()
            
            # Get summary
            summary = cost_tracking_service.get_monthly_summary()
            
            # Verify workflow completed
            assert analysis['status'] == 'success'
            assert summary['spent'] > 0
    
    async def test_alert_escalation_workflow(self, cost_tracking_service):
        """Test alert escalation through severity levels."""
        # WARNING at 75%
        alert1 = cost_tracking_service._check_budget_thresholds(
            spent=75.0, budget=100.0, percentage=75.0
        )
        assert alert1.level == BudgetAlertLevel.WARNING
        
        # URGENT at 90%
        alert2 = cost_tracking_service._check_budget_thresholds(
            spent=90.0, budget=100.0, percentage=90.0
        )
        assert alert2.level == BudgetAlertLevel.URGENT
        
        # CRITICAL at 100%
        alert3 = cost_tracking_service._check_budget_thresholds(
            spent=100.0, budget=100.0, percentage=100.0
        )
        assert alert3.level == BudgetAlertLevel.CRITICAL
        
        # Verify escalation tracked
        assert len(cost_tracking_service.alert_history) == 3
