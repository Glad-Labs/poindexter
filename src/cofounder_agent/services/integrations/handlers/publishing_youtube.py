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
        "shorts": false,                              # 9:16 vertical short — appends a #Shorts marker
        "post_id": "<uuid>",                          # for audit linkage
    }

For video-backfill uploads the ``description`` and ``tags`` are
SEO-composed upstream (glad-labs-stack#275): ``description`` is the
post's SEO meta description (``posts.excerpt``) + a canonical
"Read the full post: {site_url}/posts/{slug}" back-link + the
markup-stripped body (capped ≤ 4800 chars under YouTube's 5000 limit),
and ``tags`` is the parsed ``posts.seo_keywords`` list (≤ 30 tags,
≤ 500 joined chars) or ``None`` when there are no keywords. See
``services/jobs/backfill_videos.py::_dispatch_video_publishers``. The
handler forwards both verbatim; the adapter re-clamps to YouTube's hard
caps as a backstop.

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
        # Vertical short-form (9:16, ≤60s) gets YouTube's ``#Shorts``
        # classification marker injected by the adapter. Long uploads
        # leave it off.
        shorts=bool(payload.get("shorts", False)),
        _pool=pool,
        _site_config=site_config,
    )

    # Map the PublishResult dataclass to the narrow dict shape the
    # social_poster + audit log consume. Use DIRECT attribute access
    # (not getattr-with-default): the contract is the PublishResult
    # dataclass, so a field rename should fail loud here rather than
    # silently degrade to None — that silent degrade was bug #682,
    # where the shim read the non-existent ``platform_post_id`` / ``url``
    # and recorded post_id=None / url=None on every successful upload.
    return {
        "success": bool(result.success),
        "platform": "youtube",
        "post_id": result.external_id,
        "url": result.public_url,
        "error": result.error,
    }
