"""refresh_live_post — an edit to a published post must reach the live site.

2026-10-18: retitling a live post changed the DB row only; the site serves
each post from its static JSON, so readers saw the old title until the post
was re-exported by hand.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services.publish_service import refresh_live_post


def _pool(row):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool


@pytest.mark.asyncio
async def test_a_published_post_is_re_exported_and_revalidated():
    pool = _pool({"slug": "kumo", "status": "published"})
    sc = MagicMock()
    with patch("poindexter.services.static_export_service.export_post", new=AsyncMock(return_value=True)) as exp, \
         patch("poindexter.services.revalidation_service.trigger_isr_revalidate", new=AsyncMock(return_value=True)) as rev:
        out = await refresh_live_post(pool, "p1", site_config=sc)
    exp.assert_awaited_once_with(pool, "kumo", site_config=sc)
    rev.assert_awaited_once()
    assert out == {"refreshed": True, "slug": "kumo", "exported": True, "revalidated": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("row,reason", [({"slug": "d", "status": "draft"}, "not_published"), (None, "post_not_found")])
async def test_an_unpublished_or_missing_post_is_left_alone(row, reason):
    with patch("poindexter.services.static_export_service.export_post", new=AsyncMock()) as exp:
        out = await refresh_live_post(_pool(row), "p1", site_config=MagicMock())
    exp.assert_not_awaited()
    assert out == {"refreshed": False, "reason": reason}


@pytest.mark.asyncio
async def test_a_failed_export_is_reported_not_raised():
    pool = _pool({"slug": "kumo", "status": "published"})
    with patch("poindexter.services.static_export_service.export_post", new=AsyncMock(side_effect=RuntimeError("r2 down"))), \
         patch("poindexter.services.revalidation_service.trigger_isr_revalidate", new=AsyncMock(return_value=True)):
        out = await refresh_live_post(pool, "p1", site_config=MagicMock())
    assert out["refreshed"] is False and out["exported"] is False


@pytest.mark.asyncio
async def test_the_cli_patch_carries_the_token():
    """`posts retitle` went PUT → 405 → PATCH on the raw client (no token) → 401."""
    from poindexter.cli import posts as cli_posts

    client = MagicMock()
    client.patch = AsyncMock(return_value="resp")
    client.json_or_raise = AsyncMock(return_value={"success": True})
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)
    with patch.object(cli_posts, "WorkerClient", return_value=cm):
        assert await cli_posts._patch_post("676c6660", {"title": "T"}) == {"success": True}
    client.patch.assert_awaited_once_with("/api/posts/676c6660", json={"title": "T"})


def test_worker_client_patch_goes_through_the_authenticated_request():
    import inspect

    from poindexter.cli._api_client import WorkerClient

    assert '_authed_request("PATCH"' in inspect.getsource(WorkerClient.patch)
