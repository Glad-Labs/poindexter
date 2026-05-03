"""
Unit tests for services/topic_discovery.py

Tests topic scraping, deduplication, classification, queuing,
brand relevance filtering, and source scrapers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.topic_discovery import CATEGORY_SEARCHES, DiscoveredTopic, TopicDiscovery


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

    def test_self_hosted_topic_is_relevant(self):
        assert TopicDiscovery._is_brand_relevant("Self-Hosted AI Content Pipeline for Solo Founders") is True

    def test_local_inference_topic_is_relevant(self):
        assert TopicDiscovery._is_brand_relevant("Running Local Inference with Ollama on Your Own Hardware") is True

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
        """Single content word titles skip fuzzy matching."""
        pool = _make_pool(published_titles=["advanced python tricks"])
        d = TopicDiscovery(pool)
        topics = [
            DiscoveredTopic(title="Python", category="technology",
                           source="hn", source_url=""),
        ]
        result = await d._deduplicate(topics)
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


def _make_qt_pool(execute_side_effect=None):
    """Build a pool whose acquire() yields a conn with execute +
    transaction support. #188 — queue_topics now writes to
    pipeline_tasks + pipeline_versions inside a transaction.
    """
    from contextlib import asynccontextmanager

    conn = MagicMock()
    if execute_side_effect:
        conn.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        conn.execute = AsyncMock()

    @asynccontextmanager
    async def _tx_inner():
        yield

    conn.transaction = MagicMock(side_effect=lambda *a, **kw: _tx_inner())

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


class TestQueueTopics:
    @pytest.mark.asyncio
    async def test_queues_to_database(self):
        pool, conn = _make_qt_pool()
        d = TopicDiscovery(pool)
        topics = [
            DiscoveredTopic(title="New Topic", category="technology",
                           source="hackernews", source_url="http://hn.com/123"),
        ]
        queued = await d.queue_topics(topics)
        assert queued == 1
        # #188: each topic now triggers TWO INSERTs (pipeline_tasks +
        # pipeline_versions) inside a single transaction.
        assert conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_handles_db_error(self):
        pool, conn = _make_qt_pool(execute_side_effect=Exception("unique violation"))
        d = TopicDiscovery(pool)
        topics = [DiscoveredTopic(title="Dup", category="tech", source="hn", source_url="")]
        queued = await d.queue_topics(topics)
        assert queued == 0

    @pytest.mark.asyncio
    async def test_queues_multiple_topics(self):
        pool, conn = _make_qt_pool()
        d = TopicDiscovery(pool)
        topics = [
            DiscoveredTopic(title=f"Topic {i}", category="technology",
                           source="hn", source_url="")
            for i in range(5)
        ]
        queued = await d.queue_topics(topics)
        assert queued == 5
        # 5 topics * 2 INSERTs each = 10 execute calls
        assert conn.execute.await_count == 10

    @pytest.mark.asyncio
    async def test_partial_failure_counts_successes(self):
        # First topic: 2 successful executes.
        # Second topic: first execute (pipeline_tasks) fails -> raise.
        # Third topic: 2 successful executes.
        # Total expected execute calls: 2 + 1 (failing) + 2 = 5,
        # successful queued count = 2.
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Topic 2 fails on its first execute (call #3 overall)
            if call_count == 3:
                raise Exception("DB error on second topic")

        pool, conn = _make_qt_pool(execute_side_effect=_side_effect)
        d = TopicDiscovery(pool)
        topics = [
            DiscoveredTopic(title=f"Topic {i}", category="tech", source="hn", source_url="")
            for i in range(3)
        ]
        queued = await d.queue_topics(topics)
        assert queued == 2  # 1st and 3rd succeed

    @pytest.mark.asyncio
    async def test_writes_to_pipeline_tables_not_view(self):
        """#188 regression guard — queue_topics must INSERT into
        pipeline_tasks + pipeline_versions, never content_tasks.
        """
        seen: list[str] = []

        async def _capture(sql, *args, **kwargs):
            seen.append(sql)

        pool, conn = _make_qt_pool(execute_side_effect=_capture)
        d = TopicDiscovery(pool)
        topics = [DiscoveredTopic(title="X", category="tech", source="hn", source_url="")]
        await d.queue_topics(topics)

        joined = "\n".join(seen)
        assert "pipeline_tasks" in joined
        assert "pipeline_versions" in joined
        assert "INSERT INTO content_tasks" not in joined


# ===========================================================================
# _scrape_hackernews
# ===========================================================================


class TestScrapeHackerNews:
    @pytest.mark.asyncio
    async def test_returns_topics_from_hn(self):
        d = TopicDiscovery(AsyncMock())

        def _mock_response(url):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "topstories" in url:
                resp.json.return_value = [1, 2]
            elif "/item/1" in url:
                resp.json.return_value = {"id": 1, "title": "AI Agent Framework Released", "url": "http://example.com", "score": 200}
            elif "/item/2" in url:
                resp.json.return_value = {"id": 2, "title": "Low Score Post", "url": "", "score": 10}
            return resp

        with patch("services.topic_sources.hackernews.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=lambda url, **_: _mock_response(url))
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

        # httpx is imported inside the source module after Phase F;
        # patch the source's httpx reference, not the legacy dispatcher's.
        with patch("services.topic_sources.hackernews.httpx.AsyncClient") as mock_client_cls:
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

        with patch("services.topic_sources.devto.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
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

        with patch("services.topic_sources.devto.httpx.AsyncClient") as mock_client_cls:
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
                assert isinstance(q, str)
                assert len(q) > 5


# ===========================================================================
# _is_news_or_junk
# ===========================================================================


class TestIsNewsOrJunk:
    # gh#218: _is_news_or_junk is an instance method now (was @staticmethod)
    # so the regex list can be sourced from app_settings via SiteConfig.
    # Each test instantiates a TopicDiscovery — the kwargless ctor falls back
    # to the module singleton, which in tests is unloaded so we get the
    # hardcoded _NEWS_RE fallback path.

    def _d(self):
        return TopicDiscovery(AsyncMock())

    def test_too_short_title_rejected(self):
        d = self._d()
        assert d._is_news_or_junk("Short title") is True
        assert d._is_news_or_junk("AI Tools") is True

    def test_long_title_not_rejected_for_length(self):
        assert self._d()._is_news_or_junk(
            "Building Production-Ready Microservices with Go"
        ) is False

    def test_lawsuit_pattern_rejected(self):
        # Real pattern from _NEWS_PATTERNS — "lawsuit" matches
        assert self._d()._is_news_or_junk(
            "Tech Giant Faces Major Lawsuit Over Privacy"
        ) is True

    def test_personal_anecdote_pattern_rejected(self):
        # "my experience" is in the news/junk pattern list
        assert self._d()._is_news_or_junk(
            "My experience building a startup from scratch"
        ) is True

    def test_merch_pattern_rejected(self):
        assert self._d()._is_news_or_junk(
            "Limited Edition Tech Merch and Sticker Pack"
        ) is True


# ===========================================================================
# gh#218 — DB-backed filter overrides (_resolved_news_re,
# _resolved_category_searches)
# ===========================================================================


class _FakeSiteConfig:
    """Minimal SiteConfig stand-in for gh#218 override tests.

    Only exposes the surface ``TopicDiscovery._resolved_*`` calls reach
    for: ``get(key, default)`` and ``all()``. Avoids importing the real
    ``SiteConfig`` class so tests stay decoupled from any Phase H churn
    on github/main.
    """

    def __init__(self, values: dict[str, str] | None = None):
        self._values = values or {}

    def get(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)

    def all(self) -> dict[str, str]:
        return dict(self._values)


class TestResolvedCategorySearches:
    def test_empty_falls_back_to_hardcoded(self):
        sc = _FakeSiteConfig({"topic_discovery_category_searches": "{}"})
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        # Empty JSON object -> hardcoded CATEGORY_SEARCHES fallback
        assert d._resolved_category_searches() is CATEGORY_SEARCHES

    def test_json_override_used(self):
        sc = _FakeSiteConfig({
            "topic_discovery_category_searches":
                '{"gardening": ["heirloom tomato varieties", "compost tea"]}',
        })
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        resolved = d._resolved_category_searches()
        assert "gardening" in resolved
        assert resolved["gardening"] == ["heirloom tomato varieties", "compost tea"]
        # Override replaces, doesn't merge
        assert "technology" not in resolved

    def test_malformed_json_falls_back(self):
        sc = _FakeSiteConfig({
            "topic_discovery_category_searches": "{not valid json",
        })
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        # Bad JSON -> hardcoded fallback, no exception raised
        assert d._resolved_category_searches() is CATEGORY_SEARCHES

    def test_result_cached_on_instance(self):
        sc = _FakeSiteConfig({"topic_discovery_category_searches": "{}"})
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        first = d._resolved_category_searches()
        second = d._resolved_category_searches()
        assert first is second  # cached, not re-parsed


class TestResolvedNewsRe:
    def test_empty_falls_back_to_hardcoded(self):
        sc = _FakeSiteConfig({"topic_discovery_news_patterns": "[]"})
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        # Empty array -> hardcoded _NEWS_RE fallback
        assert d._resolved_news_re() is TopicDiscovery._NEWS_RE

    def test_json_override_used(self):
        sc = _FakeSiteConfig({
            "topic_discovery_news_patterns":
                r'["\\b(?:foo|bar)\\b"]',
        })
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        compiled = d._resolved_news_re()
        # Operator's pattern, not the Glad Labs lawsuit/election set
        assert len(compiled) == 1
        assert compiled[0].search("things about foo here")

    def test_invalid_regex_skipped(self):
        # One bad pattern shouldn't break the rest — and shouldn't raise.
        sc = _FakeSiteConfig({
            "topic_discovery_news_patterns":
                r'["[unclosed", "\\b(?:keepme)\\b"]',
        })
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        compiled = d._resolved_news_re()
        assert len(compiled) == 1
        assert compiled[0].search("we should keepme out")

    def test_malformed_json_falls_back(self):
        sc = _FakeSiteConfig({
            "topic_discovery_news_patterns": "[not valid json",
        })
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        assert d._resolved_news_re() is TopicDiscovery._NEWS_RE

    def test_override_changes_is_news_or_junk_behaviour(self):
        # With an empty override, lawsuit titles still get rejected
        sc = _FakeSiteConfig({"topic_discovery_news_patterns": "[]"})
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        assert d._is_news_or_junk(
            "Tech Giant Faces Major Lawsuit Over Privacy"
        ) is True

        # With a custom override that doesn't include "lawsuit",
        # the same title should pass (real-estate/legal niche)
        sc2 = _FakeSiteConfig({
            "topic_discovery_news_patterns": r'["\\b(?:onlything)\\b"]',
        })
        d2 = TopicDiscovery(AsyncMock(), site_config=sc2)
        assert d2._is_news_or_junk(
            "Tech Giant Faces Major Lawsuit Over Privacy"
        ) is False


# ===========================================================================
# _search_by_category
# ===========================================================================


class TestSearchByCategory:
    @pytest.mark.asyncio
    async def test_returns_topics_from_research_results(self):
        d = TopicDiscovery(AsyncMock())

        fake_researcher = MagicMock()
        fake_researcher.search_simple = AsyncMock(return_value=[
            {"title": "Building Production Ready FastAPI Microservices", "url": "https://x.com/1"},
            {"title": "Modern Python Development Best Practices Guide", "url": "https://x.com/2"},
        ])

        with patch("services.web_research.WebResearcher", return_value=fake_researcher):
            result = await d._search_by_category(categories=["technology"])

        assert len(result) >= 1
        assert all(t.source == "ddg_search" for t in result)
        assert all(t.category == "technology" for t in result)

    @pytest.mark.asyncio
    async def test_empty_results_returns_empty_list(self):
        d = TopicDiscovery(AsyncMock())

        fake_researcher = MagicMock()
        fake_researcher.search_simple = AsyncMock(return_value=[])

        with patch("services.web_research.WebResearcher", return_value=fake_researcher):
            result = await d._search_by_category(categories=["technology"])

        assert result == []

    @pytest.mark.asyncio
    async def test_unknown_category_skipped(self):
        d = TopicDiscovery(AsyncMock())

        fake_researcher = MagicMock()
        fake_researcher.search_simple = AsyncMock(return_value=[])

        with patch("services.web_research.WebResearcher", return_value=fake_researcher):
            # Category not in CATEGORY_SEARCHES — no queries to issue
            result = await d._search_by_category(categories=["fake-category"])

        assert result == []

    @pytest.mark.asyncio
    async def test_research_exception_returns_empty(self):
        d = TopicDiscovery(AsyncMock())

        fake_researcher = MagicMock()
        fake_researcher.search_simple = AsyncMock(side_effect=RuntimeError("ddg down"))

        with patch("services.web_research.WebResearcher", return_value=fake_researcher):
            result = await d._search_by_category(categories=["technology"])

        # Exception swallowed; returns whatever was collected before the failure
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_filters_titles_that_rewrite_to_empty(self):
        """Titles rejected by _rewrite_as_blog_topic (e.g. Show HN) should not appear."""
        d = TopicDiscovery(AsyncMock())

        fake_researcher = MagicMock()
        fake_researcher.search_simple = AsyncMock(return_value=[
            {"title": "Show HN: My side project", "url": "https://x.com/1"},  # filtered
            {"title": "Production-Grade Kubernetes Patterns for Multi-Tenant Apps", "url": "https://x.com/2"},
        ])

        with patch("services.web_research.WebResearcher", return_value=fake_researcher):
            result = await d._search_by_category(categories=["technology"])

        # Show HN is filtered, only the real topic comes through
        titles = [t.title for t in result]
        assert not any("Show HN" in t for t in titles)


# ===========================================================================
# discover() — top-level orchestrator
# ===========================================================================


_ALL_SOURCES = {"knowledge", "codebase", "hackernews", "devto", "web_search"}


class TestDiscover:
    @pytest.mark.asyncio
    async def test_combines_sources_and_returns_top_n(self):
        pool = _make_pool()
        d = TopicDiscovery(pool)
        d._get_enabled_sources = AsyncMock(return_value=_ALL_SOURCES)

        # Stub all the source methods
        d._discover_from_knowledge = AsyncMock(return_value=[
            DiscoveredTopic(title="AI Coding Assistants for Solo Developers",
                           category="technology", source="brain", source_url="",
                           relevance_score=5.0),
        ])
        d._scrape_hackernews = AsyncMock(return_value=[
            DiscoveredTopic(title="Building Local LLM Inference Servers with Ollama",
                           category="technology", source="hn", source_url="",
                           relevance_score=4.0),
        ])
        d._scrape_devto = AsyncMock(return_value=[])
        d._search_by_category = AsyncMock(return_value=[])

        result = await d.discover(max_topics=5)
        # Both topics should pass _is_brand_relevant (they mention AI/LLM/Ollama)
        assert len(result) == 2
        # Higher relevance score wins the sort
        assert result[0].relevance_score >= result[1].relevance_score

    @pytest.mark.asyncio
    async def test_filters_brand_irrelevant(self):
        pool = _make_pool()
        d = TopicDiscovery(pool)
        d._get_enabled_sources = AsyncMock(return_value=_ALL_SOURCES)

        d._discover_from_knowledge = AsyncMock(return_value=[
            DiscoveredTopic(title="Best Recipes for Dinner Tonight",
                           category="technology", source="brain", source_url=""),
            DiscoveredTopic(title="Self-Hosted AI Pipelines for Solo Founders",
                           category="technology", source="brain", source_url=""),
        ])
        d._scrape_hackernews = AsyncMock(return_value=[])
        d._scrape_devto = AsyncMock(return_value=[])
        d._search_by_category = AsyncMock(return_value=[])

        result = await d.discover(max_topics=5)
        assert len(result) == 1
        assert "Self-Hosted AI" in result[0].title

    @pytest.mark.asyncio
    async def test_source_exception_does_not_crash(self):
        pool = _make_pool()
        d = TopicDiscovery(pool)
        d._get_enabled_sources = AsyncMock(return_value=_ALL_SOURCES)

        d._discover_from_knowledge = AsyncMock(return_value=[])
        d._scrape_hackernews = AsyncMock(side_effect=RuntimeError("hn down"))
        d._scrape_devto = AsyncMock(return_value=[
            DiscoveredTopic(title="GPU Local Inference Best Practices",
                           category="technology", source="devto", source_url=""),
        ])
        d._search_by_category = AsyncMock(return_value=[])

        # Should not raise — failed source is logged and skipped
        result = await d.discover(max_topics=5)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_max_topics_cap(self):
        pool = _make_pool()
        d = TopicDiscovery(pool)
        d._get_enabled_sources = AsyncMock(return_value=_ALL_SOURCES)

        # Generate 10 brand-relevant topics with distinct titles
        _titles = [
            "Building Local LLM Inference Pipelines with Ollama",
            "GPU Memory Management for Deep Learning Workloads",
            "Self-Hosted AI Content Generation Architecture",
            "Automated Quality Assurance for Machine Learning Outputs",
            "Vector Database Performance Benchmarks Compared",
            "Fine-Tuning Open Source Models on Consumer Hardware",
            "Prompt Engineering Patterns for Technical Writing",
            "Running Stable Diffusion Locally on RTX GPUs",
            "Embedding Search for Knowledge Base Retrieval",
            "Edge Inference Deployment Strategies for Solo Developers",
        ]
        many = [
            DiscoveredTopic(title=_titles[i],
                           category="technology", source="brain", source_url="",
                           relevance_score=float(i))
            for i in range(10)
        ]
        d._discover_from_knowledge = AsyncMock(return_value=many)
        d._scrape_hackernews = AsyncMock(return_value=[])
        d._scrape_devto = AsyncMock(return_value=[])
        d._search_by_category = AsyncMock(return_value=[])

        result = await d.discover(max_topics=3)
        assert len(result) == 3
        # Top 3 by relevance score
        scores = [t.relevance_score for t in result]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_category_filter_applied(self):
        pool = _make_pool()
        d = TopicDiscovery(pool)
        d._get_enabled_sources = AsyncMock(return_value=_ALL_SOURCES)

        d._discover_from_knowledge = AsyncMock(return_value=[
            DiscoveredTopic(title="AI Self-Hosted Pipeline for Solo Devs",
                           category="technology", source="brain", source_url=""),
            DiscoveredTopic(title="Self-Hosted GPU Inference Hardware Setup",
                           category="hardware", source="brain", source_url=""),
        ])
        d._scrape_hackernews = AsyncMock(return_value=[])
        d._scrape_devto = AsyncMock(return_value=[])
        d._search_by_category = AsyncMock(return_value=[])

        result = await d.discover(max_topics=5, categories=["hardware"])
        assert len(result) == 1
        assert result[0].category == "hardware"
