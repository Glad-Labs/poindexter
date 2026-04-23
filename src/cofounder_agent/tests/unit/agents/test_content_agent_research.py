"""
Unit tests for agents/content_agent/agents/research_agent.py

Tests focus on:
- _WorkflowResearchAgentAdapter.run(): input extraction, error handling
- ResearchAgent initialization without SERPER_API_KEY raises ValueError
- ResearchAgent.run(): HTTP error handling, empty result on failure
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")

from services.site_config import SiteConfig  # noqa: E402

_RA_SC = SiteConfig()


# ---------------------------------------------------------------------------
# ResearchAgent — initialization
# ---------------------------------------------------------------------------


class TestResearchAgentInit:
    def test_raises_when_serper_api_key_missing(self):
        with patch("agents.content_agent.agents.research_agent.config") as mock_config:
            mock_config.SERPER_API_KEY = None
            with patch("agents.content_agent.agents.research_agent.CrewAIToolsFactory"):
                from agents.content_agent.agents.research_agent import ResearchAgent

                with pytest.raises(ValueError, match="SERPER_API_KEY"):
                    ResearchAgent(site_config=_RA_SC)

    def test_initializes_with_serper_api_key(self):
        with (
            patch("agents.content_agent.agents.research_agent.config") as mock_config,
            patch("agents.content_agent.agents.research_agent.CrewAIToolsFactory") as mock_tools,
            patch("agents.content_agent.agents.research_agent.ResearchQualityService"),
        ):
            mock_config.SERPER_API_KEY = "test-serper-key"
            mock_tools.get_research_agent_tools.return_value = []

            from agents.content_agent.agents.research_agent import ResearchAgent

            agent = ResearchAgent(site_config=_RA_SC)
            assert agent.serper_api_key == "test-serper-key"

    def test_uses_empty_tools_when_factory_raises(self):
        with (
            patch("agents.content_agent.agents.research_agent.config") as mock_config,
            patch("agents.content_agent.agents.research_agent.CrewAIToolsFactory") as mock_tools,
            patch("agents.content_agent.agents.research_agent.ResearchQualityService"),
        ):
            mock_config.SERPER_API_KEY = "key"
            mock_tools.get_research_agent_tools.side_effect = RuntimeError("Tools failed")

            from agents.content_agent.agents.research_agent import ResearchAgent

            agent = ResearchAgent(site_config=_RA_SC)
            assert agent.tools == []


# ---------------------------------------------------------------------------
# ResearchAgent.run() — HTTP error handling
# ---------------------------------------------------------------------------


class TestResearchAgentRun:
    @pytest.mark.asyncio
    async def test_returns_empty_string_on_http_error(self):
        import httpx

        with (
            patch("agents.content_agent.agents.research_agent.config") as mock_config,
            patch("agents.content_agent.agents.research_agent.CrewAIToolsFactory") as mock_tools,
            patch("agents.content_agent.agents.research_agent.ResearchQualityService"),
        ):
            mock_config.SERPER_API_KEY = "test-key"
            mock_tools.get_research_agent_tools.return_value = []

            from agents.content_agent.agents.research_agent import ResearchAgent

            agent = ResearchAgent(site_config=_RA_SC)

            # Patch httpx.AsyncClient to raise HTTP error
            with patch(
                "agents.content_agent.agents.research_agent.httpx.AsyncClient"
            ) as mock_client:
                mock_response = AsyncMock()
                mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "404", request=MagicMock(), response=MagicMock()
                )
                mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_response)
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await agent.run("AI trends", ["machine learning"])

        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_empty_string_on_unexpected_error(self):
        with (
            patch("agents.content_agent.agents.research_agent.config") as mock_config,
            patch("agents.content_agent.agents.research_agent.CrewAIToolsFactory") as mock_tools,
            patch("agents.content_agent.agents.research_agent.ResearchQualityService"),
        ):
            mock_config.SERPER_API_KEY = "test-key"
            mock_tools.get_research_agent_tools.return_value = []

            from agents.content_agent.agents.research_agent import ResearchAgent

            agent = ResearchAgent(site_config=_RA_SC)

            with patch(
                "agents.content_agent.agents.research_agent.httpx.AsyncClient"
            ) as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(
                    side_effect=RuntimeError("Network failure")
                )
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await agent.run("topic", [])

        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_formatted_context_on_success(self):
        with (
            patch("agents.content_agent.agents.research_agent.config") as mock_config,
            patch("agents.content_agent.agents.research_agent.CrewAIToolsFactory") as mock_tools,
            patch(
                "agents.content_agent.agents.research_agent.ResearchQualityService"
            ) as mock_rqs_class,
        ):
            mock_config.SERPER_API_KEY = "test-key"
            mock_tools.get_research_agent_tools.return_value = []

            mock_rqs = MagicMock()
            mock_rqs.filter_and_score.return_value = [
                {"title": "Article 1", "snippet": "Summary 1"},
            ]
            mock_rqs.format_context.return_value = "Formatted research context"
            mock_rqs_class.return_value = mock_rqs

            from agents.content_agent.agents.research_agent import ResearchAgent

            agent = ResearchAgent(site_config=_RA_SC)

            mock_post_response = MagicMock()
            mock_post_response.raise_for_status.return_value = None
            mock_post_response.json.return_value = {
                "organic": [{"title": "Art 1", "snippet": "Snip 1"}]
            }

            with patch(
                "agents.content_agent.agents.research_agent.httpx.AsyncClient"
            ) as mock_client:
                mock_async_client = AsyncMock()
                mock_async_client.post = AsyncMock(return_value=mock_post_response)
                mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_async_client)
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await agent.run("AI trends", ["ml"])

        assert result == "Formatted research context"


# ---------------------------------------------------------------------------
# _WorkflowResearchAgentAdapter
# ---------------------------------------------------------------------------


class TestWorkflowResearchAgentAdapter:
    @pytest.mark.asyncio
    async def test_extracts_topic_from_inputs(self):
        with (
            patch("agents.content_agent.agents.research_agent.config") as mock_config,
            patch("agents.content_agent.agents.research_agent.CrewAIToolsFactory") as mock_tools,
            patch("agents.content_agent.agents.research_agent.ResearchQualityService"),
        ):
            mock_config.SERPER_API_KEY = "key"
            mock_tools.get_research_agent_tools.return_value = []

            from agents.content_agent.agents.research_agent import _WorkflowResearchAgentAdapter

            adapter = _WorkflowResearchAgentAdapter()
            adapter._agent = AsyncMock()
            adapter._agent.run = AsyncMock(return_value="context data")

            result = await adapter.run({"topic": "Blockchain", "keywords": ["crypto"]})

            adapter._agent.run.assert_awaited_once_with(topic="Blockchain", keywords=["crypto"])
            assert result["status"] == "success"
            assert result["research_data"] == "context data"

    @pytest.mark.asyncio
    async def test_falls_back_to_prompt_when_topic_missing(self):
        with (
            patch("agents.content_agent.agents.research_agent.config") as mock_config,
            patch("agents.content_agent.agents.research_agent.CrewAIToolsFactory") as mock_tools,
            patch("agents.content_agent.agents.research_agent.ResearchQualityService"),
        ):
            mock_config.SERPER_API_KEY = "key"
            mock_tools.get_research_agent_tools.return_value = []

            from agents.content_agent.agents.research_agent import _WorkflowResearchAgentAdapter

            adapter = _WorkflowResearchAgentAdapter()
            adapter._agent = AsyncMock()
            adapter._agent.run = AsyncMock(return_value="")

            await adapter.run({"prompt": "Fallback topic", "keywords": []})

            adapter._agent.run.assert_awaited_once_with(topic="Fallback topic", keywords=[])

    @pytest.mark.asyncio
    async def test_returns_success_when_agent_is_none(self):
        with (
            patch("agents.content_agent.agents.research_agent.config") as mock_config,
            patch("agents.content_agent.agents.research_agent.CrewAIToolsFactory"),
            patch("agents.content_agent.agents.research_agent.ResearchQualityService"),
        ):
            mock_config.SERPER_API_KEY = None  # Forces init error

            from agents.content_agent.agents.research_agent import _WorkflowResearchAgentAdapter

            adapter = _WorkflowResearchAgentAdapter()
            # _agent is None due to init failure

            result = await adapter.run({"topic": "Any topic"})

            assert result["status"] == "success"
            assert result["research_data"] == ""
            assert "unavailable" in result.get("notes", "").lower()

    @pytest.mark.asyncio
    async def test_normalizes_string_keywords_to_list(self):
        with (
            patch("agents.content_agent.agents.research_agent.config") as mock_config,
            patch("agents.content_agent.agents.research_agent.CrewAIToolsFactory") as mock_tools,
            patch("agents.content_agent.agents.research_agent.ResearchQualityService"),
        ):
            mock_config.SERPER_API_KEY = "key"
            mock_tools.get_research_agent_tools.return_value = []

            from agents.content_agent.agents.research_agent import _WorkflowResearchAgentAdapter

            adapter = _WorkflowResearchAgentAdapter()
            adapter._agent = AsyncMock()
            adapter._agent.run = AsyncMock(return_value="")

            await adapter.run({"topic": "topic", "keywords": "single-keyword"})

            call_kwargs = adapter._agent.run.call_args
            assert isinstance(call_kwargs.kwargs["keywords"], list)
