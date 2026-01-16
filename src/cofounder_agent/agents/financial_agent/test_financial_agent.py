import pytest
from src.agents.financial_agent.financial_agent import FinancialAgent


def test_get_financial_summary():
    """
    Tests that the get_financial_summary method returns a formatted string
    containing the expected mock data.
    """
    agent = FinancialAgent()
    summary = agent.get_financial_summary()

    assert "Cloud Spend (Last 7 Days): $15.72" in summary
    assert "Mercury Bank Balance: $1,234.56" in summary
    assert "Estimated Monthly Burn Rate: $250.00 (estimated)" in summary
