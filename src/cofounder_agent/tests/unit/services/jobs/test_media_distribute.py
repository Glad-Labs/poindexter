"""Unit tests for MediaDistributeJob — the Stage-2 link + Gate-2-seed pass.

The media_pipeline persists task-keyed ``media_assets`` rows (video_long /
video_short) with ``post_id=NULL`` (the post may not exist at render time). This
job is the bridge to the post-keyed Gate-2 world: once the post is published
(resolvable via ``posts.metadata->>'pipeline_task_id'``), it back-stamps
``post_id`` onto the asset row and seeds a ``media_approvals`` pending row so the
asset surfaces in the operator's Gate-2 queue (``video`` for the long form,
``video_short`` for the short).

Default-OFF: gated on ``media_pipeline_trigger_enabled`` (the Stage-2 master
switch), so it's scheduled but dormant in prod until the operator opts in.
"""

from unittest.mock import AsyncMock, patch

import pytest

from services.jobs import media_distribute as md
from services.jobs.media_distribute import MediaDistributeJob
from services.site_config import SiteConfig


def _sc(**overrides):
    base = {"media_pipeline_trigger_enabled": "false"}
    base.update(overrides)
    return SiteConfig(initial_config=base)


class _FakePool:
    """asyncpg-pool stand-in. ``run()`` issues two fetches per active cycle —
    the link-pass unlinked-asset query, then the dispatch-pass
    approved-undispatched query — so ``fetch`` is a 2-element side_effect.
    ``fetchval`` resolves the post id; ``execute`` returns a command tag."""

    def __init__(self, unlinked=None, approved=None, post_id="p1"):
        self.fetch = AsyncMock(side_effect=[list(unlinked or []), list(approved or [])])
        self.fetchval = AsyncMock(return_value=post_id)
        self.execute = AsyncMock(return_value="UPDATE 1")


@pytest.mark.asyncio
async def test_dormant_when_flag_off():
    job = MediaDistributeJob()
    pool = _FakePool([{"id": "a1", "task_id": "t", "type": "video_long"}])
    out = await job.run(pool, {"_site_config": _sc()})
    assert out.ok
    assert out.changes_made == 0
    pool.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_no_site_config_skips():
    job = MediaDistributeJob()
    out = await job.run(_FakePool([]), {})
    assert out.ok
    assert out.changes_made == 0


@pytest.mark.asyncio
async def test_no_pool_skips():
    job = MediaDistributeJob()
    out = await job.run(None, {"_site_config": _sc(media_pipeline_trigger_enabled="true")})
    assert out.ok
    assert out.changes_made == 0


@pytest.mark.asyncio
async def test_links_assets_and_seeds_gate2_approvals():
    """Flag on + two unlinked assets whose task has a published post → back-stamp
    post_id and seed the right media_approvals medium per flavor."""
    job = MediaDistributeJob()
    pool = _FakePool(
        [
            {"id": "a-long", "task_id": "abc", "type": "video_long"},
            {"id": "a-short", "task_id": "abc", "type": "video_short"},
        ],
        post_id="post-1",
    )
    pending = AsyncMock(return_value="pending")
    with patch.object(md, "record_pending", pending):
        out = await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )
    assert out.changes_made == 2
    # Each asset got its post_id back-stamped (one execute per asset).
    assert pool.execute.await_count == 2
    # Gate-2 rows seeded with the flavor-correct medium.
    media_args = {c.args[2] for c in pending.await_args_list}
    assert media_args == {"video", "video_short"}
    for c in pending.await_args_list:
        assert c.args[1] == "post-1"


@pytest.mark.asyncio
async def test_skips_asset_with_no_published_post():
    """No post resolves from the task seam yet (not published) → leave the asset
    unlinked, seed nothing, try again next cycle."""
    job = MediaDistributeJob()
    pool = _FakePool(
        [{"id": "a1", "task_id": "orphan", "type": "video_long"}], post_id=None
    )
    pending = AsyncMock()
    with patch.object(md, "record_pending", pending):
        out = await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )
    assert out.changes_made == 0
    pool.execute.assert_not_called()  # no back-stamp without a post
    pending.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_failure_is_best_effort():
    """A record_pending failure for one asset never halts the pass."""
    job = MediaDistributeJob()
    pool = _FakePool(
        [
            {"id": "a1", "task_id": "abc", "type": "video_long"},
            {"id": "a2", "task_id": "def", "type": "video_short"},
        ],
        post_id="post-1",
    )
    pending = AsyncMock(side_effect=[RuntimeError("boom"), "pending"])
    with patch.object(md, "record_pending", pending):
        out = await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )
    assert out.ok  # best-effort
    assert out.changes_made == 1  # only the second linked


@pytest.mark.asyncio
async def test_dispatches_approved_assets_with_correct_shorts_flag(tmp_path):
    """Approved long + short assets (file present) → dispatch each via the
    Shorts-aware handler (long shorts=False, short shorts=True) and stamp
    record_dispatched per flavor."""
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    job = MediaDistributeJob()
    base = {
        "post_id": "p1", "title": "T", "content": "c", "excerpt": "e",
        "seo_keywords": "a,b", "slug": "s", "storage_path": str(f),
    }
    pool = _FakePool(
        unlinked=[],
        approved=[
            {**base, "medium": "video"},
            {**base, "medium": "video_short"},
        ],
    )
    disp = AsyncMock(return_value=True)
    rec = AsyncMock()
    with patch.object(md, "_dispatch_asset", disp), patch.object(md, "record_dispatched", rec):
        out = await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )
    assert out.changes_made == 2
    assert {c.kwargs["shorts"] for c in disp.await_args_list} == {False, True}
    assert {c.args[2] for c in rec.await_args_list} == {"video", "video_short"}


@pytest.mark.asyncio
async def test_dispatch_skips_and_does_not_stamp_missing_file(tmp_path):
    """An approved asset whose durable file is gone → don't dispatch and don't
    stamp dispatched (leave it for the reconciliation watchdog)."""
    job = MediaDistributeJob()
    pool = _FakePool(
        unlinked=[],
        approved=[{
            "post_id": "p1", "medium": "video", "title": "T", "content": "c",
            "excerpt": "e", "seo_keywords": "", "slug": "s",
            "storage_path": str(tmp_path / "gone.mp4"),
        }],
    )
    disp = AsyncMock(return_value=True)
    rec = AsyncMock()
    with patch.object(md, "_dispatch_asset", disp), patch.object(md, "record_dispatched", rec):
        out = await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )
    assert out.changes_made == 0
    disp.assert_not_awaited()
    rec.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_asset_fires_handler_with_shorts_in_payload():
    """_dispatch_asset builds the publishing payload (with the shorts flag) and
    fires the registered handler for each enabled video adapter."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[
        {"name": "yt", "platform": "youtube", "handler_name": "youtube",
         "config": {}, "metadata": {}},
    ])
    dispatch = AsyncMock(return_value={"success": True})
    row = {
        "post_id": "p1", "title": "Clip", "content": "c", "excerpt": "e",
        "seo_keywords": "", "slug": "s", "storage_path": "/tmp/v.mp4",
    }
    with patch("services.integrations.registry.dispatch", dispatch), patch(
        "services.integrations.handlers.load_all", lambda: None
    ):
        ok = await md._dispatch_asset(
            pool, _sc(media_pipeline_trigger_enabled="true"), row, shorts=True
        )
    assert ok is True
    payload = dispatch.await_args.args[2]
    assert payload["shorts"] is True
    assert payload["media_path"] == "/tmp/v.mp4"
    assert payload["post_id"] == "p1"


def test_job_protocol_shape():
    job = MediaDistributeJob()
    assert job.name == "media_distribute"
    assert isinstance(job.schedule, str)
    assert job.idempotent is True
