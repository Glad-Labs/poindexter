"""
Unit tests for services/research_service.py

Tests topic research via known references, internal link lookup,
web search integration, context building, and error handling.
All external dependencies (DB pool, web search) are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.research_service import KNOWN_REFERENCES, ResearchService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool():
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


@pytest.fixture()
def service(mock_pool):
    return ResearchService(pool=mock_pool)


@pytest.fixture()
def service_no_pool():
    return ResearchService(pool=None)


# ---------------------------------------------------------------------------
# _find_references — known reference matching
# ---------------------------------------------------------------------------


class TestFindReferences:
    def test_exact_keyword_match(self, service):
        refs = service._find_references("Setting up FastAPI with Docker")
        urls = {r["url"] for r in refs}
        assert "https://fastapi.tiangolo.com" in urls
        assert "https://docs.docker.com" in urls

    def test_case_insensitive(self, service):
        refs = service._find_references("FASTAPI deployment")
        assert any("fastapi" in r["url"] for r in refs)

    def test_no_match_returns_empty(self, service):
        refs = service._find_references("quantum entanglement overview")
        assert refs == []

    def test_deduplicates_urls(self, service):
        refs = service._find_references("next.js and nextjs comparison")
        urls = [r["url"] for r in refs]
        assert len(urls) == len(set(urls)), "Duplicate URLs found"

    def test_caps_at_eight(self, service):
        # Use a topic that matches many keywords
        big_topic = " ".join(KNOWN_REFERENCES.keys())
        refs = service._find_references(big_topic)
        assert len(refs) <= 8

    def test_partial_word_match(self, service):
        # "monitoring" contains word overlap with known refs
        refs = service._find_references("monitoring infrastructure")
        # Should pick up the monitoring refs
        assert len(refs) > 0

    def test_single_keyword(self, service):
        refs = service._find_references("redis caching patterns")
        urls = {r["url"] for r in refs}
        assert "https://redis.io/docs/latest/" in urls


# ---------------------------------------------------------------------------
# _find_internal_links — database lookup
# ---------------------------------------------------------------------------


class TestFindInternalLinks:
    @pytest.mark.asyncio
    async def test_returns_matching_posts(self, service, mock_pool):
        mock_pool.fetch.return_value = [
            {"title": "Getting Started with Docker", "slug": "getting-started-docker"},
            {"title": "Docker Compose Guide", "slug": "docker-compose-guide"},
        ]
        results = await service._find_internal_links("Docker deployment tips")
        assert len(results) == 2
        assert results[0]["title"] == "Getting Started with Docker"
        assert results[0]["slug"] == "getting-started-docker"

    @pytest.mark.asyncio
    async def test_returns_empty_without_pool(self, service_no_pool):
        results = await service_no_pool._find_internal_links("Docker tips")
        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_db_error(self, service, mock_pool):
        mock_pool.fetch.side_effect = Exception("connection refused")
        results = await service._find_internal_links("Docker tips")
        assert results == []

    @pytest.mark.asyncio
    async def test_short_words_skipped(self, service, mock_pool):
        # Words <= 3 chars are filtered out; "AI ML" has no 4+ char words
        results = await service._find_internal_links("AI ML")
        # Should return empty because no significant words
        assert results == []
        mock_pool.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_query_uses_topic_words(self, service, mock_pool):
        mock_pool.fetch.return_value = []
        await service._find_internal_links("kubernetes monitoring setup")
        mock_pool.fetch.assert_called_once()
        args = mock_pool.fetch.call_args
        # The pattern list should contain ILIKE patterns for topic words
        patterns = args[0][1]
        assert any("kubernetes" in p for p in patterns)


# ---------------------------------------------------------------------------
# _web_search — DuckDuckGo integration
# ---------------------------------------------------------------------------


class TestWebSearch:
    @pytest.mark.asyncio
    async def test_returns_results(self, service):
        mock_researcher = MagicMock()
        mock_researcher.search_simple = AsyncMock(return_value=[
            {"title": "Docker Guide", "url": "https://example.com/docker", "snippet": "A guide"},
        ])
        with patch("services.web_research.WebResearcher", return_value=mock_researcher):
            results = await service._web_search("Docker tips")
        assert len(results) == 1
        assert results[0]["title"] == "Docker Guide"

    @pytest.mark.asyncio
    async def test_returns_empty_on_import_error(self, service):
        with patch.dict("sys.modules", {"services.web_research": None}):
            results = await service._web_search("Docker tips")
        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_search_exception(self, service):
        mock_researcher = MagicMock()
        mock_researcher.search_simple = AsyncMock(side_effect=RuntimeError("network error"))
        with patch("services.web_research.WebResearcher", return_value=mock_researcher):
            results = await service._web_search("Docker tips")
        assert results == []


# ---------------------------------------------------------------------------
# build_context — full integration of all sources
# ---------------------------------------------------------------------------


class TestBuildContext:
    @pytest.mark.asyncio
    async def test_includes_verified_references(self, service, mock_pool):
        with patch.object(service, "_web_search", new_callable=AsyncMock, return_value=[]):
            context = await service.build_context("FastAPI best practices")
        assert "VERIFIED REFERENCE LINKS" in context
        assert "fastapi.tiangolo.com" in context

    @pytest.mark.asyncio
    async def test_includes_internal_links(self, service, mock_pool):
        mock_pool.fetch.return_value = [
            {"title": "FastAPI Intro", "slug": "fastapi-intro"},
        ]
        with patch.object(service, "_web_search", new_callable=AsyncMock, return_value=[]):
            context = await service.build_context("FastAPI tutorial")
        assert "EXISTING POSTS ON OUR SITE" in context
        assert "/posts/fastapi-intro" in context

    @pytest.mark.asyncio
    async def test_includes_web_results(self, service, mock_pool):
        web_results = [
            {"title": "Fresh Article", "url": "https://blog.example.com/fresh", "snippet": "Recent findings on the topic"},
        ]
        with patch.object(service, "_web_search", new_callable=AsyncMock, return_value=web_results):
            context = await service.build_context("FastAPI deployment")
        assert "RECENT WEB SOURCES" in context
        assert "Fresh Article" in context

    @pytest.mark.asyncio
    async def test_includes_citation_guidance_when_sources_exist(self, service, mock_pool):
        with patch.object(service, "_web_search", new_callable=AsyncMock, return_value=[]):
            context = await service.build_context("FastAPI patterns")
        assert "CITATION GUIDANCE" in context

    @pytest.mark.asyncio
    async def test_empty_context_for_unknown_topic(self, service_no_pool):
        with patch.object(service_no_pool, "_web_search", new_callable=AsyncMock, return_value=[]):
            context = await service_no_pool.build_context("quantum entanglement overview")
        # No refs, no pool, no web results -> empty
        assert context == ""

    @pytest.mark.asyncio
    async def test_no_citation_guidance_when_no_sources(self, service_no_pool):
        with patch.object(service_no_pool, "_web_search", new_callable=AsyncMock, return_value=[]):
            context = await service_no_pool.build_context("quantum entanglement overview")
        assert "CITATION GUIDANCE" not in context

    @pytest.mark.asyncio
    async def test_snippet_truncated_to_100_chars(self, service, mock_pool):
        long_snippet = "A" * 200
        web_results = [
            {"title": "Long", "url": "https://example.com", "snippet": long_snippet},
        ]
        with patch.object(service, "_web_search", new_callable=AsyncMock, return_value=web_results):
            context = await service.build_context("FastAPI guide")
        # The snippet in the context should be truncated
        # Find the line with the web result
        for line in context.split("\n"):
            if "Long" in line and "example.com" in line:
                # After the ): should be at most 100 chars of snippet
                snippet_part = line.split("): ")[1] if "): " in line else ""
                assert len(snippet_part) <= 100

    @pytest.mark.asyncio
    async def test_category_parameter_accepted(self, service, mock_pool):
        """build_context accepts category param without error."""
        with patch.object(service, "_web_search", new_callable=AsyncMock, return_value=[]):
            context = await service.build_context("Docker tips", category="devops")
        # Should not raise; category is accepted but currently unused
        assert isinstance(context, str)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_pool_and_settings(self):
        pool = MagicMock()
        settings = MagicMock()
        svc = ResearchService(pool=pool, settings_service=settings)
        assert svc.pool is pool
        assert svc.settings is settings

    def test_defaults_to_none(self):
        svc = ResearchService()
        assert svc.pool is None
        assert svc.settings is None


# ---------------------------------------------------------------------------
# KNOWN_REFERENCES integrity
# ---------------------------------------------------------------------------


class TestKnownReferences:
    def test_all_entries_have_title_and_url(self):
        for keyword, refs in KNOWN_REFERENCES.items():
            for ref in refs:
                assert "title" in ref, f"Missing title in {keyword}"
                assert "url" in ref, f"Missing url in {keyword}"
                assert ref["url"].startswith("http"), f"Bad URL in {keyword}: {ref['url']}"

    def test_no_duplicate_urls_within_keyword(self):
        for keyword, refs in KNOWN_REFERENCES.items():
            urls = [r["url"] for r in refs]
            assert len(urls) == len(set(urls)), f"Duplicate URLs under {keyword}"
