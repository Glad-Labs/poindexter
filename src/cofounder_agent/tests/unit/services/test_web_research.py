"""
Unit tests for services/web_research.py

Tests DuckDuckGo search, content extraction, and prompt formatting.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.web_research import WebResearcher


class TestSearchSimple:
    async def test_returns_results(self):
        researcher = WebResearcher()
        with patch("services.web_research.WebResearcher._ddg_search") as mock:
            mock.return_value = [
                {"title": "Test", "url": "https://example.com", "snippet": "A test", "content": ""},
            ]
            results = await researcher.search_simple("test query", num_results=1)
            assert len(results) == 1
            assert results[0]["title"] == "Test"

    async def test_empty_on_failure(self):
        researcher = WebResearcher()
        with patch("services.web_research.WebResearcher._ddg_search") as mock:
            mock.return_value = []
            results = await researcher.search_simple("nonexistent", num_results=3)
            assert results == []


class TestDDGSearch:
    async def test_handles_import_error(self):
        researcher = WebResearcher()
        with patch.dict("sys.modules", {"duckduckgo_search": None}):
            # Should fail gracefully
            results = await researcher._ddg_search("test", 3)
            # May return empty or raise — just shouldn't crash
            assert isinstance(results, list)


class TestExtractContent:
    async def test_extracts_from_html(self):
        researcher = WebResearcher()
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
        researcher = WebResearcher()
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
        researcher = WebResearcher()
        content = await researcher._extract_content("")
        assert content == ""


class TestFormatForPrompt:
    def test_formats_results(self):
        researcher = WebResearcher()
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
        researcher = WebResearcher()
        assert researcher.format_for_prompt([]) == ""

    def test_respects_max_chars(self):
        researcher = WebResearcher()
        results = [
            {"title": f"Article {i}", "url": f"https://example.com/{i}", "snippet": "x" * 200, "content": "y" * 500}
            for i in range(20)
        ]
        formatted = researcher.format_for_prompt(results, max_chars=500)
        assert len(formatted) < 800  # Some overhead but respects limit
