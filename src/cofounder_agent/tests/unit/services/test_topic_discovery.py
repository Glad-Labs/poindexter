"""
Unit tests for services/topic_discovery.py

Tests topic scraping, deduplication, classification, and queuing.
"""

from unittest.mock import AsyncMock, patch

import pytest

from services.topic_discovery import TopicDiscovery, DiscoveredTopic


def _make_pool(published_titles=None, pending_topics=None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=[
        [{"title": t} for t in (published_titles or [])],
        [{"topic": t} for t in (pending_topics or [])],
    ])
    pool.execute = AsyncMock()
    return pool


class TestClassifyCategory:
    def test_security_topic(self):
        d = TopicDiscovery(AsyncMock())
        assert d._classify_category("Zero Trust Architecture for Developers") == "security"

    def test_startup_topic(self):
        d = TopicDiscovery(AsyncMock())
        assert d._classify_category("How to Launch Your MVP in a Weekend") == "startup"

    def test_engineering_topic(self):
        d = TopicDiscovery(AsyncMock())
        assert d._classify_category("Monorepo vs Polyrepo Architecture Patterns") == "engineering"

    def test_default_to_technology(self):
        d = TopicDiscovery(AsyncMock())
        assert d._classify_category("Something completely unrelated to anything") == "technology"


class TestRewriteTitle:
    def test_removes_show_hn_prefix(self):
        d = TopicDiscovery(AsyncMock())
        assert d._rewrite_as_blog_topic("[Show HN] My Cool Project") == "My Cool Project"

    def test_removes_site_suffix(self):
        d = TopicDiscovery(AsyncMock())
        assert d._rewrite_as_blog_topic("Cool Article | TechCrunch") == "Cool Article"

    def test_clean_title_unchanged(self):
        d = TopicDiscovery(AsyncMock())
        assert d._rewrite_as_blog_topic("A Normal Title") == "A Normal Title"


class TestDeduplicate:
    async def test_marks_exact_duplicates(self):
        pool = _make_pool(published_titles=["Docker Best Practices"])
        d = TopicDiscovery(pool)
        topics = [
            DiscoveredTopic(title="Docker Best Practices", category="technology",
                           source="hn", source_url="http://example.com"),
            DiscoveredTopic(title="Something New", category="technology",
                           source="hn", source_url="http://example.com"),
        ]
        result = await d._deduplicate(topics)
        assert result[0].is_duplicate is True
        assert result[1].is_duplicate is False

    async def test_marks_similar_titles(self):
        pool = _make_pool(published_titles=["how to use docker containers effectively"])
        d = TopicDiscovery(pool)
        topics = [
            DiscoveredTopic(title="how to use docker containers in production",
                           category="technology", source="hn", source_url=""),
        ]
        result = await d._deduplicate(topics)
        assert result[0].is_duplicate is True  # >60% word overlap

    async def test_no_pool_skips_dedup(self):
        d = TopicDiscovery(None)
        topics = [DiscoveredTopic(title="Test", category="tech", source="hn", source_url="")]
        result = await d._deduplicate(topics)
        assert result[0].is_duplicate is False


class TestQueueTopics:
    async def test_queues_to_database(self):
        pool = AsyncMock()
        pool.execute = AsyncMock()
        d = TopicDiscovery(pool)
        topics = [
            DiscoveredTopic(title="New Topic", category="technology",
                           source="hackernews", source_url="http://hn.com/123"),
        ]
        queued = await d.queue_topics(topics)
        assert queued == 1
        pool.execute.assert_awaited_once()

    async def test_handles_db_error(self):
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=Exception("unique violation"))
        d = TopicDiscovery(pool)
        topics = [DiscoveredTopic(title="Dup", category="tech", source="hn", source_url="")]
        queued = await d.queue_topics(topics)
        assert queued == 0
