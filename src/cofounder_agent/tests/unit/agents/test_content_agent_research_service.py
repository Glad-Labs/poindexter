"""
Unit tests for agents/content_agent/research_service.py

Tests for SearXNGResearchService and research_content_topic helper.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.content_agent.research_service import SearXNGResearchService


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestSearXNGResearchServiceInit:
    def test_default_instance(self):
        svc = SearXNGResearchService()
        # Trailing slash should be stripped
        assert not svc.searxng_instance.endswith("/")

    def test_default_timeout(self):
        svc = SearXNGResearchService()
        assert svc.timeout == 30

    def test_default_max_results(self):
        svc = SearXNGResearchService()
        assert svc.max_results == 10

    def test_custom_instance_url_stripped(self):
        svc = SearXNGResearchService(searxng_instance="https://search.example.com/")
        assert svc.searxng_instance == "https://search.example.com"

    def test_custom_timeout(self):
        svc = SearXNGResearchService(timeout=60)
        assert svc.timeout == 60

    def test_custom_max_results(self):
        svc = SearXNGResearchService(max_results=20)
        assert svc.max_results == 20

    def test_initial_client_initialized(self):
        svc = SearXNGResearchService()
        assert svc.client is not None


# ---------------------------------------------------------------------------
# Context manager (__aenter__ / __aexit__)
# ---------------------------------------------------------------------------


class TestContextManager:
    @pytest.mark.asyncio
    async def test_aenter_sets_client(self):
        svc = SearXNGResearchService()
        with patch("agents.content_agent.research_service.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client
            result = await svc.__aenter__()
        assert result is svc
        assert svc.client is not None

    @pytest.mark.asyncio
    async def test_aexit_closes_client(self):
        svc = SearXNGResearchService()
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        svc.client = mock_client

        await svc.__aexit__(None, None, None)
        mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_aexit_no_client_does_not_raise(self):
        svc = SearXNGResearchService()
        svc.client = None
        # Should not raise
        await svc.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# _parse_results
# ---------------------------------------------------------------------------


class TestParseResults:
    def test_empty_list(self):
        svc = SearXNGResearchService()
        result = svc._parse_results([])
        assert result == []

    def test_parses_title_url_content(self):
        svc = SearXNGResearchService()
        raw = [
            {
                "title": "AI Article",
                "url": "https://example.com/ai",
                "content": "AI content here",
                "engine": "google",
                "score": 0.95,
            }
        ]
        result = svc._parse_results(raw)
        assert len(result) == 1
        assert result[0]["title"] == "AI Article"
        assert result[0]["url"] == "https://example.com/ai"
        assert result[0]["content"] == "AI content here"
        assert result[0]["score"] == 0.95

    def test_missing_fields_default_empty(self):
        svc = SearXNGResearchService()
        raw = [{}]
        result = svc._parse_results(raw)
        assert result[0]["title"] == ""
        assert result[0]["url"] == ""
        assert result[0]["content"] == ""
        assert result[0]["score"] == 0

    def test_multiple_results(self):
        svc = SearXNGResearchService()
        raw = [{"title": f"Article {i}"} for i in range(5)]
        result = svc._parse_results(raw)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    def _make_mock_response(self, results=None):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": results or []}
        return mock_resp

    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_keys(self):
        svc = SearXNGResearchService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=self._make_mock_response([]))
        svc.client = mock_client

        result = await svc.search("AI trends")
        assert "query" in result
        assert "category" in result
        assert "timestamp" in result
        assert "results" in result
        assert "count" in result
        assert "source" in result

    @pytest.mark.asyncio
    async def test_query_in_result(self):
        svc = SearXNGResearchService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=self._make_mock_response([]))
        svc.client = mock_client

        result = await svc.search("blockchain")
        assert result["query"] == "blockchain"

    @pytest.mark.asyncio
    async def test_category_defaults_to_general(self):
        svc = SearXNGResearchService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=self._make_mock_response([]))
        svc.client = mock_client

        result = await svc.search("test")
        assert result["category"] == "general"

    @pytest.mark.asyncio
    async def test_source_is_searxng(self):
        svc = SearXNGResearchService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=self._make_mock_response([]))
        svc.client = mock_client

        result = await svc.search("test")
        assert result["source"] == "SearXNG"

    @pytest.mark.asyncio
    async def test_returns_error_dict_on_exception(self):
        svc = SearXNGResearchService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("network error"))
        svc.client = mock_client

        result = await svc.search("query")
        assert "error" in result
        assert result["results"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_uses_existing_client(self):
        svc = SearXNGResearchService()
        assert svc.client is not None

        mock_response = self._make_mock_response([])
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        svc.client = mock_client  # type: ignore[assignment]

        await svc.search("test")

        mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_result_count_matches_results(self):
        svc = SearXNGResearchService()
        mock_client = AsyncMock()
        raw_results = [{"title": f"r{i}", "url": "", "content": "", "engine": [], "score": 0}
                       for i in range(3)]
        mock_client.get = AsyncMock(return_value=self._make_mock_response(raw_results))
        svc.client = mock_client

        result = await svc.search("query")
        assert result["count"] == 3


# ---------------------------------------------------------------------------
# search_news
# ---------------------------------------------------------------------------


class TestSearchNews:
    @pytest.mark.asyncio
    async def test_delegates_to_search_with_news_category(self):
        svc = SearXNGResearchService()
        svc.search = AsyncMock(return_value={"category": "news", "results": []})

        result = await svc.search_news("AI news")

        svc.search.assert_called_once_with("AI news", category="news")
        assert result["category"] == "news"


# ---------------------------------------------------------------------------
# research_topic
# ---------------------------------------------------------------------------


class TestResearchTopic:
    @pytest.mark.asyncio
    async def test_quick_depth_returns_fewer_queries(self):
        svc = SearXNGResearchService()

        async def fake_search(query, category="general"):
            return {"query": query, "results": [], "count": 0}

        svc.search = fake_search

        result = await svc.research_topic("machine learning", depth="quick")
        assert "topic" in result
        assert "research" in result
        assert "total_results" in result
        assert result["topic"] == "machine learning"

    @pytest.mark.asyncio
    async def test_standard_depth_includes_market_and_challenges(self):
        svc = SearXNGResearchService()

        async def fake_search(query, category="general"):
            return {"query": query, "results": [], "count": 0}

        svc.search = fake_search

        result = await svc.research_topic("cloud computing", depth="standard")
        assert "market" in result["research"]
        assert "challenges" in result["research"]

    @pytest.mark.asyncio
    async def test_comprehensive_depth_has_more_categories(self):
        svc = SearXNGResearchService()

        async def fake_search(query, category="general"):
            return {"query": query, "results": [], "count": 0}

        svc.search = fake_search

        result = await svc.research_topic("AI", depth="comprehensive")
        assert "research" in result["research"]
        assert "expert" in result["research"]
        assert "case_studies" in result["research"]

    @pytest.mark.asyncio
    async def test_total_results_sums_counts(self):
        svc = SearXNGResearchService()
        call_count = [0]

        async def fake_search(query, category="general"):
            call_count[0] += 1
            return {"query": query, "results": [], "count": 5}

        svc.search = fake_search

        result = await svc.research_topic("topic", depth="quick")
        # total_results should be sum of all counts
        expected_total = sum(v.get("count", 0) for v in result["research"].values())
        assert result["total_results"] == expected_total

    @pytest.mark.asyncio
    async def test_returns_timestamp(self):
        svc = SearXNGResearchService()

        async def fake_search(query, category="general"):
            return {"results": [], "count": 0}

        svc.search = fake_search

        result = await svc.research_topic("test")
        assert "timestamp" in result
        assert len(result["timestamp"]) > 0


# ---------------------------------------------------------------------------
# fetch_article_content
# ---------------------------------------------------------------------------


class TestFetchArticleContent:
    @pytest.mark.asyncio
    async def test_returns_none_when_bs4_unavailable(self):
        svc = SearXNGResearchService()

        with patch("agents.content_agent.research_service.BS4_AVAILABLE", False):
            result = await svc.fetch_article_content("https://example.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self):
        svc = SearXNGResearchService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("connection refused"))
        svc.client = mock_client

        with patch("agents.content_agent.research_service.BS4_AVAILABLE", True):
            result = await svc.fetch_article_content("https://example.com")

        assert result is None


# ---------------------------------------------------------------------------
# get_news_feeds — feedparser unavailable path
# ---------------------------------------------------------------------------


class TestGetNewsFeeds:
    @pytest.mark.asyncio
    async def test_returns_note_when_feedparser_unavailable(self):
        svc = SearXNGResearchService()

        with patch("agents.content_agent.research_service.FEEDPARSER_AVAILABLE", False):
            result = await svc.get_news_feeds(["AI", "ML"])

        assert "note" in result
        assert "feedparser" in result["note"].lower()
        assert result["feeds"] == {}

    @pytest.mark.asyncio
    async def test_keywords_in_result(self):
        svc = SearXNGResearchService()

        with patch("agents.content_agent.research_service.FEEDPARSER_AVAILABLE", False):
            result = await svc.get_news_feeds(["AI", "blockchain"])

        assert result["keywords"] == ["AI", "blockchain"]
