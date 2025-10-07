import logging
from agents.financial_agent.services.mercury_client import MercuryClient
from agents.content_agent.services.firestore_client import FirestoreClient

class FinancialAgent:
    """
    An agent responsible for handling financial data, including fetching
    bank balances and cloud spending.
    """
    def __init__(self):
        self.mercury_client = MercuryClient()
        self.firestore_client = FirestoreClient()
        logging.info("Financial Agent initialized.")

    def get_financial_summary(self) -> str:
        """
        Generates a summary of the current financial status.
        """
        try:
            balance = self.mercury_client.get_account_balance()
            # Placeholder for fetching cloud spend from Firestore
            cloud_spend = self.get_cloud_spend()
            
            summary = (
                "Here is your financial summary:\\n"
                f"- **Mercury Bank Balance:** {balance}\\n"
                f"- **Last Week's Cloud Spend:** {cloud_spend}"
            )
            return summary
        except Exception as e:
            logging.error(f"Error generating financial summary: {e}")
            return "I'm sorry, I had trouble fetching the financial summary."

    def get_cloud_spend(self) -> str:
        """
        Fetches the cloud spend data from Firestore.
        (This is a placeholder and will need to be implemented)
        """
        # In a real implementation, you would query the 'financials' collection
        # in Firestore for entries related to GCP billing.
        logging.info("Fetching cloud spend from Firestore (mocked).")
        return "$7.89 (mocked)"
