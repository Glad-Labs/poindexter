"""
Unit tests for agents/market_insight_agent/market_insight_agent.py

Tests for MarketInsightAgent class.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.market_insight_agent.market_insight_agent import MarketInsightAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent():
    """Build a MarketInsightAgent with fully mocked dependencies."""
    mock_llm = AsyncMock()
    mock_llm.generate_text = AsyncMock(return_value="1. AI trends\n2. ML pipelines\n3. LLM ops")
    mock_llm.generate_json = AsyncMock(
        return_value={
            "ideas": [
                {
                    "topic": "AI for SMBs",
                    "primary_keyword": "AI tools",
                    "target_audience": "small business owners",
                    "category": "Technology",
                }
            ]
        }
    )

    with patch(
        "agents.market_insight_agent.market_insight_agent.ResearchAgent"
    ) as mock_research_cls, patch(
        "agents.market_insight_agent.market_insight_agent.CrewAIToolsFactory"
    ) as mock_factory:
        mock_research_cls.return_value = AsyncMock()
        mock_factory.get_market_agent_tools.return_value = [MagicMock()]
        agent = MarketInsightAgent(llm_client=mock_llm)

    return agent, mock_llm


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestMarketInsightAgentInit:
    def test_stores_llm_client(self):
        agent, mock_llm = _make_agent()
        assert agent.llm_client is mock_llm

    def test_research_agent_created(self):
        agent, _ = _make_agent()
        assert agent.research_agent is not None

    def test_tools_list_populated(self):
        agent, _ = _make_agent()
        assert len(agent.tools) > 0

    def test_logs_info_on_init(self):
        with patch("agents.market_insight_agent.market_insight_agent.logging") as mock_logging, \
             patch("agents.market_insight_agent.market_insight_agent.ResearchAgent"), \
             patch("agents.market_insight_agent.market_insight_agent.CrewAIToolsFactory"):
            mock_llm = AsyncMock()
            MarketInsightAgent(llm_client=mock_llm)
            mock_logging.info.assert_called()


# ---------------------------------------------------------------------------
# suggest_topics
# ---------------------------------------------------------------------------


class TestSuggestTopics:
    @pytest.mark.asyncio
    async def test_returns_string_with_base_query(self):
        agent, mock_llm = _make_agent()
        mock_research = AsyncMock()
        mock_research.run = AsyncMock(return_value="research results")
        agent.research_agent = mock_research

        result = await agent.suggest_topics("machine learning")
        assert isinstance(result, str)
        assert "machine learning" in result

    @pytest.mark.asyncio
    async def test_calls_research_agent(self):
        agent, mock_llm = _make_agent()
        mock_research = AsyncMock()
        mock_research.run = AsyncMock(return_value="some research")
        agent.research_agent = mock_research

        await agent.suggest_topics("blockchain")

        mock_research.run.assert_called_once_with("blockchain", [])

    @pytest.mark.asyncio
    async def test_calls_llm_generate_text(self):
        agent, mock_llm = _make_agent()
        mock_research = AsyncMock()
        mock_research.run = AsyncMock(return_value="research data")
        agent.research_agent = mock_research

        await agent.suggest_topics("cloud computing")

        mock_llm.generate_text.assert_called_once()
        call_args = mock_llm.generate_text.call_args[0][0]
        assert "cloud computing" in call_args

    @pytest.mark.asyncio
    async def test_returns_error_message_on_exception(self):
        agent, mock_llm = _make_agent()
        mock_research = AsyncMock()
        mock_research.run = AsyncMock(side_effect=RuntimeError("network error"))
        agent.research_agent = mock_research

        result = await agent.suggest_topics("any topic")
        assert isinstance(result, str)
        assert "sorry" in result.lower() or "trouble" in result.lower()

    @pytest.mark.asyncio
    async def test_logs_error_on_exception(self):
        agent, mock_llm = _make_agent()
        mock_research = AsyncMock()
        mock_research.run = AsyncMock(side_effect=Exception("crash"))
        agent.research_agent = mock_research

        with patch("agents.market_insight_agent.market_insight_agent.logging") as mock_logging:
            await agent.suggest_topics("topic")
            mock_logging.error.assert_called()


# ---------------------------------------------------------------------------
# create_tasks_from_trends
# ---------------------------------------------------------------------------


class TestCreateTasksFromTrends:
    @pytest.mark.asyncio
    async def test_returns_string_result(self):
        agent, mock_llm = _make_agent()
        result = await agent.create_tasks_from_trends("generative AI")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_result_mentions_trend(self):
        agent, mock_llm = _make_agent()
        result = await agent.create_tasks_from_trends("generative AI")
        assert "generative AI" in result

    @pytest.mark.asyncio
    async def test_result_mentions_count(self):
        agent, mock_llm = _make_agent()
        # mock_llm.generate_json returns 1 idea
        result = await agent.create_tasks_from_trends("blockchain")
        # The result should mention how many ideas were generated
        assert "1" in result

    @pytest.mark.asyncio
    async def test_calls_llm_generate_json(self):
        agent, mock_llm = _make_agent()
        await agent.create_tasks_from_trends("quantum computing")
        mock_llm.generate_json.assert_called_once()
        call_args = mock_llm.generate_json.call_args[0][0]
        assert "quantum computing" in call_args

    @pytest.mark.asyncio
    async def test_empty_ideas_list(self):
        agent, mock_llm = _make_agent()
        mock_llm.generate_json = AsyncMock(return_value={"ideas": []})
        result = await agent.create_tasks_from_trends("any trend")
        assert isinstance(result, str)
        assert "0" in result

    @pytest.mark.asyncio
    async def test_returns_error_message_on_exception(self):
        agent, mock_llm = _make_agent()
        mock_llm.generate_json = AsyncMock(side_effect=RuntimeError("LLM down"))
        result = await agent.create_tasks_from_trends("trend")
        assert "sorry" in result.lower() or "trouble" in result.lower()

    @pytest.mark.asyncio
    async def test_logs_error_on_exception(self):
        agent, mock_llm = _make_agent()
        mock_llm.generate_json = AsyncMock(side_effect=Exception("crash"))
        with patch("agents.market_insight_agent.market_insight_agent.logging") as mock_logging:
            await agent.create_tasks_from_trends("trend")
            mock_logging.error.assert_called()

    @pytest.mark.asyncio
    async def test_logs_info_for_generated_suggestions(self):
        agent, mock_llm = _make_agent()
        with patch("agents.market_insight_agent.market_insight_agent.logging") as mock_logging:
            await agent.create_tasks_from_trends("AI")
            mock_logging.info.assert_called()
