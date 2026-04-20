"""Unit tests for WebSearchSource.

Mocks ``WebResearcher.search_simple`` — no real network calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.topic_source import TopicSource
from services.topic_sources.web_search import WebSearchSource


def _make_researcher(results_by_query: dict[str, list[dict]] | list[dict]):
    """Build a fake WebResearcher whose search_simple returns canned results."""
    researcher = MagicMock()

    async def search_simple(query: str, num_results: int = 3):
        if isinstance(results_by_query, dict):
            return results_by_query.get(query, [])
        return list(results_by_query)[:num_results]

    researcher.search_simple = AsyncMock(side_effect=search_simple)
    return researcher


class TestWebSearchSource:
    @pytest.mark.asyncio
    async def test_yields_topics_from_search_results(self):
        fake = _make_researcher([
            {"title": "Understanding modern database transaction isolation", "url": "https://example.com/1"},
            {"title": "A practical guide to distributed consensus algorithms", "url": "https://example.com/2"},
        ])
        fake_categories = {"technology": ["distributed systems", "databases"]}

        with patch("services.web_research.WebResearcher", return_value=fake), \
             patch("services.topic_discovery.CATEGORY_SEARCHES", fake_categories):
            source = WebSearchSource()
            topics = await source.extract(
                pool=None,
                config={"categories": ["technology"], "max_categories_per_run": 1},
            )

        assert len(topics) == 2
        assert all(t.source == "ddg_search" for t in topics)
        assert all(t.category == "technology" for t in topics)
        assert topics[0].relevance_score == 2.0

    @pytest.mark.asyncio
    async def test_rewrite_filter_applied(self):
        fake = _make_researcher([
            {"title": "Short", "url": "https://x.com/1"},  # too short, rejected
            {"title": "How to scale your Postgres database efficiently", "url": "https://x.com/2"},
        ])
        fake_categories = {"technology": ["scale databases"]}

        with patch("services.web_research.WebResearcher", return_value=fake), \
             patch("services.topic_discovery.CATEGORY_SEARCHES", fake_categories):
            source = WebSearchSource()
            topics = await source.extract(
                pool=None, config={"categories": ["technology"]},
            )

        assert len(topics) == 1
        assert "scale" in topics[0].title.lower()

    @pytest.mark.asyncio
    async def test_missing_title_skipped(self):
        fake = _make_researcher([
            {"url": "https://x.com/1"},  # no title
            {"title": "A guide to reliable async task queue design patterns", "url": "https://x.com/2"},
        ])
        fake_categories = {"technology": ["async task queues"]}

        with patch("services.web_research.WebResearcher", return_value=fake), \
             patch("services.topic_discovery.CATEGORY_SEARCHES", fake_categories):
            source = WebSearchSource()
            topics = await source.extract(
                pool=None, config={"categories": ["technology"]},
            )

        assert len(topics) == 1

    @pytest.mark.asyncio
    async def test_max_categories_cap(self):
        fake = _make_researcher([
            {"title": "A readable topic about something technical", "url": "https://x.com/1"},
        ])
        # 5 categories in the map; cap should limit to 2
        fake_categories = {
            "tech": ["a"], "ai": ["b"], "cloud": ["c"], "devops": ["d"], "security": ["e"],
        }

        with patch("services.web_research.WebResearcher", return_value=fake), \
             patch("services.topic_discovery.CATEGORY_SEARCHES", fake_categories):
            source = WebSearchSource()
            await source.extract(pool=None, config={"max_categories_per_run": 2})

        assert fake.search_simple.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_categories_no_calls(self):
        fake = _make_researcher([])
        with patch("services.web_research.WebResearcher", return_value=fake), \
             patch("services.topic_discovery.CATEGORY_SEARCHES", {}):
            source = WebSearchSource()
            topics = await source.extract(pool=None, config={})

        assert topics == []
        fake.search_simple.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_custom_relevance_score(self):
        fake = _make_researcher([
            {"title": "Rust memory safety in practice with real examples", "url": "https://x.com/1"},
        ])
        fake_categories = {"technology": ["rust safety"]}

        with patch("services.web_research.WebResearcher", return_value=fake), \
             patch("services.topic_discovery.CATEGORY_SEARCHES", fake_categories):
            source = WebSearchSource()
            topics = await source.extract(
                pool=None,
                config={"categories": ["technology"], "relevance_score": 4.5},
            )

        assert topics[0].relevance_score == 4.5

    @pytest.mark.asyncio
    async def test_category_without_queries_skipped(self):
        fake = _make_researcher([
            {"title": "A normal technical topic passes through", "url": "https://x.com/1"},
        ])
        fake_categories = {"technology": ["valid query"], "empty_cat": []}

        with patch("services.web_research.WebResearcher", return_value=fake), \
             patch("services.topic_discovery.CATEGORY_SEARCHES", fake_categories):
            source = WebSearchSource()
            topics = await source.extract(
                pool=None,
                config={"categories": ["empty_cat", "technology"]},
            )

        # empty_cat had no seed queries, skipped; technology ran once.
        assert fake.search_simple.call_count == 1
        assert len(topics) == 1


class TestContract:
    def test_conforms_to_topic_source_protocol(self):
        source = WebSearchSource()
        assert isinstance(source, TopicSource)
        assert source.name == "web_search"

    def test_extract_is_coroutine(self):
        import inspect
        assert inspect.iscoroutinefunction(WebSearchSource.extract)
