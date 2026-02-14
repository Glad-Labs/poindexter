import logging

from ..content_agent.utils.tools import CrewAIToolsFactory


class FinancialAgent:
    """
    A specialized agent for handling financial data.
    """

    def __init__(self):
        """Initializes the FinancialAgent."""
        logging.info("Financial Agent initialized.")
        self.tools = [
            CrewAIToolsFactory.get_web_search_tool(),
            CrewAIToolsFactory.get_data_processing_tool(),
        ]

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
        return response
