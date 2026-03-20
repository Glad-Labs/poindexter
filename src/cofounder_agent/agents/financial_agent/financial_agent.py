import logging
import os

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

        Reads from MERCURY_API_KEY (bank balance) and GCP_BILLING_PROJECT
        (cloud spend) when configured. Returns an unavailable notice otherwise.
        """
        missing = []
        if not os.getenv("MERCURY_API_KEY"):
            missing.append("MERCURY_API_KEY")
        if not os.getenv("GCP_BILLING_PROJECT"):
            missing.append("GCP_BILLING_PROJECT")

        if missing:
            logging.warning(
                "Financial summary unavailable — configure %s to enable",
                ", ".join(missing),
            )
            return (
                "Financial data unavailable. "
                f"Configure {', '.join(missing)} to enable real-time financial reporting."
            )

        # Real API integrations would be called here when keys are present.
        # Placeholder: return unavailable until Mercury and GCP integrations are implemented.
        return "Financial data unavailable. Real-time integration not yet implemented."
