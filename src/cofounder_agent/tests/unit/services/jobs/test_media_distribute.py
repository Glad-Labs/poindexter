"""Unit tests for MediaDistributeJob — the Stage-2 link + Gate-2-seed pass.

The media_pipeline persists task-keyed ``media_assets`` rows (video /
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
from unittest.mock import AsyncMock, Mock, patch

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
    asserted); ``transaction`` yields a no-op async context.

    ``fetchval`` serves the single-flight dispatch guard
    (``claim_media_dispatch``): the advisory-lock acquire returns True (lock
    granted), the still-undispatched re-check returns 1 (row still eligible), and
    the unlock returns True — so every dispatch test proceeds exactly as it did
    before the guard existed. The concurrency-regression test below drives a
    stateful pool instead, so it can model contention."""

    def __init__(self):
        self.execute = AsyncMock(return_value="OK")

        async def _fetchval(sql, *args):
            if "pg_try_advisory_lock" in sql:
                return True
            if "pg_advisory_unlock" in sql:
                return True
            # _STILL_UNDISPATCHED_SQL — eligible in the single-asset tests.
            return 1

        self.fetchval = AsyncMock(side_effect=_fetchval)

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

    def __init__(self, unlinked=None, approved=None, post_id="p1", existing_video=None):
        self.fetch = AsyncMock(side_effect=[list(unlinked or []), list(approved or [])])
        self._post_id = post_id
        self._existing_video = existing_video

        async def _fetchval(sql, *args):
            # _RESOLVE_POST_SQL resolves the post id; _EXISTING_VIDEO_SQL is the
            # link-time guard checking for a pre-existing video-family asset.
            if "pipeline_task_id" in sql:
                return self._post_id
            return self._existing_video

        self.fetchval = AsyncMock(side_effect=_fetchval)
        self.execute = AsyncMock(return_value="UPDATE 1")
        self.conn = _FakeConn()

    def acquire(self):
        return _FakeAcquire(self.conn)


@pytest.mark.asyncio
async def test_dormant_when_flag_off():
    job = MediaDistributeJob()
    pool = _FakePool([{"id": "a1", "task_id": "t", "type": "video"}])
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
            {"id": "a-long", "task_id": "abc", "type": "video"},
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


def test_type_to_medium_is_identity():
    assert md._TYPE_TO_MEDIUM == {"video": "video", "video_short": "video_short"}


@pytest.mark.asyncio
async def test_seed_threads_storage_path_to_quality_eval():
    """The unlinked-asset row's storage_path reaches record_pending as
    file_path so the Layer-1 quality eval can probe it (poindexter#816);
    a missing path degrades to None, never ''."""
    job = MediaDistributeJob()
    pool = _FakePool(
        [
            {"id": "a1", "task_id": "abc", "type": "video",
             "storage_path": "/data/media/clip.mp4"},
            {"id": "a2", "task_id": "def", "type": "video_short",
             "storage_path": ""},
        ],
        post_id="post-1",
    )
    pending = AsyncMock(return_value="pending")
    with patch.object(md, "record_pending", pending):
        await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )
    paths = [c.kwargs.get("file_path") for c in pending.await_args_list]
    assert paths == ["/data/media/clip.mp4", None]
    # The SQL must actually select the column the row shape relies on.
    assert "storage_path" in md._UNLINKED_SQL


@pytest.mark.asyncio
async def test_link_skips_when_post_already_has_video_asset():
    """A second task-keyed render for a post that already has a video asset must
    NOT be linked — it should be self-pruned (DELETE) and a finding emitted so
    the next cycle doesn't rediscover the orphan and re-alert."""
    job = MediaDistributeJob()
    pool = _FakePool(
        [{"id": "a-dup", "task_id": "abc", "type": "video"}],
        post_id="post-1",
        existing_video=1,  # _EXISTING_VIDEO_SQL → truthy: post already has a video asset
    )
    findings = []
    pending = AsyncMock(return_value="pending")
    with patch.object(md, "record_pending", pending), patch.object(
        md, "emit_finding", Mock(side_effect=lambda **kw: findings.append(kw))
    ):
        out = await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )
    pending.assert_not_called()  # no second Gate-2 row seeded
    # The orphan row is pruned via pool.execute(_PRUNE_ORPHAN_SQL, asset_id);
    # no back-stamp execute should follow it.
    pool.execute.assert_awaited_once()
    prune_sql = pool.execute.call_args.args[0]
    assert "DELETE FROM media_assets" in prune_sql
    assert "post_id IS NULL" in prune_sql
    assert out.changes_made == 0
    assert findings and findings[0]["kind"] == "duplicate_video_asset"


@pytest.mark.asyncio
async def test_link_orphan_prune_failure_is_best_effort():
    """If the orphan DELETE raises, the job still completes ok and the finding
    is still emitted — a failed prune is non-fatal."""
    job = MediaDistributeJob()
    pool = _FakePool(
        [{"id": "a-dup", "task_id": "abc", "type": "video"}],
        post_id="post-1",
        existing_video=1,
    )
    pool.execute = AsyncMock(side_effect=RuntimeError("db error"))
    findings = []
    pending = AsyncMock(return_value="pending")
    with patch.object(md, "record_pending", pending), patch.object(
        md, "emit_finding", Mock(side_effect=lambda **kw: findings.append(kw))
    ):
        out = await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )
    assert out.ok
    assert out.changes_made == 0
    assert findings and findings[0]["kind"] == "duplicate_video_asset"


@pytest.mark.asyncio
async def test_skips_asset_with_no_published_post():
    """No post resolves from the task seam yet (not published) → leave the asset
    unlinked, seed nothing, try again next cycle."""
    job = MediaDistributeJob()
    pool = _FakePool(
        [{"id": "a1", "task_id": "orphan", "type": "video"}], post_id=None
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
            {"id": "a1", "task_id": "abc", "type": "video"},
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
async def test_long_form_dispatch_rebuilds_the_video_rss_feed(tmp_path):
    """Delivering a long-form video must refresh video/feed.xml.

    podcast_distribute has always rebuilt its feed after a delivery; this lane
    did not, so a delivered video only reached the public feed if an unrelated
    later event happened to rebuild it — the video half of the 2026-07-18
    stale-feed class.
    """
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    job = MediaDistributeJob()
    pool = _FakePool(
        unlinked=[],
        approved=[{
            "post_id": "p1", "title": "T", "content": "c", "excerpt": "e",
            "seo_keywords": "a,b", "slug": "s", "storage_path": str(f),
            "medium": "video",
        }],
    )
    disp = AsyncMock(return_value=[
        md._PlatformDispatchResult(
            platform="youtube", success=True, external_id="vid", url="u"
        )
    ])
    rebuild = AsyncMock()
    with patch.object(md, "_dispatch_asset", disp), \
            patch.object(md, "record_dispatched", AsyncMock()), \
            patch("services.media_feed_rebuild.rebuild_video_feed", rebuild):
        await job.run(pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")})
    rebuild.assert_awaited_once()


@pytest.mark.asyncio
async def test_shorts_only_dispatch_does_not_rebuild_the_video_feed(tmp_path):
    """Shorts go to YouTube Shorts and have no RSS surface — rebuilding on a
    shorts-only cycle would be a pointless R2 write every 10 minutes."""
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    job = MediaDistributeJob()
    pool = _FakePool(
        unlinked=[],
        approved=[{
            "post_id": "p1", "title": "T", "content": "c", "excerpt": "e",
            "seo_keywords": "a,b", "slug": "s", "storage_path": str(f),
            "medium": "video_short",
        }],
    )
    disp = AsyncMock(return_value=[
        md._PlatformDispatchResult(
            platform="youtube", success=True, external_id="vid", url="u"
        )
    ])
    rebuild = AsyncMock()
    with patch.object(md, "_dispatch_asset", disp), \
            patch.object(md, "record_dispatched", AsyncMock()), \
            patch("services.media_feed_rebuild.rebuild_video_feed", rebuild):
        await job.run(pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")})
    rebuild.assert_not_awaited()


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


def test_approved_undispatched_sql_excludes_grandfather():
    """The asset-pass selector must exclude grandfathered media_approvals.

    Same conflation as glad-labs-stack#1596: ``approved AND dispatched_at IS
    NULL`` reads as "deliver now", but grandfather rows are already-live media
    that must never be queued for upload. NULL-safe via COALESCE so operator
    rows with a NULL ``decided_by`` still dispatch.
    """
    sql = md._APPROVED_UNDISPATCHED_SQL
    assert "ma.dispatched_at IS NULL" in sql  # still gates on never-delivered
    assert "COALESCE(ma.decided_by, '') NOT LIKE '%grandfather%'" in sql


# ---------------------------------------------------------------------------
# Single-flight dispatch guard — two overlapping passes upload once (#3370 twin)
# ---------------------------------------------------------------------------


class _RaceConn:
    """Shared conn modelling pg_try_advisory_lock over process-wide state.

    One instance is shared across every ``pool.acquire()`` so the lock set is
    process-wide, exactly like a real advisory lock (mirrors the social_drafts
    ``_LockConn`` in test_social_drafts.py). ``fetchval`` routes on the SQL: the
    lock/unlock calls mutate the shared ``held`` set (True on first acquire of a
    key, False while held); the still-undispatched re-check reads the shared
    ``dispatched`` set (None once a pass stamped it, else 1). ``execute`` is a
    no-op stand-in for the persist writes."""

    def __init__(self, held: set, dispatched: set):
        self._held = held
        self._dispatched = dispatched
        self.execute = AsyncMock(return_value="OK")

    def transaction(self):
        return _FakeTxn()

    async def fetchval(self, sql, *args):
        if "pg_try_advisory_lock" in sql:
            key = tuple(args)
            if key in self._held:
                return False
            self._held.add(key)
            return True
        if "pg_advisory_unlock" in sql:
            self._held.discard(tuple(args))
            return True
        # _STILL_UNDISPATCHED_SQL — (post_id, medium) already stamped?
        return None if (args[0], args[1]) in self._dispatched else 1


class _RacePool:
    """asyncpg-pool stand-in for the concurrency test. ``fetch`` routes on the
    SQL and returns the SAME (non-consumed) approved batch to both racing passes;
    ``acquire`` always yields the one shared ``_RaceConn`` so the lock is
    process-wide."""

    def __init__(self, approved):
        self._approved = approved
        self.held: set = set()
        self.dispatched: set = set()
        self._conn = _RaceConn(self.held, self.dispatched)

    async def fetch(self, sql, *args):
        if "post_id IS NULL" in sql:
            return []  # link pass: nothing unlinked
        return [dict(r) for r in self._approved]  # dispatch pass: both see it

    def acquire(self):
        return _FakeAcquire(self._conn)


def _approved_video_row(path):
    return {
        "post_id": "p1", "medium": "video", "title": "T", "content": "c",
        "excerpt": "e", "seo_keywords": "", "slug": "s",
        "asset_id": "a1", "task_id": "t1", "storage_path": str(path),
    }


@pytest.mark.asyncio
async def test_concurrent_dispatch_passes_upload_once(tmp_path):
    """Two overlapping dispatch passes that both SELECT the same
    approved+undispatched asset must upload it exactly ONCE.

    These jobs are ``idempotent=True`` → apscheduler ``max_instances=3``, so a
    cycle running past its 10-min interval overlaps itself (and the multi-worker
    future overlaps across processes). Without the per-(post,medium) advisory
    lock both passes clear the batch read and both call ``_dispatch_asset`` — a
    duplicate YouTube video (the media twin of the social double-post fixed in
    glad-labs-stack#3370)."""
    import asyncio

    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    pool = _RacePool([_approved_video_row(f)])

    async def _slow_upload(*a, **k):
        # Hold the lock across a real suspension so the racing pass reaches its
        # pg_try_advisory_lock and gets False while this one is mid-upload.
        await asyncio.sleep(0.05)
        return [md._PlatformDispatchResult(
            platform="youtube", success=True, external_id="vid", url="u"
        )]

    async def _mark_dispatched(conn, post_id, medium, *, success):
        if success:
            pool.dispatched.add((post_id, medium))

    disp = AsyncMock(side_effect=_slow_upload)
    with patch.object(md, "_dispatch_asset", disp), \
            patch.object(md, "record_dispatched", AsyncMock(side_effect=_mark_dispatched)), \
            patch("services.media_feed_rebuild.rebuild_video_feed", AsyncMock()):
        job = MediaDistributeJob()
        cfg = {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        await asyncio.gather(job.run(pool, cfg), job.run(pool, cfg))

    assert disp.await_count == 1, "asset uploaded more than once under concurrency"


@pytest.mark.asyncio
async def test_concurrent_dispatch_double_uploads_without_the_guard(tmp_path):
    """Mutation check: neuter the single-flight guard (always proceed, no lock)
    and the SAME two concurrent passes upload the asset TWICE — proving the lock
    in the test above is what holds it to one, not some incidental
    serialization in the fake."""
    import asyncio
    import contextlib

    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    pool = _RacePool([_approved_video_row(f)])

    async def _slow_upload(*a, **k):
        await asyncio.sleep(0.05)
        return [md._PlatformDispatchResult(
            platform="youtube", success=True, external_id="vid", url="u"
        )]

    @contextlib.asynccontextmanager
    async def _no_guard(pool, *, post_id, medium):
        yield True  # neutered: always proceed, no lock, no re-check

    disp = AsyncMock(side_effect=_slow_upload)
    with patch.object(md, "_dispatch_asset", disp), \
            patch.object(md, "claim_media_dispatch", _no_guard), \
            patch.object(md, "record_dispatched", AsyncMock()), \
            patch("services.media_feed_rebuild.rebuild_video_feed", AsyncMock()):
        job = MediaDistributeJob()
        cfg = {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        await asyncio.gather(job.run(pool, cfg), job.run(pool, cfg))

    assert disp.await_count == 2, "neutered guard should let both passes upload"
