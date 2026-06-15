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

import json
from unittest.mock import AsyncMock, patch

import pytest

from services.jobs import media_distribute as md
from services.jobs.media_distribute import MediaDistributeJob
from services.site_config import SiteConfig


def _sc(**overrides):
    base = {"media_pipeline_trigger_enabled": "false"}
    base.update(overrides)
    return SiteConfig(initial_config=base)


class _FakeTxn:
    """``conn.transaction()`` async-context stand-in (no-op begin/commit)."""

    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    """Pooled-connection stand-in. ``execute`` records calls (so the persist
    pass's platform_video_ids merge + pipeline_distributions insert can be
    asserted); ``transaction`` yields a no-op async context."""

    def __init__(self):
        self.execute = AsyncMock(return_value="OK")

    def transaction(self):
        return _FakeTxn()


class _FakeAcquire:
    """``pool.acquire()`` async-context stand-in yielding a single conn."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    """asyncpg-pool stand-in. ``run()`` issues two fetches per active cycle —
    the link-pass unlinked-asset query, then the dispatch-pass
    approved-undispatched query — so ``fetch`` is a 2-element side_effect.
    ``fetchval`` resolves the post id; ``execute`` returns a command tag.
    ``acquire()`` yields a shared ``conn`` so the transactional persist pass
    (``_persist_dispatch_result``) is observable via ``pool.conn.execute``."""

    def __init__(self, unlinked=None, approved=None, post_id="p1"):
        self.fetch = AsyncMock(side_effect=[list(unlinked or []), list(approved or [])])
        self.fetchval = AsyncMock(return_value=post_id)
        self.execute = AsyncMock(return_value="UPDATE 1")
        self.conn = _FakeConn()

    def acquire(self):
        return _FakeAcquire(self.conn)


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
    # _dispatch_asset now returns a list of per-platform results (not a bool).
    disp = AsyncMock(
        return_value=[
            md._PlatformDispatchResult(
                platform="youtube", success=True, external_id="vid", url="u"
            )
        ]
    )
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
async def test_dispatch_asset_threads_back_external_id_and_url():
    """_dispatch_asset builds the publishing payload (with the shorts flag),
    fires the registered handler for each enabled video adapter, and threads
    the handler's external id (returned under the ``post_id`` key) + public url
    back to the caller as a per-platform result (it used to discard them and
    return a bare bool — the bug)."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[
        {"name": "yt", "platform": "youtube", "handler_name": "youtube",
         "config": {}, "metadata": {}},
    ])
    # The handler returns the external (YouTube) video id under "post_id"
    # and the watch URL under "url" — see publishing_youtube.youtube().
    dispatch = AsyncMock(return_value={
        "success": True, "post_id": "VID123", "url": "https://youtu.be/VID123",
    })
    row = {
        "post_id": "p1", "title": "Clip", "content": "c", "excerpt": "e",
        "seo_keywords": "", "slug": "s", "storage_path": "/tmp/v.mp4",
    }
    with patch("services.integrations.registry.dispatch", dispatch), patch(
        "services.integrations.handlers.load_all", lambda: None
    ):
        results = await md._dispatch_asset(
            pool, _sc(media_pipeline_trigger_enabled="true"), row, shorts=True
        )
    assert len(results) == 1
    r = results[0]
    assert r.success is True
    assert r.platform == "youtube"
    assert r.external_id == "VID123"
    assert r.url == "https://youtu.be/VID123"
    payload = dispatch.await_args.args[2]
    assert payload["shorts"] is True
    assert payload["media_path"] == "/tmp/v.mp4"
    assert payload["post_id"] == "p1"


@pytest.mark.asyncio
async def test_dispatch_asset_marks_failure_without_external_id():
    """A handler result with success=False yields a failed per-platform result
    carrying no external id (so the persist pass records the failed attempt but
    writes no distribution row)."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[
        {"name": "yt", "platform": "youtube", "handler_name": "youtube",
         "config": {}, "metadata": {}},
    ])
    dispatch = AsyncMock(return_value={"success": False, "error": "quota"})
    row = {
        "post_id": "p1", "title": "Clip", "content": "c", "excerpt": "e",
        "seo_keywords": "", "slug": "s", "storage_path": "/tmp/v.mp4",
    }
    with patch("services.integrations.registry.dispatch", dispatch), patch(
        "services.integrations.handlers.load_all", lambda: None
    ):
        results = await md._dispatch_asset(
            pool, _sc(media_pipeline_trigger_enabled="true"), row, shorts=False
        )
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].external_id is None


@pytest.mark.asyncio
async def test_persist_dispatch_result_records_id_and_url():
    """A successful youtube dispatch persists the external id + url:
    media_assets.platform_video_ids gets {"youtube": <id>} merged in (without
    clobbering other platforms) and a pipeline_distributions row is inserted —
    all in one transaction with the record_dispatched stamp."""
    pool = _FakePool()
    results = [md._PlatformDispatchResult(
        platform="youtube", success=True,
        external_id="VID123", url="https://youtu.be/VID123",
    )]
    rec = AsyncMock()
    with patch.object(md, "record_dispatched", rec):
        await md._persist_dispatch_result(
            pool, post_id="post-1", medium="video",
            asset_id="asset-1", task_id="task-1", results=results,
        )

    # The dispatch stamp was recorded as a success, on the acquired conn.
    rec.assert_awaited_once()
    assert rec.await_args.kwargs["success"] is True
    assert rec.await_args.args[0] is pool.conn   # same transactional conn
    assert rec.await_args.args[1] == "post-1"
    assert rec.await_args.args[2] == "video"

    calls = pool.conn.execute.await_args_list
    # media_assets.platform_video_ids merged with the youtube id (merge, not
    # clobber — the SQL uses the jsonb || concat operator).
    merge = next(c for c in calls if "platform_video_ids" in c.args[0])
    assert "||" in merge.args[0]
    assert merge.args[1] == "asset-1"
    assert json.loads(merge.args[2]) == {"youtube": "VID123"}

    # pipeline_distributions row: task_id, target, external_id, external_url,
    # post_id (status 'published' is literal in the SQL).
    dist = next(c for c in calls if "pipeline_distributions" in c.args[0])
    assert dist.args[1] == "task-1"
    assert dist.args[2] == "youtube"
    assert dist.args[3] == "VID123"
    assert dist.args[4] == "https://youtu.be/VID123"
    assert dist.args[5] == "post-1"


@pytest.mark.asyncio
async def test_persist_dispatch_result_failure_writes_no_distribution():
    """A failed dispatch stamps record_dispatched(success=False) but writes no
    platform_video_ids merge and no pipeline_distributions row."""
    pool = _FakePool()
    results = [md._PlatformDispatchResult(platform="youtube", success=False)]
    rec = AsyncMock()
    with patch.object(md, "record_dispatched", rec):
        await md._persist_dispatch_result(
            pool, post_id="post-1", medium="video",
            asset_id="asset-1", task_id="task-1", results=results,
        )
    rec.assert_awaited_once()
    assert rec.await_args.kwargs["success"] is False
    pool.conn.execute.assert_not_awaited()  # no observability writes on failure


@pytest.mark.asyncio
async def test_run_persists_distribution_for_successful_dispatch(tmp_path):
    """End-to-end: run() threads the asset_id + task_id off the approved row
    into the persist pass, so a successful dispatch lands the platform_video_ids
    merge + pipeline_distributions row (the seam the bug dropped on the floor)."""
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    job = MediaDistributeJob()
    row = {
        "post_id": "p1", "medium": "video", "title": "T", "content": "c",
        "excerpt": "e", "seo_keywords": "", "slug": "s",
        "asset_id": "asset-9", "task_id": "task-9", "storage_path": str(f),
    }
    pool = _FakePool(unlinked=[], approved=[row])
    disp = AsyncMock(return_value=[md._PlatformDispatchResult(
        platform="youtube", success=True,
        external_id="VID9", url="https://youtu.be/VID9",
    )])
    with patch.object(md, "_dispatch_asset", disp):
        out = await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )
    assert out.changes_made == 1
    calls = pool.conn.execute.await_args_list
    merge = next(c for c in calls if "platform_video_ids" in c.args[0])
    assert merge.args[1] == "asset-9"
    assert json.loads(merge.args[2]) == {"youtube": "VID9"}
    dist = next(c for c in calls if "pipeline_distributions" in c.args[0])
    assert dist.args[1] == "task-9"
    assert dist.args[3] == "VID9"


def test_job_protocol_shape():
    job = MediaDistributeJob()
    assert job.name == "media_distribute"
    assert isinstance(job.schedule, str)
    assert job.idempotent is True
