"""
Unit tests for agents/content_agent/orchestrator.py

Tests for ContentAgentOrchestrator class.
"""

from unittest.mock import patch

import pytest

from agents.content_agent.orchestrator import ContentAgentOrchestrator


class TestContentAgentOrchestratorInit:
    def test_default_api_url(self):
        orch = ContentAgentOrchestrator()
        assert orch.api_url == "http://localhost:8000"

    def test_custom_api_url(self):
        orch = ContentAgentOrchestrator(api_url="http://staging:8080")
        assert orch.api_url == "http://staging:8080"

    def test_initial_is_running_false(self):
        orch = ContentAgentOrchestrator()
        assert orch.is_running is False

    def test_initial_pubsub_client_none(self):
        orch = ContentAgentOrchestrator()
        assert orch.pubsub_client is None

    def test_logs_info_on_init(self):
        with patch("agents.content_agent.orchestrator.logger") as mock_logger:
            ContentAgentOrchestrator(api_url="http://localhost:8000")
            mock_logger.info.assert_called()

    def test_api_url_in_log_message(self):
        with patch("agents.content_agent.orchestrator.logger") as mock_logger:
            ContentAgentOrchestrator(api_url="http://custom:9000")
            call_args = str(mock_logger.info.call_args)
            assert "http://custom:9000" in call_args


class TestStartPolling:
    @pytest.mark.asyncio
    async def test_sets_is_running_true(self):
        orch = ContentAgentOrchestrator()
        assert orch.is_running is False
        # start_polling only sets is_running and logs — it does not loop
        await orch.start_polling(interval=1)
        assert orch.is_running is True

    @pytest.mark.asyncio
    async def test_logs_polling_start(self):
        orch = ContentAgentOrchestrator()
        with patch("agents.content_agent.orchestrator.logger") as mock_logger:
            await orch.start_polling(interval=60)
            mock_logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_custom_interval_in_log(self):
        orch = ContentAgentOrchestrator()
        with patch("agents.content_agent.orchestrator.logger") as mock_logger:
            await orch.start_polling(interval=120)
            all_calls = str(mock_logger.info.call_args_list)
            assert "120" in all_calls
