"""Tests for ``services.media_feed_rebuild``.

Shared, non-fatal helper that rebuilds a media RSS feed on R2 from the
worker's feed route. ``media_approval_service.decide`` calls it on approve so
an approval reaches Apple/Spotify/the video feed immediately (media is
approved AFTER publish, when the publish-time R2 rebuild already ran).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import media_feed_rebuild


def _site_config() -> MagicMock:
    sc = MagicMock()
    sc.get.side_effect = lambda k, d=None: {
        "internal_api_base_url": "http://worker.test:8002",
    }.get(k, d)
    return sc


# ---------------------------------------------------------------------------
# rebuild_feed_for_medium — routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_feed_for_medium_routes_podcast() -> None:
    sc = _site_config()
    with patch.object(
        media_feed_rebuild, "rebuild_podcast_feed", new=AsyncMock()
    ) as pod, patch.object(
        media_feed_rebuild, "rebuild_video_feed", new=AsyncMock()
    ) as vid:
        await media_feed_rebuild.rebuild_feed_for_medium(sc, "podcast")
    pod.assert_awaited_once_with(sc)
    vid.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_feed_for_medium_routes_video() -> None:
    sc = _site_config()
    with patch.object(
        media_feed_rebuild, "rebuild_podcast_feed", new=AsyncMock()
    ) as pod, patch.object(
        media_feed_rebuild, "rebuild_video_feed", new=AsyncMock()
    ) as vid:
        await media_feed_rebuild.rebuild_feed_for_medium(sc, "video")
    vid.assert_awaited_once_with(sc)
    pod.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_feed_for_medium_video_short_is_noop() -> None:
    """Shorts are dispatched to YouTube Shorts, not an RSS feed — nothing to
    rebuild. Neither feed helper fires."""
    sc = _site_config()
    with patch.object(
        media_feed_rebuild, "rebuild_podcast_feed", new=AsyncMock()
    ) as pod, patch.object(
        media_feed_rebuild, "rebuild_video_feed", new=AsyncMock()
    ) as vid:
        await media_feed_rebuild.rebuild_feed_for_medium(sc, "video_short")
    pod.assert_not_awaited()
    vid.assert_not_awaited()


# ---------------------------------------------------------------------------
# rebuild_{podcast,video}_feed — fetch worker route, upload to R2
# ---------------------------------------------------------------------------


def _mock_httpx_client(feed_text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = feed_text
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
async def test_rebuild_podcast_feed_fetches_route_and_uploads_r2_key() -> None:
    sc = _site_config()
    client = _mock_httpx_client("<rss>podcast</rss>")
    upload = AsyncMock(return_value="https://r2.test/podcast/feed.xml")
    r2 = MagicMock()
    r2.upload_to_r2 = upload
    with patch("httpx.AsyncClient", return_value=client), patch(
        "services.r2_upload_service.R2UploadService", return_value=r2
    ):
        await media_feed_rebuild.rebuild_podcast_feed(sc)
    # GET the worker's podcast feed route ...
    assert "/api/podcast/feed.xml" in client.get.await_args.args[0]
    # ... and upload it to the canonical R2 key.
    assert upload.await_args.args[1] == "podcast/feed.xml"


@pytest.mark.asyncio
async def test_rebuild_video_feed_fetches_route_and_uploads_r2_key() -> None:
    sc = _site_config()
    client = _mock_httpx_client("<rss>video</rss>")
    upload = AsyncMock(return_value="https://r2.test/video/feed.xml")
    r2 = MagicMock()
    r2.upload_to_r2 = upload
    with patch("httpx.AsyncClient", return_value=client), patch(
        "services.r2_upload_service.R2UploadService", return_value=r2
    ):
        await media_feed_rebuild.rebuild_video_feed(sc)
    assert "/api/video/feed.xml" in client.get.await_args.args[0]
    assert upload.await_args.args[1] == "video/feed.xml"


@pytest.mark.asyncio
async def test_rebuild_is_non_fatal_on_error() -> None:
    """A failure fetching/uploading the feed must never raise — the rebuild is
    additive self-healing, not part of the approval transaction."""
    sc = _site_config()
    with patch("httpx.AsyncClient", side_effect=RuntimeError("worker down")):
        # Must not raise.
        await media_feed_rebuild.rebuild_podcast_feed(sc)


# ---------------------------------------------------------------------------
# count_feed_items
# ---------------------------------------------------------------------------


def _feed(n_items: int) -> str:
    items = "".join(
        f"<item><title>ep {i}</title><guid>g-{i}</guid></item>" for i in range(n_items)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'


@pytest.mark.parametrize("n", [0, 1, 71, 100])
def test_count_feed_items(n: int) -> None:
    assert media_feed_rebuild.count_feed_items(_feed(n)) == n


def test_count_feed_items_handles_none_and_garbage() -> None:
    """A missing or unparseable published object counts as zero, never raises —
    the caller treats that as drift and republishes."""
    assert media_feed_rebuild.count_feed_items(None) == 0
    assert media_feed_rebuild.count_feed_items("not xml at all") == 0


def test_count_feed_items_ignores_itunes_prefixed_tags() -> None:
    """``<itunes:image>`` and friends must not be miscounted as ``<item>``."""
    xml = (
        '<?xml version="1.0"?><rss><channel>'
        '<itunes:image href="x"/><itunes:owner/>'
        "<item><title>only one</title></item>"
        "</channel></rss>"
    )
    assert media_feed_rebuild.count_feed_items(xml) == 1


# ---------------------------------------------------------------------------
# reconcile_feed — the convergence loop
# ---------------------------------------------------------------------------


def _patch_transport(rendered: str | None, published: str | None):
    """Patch the rendered-feed GET + the authoritative R2 read/write.

    Returns ``(context_manager_factory, upload_mock)``.
    """
    upload = AsyncMock(return_value="https://r2.test/podcast/feed.xml")
    r2 = MagicMock()
    r2.upload_to_r2 = upload
    r2.get_object_text = AsyncMock(return_value=published)

    if rendered is None:
        client_patch = patch("httpx.AsyncClient", side_effect=RuntimeError("worker down"))
    else:
        client_patch = patch("httpx.AsyncClient", return_value=_mock_httpx_client(rendered))

    return client_patch, patch(
        "services.r2_upload_service.R2UploadService", return_value=r2
    ), upload


@pytest.mark.asyncio
async def test_reconcile_in_sync_does_not_upload() -> None:
    """Rendered == published: nothing to do. The steady state must be a pure
    read, so the watchdog costs one query + one GET per cycle, not an R2 write."""
    sc = _site_config()
    body = _feed(100)
    cp, rp, upload = _patch_transport(rendered=body, published=body)
    with cp, rp:
        res = await media_feed_rebuild.reconcile_feed(sc, "podcast")
    assert res.drifted is False
    assert res.healed is False
    assert (res.rendered_items, res.published_items) == (100, 100)
    upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_uploads_when_published_feed_is_behind() -> None:
    """The bug this job exists for: R2 held 71 episodes while the DB was
    eligible for 100 (2026-07-18). Drift must republish the rendered body."""
    sc = _site_config()
    rendered = _feed(100)
    cp, rp, upload = _patch_transport(rendered=rendered, published=_feed(71))
    with cp, rp:
        res = await media_feed_rebuild.reconcile_feed(sc, "podcast")
    assert res.drifted is True
    assert res.healed is True
    assert (res.rendered_items, res.published_items) == (100, 71)
    upload.assert_awaited_once()
    assert upload.await_args.args[1] == "podcast/feed.xml"


@pytest.mark.asyncio
async def test_reconcile_publishes_when_object_absent() -> None:
    """No published object yet (fresh install / deleted key) — bootstrap it."""
    sc = _site_config()
    cp, rp, upload = _patch_transport(rendered=_feed(3), published=None)
    with cp, rp:
        res = await media_feed_rebuild.reconcile_feed(sc, "podcast")
    assert res.drifted is True
    assert res.healed is True
    upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_never_clobbers_when_render_fails() -> None:
    """If the worker route is unreachable we have no truth to publish. Leaving
    the existing feed alone is strictly safer than overwriting it."""
    sc = _site_config()
    cp, rp, upload = _patch_transport(rendered=None, published=_feed(100))
    with cp, rp:
        res = await media_feed_rebuild.reconcile_feed(sc, "podcast")
    assert res.healed is False
    assert res.error is not None
    upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_refuses_to_publish_an_empty_feed_over_a_full_one() -> None:
    """THE load-bearing guard.

    ``podcast_feed`` swallows its own DB error and renders a *valid* zero-item
    feed. A naive convergence loop would treat that as truth and wipe every
    episode off Apple/Spotify — turning a staleness bug into an outage. A
    collapse past ``media_feed_reconcile_max_shrink`` must refuse and escalate.
    """
    sc = _site_config()
    cp, rp, upload = _patch_transport(rendered=_feed(0), published=_feed(100))
    with cp, rp:
        res = await media_feed_rebuild.reconcile_feed(sc, "podcast")
    assert res.refused is True
    assert res.healed is False
    assert (res.rendered_items, res.published_items) == (0, 100)
    upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_allows_a_shrink_within_tolerance() -> None:
    """An operator un-publishing one post is a legitimate shrink — the guard
    must not wedge the feed for ordinary edits."""
    sc = _site_config()
    cp, rp, upload = _patch_transport(rendered=_feed(99), published=_feed(100))
    with cp, rp:
        res = await media_feed_rebuild.reconcile_feed(sc, "podcast")
    assert res.refused is False
    assert res.healed is True
    upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_shrink_tolerance_is_configurable() -> None:
    sc = MagicMock()
    sc.get.side_effect = lambda k, d=None: {
        "internal_api_base_url": "http://worker.test:8002",
        "media_feed_reconcile_max_shrink": "50",
    }.get(k, d)
    cp, rp, upload = _patch_transport(rendered=_feed(60), published=_feed(100))
    with cp, rp:
        res = await media_feed_rebuild.reconcile_feed(sc, "podcast")
    # 40-item shrink is inside the widened tolerance → publish.
    assert res.refused is False
    upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_routes_video_to_the_video_surface() -> None:
    sc = _site_config()
    cp, rp, upload = _patch_transport(rendered=_feed(5), published=_feed(1))
    with cp, rp:
        res = await media_feed_rebuild.reconcile_feed(sc, "video")
    assert res.medium == "video"
    assert upload.await_args.args[1] == "video/feed.xml"


@pytest.mark.asyncio
async def test_reconcile_rejects_a_medium_with_no_feed_surface() -> None:
    """``video_short`` goes to YouTube Shorts, not RSS. Fail loud rather than
    silently reconciling nothing (``feedback_no_silent_defaults``)."""
    sc = _site_config()
    with pytest.raises(ValueError, match="video_short"):
        await media_feed_rebuild.reconcile_feed(sc, "video_short")


@pytest.mark.asyncio
async def test_reconcile_is_non_fatal_on_unexpected_error() -> None:
    """A watchdog must never crash the scheduler cycle."""
    sc = _site_config()
    with patch.object(
        media_feed_rebuild, "_fetch_rendered_feed",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        res = await media_feed_rebuild.reconcile_feed(sc, "podcast")
    assert res.healed is False
    assert res.error is not None
