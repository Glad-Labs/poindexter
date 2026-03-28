"""
Unit tests for agents/financial_agent/financial_agent.py

Tests for FinancialAgent class.
"""

from unittest.mock import MagicMock, patch

from agents.financial_agent.financial_agent import FinancialAgent


class TestFinancialAgentInit:
    def test_init_creates_tools_list(self):
        with patch("agents.financial_agent.financial_agent.CrewAIToolsFactory") as mock_factory:
            mock_factory.get_web_search_tool.return_value = MagicMock()
            mock_factory.get_data_processing_tool.return_value = MagicMock()
            agent = FinancialAgent()
        assert hasattr(agent, "tools")
        assert len(agent.tools) == 2

    def test_init_logs_info(self):
        with (
            patch("agents.financial_agent.financial_agent.logging") as mock_logging,
            patch("agents.financial_agent.financial_agent.CrewAIToolsFactory"),
        ):
            FinancialAgent()
            mock_logging.info.assert_called()


class TestGetFinancialSummary:
    def _make_agent(self):
        with patch("agents.financial_agent.financial_agent.CrewAIToolsFactory"):
            return FinancialAgent()

    def test_returns_unavailable_when_no_env_vars(self):
        agent = self._make_agent()
        with patch.dict("os.environ", {}, clear=False):
            # Ensure both keys are absent
            import os

            os.environ.pop("MERCURY_API_KEY", None)
            os.environ.pop("GCP_BILLING_PROJECT", None)
            result = agent.get_financial_summary()
        assert "unavailable" in result.lower() or "Financial data" in result

    def test_returns_string(self):
        agent = self._make_agent()
        with patch("os.getenv", return_value=None):
            result = agent.get_financial_summary()
        assert isinstance(result, str)

    def test_lists_missing_keys_in_message(self):
        agent = self._make_agent()
        import os

        os.environ.pop("MERCURY_API_KEY", None)
        os.environ.pop("GCP_BILLING_PROJECT", None)
        result = agent.get_financial_summary()
        # Should mention the missing keys
        assert "MERCURY_API_KEY" in result or "GCP_BILLING_PROJECT" in result

    def test_with_both_env_vars_set(self):
        agent = self._make_agent()
        with patch.dict("os.environ", {"MERCURY_API_KEY": "key1", "GCP_BILLING_PROJECT": "proj1"}):
            result = agent.get_financial_summary()
        # When both are set, the stub still returns unavailable (not yet implemented)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_logs_warning_for_missing_keys(self):
        agent = self._make_agent()
        import os

        os.environ.pop("MERCURY_API_KEY", None)
        os.environ.pop("GCP_BILLING_PROJECT", None)
        with patch("agents.financial_agent.financial_agent.logging") as mock_logging:
            agent.get_financial_summary()
            mock_logging.warning.assert_called()
