"""
Unit tests for services/devto_service.py

Tests markdown cleaning, tag normalization, cross-posting via httpx,
and graceful skip when the Dev.to API key is not configured.
All database and HTTP calls are mocked — no real connections required.
"""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

# Ensure site_config returns a test URL for SITE_URL
from services.site_config import site_config

site_config._config["site_url"] = "https://test.example.com"

# _site_url() is now a lazy function (2026-04-11 fix for module-import-time
# silent swallow). Call it once to get the value for test assertions.
from services.devto_service import _site_url, DevToCrossPostService

SITE_URL = _site_url()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_mock_pool(api_key_row=None) -> AsyncMock:
    """Return an AsyncMock that behaves like an asyncpg pool."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=api_key_row)
    return pool


# ---------------------------------------------------------------------------
# _clean_markdown
# ---------------------------------------------------------------------------


class TestCleanMarkdown:
    """Test markdown preparation for Dev.to."""

    def test_relative_links_converted_to_absolute(self):
        md = "Read [this post](/posts/my-slug) for details."
        result = DevToCrossPostService._clean_markdown(md)
        assert f"{SITE_URL}/posts/my-slug" in result
        assert "(/posts/my-slug)" not in result

    def test_relative_image_paths_converted_to_absolute(self):
        md = "![screenshot](/images/demo.png)"
        result = DevToCrossPostService._clean_markdown(md)
        assert f"{SITE_URL}/images/demo.png" in result

    def test_absolute_links_unchanged(self):
        md = "[example](https://example.com/page)"
        result = DevToCrossPostService._clean_markdown(md)
        assert "https://example.com/page" in result
        assert SITE_URL not in result

    def test_script_tags_stripped(self):
        md = "Before<script>alert('xss')</script>After"
        result = DevToCrossPostService._clean_markdown(md)
        assert "<script" not in result
        assert "alert" not in result
        assert "BeforeAfter" in result

    def test_iframe_tags_stripped(self):
        md = 'Text<iframe src="https://evil.com"></iframe>More'
        result = DevToCrossPostService._clean_markdown(md)
        assert "<iframe" not in result
        assert "TextMore" in result

    def test_html_comments_stripped(self):
        md = "Visible<!-- hidden comment -->Also visible"
        result = DevToCrossPostService._clean_markdown(md)
        assert "<!--" not in result
        assert "hidden comment" not in result
        assert "VisibleAlso visible" in result

    def test_custom_react_components_stripped(self):
        md = "Before\n<ViewTracker />\n<AdSense slot=\"123\" />\nAfter"
        result = DevToCrossPostService._clean_markdown(md)
        assert "<ViewTracker" not in result
        assert "<AdSense" not in result
        assert "Before" in result
        assert "After" in result

    def test_plain_markdown_unchanged(self):
        md = "## Hello World\n\nThis is a paragraph."
        result = DevToCrossPostService._clean_markdown(md)
        assert result == md

    def test_empty_input(self):
        assert DevToCrossPostService._clean_markdown("") == ""

    def test_multiple_relative_links(self):
        md = "[A](/posts/a) and [B](/posts/b)"
        result = DevToCrossPostService._clean_markdown(md)
        assert f"{SITE_URL}/posts/a" in result
        assert f"{SITE_URL}/posts/b" in result


# ---------------------------------------------------------------------------
# _normalize_tags
# ---------------------------------------------------------------------------


class TestNormalizeTags:
    """Test tag normalization for Dev.to."""

    def test_lowercase(self):
        assert DevToCrossPostService._normalize_tags(["LLM", "AI"]) == ["llm", "ai"]

    def test_max_four_tags(self):
        tags = ["python", "javascript", "docker", "kubernetes", "react", "fastapi"]
        result = DevToCrossPostService._normalize_tags(tags)
        assert len(result) == 4

    def test_alphanumeric_only(self):
        tags = ["self-hosting", "machine learning", "c++"]
        result = DevToCrossPostService._normalize_tags(tags)
        # "c" is rejected (single char after stripping ++)
        assert result == ["selfhosting", "machinelearning"]

    def test_single_char_tags_rejected(self):
        tags = ["a", "b", "ai", "ml"]
        result = DevToCrossPostService._normalize_tags(tags)
        assert result == ["ai", "ml"]

    def test_duplicates_removed(self):
        tags = ["AI", "ai", "Ai"]
        result = DevToCrossPostService._normalize_tags(tags)
        assert result == ["ai"]

    def test_empty_tags_skipped(self):
        tags = ["", "  ", "valid"]
        result = DevToCrossPostService._normalize_tags(tags)
        assert result == ["valid"]

    def test_empty_list(self):
        assert DevToCrossPostService._normalize_tags([]) == []


# ---------------------------------------------------------------------------
# cross_post — API key missing
# ---------------------------------------------------------------------------


class TestCrossPostNoApiKey:
    """Test graceful skip when devto_api_key is not configured."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key_in_settings(self):
        pool = make_mock_pool(api_key_row=None)
        svc = DevToCrossPostService(pool)
        result = await svc.cross_post(
            title="Test",
            content_markdown="Content",
            canonical_url="https://www.gladlabs.io/posts/test",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_api_key_empty(self):
        pool = make_mock_pool(api_key_row={"value": ""})
        svc = DevToCrossPostService(pool)
        result = await svc.cross_post(
            title="Test",
            content_markdown="Content",
            canonical_url="https://www.gladlabs.io/posts/test",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_db_fetch_fails(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=Exception("connection refused"))
        svc = DevToCrossPostService(pool)
        result = await svc.cross_post(
            title="Test",
            content_markdown="Content",
            canonical_url="https://www.gladlabs.io/posts/test",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_api_key_cached_after_first_load(self):
        pool = make_mock_pool(api_key_row=None)
        svc = DevToCrossPostService(pool)
        await svc.cross_post("T", "C", "https://www.gladlabs.io/posts/t")
        await svc.cross_post("T2", "C2", "https://www.gladlabs.io/posts/t2")
        # fetchrow should only be called once (cached)
        assert pool.fetchrow.call_count == 1


# ---------------------------------------------------------------------------
# cross_post — successful API call
# ---------------------------------------------------------------------------


class TestCrossPostSuccess:
    """Test cross_post with a mocked httpx client."""

    @pytest.mark.asyncio
    async def test_successful_cross_post_returns_url(self):
        pool = make_mock_pool(api_key_row={"value": "fake-api-key"})
        svc = DevToCrossPostService(pool)

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "url": "https://dev.to/gladlabs/test-article",
            "id": 12345,
        }

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await svc.cross_post(
                title="Test Article",
                content_markdown="## Hello\n[link](/posts/slug)",
                canonical_url="https://www.gladlabs.io/posts/test-article",
                tags=["ai", "python"],
            )

        assert result == "https://dev.to/gladlabs/test-article"
        # Verify the POST was called with the right structure
        call_kwargs = mock_client_instance.post.call_args
        assert call_kwargs[0][0] == "https://dev.to/api/articles"
        payload = call_kwargs[1]["json"]
        assert payload["article"]["title"] == "Test Article"
        assert payload["article"]["published"] is True  # Auto-publish by default
        assert payload["article"]["canonical_url"] == "https://www.gladlabs.io/posts/test-article"
        assert payload["article"]["tags"] == ["ai", "python"]
        # Verify markdown was cleaned (relative link -> absolute)
        assert SITE_URL in payload["article"]["body_markdown"]
        # Verify API key header
        assert call_kwargs[1]["headers"]["api-key"] == "fake-api-key"

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self):
        pool = make_mock_pool(api_key_row={"value": "fake-api-key"})
        svc = DevToCrossPostService(pool)

        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = "Unprocessable Entity"

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await svc.cross_post(
                title="Test",
                content_markdown="Content",
                canonical_url="https://www.gladlabs.io/posts/test",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        pool = make_mock_pool(api_key_row={"value": "fake-api-key"})
        svc = DevToCrossPostService(pool)

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(
                side_effect=Exception("Connection timeout")
            )
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await svc.cross_post(
                title="Test",
                content_markdown="Content",
                canonical_url="https://www.gladlabs.io/posts/test",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_tags_normalized_in_payload(self):
        pool = make_mock_pool(api_key_row={"value": "key"})
        svc = DevToCrossPostService(pool)

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"url": "https://dev.to/x", "id": 1}

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            await svc.cross_post(
                title="T",
                content_markdown="C",
                canonical_url="https://www.gladlabs.io/posts/t",
                tags=["Machine Learning", "Self-Hosting", "AI", "Python", "Extra"],
            )

        payload = mock_client_instance.post.call_args[1]["json"]
        tags = payload["article"]["tags"]
        assert len(tags) <= 4
        assert all(t == t.lower() for t in tags)
        assert all(t.isalnum() for t in tags)
