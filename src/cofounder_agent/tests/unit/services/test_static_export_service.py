"""
Unit tests for static_export_service — push-only headless CMS export layer.

Tests JSON generation, data shaping, and feed building.
No R2/S3 uploads — those are mocked.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.static_export_service import (
    _build_json_feed,
    _build_sitemap,
    _post_full,
    _post_summary,
    _to_json,
)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 8, 12, 0, 0, tzinfo=timezone.utc)

_SAMPLE_POST = {
    "id": "abc-123",
    "title": "Test Post Title",
    "slug": "test-post-title-abc123",
    "content": "<p>Hello world</p>",
    "excerpt": "A test post excerpt",
    "featured_image_url": "https://example.com/img.png",
    "cover_image_url": None,
    "author_id": "author-1",
    "category_id": "cat-1",
    "status": "published",
    "seo_title": "Test SEO Title",
    "seo_description": "Test SEO desc",
    "seo_keywords": "test, post",
    "published_at": _NOW,
    "created_at": _NOW,
    "updated_at": _NOW,
}

_SAMPLE_CATEGORIES = [
    {"id": "cat-1", "name": "Technology", "slug": "technology", "description": "Tech articles"},
    {"id": "cat-2", "name": "Business", "slug": "business", "description": None},
]


# ---------------------------------------------------------------------------
# _post_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPostSummary:
    def test_excludes_content(self):
        result = _post_summary(_SAMPLE_POST)
        assert "content" not in result

    def test_includes_metadata(self):
        result = _post_summary(_SAMPLE_POST)
        assert result["id"] == "abc-123"
        assert result["title"] == "Test Post Title"
        assert result["slug"] == "test-post-title-abc123"
        assert result["excerpt"] == "A test post excerpt"

    def test_published_at_is_iso_string(self):
        result = _post_summary(_SAMPLE_POST)
        assert result["published_at"] == "2026-04-08T12:00:00+00:00"

    def test_featured_image_falls_back_to_cover(self):
        post = {**_SAMPLE_POST, "featured_image_url": None, "cover_image_url": "https://cover.png"}
        result = _post_summary(post)
        assert result["featured_image_url"] == "https://cover.png"

    def test_handles_none_dates(self):
        post = {**_SAMPLE_POST, "published_at": None, "created_at": None, "updated_at": None}
        result = _post_summary(post)
        assert result["published_at"] is None
        assert result["created_at"] is None

    def test_stringifies_uuids(self):
        result = _post_summary(_SAMPLE_POST)
        assert isinstance(result["id"], str)
        assert isinstance(result["author_id"], str)
        assert isinstance(result["category_id"], str)


# ---------------------------------------------------------------------------
# _post_full
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPostFull:
    def test_includes_content(self):
        result = _post_full(_SAMPLE_POST)
        assert result["content"] == "<p>Hello world</p>"

    def test_includes_all_summary_fields(self):
        result = _post_full(_SAMPLE_POST)
        assert result["title"] == "Test Post Title"
        assert result["slug"] == "test-post-title-abc123"
        assert result["seo_description"] == "Test SEO desc"


# ---------------------------------------------------------------------------
# _build_json_feed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildJsonFeed:
    def test_feed_version(self):
        feed = _build_json_feed([_SAMPLE_POST], "https://example.com", "Test Site")
        assert feed["version"] == "https://jsonfeed.org/version/1.1"

    def test_feed_metadata(self):
        feed = _build_json_feed([_SAMPLE_POST], "https://example.com", "Test Site")
        assert feed["title"] == "Test Site"
        assert feed["home_page_url"] == "https://example.com"

    def test_feed_items(self):
        feed = _build_json_feed([_SAMPLE_POST], "https://example.com", "Test Site")
        assert len(feed["items"]) == 1
        item = feed["items"][0]
        assert item["title"] == "Test Post Title"
        assert item["url"] == "https://example.com/posts/test-post-title-abc123"

    def test_feed_item_has_image(self):
        feed = _build_json_feed([_SAMPLE_POST], "https://example.com", "Test Site")
        assert feed["items"][0]["image"] == "https://example.com/img.png"

    def test_feed_limits_to_50(self):
        posts = [_SAMPLE_POST.copy() for _ in range(100)]
        feed = _build_json_feed(posts, "https://example.com", "Test Site")
        assert len(feed["items"]) == 50

    def test_empty_posts(self):
        feed = _build_json_feed([], "https://example.com", "Test Site")
        assert feed["items"] == []


# ---------------------------------------------------------------------------
# _build_sitemap
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSitemap:
    def test_includes_static_pages(self):
        sitemap = _build_sitemap([], [], "https://example.com")
        urls = [u["url"] for u in sitemap["urls"]]
        assert "https://example.com" in urls
        assert "https://example.com/posts" in urls
        assert "https://example.com/archive" in urls

    def test_includes_posts(self):
        sitemap = _build_sitemap([_SAMPLE_POST], [], "https://example.com")
        urls = [u["url"] for u in sitemap["urls"]]
        assert "https://example.com/posts/test-post-title-abc123" in urls

    def test_includes_categories(self):
        sitemap = _build_sitemap([], _SAMPLE_CATEGORIES, "https://example.com")
        urls = [u["url"] for u in sitemap["urls"]]
        assert "https://example.com/category/technology" in urls
        assert "https://example.com/category/business" in urls

    def test_total_count(self):
        sitemap = _build_sitemap([_SAMPLE_POST], _SAMPLE_CATEGORIES, "https://example.com")
        # 3 static + 2 categories + 1 post = 6
        assert sitemap["total"] == 6


# ---------------------------------------------------------------------------
# _to_json
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToJson:
    def test_serializes_datetime(self):
        data = {"ts": _NOW}
        result = _to_json(data)
        assert "2026-04-08T12:00:00" in result

    def test_handles_nested_structures(self):
        data = {"posts": [{"title": "Hello", "published_at": _NOW}]}
        result = _to_json(data)
        assert '"Hello"' in result

    def test_handles_unicode(self):
        data = {"title": "Café & résumé"}
        result = _to_json(data)
        assert "Café" in result


# ---------------------------------------------------------------------------
# export_post (integration — mocks R2 upload)
# ---------------------------------------------------------------------------


class _MockAcquireCtx:
    """Simulates asyncpg pool.acquire() which returns an async context manager."""
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


def _make_mock_pool(fetchrow_result=None, fetch_result=None):
    """Create a mock asyncpg pool with proper async context manager."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = fetchrow_result
    if fetch_result is not None:
        mock_conn.fetch.return_value = fetch_result
    else:
        mock_conn.fetch.return_value = []

    from unittest.mock import MagicMock
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = _MockAcquireCtx(mock_conn)
    return mock_pool


@pytest.mark.unit
class TestExportPost:
    @pytest.mark.asyncio
    async def test_export_post_calls_upload(self):
        mock_pool = _make_mock_pool(
            fetchrow_result=_SAMPLE_POST,
            fetch_result=[_SAMPLE_POST],
        )

        with patch("services.static_export_service._upload_json", new_callable=AsyncMock) as mock_upload:
            mock_upload.return_value = "https://r2.dev/static/test.json"

            from services.static_export_service import export_post
            result = await export_post(mock_pool, "test-post-title-abc123")

            assert result is True
            # Should upload: individual post, index, feed, sitemap, manifest = 5 calls
            assert mock_upload.call_count == 5

    @pytest.mark.asyncio
    async def test_export_post_returns_false_on_missing_post(self):
        mock_pool = _make_mock_pool(fetchrow_result=None)

        with patch("services.static_export_service._upload_json", new_callable=AsyncMock):
            from services.static_export_service import export_post
            result = await export_post(mock_pool, "nonexistent-slug")

            assert result is False
