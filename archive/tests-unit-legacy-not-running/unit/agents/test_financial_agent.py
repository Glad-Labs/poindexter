"""
Comprehensive tests for FinancialAgent.

Tests cost analysis integration, monthly summaries, and financial
summary generation with cost tracking.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime
from typing import Dict, Any

from ...financial_agent.financial_agent import FinancialAgent
from ...financial_agent.cost_tracking import (
    CostTrackingService,
    BudgetAlertLevel,
    BudgetAlert
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def financial_agent():
    """Create FinancialAgent instance with cost tracking disabled."""
    return FinancialAgent(
        cofounder_api_url="http://localhost:8000",
        pubsub_client=None,
        enable_cost_tracking=False
    )


@pytest.fixture
def financial_agent_with_tracking():
    """Create FinancialAgent instance with cost tracking enabled."""
    return FinancialAgent(
        cofounder_api_url="http://localhost:8000",
        pubsub_client=None,
        enable_cost_tracking=True
    )


@pytest.fixture
def mock_cost_analysis():
    """Mock cost analysis result."""
    return {
        'status': 'success',
        'timestamp': datetime.now().isoformat(),
        'monthly_budget': {
            'limit': 100.0,
            'spent': 45.67,
            'remaining': 54.33,
            'percentage_used': 45.7,
            'period': '2025-10'
        },
        'optimization_performance': {
            'ai_cache_hit_rate': 75.0,
            'ai_cache_savings': 12.34,
            'model_router_savings': 15.67,
            'budget_model_usage': 80.0,
            'total_savings': 28.01
        },
        'alert': None,
        'recommendations': [
            "Maintain current optimization settings",
            "Cache hit rate is excellent"
        ],
        'projections': {
            'projected_monthly_total': 91.34,
            'projected_overage': 0.0,
            'daily_rate': 3.04,
            'days_elapsed': 15,
            'days_remaining': 15
        }
    }


@pytest.fixture
def mock_monthly_summary():
    """Mock monthly summary result."""
    return {
        'period': '2025-10',
        'budget': 100.0,
        'spent': 45.67,
        'remaining': 54.33,
        'percentage_used': 45.7,
        'alerts_triggered': 0,
        'last_alert_level': None,
        'projections': {
            'projected_monthly_total': 91.34,
            'projected_overage': 0.0,
            'daily_rate': 3.04
        }
    }


# ============================================================================
# TEST INITIALIZATION
# ============================================================================

@pytest.mark.unit
class TestFinancialAgentInitialization:
    """Test FinancialAgent initialization."""
    
    def test_default_initialization(self):
        """Test agent with default settings."""
        agent = FinancialAgent()
        
        assert agent.cost_tracking is not None
        assert isinstance(agent.cost_tracking, CostTrackingService)
    
    def test_with_cost_tracking_enabled(self):
        """Test agent with cost tracking enabled."""
        agent = FinancialAgent(
            cofounder_api_url="http://localhost:8000",
            enable_cost_tracking=True
        )
        
        assert agent.cost_tracking is not None
        assert agent.cost_tracking.api_url == "http://localhost:8000"
    
    def test_with_cost_tracking_disabled(self):
        """Test agent with cost tracking disabled."""
        agent = FinancialAgent(enable_cost_tracking=False)
        
        assert agent.cost_tracking is None
    
    def test_with_pubsub_client(self):
        """Test agent with Pub/Sub client."""
        mock_pubsub = Mock()
        agent = FinancialAgent(
            pubsub_client=mock_pubsub,
            enable_cost_tracking=True
        )
        
        assert agent.cost_tracking is not None
        assert agent.cost_tracking.pubsub_client == mock_pubsub


# ============================================================================
# TEST ANALYZE COSTS
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestAnalyzeCosts:
    """Test cost analysis method."""
    
    async def test_analyze_costs_success(self, financial_agent_with_tracking, mock_cost_analysis):
        """Test successful cost analysis."""
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'analyze_costs',
            new=AsyncMock(return_value=mock_cost_analysis)
        ):
            result = await financial_agent_with_tracking.analyze_costs()
            
            assert result['status'] == 'success'
            assert 'monthly_budget' in result
            assert 'optimization_performance' in result
            assert 'recommendations' in result
    
    async def test_analyze_costs_without_tracking(self, financial_agent):
        """Test cost analysis when tracking disabled."""
        result = await financial_agent.analyze_costs()
        
        assert result['status'] == 'error'
        assert 'not enabled' in result['message']
    
    async def test_analyze_costs_with_alert(self, financial_agent_with_tracking):
        """Test cost analysis with budget alert."""
        mock_analysis_with_alert = {
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'monthly_budget': {
                'limit': 100.0,
                'spent': 80.0,
                'remaining': 20.0,
                'percentage_used': 80.0
            },
            'alert': {
                'level': 'warning',
                'message': 'WARNING: 80% of budget used',
                'recommendations': [
                    'Monitor spending closely',
                    'Use budget models for non-critical tasks'
                ]
            },
            'recommendations': ['Test recommendation'],
            'projections': {
                'projected_monthly_total': 160.0,
                'projected_overage': 60.0
            }
        }
        
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'analyze_costs',
            new=AsyncMock(return_value=mock_analysis_with_alert)
        ):
            result = await financial_agent_with_tracking.analyze_costs()
            
            assert result['alert'] is not None
            assert result['alert']['level'] == 'warning'
            assert len(result['alert']['recommendations']) > 0
    
    async def test_analyze_costs_exception_handling(self, financial_agent_with_tracking):
        """Test cost analysis with exception."""
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'analyze_costs',
            new=AsyncMock(side_effect=Exception("API error"))
        ):
            with pytest.raises(Exception):
                await financial_agent_with_tracking.analyze_costs()


# ============================================================================
# TEST GET MONTHLY SUMMARY
# ============================================================================

@pytest.mark.unit
class TestGetMonthlySummary:
    """Test monthly summary method."""
    
    def test_get_monthly_summary_success(self, financial_agent_with_tracking, mock_monthly_summary):
        """Test successful monthly summary."""
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'get_monthly_summary',
            return_value=mock_monthly_summary
        ):
            result = financial_agent_with_tracking.get_monthly_summary()
            
            assert 'period' in result
            assert 'budget' in result
            assert 'spent' in result
            assert result['budget'] == 100.0
            assert result['spent'] == 45.67
    
    def test_get_monthly_summary_without_tracking(self, financial_agent):
        """Test monthly summary when tracking disabled."""
        result = financial_agent.get_monthly_summary()
        
        assert result['status'] == 'error'
        assert 'not enabled' in result['message']
    
    def test_get_monthly_summary_with_alerts(self, financial_agent_with_tracking):
        """Test monthly summary with triggered alerts."""
        mock_summary_with_alerts = {
            'period': '2025-10',
            'budget': 100.0,
            'spent': 85.0,
            'remaining': 15.0,
            'percentage_used': 85.0,
            'alerts_triggered': 2,
            'last_alert_level': 'warning',
            'projections': {
                'projected_monthly_total': 170.0,
                'projected_overage': 70.0
            }
        }
        
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'get_monthly_summary',
            return_value=mock_summary_with_alerts
        ):
            result = financial_agent_with_tracking.get_monthly_summary()
            
            assert result['alerts_triggered'] == 2
            assert result['last_alert_level'] == 'warning'
    
    def test_get_monthly_summary_projections(self, financial_agent_with_tracking, mock_monthly_summary):
        """Test monthly summary includes projections."""
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'get_monthly_summary',
            return_value=mock_monthly_summary
        ):
            result = financial_agent_with_tracking.get_monthly_summary()
            
            assert 'projections' in result
            assert 'projected_monthly_total' in result['projections']
            assert 'daily_rate' in result['projections']


# ============================================================================
# TEST GET FINANCIAL SUMMARY
# ============================================================================

@pytest.mark.unit
class TestGetFinancialSummary:
    """Test financial summary string generation."""
    
    def test_financial_summary_basic(self, financial_agent):
        """Test basic financial summary without cost tracking."""
        summary = financial_agent.get_financial_summary()
        
        assert isinstance(summary, str)
        assert "financial summary" in summary.lower()
        assert "Cloud Spend" in summary
        assert "Mercury Bank Balance" in summary
    
    def test_financial_summary_with_cost_tracking(self, financial_agent_with_tracking):
        """Test financial summary with cost tracking data."""
        mock_summary = {
            'budget': 100.0,
            'spent': 45.67,
            'remaining': 54.33,
            'percentage_used': 45.7
        }
        
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'get_monthly_summary',
            return_value=mock_summary
        ):
            summary = financial_agent_with_tracking.get_financial_summary()
            
            assert "AI API Costs This Month" in summary
            assert "$100.00" in summary  # Budget
            assert "$45.67" in summary  # Spent
            assert "$54.33" in summary  # Remaining
            assert "45.7%" in summary  # Percentage
    
    def test_financial_summary_format(self, financial_agent_with_tracking):
        """Test financial summary formatting."""
        mock_summary = {
            'budget': 100.0,
            'spent': 75.50,
            'remaining': 24.50,
            'percentage_used': 75.5
        }
        
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'get_monthly_summary',
            return_value=mock_summary
        ):
            summary = financial_agent_with_tracking.get_financial_summary()
            
            # Verify formatting
            lines = summary.split('\n')
            assert len(lines) > 5  # Should have multiple lines
            assert any("Budget:" in line for line in lines)
            assert any("Spent:" in line for line in lines)
            assert any("Remaining:" in line for line in lines)


# ============================================================================
# TEST INTEGRATION WITH COST TRACKING
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestCostTrackingIntegration:
    """Test Financial Agent integration with Cost Tracking Service."""
    
    async def test_full_cost_monitoring_workflow(self, financial_agent_with_tracking):
        """Test complete cost monitoring workflow."""
        # Mock the cost tracking service methods
        mock_analysis = {
            'status': 'success',
            'monthly_budget': {
                'limit': 100.0,
                'spent': 50.0,
                'remaining': 50.0,
                'percentage_used': 50.0
            },
            'alert': None,
            'recommendations': ['Test recommendation']
        }
        
        mock_summary = {
            'period': '2025-10',
            'budget': 100.0,
            'spent': 50.0,
            'remaining': 50.0,
            'percentage_used': 50.0
        }
        
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'analyze_costs',
            new=AsyncMock(return_value=mock_analysis)
        ), patch.object(
            financial_agent_with_tracking.cost_tracking,
            'get_monthly_summary',
            return_value=mock_summary
        ):
            # Perform cost analysis
            analysis = await financial_agent_with_tracking.analyze_costs()
            assert analysis['status'] == 'success'
            
            # Get monthly summary
            summary = financial_agent_with_tracking.get_monthly_summary()
            assert summary['spent'] == 50.0
            
            # Get financial summary string
            financial_summary = financial_agent_with_tracking.get_financial_summary()
            assert "$50.00" in financial_summary
    
    async def test_alert_propagation(self, financial_agent_with_tracking):
        """Test budget alert propagation through agent."""
        mock_analysis_with_alert = {
            'status': 'success',
            'monthly_budget': {
                'limit': 100.0,
                'spent': 90.0,
                'remaining': 10.0,
                'percentage_used': 90.0
            },
            'alert': {
                'level': 'urgent',
                'message': 'URGENT: 90% of budget used',
                'recommendations': [
                    'Prioritize critical tasks only',
                    'Increase cache hit rate'
                ]
            },
            'recommendations': []
        }
        
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'analyze_costs',
            new=AsyncMock(return_value=mock_analysis_with_alert)
        ):
            analysis = await financial_agent_with_tracking.analyze_costs()
            
            # Verify alert propagated
            assert analysis['alert'] is not None
            assert analysis['alert']['level'] == 'urgent'
            assert 'URGENT' in analysis['alert']['message']
            assert len(analysis['alert']['recommendations']) > 0


# ============================================================================
# TEST ERROR HANDLING
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestErrorHandling:
    """Test error handling in Financial Agent."""
    
    async def test_analyze_costs_api_failure(self, financial_agent_with_tracking):
        """Test handling of API failures in cost analysis."""
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'analyze_costs',
            new=AsyncMock(return_value={
                'status': 'error',
                'message': 'Failed to fetch cost metrics'
            })
        ):
            result = await financial_agent_with_tracking.analyze_costs()
            
            assert result['status'] == 'error'
            assert 'Failed to fetch' in result['message']
    
    def test_get_monthly_summary_no_service(self, financial_agent):
        """Test monthly summary when service not initialized."""
        result = financial_agent.get_monthly_summary()
        
        assert result['status'] == 'error'
    
    def test_financial_summary_exception_handling(self, financial_agent_with_tracking):
        """Test financial summary handles exceptions gracefully."""
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'get_monthly_summary',
            side_effect=Exception("Tracking error")
        ):
            # Should not raise, should return basic summary
            summary = financial_agent_with_tracking.get_financial_summary()
            
            assert isinstance(summary, str)
            assert "financial summary" in summary.lower()


# ============================================================================
# TEST EDGE CASES
# ============================================================================

@pytest.mark.unit
class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_zero_spending(self, financial_agent_with_tracking):
        """Test with zero spending."""
        mock_summary = {
            'period': '2025-10',
            'budget': 100.0,
            'spent': 0.0,
            'remaining': 100.0,
            'percentage_used': 0.0
        }
        
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'get_monthly_summary',
            return_value=mock_summary
        ):
            result = financial_agent_with_tracking.get_monthly_summary()
            
            assert result['spent'] == 0.0
            assert result['percentage_used'] == 0.0
    
    def test_over_budget(self, financial_agent_with_tracking):
        """Test when spending exceeds budget."""
        mock_summary = {
            'period': '2025-10',
            'budget': 100.0,
            'spent': 120.0,
            'remaining': -20.0,
            'percentage_used': 120.0,
            'alerts_triggered': 3,
            'last_alert_level': 'critical'
        }
        
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'get_monthly_summary',
            return_value=mock_summary
        ):
            result = financial_agent_with_tracking.get_monthly_summary()
            
            assert result['spent'] > result['budget']
            assert result['remaining'] < 0
            assert result['last_alert_level'] == 'critical'
    
    def test_exactly_at_budget(self, financial_agent_with_tracking):
        """Test when spending exactly meets budget."""
        mock_summary = {
            'period': '2025-10',
            'budget': 100.0,
            'spent': 100.0,
            'remaining': 0.0,
            'percentage_used': 100.0,
            'last_alert_level': 'critical'
        }
        
        with patch.object(
            financial_agent_with_tracking.cost_tracking,
            'get_monthly_summary',
            return_value=mock_summary
        ):
            result = financial_agent_with_tracking.get_monthly_summary()
            
            assert result['spent'] == result['budget']
            assert result['remaining'] == 0.0
