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

from poindexter.services.site_config import SiteConfig
from poindexter.services.youtube_metadata_sync import SyncOutcome, sync_youtube_metadata

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
    import poindexter.services.publish_adapters.youtube as yt

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
    import poindexter.services.publish_adapters.youtube as yt

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
    import poindexter.services.publish_adapters.youtube as yt

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
    import poindexter.services.publish_adapters.youtube as yt

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
    import poindexter.services.publish_adapters.youtube as yt

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
    import poindexter.services.publish_adapters.youtube as yt

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
        "poindexter.services.youtube_metadata_sync.emit_finding", lambda **_kw: None
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
        "poindexter.services.youtube_metadata_sync", fromlist=["_MARK_DELETED_SQL"]
    )._MARK_DELETED_SQL
    assert "external_id = $1" in sql
    assert "task_id" not in sql


@pytest.mark.asyncio
async def test_vanished_upload_raises_a_finding(monkeypatch):
    """Nothing else in the system would ever report that a public surface went
    away, so the reconcile has to say so rather than just tidying the row."""
    seen: list[dict] = []
    monkeypatch.setattr(
        "poindexter.services.youtube_metadata_sync.emit_finding",
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
        "poindexter.services.youtube_metadata_sync.emit_finding", lambda **_kw: None
    )
    import poindexter.services.publish_adapters.youtube as yt

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
        "poindexter.services.youtube_metadata_sync.emit_finding", lambda **_kw: None
    )
    pool = _FakePool([ROW])
    out = await sync_youtube_metadata(pool, _sc())
    assert out[0].reconciled_deleted is False
    assert pool.conn.executed == []


@pytest.mark.asyncio
async def test_demoted_rows_drop_out_of_the_target_set():
    """The demotion only pays off if the next run stops offering the row."""
    from poindexter.services.youtube_metadata_sync import _TARGETS_SQL

    assert "pd.status = 'published'" in _TARGETS_SQL


@pytest.mark.asyncio
async def test_medium_comes_from_the_distribution_row_not_a_subquery():
    """One source for 'which render is this'. It used to be recovered by a
    correlated subquery into media_assets because pipeline_distributions had no
    medium column — the same missing column that let a Short's row be
    overwritten in the first place (migration 20260901_173133)."""
    from poindexter.services.youtube_metadata_sync import _TARGETS_SQL

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
    from poindexter.services.youtube_metadata_sync import find_unrecorded_uploads

    pool = _TwoSourcePool(
        [ROW],
        [{"video_id": "orphanVid1", "medium": "video_short",
          "task_id": "t1", "post_id": "p1"}],
    )
    out = await find_unrecorded_uploads(pool)
    assert [o["video_id"] for o in out] == ["orphanVid1"]


@pytest.mark.asyncio
async def test_cross_check_is_clean_when_the_two_records_agree():
    from poindexter.services.youtube_metadata_sync import find_unrecorded_uploads

    assert await find_unrecorded_uploads(_TwoSourcePool([ROW], [])) == []


@pytest.mark.asyncio
async def test_cross_check_failure_never_breaks_the_sync():
    """It is a check on the answer, not the answer. A broken cross-check must
    degrade to 'no discrepancy reported', never take the sync down with it."""
    from poindexter.services.youtube_metadata_sync import find_unrecorded_uploads

    class _Boom:
        def acquire(self):
            raise RuntimeError("pool gone")

    assert await find_unrecorded_uploads(_Boom()) == []


def test_cross_check_reads_the_source_that_did_not_lose_data():
    """media_assets stores one handle per asset row, so it structurally could
    not collide the way (task_id, target) did — which is why it is the right
    thing to check the distribution table against."""
    from poindexter.services.youtube_metadata_sync import _ORPHAN_HANDLES_SQL

    assert "media_assets" in _ORPHAN_HANDLES_SQL
    assert "platform_video_ids->>'youtube'" in _ORPHAN_HANDLES_SQL
    assert "NOT EXISTS" in _ORPHAN_HANDLES_SQL
    # Deliberately NOT filtered on status: a row demoted to 'deleted' is still
    # a recorded upload, and re-reporting it as unrecorded would be noise.
    assert "status" not in _ORPHAN_HANDLES_SQL


# --------------------------------------------------------------------------
# The pair (2026-09-22): the Short's own hook + hashtags, and cross-links
# composed from the twin's LIVE row only
# --------------------------------------------------------------------------


def test_targets_sql_carries_the_twin_and_the_short_script():
    from poindexter.services.youtube_metadata_sync import _TARGETS_SQL

    assert "short_summary_script" in _TARGETS_SQL
    assert "twin_video_id" in _TARGETS_SQL
    # Only a LIVE twin is a link target: a vanished/deleted row must drop out.
    assert "t.status = 'published'" in _TARGETS_SQL
    assert "t.medium <> pd.medium" in _TARGETS_SQL


def test_compose_gives_the_short_its_own_title_hook_and_links_the_long_form():
    from poindexter.services.youtube_metadata_sync import _compose

    row = {
        **ROW, "medium": "video_short", "twin_video_id": "LONG1", "twin_medium": "video",
        "short_script": "Your best breakdown dies in silence. Here is why.",
    }
    title, description, _tags = _compose(row, _sc())
    assert title == "Your best breakdown dies in silence #Shorts"
    assert description.startswith("Your best breakdown dies in silence. Here is why.")
    assert "Watch the full breakdown: https://www.youtube.com/watch?v=LONG1" in description
    assert "utm_medium=shorts" in description
    assert "#Shorts #ContentAmplification #DistributionInfrastructure" in description


def test_compose_links_the_long_form_to_its_live_short():
    from poindexter.services.youtube_metadata_sync import _compose

    title, description, _tags = _compose({**ROW, "twin_video_id": "SHORT1", "twin_medium": "video_short"}, _sc())
    assert title == ROW["title"]
    assert "Watch the Short: https://www.youtube.com/shorts/SHORT1" in description
    assert "utm_medium=video" in description


def test_compose_without_a_live_twin_omits_the_cross_link():
    from poindexter.services.youtube_metadata_sync import _compose

    _t, description, _ = _compose({**ROW, "medium": "video_short", "short_script": "Hook one."}, _sc())
    assert "Watch the full breakdown" not in description
    _t, description, _ = _compose(dict(ROW), _sc())
    assert "Watch the Short" not in description


# ---------------------------------------------------------------------------
# Hook repair on the sync path (pd#1074)
# ---------------------------------------------------------------------------
#
# The gate that writes a good hook runs at SCRIPT-generation time. This path
# can only SHORTEN, so a Short rendered before the gate existed keeps whatever
# the model first wrote and its title comes out mid-phrase. Measured
# 2026-09-23: 4 of the 9 live Shorts.

# A run-on the model never finished — `runaway`, which shortening cannot fix.
_BAD_SCRIPT = (
    "The hidden debt of five tech giants, including Alphabet, Microsoft, Amazon, "
    "Meta, and Oracle, is an astounding $1.65 trillion, $300 billion more than "
    "officially listed on their balance sheets. That gap is the story."
)
_GOOD_SCRIPT = (
    "Five tech giants hide $1.65 trillion in AI infrastructure debt. "
    "That gap is the story."
)

SHORT_ROW = {
    **ROW,
    "medium": "video_short",
    "video_id": "Q2Sc2niHIgI",
    "task_id": "f555bedc-1dac-44de-ba06-911cf97d088c",
    "short_script": _BAD_SCRIPT,
}


def _short_row(**over):
    return {**SHORT_ROW, **over}


class TestStoredHookDefects:
    def test_a_runaway_hook_is_reported(self):
        from poindexter.services.youtube_metadata_sync import stored_hook_defects

        assert "runaway" in stored_hook_defects(_short_row(), _sc())

    def test_a_clean_hook_reports_nothing(self):
        from poindexter.services.youtube_metadata_sync import stored_hook_defects

        assert stored_hook_defects(_short_row(short_script=_GOOD_SCRIPT), _sc()) == ()

    def test_a_long_form_row_is_never_scored(self):
        """Only a Short titles itself from its narration."""
        from poindexter.services.youtube_metadata_sync import stored_hook_defects

        assert stored_hook_defects(_short_row(medium="video"), _sc()) == ()

    def test_length_alone_is_not_a_defect(self):
        """Over-long is a SHORTENING problem — the title builder handles it and
        it must not buy an LLM call."""
        from poindexter.services.short_hook import first_sentence, hook_defects, strip_preamble
        from poindexter.services.youtube_metadata_sync import stored_hook_defects

        # 79-char hook: past the 70 budget, inside the 105 runaway threshold,
        # and a finished claim. Exactly the case a shorten handles.
        long_but_good = (
            "Five tech giants are hiding one point six five trillion dollars off "
            "their books. Here is the gap."
        )
        hook = strip_preamble(first_sentence(long_but_good)).rstrip(".").strip()
        assert hook_defects(hook) == ("too_long",), "fixture must be over budget"
        assert stored_hook_defects(_short_row(short_script=long_but_good), _sc()) == ()


@pytest.mark.asyncio
class TestHookRepairOnSync:
    async def test_a_dry_run_reports_the_defect_and_spends_nothing(self):
        """A dry run must not cost a GPU call or touch the DB, but it still has
        to say what --apply would fix."""
        async def _boom(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("repair called during a dry run")

        pool = _FakePool([_short_row()])
        out = await sync_youtube_metadata(pool, _sc(), repair_hook=_boom)
        assert out[0].applied is False
        assert "runaway" in out[0].hook_defects
        assert out[0].hook_repaired is False
        assert pool.conn.executed == [], "a dry run wrote to the database"

    async def test_no_repair_hook_means_no_repair(self, monkeypatch):
        """Backcompat: the pre-2026-09-23 callers inject nothing and must keep
        composing from whatever is stored — media_distribute is one of them."""
        import poindexter.services.publish_adapters.youtube as yt

        class _Adapter:
            def __init__(self, **k): pass
            async def update_metadata(self, **k):
                return SimpleNamespace(success=True, status="ok")

        monkeypatch.setattr(yt, "YouTubePublishAdapter", _Adapter)
        out = await sync_youtube_metadata(_FakePool([_short_row()]), _sc(), apply=True)
        assert out[0].hook_repaired is False
        assert "runaway" in out[0].hook_defects

    async def test_apply_repairs_titles_from_the_new_hook_and_persists_it(self, monkeypatch):
        import poindexter.services.publish_adapters.youtube as yt

        async def _repair(script, **kwargs):
            return _GOOD_SCRIPT, {"repaired": True}

        seen = {}

        class _Adapter:
            def __init__(self, **k): pass
            async def update_metadata(self, **k):
                seen.update(k)
                return SimpleNamespace(success=True, status="ok")

        monkeypatch.setattr(yt, "YouTubePublishAdapter", _Adapter)
        pool = _FakePool([_short_row()])
        out = await sync_youtube_metadata(pool, _sc(), apply=True, repair_hook=_repair)

        assert out[0].hook_repaired is True
        assert out[0].applied is True
        # The title YouTube receives comes from the REPAIRED hook.
        assert seen["title"].startswith("Five tech giants hide")
        # ...and the repaired narration was written back, so it is paid for once.
        writes = [e for e in pool.conn.executed if "pipeline_versions" in e[0]]
        assert len(writes) == 1
        assert writes[0][1] == SHORT_ROW["task_id"]
        assert writes[0][2] == _GOOD_SCRIPT

    async def test_a_refused_repair_keeps_the_original_and_writes_nothing(self, monkeypatch):
        """repair_short_hook never returns a worse hook; when it declines, the
        sync proceeds with what was stored."""
        import poindexter.services.publish_adapters.youtube as yt

        async def _declined(script, **kwargs):
            return script, {"repaired": False, "skipped": "gpu_busy"}

        class _Adapter:
            def __init__(self, **k): pass
            async def update_metadata(self, **k):
                return SimpleNamespace(success=True, status="ok")

        monkeypatch.setattr(yt, "YouTubePublishAdapter", _Adapter)
        pool = _FakePool([_short_row()])
        out = await sync_youtube_metadata(pool, _sc(), apply=True, repair_hook=_declined)
        assert out[0].hook_repaired is False
        assert out[0].applied is True, "a declined repair must not fail the sync"
        assert [e for e in pool.conn.executed if "pipeline_versions" in e[0]] == []

    async def test_a_raising_repair_never_fails_the_sync(self, monkeypatch):
        import poindexter.services.publish_adapters.youtube as yt

        async def _raise(script, **kwargs):
            raise RuntimeError("ollama down")

        class _Adapter:
            def __init__(self, **k): pass
            async def update_metadata(self, **k):
                return SimpleNamespace(success=True, status="ok")

        monkeypatch.setattr(yt, "YouTubePublishAdapter", _Adapter)
        out = await sync_youtube_metadata(
            _FakePool([_short_row()]), _sc(), apply=True, repair_hook=_raise)
        assert out[0].applied is True and out[0].hook_repaired is False

    async def test_a_clean_hook_never_buys_a_call(self, monkeypatch):
        import poindexter.services.publish_adapters.youtube as yt

        async def _boom(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("repair called on a clean hook")

        class _Adapter:
            def __init__(self, **k): pass
            async def update_metadata(self, **k):
                return SimpleNamespace(success=True, status="ok")

        monkeypatch.setattr(yt, "YouTubePublishAdapter", _Adapter)
        out = await sync_youtube_metadata(
            _FakePool([_short_row(short_script=_GOOD_SCRIPT)]),
            _sc(), apply=True, repair_hook=_boom)
        assert out[0].hook_defects == () and out[0].hook_repaired is False
