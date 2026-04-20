"""
Podcast Routes — Serve podcast RSS feed and MP3 episode files.

Endpoints:
    GET /api/podcast/feed.xml     — Podcast RSS feed (Apple/Spotify compatible)
    GET /api/podcast/episodes     — JSON list of all episodes
    GET /api/podcast/episodes/{post_id}.mp3 — Stream an episode MP3
    POST /api/podcast/generate/{post_id} — Manually trigger episode generation
"""

from datetime import datetime, timezone
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from middleware.api_token_auth import verify_api_token
from services.logger_config import get_logger
from services.podcast_service import PODCAST_DIR, PodcastService
from utils.route_utils import get_site_config_dependency

logger = get_logger(__name__)

router = APIRouter(prefix="/api/podcast", tags=["podcast"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_R2_FALLBACK = "https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev"


def _site_url(site_config: Any) -> str:
    return site_config.require("site_url")


def _r2_url(site_config: Any) -> str:
    """Get R2 CDN base URL, with DB override support."""
    return site_config.get("r2_public_url", _R2_FALLBACK)


def _format_duration(seconds: int) -> str:
    """Format seconds as HH:MM:SS for iTunes duration tag."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _rfc2822(dt: datetime) -> str:
    """Format datetime as RFC 2822 for RSS pubDate."""
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


# ---------------------------------------------------------------------------
# RSS Feed
# ---------------------------------------------------------------------------


@router.get("/feed.xml", response_class=Response)
async def podcast_feed(
    site_config: Any = Depends(get_site_config_dependency),
):
    """Generate a valid podcast RSS feed (Apple Podcasts / Spotify compatible)."""
    # Lazy import to avoid circular deps
    from utils.route_utils import get_services

    db = get_services().get_database()

    svc = PodcastService()
    episodes_on_disk = {ep["post_id"]: ep for ep in svc.list_episodes()}

    if not episodes_on_disk:
        # Return a valid but empty feed
        return Response(
            content=_build_rss_xml([], site_config),
            media_type="application/rss+xml; charset=utf-8",
        )

    # Fetch post metadata for episodes that exist on disk
    post_ids = list(episodes_on_disk.keys())
    posts_meta = []

    # Use cloud_pool (if configured) where published posts live
    pool = getattr(db, "cloud_pool", None) or (db.pool if db else None)
    if pool:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id::text, title, slug, excerpt, published_at
                    FROM posts
                    WHERE id::text = ANY($1) AND status = 'published'
                    ORDER BY published_at DESC
                    """,
                    post_ids,
                )
                posts_meta = [dict(r) for r in rows]
        except Exception as e:
            logger.warning("[PODCAST] Failed to fetch post metadata: %s", e)

    # Build episode list merging DB metadata with disk info
    episodes = []
    for post in posts_meta:
        pid = post["id"]
        disk_info = episodes_on_disk.get(pid, {})
        if not disk_info:
            continue
        episodes.append({
            "post_id": pid,
            "title": post.get("title", "Untitled"),
            "slug": post.get("slug", pid),
            "description": post.get("excerpt", ""),
            "published_at": post.get("published_at"),
            "file_size_bytes": disk_info.get("file_size_bytes", 0),
            "duration_seconds": 0,  # Estimated on the fly if needed
        })

    xml_content = _build_rss_xml(episodes, site_config)
    return Response(
        content=xml_content,
        media_type="application/rss+xml; charset=utf-8",
    )


def _build_rss_xml(episodes: list[dict], site_config: Any) -> str:
    """Build a podcast RSS XML string following Apple Podcasts spec."""
    rss = Element("rss")
    rss.set("version", "2.0")
    rss.set("xmlns:itunes", "http://www.itunes.com/dtds/podcast-1.0.dtd")
    rss.set("xmlns:content", "http://purl.org/rss/1.0/modules/content/")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")

    channel = SubElement(rss, "channel")

    # Channel metadata
    SubElement(channel, "title").text = site_config.get("podcast_name", "Podcast")
    SubElement(channel, "link").text = _site_url(site_config)
    SubElement(channel, "language").text = "en-us"
    SubElement(channel, "description").text = site_config.get(
        "podcast_description", "Podcast feed"
    )
    SubElement(
        channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}author"
    ).text = site_config.get("site_name", "Author")
    SubElement(
        channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}summary"
    ).text = (
        "AI development insights and guides from an AI-operated content business."
    )

    # itunes:owner
    owner = SubElement(
        channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}owner"
    )
    SubElement(
        owner, "{http://www.itunes.com/dtds/podcast-1.0.dtd}name"
    ).text = site_config.get("owner_name", "Owner")
    SubElement(
        owner, "{http://www.itunes.com/dtds/podcast-1.0.dtd}email"
    ).text = site_config.get("owner_email", "")

    # itunes:category
    cat = SubElement(
        channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}category"
    )
    cat.set("text", "Technology")

    SubElement(
        channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}explicit"
    ).text = "no"

    # Atom self-link
    atom_link = SubElement(channel, "{http://www.w3.org/2005/Atom}link")
    atom_link.set("href", f"{_site_url(site_config)}/api/podcast/feed.xml")
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    # Hoisted lookups (constant per feed render — read once, not per-episode)
    _domain = site_config.get("site_domain", "podcast")
    # Bump via: UPDATE app_settings SET value = 'v3' WHERE key = 'podcast_cdn_version';
    _cdn_ver = site_config.get("podcast_cdn_version", "v2")
    _r2 = _r2_url(site_config)
    _site = _site_url(site_config)

    # Episodes
    for ep in episodes:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = ep["title"]
        SubElement(item, "link").text = f"{_site}/posts/{ep['slug']}"
        SubElement(item, "description").text = ep.get("description", "")
        SubElement(item, "guid").text = f"{_domain}-podcast-{ep['post_id']}"

        pub_date = ep.get("published_at")
        if pub_date:
            if isinstance(pub_date, str):
                pub_date = datetime.fromisoformat(pub_date)
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            SubElement(item, "pubDate").text = _rfc2822(pub_date)

        # Enclosure (the MP3 file)
        enclosure = SubElement(item, "enclosure")
        enclosure.set(
            "url", f"{_r2}/podcast/{_cdn_ver}/{ep['post_id']}.mp3"
        )
        enclosure.set("length", str(ep.get("file_size_bytes", 0)))
        enclosure.set("type", "audio/mpeg")

        # iTunes-specific
        duration = ep.get("duration_seconds", 0)
        if duration:
            SubElement(
                item,
                "{http://www.itunes.com/dtds/podcast-1.0.dtd}duration",
            ).text = _format_duration(duration)

        SubElement(
            item, "{http://www.itunes.com/dtds/podcast-1.0.dtd}explicit"
        ).text = "no"

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
        rss, encoding="unicode"
    )


# ---------------------------------------------------------------------------
# Episode streaming
# ---------------------------------------------------------------------------


@router.get("/episodes/{post_id}.mp3")
async def stream_episode(post_id: str):
    """Stream a podcast episode MP3 file."""
    # Sanitize post_id to prevent path traversal
    safe_id = post_id.replace("/", "").replace("\\", "").replace("..", "")
    path = PODCAST_DIR / f"{safe_id}.mp3"

    # Defense-in-depth: verify resolved path is under PODCAST_DIR
    if not path.resolve().is_relative_to(PODCAST_DIR.resolve()):
        raise HTTPException(status_code=404, detail="Episode not found")

    if not path.exists():
        raise HTTPException(status_code=404, detail="Episode not found")

    return FileResponse(
        path=str(path),
        media_type="audio/mpeg",
        filename=f"{safe_id}.mp3",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=86400",
        },
    )


# ---------------------------------------------------------------------------
# Episode listing (JSON)
# ---------------------------------------------------------------------------


@router.get("/episodes")
async def list_episodes():
    """List all available podcast episodes as JSON."""
    svc = PodcastService()
    episodes = svc.list_episodes()
    return {"episodes": episodes, "count": len(episodes)}


# ---------------------------------------------------------------------------
# Manual generation trigger
# ---------------------------------------------------------------------------


@router.post("/generate/{post_id}", dependencies=[Depends(verify_api_token)])
async def generate_episode(post_id: str):
    """Manually trigger podcast episode generation for a published post."""
    from utils.route_utils import get_services

    db = get_services().get_database()
    pool = getattr(db, "cloud_pool", None) or (db.pool if db else None)
    if not pool:
        raise HTTPException(status_code=503, detail="Database not available")

    # Fetch post content from cloud DB where published posts live
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id::text, title, content
                FROM posts
                WHERE id::text = $1 AND status = 'published'
                """,
                post_id,
            )
    except Exception as e:
        logger.error("Podcast generate DB error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")

    if not row:
        raise HTTPException(status_code=404, detail="Published post not found")

    svc = PodcastService()
    result = await svc.generate_episode(
        post_id=row["id"],
        title=row["title"],
        content=row["content"] or "",
        force=True,
    )

    if not result.success:
        logger.error("Podcast generation failed for %s: %s", post_id, result.error)
        raise HTTPException(status_code=500, detail="Episode generation failed")

    return {
        "success": True,
        "post_id": post_id,
        "file_path": result.file_path,
        "duration_seconds": result.duration_seconds,
        "file_size_bytes": result.file_size_bytes,
    }
