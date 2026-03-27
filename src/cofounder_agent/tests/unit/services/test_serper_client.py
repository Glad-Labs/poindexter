"""
Unit tests for services/serper_client.py

Tests SerperClient search methods by mocking httpx.AsyncClient.
No real network calls are made.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.serper_client import SerperClient, get_serper_client

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_mock_response(data: dict, status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


SAMPLE_ORGANIC = [
    {"title": "Result 1", "link": "https://example.com/1", "snippet": "Snippet 1"},
    {"title": "Result 2", "link": "https://example.com/2", "snippet": "Snippet 2"},
]

SAMPLE_SEARCH_RESPONSE = {
    "organic": SAMPLE_ORGANIC,
    "knowledgePanel": {"title": "AI"},
}


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestSerperClientInit:
    def test_uses_env_api_key(self, monkeypatch):
        monkeypatch.setenv("SERPER_API_KEY", "env-key")
        client = SerperClient()
        assert client.api_key == "env-key"

    def test_explicit_api_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("SERPER_API_KEY", "env-key")
        client = SerperClient(api_key="explicit-key")
        assert client.api_key == "explicit-key"

    def test_no_api_key_warns(self, monkeypatch, caplog):
        monkeypatch.delenv("SERPER_API_KEY", raising=False)
        import logging

        with caplog.at_level(logging.WARNING):
            client = SerperClient()
        assert client.api_key is None

    def test_check_api_quota_structure(self):
        client = SerperClient(api_key="key")
        quota = client.check_api_quota()
        assert "monthly_limit" in quota
        assert quota["monthly_limit"] == 100
        assert "local_usage_tracked" in quota


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


class TestSerperSearch:
    @pytest.mark.asyncio
    async def test_search_no_api_key_returns_empty(self, monkeypatch):
        monkeypatch.delenv("SERPER_API_KEY", raising=False)
        client = SerperClient()
        result = await client.search("test query")
        assert result == {}

    @pytest.mark.asyncio
    async def test_search_returns_data(self):
        client = SerperClient(api_key="test-key")
        mock_resp = make_mock_response(SAMPLE_SEARCH_RESPONSE)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            result = await client.search("AI trends", num=10)

        assert result == SAMPLE_SEARCH_RESPONSE

    @pytest.mark.asyncio
    async def test_search_increments_usage(self):
        client = SerperClient(api_key="test-key")
        mock_resp = make_mock_response(SAMPLE_SEARCH_RESPONSE)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            await client.search("query one")
            assert client.monthly_usage == 1

    @pytest.mark.asyncio
    async def test_search_http_error_returns_empty(self):
        client = SerperClient(api_key="test-key")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(side_effect=httpx.HTTPError("connection refused"))
            mock_cls.return_value = mock_ctx

            result = await client.search("test")
        assert result == {}

    @pytest.mark.asyncio
    async def test_search_num_capped_at_30(self):
        """num > 30 should be capped at 30 in the payload."""
        client = SerperClient(api_key="test-key")
        mock_resp = make_mock_response(SAMPLE_SEARCH_RESPONSE)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            await client.search("test", num=100)

        call_kwargs = mock_ctx.post.call_args[1]
        assert call_kwargs["json"]["num"] == 30


# ---------------------------------------------------------------------------
# news_search / shopping_search
# ---------------------------------------------------------------------------


class TestSerperSpecializedSearch:
    @pytest.mark.asyncio
    async def test_news_search_calls_search_with_news_type(self):
        client = SerperClient(api_key="key")
        with patch.object(client, "search", new=AsyncMock(return_value={})) as mock_search:
            await client.news_search("breaking news")
            mock_search.assert_awaited_once_with("breaking news", 10, search_type="news")

    @pytest.mark.asyncio
    async def test_shopping_search_calls_search_with_shopping_type(self):
        client = SerperClient(api_key="key")
        with patch.object(client, "search", new=AsyncMock(return_value={})) as mock_search:
            await client.shopping_search("laptop")
            mock_search.assert_awaited_once_with("laptop", 10, search_type="shopping")


# ---------------------------------------------------------------------------
# get_search_results_summary
# ---------------------------------------------------------------------------


class TestSerperSummary:
    @pytest.mark.asyncio
    async def test_summary_structure(self):
        client = SerperClient(api_key="key")
        with patch.object(client, "search", new=AsyncMock(return_value=SAMPLE_SEARCH_RESPONSE)):
            result = await client.get_search_results_summary("AI", max_results=2)

        assert result["query"] == "AI"
        assert result["results_found"] == 2
        assert len(result["sources"]) == 2
        assert result["sources"][0]["position"] == 1

    @pytest.mark.asyncio
    async def test_summary_empty_search_results(self):
        client = SerperClient(api_key="key")
        with patch.object(client, "search", new=AsyncMock(return_value={})):
            result = await client.get_search_results_summary("empty")
        assert result["results_found"] == 0
        assert result["sources"] == []


# ---------------------------------------------------------------------------
# fact_check_claims
# ---------------------------------------------------------------------------


class TestSerperFactCheck:
    @pytest.mark.asyncio
    async def test_fact_check_processes_claims(self):
        client = SerperClient(api_key="key")
        with patch.object(
            client,
            "search",
            new=AsyncMock(return_value={"organic": [SAMPLE_ORGANIC[0]]}),
        ):
            result = await client.fact_check_claims(["The earth is round"])
        assert "The earth is round" in result
        assert result["The earth is round"]["sources_found"] == 1

    @pytest.mark.asyncio
    async def test_fact_check_limits_to_3_claims(self):
        client = SerperClient(api_key="key")
        calls = []

        async def mock_search(q, num=10, search_type="search"):
            calls.append(q)
            return {"organic": []}

        with patch.object(client, "search", side_effect=mock_search):
            await client.fact_check_claims(["a", "b", "c", "d", "e"])

        assert len(calls) == 3


# ---------------------------------------------------------------------------
# get_trending_topics
# ---------------------------------------------------------------------------


class TestSerperTrending:
    @pytest.mark.asyncio
    async def test_trending_topics_returns_list(self):
        client = SerperClient(api_key="key")
        with patch.object(client, "search", new=AsyncMock(return_value=SAMPLE_SEARCH_RESPONSE)):
            topics = await client.get_trending_topics(category="technology")
        assert isinstance(topics, list)
        assert len(topics) == len(SAMPLE_ORGANIC)
        assert topics[0]["trend_rank"] == 1

    @pytest.mark.asyncio
    async def test_trending_topics_empty_on_error(self):
        client = SerperClient(api_key="key")
        with patch.object(client, "search", new=AsyncMock(side_effect=RuntimeError("API down"))):
            topics = await client.get_trending_topics()
        assert topics == []


# ---------------------------------------------------------------------------
# research_topic
# ---------------------------------------------------------------------------


class TestSerperResearchTopic:
    @pytest.mark.asyncio
    async def test_research_topic_structure(self):
        client = SerperClient(api_key="key")
        with patch.object(client, "search", new=AsyncMock(return_value=SAMPLE_SEARCH_RESPONSE)):
            result = await client.research_topic("machine learning")
        assert result["topic"] == "machine learning"
        assert "main_sources" in result
        assert "aspects" in result

    @pytest.mark.asyncio
    async def test_research_topic_uses_custom_aspects(self):
        client = SerperClient(api_key="key")
        search_calls: list[str] = []

        async def mock_search(q, num=10, search_type="search"):
            search_calls.append(q)
            return {"organic": []}

        with patch.object(client, "search", side_effect=mock_search):
            await client.research_topic("Python", aspects=["history", "ecosystem"])
        # Aspect searches limited to 2
        assert any("history" in q for q in search_calls)


# ---------------------------------------------------------------------------
# get_author_information
# ---------------------------------------------------------------------------


class TestSerperAuthorInfo:
    @pytest.mark.asyncio
    async def test_author_info_returns_results(self):
        client = SerperClient(api_key="key")
        with patch.object(client, "search", new=AsyncMock(return_value=SAMPLE_SEARCH_RESPONSE)):
            result = await client.get_author_information("Yann LeCun")
        assert result["author"] == "Yann LeCun"
        assert "results" in result
        assert len(result["results"]) == 2

    @pytest.mark.asyncio
    async def test_author_info_error_returns_empty(self):
        client = SerperClient(api_key="key")
        with patch.object(client, "search", new=AsyncMock(side_effect=RuntimeError("error"))):
            result = await client.get_author_information("Unknown")
        assert result == {}


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestGetSerperClient:
    def test_returns_serper_client_instance(self, monkeypatch):
        monkeypatch.setenv("SERPER_API_KEY", "factory-key")
        client = get_serper_client()
        assert isinstance(client, SerperClient)
        assert client.api_key == "factory-key"
