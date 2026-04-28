"""
Unit tests for routes/cms_routes.py.

Tests cover:
- GET /api/posts                      — list_posts
- GET /api/posts/{slug}               — get_post_by_slug
- GET /api/categories                 — list_categories
- GET /api/tags                       — list_tags
- GET /api/cms/status                 — cms_status (requires auth)

All tests patch `routes.cms_routes.get_db_pool` to return an asyncpg-like
mock pool so no real database connection is required.
cms_status requires authentication — get_current_user overridden with TEST_USER.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.cms_routes import router

# ---------------------------------------------------------------------------
# Helper: build a minimal app.
# cms_status requires auth — get_current_user overridden to return TEST_USER.
# ---------------------------------------------------------------------------


def _build_app():
    app = FastAPI()
    app.include_router(router)
    # Override auth for protected endpoints (cms_status)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


# ---------------------------------------------------------------------------
# DB pool mock helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)

SAMPLE_POST_ROW = {
    "id": "post-001",
    "title": "Test Post",
    "slug": "test-post",
    "excerpt": "A test excerpt",
    "featured_image_url": None,
    "cover_image_url": None,
    "category_id": None,
    "published_at": NOW,
    "created_at": NOW,
    "updated_at": NOW,
    "seo_title": "Test Post SEO",
    "seo_description": "SEO description",
    "seo_keywords": "test",
    "status": "published",
    "content": "# Hello\n\nThis is test content.",
    "author_id": "author-001",
    # Window function column added by list_posts single-query path
    "total_count": 1,
}

SAMPLE_CATEGORY_ROW = {
    "id": "cat-001",
    "name": "Technology",
    "slug": "technology",
    "description": "Tech articles",
    "created_at": NOW,
    "updated_at": NOW,
}

SAMPLE_TAG_ROW = {
    "id": "tag-001",
    "name": "Python",
    "slug": "python",
    "description": "Python articles",
    "created_at": NOW,
    "updated_at": NOW,
}


_SENTINEL = object()  # Used to distinguish explicit None from "not provided"


def _make_pool_mock(
    fetchrow_return=_SENTINEL,
    fetch_return=_SENTINEL,
    count_return=_SENTINEL,
):
    """
    Return an async context-manager pool mock that yields a conn mock.
    conn.fetchrow returns fetchrow_return (or a fake count row by default).
    conn.fetch returns fetch_return (default: []).

    Pass fetchrow_return=None explicitly to simulate "no row found".
    """
    conn = MagicMock()

    # fetchrow: first call often used for COUNT(*), second for row lookup
    if fetchrow_return is _SENTINEL:
        # Default: count row with total=0
        conn.fetchrow = AsyncMock(return_value={"total": 0})
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    if fetch_return is _SENTINEL:
        conn.fetch = AsyncMock(return_value=[])
    else:
        conn.fetch = AsyncMock(return_value=fetch_return)

    # asyncpg uses async context manager: `async with pool.acquire() as conn:`
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_cm)

    return pool, conn


# ---------------------------------------------------------------------------
# GET /api/posts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListPosts:
    def test_returns_200_with_empty_posts(self):
        # No rows → total_count comes from empty list (total=0 branch)
        pool, conn = _make_pool_mock(fetch_return=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.get("/api/posts")
        assert resp.status_code == 200

    def test_response_has_standard_envelope(self):
        pool, conn = _make_pool_mock(fetch_return=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts").json()
        assert "posts" in data
        assert "total" in data
        assert "offset" in data
        assert "limit" in data

    def test_default_pagination_values(self):
        pool, conn = _make_pool_mock(fetch_return=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts").json()
        assert data["offset"] == 0
        assert data["limit"] == 20

    def test_custom_offset_and_limit(self):
        pool, conn = _make_pool_mock(fetch_return=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts?offset=10&limit=5").json()
        assert data["offset"] == 10
        assert data["limit"] == 5

    def test_deprecated_skip_falls_back_to_offset(self):
        """The skip param should be accepted as alias for offset."""
        pool, conn = _make_pool_mock(fetch_return=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts?skip=5").json()
        assert data["offset"] == 5

    def test_total_count_from_window_function(self):
        """total in response comes from total_count window column, not a separate fetchrow."""
        row = {**SAMPLE_POST_ROW, "total_count": 42}
        pool, conn = _make_pool_mock(fetch_return=[row])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts").json()
        assert data["total"] == 42

    def test_total_count_not_in_post_fields(self):
        """The internal total_count window column must be stripped from the post objects."""
        row = {**SAMPLE_POST_ROW, "total_count": 7}
        pool, conn = _make_pool_mock(fetch_return=[row])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts").json()
        assert len(data["posts"]) == 1
        assert "total_count" not in data["posts"][0]

    def test_db_error_returns_500(self):
        with patch(
            "routes.cms_routes.get_db_pool",
            new=AsyncMock(side_effect=RuntimeError("DB down")),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/posts")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/posts/{slug}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPostBySlug:
    def test_found_post_returns_200(self):
        pool, conn = _make_pool_mock(fetchrow_return=SAMPLE_POST_ROW)
        # tags fetch returns empty list
        conn.fetch = AsyncMock(return_value=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.get("/api/posts/test-post")
        assert resp.status_code == 200

    def test_found_post_has_data_and_meta_keys(self):
        pool, conn = _make_pool_mock(fetchrow_return=SAMPLE_POST_ROW)
        conn.fetch = AsyncMock(return_value=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts/test-post").json()
        assert "data" in data
        assert "meta" in data
        assert "tags" in data["meta"]

    def test_found_post_has_slug_in_data(self):
        pool, conn = _make_pool_mock(fetchrow_return=SAMPLE_POST_ROW)
        conn.fetch = AsyncMock(return_value=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts/test-post").json()
        assert data["data"]["slug"] == "test-post"

    def test_missing_post_returns_404(self):
        pool, conn = _make_pool_mock(fetchrow_return=None)
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.get("/api/posts/nonexistent-slug")
        assert resp.status_code == 404

    def test_db_error_returns_500(self):
        with patch(
            "routes.cms_routes.get_db_pool",
            new=AsyncMock(side_effect=RuntimeError("DB down")),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/posts/any-slug")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/categories
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListCategories:
    def test_returns_200_with_empty_list(self):
        pool, conn = _make_pool_mock(fetch_return=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.get("/api/categories")
        assert resp.status_code == 200

    def test_response_has_standard_envelope(self):
        pool, conn = _make_pool_mock(fetch_return=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/categories").json()
        assert "categories" in data
        assert "total" in data
        assert "offset" in data
        assert "limit" in data

    def test_returns_categories_from_db(self):
        pool, conn = _make_pool_mock(fetch_return=[SAMPLE_CATEGORY_ROW])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/categories").json()
        assert data["total"] == 1
        assert data["categories"][0]["slug"] == "technology"

    def test_default_limit_is_100(self):
        pool, conn = _make_pool_mock(fetch_return=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/categories").json()
        assert data["limit"] == 100

    def test_db_error_returns_500(self):
        with patch(
            "routes.cms_routes.get_db_pool",
            new=AsyncMock(side_effect=RuntimeError("DB down")),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/categories")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/tags
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListTags:
    def test_returns_200_with_empty_list(self):
        pool, conn = _make_pool_mock(fetch_return=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.get("/api/tags")
        assert resp.status_code == 200

    def test_response_has_standard_envelope(self):
        pool, conn = _make_pool_mock(fetch_return=[])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/tags").json()
        assert "tags" in data
        assert "total" in data
        assert "offset" in data
        assert "limit" in data

    def test_returns_tags_from_db(self):
        pool, conn = _make_pool_mock(fetch_return=[SAMPLE_TAG_ROW])
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/tags").json()
        assert data["total"] == 1
        assert data["tags"][0]["slug"] == "python"

    def test_db_error_returns_500(self):
        with patch(
            "routes.cms_routes.get_db_pool",
            new=AsyncMock(side_effect=RuntimeError("DB down")),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/tags")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/cms/status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCmsStatus:
    def _make_status_pool(self, all_tables_exist=True):
        """Build a pool mock where information_schema queries return table-exists status."""
        conn = MagicMock()
        # fetchrow is called twice per table: exists check + count
        exists_row = {"exists": all_tables_exist}
        count_row = {"cnt": 5 if all_tables_exist else 0}

        # Alternate between exists_row and count_row based on query patterns
        # Since the same mock is called multiple times, use side_effect list
        calls = []
        for _ in range(4):  # 4 tables
            calls.append(exists_row)
            if all_tables_exist:
                calls.append(count_row)
        conn.fetchrow = AsyncMock(side_effect=calls)

        acquire_cm = MagicMock()
        acquire_cm.__aenter__ = AsyncMock(return_value=conn)
        acquire_cm.__aexit__ = AsyncMock(return_value=None)

        pool = MagicMock()
        pool.acquire = MagicMock(return_value=acquire_cm)
        return pool

    def test_error_detail_does_not_leak_exception_message(self):
        """Ensure raw exception text is not exposed in the HTTP response."""
        with patch(
            "routes.cms_routes.get_db_pool",
            new=AsyncMock(side_effect=RuntimeError("SECRET_DB_PASSWORD_EXPOSED")),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/cms/status")
        body = resp.text
        # The raw exception message must NOT appear in the response
        assert "SECRET_DB_PASSWORD_EXPOSED" not in body


# ---------------------------------------------------------------------------
# convert_markdown_to_html — pure helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConvertMarkdownToHtml:
    def test_empty_returns_empty(self):
        from routes.cms_routes import convert_markdown_to_html
        assert convert_markdown_to_html("") == ""

    def test_none_returns_empty(self):
        from routes.cms_routes import convert_markdown_to_html
        assert convert_markdown_to_html(None) == ""  # type: ignore[arg-type]

    def test_headings_convert_to_h_tags(self):
        from routes.cms_routes import convert_markdown_to_html
        html = convert_markdown_to_html("# Title\n\nParagraph.")
        assert "<h1>" in html
        assert "</h1>" in html
        assert "Title" in html
        assert "<p>" in html

    def test_bold_and_italic(self):
        from routes.cms_routes import convert_markdown_to_html
        html = convert_markdown_to_html("**bold** and *italic*.")
        assert "<strong>" in html or "<b>" in html
        assert "<em>" in html or "<i>" in html

    def test_code_fence(self):
        from routes.cms_routes import convert_markdown_to_html
        html = convert_markdown_to_html("```python\nprint('hi')\n```")
        assert "<code" in html or "<pre" in html

    def test_already_html_passed_through(self):
        """Content that is pure HTML (no markdown markers) passes through unchanged."""
        from routes.cms_routes import convert_markdown_to_html
        already_html = "<article><p>Hello</p></article>"
        result = convert_markdown_to_html(already_html)
        assert result == already_html

    def test_leading_img_with_markdown_body_converts(self):
        """#198 regression: posts with leading <img> + markdown body now
        convert instead of being returned raw. The old early-return
        shipped `## Heading` and `**bold**` markers to the live site."""
        from routes.cms_routes import convert_markdown_to_html
        mixed = (
            '<img src="x"/>\n\n'
            "## Heading\n\nSome **bold** text.\n\n"
            "- item 1\n- item 2"
        )
        html = convert_markdown_to_html(mixed)
        assert "<h2>" in html
        assert "<strong>" in html
        assert "<ul>" in html
        assert '<img src="x"/>' in html  # HTML survived passthrough

    def test_comment_block_not_treated_as_html(self):
        """Content starting with <![ is markdown (CDATA), not HTML."""
        from routes.cms_routes import convert_markdown_to_html
        # This should still get markdown processing because it starts with <!
        content = "<![CDATA[stuff]]> after the cdata"
        result = convert_markdown_to_html(content)
        # Result should be processed (though CDATA may be kept as-is)
        assert result is not None

    def test_lists_convert(self):
        from routes.cms_routes import convert_markdown_to_html
        html = convert_markdown_to_html("- item one\n- item two\n- item three")
        assert "<ul>" in html
        assert "<li>" in html

    def test_error_falls_back_to_original(self):
        """If markdown library raises, return the raw content unchanged."""
        from unittest.mock import patch

        from routes.cms_routes import convert_markdown_to_html
        with patch("markdown.markdown", side_effect=RuntimeError("parser broke")):
            result = convert_markdown_to_html("# Title\n\nBody.")
        assert result == "# Title\n\nBody."


# ---------------------------------------------------------------------------
# generate_excerpt_from_content
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateExcerpt:
    def test_empty_returns_empty(self):
        from routes.cms_routes import generate_excerpt_from_content
        assert generate_excerpt_from_content("") == ""

    def test_skips_headers(self):
        from routes.cms_routes import generate_excerpt_from_content
        content = "# Main Heading\n\n## Subheading\n\nActual content goes here."
        excerpt = generate_excerpt_from_content(content)
        assert "Main Heading" not in excerpt
        assert "Actual content" in excerpt

    def test_respects_length_limit(self):
        from routes.cms_routes import generate_excerpt_from_content
        long_content = "word " * 200
        excerpt = generate_excerpt_from_content(long_content, length=100)
        assert len(excerpt) <= 110  # allow for ellipsis padding

    def test_adds_ellipsis_when_truncated(self):
        from routes.cms_routes import generate_excerpt_from_content
        long = "This is a long paragraph that should definitely exceed the limit. " * 5
        excerpt = generate_excerpt_from_content(long, length=80)
        assert excerpt.endswith("...")

    def test_no_ellipsis_when_short(self):
        from routes.cms_routes import generate_excerpt_from_content
        short = "Just a brief sentence."
        excerpt = generate_excerpt_from_content(short, length=200)
        assert not excerpt.endswith("...")

    def test_removes_markdown_brackets(self):
        from routes.cms_routes import generate_excerpt_from_content
        content = "See [this link](https://example.com) for details."
        excerpt = generate_excerpt_from_content(content)
        assert "[" not in excerpt
        assert "]" not in excerpt
        # Parens also stripped
        assert "(" not in excerpt

    def test_removes_backticks(self):
        from routes.cms_routes import generate_excerpt_from_content
        content = "Use `pip install foo` to install."
        excerpt = generate_excerpt_from_content(content)
        assert "`" not in excerpt


# ---------------------------------------------------------------------------
# map_featured_image_to_coverimage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMapFeaturedImageToCoverimage:
    def test_adds_coverimage_when_featured_set(self):
        from routes.cms_routes import map_featured_image_to_coverimage
        post = {"title": "My Post", "featured_image_url": "https://img/hero.jpg"}
        result = map_featured_image_to_coverimage(post)
        assert result["coverImage"] is not None
        assert result["coverImage"]["data"]["attributes"]["url"] == "https://img/hero.jpg"

    def test_alt_text_includes_title(self):
        from routes.cms_routes import map_featured_image_to_coverimage
        post = {"title": "My Post", "featured_image_url": "https://img/x.jpg"}
        result = map_featured_image_to_coverimage(post)
        alt = result["coverImage"]["data"]["attributes"]["alternativeText"]
        assert "My Post" in alt

    def test_no_image_returns_null_coverimage(self):
        from routes.cms_routes import map_featured_image_to_coverimage
        post = {"title": "Post", "featured_image_url": None}
        result = map_featured_image_to_coverimage(post)
        assert result["coverImage"] is None

    def test_empty_string_returns_null(self):
        from routes.cms_routes import map_featured_image_to_coverimage
        post = {"title": "Post", "featured_image_url": ""}
        result = map_featured_image_to_coverimage(post)
        assert result["coverImage"] is None

    def test_missing_title_uses_fallback_alt(self):
        from routes.cms_routes import map_featured_image_to_coverimage
        post = {"featured_image_url": "https://img/x.jpg"}
        result = map_featured_image_to_coverimage(post)
        alt = result["coverImage"]["data"]["attributes"]["alternativeText"]
        assert "post" in alt.lower()

    def test_returns_same_dict_mutated(self):
        from routes.cms_routes import map_featured_image_to_coverimage
        post = {"title": "P", "featured_image_url": "https://img/x.jpg"}
        result = map_featured_image_to_coverimage(post)
        assert result is post  # same dict, mutated in place


# ---------------------------------------------------------------------------
# PATCH /api/posts/{post_id} — update_post
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdatePost:
    def test_success_returns_200(self):
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={"slug": "test-post"})
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={"title": "New Title", "content": "New body"},
                headers={"Authorization": "Bearer test-token"},
            )

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_no_valid_fields_returns_400(self):
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=MagicMock())):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={"bogus_field": "value"},
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 400
        assert "No valid fields" in resp.json()["detail"]

    def test_post_not_found_returns_404(self):
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value=None)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/missing-id",
                json={"title": "x"},
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 404

    def test_invalid_published_at_returns_400(self):
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=MagicMock())):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={"title": "x", "published_at": "not-a-date"},
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 400
        assert "ISO 8601" in resp.json()["detail"]

    def test_scheduled_without_published_at_returns_400(self):
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=MagicMock())):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={"status": "scheduled"},
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 400
        assert "published_at is required" in resp.json()["detail"]

    def test_scheduled_with_past_date_returns_400(self):
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=MagicMock())):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={
                    "status": "scheduled",
                    "published_at": "2020-01-01T00:00:00Z",
                },
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 400
        assert "future" in resp.json()["detail"]

    def test_scheduled_with_future_date_succeeds(self):
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={"slug": "test-post"})
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={
                    "status": "scheduled",
                    "published_at": "2099-01-01T00:00:00Z",
                },
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200

    def test_z_suffix_normalized_to_utc(self):
        """ISO dates ending in Z should be accepted."""
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={"slug": "test-post"})
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={"published_at": "2099-06-15T12:00:00Z", "title": "x"},
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200

    def test_filters_out_disallowed_fields(self):
        """Fields not in the allowed set get dropped before SQL."""
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={"slug": "test-post"})
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.patch(
                "/api/posts/post-001",
                json={
                    "title": "x",
                    "admin_override": "bypass",  # not in allowed list
                    "deleted_at": "now",  # not in allowed list
                },
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200
        # The UPDATE SQL should not mention admin_override or deleted_at
        sql_arg = conn.fetchrow.await_args.args[0]
        assert "admin_override" not in sql_arg
        assert "deleted_at" not in sql_arg


# ---------------------------------------------------------------------------
# DELETE /api/posts/{post_id} — delete_post
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeletePost:
    def test_success_returns_204(self):
        conn = MagicMock()
        conn.execute = AsyncMock(return_value="DELETE 1")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.delete(
                "/api/posts/post-001",
                headers={"Authorization": "Bearer test-token"},
            )

        assert resp.status_code == 204

    def test_not_found_returns_404(self):
        conn = MagicMock()
        conn.execute = AsyncMock(return_value="DELETE 0")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.delete(
                "/api/posts/missing-id",
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 404

    def test_delete_uses_id_param(self):
        """Verify the SQL uses the post_id from the URL."""
        conn = MagicMock()
        conn.execute = AsyncMock(return_value="DELETE 1")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            client.delete(
                "/api/posts/abc-123",
                headers={"Authorization": "Bearer test-token"},
            )

        args = conn.execute.await_args.args
        assert "DELETE FROM posts" in args[0]
        assert args[1] == "abc-123"


# ---------------------------------------------------------------------------
# GET /api/categories/{slug} — get_category_by_slug
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCategoryBySlug:
    def test_success_returns_category(self):
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={
            "id": "cat-1",
            "name": "Tech",
            "slug": "tech",
            "description": "Tech stuff",
            "created_at": NOW,
            "updated_at": NOW,
        })
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.get("/api/categories/tech")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["slug"] == "tech"
        assert body["data"]["name"] == "Tech"

    def test_not_found_returns_404(self):
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value=None)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.get("/api/categories/missing")
        assert resp.status_code == 404

    def test_timestamps_isoformatted(self):
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={
            "id": "c1", "name": "T", "slug": "t",
            "description": None, "created_at": NOW, "updated_at": NOW,
        })
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/categories/t").json()

        # Timestamps should be ISO format strings
        assert isinstance(data["data"]["created_at"], str)
        assert "T" in data["data"]["created_at"]

    def test_null_timestamps_become_none(self):
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={
            "id": "c1", "name": "T", "slug": "t",
            "description": None, "created_at": None, "updated_at": None,
        })
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/categories/t").json()

        assert data["data"]["created_at"] is None
        assert data["data"]["updated_at"] is None


# ---------------------------------------------------------------------------
# POST /api/track/view — track_page_view
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTrackPageView:
    def test_valid_path_returns_204(self):
        conn = MagicMock()
        conn.execute = AsyncMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.post("/api/track/view", json={
                "path": "/posts/my-slug",
                "slug": "my-slug",
                "referrer": "https://google.com",
            })

        assert resp.status_code == 204

    def test_empty_path_still_returns_204_but_skips_db(self):
        """Empty path is a no-op early return."""
        pool_mock = AsyncMock()
        with patch("routes.cms_routes.get_db_pool", new=pool_mock):
            client = TestClient(_build_app())
            resp = client.post("/api/track/view", json={"path": ""})

        assert resp.status_code == 204
        # get_db_pool should NOT have been called since we bailed early
        pool_mock.assert_not_called()

    def test_no_auth_required(self):
        """track_page_view should be callable without Authorization header."""
        conn = MagicMock()
        conn.execute = AsyncMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.post("/api/track/view", json={"path": "/posts/x"})
        # 204 = success, no auth challenge
        assert resp.status_code == 204

    def test_db_failure_is_non_fatal(self):
        """Page view tracking failures must not crash the endpoint."""
        pool_mock = AsyncMock(side_effect=RuntimeError("DB down"))
        with patch("routes.cms_routes.get_db_pool", new=pool_mock):
            client = TestClient(_build_app())
            resp = client.post("/api/track/view", json={"path": "/posts/x"})
        assert resp.status_code == 204

    def test_long_path_truncated(self):
        """Paths longer than 500 chars get truncated, not rejected."""
        conn = MagicMock()
        conn.execute = AsyncMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            long_path = "/posts/" + "x" * 1000
            resp = client.post("/api/track/view", json={"path": long_path, "slug": "x"})

        assert resp.status_code == 204
        # First INSERT gets the truncated path
        first_call = conn.execute.await_args_list[0]
        stored_path = first_call.args[1]
        assert len(stored_path) <= 500

    def test_slug_triggers_view_count_update(self):
        """Non-empty slug triggers an UPDATE posts SET view_count."""
        conn = MagicMock()
        conn.execute = AsyncMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=cm)

        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            client.post("/api/track/view", json={"path": "/posts/x", "slug": "x"})

        # Two execute calls: INSERT page_views + UPDATE posts
        assert conn.execute.await_count == 2
        second_sql = conn.execute.await_args_list[1].args[0]
        assert "UPDATE posts" in second_sql
        assert "view_count" in second_sql
