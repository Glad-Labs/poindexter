"""Unit tests for ``services/jobs/sync_postiz_delivery_state.py``.

PostizClient mocked. Focus: the ERROR→failed demotion (+finding), the
PUBLISHED permalink stamp, in-flight/unknown rows left alone, and the
fail-loud contract when Postiz is unreachable (a delivery auditor must
never report "all fine" from behind a dead API — the 2026-08-26 X
credits-depleted incident sat invisible for five days exactly because
nothing read delivery state back).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.sync_postiz_delivery_state import SyncPostizDeliveryStateJob


def _site_config(settings: dict[str, str]) -> MagicMock:
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": settings.get(key, default)
    sc.get_secret = AsyncMock(return_value="test-key")
    return sc


_ENABLED = {"social_drafts_enabled": "true", "postiz_api_url": "http://postiz:3000"}


def _draft_row(draft_id: str, postiz_id: str, platform: str = "twitter") -> dict:
    return {
        "id": draft_id,
        "platform": platform,
        "postiz_post_id": postiz_id,
        "posted_at": datetime.now(timezone.utc) - timedelta(hours=2),
    }


def _pool(rows: list[dict]) -> MagicMock:
    """A pool whose acquire() yields a conn with fetch→rows and execute recorded."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.execute = AsyncMock()
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool


def _patch_client(posts: list[dict] | None = None, raises: BaseException | None = None):
    inst = MagicMock()
    if raises is not None:
        inst.list_posts = AsyncMock(side_effect=raises)
    else:
        inst.list_posts = AsyncMock(return_value=posts or [])
    ctor = MagicMock(return_value=inst)
    return patch("services.integrations.postiz_client.PostizClient", ctor), inst


@pytest.mark.asyncio
async def test_no_site_config_returns_not_ok():
    job = SyncPostizDeliveryStateJob()
    result = await job.run(pool=MagicMock(), config={})
    assert result.ok is False
    assert "_site_config" in result.detail


@pytest.mark.asyncio
async def test_disabled_is_noop():
    job = SyncPostizDeliveryStateJob()
    cfg = {"_site_config": _site_config({"social_drafts_enabled": "false"})}
    result = await job.run(pool=MagicMock(), config=cfg)
    assert result.ok is True
    assert "no-op" in result.detail


@pytest.mark.asyncio
async def test_no_unverified_drafts_is_noop():
    job = SyncPostizDeliveryStateJob()
    cfg = {"_site_config": _site_config(_ENABLED)}
    result = await job.run(pool=_pool([]), config=cfg)
    assert result.ok is True
    assert "no unverified" in result.detail


@pytest.mark.asyncio
async def test_error_state_demotes_to_failed_and_emits_finding():
    job = SyncPostizDeliveryStateJob()
    cfg = {"_site_config": _site_config(_ENABLED)}
    pool = _pool([_draft_row("d1", "pz1")])
    ctx, _ = _patch_client(posts=[{"id": "pz1", "state": "ERROR"}])
    with ctx, patch(
        "services.jobs.sync_postiz_delivery_state.emit_finding"
    ) as finding:
        result = await job.run(pool=pool, config=cfg)
    assert result.ok is True
    assert result.changes_made == 1
    assert result.metrics["errored"] == 1
    conn = pool.acquire.return_value.__aenter__.return_value
    demote_sql = conn.execute.await_args_list[0].args[0]
    assert "status = 'failed'" in demote_sql
    assert "posted_at = NULL" in demote_sql
    finding.assert_called_once()
    assert finding.call_args.kwargs["kind"] == "social_post_delivery_failed"
    assert finding.call_args.kwargs["severity"] == "warn"


@pytest.mark.asyncio
async def test_published_state_stamps_release_url():
    job = SyncPostizDeliveryStateJob()
    cfg = {"_site_config": _site_config(_ENABLED)}
    pool = _pool([_draft_row("d1", "pz1", platform="bluesky")])
    ctx, _ = _patch_client(
        posts=[{"id": "pz1", "state": "PUBLISHED",
                "releaseURL": "https://bsky.app/profile/x/post/y"}]
    )
    with ctx, patch(
        "services.jobs.sync_postiz_delivery_state.emit_finding"
    ) as finding:
        result = await job.run(pool=pool, config=cfg)
    assert result.ok is True
    assert result.metrics["published"] == 1
    conn = pool.acquire.return_value.__aenter__.return_value
    stamp_args = conn.execute.await_args_list[0].args
    assert "platform_config" in stamp_args[0]
    assert "bsky.app" in stamp_args[2]
    finding.assert_not_called()


@pytest.mark.asyncio
async def test_queue_and_missing_ids_are_left_alone():
    job = SyncPostizDeliveryStateJob()
    cfg = {"_site_config": _site_config(_ENABLED)}
    pool = _pool([_draft_row("d1", "pz1"), _draft_row("d2", "pz-missing")])
    ctx, _ = _patch_client(posts=[{"id": "pz1", "state": "QUEUE"}])
    with ctx:
        result = await job.run(pool=pool, config=cfg)
    assert result.ok is True
    assert result.changes_made == 0
    assert result.metrics["in_flight"] == 1
    assert result.metrics["unknown"] == 1
    conn = pool.acquire.return_value.__aenter__.return_value
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_postiz_unreachable_is_a_failed_run_not_a_quiet_pass():
    job = SyncPostizDeliveryStateJob()
    cfg = {"_site_config": _site_config(_ENABLED)}
    pool = _pool([_draft_row("d1", "pz1")])
    ctx, _ = _patch_client(raises=RuntimeError("connect refused"))
    with ctx:
        result = await job.run(pool=pool, config=cfg)
    assert result.ok is False
    assert "unreachable" in result.detail.lower()


@pytest.mark.asyncio
async def test_db_query_failure_returns_not_ok():
    job = SyncPostizDeliveryStateJob()
    cfg = {"_site_config": _site_config(_ENABLED)}
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=RuntimeError("db down"))
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_cm)
    result = await job.run(pool=pool, config=cfg)
    assert result.ok is False
    assert "db down" in result.detail
