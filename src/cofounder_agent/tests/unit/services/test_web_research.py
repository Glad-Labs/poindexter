"""
Unit tests for services/web_research.py

Tests DuckDuckGo search, content extraction, and prompt formatting.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.site_config import SiteConfig
from services.web_research import WebResearcher


def _sc() -> SiteConfig:
    """Fresh SiteConfig — Phase H DI (GH#95)."""
    return SiteConfig()


class TestSearchSimple:
    async def test_returns_results(self):
        researcher = WebResearcher(site_config=_sc())
        with patch("services.web_research.WebResearcher._ddg_search") as mock:
            mock.return_value = [
                {"title": "Test", "url": "https://example.com", "snippet": "A test", "content": ""},
            ]
            results = await researcher.search_simple("test query", num_results=1)
            assert len(results) == 1
            assert results[0]["title"] == "Test"

    async def test_empty_on_failure(self):
        researcher = WebResearcher(site_config=_sc())
        with patch("services.web_research.WebResearcher._ddg_search") as mock:
            mock.return_value = []
            results = await researcher.search_simple("nonexistent", num_results=3)
            assert results == []


class TestDDGSearch:
    async def test_handles_import_error(self):
        researcher = WebResearcher(site_config=_sc())
        with patch.dict("sys.modules", {"duckduckgo_search": None}):
            # Should fail gracefully
            results = await researcher._ddg_search("test", 3)
            # May return empty or raise — just shouldn't crash
            assert isinstance(results, list)


class TestExtractContent:
    async def test_extracts_from_html(self):
        researcher = WebResearcher(site_config=_sc())
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = """
            <html><body>
            <article>
                <h1>Test Article</h1>
                <p>This is the main content of the article with enough words to be useful.</p>
            </article>
            </body></html>
            """
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            content = await researcher._extract_content("https://example.com/article")
            assert "main content" in content

    async def test_handles_404(self):
        researcher = WebResearcher(site_config=_sc())
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            content = await researcher._extract_content("https://example.com/missing")
            assert content == ""

    async def test_empty_url_returns_empty(self):
        researcher = WebResearcher(site_config=_sc())
        content = await researcher._extract_content("")
        assert content == ""


class TestFormatForPrompt:
    def test_formats_results(self):
        researcher = WebResearcher(site_config=_sc())
        results = [
            {"title": "FastAPI Guide", "url": "https://fastapi.dev", "snippet": "Learn FastAPI", "content": "FastAPI is a modern framework."},
            {"title": "Django vs Flask", "url": "https://blog.dev", "snippet": "Comparison", "content": ""},
        ]
        formatted = researcher.format_for_prompt(results)
        assert "WEB RESEARCH" in formatted
        assert "FastAPI Guide" in formatted
        assert "https://fastapi.dev" in formatted
        assert "Learn FastAPI" in formatted

    def test_empty_results(self):
        researcher = WebResearcher(site_config=_sc())
        assert researcher.format_for_prompt([]) == ""

    def test_respects_max_chars(self):
        researcher = WebResearcher(site_config=_sc())
        results = [
            {"title": f"Article {i}", "url": f"https://example.com/{i}", "snippet": "x" * 200, "content": "y" * 500}
            for i in range(20)
        ]
        formatted = researcher.format_for_prompt(results, max_chars=500)
        assert len(formatted) < 800  # Some overhead but respects limit


class TestSearchFullPipeline:
    """Coverage for the main search() method which combines DDG + content fetch."""

    @pytest.mark.asyncio
    async def test_returns_enriched_results(self):
        researcher = WebResearcher(site_config=_sc())

        async def fake_ddg(query, n):
            return [
                {"title": "A", "url": "https://a.example", "snippet": "snip a", "content": ""},
                {"title": "B", "url": "https://b.example", "snippet": "snip b", "content": ""},
            ]

        async def fake_extract(url):
            return f"content from {url}"

        with patch.object(WebResearcher, "_ddg_search", side_effect=fake_ddg), \
             patch.object(WebResearcher, "_extract_content", side_effect=fake_extract):
            results = await researcher.search("query", num_results=2)

        assert len(results) == 2
        urls = {r["url"] for r in results}
        assert urls == {"https://a.example", "https://b.example"}
        for r in results:
            assert r["content"] == f"content from {r['url']}"

    @pytest.mark.asyncio
    async def test_empty_ddg_returns_empty(self):
        researcher = WebResearcher(site_config=_sc())
        with patch.object(WebResearcher, "_ddg_search", return_value=[]):
            results = await researcher.search("nothing", num_results=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_extract_failures_filtered_out(self):
        """If one fetch raises, gather should not lose the others."""
        researcher = WebResearcher(site_config=_sc())

        async def fake_ddg(query, n):
            return [
                {"title": "Good", "url": "https://good.example", "snippet": "ok", "content": ""},
                {"title": "Bad", "url": "https://bad.example", "snippet": "boom", "content": ""},
            ]

        async def fake_extract(url):
            if "bad" in url:
                raise RuntimeError("fetch failed")
            return "good content"

        with patch.object(WebResearcher, "_ddg_search", side_effect=fake_ddg), \
             patch.object(WebResearcher, "_extract_content", side_effect=fake_extract):
            results = await researcher.search("q")

        # The failing fetch returns an Exception via return_exceptions=True;
        # the dict-or-not filter drops it. The good one stays.
        assert len(results) == 1
        assert results[0]["url"] == "https://good.example"
        assert results[0]["content"] == "good content"


class TestDDGTimeoutPath:
    @pytest.mark.asyncio
    async def test_ddg_timeout_returns_empty(self):
        """When asyncio.wait_for times out, _ddg_search must return []."""
        researcher = WebResearcher(site_config=_sc())

        # Patch run_in_executor to return a future that never completes
        # by raising TimeoutError directly on the wait_for.
        async def _slow_wait_for(*args, **kwargs):
            raise asyncio.TimeoutError()

        # Make sure the import resolves so we don't hit the ImportError branch
        with patch.dict("sys.modules", {"duckduckgo_search": MagicMock(DDGS=MagicMock())}), \
             patch("asyncio.wait_for", new=_slow_wait_for):
            results = await researcher._ddg_search("test query", 5)
            assert results == []

    @pytest.mark.asyncio
    async def test_ddg_returns_results_on_success(self):
        """Verify _ddg_search transforms raw DDG output into the expected dict shape.

        The real library is `ddgs` (new name); `duckduckgo_search` is only
        a legacy fallback. Patch BOTH so whichever the code picks, the mock
        wins and no real network call fires.
        """
        fake_ddgs = MagicMock()
        fake_ddgs_instance = MagicMock()
        fake_ddgs_instance.text.return_value = iter([
            {"title": "T1", "href": "https://t1.example", "body": "body 1"},
            {"title": "T2", "link": "https://t2.example", "snippet": "body 2"},
            {"title": "Skipped", "body": "no link, dropped"},  # no href/link → filtered
        ])
        fake_ddgs.return_value.__enter__ = MagicMock(return_value=fake_ddgs_instance)
        fake_ddgs.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict("sys.modules", {
            "ddgs": MagicMock(DDGS=fake_ddgs),
            "duckduckgo_search": MagicMock(DDGS=fake_ddgs),
        }):
            researcher = WebResearcher(site_config=_sc())
            results = await researcher._ddg_search("test", 3)

        assert len(results) == 2
        assert results[0]["url"] == "https://t1.example"
        assert results[0]["snippet"] == "body 1"
        assert results[1]["url"] == "https://t2.example"
        # Note: second result uses 'snippet' key not 'body', code checks both
        assert results[1]["snippet"] == "body 2"


class TestExtractContentEdgeCases:
    @pytest.mark.asyncio
    async def test_no_article_or_main_or_body_returns_empty(self):
        """If the page has none of the expected containers, extract returns ''."""
        researcher = WebResearcher(site_config=_sc())
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "<html></html>"  # no body
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            content = await researcher._extract_content("https://example.com/empty")
        assert content == ""

    @pytest.mark.asyncio
    async def test_strips_script_and_style(self):
        researcher = WebResearcher(site_config=_sc())
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = """
            <html><body>
                <script>var noise = 'should not appear';</script>
                <style>.x{color:red}</style>
                <article><p>Real content here.</p></article>
            </body></html>
            """
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            content = await researcher._extract_content("https://example.com")

        assert "Real content here" in content
        assert "should not appear" not in content
        assert "color:red" not in content

    @pytest.mark.asyncio
    async def test_truncates_to_max_chars(self):
        from services.web_research import MAX_CONTENT_CHARS
        researcher = WebResearcher(site_config=_sc())
        long_text = "lorem ipsum " * 1000  # ~12000 chars
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = f"<html><body><article>{long_text}</article></body></html>"
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            content = await researcher._extract_content("https://example.com")
        assert len(content) <= MAX_CONTENT_CHARS

    @pytest.mark.asyncio
    async def test_network_error_returns_empty(self):
        researcher = WebResearcher(site_config=_sc())
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=Exception("connection refused"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            content = await researcher._extract_content("https://example.com")
        assert content == ""
