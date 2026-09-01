"""Tests for re-pushing YouTube metadata to already-published videos.

The gap this closes: the upload path composes metadata once and never revisits
it, so when the description builder changed on 2026-08-31 the 12 videos already
on the channel kept their old 4,800-char markdown wall.

The scope reality is baked into these tests because it is the first thing an
operator will hit: the live refresh token was granted ``youtube.upload``, which
is INSERT-ONLY, so every real call fails until a re-consent — and it has to
fail with the remediation, not a raw Google 403.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from services.site_config import SiteConfig
from services.youtube_metadata_sync import SyncOutcome, sync_youtube_metadata

ROW = {
    "video_id": "dZxk7FuodZo",
    "post_id": "11111111-2222-3333-4444-555555555555",
    "title": "Why Great Content Dies Without an Amplification System",
    "excerpt": "You can write the best breakdown. Nobody cares.",
    "content": "You can write the best breakdown. Nobody cares.\n\n"
               "## What this looks like\n"
               "We built [Poindexter](/posts/x) to scale a pipeline.",
    "seo_keywords": "content amplification, distribution infrastructure",
    "slug": "why-great-content-dies-e311bcc1",
    "medium": "video",
}


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.args = None
        self.executed: list[tuple] = []

    async def fetch(self, _sql, *args):
        self.args = args
        return list(self._rows)

    async def execute(self, sql, *args):
        self.executed.append((sql, *args))


class _FakePool:
    def __init__(self, rows):
        self.conn = _FakeConn(rows)

    @asynccontextmanager
    async def _acquire(self):
        yield self.conn

    def acquire(self):
        return self._acquire()


def _sc(**extra):
    return SiteConfig(initial_config={"site_url": "https://www.gladlabs.io", **extra})


@pytest.mark.asyncio
async def test_dry_run_is_the_default_and_sends_nothing(monkeypatch):
    """This writes to a public channel — a mistake must cost a printed diff,
    not 12 rewritten videos."""
    import services.publish_adapters.youtube as yt

    def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("adapter constructed during a dry run")

    monkeypatch.setattr(yt, "YouTubePublishAdapter", _boom)
    out = await sync_youtube_metadata(_FakePool([ROW]), _sc())
    assert len(out) == 1
    assert out[0].applied is False
    assert out[0].error is None
    assert out[0].video_id == "dZxk7FuodZo"


@pytest.mark.asyncio
async def test_dry_run_reports_the_new_composition_not_the_old():
    """The whole point: the recomposed description is the SHORT one, proving
    the sync uses the current builders rather than replaying what was sent."""
    out = await sync_youtube_metadata(_FakePool([ROW]), _sc())
    # Excerpt + tagged link only — nowhere near the old 4,800-char wall.
    assert out[0].description_chars < 400
    assert out[0].tag_count == 2


@pytest.mark.asyncio
async def test_apply_pushes_recomposed_metadata_through_the_adapter(monkeypatch):
    import services.publish_adapters.youtube as yt

    seen = {}

    class _Adapter:
        def __init__(self, site_config=None):
            pass

        async def update_metadata(self, *, video_id, title, description, tags):
            seen.update(
                video_id=video_id, title=title, description=description, tags=tags
            )
            return SimpleNamespace(success=True, error=None)

    monkeypatch.setattr(yt, "YouTubePublishAdapter", _Adapter)
    out = await sync_youtube_metadata(_FakePool([ROW]), _sc(), apply=True)

    assert out[0].applied is True
    assert seen["video_id"] == "dZxk7FuodZo"
    assert seen["title"] == ROW["title"]
    # Composed by the shared builder: tagged back-link present, markdown gone.
    assert "utm_source=youtube" in seen["description"]
    assert "](" not in seen["description"] and "##" not in seen["description"]
    assert seen["tags"] == ["content amplification", "distribution infrastructure"]


@pytest.mark.asyncio
async def test_failure_is_reported_per_video_not_swallowed(monkeypatch):
    """A partial result is the useful one — a scope refusal that stopped the
    run must not read as 'nothing needed changing'."""
    import services.publish_adapters.youtube as yt

    class _Adapter:
        def __init__(self, site_config=None):
            pass

        async def update_metadata(self, **_kw):
            return SimpleNamespace(
                success=False, error="youtube.upload is INSERT-ONLY; re-consent"
            )

    monkeypatch.setattr(yt, "YouTubePublishAdapter", _Adapter)
    out = await sync_youtube_metadata(_FakePool([ROW]), _sc(), apply=True)
    assert out[0].applied is False
    assert "INSERT-ONLY" in out[0].error


@pytest.mark.asyncio
async def test_selector_and_limit_narrow_the_run():
    pool = _FakePool([ROW, {**ROW, "video_id": "other"}])
    out = await sync_youtube_metadata(pool, _sc(), selector="dZxk7FuodZo", limit=1)
    assert pool.conn.args == ("dZxk7FuodZo",)
    assert len(out) == 1


@pytest.mark.asyncio
async def test_no_targets_returns_empty():
    assert await sync_youtube_metadata(_FakePool([]), _sc()) == []


def test_outcome_is_immutable():
    o = SyncOutcome(video_id="v", post_id="p", title="t", applied=False)
    with pytest.raises(Exception):
        o.applied = True  # type: ignore[misc]


@pytest.mark.asyncio
async def test_resync_preserves_the_shorts_title_suffix(monkeypatch):
    """A re-sync must not strip a Short back to the long-form title.

    The sync recomposes from posts.title, which is the LONG-FORM name — so
    without carrying the asset type through, --apply would helpfully undo the
    suffix and re-collide the pair it exists to separate.
    """
    import services.publish_adapters.youtube as yt

    seen = {}

    class _Adapter:
        def __init__(self, site_config=None):
            pass

        async def update_metadata(self, *, video_id, title, description, tags):
            seen["title"] = title
            return SimpleNamespace(success=True, error=None)

    monkeypatch.setattr(yt, "YouTubePublishAdapter", _Adapter)
    short_row = {**ROW, "medium": "video_short"}
    await sync_youtube_metadata(_FakePool([short_row]), _sc(), apply=True)
    assert seen["title"].endswith(" #Shorts")


@pytest.mark.asyncio
async def test_resync_leaves_long_form_titles_alone(monkeypatch):
    import services.publish_adapters.youtube as yt

    seen = {}

    class _Adapter:
        def __init__(self, site_config=None):
            pass

        async def update_metadata(self, *, video_id, title, description, tags):
            seen["title"] = title
            return SimpleNamespace(success=True, error=None)

    monkeypatch.setattr(yt, "YouTubePublishAdapter", _Adapter)
    await sync_youtube_metadata(
        _FakePool([{**ROW, "medium": "video"}]), _sc(), apply=True
    )
    assert seen["title"] == ROW["title"]
    assert "#Shorts" not in seen["title"]


# --------------------------------------------------------------------------
# Vanished-upload reconcile
# --------------------------------------------------------------------------


def _not_found_adapter(monkeypatch, calls=None):
    """Adapter stand-in for a video the API says is not on this channel."""
    import services.publish_adapters.youtube as yt

    class _Adapter:
        def __init__(self, site_config=None):
            pass

        async def update_metadata(self, *, video_id, **_kw):
            if calls is not None:
                calls.append(video_id)
            return SimpleNamespace(
                success=False,
                status=yt.STATUS_NOT_FOUND,
                error=f"video {video_id!r} not found on this channel",
            )

    monkeypatch.setattr(yt, "YouTubePublishAdapter", _Adapter)


@pytest.mark.asyncio
async def test_vanished_upload_is_demoted_to_deleted(monkeypatch):
    """A video deleted from the channel left a row claiming it was published
    forever: it inflated the published count and failed every --apply. The
    platform's own 'not found' is the authority, so the row is demoted."""
    monkeypatch.setattr(
        "services.youtube_metadata_sync.emit_finding", lambda **_kw: None
    )
    _not_found_adapter(monkeypatch)
    pool = _FakePool([ROW])
    out = await sync_youtube_metadata(pool, _sc(), apply=True)

    assert out[0].reconciled_deleted is True
    assert out[0].applied is False
    sql, *args = pool.conn.executed[0]
    assert "status = 'deleted'" in sql
    assert args[0] == "dZxk7FuodZo"


@pytest.mark.asyncio
async def test_demotion_is_keyed_on_the_handle_not_the_task(monkeypatch):
    """Only the render that actually vanished may be demoted — its twin under
    the same task_id is a different upload and stays published."""
    sql = __import__(
        "services.youtube_metadata_sync", fromlist=["_MARK_DELETED_SQL"]
    )._MARK_DELETED_SQL
    assert "external_id = $1" in sql
    assert "task_id" not in sql


@pytest.mark.asyncio
async def test_vanished_upload_raises_a_finding(monkeypatch):
    """Nothing else in the system would ever report that a public surface went
    away, so the reconcile has to say so rather than just tidying the row."""
    seen: list[dict] = []
    monkeypatch.setattr(
        "services.youtube_metadata_sync.emit_finding",
        lambda **kw: seen.append(kw),
    )
    _not_found_adapter(monkeypatch)
    await sync_youtube_metadata(_FakePool([ROW]), _sc(), apply=True)

    assert seen[0]["kind"] == "youtube_upload_vanished"
    assert seen[0]["severity"] == "warn"
    # Per-video dedup key: one deletion must never mute another's.
    assert seen[0]["dedup_key"].endswith(":dZxk7FuodZo")


@pytest.mark.asyncio
async def test_ordinary_failure_is_not_demoted(monkeypatch):
    """A scope refusal / quota / 5xx is a window, not a verdict. Only the
    structural not_found marker may demote a row — string-matching the message
    would eventually mark a live video deleted."""
    monkeypatch.setattr(
        "services.youtube_metadata_sync.emit_finding", lambda **_kw: None
    )
    import services.publish_adapters.youtube as yt

    class _Adapter:
        def __init__(self, site_config=None):
            pass

        async def update_metadata(self, **_kw):
            return SimpleNamespace(
                success=False,
                status="",
                error="youtube.upload is INSERT-ONLY; re-consent — video not found?",
            )

    monkeypatch.setattr(yt, "YouTubePublishAdapter", _Adapter)
    pool = _FakePool([ROW])
    out = await sync_youtube_metadata(pool, _sc(), apply=True)
    assert out[0].reconciled_deleted is False
    assert pool.conn.executed == []


@pytest.mark.asyncio
async def test_dry_run_never_demotes(monkeypatch):
    """A dry run makes no API call, so it has no evidence — and this writes to
    a durable row, which is exactly what dry run exists to withhold."""
    monkeypatch.setattr(
        "services.youtube_metadata_sync.emit_finding", lambda **_kw: None
    )
    pool = _FakePool([ROW])
    out = await sync_youtube_metadata(pool, _sc())
    assert out[0].reconciled_deleted is False
    assert pool.conn.executed == []


@pytest.mark.asyncio
async def test_demoted_rows_drop_out_of_the_target_set():
    """The demotion only pays off if the next run stops offering the row."""
    from services.youtube_metadata_sync import _TARGETS_SQL

    assert "pd.status = 'published'" in _TARGETS_SQL


@pytest.mark.asyncio
async def test_medium_comes_from_the_distribution_row_not_a_subquery():
    """One source for 'which render is this'. It used to be recovered by a
    correlated subquery into media_assets because pipeline_distributions had no
    medium column — the same missing column that let a Short's row be
    overwritten in the first place (migration 20260901_173133)."""
    from services.youtube_metadata_sync import _TARGETS_SQL

    assert "pd.medium" in _TARGETS_SQL
    assert "platform_video_ids" not in _TARGETS_SQL


# --------------------------------------------------------------------------
# Completeness cross-check
# --------------------------------------------------------------------------


class _TwoSourcePool:
    """Routes ``fetch`` on the SQL so the two record-sets can differ."""

    def __init__(self, targets, orphans):
        self.conn = _FakeConn(targets)
        self._orphans = orphans

        async def _fetch(sql, *args):
            if "NOT EXISTS" in sql:
                return list(self._orphans)
            self.conn.args = args
            return list(targets)

        self.conn.fetch = _fetch  # type: ignore[method-assign]

    @asynccontextmanager
    async def _acquire(self):
        yield self.conn

    def acquire(self):
        return self._acquire()


@pytest.mark.asyncio
async def test_cross_check_finds_handles_with_no_distribution_row():
    """The sync reads pipeline_distributions, so a handle that only exists in
    media_assets is a video it cannot see. That is precisely how five Shorts
    went unreachable — silently, with the sync reporting success."""
    from services.youtube_metadata_sync import find_unrecorded_uploads

    pool = _TwoSourcePool(
        [ROW],
        [{"video_id": "orphanVid1", "medium": "video_short",
          "task_id": "t1", "post_id": "p1"}],
    )
    out = await find_unrecorded_uploads(pool)
    assert [o["video_id"] for o in out] == ["orphanVid1"]


@pytest.mark.asyncio
async def test_cross_check_is_clean_when_the_two_records_agree():
    from services.youtube_metadata_sync import find_unrecorded_uploads

    assert await find_unrecorded_uploads(_TwoSourcePool([ROW], [])) == []


@pytest.mark.asyncio
async def test_cross_check_failure_never_breaks_the_sync():
    """It is a check on the answer, not the answer. A broken cross-check must
    degrade to 'no discrepancy reported', never take the sync down with it."""
    from services.youtube_metadata_sync import find_unrecorded_uploads

    class _Boom:
        def acquire(self):
            raise RuntimeError("pool gone")

    assert await find_unrecorded_uploads(_Boom()) == []


def test_cross_check_reads_the_source_that_did_not_lose_data():
    """media_assets stores one handle per asset row, so it structurally could
    not collide the way (task_id, target) did — which is why it is the right
    thing to check the distribution table against."""
    from services.youtube_metadata_sync import _ORPHAN_HANDLES_SQL

    assert "media_assets" in _ORPHAN_HANDLES_SQL
    assert "platform_video_ids->>'youtube'" in _ORPHAN_HANDLES_SQL
    assert "NOT EXISTS" in _ORPHAN_HANDLES_SQL
    # Deliberately NOT filtered on status: a row demoted to 'deleted' is still
    # a recorded upload, and re-reporting it as unrecorded would be noise.
    assert "status" not in _ORPHAN_HANDLES_SQL
