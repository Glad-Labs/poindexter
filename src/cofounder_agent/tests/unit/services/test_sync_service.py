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

from datetime import datetime
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


# ---------------------------------------------------------------------------
# connect / __aenter__ / __aexit__
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_creates_both_pools(self):
        svc = SyncService(cloud_url="postgres://cloud", local_url="postgres://local")

        cloud_pool_obj = MagicMock(name="cloud_pool")
        local_pool_obj = MagicMock(name="local_pool")
        # Each call to create_pool returns a different awaitable result
        create_pool = AsyncMock(side_effect=[cloud_pool_obj, local_pool_obj])

        with patch("services.sync_service.asyncpg.create_pool", create_pool):
            await svc.connect()

        assert svc._cloud_pool is cloud_pool_obj
        assert svc._local_pool is local_pool_obj
        assert create_pool.await_count == 2

    @pytest.mark.asyncio
    async def test_connect_cloud_failure_sets_pool_to_none(self):
        """If cloud create_pool raises, _cloud_pool should be left None and local still attempted."""
        svc = SyncService(cloud_url="postgres://cloud", local_url="postgres://local")

        local_pool_obj = MagicMock(name="local_pool")
        # First call (cloud) raises, second (local) succeeds
        create_pool = AsyncMock(side_effect=[RuntimeError("cloud unreachable"), local_pool_obj])

        with patch("services.sync_service.asyncpg.create_pool", create_pool):
            await svc.connect()  # should not raise

        assert svc._cloud_pool is None
        assert svc._local_pool is local_pool_obj


@pytest.mark.unit
class TestContextManager:
    @pytest.mark.asyncio
    async def test_aenter_calls_connect(self):
        svc = SyncService(cloud_url="x", local_url="y")
        svc.connect = AsyncMock()
        result = await svc.__aenter__()
        svc.connect.assert_awaited_once()
        assert result is svc

    @pytest.mark.asyncio
    async def test_aexit_calls_close(self):
        svc = SyncService(cloud_url="x", local_url="y")
        svc.close = AsyncMock()
        await svc.__aexit__(None, None, None)
        svc.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Static upsert helpers — pure mock-the-conn assertions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpsertCategory:
    @pytest.mark.asyncio
    async def test_calls_execute_with_record_fields(self):
        conn = AsyncMock()
        rec = _make_record({
            "id": "cat-1",
            "name": "AI",
            "slug": "ai",
            "description": "AI articles",
            "created_at": datetime(2026, 1, 1),
            "updated_at": datetime(2026, 1, 2),
        })
        await SyncService._upsert_category(conn, rec)
        conn.execute.assert_awaited_once()
        args = conn.execute.await_args.args
        # First arg is the SQL string, then 6 positional record values
        assert "INSERT INTO categories" in args[0]
        assert args[1] == "cat-1"
        assert args[2] == "AI"
        assert args[3] == "ai"
        assert args[4] == "AI articles"

    @pytest.mark.asyncio
    async def test_handles_missing_description(self):
        conn = AsyncMock()
        rec = _make_record({
            "id": "cat-2",
            "name": "Tech",
            "slug": "tech",
            "created_at": datetime(2026, 1, 1),
            "updated_at": datetime(2026, 1, 2),
        })
        await SyncService._upsert_category(conn, rec)
        # description was None, passed positionally
        args = conn.execute.await_args.args
        assert args[4] is None


@pytest.mark.unit
class TestUpsertTag:
    @pytest.mark.asyncio
    async def test_calls_execute(self):
        conn = AsyncMock()
        rec = _make_record({
            "id": "tag-1",
            "name": "ollama",
            "slug": "ollama",
            "description": None,
            "created_at": datetime(2026, 1, 1),
            "updated_at": datetime(2026, 1, 2),
        })
        await SyncService._upsert_tag(conn, rec)
        conn.execute.assert_awaited_once()
        args = conn.execute.await_args.args
        assert "INSERT INTO tags" in args[0]
        assert args[1] == "tag-1"
        assert args[2] == "ollama"


@pytest.mark.unit
class TestUpsertPost:
    @pytest.mark.asyncio
    async def test_calls_execute_with_18_params(self):
        conn = AsyncMock()
        rec = _make_record({
            "id": "post-1",
            "title": "Title",
            "slug": "title",
            "content": "body",
            "excerpt": "ex",
            "featured_image_url": "https://img",
            "cover_image_url": None,
            "author_id": "a1",
            "category_id": "c1",
            "status": "published",
            "seo_title": "SEO",
            "seo_description": "SEO desc",
            "seo_keywords": "k1,k2",
            "created_by": "u1",
            "updated_by": "u1",
            "created_at": datetime(2026, 1, 1),
            "updated_at": datetime(2026, 1, 2),
            "published_at": datetime(2026, 1, 3),
        })
        await SyncService._upsert_post(conn, rec)
        conn.execute.assert_awaited_once()
        args = conn.execute.await_args.args
        # SQL + 18 positional values
        assert len(args) == 19
        assert "INSERT INTO posts" in args[0]
        assert args[1] == "post-1"
        assert args[2] == "Title"

    @pytest.mark.asyncio
    async def test_defaults_status_when_missing(self):
        conn = AsyncMock()
        rec = _make_record({
            "id": "p2",
            "title": "T",
            "slug": "t",
            "content": "c",
            "created_at": datetime(2026, 1, 1),
            "updated_at": datetime(2026, 1, 2),
        })
        await SyncService._upsert_post(conn, rec)
        args = conn.execute.await_args.args
        # status is the 10th positional value (index 10 with sql at 0)
        assert args[10] == "draft"


@pytest.mark.unit
class TestUpsertPostTag:
    @pytest.mark.asyncio
    async def test_calls_execute_with_post_id_and_tag_id(self):
        conn = AsyncMock()
        rec = _make_record({"post_id": "p1", "tag_id": "t1"})
        await SyncService._upsert_post_tag(conn, rec)
        conn.execute.assert_awaited_once()
        args = conn.execute.await_args.args
        assert "INSERT INTO post_tags" in args[0]
        assert args[1] == "p1"
        assert args[2] == "t1"


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetStatus:
    @pytest.mark.asyncio
    async def test_no_pools_connected(self):
        svc = SyncService(cloud_url="x", local_url="y")
        # Both pools None
        result = await svc.get_status()
        assert result["cloud_connected"] is False
        assert result["local_connected"] is False
        assert "checked_at" in result

    @pytest.mark.asyncio
    async def test_with_both_pools_returns_counts(self):
        svc = SyncService(cloud_url="x", local_url="y")

        cloud_pool, cloud_conn = _make_pool()
        local_pool, local_conn = _make_pool()

        cloud_conn.fetchrow = AsyncMock(return_value={"posts": 10, "categories": 3, "tags": 5})

        async def local_fetchrow(query, *args):
            q = " ".join(query.split())
            if "sync_metrics" in q:
                return {"metric_name": "newsletter", "metric_value": "{}", "synced_at": "2026-04-10"}
            return {"posts_total": 12, "posts_published": 8, "categories": 3, "tags": 5}

        local_conn.fetchrow = local_fetchrow

        svc._cloud_pool = cloud_pool
        svc._local_pool = local_pool

        result = await svc.get_status()
        assert result["cloud_connected"] is True
        assert result["local_connected"] is True
        assert result["cloud"]["posts"] == 10
        assert result["local"]["posts_total"] == 12
        assert result["local"]["posts_published"] == 8
        assert result["last_metric_sync"]["metric"] == "newsletter"

    @pytest.mark.asyncio
    async def test_cloud_query_error_captured(self):
        svc = SyncService(cloud_url="x", local_url="y")
        cloud_pool, cloud_conn = _make_pool()
        cloud_conn.fetchrow = AsyncMock(side_effect=RuntimeError("cloud down"))
        svc._cloud_pool = cloud_pool

        result = await svc.get_status()
        assert "error" in result["cloud"]
        assert "cloud down" in result["cloud"]["error"]

    @pytest.mark.asyncio
    async def test_local_query_error_captured(self):
        svc = SyncService(cloud_url="x", local_url="y")
        local_pool, local_conn = _make_pool()
        local_conn.fetchrow = AsyncMock(side_effect=RuntimeError("local down"))
        svc._local_pool = local_pool

        result = await svc.get_status()
        assert "error" in result["local"]
        assert "local down" in result["local"]["error"]
