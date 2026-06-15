"""Rebuild a media RSS feed on R2 from the worker's feed route.

Shared, idempotent + non-fatal helper. Media (podcast / video) is approved
*after* the post publishes, but the R2 copy of a feed is only rebuilt at
publish time (``publish_service``) — so between publish and approval the feed
is stale and an approval never propagates until some *later* publish. That's
the mechanism behind the 2026-05-27→06-13 feed freeze recurring even once
assets were seeded.

``media_approval_service.decide`` calls :func:`rebuild_feed_for_medium` on
approve so the approval reaches Apple / Spotify / the video feed immediately.

Lifted from ``services.jobs.podcast_distribute._rebuild_feed`` (#689) and
generalized to podcast + video so there's one rebuild seam, not three copies
(``feedback_no_wheel_reinvention``).
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)


async def _rebuild_feed(
    site_config: Any, *, route: str, r2_path: str, label: str,
) -> None:
    """GET ``{internal_api_base_url}{route}`` → upload the body to R2 ``r2_path``.

    Non-fatal: any failure (worker unreachable, R2 unconfigured, upload error)
    is logged and swallowed — the rebuild is additive self-healing, never part
    of the approval transaction.
    """
    try:
        import httpx

        from services.bootstrap_defaults import DEFAULT_WORKER_API_URL
        from services.r2_upload_service import R2UploadService

        api_base = site_config.get("internal_api_base_url", DEFAULT_WORKER_API_URL)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0)
        ) as client:
            feed = await client.get(f"{api_base}{route}", timeout=30)
        fd, feed_path = tempfile.mkstemp(suffix=".xml", prefix="poindexter-feed-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(feed.text)
            await R2UploadService(site_config=site_config).upload_to_r2(
                feed_path, r2_path, "application/rss+xml",
            )
            logger.info(
                "[MEDIA_FEED_REBUILD] %s RSS feed rebuilt on R2 (%s)", label, r2_path,
            )
        finally:
            try:
                os.unlink(feed_path)
            except OSError:  # noqa: silent-ok — best-effort temp-file cleanup
                pass
    except Exception as exc:  # noqa: BLE001 — feed rebuild is non-fatal
        logger.warning(
            "[MEDIA_FEED_REBUILD] %s feed rebuild failed (non-fatal): %s",
            label, exc,
        )


async def rebuild_podcast_feed(site_config: Any) -> None:
    """Rebuild ``podcast/feed.xml`` on R2 from ``/api/podcast/feed.xml``."""
    await _rebuild_feed(
        site_config,
        route="/api/podcast/feed.xml",
        r2_path="podcast/feed.xml",
        label="podcast",
    )


async def rebuild_video_feed(site_config: Any) -> None:
    """Rebuild ``video/feed.xml`` on R2 from ``/api/video/feed.xml``."""
    await _rebuild_feed(
        site_config,
        route="/api/video/feed.xml",
        r2_path="video/feed.xml",
        label="video",
    )


async def rebuild_feed_for_medium(site_config: Any, medium: str) -> None:
    """Rebuild the R2 RSS feed that surfaces an approved ``medium``.

    - ``podcast`` → podcast feed
    - ``video`` → video feed (long-form RSS)
    - ``video_short`` → **no-op**: shorts are dispatched to YouTube Shorts by
      ``media_distribute``, they have no RSS feed surface to rebuild.
    """
    if medium == "podcast":
        await rebuild_podcast_feed(site_config)
    elif medium == "video":
        await rebuild_video_feed(site_config)
    # video_short: no RSS surface — intentionally nothing to rebuild.


__all__ = [
    "rebuild_podcast_feed",
    "rebuild_video_feed",
    "rebuild_feed_for_medium",
]
