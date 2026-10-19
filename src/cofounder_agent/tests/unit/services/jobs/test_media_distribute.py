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
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from poindexter.services.jobs import media_distribute as md
from poindexter.services.jobs.media_distribute import MediaDistributeJob
from poindexter.services.site_config import SiteConfig


def _sc(**overrides):
    base = {"media_pipeline_trigger_enabled": "false"}
    base.update(overrides)
    return SiteConfig(initial_config=base)


@pytest.fixture(autouse=True)
def _no_live_video_feed_render(monkeypatch):
    """Stub the feed rebuild a long-form delivery triggers.

    ``run()`` calls ``rebuild_video_feed`` after it dispatches a long-form
    video, and that GETs ``{internal_api_base_url}/api/video/feed.xml``: the
    live worker API on :8002 when the suite runs on the operator box. Three
    tests here fetched it before this stub existed (poindexter#1011). Tests
    that assert on the rebuild still patch it themselves; their patch wins.
    """
    monkeypatch.setattr(
        "poindexter.services.media_feed_rebuild.rebuild_video_feed", AsyncMock(),
    )


@pytest.fixture(autouse=True)
def _no_upload_slot(monkeypatch):
    """Publish immediately unless a test picks a slot (``md.next_upload_slot``)."""
    monkeypatch.setattr(md, "next_upload_slot", AsyncMock(return_value=None))
    monkeypatch.setattr(md, "record_slot", AsyncMock())


@pytest.fixture(autouse=True)
def _no_live_captioning(monkeypatch):
    """Stub the caption step a successful YouTube upload runs.

    ``caption_after_upload`` runs ffmpeg on the video and calls the caption
    engine; a unit test must do neither. The tests that assert on it read
    this stub (``md.caption_after_upload``).
    """
    monkeypatch.setattr(md, "caption_after_upload", AsyncMock(return_value="set"))


@pytest.fixture(autouse=True)
def _no_live_playlists(monkeypatch):
    """Stub the playlist step a successful YouTube upload runs (it calls Google).
    The tests that assert on it read this stub (``md.playlists_after_upload``)."""
    monkeypatch.setattr(md, "playlists_after_upload", AsyncMock(return_value="ok"))


@pytest.fixture(autouse=True)
def _no_live_chapters(monkeypatch):
    """Stub chapter-making (it transcribes and calls the model) before a long upload."""
    monkeypatch.setattr(md, "chapters_for_upload", AsyncMock(return_value=""))


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
async def test_standalone_asset_is_seeded_under_its_task_without_a_post():
    """A standalone video (poindexter#1140) never gets a post: it is seeded for
    review under its task straight away, and no post lookup or back-stamp runs."""
    job = MediaDistributeJob()
    pool = _FakePool(
        [{"id": "a1", "task_id": "cartoon-task", "type": "video",
          "storage_path": "/v/cartoon.mp4", "standalone": True}],
        post_id=None,
    )
    pending = AsyncMock()
    standalone = AsyncMock(return_value="pending")
    with patch.object(md, "record_pending", pending), patch.object(
        md, "record_pending_standalone", standalone,
    ):
        out = await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )
    assert out.changes_made == 1
    standalone.assert_awaited_once()
    assert standalone.await_args.args[1:] == ("cartoon-task", "video")
    assert standalone.await_args.kwargs["file_path"] == "/v/cartoon.mp4"
    pending.assert_not_awaited()
    pool.fetchval.assert_not_awaited()  # no post resolve for a standalone video
    pool.execute.assert_not_called()


def test_unlinked_sql_drops_standalone_assets_once_seeded():
    """Without this a seeded standalone video would hold a LIMIT slot forever."""
    sql = " ".join(md._UNLINKED_SQL.split())
    assert "metadata->>'standalone'" in sql
    assert "NOT EXISTS ( SELECT 1 FROM media_approvals" in sql


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
async def test_both_renders_of_one_task_land_two_distribution_rows(tmp_path):
    """The bug this whole change exists to close.

    One task ships a long-form video AND a Short to the same target under the
    same task_id. Under the old ``UNIQUE (task_id, target)`` key the second
    upsert overwrote the first, so one YouTube handle was silently lost from
    ``pipeline_distributions`` — five of twelve prod rows — and the orphaned
    Short became invisible to ``youtube_metadata_sync``. Both renders must now
    write their own row, distinguished by medium.
    """
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    job = MediaDistributeJob()
    base = {
        "post_id": "p1", "title": "T", "content": "c", "excerpt": "e",
        "seo_keywords": "", "slug": "s", "storage_path": str(f),
        "asset_id": "asset-1", "task_id": "task-1",
    }
    pool = _FakePool(
        unlinked=[],
        approved=[
            {**base, "medium": "video", "asset_id": "asset-long"},
            {**base, "medium": "video_short", "asset_id": "asset-short"},
        ],
    )

    async def _dispatch(_pool, _sc_, row, *, shorts):
        vid = "SHORT" if shorts else "LONG"
        return [md._PlatformDispatchResult(
            platform="youtube", success=True, external_id=vid,
            url=f"https://www.youtube.com/watch?v={vid}",
        )]

    with patch.object(md, "_dispatch_asset", _dispatch):
        out = await job.run(
            pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        )

    assert out.changes_made == 2
    dists = [
        c for c in pool.conn.execute.await_args_list
        if "pipeline_distributions" in c.args[0]
    ]
    # Two rows, same (task, target), different medium + handle.
    assert [(d.args[1], d.args[2], d.args[3], d.args[4]) for d in dists] == [
        ("task-1", "youtube", "video", "LONG"),
        ("task-1", "youtube", "video_short", "SHORT"),
    ]


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
            patch("poindexter.services.media_feed_rebuild.rebuild_video_feed", rebuild):
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
            patch("poindexter.services.media_feed_rebuild.rebuild_video_feed", rebuild):
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
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
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
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        results = await md._dispatch_asset(
            pool, _sc(media_pipeline_trigger_enabled="true"), row, shorts=False
        )
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].external_id is None


def _adapter_run_calls(pool):
    return [
        c for c in pool.execute.await_args_list
        if "UPDATE publishing_adapters" in c.args[0]
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dispatch", "ok", "error_part"),
    [
        (AsyncMock(return_value={"success": True, "post_id": "V", "url": "u"}), True, None),
        (AsyncMock(return_value={"success": False, "error": "quota"}), False, "quota"),
        (AsyncMock(side_effect=RuntimeError("boom")), False, "boom"),
    ],
)
async def test_every_dispatch_attempt_is_recorded_on_its_adapter_row(dispatch, ok, error_part):
    """poindexter#1067: nothing wrote publishing_adapters' run counters, so
    youtube_main read "3 runs, 3 failures" (June misroutes) for three months
    while uploading every approved video. Success, handler failure and a
    raising handler each stamp one run on the row they went through."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[
        {"name": "youtube_main", "platform": "youtube", "handler_name": "youtube",
         "config": {}, "metadata": {}},
    ])
    row = {
        "post_id": "p1", "title": "Clip", "content": "c", "excerpt": "e",
        "seo_keywords": "", "slug": "s", "storage_path": "/tmp/v.mp4",
    }
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(
            pool, _sc(media_pipeline_trigger_enabled="true"), row, shorts=False
        )
    calls = _adapter_run_calls(pool)
    assert len(calls) == 1
    _sql, name, status, duration_ms, error, failed = calls[0].args
    assert name == "youtube_main"
    assert status == ("success" if ok else "failed")
    assert failed == (0 if ok else 1)
    assert isinstance(duration_ms, int) and duration_ms >= 0
    if error_part:
        assert error_part in error
    else:
        assert error is None


@pytest.mark.asyncio
async def test_a_failed_run_record_never_fails_the_upload():
    from poindexter.services.publishing_adapters_db import record_adapter_run

    pool = AsyncMock()
    pool.execute = AsyncMock(side_effect=RuntimeError("db down"))
    await record_adapter_run(pool, "youtube_main", ok=True, duration_ms=5)  # no raise


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

    # pipeline_distributions row: task_id, target, medium, external_id,
    # external_url, post_id (status 'published' is literal in the SQL). The
    # medium is in the row's unique key, so it has to be threaded from the
    # dispatch — a long form and a Short of one post are two rows.
    dist = next(c for c in calls if "pipeline_distributions" in c.args[0])
    assert dist.args[1] == "task-1"
    assert dist.args[2] == "youtube"
    assert dist.args[3] == "video"
    assert dist.args[4] == "VID123"
    assert dist.args[5] == "https://youtu.be/VID123"
    assert dist.args[6] == "post-1"


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
    assert dist.args[3] == "video"
    assert dist.args[4] == "VID9"


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
        if "dispatched_at IS NULL" not in sql:
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
            patch("poindexter.services.media_feed_rebuild.rebuild_video_feed", AsyncMock()):
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
            patch("poindexter.services.media_feed_rebuild.rebuild_video_feed", AsyncMock()):
        job = MediaDistributeJob()
        cfg = {"_site_config": _sc(media_pipeline_trigger_enabled="true")}
        await asyncio.gather(job.run(pool, cfg), job.run(pool, cfg))

    assert disp.await_count == 2, "neutered guard should let both passes upload"


# ---------------------------------------------------------------------------
# synthetic-media disclosure derivation
# ---------------------------------------------------------------------------

def _presenter_row(**over):
    row = {
        "post_id": "p1", "title": "Clip", "content": "c", "excerpt": "e", "seo_keywords": "",
        "slug": "s", "storage_path": "/tmp/v.mp4", "niche_slug": "glad-labs",
        "video_shot_list": {"shots": [{"source": "pexels"}, {"source": "presenter"}]},
    }
    row.update(over)
    return row


def _persona_sc(**extra):
    base = {
        "media_default_persona": "presenter",
        "persona.presenter.display_name": "Ada",
        "persona.presenter.portrait_url": "https://cdn/p.png",
        "persona.presenter.style_policy": "photoreal",
        "persona.presenter.enabled": "true",
        "media_human_subjects": "allow",
        "media_style_policy": "any",
    }
    base.update(extra)
    return _sc(**base)


def test_disclosure_auto_derives_from_presenter_shot_and_photoreal_persona():
    assert md._contains_synthetic_media(_presenter_row(), _persona_sc()) is True
    assert md._contains_synthetic_media(_presenter_row(video_shot_list={"shots": [{"source": "pexels"}]}), _persona_sc()) is False
    assert md._contains_synthetic_media(_presenter_row(video_shot_list=None), _persona_sc()) is False


def test_disclosure_accepts_a_json_string_shot_list():
    import json
    row = _presenter_row(video_shot_list=json.dumps({"shots": [{"source": "presenter"}]}))
    assert md._contains_synthetic_media(row, _persona_sc()) is True


def test_disclosure_setting_forces_the_answer():
    assert md._contains_synthetic_media(_presenter_row(video_shot_list=None), _persona_sc(youtube_contains_synthetic_media="true")) is True
    assert md._contains_synthetic_media(_presenter_row(), _persona_sc(youtube_contains_synthetic_media="false")) is False


@pytest.mark.asyncio
async def test_dispatch_payload_carries_the_disclosure():
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[
        {"name": "yt", "platform": "youtube", "handler_name": "youtube", "config": {}, "metadata": {}},
    ])
    dispatch = AsyncMock(return_value={"success": True, "post_id": "VID1", "url": "https://youtu.be/VID1"})
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(pool, _persona_sc(media_pipeline_trigger_enabled="true"), _presenter_row(), shorts=False)
    payload = dispatch.await_args.args[2]
    assert payload["contains_synthetic_media"] is True


# --------------------------------------------------------------------------
# The pair (2026-09-22): the Short's own hook + medium; the render that lands
# second gives its live twin the cross-link
# --------------------------------------------------------------------------


def _pair_pool(*, script=None, twin=None, adapters=None):
    """``fetch`` serves the adapter rows; ``fetchval`` routes on the SQL text."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=adapters if adapters is not None else [
        {"name": "yt", "platform": "youtube", "handler_name": "youtube", "config": {}, "metadata": {}},
    ])

    async def _fetchval(sql, *args):
        if "short_summary_script" in sql:
            return script
        if "pipeline_distributions" in sql:
            return twin
        return None
    pool.fetchval = AsyncMock(side_effect=_fetchval)
    return pool


@pytest.mark.asyncio
async def test_short_payload_uses_its_own_hook_medium_and_links_the_live_long_form():
    pool = _pair_pool(script="Nobody clicks anymore. Here is what to do.", twin="LONG1")
    dispatch = AsyncMock(return_value={"success": True, "post_id": "VID1", "url": "u"})
    row = {
        "post_id": "p1", "task_id": "t1", "title": "Nobody Clicks Anymore: Building Content",
        "content": "c", "excerpt": "The excerpt.", "seo_keywords": "zeroclick content",
        "slug": "s", "storage_path": "/tmp/v.mp4",
    }
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(pool, _sc(media_pipeline_trigger_enabled="true", site_url="https://www.gladlabs.io"), row, shorts=True)
    payload = dispatch.await_args.args[2]
    assert payload["title"] == "Nobody clicks anymore #Shorts"
    assert payload["description"].startswith("Nobody clicks anymore. Here is what to do.")
    assert "Watch the full breakdown: https://www.youtube.com/watch?v=LONG1" in payload["description"]
    assert "utm_medium=shorts" in payload["description"]
    assert "#Shorts #ZeroclickContent" in payload["description"]


@pytest.mark.asyncio
async def test_long_payload_links_a_live_short_and_keeps_its_medium():
    pool = _pair_pool(twin="SHORT1")
    dispatch = AsyncMock(return_value={"success": True, "post_id": "VID1", "url": "u"})
    row = {"post_id": "p1", "task_id": "t1", "title": "T", "content": "c", "excerpt": "The excerpt.",
           "seo_keywords": "", "slug": "s", "storage_path": "/tmp/v.mp4"}
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(pool, _sc(media_pipeline_trigger_enabled="true", site_url="https://www.gladlabs.io"), row, shorts=False)
    payload = dispatch.await_args.args[2]
    assert payload["title"] == "T"
    assert "Watch the Short: https://www.youtube.com/shorts/SHORT1" in payload["description"]
    assert "utm_medium=video" in payload["description"]
    # The short-script lookup is a Short-only cost.
    assert not any("short_summary_script" in c.args[0] for c in pool.fetchval.await_args_list)


@pytest.mark.asyncio
async def test_twin_refresh_recomposes_the_live_twin_through_sync_metadata(monkeypatch):
    """Second render lands → the first gets its cross-link via ONE update, through
    the same recompose path sync-metadata --apply uses."""
    pool = _pair_pool(twin="LONG1")
    calls = {}

    async def fake_sync(p, sc, *, selector=None, apply=False, limit=None):
        calls.update(selector=selector, apply=apply, limit=limit)
        return [SimpleNamespace(applied=True)]

    import poindexter.services.youtube_metadata_sync as sync_mod
    monkeypatch.setattr(sync_mod, "sync_youtube_metadata", fake_sync)
    applied = await md._refresh_twin_after_upload(pool, _sc(), task_id="t1", medium="video_short")
    assert applied is True
    assert calls == {"selector": "LONG1", "apply": True, "limit": 1}


@pytest.mark.asyncio
async def test_twin_refresh_is_skipped_without_a_live_twin_or_when_disabled(monkeypatch):
    import poindexter.services.youtube_metadata_sync as sync_mod
    fake_sync = AsyncMock(return_value=[])
    monkeypatch.setattr(sync_mod, "sync_youtube_metadata", fake_sync)

    assert await md._refresh_twin_after_upload(_pair_pool(twin=None), _sc(), task_id="t1", medium="video") is False
    assert await md._refresh_twin_after_upload(
        _pair_pool(twin="X"), _sc(youtube_pair_cross_links="false"), task_id="t1", medium="video",
    ) is False
    assert await md._refresh_twin_after_upload(_pair_pool(twin="X"), _sc(), task_id=None, medium="video") is False
    fake_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_twin_refresh_failure_never_fails_the_landed_upload(monkeypatch):
    import poindexter.services.youtube_metadata_sync as sync_mod
    monkeypatch.setattr(sync_mod, "sync_youtube_metadata", AsyncMock(side_effect=RuntimeError("scope")))
    assert await md._refresh_twin_after_upload(_pair_pool(twin="LONG1"), _sc(), task_id="t1", medium="video_short") is False


# --------------------------------------------------------------------------
# Upload slots vs cross-links (2026-10-18): a scheduled upload is private until
# its slot, so a pair's link may name only a twin public by then, and the twin
# of a scheduled upload gets its link when the slot passes.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_scheduled_upload_links_only_a_twin_public_by_its_own_slot():
    import datetime as dt

    slot = dt.datetime(2026, 10, 20, 16, 0, tzinfo=dt.timezone.utc)
    pool = _pair_pool(script="Nobody clicks anymore. Here is what to do.", twin="LONG1")
    dispatch = AsyncMock(return_value={"success": True, "post_id": "VID1", "url": "u"})
    row = {"post_id": "p1", "task_id": "t1", "title": "T", "content": "c", "excerpt": "e",
           "seo_keywords": "", "slug": "s", "storage_path": "/tmp/v.mp4"}
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ), patch.object(md, "next_upload_slot", AsyncMock(return_value=slot)), patch.object(
        md, "record_slot", AsyncMock()
    ):
        await md._dispatch_asset(pool, _sc(media_pipeline_trigger_enabled="true"), row, shorts=True)
    twin_calls = [c for c in pool.fetchval.await_args_list if c.args[0] is md._TWIN_SQL]
    assert twin_calls and twin_calls[0].args[3] == slot, "the twin must be public by THIS video's slot"
    assert dispatch.await_args.args[2]["scheduled_at"] == "2026-10-20T16:00:00Z"


def test_the_twin_query_skips_a_twin_whose_slot_is_later():
    assert "youtube_publish_at" in md._TWIN_SQL and "> $3" in md._TWIN_SQL


@pytest.mark.asyncio
async def test_twin_refresh_waits_while_this_upload_is_private(monkeypatch):
    """Linking the twin to a video still private until its slot is a dead link."""
    import poindexter.services.youtube_metadata_sync as sync_mod

    fake_sync = AsyncMock(return_value=[SimpleNamespace(applied=True)])
    monkeypatch.setattr(sync_mod, "sync_youtube_metadata", fake_sync)
    pool = _pair_pool(twin="LONG1")
    base = pool.fetchval.side_effect

    async def _fetchval(sql, *args):
        if sql is md._PUBLISHES_LATER_SQL:
            return True
        return await base(sql, *args)

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    assert await md._refresh_twin_after_upload(
        pool, _sc(), task_id="t1", medium="video_short", asset_id="a1",
    ) is False
    fake_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_gone_public_pass_refreshes_each_twin_once():
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[
        {"asset_id": "a1", "task_id": "t1", "type": "video_short"},
        {"asset_id": "a2", "task_id": "t2", "type": "video"},
    ])
    pool.execute = AsyncMock(return_value="UPDATE 1")
    with patch.object(md, "_refresh_twin_after_upload", AsyncMock(side_effect=[True, RuntimeError("x")])) as ref:
        updated = await md._refresh_twins_gone_public(pool, _sc())
    assert updated == 1
    assert [c.kwargs["medium"] for c in ref.await_args_list] == ["video_short", "video"]
    # Both are stamped, the failed one too: one attempt per video, as at upload.
    assert [c.args[1] for c in pool.execute.await_args_list] == ["a1", "a2"]
    assert all(c.args[0] is md._MARK_GONE_PUBLIC_SQL for c in pool.execute.await_args_list)
    assert pool.fetch.await_args.args[2] == 72


@pytest.mark.asyncio
async def test_a_failed_gone_public_lookup_updates_nothing():
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("db"))
    assert await md._refresh_twins_gone_public(pool, _sc()) == 0


# --------------------------------------------------------------------------
# Custom thumbnails (2026-09-25): each render's composed thumbnail rides with
# its upload (the Short's since 2026-10-13), and what YouTube did with it is
# stamped + surfaced
# --------------------------------------------------------------------------


def _thumb_pool(thumb_path, short_thumb_path=None, package=None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[
        {"name": "yt", "platform": "youtube", "handler_name": "youtube", "config": {}, "metadata": {}},
    ])

    async def _fetchval(sql, *args):
        if "youtube_package" in sql:
            return package
        if "type = $2" in sql and "storage_path" in sql:
            return {"video_thumbnail": thumb_path, "video_short_thumbnail": short_thumb_path}.get(args[1])
        return None
    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.execute = AsyncMock()
    return pool


def _thumb_row():
    return {
        "post_id": "p1", "task_id": "t1", "title": "Clip", "content": "c", "excerpt": "e",
        "seo_keywords": "", "slug": "s", "storage_path": "/tmp/v.mp4",
    }


async def _dispatch_with(pool, sc, *, shorts, result):
    dispatch = AsyncMock(return_value=result)
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ), patch.object(md, "emit_finding") as finding:
        await md._dispatch_asset(pool, sc, _thumb_row(), shorts=shorts)
    return dispatch.await_args.args[2], finding


@pytest.mark.asyncio
async def test_the_long_upload_carries_the_thumbnail_and_stamps_its_outcome(tmp_path):
    thumb = tmp_path / "t1_thumbnail.jpg"
    thumb.write_bytes(b"\xff\xd8jpeg")
    pool = _thumb_pool(str(thumb))
    payload, finding = await _dispatch_with(
        pool, _sc(media_pipeline_trigger_enabled="true"), shorts=False,
        result={"success": True, "post_id": "VID9", "url": "u", "thumbnail": "set"},
    )
    assert payload["thumbnail_path"] == str(thumb)
    stamp = [c for c in pool.execute.await_args_list if "jsonb_build_object('youtube'" in c.args[0]]
    assert stamp and json.loads(stamp[0].args[2])["status"] == "set"
    assert stamp[0].args[3] == "video_thumbnail"
    finding.assert_not_called()


@pytest.mark.asyncio
async def test_a_short_carries_its_own_vertical_thumbnail(tmp_path):
    long_thumb, short_thumb = tmp_path / "t.jpg", tmp_path / "t_short.jpg"
    long_thumb.write_bytes(b"\xff\xd8jpeg")
    short_thumb.write_bytes(b"\xff\xd8jpeg")
    pool = _thumb_pool(str(long_thumb), str(short_thumb))
    payload, _ = await _dispatch_with(
        pool, _sc(media_pipeline_trigger_enabled="true"), shorts=True,
        result={"success": True, "post_id": "S1", "url": "u", "thumbnail": "set"},
    )
    assert payload["thumbnail_path"] == str(short_thumb)
    stamp = [c for c in pool.execute.await_args_list if "jsonb_build_object('youtube'" in c.args[0]]
    assert stamp and stamp[0].args[3] == "video_short_thumbnail"


@pytest.mark.asyncio
async def test_the_short_switch_turns_off_only_the_shorts_thumbnail(tmp_path):
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"\xff\xd8jpeg")
    sc = _sc(media_pipeline_trigger_enabled="true", youtube_short_custom_thumbnail_enabled="false")
    short_payload, _ = await _dispatch_with(
        _thumb_pool(str(thumb), str(thumb)), sc, shorts=True,
        result={"success": True, "post_id": "S1", "url": "u"},
    )
    long_payload, _ = await _dispatch_with(
        _thumb_pool(str(thumb), str(thumb)), sc, shorts=False,
        result={"success": True, "post_id": "V1", "url": "u"},
    )
    assert "thumbnail_path" not in short_payload
    assert long_payload["thumbnail_path"] == str(thumb)


@pytest.mark.asyncio
async def test_a_refused_short_thumbnail_names_the_partner_program_and_its_switch(tmp_path):
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"\xff\xd8jpeg")
    _, finding = await _dispatch_with(
        _thumb_pool(None, str(thumb)), _sc(media_pipeline_trigger_enabled="true"), shorts=True,
        result={"success": True, "post_id": "S1", "url": "u", "thumbnail": "failed: forbidden"},
    )
    finding.assert_called_once()
    kw = finding.call_args.kwargs
    assert "Partner Program" in kw["body"] and "youtube_short_custom_thumbnail_enabled=false" in kw["body"]
    assert kw["dedup_key"].endswith(":short") and kw["extra"]["medium"] == "video_short"


@pytest.mark.asyncio
async def test_a_short_is_titled_and_opened_from_its_reviewed_package(tmp_path):
    package = json.dumps({
        "title": "OpenAI gpt-oss: No new models in 350 days",
        "description": "OpenAI has shipped no open-weights model since gpt-oss.",
    })
    payload, _ = await _dispatch_with(
        _thumb_pool(None, None, package=package), _sc(media_pipeline_trigger_enabled="true"),
        shorts=True, result={"success": True, "post_id": "S1", "url": "u"},
    )
    assert payload["title"] == "OpenAI gpt-oss: No new models in 350 days #Shorts"
    assert payload["description"].startswith("OpenAI has shipped no open-weights model since gpt-oss.")


@pytest.mark.asyncio
async def test_a_short_without_a_package_keeps_the_narration_title(tmp_path):
    pool = _thumb_pool(None, None, package=None)
    base_fetchval = pool.fetchval.side_effect

    async def _fetchval(sql, *args):
        if "short_summary_script" in sql:
            return "Zero-click content is the new standard. It changes how posts get read."
        return await base_fetchval(sql, *args)
    pool.fetchval = AsyncMock(side_effect=_fetchval)
    payload, _ = await _dispatch_with(
        pool, _sc(media_pipeline_trigger_enabled="true"), shorts=True,
        result={"success": True, "post_id": "S1", "url": "u"},
    )
    assert payload["title"] == "Zero-click content is the new standard #Shorts"


@pytest.mark.asyncio
async def test_the_setting_switches_it_off(tmp_path):
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"\xff\xd8jpeg")
    payload, _ = await _dispatch_with(
        _thumb_pool(str(thumb)),
        _sc(media_pipeline_trigger_enabled="true", youtube_custom_thumbnail_enabled="false"),
        shorts=False, result={"success": True, "post_id": "VID9", "url": "u"},
    )
    assert "thumbnail_path" not in payload


@pytest.mark.asyncio
async def test_a_refused_thumbnail_raises_a_finding(tmp_path):
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"\xff\xd8jpeg")
    _, finding = await _dispatch_with(
        _thumb_pool(str(thumb)), _sc(media_pipeline_trigger_enabled="true"), shorts=False,
        result={"success": True, "post_id": "VID9", "url": "u",
                "thumbnail": "failed: the channel cannot set custom thumbnails yet"},
    )
    finding.assert_called_once()
    assert finding.call_args.kwargs["kind"] == "youtube_thumbnail_failed"
    assert "--apply" in finding.call_args.kwargs["body"]


# --------------------------------------------------------------------------
# Mirror pass (Glad-Labs/poindexter#1085): the feed's enclosures go in the
# bucket every cycle, whether or not this cycle linked or dispatched anything
# --------------------------------------------------------------------------


def _mirror_result(*statuses):
    from poindexter.services.video_r2_mirror import MirrorOutcome, MirrorPassResult

    return MirrorPassResult(
        outcomes=[MirrorOutcome(f"p{i}", s) for i, s in enumerate(statuses)],
    )


@pytest.mark.asyncio
async def test_every_active_cycle_runs_the_mirror_pass_even_with_nothing_new():
    """The pass is keyed on state, not on this cycle's dispatches: the
    backlog it exists to heal was dispatched to YouTube cycles (months) ago."""
    job = MediaDistributeJob()
    pool = _FakePool(unlinked=[], approved=[])
    sc = _sc(media_pipeline_trigger_enabled="true", media_distribute_max_per_cycle="7")
    mirror = AsyncMock(return_value=_mirror_result("uploaded", "stamped", "blocked", "error"))
    with patch.object(md, "run_video_r2_mirror", mirror):
        out = await job.run(pool, {"_site_config": sc})

    mirror.assert_awaited_once_with(pool, sc, limit=7)
    assert out.ok
    # Delivered (uploaded + stamped) count as changes; blocked/error don't.
    assert out.changes_made == 2
    assert "video R2 mirror: uploaded 1, stamped 1, blocked 1, errors 1" in out.detail
    assert out.metrics["video_r2_uploaded"] == 1
    assert out.metrics["video_r2_stamped"] == 1
    assert out.metrics["video_r2_blocked"] == 1
    assert out.metrics["video_r2_errors"] == 1


@pytest.mark.asyncio
async def test_an_idle_cycle_records_no_metrics():
    """The scheduler turns any metrics into an audit_log job_run row; an idle
    10-minute cycle must not write 144 rows of zeros a day."""
    job = MediaDistributeJob()
    with patch.object(md, "run_video_r2_mirror", AsyncMock(return_value=_mirror_result())):
        out = await job.run(
            _FakePool(unlinked=[], approved=[]),
            {"_site_config": _sc(media_pipeline_trigger_enabled="true")},
        )
    assert out.ok
    assert out.metrics == {}


@pytest.mark.asyncio
async def test_a_dormant_lane_does_not_mirror():
    job = MediaDistributeJob()
    mirror = AsyncMock(return_value=_mirror_result())
    with patch.object(md, "run_video_r2_mirror", mirror):
        await job.run(_FakePool(), {"_site_config": _sc()})
    mirror.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_mirror_only_cycle_does_not_rebuild_the_feed():
    """The stamped URL is the one the feed already rendered through its
    fallback, so a cycle that only mirrored has nothing to republish."""
    job = MediaDistributeJob()
    mirror = AsyncMock(return_value=_mirror_result("uploaded"))
    rebuild = AsyncMock()
    with patch.object(md, "run_video_r2_mirror", mirror), \
            patch("poindexter.services.media_feed_rebuild.rebuild_video_feed", rebuild):
        await job.run(
            _FakePool(unlinked=[], approved=[]),
            {"_site_config": _sc(media_pipeline_trigger_enabled="true")},
        )
    mirror.assert_awaited_once()
    rebuild.assert_not_awaited()


@pytest.mark.asyncio
async def test_standalone_dispatch_stamps_its_task_and_records_no_post(tmp_path):
    """An approved standalone video (poindexter#1140) is claimed and stamped
    under its task (the approval row's subject) while its distribution row
    carries no post — a task id in that uuid FK would fail the transaction."""
    f = tmp_path / "cartoon.mp4"
    f.write_bytes(b"x")
    job = MediaDistributeJob()
    row = {
        "subject_id": "task-c", "post_id": None, "medium": "video",
        "title": "Kai and Mira Explain VRAM", "content": "", "excerpt": "Two friends, one GPU.",
        "seo_keywords": "", "slug": "", "asset_id": "asset-c", "task_id": "task-c",
        "storage_path": str(f),
    }
    pool = _FakePool(unlinked=[], approved=[row])
    disp = AsyncMock(return_value=[md._PlatformDispatchResult(
        platform="youtube", success=True, external_id="VIDC", url="https://youtu.be/VIDC",
    )])
    with patch.object(md, "_dispatch_asset", disp):
        out = await job.run(pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")})
    assert out.changes_made == 1
    recheck = [c for c in pool.conn.fetchval.await_args_list if "media_approvals" in c.args[0]]
    assert recheck and recheck[0].args[1] == "task-c"
    calls = pool.conn.execute.await_args_list
    stamp = next(c for c in calls if "dispatch_success" in c.args[0])
    assert stamp.args[1] == "task-c"
    dist = next(c for c in calls if "pipeline_distributions" in c.args[0])
    assert dist.args[1] == "task-c"
    assert dist.args[6] is None  # post_id


@pytest.mark.asyncio
async def test_standalone_payload_uses_the_assets_own_title_and_no_backlink():
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[
        {"name": "yt", "platform": "youtube", "handler_name": "youtube", "config": {}, "metadata": {}},
    ])
    pool.fetchval = AsyncMock(return_value=None)
    dispatch = AsyncMock(return_value={"success": True, "post_id": "V", "url": "u"})
    row = {
        "subject_id": "task-c", "post_id": None, "title": "Kai and Mira Explain VRAM",
        "content": "", "excerpt": "Two friends, one GPU.", "seo_keywords": "", "slug": "",
        "storage_path": "/tmp/c.mp4", "task_id": "task-c",
    }
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(pool, _sc(media_pipeline_trigger_enabled="true"), row, shorts=False)
    payload = dispatch.await_args.args[2]
    assert payload["title"] == "Kai and Mira Explain VRAM"
    assert payload["description"].startswith("Two friends, one GPU.")
    assert "/posts/" not in payload["description"]
    assert payload["post_id"] == "task-c"


def test_approved_undispatched_sql_offers_standalone_videos():
    sql = " ".join(md._APPROVED_UNDISPATCHED_SQL.split())
    assert "UNION ALL" in sql
    assert "WHERE ma.post_id IS NULL AND ma.status = 'approved'" in sql


# --------------------------------------------------------------------------
# Upload slots (2026-10-18): an upload goes public at its slot, not at approval
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_slotted_upload_is_scheduled_and_its_slot_stamped():
    import datetime as dt

    slot = dt.datetime(2026, 10, 18, 16, 0, tzinfo=dt.timezone.utc)
    md.next_upload_slot.return_value = slot
    pool = _thumb_pool(None)
    row = {**_thumb_row(), "asset_id": "a1"}
    dispatch = AsyncMock(return_value={"success": True, "post_id": "VID9", "url": "u"})
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(pool, _sc(media_pipeline_trigger_enabled="true"), row, shorts=True)
    assert dispatch.await_args.args[2]["scheduled_at"] == "2026-10-18T16:00:00Z"
    assert md.next_upload_slot.await_args.kwargs == {"shorts": True}
    md.record_slot.assert_awaited_once_with(pool, "a1", slot)


@pytest.mark.asyncio
async def test_no_slot_publishes_now_and_stamps_nothing():
    pool = _thumb_pool(None)
    dispatch = AsyncMock(return_value={"success": True, "post_id": "VID9", "url": "u"})
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(pool, _sc(media_pipeline_trigger_enabled="true"), {**_thumb_row(), "asset_id": "a1"}, shorts=False)
    assert "scheduled_at" not in dispatch.await_args.args[2]
    md.record_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_upload_does_not_hold_the_slot():
    import datetime as dt

    md.next_upload_slot.return_value = dt.datetime(2026, 10, 18, 16, tzinfo=dt.timezone.utc)
    pool = _thumb_pool(None)
    dispatch = AsyncMock(return_value={"success": False, "error": "quota"})
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(pool, _sc(media_pipeline_trigger_enabled="true"), {**_thumb_row(), "asset_id": "a1"}, shorts=True)
    md.record_slot.assert_not_awaited()


# --------------------------------------------------------------------------
# Caption tracks (2026-10-18): a successful YouTube upload gets its captions
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_youtube_upload_gets_its_caption_track(tmp_path):
    pool = _thumb_pool(None)
    row = {**_thumb_row(), "asset_id": "a1"}
    dispatch = AsyncMock(return_value={"success": True, "post_id": "VID9", "url": "u"})
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(pool, _sc(media_pipeline_trigger_enabled="true"), row, shorts=True)
    md.caption_after_upload.assert_awaited_once()
    kw = md.caption_after_upload.await_args.kwargs
    assert kw["asset_id"] == "a1"
    assert kw["video_id"] == "VID9"
    assert kw["video_path"] == "/tmp/v.mp4"
    assert kw["shorts"] is True


@pytest.mark.asyncio
async def test_a_failed_upload_gets_no_captions():
    pool = _thumb_pool(None)
    dispatch = AsyncMock(return_value={"success": False, "error": "quota"})
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(pool, _sc(media_pipeline_trigger_enabled="true"), _thumb_row(), shorts=False)
    md.caption_after_upload.assert_not_awaited()


# --------------------------------------------------------------------------
# Topic playlists (2026-10-18): a successful YouTube upload joins its playlists
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_youtube_upload_joins_its_playlists():
    pool = _thumb_pool(None)
    row = {**_thumb_row(), "asset_id": "a1", "title": "VRAM budgeting", "seo_keywords": "local llm"}
    dispatch = AsyncMock(return_value={"success": True, "post_id": "VID9", "url": "u"})
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(pool, _sc(media_pipeline_trigger_enabled="true"), row, shorts=False)
    md.playlists_after_upload.assert_awaited_once()
    kw = md.playlists_after_upload.await_args.kwargs
    assert kw["asset_id"] == "a1" and kw["video_id"] == "VID9"
    assert "VRAM budgeting" in kw["text"] and "local llm" in kw["text"]


@pytest.mark.asyncio
async def test_a_failed_upload_joins_no_playlist():
    pool = _thumb_pool(None)
    dispatch = AsyncMock(return_value={"success": False, "error": "quota"})
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(pool, _sc(media_pipeline_trigger_enabled="true"), _thumb_row(), shorts=False)
    md.playlists_after_upload.assert_not_awaited()


# --------------------------------------------------------------------------
# Chapters (2026-10-18): a long upload's description carries them, a Short's never
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_long_upload_carries_its_chapters():
    md.chapters_for_upload.return_value = "0:00 A\n0:31 B\n1:05 C"
    pool = _thumb_pool(None)
    dispatch = AsyncMock(return_value={"success": True, "post_id": "VID9", "url": "u"})
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(pool, _sc(media_pipeline_trigger_enabled="true"), {**_thumb_row(), "asset_id": "a1"}, shorts=False)
    assert "Chapters\n0:00 A" in dispatch.await_args.args[2]["description"]
    assert md.chapters_for_upload.await_args.kwargs["asset_id"] == "a1"


@pytest.mark.asyncio
async def test_a_short_never_asks_for_chapters():
    pool = _thumb_pool(None)
    dispatch = AsyncMock(return_value={"success": True, "post_id": "VID9", "url": "u"})
    with patch("poindexter.services.integrations.registry.dispatch", dispatch), patch(
        "poindexter.services.integrations.handlers.load_all", lambda: None
    ):
        await md._dispatch_asset(pool, _sc(media_pipeline_trigger_enabled="true"), _thumb_row(), shorts=True)
    md.chapters_for_upload.assert_not_awaited()
