"""Unit tests for ``modules.content.posts_service.PostsService``.

All tests construct the service directly with an asyncpg-like mock pool —
no real DB connection required. The mock simulates ``pool.acquire()`` as an
async context manager yielding a connection with ``fetch``, ``fetchrow``,
``fetchval``, and ``execute`` as ``AsyncMock``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.content.posts_service import PostsService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)


def _make_pool(
    *,
    fetch_return=None,
    fetchrow_return=None,
    fetchval_return=None,
    execute_return="DELETE 1",
):
    """Return (pool, conn) with AsyncMock DB methods.

    ``pool.acquire()`` is an async context manager that yields ``conn``.
    """
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.execute = AsyncMock(return_value=execute_return)

    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool, conn


def _post_row(overrides=None):
    """Build a minimal post row dict suitable for fetch() returns."""
    row = {
        "id": "post-001",
        "title": "Test Post",
        "slug": "test-post",
        "excerpt": None,
        "featured_image_url": None,
        "cover_image_url": None,
        "category_id": None,
        "published_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
        "seo_title": "SEO Title",
        "seo_description": "SEO Desc",
        "seo_keywords": "test",
        "status": "published",
        "content": "# Hello\n\nContent here.",
        "author_id": "author-001",
        "total_count": 1,
    }
    if overrides:
        row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# get_published_post_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPublishedPostCount:
    async def test_returns_count_from_db(self):
        pool, _ = _make_pool(fetchval_return=42)
        svc = PostsService(pool=pool)
        assert await svc.get_published_post_count() == 42

    async def test_returns_zero_when_none(self):
        pool, _ = _make_pool(fetchval_return=None)
        svc = PostsService(pool=pool)
        assert await svc.get_published_post_count() == 0


# ---------------------------------------------------------------------------
# list_posts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListPosts:
    async def test_empty_returns_standard_envelope(self):
        pool, _ = _make_pool(fetch_return=[])
        svc = PostsService(pool=pool)
        result = await svc.list_posts()
        assert result == {"posts": [], "total": 0, "offset": 0, "limit": 20}

    async def test_returns_post_with_timestamps_iso(self):
        row = _post_row()
        pool, _ = _make_pool(fetch_return=[row])
        svc = PostsService(pool=pool)
        result = await svc.list_posts()
        posts = result["posts"]
        assert len(posts) == 1
        assert posts[0]["published_at"] == NOW.isoformat()

    async def test_custom_offset_and_limit(self):
        pool, _ = _make_pool(fetch_return=[])
        svc = PostsService(pool=pool)
        result = await svc.list_posts(offset=10, limit=5)
        assert result["offset"] == 10
        assert result["limit"] == 5

    async def test_total_from_window_function(self):
        row = _post_row({"total_count": 99})
        pool, _ = _make_pool(fetch_return=[row])
        svc = PostsService(pool=pool)
        result = await svc.list_posts()
        assert result["total"] == 99

    async def test_content_converted_to_html(self):
        row = _post_row({"content": "**bold**"})
        pool, _ = _make_pool(fetch_return=[row])
        svc = PostsService(pool=pool)
        result = await svc.list_posts()
        # Markdown should be converted; total_count removed from post dict
        post = result["posts"][0]
        assert "total_count" not in post
        assert "<strong>" in post["content"] or "<b>" in post["content"]

    async def test_excerpt_generated_when_missing(self):
        row = _post_row({"excerpt": None, "content": "This is a plain paragraph."})
        pool, _ = _make_pool(fetch_return=[row])
        svc = PostsService(pool=pool)
        result = await svc.list_posts()
        assert result["posts"][0]["excerpt"]


# ---------------------------------------------------------------------------
# search_posts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchPosts:
    async def test_empty_query_returns_empty(self):
        pool, _ = _make_pool()
        svc = PostsService(pool=pool)
        result = await svc.search_posts(q="   ")
        assert result == {"posts": [], "total": 0, "offset": 0, "limit": 50}

    async def test_returns_matching_posts(self):
        row = _post_row()
        pool, _ = _make_pool(fetch_return=[row])
        svc = PostsService(pool=pool)
        result = await svc.search_posts(q="Hello")
        assert len(result["posts"]) == 1
        assert result["total"] == 1

    async def test_custom_limit_respected(self):
        pool, _ = _make_pool(fetch_return=[])
        svc = PostsService(pool=pool)
        result = await svc.search_posts(q="test", limit=10)
        assert result["limit"] == 10


# ---------------------------------------------------------------------------
# get_post_by_slug
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPostBySlug:
    async def test_returns_none_when_not_found(self):
        pool, _ = _make_pool(fetchrow_return=None, fetch_return=[])
        svc = PostsService(pool=pool)
        result = await svc.get_post_by_slug("nonexistent")
        assert result is None

    async def test_returns_data_and_meta(self):
        row = _post_row()
        # fetchrow returns the post; fetch returns tags
        pool, _ = _make_pool(fetchrow_return=row, fetch_return=[])
        svc = PostsService(pool=pool)
        result = await svc.get_post_by_slug("test-post")
        assert result is not None
        assert "data" in result
        assert "meta" in result
        assert "tags" in result["meta"]
        assert "category" in result["meta"]

    async def test_timestamps_are_iso(self):
        row = _post_row()
        pool, _ = _make_pool(fetchrow_return=row, fetch_return=[])
        svc = PostsService(pool=pool)
        result = await svc.get_post_by_slug("test-post")
        assert result["data"]["published_at"] == NOW.isoformat()

    async def test_tags_included_in_meta(self):
        row = _post_row()
        tag_row = {"id": "t-1", "name": "Python", "slug": "python"}
        pool, _ = _make_pool(fetchrow_return=row, fetch_return=[tag_row])
        svc = PostsService(pool=pool)
        result = await svc.get_post_by_slug("test-post")
        assert result["meta"]["tags"] == [tag_row]


# ---------------------------------------------------------------------------
# delete_post
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeletePost:
    async def test_returns_true_when_deleted(self):
        pool, _ = _make_pool(execute_return="DELETE 1")
        svc = PostsService(pool=pool)
        assert await svc.delete_post("post-001") is True

    async def test_returns_false_when_not_found(self):
        pool, _ = _make_pool(execute_return="DELETE 0")
        svc = PostsService(pool=pool)
        assert await svc.delete_post("missing") is False


# ---------------------------------------------------------------------------
# list_categories
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListCategories:
    async def test_empty_returns_standard_envelope(self):
        pool, _ = _make_pool(fetch_return=[])
        svc = PostsService(pool=pool)
        result = await svc.list_categories()
        assert result == {"categories": [], "total": 0, "offset": 0, "limit": 100}

    async def test_pagination_slices_results(self):
        cats = [
            {"id": f"c-{i}", "name": f"Cat {i}", "slug": f"cat-{i}",
             "description": None, "created_at": NOW, "updated_at": NOW}
            for i in range(5)
        ]
        pool, _ = _make_pool(fetch_return=cats)
        svc = PostsService(pool=pool)
        result = await svc.list_categories(offset=2, limit=2)
        assert result["total"] == 5
        assert len(result["categories"]) == 2
        assert result["categories"][0]["name"] == "Cat 2"


# ---------------------------------------------------------------------------
# list_tags
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListTags:
    async def test_empty_returns_standard_envelope(self):
        pool, _ = _make_pool(fetch_return=[])
        svc = PostsService(pool=pool)
        result = await svc.list_tags()
        assert result == {"tags": [], "total": 0, "offset": 0, "limit": 100}

    async def test_timestamps_iso_formatted(self):
        tag = {
            "id": "t-1", "name": "Python", "slug": "python",
            "description": None, "created_at": NOW, "updated_at": NOW,
        }
        pool, _ = _make_pool(fetch_return=[tag])
        svc = PostsService(pool=pool)
        result = await svc.list_tags()
        assert result["tags"][0]["created_at"] == NOW.isoformat()
