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
    _json_serial,
    _markdown_to_html,
    _post_full,
    _post_summary,
    _to_json,
    export_full_rebuild,
    export_post,
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


# ---------------------------------------------------------------------------
# _json_serial — direct unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestJsonSerial:
    def test_datetime_returns_isoformat(self):
        result = _json_serial(_NOW)
        assert result == _NOW.isoformat()

    def test_pydantic_like_model_dump(self):
        class FakeModel:
            def model_dump(self):
                return {"a": 1, "b": "two"}
        result = _json_serial(FakeModel())
        assert result == {"a": 1, "b": "two"}

    def test_unserializable_raises_typeerror(self):
        class NotSerializable:
            pass
        with pytest.raises(TypeError, match="not serializable"):
            _json_serial(NotSerializable())


# ---------------------------------------------------------------------------
# _markdown_to_html
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMarkdownToHtml:
    def test_empty_string_returns_empty(self):
        assert _markdown_to_html("") == ""

    def test_already_html_passes_through_unchanged(self):
        html = "<p>Already HTML</p>"
        assert _markdown_to_html(html) == html

    def test_html_with_entities_passes_through(self):
        html = "<div>Tom &amp; Jerry</div>"
        assert _markdown_to_html(html) == html

    def test_markdown_h1_converts_to_html(self):
        result = _markdown_to_html("# Hello")
        assert "<h1>" in result
        assert "Hello" in result

    def test_markdown_paragraph_converts_to_html(self):
        result = _markdown_to_html("This is body text.")
        assert "<p>" in result

    def test_markdown_with_image_attachment_marker_falls_through(self):
        # Starts with "<![" — the early-return guard does NOT trigger,
        # so it goes through the markdown converter.
        result = _markdown_to_html("<![image]> some text")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Additional _post_summary edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPostSummaryEdgeCases:
    def test_default_status_when_missing(self):
        post = {**_SAMPLE_POST}
        del post["status"]
        result = _post_summary(post)
        assert result["status"] == "published"

    def test_null_author_id_serializes_to_none(self):
        post = {**_SAMPLE_POST, "author_id": None}
        result = _post_summary(post)
        assert result["author_id"] is None

    def test_null_category_id_serializes_to_none(self):
        post = {**_SAMPLE_POST, "category_id": None}
        result = _post_summary(post)
        assert result["category_id"] is None


# ---------------------------------------------------------------------------
# Additional _build_json_feed edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildJsonFeedEdgeCases:
    def test_falls_back_to_seo_description_when_no_excerpt(self):
        post = {**_SAMPLE_POST, "excerpt": None, "seo_description": "fallback"}
        feed = _build_json_feed([post], "https://example.com", "Site")
        assert feed["items"][0]["summary"] == "fallback"

    def test_summary_empty_string_when_no_excerpt_or_seo(self):
        post = {**_SAMPLE_POST, "excerpt": None, "seo_description": None}
        feed = _build_json_feed([post], "https://example.com", "Site")
        assert feed["items"][0]["summary"] == ""

    def test_content_html_when_content_present(self):
        post = {**_SAMPLE_POST, "content": "# Heading"}
        feed = _build_json_feed([post], "https://example.com", "Site")
        assert "<h1>" in feed["items"][0]["content_html"]


# ---------------------------------------------------------------------------
# Additional _build_sitemap edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSitemapEdgeCases:
    def test_post_with_null_updated_at_emits_null_lastmod(self):
        post = {**_SAMPLE_POST, "updated_at": None}
        sitemap = _build_sitemap([post], [], "https://example.com")
        post_entry = next(u for u in sitemap["urls"] if "/posts/test-post-title-abc123" in u["url"])
        assert post_entry["lastmod"] is None


# ---------------------------------------------------------------------------
# export_post — upload failure + db exception paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExportPostFailurePaths:
    @pytest.mark.asyncio
    async def test_upload_failure_returns_false(self):
        mock_pool = _make_mock_pool(
            fetchrow_result=_SAMPLE_POST,
            fetch_result=[_SAMPLE_POST],
        )

        async def fake_upload(*args, **kwargs):
            return None  # signal upload failure

        with patch("services.static_export_service._upload_json", side_effect=fake_upload):
            result = await export_post(mock_pool, "test-post-title-abc123")
        assert result is False

    @pytest.mark.asyncio
    async def test_db_exception_returns_false(self):
        from unittest.mock import MagicMock
        broken_pool = MagicMock()

        def _explode():
            raise RuntimeError("db down")

        broken_pool.acquire = _explode

        result = await export_post(broken_pool, "any-slug")
        assert result is False


# ---------------------------------------------------------------------------
# export_full_rebuild
# ---------------------------------------------------------------------------


def _make_full_rebuild_pool(posts, categories, authors):
    """Pool that returns posts/categories/authors based on the SQL keyword."""
    from unittest.mock import MagicMock

    def _acquire():
        conn = AsyncMock()

        async def fetch(query, *args):
            q = " ".join(query.split())
            if "FROM categories" in q:
                return categories
            if "FROM authors" in q:
                return authors
            return posts

        conn.fetch = fetch
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    pool = MagicMock()
    pool.acquire = _acquire
    return pool


@pytest.mark.unit
class TestExportFullRebuild:
    @pytest.mark.asyncio
    async def test_happy_path_returns_full_summary(self):
        posts = [
            {**_SAMPLE_POST, "slug": f"post-{i}"} for i in range(3)
        ]
        cats = [{"id": "c1", "name": "AI", "slug": "ai", "description": None}]
        authors = [{"id": "a1", "name": "Matt"}]
        pool = _make_full_rebuild_pool(posts, cats, authors)

        with patch("services.static_export_service._upload_json", new_callable=AsyncMock) as mock_upload, \
             patch("services.static_export_service.site_config") as mock_sc:
            mock_upload.return_value = "https://r2/x"
            mock_sc.get.side_effect = lambda k, d=None: None
            mock_sc.require.side_effect = lambda k: {
                "site_url": "https://example.com",
                "site_name": "Test Site",
            }[k]
            result = await export_full_rebuild(pool)

        assert result["success"] is True
        assert result["posts_exported"] == 3
        assert result["categories_exported"] == 1
        assert result["authors_exported"] == 1
        # total_files = len(posts) + 5 = 8
        assert result["total_files"] == 8
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_partial_upload_failure_records_errors(self):
        posts = [
            {**_SAMPLE_POST, "slug": "ok"},
            {**_SAMPLE_POST, "slug": "fail"},
        ]
        pool = _make_full_rebuild_pool(posts, [], [])

        async def fake_upload(key, data, content_type="application/json"):
            if "fail" in key:
                return None
            return f"https://r2/{key}"

        with patch("services.static_export_service._upload_json", side_effect=fake_upload), \
             patch("services.static_export_service.site_config") as mock_sc:
            mock_sc.get.side_effect = lambda k, d=None: None
            mock_sc.require.side_effect = lambda k: {
                "site_url": "https://example.com",
                "site_name": "Test Site",
            }[k]
            result = await export_full_rebuild(pool)

        assert result["success"] is False
        assert any("fail" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_db_exception_returns_error_dict(self):
        from unittest.mock import MagicMock
        broken_pool = MagicMock()

        def _explode():
            raise RuntimeError("db down")

        broken_pool.acquire = _explode

        with patch("services.static_export_service.site_config") as mock_sc:
            mock_sc.get.side_effect = lambda k, d=None: None
            mock_sc.require.side_effect = lambda k: {
                "site_url": "https://example.com",
                "site_name": "Test Site",
            }[k]
            result = await export_full_rebuild(broken_pool)

        assert result["success"] is False
        assert "error" in result
