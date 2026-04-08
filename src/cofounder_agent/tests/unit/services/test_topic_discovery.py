"""
Unit tests for services/topic_discovery.py

Tests topic scraping, deduplication, classification, queuing,
brand relevance filtering, and source scrapers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.topic_discovery import TopicDiscovery, DiscoveredTopic, CATEGORY_SEARCHES


def _make_pool(published_titles=None, pending_topics=None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=[
        [{"title": t} for t in (published_titles or [])],
        [{"topic": t} for t in (pending_topics or [])],
    ])
    pool.execute = AsyncMock()
    return pool


# ===========================================================================
# _classify_category
# ===========================================================================


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

    def test_hardware_topic(self):
        d = TopicDiscovery(AsyncMock())
        assert d._classify_category("AMD vs NVIDIA GPU Benchmarks") == "hardware"

    def test_gaming_topic(self):
        d = TopicDiscovery(AsyncMock())
        assert d._classify_category("Indie Game Development with Godot Engine") == "gaming"

    def test_business_topic(self):
        d = TopicDiscovery(AsyncMock())
        assert d._classify_category("SaaS Metrics That Matter for Growth") == "business"

    def test_insights_topic(self):
        d = TopicDiscovery(AsyncMock())
        assert d._classify_category("State of Developer Productivity Survey Results") == "insights"


# ===========================================================================
# _rewrite_as_blog_topic
# ===========================================================================


class TestRewriteTitle:
    def test_removes_show_hn_prefix(self):
        d = TopicDiscovery(AsyncMock())
        assert d._rewrite_as_blog_topic("[Show HN] My Cool Project") == "My Cool Project"

    def test_removes_site_suffix(self):
        d = TopicDiscovery(AsyncMock())
        assert d._rewrite_as_blog_topic("Cool Article | TechCrunch") == "Cool Article"

    def test_clean_title_unchanged(self):
        d = TopicDiscovery(AsyncMock())
        assert d._rewrite_as_blog_topic("A Perfectly Normal Blog Title") == "A Perfectly Normal Blog Title"

    def test_removes_ask_hn_prefix(self):
        d = TopicDiscovery(AsyncMock())
        result = d._rewrite_as_blog_topic("[Ask HN] Best way to deploy ML models?")
        assert "[Ask HN]" not in result

    def test_removes_dash_site_suffix(self):
        d = TopicDiscovery(AsyncMock())
        result = d._rewrite_as_blog_topic("Cool New Framework - InfoWorld")
        assert "InfoWorld" not in result


# ===========================================================================
# _is_brand_relevant
# ===========================================================================


class TestIsBrandRelevant:
    def test_ai_topic_is_relevant(self):
        assert TopicDiscovery._is_brand_relevant("Building AI Agents with LLMs") is True

    def test_gpu_topic_is_relevant(self):
        assert TopicDiscovery._is_brand_relevant("NVIDIA RTX 5090 Benchmarks") is True

    def test_gaming_topic_is_relevant(self):
        assert TopicDiscovery._is_brand_relevant("Best Steam Games of 2026") is True

    def test_docker_topic_is_relevant(self):
        assert TopicDiscovery._is_brand_relevant("Docker Kubernetes CI/CD Pipeline") is True

    def test_unrelated_topic_not_relevant(self):
        assert TopicDiscovery._is_brand_relevant("Best Recipes for Dinner Tonight") is False

    def test_empty_string(self):
        assert TopicDiscovery._is_brand_relevant("") is False

    def test_case_insensitive(self):
        assert TopicDiscovery._is_brand_relevant("MACHINE LEARNING Trends") is True

    def test_ollama_relevant(self):
        assert TopicDiscovery._is_brand_relevant("Running Ollama Locally") is True

    def test_self_hosted_relevant(self):
        assert TopicDiscovery._is_brand_relevant("Self-Host Your Own Blog") is True


# ===========================================================================
# _deduplicate
# ===========================================================================


class TestDeduplicate:
    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_marks_similar_titles(self):
        pool = _make_pool(published_titles=["how to use docker containers effectively"])
        d = TopicDiscovery(pool)
        topics = [
            DiscoveredTopic(title="how to use docker containers in production",
                           category="technology", source="hn", source_url=""),
        ]
        result = await d._deduplicate(topics)
        assert result[0].is_duplicate is True  # >60% word overlap

    @pytest.mark.asyncio
    async def test_no_pool_skips_dedup(self):
        d = TopicDiscovery(None)
        topics = [DiscoveredTopic(title="Test", category="tech", source="hn", source_url="")]
        result = await d._deduplicate(topics)
        assert result[0].is_duplicate is False

    @pytest.mark.asyncio
    async def test_marks_pending_task_duplicates(self):
        pool = _make_pool(pending_topics=["Kubernetes Scaling Guide"])
        d = TopicDiscovery(pool)
        topics = [
            DiscoveredTopic(title="Kubernetes Scaling Guide", category="engineering",
                           source="devto", source_url=""),
        ]
        result = await d._deduplicate(topics)
        assert result[0].is_duplicate is True

    @pytest.mark.asyncio
    async def test_short_titles_not_fuzzy_matched(self):
        """Titles with 3 or fewer words shouldn't trigger fuzzy overlap matching."""
        pool = _make_pool(published_titles=["ai tools tips"])
        d = TopicDiscovery(pool)
        topics = [
            DiscoveredTopic(title="AI Tools", category="technology",
                           source="hn", source_url=""),
        ]
        result = await d._deduplicate(topics)
        # Short title: word overlap check requires >3 words
        assert result[0].is_duplicate is False

    @pytest.mark.asyncio
    async def test_db_error_doesnt_crash(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=Exception("connection lost"))
        d = TopicDiscovery(pool)
        topics = [DiscoveredTopic(title="Test", category="tech", source="hn", source_url="")]
        result = await d._deduplicate(topics)
        assert result[0].is_duplicate is False  # Graceful fallback


# ===========================================================================
# queue_topics
# ===========================================================================


class TestQueueTopics:
    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_handles_db_error(self):
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=Exception("unique violation"))
        d = TopicDiscovery(pool)
        topics = [DiscoveredTopic(title="Dup", category="tech", source="hn", source_url="")]
        queued = await d.queue_topics(topics)
        assert queued == 0

    @pytest.mark.asyncio
    async def test_queues_multiple_topics(self):
        pool = AsyncMock()
        pool.execute = AsyncMock()
        d = TopicDiscovery(pool)
        topics = [
            DiscoveredTopic(title=f"Topic {i}", category="technology",
                           source="hn", source_url="")
            for i in range(5)
        ]
        queued = await d.queue_topics(topics)
        assert queued == 5
        assert pool.execute.await_count == 5

    @pytest.mark.asyncio
    async def test_partial_failure_counts_successes(self):
        pool = AsyncMock()
        call_count = 0
        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("DB error on second topic")
        pool.execute = AsyncMock(side_effect=_side_effect)
        d = TopicDiscovery(pool)
        topics = [
            DiscoveredTopic(title=f"Topic {i}", category="tech", source="hn", source_url="")
            for i in range(3)
        ]
        queued = await d.queue_topics(topics)
        assert queued == 2  # 1st and 3rd succeed


# ===========================================================================
# _scrape_hackernews
# ===========================================================================


class TestScrapeHackerNews:
    @pytest.mark.asyncio
    async def test_returns_topics_from_hn(self):
        d = TopicDiscovery(AsyncMock())

        def _mock_response(url):
            resp = MagicMock()
            if "topstories" in url:
                resp.json.return_value = [1, 2]
            elif "/item/1" in url:
                resp.json.return_value = {"id": 1, "title": "AI Agent Framework Released", "url": "http://example.com", "score": 200}
            elif "/item/2" in url:
                resp.json.return_value = {"id": 2, "title": "Low Score Post", "url": "", "score": 10}
            return resp

        with patch("services.topic_discovery.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=lambda url: _mock_response(url))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            topics = await d._scrape_hackernews()
            # Only the high-score story (>=50) should be included
            assert len(topics) == 1
            assert "AI Agent" in topics[0].title
            assert topics[0].source == "hackernews"

    @pytest.mark.asyncio
    async def test_handles_network_error(self):
        d = TopicDiscovery(AsyncMock())

        with patch("services.topic_discovery.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("offline"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            topics = await d._scrape_hackernews()
            assert topics == []


# ===========================================================================
# _scrape_devto
# ===========================================================================


class TestScrapeDevTo:
    @pytest.mark.asyncio
    async def test_returns_topics_from_devto(self):
        d = TopicDiscovery(AsyncMock())

        with patch("services.topic_discovery.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            resp = MagicMock()
            resp.json.return_value = [
                {"title": "Building Docker Containers for Production", "url": "https://dev.to/test", "positive_reactions_count": 100},
                {"title": "Low engagement article", "url": "https://dev.to/low", "positive_reactions_count": 5},
            ]
            mock_client.get = AsyncMock(return_value=resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            topics = await d._scrape_devto()
            assert len(topics) == 1
            assert topics[0].source == "devto"

    @pytest.mark.asyncio
    async def test_handles_network_error(self):
        d = TopicDiscovery(AsyncMock())

        with patch("services.topic_discovery.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("offline"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            topics = await d._scrape_devto()
            assert topics == []


# ===========================================================================
# DiscoveredTopic dataclass
# ===========================================================================


class TestDiscoveredTopic:
    def test_default_values(self):
        t = DiscoveredTopic(title="Test", category="tech", source="hn", source_url="")
        assert t.relevance_score == 0.0
        assert t.is_duplicate is False

    def test_custom_values(self):
        t = DiscoveredTopic(
            title="AI Test", category="technology", source="devto",
            source_url="http://example.com", relevance_score=4.5, is_duplicate=True,
        )
        assert t.relevance_score == 4.5
        assert t.is_duplicate is True


# ===========================================================================
# CATEGORY_SEARCHES constant
# ===========================================================================


class TestCategorySearches:
    def test_all_categories_have_queries(self):
        expected_cats = {"technology", "startup", "security", "engineering", "insights", "business", "hardware", "gaming"}
        assert expected_cats.issubset(set(CATEGORY_SEARCHES.keys()))

    def test_queries_are_nonempty_strings(self):
        for cat, queries in CATEGORY_SEARCHES.items():
            assert len(queries) > 0, f"Category {cat} has no queries"
            for q in queries:
                assert isinstance(q, str) and len(q) > 5
