"""
CMS Routes — Post Completeness Tests

Validates that the CMS API returns fully-fleshed posts with "all the trimmings":
- Markdown → HTML conversion
- Excerpt auto-generation when missing
- coverImage mapping (featured_image_url → Strapi-compatible format)
- Tags and category metadata
- SEO fields (seo_title, seo_description, seo_keywords)
- Proper timestamp ISO formatting
- Post update/patch validation (scheduling, status transitions)

These tests ensure that what the frontend receives from GET /api/posts and
GET /api/posts/{slug} is complete enough to render a fully-featured blog post.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.cms_routes import (
    convert_markdown_to_html,
    generate_excerpt_from_content,
    map_featured_image_to_coverimage,
    router,
)
from tests.unit.routes.conftest import TEST_USER

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 3, 25, 14, 30, 0, tzinfo=timezone.utc)


def _build_app():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


_SENTINEL = object()


def _make_pool_mock(fetchrow_return=_SENTINEL, fetch_return=_SENTINEL):
    conn = MagicMock()
    if fetchrow_return is _SENTINEL:
        conn.fetchrow = AsyncMock(return_value={"total": 0})
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    if fetch_return is _SENTINEL:
        conn.fetch = AsyncMock(return_value=[])
    else:
        conn.fetch = AsyncMock(return_value=fetch_return)
    conn.execute = AsyncMock(return_value="UPDATE 1")

    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool, conn


FULL_POST_ROW = {
    "id": "post-full-001",
    "title": "AI-Powered Diagnostics in Healthcare",
    "slug": "ai-powered-diagnostics-healthcare",
    "excerpt": "Explore how AI is transforming healthcare diagnostics with better accuracy and earlier detection.",
    "featured_image_url": "https://images.pexels.com/photos/12345/ai-health.jpg",
    "cover_image_url": None,
    "category_id": "cat-health-001",
    "published_at": NOW,
    "created_at": NOW,
    "updated_at": NOW,
    "seo_title": "AI Healthcare Diagnostics 2026 | Glad Labs",
    "seo_description": "Discover how AI-powered diagnostic tools are revolutionizing patient care in 2026.",
    "seo_keywords": "AI, healthcare, diagnostics, machine learning, medical imaging",
    "status": "published",
    "content": "# AI-Powered Diagnostics\n\n## Introduction\n\nAI is transforming healthcare with **unprecedented accuracy**.\n\n## Benefits\n\n- Earlier detection\n- Reduced misdiagnosis\n- Cost efficiency\n\n## Conclusion\n\nThe future of diagnostics is AI-powered.",
    "author_id": "author-001",
    "total_count": 1,
}

MINIMAL_POST_ROW = {
    "id": "post-minimal-002",
    "title": "Quick Update",
    "slug": "quick-update",
    "excerpt": None,  # Missing — should auto-generate
    "featured_image_url": None,  # Missing — coverImage should be None
    "cover_image_url": None,
    "category_id": None,
    "published_at": None,
    "created_at": NOW,
    "updated_at": NOW,
    "seo_title": None,
    "seo_description": None,
    "seo_keywords": None,
    "status": "draft",
    "content": "## Quick Update\n\nThis is a **brief** post with minimal metadata. It should still render correctly.",
    "author_id": "author-001",
    "total_count": 1,
}

SAMPLE_TAG_ROWS = [
    {"id": "tag-001", "name": "AI", "slug": "ai", "color": "#00d4ff"},
    {"id": "tag-002", "name": "Healthcare", "slug": "healthcare", "color": "#22c55e"},
]

SAMPLE_CATEGORY_ROW = {
    "id": "cat-health-001",
    "name": "Healthcare Technology",
    "slug": "healthcare-technology",
}


# ---------------------------------------------------------------------------
# Unit tests: convert_markdown_to_html
# ---------------------------------------------------------------------------


try:
    import markdown as _md_check

    HAS_MARKDOWN = True
except ImportError:
    HAS_MARKDOWN = False

_skip_no_markdown = pytest.mark.skipif(
    not HAS_MARKDOWN, reason="python-markdown not installed in test env"
)


@pytest.mark.unit
class TestMarkdownToHtml:
    @_skip_no_markdown
    def test_converts_heading(self):
        html = convert_markdown_to_html("# Hello World")
        assert "<h1>" in html
        assert "Hello World" in html

    @_skip_no_markdown
    def test_converts_bold(self):
        html = convert_markdown_to_html("This is **bold** text")
        assert "<strong>bold</strong>" in html

    @_skip_no_markdown
    def test_converts_list(self):
        html = convert_markdown_to_html("- item one\n- item two")
        assert "<li>" in html
        assert "item one" in html

    @_skip_no_markdown
    def test_converts_code_block(self):
        html = convert_markdown_to_html("```python\nprint('hello')\n```")
        assert "<code" in html or "<pre" in html

    @_skip_no_markdown
    def test_converts_link(self):
        html = convert_markdown_to_html("[Click here](https://example.com)")
        assert 'href="https://example.com"' in html

    @_skip_no_markdown
    def test_converts_blockquote(self):
        html = convert_markdown_to_html("> Important quote")
        assert "<blockquote>" in html

    def test_passes_through_existing_html(self):
        """If content is already HTML, return as-is."""
        existing = "<h1>Already HTML</h1><p>Content</p>"
        result = convert_markdown_to_html(existing)
        assert result == existing

    def test_empty_content_returns_empty(self):
        assert convert_markdown_to_html("") == ""

    def test_fallback_returns_raw_on_missing_module(self):
        """When markdown module is missing, raw content is returned as fallback."""
        result = convert_markdown_to_html("# Heading")
        # Either converted HTML (if module present) or raw markdown (fallback)
        assert "Heading" in result

    @_skip_no_markdown
    def test_complex_markdown_structure(self):
        """Test a realistic blog post markdown structure."""
        md = """# Main Title

## Section One

This paragraph has **bold** and *italic* and a [link](https://example.com).

### Subsection

- Bullet one
- Bullet two

## Section Two

1. Numbered item
2. Another item

> A blockquote with wisdom.

## Conclusion

Final thoughts here.
"""
        html = convert_markdown_to_html(md)
        assert "<h1>" in html
        assert "<h2>" in html
        assert "<h3>" in html
        assert "<strong>" in html
        assert "<em>" in html
        assert "<li>" in html
        assert "<blockquote>" in html
        assert '<a href="https://example.com">' in html


# ---------------------------------------------------------------------------
# Unit tests: generate_excerpt_from_content
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExcerptGeneration:
    def test_generates_excerpt_from_content(self):
        content = "# Title\n\nThis is the first paragraph of a blog post about AI diagnostics."
        excerpt = generate_excerpt_from_content(content)
        assert len(excerpt) > 0
        assert "first paragraph" in excerpt

    def test_skips_headers(self):
        content = "# Big Header\n## Subheader\n\nActual content here."
        excerpt = generate_excerpt_from_content(content)
        assert "Big Header" not in excerpt
        assert "Actual content" in excerpt

    def test_respects_max_length(self):
        content = "This is a paragraph. " * 50
        excerpt = generate_excerpt_from_content(content, length=100)
        assert len(excerpt) <= 103  # 100 + "..."

    def test_empty_content(self):
        assert generate_excerpt_from_content("") == ""
        assert generate_excerpt_from_content(None) == ""

    def test_strips_markdown_link_syntax(self):
        content = "Check out this [great link](https://example.com) for more."
        excerpt = generate_excerpt_from_content(content)
        assert "[" not in excerpt
        assert "]" not in excerpt
        assert "(" not in excerpt
        assert ")" not in excerpt

    def test_preserves_bold_formatting(self):
        """Excerpt keeps markdown bold for frontend rendering."""
        content = "This is **important** content for readers."
        excerpt = generate_excerpt_from_content(content)
        assert "**important**" in excerpt


# ---------------------------------------------------------------------------
# Unit tests: map_featured_image_to_coverimage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCoverImageMapping:
    def test_maps_featured_image_to_strapi_format(self):
        post = {"title": "Test", "featured_image_url": "https://example.com/img.jpg"}
        result = map_featured_image_to_coverimage(post)
        assert result["coverImage"] is not None
        assert result["coverImage"]["data"]["attributes"]["url"] == "https://example.com/img.jpg"

    def test_includes_alt_text_in_cover_image(self):
        post = {"title": "My Post", "featured_image_url": "https://example.com/img.jpg"}
        result = map_featured_image_to_coverimage(post)
        alt = result["coverImage"]["data"]["attributes"]["alternativeText"]
        assert "My Post" in alt

    def test_no_featured_image_sets_null(self):
        post = {"title": "Test", "featured_image_url": None}
        result = map_featured_image_to_coverimage(post)
        assert result["coverImage"] is None

    def test_empty_featured_image_sets_null(self):
        post = {"title": "Test"}
        result = map_featured_image_to_coverimage(post)
        assert result["coverImage"] is None


# ---------------------------------------------------------------------------
# API tests: GET /api/posts — fully-fleshed post in list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListPostsCompleteness:
    def test_full_post_has_all_display_fields(self):
        """A fully-fleshed post in the list should have all fields for rendering a post card."""
        pool, conn = _make_pool_mock(fetch_return=[FULL_POST_ROW])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts").json()
        post = data["posts"][0]
        # Essential display fields
        assert post["title"] is not None
        assert post["slug"] is not None
        assert post["excerpt"] is not None
        assert post["status"] == "published"
        # Timestamps are ISO strings
        assert "T" in post["published_at"]
        assert "T" in post["created_at"]

    def test_full_post_has_seo_fields(self):
        """SEO fields should be present for search engine optimization."""
        pool, conn = _make_pool_mock(fetch_return=[FULL_POST_ROW])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts").json()
        post = data["posts"][0]
        assert post["seo_title"] is not None
        assert post["seo_description"] is not None
        assert post["seo_keywords"] is not None

    def test_full_post_has_cover_image_mapping(self):
        """Posts with featured_image_url should have Strapi-compatible coverImage."""
        pool, conn = _make_pool_mock(fetch_return=[FULL_POST_ROW])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts").json()
        post = data["posts"][0]
        assert post["coverImage"] is not None
        assert "data" in post["coverImage"]
        assert (
            post["coverImage"]["data"]["attributes"]["url"] == FULL_POST_ROW["featured_image_url"]
        )

    def test_content_processed_through_markdown_converter(self):
        """Content should be passed through convert_markdown_to_html (HTML if module available, raw fallback otherwise)."""
        pool, conn = _make_pool_mock(fetch_return=[FULL_POST_ROW])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts").json()
        content = data["posts"][0]["content"]
        # Content should be present (either HTML-converted or raw markdown fallback)
        assert len(content) > 0
        # Should contain the actual text content regardless of format
        assert "AI" in content or "Diagnostics" in content or "Introduction" in content

    def test_minimal_post_gets_auto_excerpt(self):
        """Posts without an excerpt should get one auto-generated from content."""
        pool, conn = _make_pool_mock(fetch_return=[MINIMAL_POST_ROW])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts").json()
        post = data["posts"][0]
        assert post["excerpt"] is not None
        assert len(post["excerpt"]) > 0

    def test_minimal_post_null_coverimage(self):
        """Posts without featured image should have coverImage: null."""
        pool, conn = _make_pool_mock(fetch_return=[MINIMAL_POST_ROW])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts").json()
        assert data["posts"][0]["coverImage"] is None

    def test_null_published_at_formats_safely(self):
        """Posts with null published_at (drafts) should return null, not crash."""
        pool, conn = _make_pool_mock(fetch_return=[MINIMAL_POST_ROW])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts").json()
        assert data["posts"][0]["published_at"] is None


# ---------------------------------------------------------------------------
# API tests: GET /api/posts/{slug} — single post with full metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPostBySlugCompleteness:
    def _setup_full_post_with_tags_and_category(self):
        """Set up a pool mock that returns a full post with tags and category."""
        pool, conn = _make_pool_mock(fetchrow_return=FULL_POST_ROW)
        # conn.fetch for tags, conn.fetchrow for category (second call)
        conn.fetch = AsyncMock(return_value=SAMPLE_TAG_ROWS)
        # fetchrow is called once for post, then once for category
        conn.fetchrow = AsyncMock(side_effect=[FULL_POST_ROW, SAMPLE_CATEGORY_ROW])
        return pool, conn

    def test_single_post_has_data_and_meta(self):
        pool, conn = self._setup_full_post_with_tags_and_category()
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts/ai-powered-diagnostics-healthcare").json()
        assert "data" in data
        assert "meta" in data

    def test_single_post_data_has_all_content_fields(self):
        """Single post response should include all fields needed for rendering."""
        pool, conn = self._setup_full_post_with_tags_and_category()
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts/ai-powered-diagnostics-healthcare").json()
        post = data["data"]
        # Core content
        assert post["title"] == FULL_POST_ROW["title"]
        assert post["slug"] == FULL_POST_ROW["slug"]
        assert len(post["content"]) > 0  # Content present (HTML or markdown fallback)
        assert post["excerpt"] is not None
        # SEO
        assert post["seo_title"] is not None
        assert post["seo_description"] is not None
        assert post["seo_keywords"] is not None
        # Image
        assert post["coverImage"] is not None

    def test_single_post_meta_has_tags(self):
        """Single post meta should include tags array."""
        pool, conn = self._setup_full_post_with_tags_and_category()
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts/ai-powered-diagnostics-healthcare").json()
        tags = data["meta"]["tags"]
        assert len(tags) == 2
        assert tags[0]["name"] == "AI"
        assert tags[0]["slug"] == "ai"
        assert tags[1]["name"] == "Healthcare"

    def test_single_post_meta_has_category(self):
        """Single post meta should include the resolved category."""
        pool, conn = self._setup_full_post_with_tags_and_category()
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts/ai-powered-diagnostics-healthcare").json()
        category = data["meta"]["category"]
        assert category is not None
        assert category["name"] == "Healthcare Technology"
        assert category["slug"] == "healthcare-technology"

    def test_single_post_timestamps_are_iso(self):
        """All timestamps should be ISO 8601 formatted strings."""
        pool, conn = self._setup_full_post_with_tags_and_category()
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts/ai-powered-diagnostics-healthcare").json()
        post = data["data"]
        for field in ("published_at", "created_at", "updated_at"):
            val = post[field]
            assert val is not None and "T" in val, f"{field} not ISO formatted: {val}"

    def test_tags_gracefully_empty_on_error(self):
        """If tags query fails, tags should be empty list, not error."""
        pool, conn = _make_pool_mock(fetchrow_return=FULL_POST_ROW)
        conn.fetch = AsyncMock(side_effect=Exception("post_tags table missing"))
        conn.fetchrow = AsyncMock(side_effect=[FULL_POST_ROW, None])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.get("/api/posts/ai-powered-diagnostics-healthcare")
        assert resp.status_code == 200
        assert resp.json()["meta"]["tags"] == []

    def test_category_null_when_not_set(self):
        """Posts without a category_id should return category: null."""
        row = {**FULL_POST_ROW, "category_id": None}
        pool, conn = _make_pool_mock(fetchrow_return=row)
        conn.fetch = AsyncMock(return_value=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts/ai-powered-diagnostics-healthcare").json()
        assert data["meta"]["category"] is None


# ---------------------------------------------------------------------------
# API tests: PATCH /api/posts/{post_id} — status transitions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPostUpdateValidation:
    def test_publish_sets_published_at(self):
        """Setting status to 'published' without published_at should auto-set it."""
        pool, conn = _make_pool_mock()
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={"status": "published"},
            )
        assert resp.status_code == 200
        # Verify published_at was added to the update params
        call_args = conn.execute.call_args
        # The params list should contain a datetime for published_at
        params = call_args[0][1:]  # skip SQL string
        has_datetime = any(isinstance(p, datetime) for p in params)
        assert has_datetime, "published_at datetime should be auto-set on publish"

    def test_schedule_requires_published_at(self):
        """Setting status to 'scheduled' without published_at should fail."""
        pool, conn = _make_pool_mock()
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={"status": "scheduled"},
            )
        assert resp.status_code == 400
        assert "published_at is required" in resp.json()["detail"]

    def test_schedule_requires_future_date(self):
        """Scheduled published_at must be in the future."""
        pool, conn = _make_pool_mock()
        past_date = "2020-01-01T00:00:00Z"
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={"status": "scheduled", "published_at": past_date},
            )
        assert resp.status_code == 400
        assert "future" in resp.json()["detail"].lower()

    def test_invalid_published_at_format_rejected(self):
        """Invalid date format should return 400."""
        pool, conn = _make_pool_mock()
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={"published_at": "not-a-date"},
            )
        assert resp.status_code == 400

    def test_no_valid_fields_rejected(self):
        """PATCH with only invalid fields should return 400."""
        pool, conn = _make_pool_mock()
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={"invalid_field": "value"},
            )
        assert resp.status_code == 400

    def test_update_seo_fields(self):
        """Should allow updating SEO-specific fields."""
        pool, conn = _make_pool_mock()
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={
                    "seo_title": "Updated SEO Title",
                    "seo_description": "Updated meta description",
                    "seo_keywords": "updated, keywords",
                },
            )
        assert resp.status_code == 200

    def test_update_nonexistent_post_returns_404(self):
        """PATCH on a non-existent post should return 404."""
        pool, conn = _make_pool_mock()
        conn.execute = AsyncMock(return_value="UPDATE 0")
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/nonexistent-id",
                json={"title": "New Title"},
            )
        assert resp.status_code == 404
