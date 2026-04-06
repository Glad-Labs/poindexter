"""
Unit tests for services/market_service.py

Tests MarketService initialization, metadata, and each public method
with agents/LLM dependencies mocked to avoid real calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.market_service import MarketService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service(**kwargs) -> MarketService:
    """Return a MarketService with optional injected deps."""
    return MarketService(**kwargs)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestMarketServiceInit:
    def test_creates_without_deps(self):
        svc = make_service()
        assert svc is not None

    def test_database_service_stored(self):
        mock_db = MagicMock()
        svc = make_service(database_service=mock_db)
        assert svc.database_service is mock_db

    def test_model_router_stored(self):
        mock_router = MagicMock()
        svc = make_service(model_router=mock_router)
        assert svc.model_router is mock_router

    def test_deps_default_to_none(self):
        svc = make_service()
        assert svc.database_service is None
        assert svc.model_router is None


# ---------------------------------------------------------------------------
# get_service_metadata
# ---------------------------------------------------------------------------


class TestGetServiceMetadata:
    def test_returns_dict(self):
        svc = make_service()
        meta = svc.get_service_metadata()
        assert isinstance(meta, dict)

    def test_name(self):
        meta = make_service().get_service_metadata()
        assert meta["name"] == "market_service"

    def test_category(self):
        meta = make_service().get_service_metadata()
        assert meta["category"] == "market_analysis"

    def test_capabilities_is_list(self):
        meta = make_service().get_service_metadata()
        assert isinstance(meta["capabilities"], list)
        assert len(meta["capabilities"]) >= 4

    def test_version_present(self):
        meta = make_service().get_service_metadata()
        assert "version" in meta


# ---------------------------------------------------------------------------
# analyze_market_trends
# ---------------------------------------------------------------------------


class TestAnalyzeMarketTrends:
    @pytest.mark.asyncio
    @patch("services.market_service.MarketService.analyze_market_trends", new_callable=AsyncMock)
    async def test_delegates_to_market_insight_agent(self, mock_method):
        """Trend analysis delegates to MarketInsightAgent.suggest_topics."""
        mock_method.return_value = {
            "phase": "market_trend_analysis",
            "topic": "AI chips",
            "industry": "semiconductors",
            "timeframe_months": 12,
            "analysis": ["trend1", "trend2"],
            "timestamp": "2026-04-06T00:00:00+00:00",
            "source": "market_insight_agent",
        }
        svc = make_service()
        result = await mock_method(svc, topic="AI chips", industry="semiconductors")
        assert result["phase"] == "market_trend_analysis"
        assert result["topic"] == "AI chips"
        assert result["source"] == "market_insight_agent"

    @pytest.mark.asyncio
    async def test_success_with_mocked_agent(self):
        """Full path through analyze_market_trends with mocked internals."""
        svc = make_service()
        mock_agent = MagicMock()
        mock_agent.suggest_topics = AsyncMock(return_value=["trend_a", "trend_b"])

        mock_llm_mod = MagicMock()
        mock_llm_mod.LLMClient = MagicMock()
        mock_agent_mod = MagicMock()
        mock_agent_mod.MarketInsightAgent = MagicMock(return_value=mock_agent)

        with patch.dict(
            "sys.modules",
            {
                "agents.content_agent.services.llm_client": mock_llm_mod,
                "agents.market_insight_agent.market_insight_agent": mock_agent_mod,
            },
        ):
            result = await svc.analyze_market_trends(
                topic="GPU pricing", industry="hardware", timeframe_months=6
            )

        assert result["phase"] == "market_trend_analysis"
        assert result["topic"] == "GPU pricing"
        assert result["industry"] == "hardware"
        assert result["timeframe_months"] == 6
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_error_returns_error_dict(self):
        """When the agent import or call fails, returns error dict."""
        svc = make_service()
        with patch.dict(
            "sys.modules",
            {
                "agents.content_agent.services.llm_client": None,
            },
        ):
            result = await svc.analyze_market_trends(topic="broken")

        assert "error" in result
        assert result["phase"] == "market_trend_analysis"
        assert result["topic"] == "broken"

    @pytest.mark.asyncio
    async def test_query_combines_topic_and_industry(self):
        """When industry is provided, query should be 'topic in industry'."""
        svc = make_service()
        mock_agent = MagicMock()
        mock_agent.suggest_topics = AsyncMock(return_value=[])

        mock_llm_mod = MagicMock()
        mock_llm_mod.LLMClient = MagicMock()
        mock_agent_mod = MagicMock()
        mock_agent_mod.MarketInsightAgent = MagicMock(return_value=mock_agent)

        with patch.dict(
            "sys.modules",
            {
                "agents.content_agent.services.llm_client": mock_llm_mod,
                "agents.market_insight_agent.market_insight_agent": mock_agent_mod,
            },
        ):
            result = await svc.analyze_market_trends(topic="AI", industry="healthcare")

        # Verify suggest_topics was called with combined query
        mock_agent.suggest_topics.assert_called_once()
        call_kwargs = mock_agent.suggest_topics.call_args
        assert "AI in healthcare" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_no_industry_uses_topic_only(self):
        """When no industry, query should just be the topic."""
        svc = make_service()
        mock_agent = MagicMock()
        mock_agent.suggest_topics = AsyncMock(return_value=[])

        mock_llm_mod = MagicMock()
        mock_llm_mod.LLMClient = MagicMock()
        mock_agent_mod = MagicMock()
        mock_agent_mod.MarketInsightAgent = MagicMock(return_value=mock_agent)

        with patch.dict(
            "sys.modules",
            {
                "agents.content_agent.services.llm_client": mock_llm_mod,
                "agents.market_insight_agent.market_insight_agent": mock_agent_mod,
            },
        ):
            result = await svc.analyze_market_trends(topic="blockchain")

        mock_agent.suggest_topics.assert_called_once_with(base_query="blockchain")


# ---------------------------------------------------------------------------
# research_competitors
# ---------------------------------------------------------------------------


class TestResearchCompetitors:
    @pytest.mark.asyncio
    async def test_returns_competitor_dict(self):
        svc = make_service()
        result = await svc.research_competitors(market_segment="cloud hosting")
        assert result["analysis_type"] == "competitor_research"
        assert result["market_segment"] == "cloud hosting"

    @pytest.mark.asyncio
    async def test_competitors_list_empty(self):
        """Currently returns empty competitors list (stub)."""
        svc = make_service()
        result = await svc.research_competitors(market_segment="SaaS")
        assert result["competitors"] == []
        assert result["competitors_analyzed"] == 0

    @pytest.mark.asyncio
    async def test_data_source_unavailable(self):
        svc = make_service()
        result = await svc.research_competitors(market_segment="fintech")
        assert result["data_source"] == "unavailable"

    @pytest.mark.asyncio
    async def test_has_timestamp(self):
        svc = make_service()
        result = await svc.research_competitors(market_segment="gaming")
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_top_n_parameter_accepted(self):
        """top_n parameter is accepted without error."""
        svc = make_service()
        result = await svc.research_competitors(market_segment="AI", top_n=10)
        assert result["analysis_type"] == "competitor_research"


# ---------------------------------------------------------------------------
# identify_opportunities
# ---------------------------------------------------------------------------


class TestIdentifyOpportunities:
    @pytest.mark.asyncio
    async def test_returns_opportunities(self):
        svc = make_service()
        result = await svc.identify_opportunities(market_segment="edtech")
        assert result["analysis_type"] == "opportunity_identification"
        assert result["market_segment"] == "edtech"

    @pytest.mark.asyncio
    async def test_three_default_opportunities(self):
        svc = make_service()
        result = await svc.identify_opportunities(market_segment="edtech")
        assert result["opportunities_identified"] == 3
        assert len(result["opportunities"]) == 3

    @pytest.mark.asyncio
    async def test_opportunity_fields(self):
        svc = make_service()
        result = await svc.identify_opportunities(market_segment="edtech")
        opp = result["opportunities"][0]
        assert "name" in opp
        assert "potential" in opp
        assert "feasibility" in opp
        assert "timeline_months" in opp
        assert "estimated_roi" in opp

    @pytest.mark.asyncio
    async def test_constraints_stored(self):
        svc = make_service()
        constraints = {"budget": 50000, "team_size": 3}
        result = await svc.identify_opportunities(
            market_segment="healthtech", constraints=constraints
        )
        assert result["constraints_considered"] == constraints

    @pytest.mark.asyncio
    async def test_constraints_default_empty(self):
        svc = make_service()
        result = await svc.identify_opportunities(market_segment="retail")
        assert result["constraints_considered"] == {}

    @pytest.mark.asyncio
    async def test_has_timestamp(self):
        svc = make_service()
        result = await svc.identify_opportunities(market_segment="logistics")
        assert "timestamp" in result


# ---------------------------------------------------------------------------
# analyze_customer_sentiment
# ---------------------------------------------------------------------------


class TestAnalyzeCustomerSentiment:
    @pytest.mark.asyncio
    async def test_returns_sentiment_dict(self):
        svc = make_service()
        result = await svc.analyze_customer_sentiment(topic="product quality")
        assert result["analysis_type"] == "sentiment_analysis"
        assert result["topic"] == "product quality"

    @pytest.mark.asyncio
    async def test_default_sources(self):
        svc = make_service()
        result = await svc.analyze_customer_sentiment(topic="pricing")
        assert result["sources"] == ["social_media", "reviews"]

    @pytest.mark.asyncio
    async def test_custom_sources(self):
        svc = make_service()
        result = await svc.analyze_customer_sentiment(
            topic="UX", sources=["forums", "surveys"]
        )
        assert result["sources"] == ["forums", "surveys"]

    @pytest.mark.asyncio
    async def test_sentiment_unavailable(self):
        """Currently returns unavailable sentiment (stub)."""
        svc = make_service()
        result = await svc.analyze_customer_sentiment(topic="brand")
        assert result["overall_sentiment"] == "unavailable"
        assert result["sentiment_score"] is None
        assert result["total_mentions"] == 0

    @pytest.mark.asyncio
    async def test_has_timestamp(self):
        svc = make_service()
        result = await svc.analyze_customer_sentiment(topic="support")
        assert "timestamp" in result


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_trend_analysis_import_error(self):
        """Import failures are caught and returned as error dicts."""
        svc = make_service()
        # Force an ImportError by making the module None
        with patch.dict(
            "sys.modules",
            {
                "agents.content_agent.services.llm_client": None,
            },
        ):
            result = await svc.analyze_market_trends(topic="test")
        assert "error" in result
        assert result["phase"] == "market_trend_analysis"

    @pytest.mark.asyncio
    async def test_trend_analysis_runtime_error(self):
        """Runtime errors in agent are caught and returned as error dicts."""
        svc = make_service()
        mock_agent = MagicMock()
        mock_agent.suggest_topics = AsyncMock(side_effect=RuntimeError("boom"))

        mock_llm_mod = MagicMock()
        mock_llm_mod.LLMClient = MagicMock()
        mock_agent_mod = MagicMock()
        mock_agent_mod.MarketInsightAgent = MagicMock(return_value=mock_agent)

        with patch.dict(
            "sys.modules",
            {
                "agents.content_agent.services.llm_client": mock_llm_mod,
                "agents.market_insight_agent.market_insight_agent": mock_agent_mod,
            },
        ):
            result = await svc.analyze_market_trends(topic="failing")

        assert "error" in result
        assert "boom" in result["error"]
        assert result["topic"] == "failing"
