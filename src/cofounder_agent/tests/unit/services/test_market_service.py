"""
Unit tests for services/market_service.py (MarketService).

Tests cover:
- MarketService initialization (default and with deps)
- analyze_market_trends — agent available (mocked), ImportError path (swallowed)
- research_competitors — always returns "unavailable" structure (no search API)
- identify_opportunities — returns fixed opportunity list, constraints preserved
- analyze_customer_sentiment — always returns "unavailable" structure (no sentiment API)
- get_service_metadata — structure validation

No real LLM calls, no DB.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.market_service import MarketService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service(**kwargs) -> MarketService:
    return MarketService(**kwargs)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestMarketServiceInit:
    def test_default_init(self):
        svc = MarketService()
        assert svc.database_service is None
        assert svc.model_router is None

    def test_init_with_deps(self):
        db = MagicMock()
        router = MagicMock()
        svc = MarketService(database_service=db, model_router=router)
        assert svc.database_service is db
        assert svc.model_router is router


# ---------------------------------------------------------------------------
# analyze_market_trends
# ---------------------------------------------------------------------------


class TestAnalyzeMarketTrends:
    @pytest.mark.asyncio
    async def test_success_via_agent(self):
        mock_agent = MagicMock()
        mock_agent.suggest_topics = AsyncMock(return_value=["AI trend A", "AI trend B"])
        mock_agent_class = MagicMock(return_value=mock_agent)
        mock_llm_class = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules",
            {
                "agents.market_insight_agent.market_insight_agent": MagicMock(
                    MarketInsightAgent=mock_agent_class
                ),
                "agents.content_agent.services.llm_client": MagicMock(
                    LLMClient=mock_llm_class
                ),
            },
        ):
            svc = _service()
            result = await svc.analyze_market_trends("AI", industry="Tech", timeframe_months=6)

        assert result["phase"] == "market_trend_analysis"
        assert result["topic"] == "AI"
        assert result["industry"] == "Tech"
        assert result["source"] == "market_insight_agent"
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_import_error_returns_error_dict(self):
        svc = _service()

        with patch("builtins.__import__", side_effect=ImportError("no module")):
            result = await svc.analyze_market_trends("AI")

        assert "error" in result
        assert result["topic"] == "AI"

    @pytest.mark.asyncio
    async def test_no_industry_builds_simple_query(self):
        mock_agent = MagicMock()
        mock_agent.suggest_topics = AsyncMock(return_value=[])
        mock_agent_class = MagicMock(return_value=mock_agent)
        mock_llm_class = MagicMock(return_value=MagicMock())

        with patch.dict(
            "sys.modules",
            {
                "agents.market_insight_agent.market_insight_agent": MagicMock(
                    MarketInsightAgent=mock_agent_class
                ),
                "agents.content_agent.services.llm_client": MagicMock(
                    LLMClient=mock_llm_class
                ),
            },
        ):
            svc = _service()
            result = await svc.analyze_market_trends("blockchain")

        # Should not raise, and industry field should be None
        assert result["industry"] is None


# ---------------------------------------------------------------------------
# research_competitors
# ---------------------------------------------------------------------------


class TestResearchCompetitors:
    @pytest.mark.asyncio
    async def test_always_returns_unavailable(self):
        svc = _service()
        result = await svc.research_competitors("SaaS productivity tools")

        assert result["analysis_type"] == "competitor_research"
        assert result["market_segment"] == "SaaS productivity tools"
        assert result["competitors_analyzed"] == 0
        assert result["competitors"] == []
        assert result["data_source"] == "unavailable"
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_top_n_parameter_accepted(self):
        svc = _service()
        result = await svc.research_competitors("segment", top_n=10)
        # Still returns empty — no API configured
        assert result["competitors_analyzed"] == 0


# ---------------------------------------------------------------------------
# identify_opportunities
# ---------------------------------------------------------------------------


class TestIdentifyOpportunities:
    @pytest.mark.asyncio
    async def test_returns_fixed_opportunities(self):
        svc = _service()
        result = await svc.identify_opportunities("cloud computing")

        assert result["analysis_type"] == "opportunity_identification"
        assert result["market_segment"] == "cloud computing"
        assert result["opportunities_identified"] == 3
        assert len(result["opportunities"]) == 3

    @pytest.mark.asyncio
    async def test_opportunity_fields_present(self):
        svc = _service()
        result = await svc.identify_opportunities("fintech")

        for opp in result["opportunities"]:
            assert "name" in opp
            assert "potential" in opp
            assert "feasibility" in opp
            assert "timeline_months" in opp
            assert "estimated_roi" in opp

    @pytest.mark.asyncio
    async def test_constraints_preserved(self):
        svc = _service()
        constraints = {"budget": 1000, "team_size": 3}
        result = await svc.identify_opportunities("edtech", constraints=constraints)

        assert result["constraints_considered"] == constraints

    @pytest.mark.asyncio
    async def test_none_constraints_defaults_to_empty_dict(self):
        svc = _service()
        result = await svc.identify_opportunities("segment", constraints=None)
        assert result["constraints_considered"] == {}


# ---------------------------------------------------------------------------
# analyze_customer_sentiment
# ---------------------------------------------------------------------------


class TestAnalyzeCustomerSentiment:
    @pytest.mark.asyncio
    async def test_always_returns_unavailable(self):
        svc = _service()
        result = await svc.analyze_customer_sentiment("AI assistants")

        assert result["analysis_type"] == "sentiment_analysis"
        assert result["topic"] == "AI assistants"
        assert result["overall_sentiment"] == "unavailable"
        assert result["sentiment_score"] is None
        assert result["total_mentions"] == 0
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_default_sources(self):
        svc = _service()
        result = await svc.analyze_customer_sentiment("topic")
        assert "social_media" in result["sources"]
        assert "reviews" in result["sources"]

    @pytest.mark.asyncio
    async def test_custom_sources_accepted(self):
        svc = _service()
        result = await svc.analyze_customer_sentiment("topic", sources=["reddit"])
        assert result["sources"] == ["reddit"]


# ---------------------------------------------------------------------------
# get_service_metadata
# ---------------------------------------------------------------------------


class TestGetServiceMetadata:
    def test_metadata_structure(self):
        svc = _service()
        meta = svc.get_service_metadata()
        assert meta["name"] == "market_service"
        assert "capabilities" in meta
        assert isinstance(meta["capabilities"], list)
        assert len(meta["capabilities"]) > 0
        assert "market_trend_analysis" in meta["capabilities"]
