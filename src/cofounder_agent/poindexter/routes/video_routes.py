"""
Video Routes — the video RSS feed.

Endpoints:
    GET /api/video/feed.xml — Video RSS feed (podcast-style)

``GET /api/video/episodes`` and ``GET /api/video/episodes/{post_id}.mp4`` were
removed (Glad-Labs/poindexter#1087). Both scanned ``VIDEO_DIR`` for post-keyed
``{post_id}.mp4`` files, but every render since the task-keyed cutover (#1460)
is ``{task_id}.mp4`` or ``{task_id}_short.mp4``. The list labelled task ids as
post ids and listed Shorts as episodes, the stream 404ed for every video
rendered since, and nothing called either one. Two surfaces already cover them,
both keyed through ``media_assets``:

- the feed's enclosure URLs, which point at each approved video's copy in
  object storage; and
- ``GET /api/media-approval/{post_id}/video/preview``, the operator console's
  authenticated stream of a local render (``post_id`` → ``storage_path``).

A new video endpoint should read ``media_assets`` the way the feed does, not
scan the directory.
"""

from datetime import datetime, timezone
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from poindexter.services.logger_config import get_logger
from poindexter.services.r2_upload_service import video_episode_key
from poindexter.utils.route_utils import get_site_config_dependency

logger = get_logger(__name__)

router = APIRouter(prefix="/api/video", tags=["video"])

# 2026-05-12 (poindexter#485): removed the "_R2_FALLBACK" constant that
# baked Matt's specific R2 bucket URL into a public OSS file. Forks
# without R2 configured would have served video feeds pointing at
# Matt's bucket. Feed handler now bails with 503 when storage_public_url
# is unset.


def _r2_url(site_config: Any) -> str:
    """Get object-store CDN base URL or raise HTTPException(503) when unset."""
    url = (site_config.get("storage_public_url", "") or "").rstrip("/")
    if not url:
        raise HTTPException(
            status_code=503,
            detail=(
                "storage_public_url not configured — video feed unavailable. "
                "Set via `poindexter settings set storage_public_url 'https://<bucket>.r2.dev'`."
            ),
        )
    return url


def _rfc2822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


@router.get("/feed.xml", response_class=Response)
async def video_feed(
    site_config: Any = Depends(get_site_config_dependency),
):
    """Video RSS feed — long-form video episodes that cleared the gate.

    Mirrors the podcast feed (#689): sourced from ``media_assets`` (the
    canonical file registry), NOT a local-disk scan, so atom-produced
    (task-keyed) episodes surface the same as legacy post-keyed ones. Two
    stacked gates keep un-reviewed video off the public surface:

    1. ``'video' = ANY(media_to_generate)`` — the niche-policy
       seam (``feedback_filter_on_seams_not_slugs``). dev_diary's policy is
       ``{}``, excluding those posts even if a stray asset exists.
    2. ``media_approvals.status='approved'`` (medium='video') — the
       operator-approval gate (``feedback_human_approval`` — every medium,
       not just blog posts). Pending/rejected video never
       reaches the feed; the fix for a missing episode is to approve the row
       (``poindexter media approve <id> video``), not to strip the gate.

    ``media_approvals`` uses ``video`` as the long-form medium; post-#1460 the
    matching ``media_assets`` *type* is identically ``video`` (the legacy
    ``video_long`` type was collapsed in). Short-form (``video_short``) is
    dispatched to YouTube Shorts, not this RSS feed.
    ``DISTINCT ON (p.id)`` collapses multiple video assets per post to the
    newest.
    """
    from poindexter.utils.route_utils import get_services

    db = get_services().get_database()
    pool = db.pool if db else None

    episodes: list[dict] = []
    if pool:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT ON (p.id)
                           p.id::text AS post_id, p.title, p.slug, p.excerpt,
                           p.published_at, mas.url, mas.file_size_bytes
                    FROM posts p
                    JOIN media_assets mas
                      ON mas.post_id = p.id
                     AND mas.type = 'video'
                    JOIN media_approvals ma
                      ON ma.post_id = p.id
                     AND ma.medium = 'video'
                     AND ma.status = 'approved'
                    WHERE p.status = 'published'
                      AND 'video' = ANY(media_to_generate)
                    ORDER BY p.id, mas.created_at DESC NULLS LAST
                    """,
                )
                for r in rows:
                    episodes.append({
                        "post_id": r["post_id"],
                        "title": r["title"] or "Untitled",
                        "slug": r["slug"] or r["post_id"],
                        "excerpt": r["excerpt"] or "",
                        "published_at": r["published_at"],
                        "url": r["url"] or "",
                        "file_size_bytes": r["file_size_bytes"] or 0,
                    })
        except Exception as e:
            logger.warning("[VIDEO] Failed to load feed episodes: %s", e)

    # DISTINCT ON forced p.id ordering; present newest-first by publish date.
    episodes.sort(
        key=lambda ep: ep.get("published_at")
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    if not episodes:
        _vname = site_config.get("video_feed_name", "Video")
        return Response(
            content=f'<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>{_vname}</title></channel></rss>',
            media_type="application/rss+xml; charset=utf-8",
        )

    # Build RSS
    rss = Element("rss")
    rss.set("version", "2.0")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")
    channel = SubElement(rss, "channel")
    _sname = site_config.get("site_name", "Site")
    SubElement(channel, "title").text = site_config.get("video_feed_name", "Video")
    SubElement(channel, "link").text = site_config.require("site_url")
    SubElement(channel, "description").text = (
        f"Video essays from {_sname}. "
        "Narrated slideshows covering technology, infrastructure, and AI."
    )
    SubElement(channel, "language").text = "en-us"

    # Atom self-link — points at the public proxy route on the site, not
    # the worker's internal /api/video/feed.xml (which 404s on the public
    # origin). Same fix as the podcast feed for the same reason.
    atom_link = SubElement(channel, "{http://www.w3.org/2005/Atom}link")
    _su = site_config.require("site_url")
    atom_link.set("href", f"{_su}/video-feed.xml")
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    # Hoisted lookups (constant per feed render). _r2_url raises 503 when
    # storage_public_url is unset — but only reached once we have episodes
    # (an empty feed never 503s).
    _domain = site_config.get("site_domain", "video")
    _r2 = _r2_url(site_config)

    for ep in episodes:
        pid = ep["post_id"]
        item = SubElement(channel, "item")
        SubElement(item, "title").text = ep["title"]
        SubElement(item, "link").text = f"{_su}/posts/{ep['slug']}"
        SubElement(item, "description").text = ep["excerpt"]
        SubElement(item, "guid").text = f"{_domain}-video-{pid}"

        pub_date = ep.get("published_at")
        if pub_date:
            if isinstance(pub_date, str):
                pub_date = datetime.fromisoformat(pub_date)
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            SubElement(item, "pubDate").text = _rfc2822(pub_date)

        # Prefer the media_assets R2 url (#689 source of truth); fall back to
        # the deterministic key for rows not stamped yet. The fallback and
        # media_distribute's mirror pass share video_episode_key, so a feed
        # item's URL is the object the mirror writes and stays the same after
        # the stamp lands. They used to be spelled apart, and every enclosure
        # since #1460 pointed at a key nothing wrote (poindexter#1085).
        enclosure = SubElement(item, "enclosure")
        enclosure.set("url", ep["url"] or f"{_r2}/{video_episode_key(pid)}")
        enclosure.set("length", str(ep["file_size_bytes"]))
        enclosure.set("type", "video/mp4")

    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(rss, encoding="unicode")
    return Response(content=xml_content, media_type="application/rss+xml; charset=utf-8")
