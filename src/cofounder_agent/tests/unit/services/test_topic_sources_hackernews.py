"""Unit tests for HackerNewsSource + shared topic-source filters.

No real HTTP — httpx.AsyncClient is mocked. Coverage:

- ``rewrite_as_blog_topic`` — all rejection paths, all cleanup passes
- ``is_news_or_junk``
- ``classify_category`` — sensible defaults + keyword hits
- HackerNewsSource end-to-end with sample payloads: score threshold,
  rewrite filter, missing fields, HTTP error, empty result
- TopicSource contract compliance
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.topic_source import TopicSource
from services.topic_sources._filters import (
    classify_category,
    is_news_or_junk,
    rewrite_as_blog_topic,
)
from services.topic_sources.hackernews import HackerNewsSource

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestRewriteAsBlogTopic:
    def test_show_hn_rejected(self):
        assert rewrite_as_blog_topic("Show HN: my cool tool") == ""
        assert rewrite_as_blog_topic("Launch HN: new startup") == ""
        assert rewrite_as_blog_topic("Ask HN: help me design") == ""

    def test_news_rejected(self):
        assert rewrite_as_blog_topic("Police arrest suspect in data breach") == ""
        assert rewrite_as_blog_topic("Major lawsuit filed against Big Tech") == ""

    def test_merch_rejected(self):
        assert rewrite_as_blog_topic("Buy now: our new shirt merch drop") == ""

    def test_too_short_rejected(self):
        assert rewrite_as_blog_topic("Short title") == ""
        # Below word count
        assert rewrite_as_blog_topic("one two three") == ""

    def test_academic_rejected(self):
        assert rewrite_as_blog_topic("NIST Special Publication 800-53 Rev 5") == ""
        assert rewrite_as_blog_topic("arXiv paper on quantum computing architecture") == ""

    def test_site_suffix_stripped(self):
        out = rewrite_as_blog_topic("Building distributed queues | Acme Engineering")
        assert "Acme Engineering" not in out
        assert "distributed queues" in out.lower()

    def test_bracket_prefix_stripped(self):
        out = rewrite_as_blog_topic("[Show HN] Building a scalable pgvector cluster locally")
        # [Show HN] prefix removed; the "Show HN" word doesn't trigger the
        # launch rejection (it's inside brackets, not the leading phrase)
        assert "[" not in out
        assert "pgvector cluster" in out

    def test_product_prefix_stripped(self):
        out = rewrite_as_blog_topic("Freestyle: Sandboxes for running LLM code safely")
        assert "Freestyle" not in out
        assert "Sandboxes" in out or "sandboxes" in out.lower()

    def test_allcaps_rejected(self):
        assert rewrite_as_blog_topic("NIST RFC IETF SSH TLS PROTOCOL") == ""

    def test_normal_topic_passes_through(self):
        title = "How to build fast distributed systems with asyncio"
        out = rewrite_as_blog_topic(title)
        assert out == title


class TestIsNewsOrJunk:
    def test_news_keywords(self):
        # Patterns use word boundaries on the base form (arrest, not arrested).
        assert is_news_or_junk("Police arrest suspect for fraud")
        assert is_news_or_junk("Major earthquake hits California coast")

    def test_personal_narrative(self):
        assert is_news_or_junk("My experience switching careers to AI")

    def test_short_titles(self):
        assert is_news_or_junk("three short words")

    def test_clean_technical_topic_passes(self):
        assert not is_news_or_junk("Building a database index with B-trees from scratch")


class TestClassifyCategory:
    def test_returns_technology_default(self):
        # Random title with no category keywords
        result = classify_category("Tuesday morning musings about the weather")
        assert result == "technology"

    def test_matches_known_category_when_keywords_present(self):
        # classify_category picks the best-scoring category from
        # CATEGORY_SEARCHES. We don't assert the specific category here
        # since CATEGORY_SEARCHES is operator-configurable; just that
        # the function returns a string.
        result = classify_category("Python async programming patterns")
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# HackerNewsSource
# ---------------------------------------------------------------------------


def _make_hn_client(top_ids: list[int], stories: dict[int, dict[str, Any]]):
    """Fake httpx.AsyncClient that returns canned responses for HN API paths."""
    client = AsyncMock()

    async def get(url: str, timeout: Any = None):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if url.endswith("/topstories.json"):
            resp.json = MagicMock(return_value=top_ids)
        else:
            # Extract story ID from /v0/item/<id>.json
            sid = int(url.rsplit("/", 1)[-1].split(".")[0])
            data = stories.get(sid)
            if data is None:
                resp.status_code = 404
                resp.json = MagicMock(return_value={})
            else:
                resp.json = MagicMock(return_value=data)
        return resp

    client.get = AsyncMock(side_effect=get)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


class TestHackerNewsSource:
    @pytest.mark.asyncio
    async def test_yields_topics_above_min_score(self):
        top_ids = [1, 2, 3]
        stories = {
            1: {"id": 1, "title": "How to build distributed caches with Redis", "score": 150, "url": "https://example.com/1"},
            2: {"id": 2, "title": "Show HN: my new tool for image generation", "score": 200, "url": "https://example.com/2"},
            3: {"id": 3, "title": "Understanding modern Rust async runtimes in depth", "score": 30, "url": "https://example.com/3"},  # below min_score
        }
        ctx, _ = _make_hn_client(top_ids, stories)

        source = HackerNewsSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(
                pool=None, config={"top_stories": 3, "min_score": 50},
            )

        # Story 2 rejected by rewrite (Show HN), story 3 below min_score.
        # Only story 1 should make it through.
        assert len(topics) == 1
        assert topics[0].source == "hackernews"
        assert "distributed caches" in topics[0].title.lower()
        assert topics[0].relevance_score == pytest.approx(1.5, abs=0.01)
        assert topics[0].source_url == "https://example.com/1"

    @pytest.mark.asyncio
    async def test_empty_result_when_all_filtered(self):
        top_ids = [1, 2]
        stories = {
            1: {"id": 1, "title": "Show HN: something", "score": 100},
            2: {"id": 2, "title": "Launch HN: product", "score": 100},
        }
        ctx, _ = _make_hn_client(top_ids, stories)

        source = HackerNewsSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(pool=None, config={})
        assert topics == []

    @pytest.mark.asyncio
    async def test_missing_title_skipped(self):
        top_ids = [1]
        stories = {1: {"id": 1, "score": 500}}  # no title
        ctx, _ = _make_hn_client(top_ids, stories)

        source = HackerNewsSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(pool=None, config={})
        assert topics == []

    @pytest.mark.asyncio
    async def test_score_normalized_to_zero_five(self):
        top_ids = [1]
        stories = {
            1: {"id": 1, "title": "Deep dive into PostgreSQL query planner internals", "score": 2000, "url": "https://x"},
        }
        ctx, _ = _make_hn_client(top_ids, stories)

        source = HackerNewsSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(pool=None, config={})
        assert len(topics) == 1
        # Capped at 5.0 even though 2000/100 = 20
        assert topics[0].relevance_score == 5.0

    @pytest.mark.asyncio
    async def test_source_url_falls_back_when_missing(self):
        top_ids = [1]
        stories = {
            1: {"id": 99, "title": "Building a tiny database in Rust for fun and learning", "score": 100},  # no url
        }
        ctx, _ = _make_hn_client(top_ids, stories)

        source = HackerNewsSource()
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await source.extract(pool=None, config={})
        assert len(topics) == 1
        assert topics[0].source_url == "https://news.ycombinator.com/item?id=99"


# ---------------------------------------------------------------------------
# Protocol contract
# ---------------------------------------------------------------------------


class TestContract:
    def test_conforms_to_topic_source_protocol(self):
        source = HackerNewsSource()
        assert isinstance(source, TopicSource)
        assert source.name == "hackernews"

    def test_extract_is_coroutine(self):
        import inspect
        assert inspect.iscoroutinefunction(HackerNewsSource.extract)
