"""Unit tests for services.publish_service.unpublish_post — the one-step
takedown / immediate-rollback path (poindexter#684).

``unpublish_post`` is the inverse of ``publish_now``: it flips a live post
``published`` → ``draft`` (and reverts the linked ``pipeline_tasks`` row
``published`` → ``approved`` to keep the seam in lockstep), then retires the
post's static JSON from storage and busts its ISR cache via ``_retire_slug``
so the live site drops it immediately — instead of waiting for the next full
static rebuild or the hourly ISR backstop. This is the missing rollback the
2026-05-26 unauthorized auto-publish incident exposed: the PATCH route flips
``posts.status`` but never retires R2 or revalidates, so a bad post stays
served from R2 even after its status is changed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.site_config import SiteConfig

_TEST_SC = SiteConfig(initial_config={"site_url": "https://www.test-site.example.com"})


def _make_pool(post_row, *, flip="UPDATE 1"):
    """Build a pool whose single conn yields ``post_row`` for the SELECT and
    ``flip`` for the status-flip UPDATE.

    ``unpublish_post`` acquires twice (SELECT, then the UPDATE transaction);
    both reuse the same conn mock, which is fine — the mock doesn't care how
    many times ``acquire()`` is called.
    """
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=post_row)
    conn.execute = AsyncMock(return_value=flip)
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=conn)
    txn_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn_cm)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


@pytest.mark.unit
class TestUnpublishPost:
    @pytest.mark.asyncio
    async def test_unpublishes_published_post_and_retires_slug(self):
        from services.publish_service import unpublish_post

        pool, conn = _make_pool(
            {"id": "post-1", "slug": "bad-post", "status": "published"},
            flip="UPDATE 1",
        )
        with patch(
            "services.static_export_service._retire_slug",
            new=AsyncMock(),
        ) as retire:
            result = await unpublish_post(pool, "post-1", site_config=_TEST_SC)

        assert result["unpublished"] is True
        assert result["slug"] == "bad-post"
        assert "retired" in result["hooks"]
        retire.assert_awaited_once_with("bad-post", site_config=_TEST_SC)
        # The status-flip UPDATE (posts → draft) + the pipeline_tasks sync both
        # run inside the one transaction.
        assert conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_post_not_found_returns_reason_and_skips_retire(self):
        from services.publish_service import unpublish_post

        pool, conn = _make_pool(None)
        with patch(
            "services.static_export_service._retire_slug",
            new=AsyncMock(),
        ) as retire:
            result = await unpublish_post(pool, "ghost", site_config=_TEST_SC)

        assert result["unpublished"] is False
        assert result["reason"] == "post_not_found"
        retire.assert_not_awaited()
        conn.execute.assert_not_awaited()  # never reached the UPDATE

    @pytest.mark.asyncio
    async def test_not_currently_published_is_idempotent_noop(self):
        from services.publish_service import unpublish_post

        # Row exists but the guarded UPDATE (... WHERE status='published')
        # matches nothing because the post is already draft.
        pool, conn = _make_pool(
            {"id": "post-2", "slug": "already-draft", "status": "draft"},
            flip="UPDATE 0",
        )
        with patch(
            "services.static_export_service._retire_slug",
            new=AsyncMock(),
        ) as retire:
            result = await unpublish_post(pool, "post-2", site_config=_TEST_SC)

        assert result["unpublished"] is False
        assert result["reason"] == "not_published"
        retire.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retire_failure_is_non_fatal(self):
        from services.publish_service import unpublish_post

        pool, _conn = _make_pool(
            {"id": "post-3", "slug": "flaky", "status": "published"},
            flip="UPDATE 1",
        )
        with patch(
            "services.static_export_service._retire_slug",
            new=AsyncMock(side_effect=RuntimeError("R2 down")),
        ):
            result = await unpublish_post(pool, "post-3", site_config=_TEST_SC)

        # Status already flipped → report success even though retire blew up.
        assert result["unpublished"] is True
        assert "retired" not in result["hooks"]
