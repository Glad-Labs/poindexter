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

import pytest
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from routes.auth_unified import get_current_user
from routes.cms_routes import router

from tests.unit.routes.conftest import TEST_USER


# ---------------------------------------------------------------------------
# Helper: build a minimal app.
# cms_status requires auth — get_current_user overridden to return TEST_USER.
# ---------------------------------------------------------------------------


def _build_app():
    app = FastAPI()
    app.include_router(router)
    # Override auth for protected endpoints (cms_status)
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
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
        pool, conn = _make_pool_mock(
            fetchrow_return={"total": 0},
            fetch_return=[],
        )
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.get("/api/posts")
        assert resp.status_code == 200

    def test_response_has_standard_envelope(self):
        pool, conn = _make_pool_mock(
            fetchrow_return={"total": 0},
            fetch_return=[],
        )
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts").json()
        assert "posts" in data
        assert "total" in data
        assert "offset" in data
        assert "limit" in data

    def test_default_pagination_values(self):
        pool, conn = _make_pool_mock(
            fetchrow_return={"total": 0},
            fetch_return=[],
        )
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts").json()
        assert data["offset"] == 0
        assert data["limit"] == 20

    def test_custom_offset_and_limit(self):
        pool, conn = _make_pool_mock(
            fetchrow_return={"total": 50},
            fetch_return=[],
        )
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts?offset=10&limit=5").json()
        assert data["offset"] == 10
        assert data["limit"] == 5

    def test_deprecated_skip_falls_back_to_offset(self):
        """The skip param should be accepted as alias for offset."""
        pool, conn = _make_pool_mock(
            fetchrow_return={"total": 0},
            fetch_return=[],
        )
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/posts?skip=5").json()
        assert data["offset"] == 5

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

    def test_healthy_db_returns_200(self):
        pool = self._make_status_pool(all_tables_exist=True)
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            resp = client.get("/api/cms/status")
        assert resp.status_code == 200

    def test_healthy_response_has_tables_key(self):
        pool = self._make_status_pool(all_tables_exist=True)
        with patch("routes.cms_routes.get_db_pool", new=AsyncMock(return_value=pool)):
            client = TestClient(_build_app())
            data = client.get("/api/cms/status").json()
        assert "tables" in data
        assert "status" in data

    def test_db_error_returns_200_with_error_status(self):
        """On DB failure, cms_status returns 200 with status: error (not 500)."""
        with patch(
            "routes.cms_routes.get_db_pool",
            new=AsyncMock(side_effect=RuntimeError("Connection refused")),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/cms/status")
        # cms_status catches the exception and returns 200 with error payload
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "CMS status check failed" in data["detail"]

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
