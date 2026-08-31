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
}


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.args = None

    async def fetch(self, _sql, *args):
        self.args = args
        return list(self._rows)


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
    short_row = {**ROW, "asset_type": "video_short"}
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
        _FakePool([{**ROW, "asset_type": "video"}]), _sc(), apply=True
    )
    assert seen["title"] == ROW["title"]
    assert "#Shorts" not in seen["title"]
