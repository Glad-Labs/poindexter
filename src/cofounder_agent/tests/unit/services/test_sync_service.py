"""
Unit tests for services/sync_service.py

Covers SyncService:
- __init__: respects custom URLs, falls back to defaults/env
- _require_pools: returns False when pools are None, True when both set
- push_post: returns False when pools missing
- push_post: returns False when post not found in local DB
- push_post: upserts category, tags, post, and post_tags to cloud
- push_post: returns False and logs on exception
- push_all_posts: returns zero counts when pools missing
- push_all_posts: iterates published posts and delegates to push_post
- pull_metrics: returns skipped status when pools missing
- pull_metrics: delegates to _pull_web_analytics and _pull_newsletter_stats
- pull_metrics: handles exceptions in sub-methods gracefully
- close: closes both pools, sets them to None
- close: tolerates exceptions during pool close
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.sync_service import SyncService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_record(data: dict):
    """Create a mock asyncpg.Record that supports dict-style access and .get()."""
    rec = MagicMock()
    rec.__getitem__ = lambda self, key: data[key]
    rec.get = lambda key, default=None: data.get(key, default)
    rec.keys = lambda: data.keys()
    return rec


class _FakeAcquireCtx:
    """Async context manager that mimics asyncpg pool.acquire()."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _make_pool():
    """Create a mock asyncpg Pool that yields a mock connection via acquire()."""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value = _FakeAcquireCtx(conn)
    pool.close = AsyncMock()
    return pool, conn


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSyncServiceInit:
    def test_uses_custom_urls(self):
        svc = SyncService(cloud_url="postgres://cloud", local_url="postgres://local")
        assert svc.cloud_url == "postgres://cloud"
        assert svc.local_url == "postgres://local"

    def test_defaults_from_env(self):
        with patch.dict("os.environ", {"CLOUD_DATABASE_URL": "postgres://env-cloud", "LOCAL_DATABASE_URL": "postgres://env-local"}):
            svc = SyncService()
            assert svc.cloud_url == "postgres://env-cloud"
            assert svc.local_url == "postgres://env-local"

    def test_pools_initially_none(self):
        svc = SyncService(cloud_url="x", local_url="y")
        assert svc._cloud_pool is None
        assert svc._local_pool is None


# ---------------------------------------------------------------------------
# _require_pools
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequirePools:
    def test_returns_false_when_cloud_pool_none(self):
        svc = SyncService(cloud_url="x", local_url="y")
        svc._cloud_pool = None
        svc._local_pool = MagicMock()
        assert svc._require_pools() is False

    def test_returns_false_when_local_pool_none(self):
        svc = SyncService(cloud_url="x", local_url="y")
        svc._cloud_pool = MagicMock()
        svc._local_pool = None
        assert svc._require_pools() is False

    def test_returns_true_when_both_pools_set(self):
        svc = SyncService(cloud_url="x", local_url="y")
        svc._cloud_pool = MagicMock()
        svc._local_pool = MagicMock()
        assert svc._require_pools() is True


# ---------------------------------------------------------------------------
# push_post
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPushPost:
    @pytest.mark.asyncio
    async def test_returns_false_when_pools_missing(self):
        svc = SyncService(cloud_url="x", local_url="y")
        result = await svc.push_post("post-123")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_post_not_found(self):
        svc = SyncService(cloud_url="x", local_url="y")
        local_pool, local_conn = _make_pool()
        cloud_pool, _ = _make_pool()
        svc._local_pool = local_pool
        svc._cloud_pool = cloud_pool

        local_conn.fetchrow = AsyncMock(return_value=None)

        result = await svc.push_post("nonexistent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_pushes_post_with_category_and_tags(self):
        svc = SyncService(cloud_url="x", local_url="y")
        local_pool, local_conn = _make_pool()
        cloud_pool, cloud_conn = _make_pool()
        svc._local_pool = local_pool
        svc._cloud_pool = cloud_pool

        post_data = {
            "id": "p1", "title": "Test", "slug": "test", "content": "body",
            "excerpt": None, "featured_image_url": None, "cover_image_url": None,
            "author_id": None, "category_id": "cat1", "tag_ids": [],
            "status": "published", "seo_title": None, "seo_description": None,
            "seo_keywords": None, "created_by": None, "updated_by": None,
            "created_at": "2025-01-01", "updated_at": "2025-01-01",
            "published_at": "2025-01-01",
        }
        cat_data = {
            "id": "cat1", "name": "AI", "slug": "ai",
            "description": "AI articles", "created_at": "2025-01-01",
            "updated_at": "2025-01-01",
        }
        tag_data = {
            "id": "t1", "name": "ml", "slug": "ml",
            "description": None, "created_at": "2025-01-01",
            "updated_at": "2025-01-01",
        }
        post_tag_data = {"post_id": "p1", "tag_id": "t1"}

        post_rec = _make_record(post_data)
        cat_rec = _make_record(cat_data)
        tag_rec = _make_record(tag_data)
        pt_rec = _make_record(post_tag_data)

        # Local fetchrow returns post, then category
        local_conn.fetchrow = AsyncMock(side_effect=[post_rec, cat_rec])
        # Local fetch returns tags, then post_tags
        local_conn.fetch = AsyncMock(side_effect=[[tag_rec], [pt_rec]])

        # Cloud transaction context manager — transaction() is a regular call
        # that returns an async context manager
        class _FakeTxCtx:
            async def __aenter__(self):
                return None
            async def __aexit__(self, *exc):
                return False

        cloud_conn.transaction = MagicMock(return_value=_FakeTxCtx())
        cloud_conn.execute = AsyncMock()

        result = await svc.push_post("p1")
        assert result is True
        # Should have called execute for: category, tag, post, post_tag = 4 calls
        assert cloud_conn.execute.call_count == 4

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        svc = SyncService(cloud_url="x", local_url="y")
        local_pool, local_conn = _make_pool()
        cloud_pool, _ = _make_pool()
        svc._local_pool = local_pool
        svc._cloud_pool = cloud_pool

        local_conn.fetchrow = AsyncMock(side_effect=RuntimeError("DB down"))

        result = await svc.push_post("p1")
        assert result is False


# ---------------------------------------------------------------------------
# push_all_posts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPushAllPosts:
    @pytest.mark.asyncio
    async def test_returns_zero_counts_when_pools_missing(self):
        svc = SyncService(cloud_url="x", local_url="y")
        result = await svc.push_all_posts()
        assert result == {"pushed": 0, "skipped": 0, "failed": 0}

    @pytest.mark.asyncio
    async def test_iterates_published_posts(self):
        svc = SyncService(cloud_url="x", local_url="y")
        local_pool, local_conn = _make_pool()
        cloud_pool, _ = _make_pool()
        svc._local_pool = local_pool
        svc._cloud_pool = cloud_pool

        row1 = _make_record({"id": "p1"})
        row2 = _make_record({"id": "p2"})
        local_conn.fetch = AsyncMock(return_value=[row1, row2])

        with patch.object(svc, "push_post", new_callable=AsyncMock) as mock_push:
            mock_push.return_value = True
            result = await svc.push_all_posts()

        assert result["pushed"] == 2
        assert mock_push.call_count == 2


# ---------------------------------------------------------------------------
# pull_metrics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPullMetrics:
    @pytest.mark.asyncio
    async def test_returns_skipped_when_pools_missing(self):
        svc = SyncService(cloud_url="x", local_url="y")
        result = await svc.pull_metrics()
        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_delegates_to_sub_methods(self):
        svc = SyncService(cloud_url="x", local_url="y")
        svc._cloud_pool = MagicMock()
        svc._local_pool = MagicMock()

        with patch.object(svc, "_pull_web_analytics", new_callable=AsyncMock, return_value={"rows_pulled": 5}) as mock_wa, \
             patch.object(svc, "_pull_newsletter_stats", new_callable=AsyncMock, return_value={"total": 42}) as mock_nl:
            result = await svc.pull_metrics()

        assert result["web_analytics"] == {"rows_pulled": 5}
        assert result["newsletter"] == {"total": 42}
        mock_wa.assert_awaited_once()
        mock_nl.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_sub_method_exceptions(self):
        svc = SyncService(cloud_url="x", local_url="y")
        svc._cloud_pool = MagicMock()
        svc._local_pool = MagicMock()

        with patch.object(svc, "_pull_web_analytics", new_callable=AsyncMock, side_effect=RuntimeError("fail")), \
             patch.object(svc, "_pull_newsletter_stats", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            result = await svc.pull_metrics()

        assert "error" in result["web_analytics"]
        assert "error" in result["newsletter"]


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClose:
    @pytest.mark.asyncio
    async def test_closes_both_pools(self):
        svc = SyncService(cloud_url="x", local_url="y")
        cloud_pool = AsyncMock()
        local_pool = AsyncMock()
        svc._cloud_pool = cloud_pool
        svc._local_pool = local_pool

        await svc.close()

        cloud_pool.close.assert_awaited_once()
        local_pool.close.assert_awaited_once()
        assert svc._cloud_pool is None
        assert svc._local_pool is None

    @pytest.mark.asyncio
    async def test_tolerates_close_exceptions(self):
        svc = SyncService(cloud_url="x", local_url="y")
        cloud_pool = AsyncMock()
        cloud_pool.close = AsyncMock(side_effect=RuntimeError("close failed"))
        local_pool = AsyncMock()
        svc._cloud_pool = cloud_pool
        svc._local_pool = local_pool

        await svc.close()  # should not raise
        assert svc._cloud_pool is None
        assert svc._local_pool is None
