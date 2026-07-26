"""Unit tests for services/jobs/media_orphan_sweep.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.media_orphan_sweep import (
    MediaOrphanSweepJob,
    _build_reference_haystack,
    _is_referenced,
    _select_orphans,
)
from services.site_config import SiteConfig

NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


@pytest.mark.unit
def test_haystack_includes_all_reference_sources():
    posts = [{
        "content": "see ![](https://x/images/inline/abc.webp)",
        "featured_image_url": "https://x/images/featured/f1.jpg",
        "cover_image_url": "",
        "featured_image_data": '{"url": "https://x/images/featured/f2.jpg"}',
    }]
    ma = [{"url": "https://x/video/v1.mp4", "storage_path": "/tmp/v1.mp4"}]
    feeds = ["<rss><image>https://x/podcast/cover.jpg</image></rss>"]
    hay = _build_reference_haystack(posts, ma, feeds)
    for token in ("abc.webp", "f1.jpg", "f2.jpg", "v1.mp4", "cover.jpg"):
        assert token in hay


@pytest.mark.unit
def test_is_referenced_matches_full_key_and_basename():
    hay = "body https://images.example/images/inline/abc.webp more"
    assert _is_referenced("images/inline/abc.webp", hay) is True   # basename
    assert _is_referenced("images/inline/zzz.webp", hay) is False


@pytest.mark.unit
def test_select_orphans_applies_prefix_grace_and_reference():
    objs = [
        {"key": "images/inline/live.webp", "size": 1, "last_modified": NOW - timedelta(days=60)},
        {"key": "images/inline/dead.webp", "size": 2, "last_modified": NOW - timedelta(days=60)},
        {"key": "images/inline/fresh.webp", "size": 3, "last_modified": NOW - timedelta(days=1)},
        {"key": "static/keep.json", "size": 4, "last_modified": NOW - timedelta(days=60)},
    ]
    hay = "post body references live.webp only"
    orphans = _select_orphans(
        objs, hay, ("images/", "video/", "podcast/"), now=NOW, grace_days=14,
    )
    keys = {o["key"] for o in orphans}
    assert keys == {"images/inline/dead.webp"}  # live=referenced, fresh=grace, static=prefix


_R2 = "services.r2_upload_service.R2UploadService"


def _pool(post_rows, ma_rows, *, fetch_error=None):
    conn = AsyncMock()
    if fetch_error is not None:
        conn.fetch = AsyncMock(side_effect=fetch_error)
    else:
        # run() issues the posts query first, then media_assets.
        conn.fetch = AsyncMock(side_effect=[post_rows, ma_rows])
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


def _fake_r2(objects, *, feed_text="<rss/>"):
    """Fake R2UploadService. ``list_objects`` filters by prefix like the real
    S3 API — a job that loops over multiple prefixes must not see the same
    object come back once per prefix."""
    inst = MagicMock()
    inst.get_object_text = AsyncMock(return_value=feed_text)

    async def _list_objects(prefix):
        return [o for o in objects if o["key"].startswith(prefix)]

    inst.list_objects = AsyncMock(side_effect=_list_objects)
    inst.delete_object = AsyncMock(return_value=True)
    return inst


def _cfg(**overrides):
    base = {
        "media_orphan_sweep_armed": "false",
        "media_orphan_sweep_grace_days": "14",
        "media_orphan_sweep_max_deletes_per_run": "500",
        "media_orphan_sweep_prefixes": "images/,video/,podcast/",
    }
    base.update(overrides)
    return {"_site_config": SiteConfig(initial_config=base)}


_OLD = datetime(2026, 5, 1, tzinfo=timezone.utc)  # comfortably before NOW - grace


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dry_run_reports_but_deletes_nothing():
    objs = [
        {"key": "images/inline/dead.webp", "size": 100, "last_modified": _OLD},
        {"key": "images/inline/live.webp", "size": 100, "last_modified": _OLD},
    ]
    pool = _pool([{"content": "uses live.webp"}], [])
    r2 = _fake_r2(objs)
    with patch(_R2, MagicMock(return_value=r2)):
        result = await MediaOrphanSweepJob().run(pool, _cfg())
    assert result.ok is True
    assert result.changes_made == 0
    r2.delete_object.assert_not_awaited()
    assert result.metrics["orphans_found"] == 1
    assert result.metrics["mode"] == "dry-run"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_armed_deletes_only_unreferenced_orphans():
    objs = [
        {"key": "images/inline/dead.webp", "size": 100, "last_modified": _OLD},
        {"key": "images/inline/live.webp", "size": 100, "last_modified": _OLD},
    ]
    pool = _pool([{"content": "uses live.webp"}], [])
    r2 = _fake_r2(objs)
    with patch(_R2, MagicMock(return_value=r2)):
        result = await MediaOrphanSweepJob().run(pool, _cfg(media_orphan_sweep_armed="true"))
    assert result.changes_made == 1
    deleted = [c.args[0] for c in r2.delete_object.await_args_list]
    assert deleted == ["images/inline/dead.webp"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cap_truncates_and_reports_remainder():
    objs = [
        {"key": f"images/inline/g{i}.webp", "size": 10, "last_modified": _OLD}
        for i in range(5)
    ]
    pool = _pool([{"content": "no refs"}], [])
    r2 = _fake_r2(objs)
    with patch(_R2, MagicMock(return_value=r2)):
        result = await MediaOrphanSweepJob().run(
            pool, _cfg(media_orphan_sweep_armed="true", media_orphan_sweep_max_deletes_per_run="2"),
        )
    assert result.changes_made == 2
    assert result.metrics["capped_remainder"] == 3
    assert "capped" in result.detail


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_keep_set_circuit_breaker_aborts():
    pool = _pool([], [])  # no posts, no media_assets
    r2 = _fake_r2([], feed_text="")  # empty feed too
    with patch(_R2, MagicMock(return_value=r2)):
        result = await MediaOrphanSweepJob().run(pool, _cfg(media_orphan_sweep_armed="true"))
    assert result.ok is False
    assert "keep-set" in result.detail
    r2.list_objects.assert_not_awaited()
    r2.delete_object.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_db_failure_fails_loud():
    pool = _pool([], [], fetch_error=RuntimeError("boom"))
    r2 = _fake_r2([])
    with patch(_R2, MagicMock(return_value=r2)):
        result = await MediaOrphanSweepJob().run(pool, _cfg())
    assert result.ok is False
    assert "DB query failed" in result.detail
    r2.list_objects.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_site_config_fails_loud():
    pool = _pool([], [])
    result = await MediaOrphanSweepJob().run(pool, {})
    assert result.ok is False
    assert "_site_config" in result.detail


@pytest.mark.unit
@pytest.mark.asyncio
async def test_feed_fetch_failure_limits_sweep_to_images():
    # get_object_text -> None means feeds unreadable; only images/ is swept.
    objs = [{"key": "video/orphan.mp4", "size": 999, "last_modified": _OLD}]
    pool = _pool([{"content": "text"}], [])
    r2 = _fake_r2(objs)
    r2.get_object_text = AsyncMock(return_value=None)
    with patch(_R2, MagicMock(return_value=r2)):
        # Result unused — the assertions below are about which prefixes were
        # swept (list_objects/delete_object), not the JobResult payload.
        await MediaOrphanSweepJob().run(pool, _cfg(media_orphan_sweep_armed="true"))
    # video/ was not swept, so the orphan under it is untouched.
    r2.list_objects.assert_awaited_once_with("images/")
    r2.delete_object.assert_not_awaited()
