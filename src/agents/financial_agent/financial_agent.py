import logging
from typing import Dict, Any, Optional
from .cost_tracking import CostTrackingService


class FinancialAgent:
    """
    A specialized agent for handling financial data and cost monitoring.
    
    Features:
    - Real-time AI API cost monitoring
    - Budget threshold alerts (75%, 90%, 100%)
    - Cost trend analysis and forecasting
    - Optimization recommendations
    - Monthly budget tracking ($100/month limit)
    """
    def __init__(
        self,
        cofounder_api_url: str = "http://localhost:8000",
        pubsub_client=None,
        enable_cost_tracking: bool = True
    ):
        """
        Initializes the FinancialAgent with cost tracking capabilities.
        
        Args:
            cofounder_api_url: URL for Co-Founder Agent API
            pubsub_client: Optional Pub/Sub client for alerts
            enable_cost_tracking: Whether to enable cost monitoring
        """
        logging.info("Financial Agent initialized with cost tracking")
        
        self.cost_tracking = None
        if enable_cost_tracking:
            self.cost_tracking = CostTrackingService(
                cofounder_api_url=cofounder_api_url,
                pubsub_client=pubsub_client,
                enable_notifications=True
            )
            logging.info("Cost tracking service enabled")

    async def analyze_costs(self) -> Dict[str, Any]:
        """
        Analyze current AI API costs and provide recommendations.
        
        Returns:
            Comprehensive cost analysis with alerts and recommendations
        """
        if not self.cost_tracking:
            return {
                'status': 'error',
                'message': 'Cost tracking not enabled'
            }
        
        return await self.cost_tracking.analyze_costs()
    
    def get_monthly_summary(self) -> Dict[str, Any]:
        """
        Get monthly cost summary.
        
        Returns:
            Monthly spending summary with projections
        """
        if not self.cost_tracking:
            return {
                'status': 'error',
                'message': 'Cost tracking not enabled'
            }
        
        return self.cost_tracking.get_monthly_summary()

    def get_financial_summary(self) -> str:
        """
        Provides a summary of the firm's financial status.

        In the future, this will fetch real data from sources like the
        Mercury Bank API and GCP Billing. For now, it returns mock data.
        """
        # Mock data for demonstration purposes
        mock_summary = {
            "cloud_spend_last_week": "$15.72",
            "mercury_balance": "$1,234.56",
            "burn_rate_monthly": "$250.00 (estimated)",
        }

        response = (
            "Here is your financial summary:\\n"
            f"- Cloud Spend (Last 7 Days): {mock_summary['cloud_spend_last_week']}\\n"
            f"- Mercury Bank Balance: {mock_summary['mercury_balance']}\\n"
            f"- Estimated Monthly Burn Rate: {mock_summary['burn_rate_monthly']}"
        )
        
        # Add AI cost summary if available
        if self.cost_tracking:
            summary = self.cost_tracking.get_monthly_summary()
            response += (
                f"\\n\\nAI API Costs This Month:\\n"
                f"- Budget: ${summary['budget']:.2f}\\n"
                f"- Spent: ${summary['spent']:.2f}\\n"
                f"- Remaining: ${summary['remaining']:.2f}\\n"
                f"- Usage: {summary['percentage_used']:.1f}%"
            )
        
        return response
