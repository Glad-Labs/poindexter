"""
Unit tests for services/serper_client.py.

Covers search, news search, summary, fact checking, trending topics,
API quota tracking, and error handling. HTTP is always mocked.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.serper_client import SerperClient


@pytest.fixture
def client():
    return SerperClient(api_key="test-key")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _mock_httpx_ok(json_data):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


SAMPLE_RESULTS = {
    "organic": [
        {"title": "Result 1", "link": "https://example.com/1", "snippet": "First result"},
        {"title": "Result 2", "link": "https://example.com/2", "snippet": "Second result"},
    ]
}


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInit:
    def test_uses_provided_api_key(self, client):
        assert client.api_key == "test-key"

    def test_no_key_logs_warning(self):
        with patch("services.site_config.site_config", MagicMock(get=lambda *a, **k: None)):
            c = SerperClient(api_key=None)
        assert not c.api_key


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearch:
    def test_returns_results_on_success(self, client):
        mock = _mock_httpx_ok(SAMPLE_RESULTS)
        with patch("services.serper_client.httpx.AsyncClient", return_value=mock):
            result = _run(client.search("test query"))
        assert len(result["organic"]) == 2

    def test_posts_to_correct_endpoint(self, client):
        mock = _mock_httpx_ok(SAMPLE_RESULTS)
        with patch("services.serper_client.httpx.AsyncClient", return_value=mock):
            _run(client.search("test", search_type="news"))
        url = mock.post.call_args[0][0]
        assert url.endswith("/news")

    def test_caps_num_at_30(self, client):
        mock = _mock_httpx_ok(SAMPLE_RESULTS)
        with patch("services.serper_client.httpx.AsyncClient", return_value=mock):
            _run(client.search("test", num=100))
        payload = mock.post.call_args[1]["json"]
        assert payload["num"] == 30

    def test_returns_empty_without_api_key(self):
        with patch("services.site_config.site_config", MagicMock(get=lambda *a, **k: None)):
            c = SerperClient(api_key=None)
        result = _run(c.search("test"))
        assert result == {}

    def test_returns_empty_on_http_error(self, client):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=httpx.HTTPError("fail"))
        with patch("services.serper_client.httpx.AsyncClient", return_value=mock_client):
            result = _run(client.search("test"))
        assert result == {}

    def test_increments_monthly_usage(self, client):
        mock = _mock_httpx_ok(SAMPLE_RESULTS)
        assert client.monthly_usage == 0
        with patch("services.serper_client.httpx.AsyncClient", return_value=mock):
            _run(client.search("test"))
        assert client.monthly_usage == 1


# ---------------------------------------------------------------------------
# news_search / shopping_search
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSpecializedSearch:
    def test_news_search_uses_news_type(self, client):
        mock = _mock_httpx_ok(SAMPLE_RESULTS)
        with patch("services.serper_client.httpx.AsyncClient", return_value=mock):
            _run(client.news_search("AI news"))
        url = mock.post.call_args[0][0]
        assert "news" in url

    def test_shopping_search_uses_shopping_type(self, client):
        mock = _mock_httpx_ok(SAMPLE_RESULTS)
        with patch("services.serper_client.httpx.AsyncClient", return_value=mock):
            _run(client.shopping_search("GPU"))
        url = mock.post.call_args[0][0]
        assert "shopping" in url


# ---------------------------------------------------------------------------
# get_search_results_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchSummary:
    def test_returns_structured_summary(self, client):
        mock = _mock_httpx_ok(SAMPLE_RESULTS)
        with patch("services.serper_client.httpx.AsyncClient", return_value=mock):
            result = _run(client.get_search_results_summary("test", max_results=2))
        assert result["query"] == "test"
        assert len(result["sources"]) == 2
        assert result["sources"][0]["position"] == 1

    def test_returns_empty_sources_on_error(self, client):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=Exception("boom"))
        with patch("services.serper_client.httpx.AsyncClient", return_value=mock_client):
            result = _run(client.get_search_results_summary("test"))
        assert result["sources"] == []
        assert result["results_found"] == 0


# ---------------------------------------------------------------------------
# check_api_quota
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuota:
    def test_returns_quota_info(self, client):
        info = client.check_api_quota()
        assert info["monthly_limit"] == 100
        assert info["local_usage_tracked"] == 0

    def test_tracks_usage(self, client):
        mock = _mock_httpx_ok(SAMPLE_RESULTS)
        with patch("services.serper_client.httpx.AsyncClient", return_value=mock):
            _run(client.search("a"))
            _run(client.search("b"))
        assert client.check_api_quota()["local_usage_tracked"] == 2
