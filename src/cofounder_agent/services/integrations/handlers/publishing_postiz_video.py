"""Handler: ``publishing.postiz_video``.

Uploads finished videos to short-form social platforms (TikTok, Instagram
Reels) via Postiz.  The handler:

1. Uploads the video asset to Postiz via ``/public/v1/uploads/url``
2. Creates a post on the target platform via ``/public/v1/posts``

Payload shape::

    {
        "media_url": "https://cdn.example.com/video.mp4",   # required
        "title": "Post caption / title",                     # required
        "description": "Optional longer description",
        "platform": "tiktok" | "instagram_reels",           # required
    }

Integration IDs are read from app_settings:
  ``postiz_integration_id_tiktok``
  ``postiz_integration_id_instagram``
"""

from __future__ import annotations

import logging
from typing import Any

from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)

_PLATFORM_TYPE = {
    "tiktok": "tiktok",
    "instagram_reels": "instagram",
}

_INTEGRATION_KEY = {
    "tiktok": "postiz_integration_id_tiktok",
    "instagram_reels": "postiz_integration_id_instagram",
}


@register_handler("publishing", "postiz_video")
async def postiz_video(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any = None,
) -> dict[str, Any]:
    """Upload a video to TikTok or Instagram Reels via Postiz."""
    from services.integrations.postiz_client import PostizClient

    if not isinstance(payload, dict):
        raise TypeError(
            "publishing.postiz_video: payload must be a dict — got "
            f"{type(payload).__name__}"
        )

    media_url = payload.get("media_url")
    title = payload.get("title") or ""
    platform = payload.get("platform") or ""

    if not media_url:
        raise ValueError("publishing.postiz_video: payload must include 'media_url'")
    if platform not in _PLATFORM_TYPE:
        raise ValueError(
            f"publishing.postiz_video: unsupported platform {platform!r}; "
            f"expected one of {list(_PLATFORM_TYPE)}"
        )

    base_url = site_config.get("postiz_api_url", "http://localhost:5003")
    integration_id = site_config.get(_INTEGRATION_KEY[platform], "")
    if not integration_id:
        return {
            "success": False,
            "platform": platform,
            "post_id": None,
            "url": None,
            "error": (
                f"postiz_integration_id not configured for {platform}; "
                f"set app_settings.{_INTEGRATION_KEY[platform]}"
            ),
        }

    client = PostizClient(base_url=base_url)
    try:
        upload_id = await client.upload_from_url(media_url)
    except Exception as exc:
        logger.error(
            "[publishing.postiz_video] upload failed for %s on %s: %s",
            media_url, platform, exc,
        )
        return {
            "success": False, "platform": platform,
            "post_id": None, "url": None, "error": str(exc),
        }

    platform_settings: dict[str, Any] = {}
    if platform == "tiktok":
        platform_settings = {
            "privacyLevel": "PUBLIC_TO_EVERYONE",
            "duetDisabled": False,
            "stitchDisabled": False,
        }
    elif platform == "instagram_reels":
        platform_settings = {"shareToFeed": True}

    result = await client.create_post(
        integration_id=integration_id,
        content=title,
        platform_type=_PLATFORM_TYPE[platform],
        platform_settings=platform_settings,
        upload_ids=[upload_id],
    )

    return {
        "success": result["success"],
        "platform": platform,
        "post_id": result.get("post_id"),
        "url": None,
        "error": result.get("error"),
    }
