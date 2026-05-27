"""Handler: ``publishing.youtube``.

Uploads finished MP4s to YouTube via the existing
:class:`services.publish_adapters.youtube.YouTubePublishAdapter`.
The adapter owns OAuth refresh + resumable upload; this handler is
a thin shim that adapts the registry's
``(payload, *, site_config, row, pool)`` contract to the adapter's
keyword-only ``publish(*, media_path, title, description, ...)``
signature.

Same shape as :mod:`publishing_bluesky` and :mod:`publishing_mastodon`.

Payload shape::

    {
        "media_path": "/local/path/to/video.mp4",   # required
        "title": "Video title",                      # required
        "description": "Show notes / description",
        "tags": ["ai", "automation"],
        "privacy": "public" | "unlisted" | "private",
        "made_for_kids": false,
        "post_id": "<uuid>",                          # for audit linkage
    }

Returns the adapter's ``PublishResult`` flattened to a dict so
:func:`services.social_poster._distribute_to_adapters` and the
backfill jobs see a consistent shape.
"""

from __future__ import annotations

from typing import Any

from services.integrations.registry import register_handler
from services.publish_adapters.youtube import YouTubePublishAdapter


@register_handler("publishing", "youtube")
async def youtube(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any = None,
) -> dict[str, Any]:
    """Dispatch a video upload to YouTube.

    ``row`` is the ``publishing_adapters`` row that selected this
    handler (used downstream for rate-limit / failure tracking — we
    don't read it directly here, but the registry's runner does).
    """
    if not isinstance(payload, dict):
        raise TypeError(
            "publishing.youtube: payload must be a dict — got "
            f"{type(payload).__name__}"
        )
    media_path = payload.get("media_path")
    title = payload.get("title")
    if not media_path or not title:
        raise TypeError(
            "publishing.youtube: payload must include 'media_path' and "
            "'title' — got keys=" + repr(list(payload.keys()))
        )

    adapter = YouTubePublishAdapter(site_config=site_config)
    # The adapter's publish() returns a PublishResult dataclass. Flatten
    # it to the dict shape every other publishing.* handler returns so
    # _distribute_to_adapters doesn't need a per-handler branch.
    result = await adapter.publish(
        media_path=media_path,
        title=title,
        description=payload.get("description") or "",
        tags=payload.get("tags") or None,
        thumbnail_path=payload.get("thumbnail_path"),
        scheduled_at=payload.get("scheduled_at"),
        category_id=payload.get("category_id"),
        privacy=payload.get("privacy"),
        made_for_kids=payload.get("made_for_kids"),
        _pool=pool,
        _site_config=site_config,
    )

    # PublishResult is a dataclass; .__dict__ is the canonical
    # serialization. Hand-pick the fields the social_poster + audit
    # log actually use rather than splat the whole thing — keeps the
    # contract narrow.
    return {
        "success": bool(getattr(result, "success", False)),
        "platform": "youtube",
        "post_id": getattr(result, "platform_post_id", None)
            or getattr(result, "video_id", None),
        "url": getattr(result, "url", None),
        "error": getattr(result, "error", None),
    }
