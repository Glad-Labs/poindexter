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
