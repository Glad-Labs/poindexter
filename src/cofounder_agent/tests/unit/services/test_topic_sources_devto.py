"""Unit tests for DevtoSource.

No real HTTP. Mocks ``httpx.AsyncClient`` and feeds canned article
payloads through the source.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.topic_source import TopicSource
from services.topic_sources.devto import DevtoSource


def _make_client(articles: list[dict[str, Any]] | dict[str, Any], status: int = 200):
    """Fake httpx.AsyncClient that returns the given payload from GET /articles."""
    client = AsyncMock()

    async def get(url: str, timeout: Any = None):
        resp = MagicMock()
        resp.status_code = status
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=articles)
        return resp

    client.get = AsyncMock(side_effect=get)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


class TestDevtoSource:
    @pytest.mark.asyncio
    async def test_filters_below_min_reactions(self):
        articles = [
            {"title": "Understanding database isolation levels in depth", "url": "https://dev.to/a", "positive_reactions_count": 100},
            {"title": "A beginner's tour of modern Python typing", "url": "https://dev.to/b", "positive_reactions_count": 5},
        ]
        ctx, _ = _make_client(articles)
        source = DevtoSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(pool=None, config={"min_reactions": 20})
        assert len(topics) == 1
        assert topics[0].source_url == "https://dev.to/a"

    @pytest.mark.asyncio
    async def test_rewrite_filter_applied(self):
        articles = [
            {"title": "Launch HN: my new product", "url": "https://dev.to/x", "positive_reactions_count": 500},
            {"title": "Building resilient microservices with circuit breakers", "url": "https://dev.to/y", "positive_reactions_count": 50},
        ]
        ctx, _ = _make_client(articles)
        source = DevtoSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(pool=None, config={})
        # First rejected by rewrite (Launch HN prefix), second passes
        assert len(topics) == 1
        assert topics[0].source == "devto"
        assert "microservices" in topics[0].title.lower()

    @pytest.mark.asyncio
    async def test_score_normalized_to_zero_five(self):
        articles = [
            {"title": "Deep dive into Rust async runtime internals", "url": "https://dev.to/r", "positive_reactions_count": 1000},
        ]
        ctx, _ = _make_client(articles)
        source = DevtoSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(pool=None, config={})
        assert len(topics) == 1
        # 1000/50 = 20, capped at 5.0
        assert topics[0].relevance_score == 5.0

    @pytest.mark.asyncio
    async def test_non_list_response_returns_empty(self):
        # Forem error endpoints sometimes return {"error": ...}
        ctx, _ = _make_client({"error": "rate limit"})
        source = DevtoSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(pool=None, config={})
        assert topics == []

    @pytest.mark.asyncio
    async def test_empty_list_response(self):
        ctx, _ = _make_client([])
        source = DevtoSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(pool=None, config={})
        assert topics == []

    @pytest.mark.asyncio
    async def test_custom_api_base_used(self):
        captured_urls: list[str] = []

        async def capture(url: str, timeout: Any = None):
            captured_urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value=[])
            return resp

        client = AsyncMock()
        client.get = AsyncMock(side_effect=capture)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        source = DevtoSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            await source.extract(
                pool=None,
                config={"api_base": "https://forem.example.com/api"},
            )
        assert len(captured_urls) == 1
        assert captured_urls[0].startswith("https://forem.example.com/api/articles?")

    @pytest.mark.asyncio
    async def test_tag_in_query_params(self):
        captured_urls: list[str] = []

        async def capture(url: str, timeout: Any = None):
            captured_urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value=[])
            return resp

        client = AsyncMock()
        client.get = AsyncMock(side_effect=capture)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        source = DevtoSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            await source.extract(pool=None, config={"tag": "python"})
        assert "tag=python" in captured_urls[0]

    @pytest.mark.asyncio
    async def test_missing_title_skipped(self):
        articles = [
            {"positive_reactions_count": 100, "url": "https://dev.to/x"},  # no title
            {"title": "A clear guide to effective unit testing practices", "positive_reactions_count": 80, "url": "https://dev.to/y"},
        ]
        ctx, _ = _make_client(articles)
        source = DevtoSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(pool=None, config={})
        assert len(topics) == 1


class TestContract:
    def test_conforms_to_topic_source_protocol(self):
        source = DevtoSource()
        assert isinstance(source, TopicSource)
        assert source.name == "devto"

    def test_extract_is_coroutine(self):
        import inspect
        assert inspect.iscoroutinefunction(DevtoSource.extract)
