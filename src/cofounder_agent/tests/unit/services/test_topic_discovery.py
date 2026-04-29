"""
Unit tests for services/topic_discovery.py

Tests topic scraping, deduplication, classification, queuing,
brand relevance filtering, and source scrapers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.site_config import SiteConfig
from services.topic_discovery import CATEGORY_SEARCHES, DiscoveredTopic, TopicDiscovery

# Shared empty SiteConfig for tests that don't care about config values.
# TopicDiscovery requires site_config (Phase H DI, GH#95) — tests that
# exercise config-dependent branches should construct their own
# SiteConfig(initial_config={...}) with the keys they need.
_TEST_SC = SiteConfig(initial_config={})


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
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)
        assert d._classify_category("Zero Trust Architecture for Developers") == "security"

    def test_startup_topic(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)
        assert d._classify_category("How to Launch Your MVP in a Weekend") == "startup"

    def test_engineering_topic(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)
        assert d._classify_category("Monorepo vs Polyrepo Architecture Patterns") == "engineering"

    def test_default_to_technology(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)
        assert d._classify_category("Something completely unrelated to anything") == "technology"

    def test_hardware_topic(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)
        assert d._classify_category("AMD vs NVIDIA GPU Benchmarks") == "hardware"

    def test_gaming_topic(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)
        assert d._classify_category("Indie Game Development with Godot Engine") == "gaming"

    def test_business_topic(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)
        assert d._classify_category("SaaS Metrics That Matter for Growth") == "business"

    def test_insights_topic(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)
        assert d._classify_category("State of Developer Productivity Survey Results") == "insights"


# ===========================================================================
# _rewrite_as_blog_topic
# ===========================================================================


class TestRewriteTitle:
    def test_removes_show_hn_prefix(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)
        assert d._rewrite_as_blog_topic("[Show HN] My Cool Project") == "My Cool Project"

    def test_removes_site_suffix(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)
        assert d._rewrite_as_blog_topic("Cool Article | TechCrunch") == "Cool Article"

    def test_clean_title_unchanged(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)
        assert d._rewrite_as_blog_topic("A Perfectly Normal Blog Title") == "A Perfectly Normal Blog Title"

    def test_removes_ask_hn_prefix(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)
        result = d._rewrite_as_blog_topic("[Ask HN] Best way to deploy ML models?")
        assert "[Ask HN]" not in result

    def test_removes_dash_site_suffix(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)
        result = d._rewrite_as_blog_topic("Cool New Framework - InfoWorld")
        assert "InfoWorld" not in result


# ===========================================================================
# _is_brand_relevant
# ===========================================================================


class TestIsBrandRelevant:
    """Default behaviour: when ``brand_keywords`` is not set in app_settings
    (the empty-SiteConfig case used by these tests), the dispatcher falls
    back to the hardcoded ``_BRAND_KEYWORDS`` set — Glad Labs's
    AI/gaming/PC-hardware niche."""

    def _td(self):
        return TopicDiscovery(AsyncMock(), site_config=_TEST_SC)

    def test_ai_topic_is_relevant(self):
        assert self._td()._is_brand_relevant("Building AI Agents with LLMs") is True

    def test_gpu_topic_is_relevant(self):
        assert self._td()._is_brand_relevant("NVIDIA RTX 5090 Benchmarks") is True

    def test_gaming_topic_is_relevant(self):
        assert self._td()._is_brand_relevant("Best Steam Games of 2026") is True

    def test_self_hosted_topic_is_relevant(self):
        assert self._td()._is_brand_relevant("Self-Hosted AI Content Pipeline for Solo Founders") is True

    def test_local_inference_topic_is_relevant(self):
        assert self._td()._is_brand_relevant("Running Local Inference with Ollama on Your Own Hardware") is True

    def test_docker_topic_is_relevant(self):
        assert self._td()._is_brand_relevant("Docker Kubernetes CI/CD Pipeline") is True

    def test_unrelated_topic_not_relevant(self):
        assert self._td()._is_brand_relevant("Best Recipes for Dinner Tonight") is False

    def test_empty_string(self):
        assert self._td()._is_brand_relevant("") is False

    def test_case_insensitive(self):
        assert self._td()._is_brand_relevant("MACHINE LEARNING Trends") is True

    def test_ollama_relevant(self):
        assert self._td()._is_brand_relevant("Running Ollama Locally") is True

    def test_self_hosted_relevant(self):
        assert self._td()._is_brand_relevant("Self-Host Your Own Blog") is True


# ===========================================================================
# _is_brand_relevant — site_config override (gh#216)
# ===========================================================================


class TestIsBrandRelevantOverride:
    """gh#216: brand_keywords is configurable via app_settings so non-Glad-Labs
    niches (gardener, dentist, indie musician) aren't filtered out by the
    AI/PC/gaming-niche hardcoded fallback."""

    def test_empty_setting_falls_back_to_hardcoded(self):
        """An empty (or whitespace-only) app_settings value must NOT swallow
        the hardcoded set — otherwise existing Glad Labs deployments that
        haven't explicitly set the setting would start rejecting every topic."""
        sc = SiteConfig(initial_config={"brand_keywords": ""})
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        assert d._is_brand_relevant("Building AI Agents with LLMs") is True
        assert d._is_brand_relevant("Running Ollama Locally") is True

        sc_ws = SiteConfig(initial_config={"brand_keywords": "   "})
        d_ws = TopicDiscovery(AsyncMock(), site_config=sc_ws)
        assert d_ws._is_brand_relevant("NVIDIA RTX 5090 Benchmarks") is True

    def test_unset_setting_falls_back_to_hardcoded(self):
        """When the key is absent entirely (not even an empty row), behaviour
        is identical to the empty-string case — fall back to hardcoded."""
        sc = SiteConfig(initial_config={})
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        assert d._is_brand_relevant("Building AI Agents with LLMs") is True

    def test_custom_keywords_replace_hardcoded(self):
        """When the override IS set, it is the authoritative list — the
        hardcoded Glad Labs niche must NOT bleed through (otherwise a
        gardener customer would still match 'AI'/'GPU'/etc and produce
        off-niche content)."""
        sc = SiteConfig(
            initial_config={"brand_keywords": "gardening,compost,heirloom tomato"}
        )
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        # Custom keywords match (word boundaries respected for single words)
        assert d._is_brand_relevant("Compost Bin Setup for the Backyard") is True
        assert d._is_brand_relevant("Heirloom Tomato Varieties for Zone 6") is True
        assert d._is_brand_relevant("Container Gardening Tips") is True
        # Glad Labs niche keywords no longer match
        assert d._is_brand_relevant("Building AI Agents with LLMs") is False
        assert d._is_brand_relevant("NVIDIA RTX 5090 Benchmarks") is False

    def test_custom_keywords_case_insensitive(self):
        """Override matching keeps the same case-insensitive semantics as the
        hardcoded path — operators don't have to think about casing when
        seeding their niche keywords."""
        sc = SiteConfig(initial_config={"brand_keywords": "Gardening,Compost"})
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        # Title-case keyword vs upper-case title
        assert d._is_brand_relevant("GARDENING TIPS FOR BEGINNERS") is True
        # Lower-case keyword vs mixed-case title
        sc2 = SiteConfig(initial_config={"brand_keywords": "compost"})
        d2 = TopicDiscovery(AsyncMock(), site_config=sc2)
        assert d2._is_brand_relevant("How To Compost At Home") is True

    def test_custom_keywords_handle_whitespace_and_empties(self):
        """Operators will inevitably leave stray spaces / trailing commas in
        the comma-separated string — those should be tolerated, not turn
        into ghost keywords that match every title."""
        sc = SiteConfig(
            initial_config={"brand_keywords": " gardening , , compost ,"}
        )
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        assert d._is_brand_relevant("Gardening Tips") is True
        assert d._is_brand_relevant("Compost Bin Setup") is True
        # Empty string between commas must not match arbitrary text
        assert d._is_brand_relevant("Best Recipes for Dinner Tonight") is False

    def test_custom_keywords_word_boundary_for_single_word(self):
        """Word-boundary matching is preserved through the override path —
        a single-word keyword 'art' must not match 'cartoon' or 'smart'."""
        sc = SiteConfig(initial_config={"brand_keywords": "art"})
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        assert d._is_brand_relevant("Modern Art Movements") is True
        assert d._is_brand_relevant("Smart Home Hubs Reviewed") is False
        assert d._is_brand_relevant("Cartoon History") is False


# ===========================================================================
# _deduplicate
# ===========================================================================


class TestDeduplicate:
    @pytest.mark.asyncio
    async def test_marks_exact_duplicates(self):
        pool = _make_pool(published_titles=["Docker Best Practices"])
        d = TopicDiscovery(pool, site_config=_TEST_SC)
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
        d = TopicDiscovery(pool, site_config=_TEST_SC)
        topics = [
            DiscoveredTopic(title="how to use docker containers in production",
                           category="technology", source="hn", source_url=""),
        ]
        result = await d._deduplicate(topics)
        assert result[0].is_duplicate is True  # >60% word overlap

    @pytest.mark.asyncio
    async def test_no_pool_skips_dedup(self):
        d = TopicDiscovery(None, site_config=_TEST_SC)
        topics = [DiscoveredTopic(title="Test", category="tech", source="hn", source_url="")]
        result = await d._deduplicate(topics)
        assert result[0].is_duplicate is False

    @pytest.mark.asyncio
    async def test_marks_pending_task_duplicates(self):
        pool = _make_pool(pending_topics=["Kubernetes Scaling Guide"])
        d = TopicDiscovery(pool, site_config=_TEST_SC)
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
        d = TopicDiscovery(pool, site_config=_TEST_SC)
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
        d = TopicDiscovery(pool, site_config=_TEST_SC)
        topics = [DiscoveredTopic(title="Test", category="tech", source="hn", source_url="")]
        result = await d._deduplicate(topics)
        assert result[0].is_duplicate is False  # Graceful fallback

    @pytest.mark.asyncio
    async def test_distinct_events_share_keywords_but_not_dups(self):
        """gitea#279: 'Join our DEV Weekend Challenge — $1,000' and
        'Join the OpenClaw Challenge: $1,200 Prizes' share Challenge / 1 / Join
        but reference different events. The previous hardcoded 0.4 threshold
        flagged them as dups; the 0.7 default does not."""
        pool = _make_pool(published_titles=["Join our DEV Weekend Challenge $1,000"])
        d = TopicDiscovery(pool, site_config=_TEST_SC)
        topics = [
            DiscoveredTopic(
                title="Join the OpenClaw Challenge: $1,200 Prizes",
                category="technology", source="devto", source_url="",
            ),
        ]
        result = await d._deduplicate(topics)
        assert result[0].is_duplicate is False

    @pytest.mark.asyncio
    async def test_existing_threshold_is_tunable(self):
        """Override `topic_dedup_existing_threshold` so an unrelated pair
        falls below the (now lower) threshold and gets flagged. Proves the
        site_config plumbing reads the live value."""
        sc = SiteConfig(initial_config={"topic_dedup_existing_threshold": "0.3"})
        pool = _make_pool(published_titles=["Join our DEV Weekend Challenge $1,000"])
        d = TopicDiscovery(pool, site_config=sc)
        topics = [
            DiscoveredTopic(
                title="Join the OpenClaw Challenge: $1,200 Prizes",
                category="technology", source="devto", source_url="",
            ),
        ]
        result = await d._deduplicate(topics)
        assert result[0].is_duplicate is True

    @pytest.mark.asyncio
    async def test_intra_batch_threshold_is_tunable(self):
        """Two distinct candidates from the same batch share keywords. At the
        loose default they're not flagged; lowering the intra-batch threshold
        to 0.3 should mark the second as a dup of the first."""
        sc_loose = SiteConfig(initial_config={})
        sc_tight = SiteConfig(initial_config={"topic_dedup_intra_batch_threshold": "0.3"})
        topics_loose = [
            DiscoveredTopic(title="Top 7 Featured DEV Posts of the Week",
                            category="business", source="devto", source_url=""),
            DiscoveredTopic(title="What was your win this week?",
                            category="business", source="devto", source_url=""),
        ]
        topics_tight = [
            DiscoveredTopic(title="Top 7 Featured DEV Posts of the Week",
                            category="business", source="devto", source_url=""),
            DiscoveredTopic(title="What was your win this week?",
                            category="business", source="devto", source_url=""),
        ]
        d_loose = TopicDiscovery(_make_pool(), site_config=sc_loose)
        d_tight = TopicDiscovery(_make_pool(), site_config=sc_tight)
        await d_loose._deduplicate(topics_loose)
        await d_tight._deduplicate(topics_tight)
        assert topics_loose[1].is_duplicate is False
        assert topics_tight[1].is_duplicate is True


# ===========================================================================
# queue_topics
# ===========================================================================


class TestQueueTopics:
    @pytest.mark.asyncio
    async def test_queues_to_database(self):
        pool = AsyncMock()
        pool.execute = AsyncMock()
        d = TopicDiscovery(pool, site_config=_TEST_SC)
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
        d = TopicDiscovery(pool, site_config=_TEST_SC)
        topics = [DiscoveredTopic(title="Dup", category="tech", source="hn", source_url="")]
        queued = await d.queue_topics(topics)
        assert queued == 0

    @pytest.mark.asyncio
    async def test_queues_multiple_topics(self):
        pool = AsyncMock()
        pool.execute = AsyncMock()
        d = TopicDiscovery(pool, site_config=_TEST_SC)
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
        d = TopicDiscovery(pool, site_config=_TEST_SC)
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
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)

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
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)

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
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)

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
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)

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
    # Each test instantiates a TopicDiscovery with the empty test SiteConfig
    # so the hardcoded _NEWS_RE fallback path is exercised.

    def _d(self):
        return TopicDiscovery(AsyncMock(), site_config=_TEST_SC)

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

    def test_truncated_trailing_preposition_rejected(self):
        """gitea#279 follow-up: scrape-truncated titles like
        'Top Cybersecurity Threats in' must be rejected — the trailing
        preposition is a strong signal the source cut off mid-phrase.
        Real evergreen titles never end with these tokens."""
        d = self._d()
        assert d._is_news_or_junk("Top Cybersecurity Threats in") is True
        assert d._is_news_or_junk("Different Language Models Learn") is False  # ends with verb, not preposition
        assert d._is_news_or_junk("Best Practices for") is True
        assert d._is_news_or_junk("How to Deploy Apps with") is True
        assert d._is_news_or_junk("Choosing Frameworks Using") is True

    def test_leading_emoji_rejected(self):
        """gitea#279 follow-up: emoji-led devto/medium clickbait
        ('🦸Let Superheroes Cheer You Up...') is junk that previously
        slipped past brand-relevance on a coincidental 'AI' match."""
        d = self._d()
        assert d._is_news_or_junk(
            "🦸Let Superheroes Cheer You Up (AI Avatar v6: Chrome Extension)"
        ) is True
        assert d._is_news_or_junk(
            "🚀Rocket-fast Startup Strategies for AI Founders"
        ) is True

    def test_bracket_prefix_not_rejected(self):
        """[Show HN] / [Ask HN] prefixes are legitimate — those go through
        the rewrite path, not the leading-emoji reject."""
        assert self._d()._is_news_or_junk(
            "[Show HN] My open-source tool for content automation"
        ) is False

    def test_normal_title_not_rejected(self):
        """Sanity check: a perfectly normal title still passes."""
        assert self._d()._is_news_or_junk(
            "Building Production-Ready Microservices with Go"
        ) is False

    def test_db_override_replaces_hardcoded_patterns(self):
        """gh#218: an operator-supplied JSON-array override in
        app_settings.topic_discovery_news_patterns replaces the
        hardcoded list — a real-estate niche customer who removes
        'lawsuit' from the patterns should see those titles pass."""
        sc = SiteConfig(initial_config={
            "topic_discovery_news_patterns": '["\\\\b(?:merch|sticker|swag)\\\\b"]',
        })
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        # 'lawsuit' is no longer in the active reject list
        assert d._is_news_or_junk(
            "Tech Giant Faces Major Lawsuit Over Privacy"
        ) is False
        # 'merch' still matches because the operator kept it
        assert d._is_news_or_junk(
            "Limited Edition Tech Merch Drop for 2026"
        ) is True

    def test_db_override_empty_array_falls_back_to_hardcoded(self):
        """gh#218: an explicit '[]' (the default seeded by migration
        0111) is semantically the same as 'no override has been
        configured' — it falls back to the hardcoded _NEWS_RE so
        existing Glad Labs deployments keep their behaviour. This
        mirrors the brand_keywords / migration 0105 precedent: ship
        empty, fall back to hardcoded, force customers to opt in."""
        sc = SiteConfig(initial_config={
            "topic_discovery_news_patterns": "[]",
        })
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        # Lawsuit still rejected — fell back to hardcoded list
        assert d._is_news_or_junk(
            "Tech Giant Faces Major Lawsuit Over Privacy"
        ) is True
        # Non-regex filters always apply
        assert d._is_news_or_junk("Top Cybersecurity Threats in") is True
        assert d._is_news_or_junk("AI Tools") is True

    def test_db_override_invalid_json_falls_back_to_hardcoded(self):
        """gh#218: a malformed JSON value should not take the dispatcher
        offline — it logs a warning and falls back to _NEWS_RE."""
        sc = SiteConfig(initial_config={
            "topic_discovery_news_patterns": "not valid json [",
        })
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        assert d._is_news_or_junk(
            "Tech Giant Faces Major Lawsuit Over Privacy"
        ) is True


# ===========================================================================
# _classify_category — DB override (gh#218)
# ===========================================================================


class TestClassifyCategoryDbOverride:
    def test_override_replaces_hardcoded_categories(self):
        """gh#218: a customer-supplied
        app_settings.topic_discovery_category_searches overrides the
        hardcoded CATEGORY_SEARCHES — a gardener's titles bucket into
        gardening categories instead of always defaulting to 'technology'."""
        import json as _json
        override = {
            "gardening": ["heirloom tomato", "compost tea", "raised bed"],
            "tools": ["pruning shears", "soil test", "drip irrigation"],
        }
        sc = SiteConfig(initial_config={
            "topic_discovery_category_searches": _json.dumps(override),
        })
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        assert d._classify_category(
            "Best heirloom tomato varieties for cool climates"
        ) == "gardening"
        assert d._classify_category(
            "How drip irrigation saves your raised bed"
        ) in ("gardening", "tools")  # both have matching keywords
        # No matches → falls through to the always-on 'technology' default
        assert d._classify_category(
            "Quantum chromodynamics explained"
        ) == "technology"

    def test_invalid_json_override_falls_back_to_hardcoded(self):
        sc = SiteConfig(initial_config={
            "topic_discovery_category_searches": "{not valid json",
        })
        d = TopicDiscovery(AsyncMock(), site_config=sc)
        # Hardcoded CATEGORY_SEARCHES still classifies hardware titles
        assert d._classify_category("AMD vs NVIDIA GPU Benchmarks") == "hardware"


# ===========================================================================
# _search_by_category
# ===========================================================================


class TestSearchByCategory:
    @pytest.mark.asyncio
    async def test_returns_topics_from_research_results(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)

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
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)

        fake_researcher = MagicMock()
        fake_researcher.search_simple = AsyncMock(return_value=[])

        with patch("services.web_research.WebResearcher", return_value=fake_researcher):
            result = await d._search_by_category(categories=["technology"])

        assert result == []

    @pytest.mark.asyncio
    async def test_unknown_category_skipped(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)

        fake_researcher = MagicMock()
        fake_researcher.search_simple = AsyncMock(return_value=[])

        with patch("services.web_research.WebResearcher", return_value=fake_researcher):
            # Category not in CATEGORY_SEARCHES — no queries to issue
            result = await d._search_by_category(categories=["fake-category"])

        assert result == []

    @pytest.mark.asyncio
    async def test_research_exception_returns_empty(self):
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)

        fake_researcher = MagicMock()
        fake_researcher.search_simple = AsyncMock(side_effect=RuntimeError("ddg down"))

        with patch("services.web_research.WebResearcher", return_value=fake_researcher):
            result = await d._search_by_category(categories=["technology"])

        # Exception swallowed; returns whatever was collected before the failure
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_filters_titles_that_rewrite_to_empty(self):
        """Titles rejected by _rewrite_as_blog_topic (e.g. Show HN) should not appear."""
        d = TopicDiscovery(AsyncMock(), site_config=_TEST_SC)

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
        d = TopicDiscovery(pool, site_config=_TEST_SC)
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
        d = TopicDiscovery(pool, site_config=_TEST_SC)
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
        d = TopicDiscovery(pool, site_config=_TEST_SC)
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
        d = TopicDiscovery(pool, site_config=_TEST_SC)
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
        d = TopicDiscovery(pool, site_config=_TEST_SC)
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
