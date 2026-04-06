"""
Video Routes — Serve generated video episodes.

Endpoints:
    GET /api/video/feed.xml              — Video RSS feed (podcast-style)
    GET /api/video/episodes              — JSON list of all video episodes
    GET /api/video/episodes/{post_id}.mp4 — Stream a video MP4
    POST /api/video/generate/{post_id}   — Manually trigger video generation
"""

import os
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from middleware.api_token_auth import verify_api_token
from services.logger_config import get_logger
from services.video_service import VIDEO_DIR

logger = get_logger(__name__)

router = APIRouter(prefix="/api/video", tags=["video"])

SITE_URL = "https://www.gladlabs.io"
MEDIA_BASE_URL = os.getenv("SITE_URL", SITE_URL)


def _rfc2822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


@router.get("/feed.xml", response_class=Response)
async def video_feed():
    """Video RSS feed — lists all generated video episodes."""
    from utils.route_utils import get_services

    db = get_services().get_database()
    episodes_on_disk = {}
    if VIDEO_DIR.exists():
        for mp4 in VIDEO_DIR.glob("*.mp4"):
            stat = mp4.stat()
            episodes_on_disk[mp4.stem] = {
                "file_size_bytes": stat.st_size,
                "created_at": stat.st_ctime,
            }

    if not episodes_on_disk:
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>Glad Labs Video</title></channel></rss>',
            media_type="application/rss+xml; charset=utf-8",
        )

    # Fetch post metadata
    post_ids = list(episodes_on_disk.keys())
    posts_meta = []
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
            logger.warning("[VIDEO] Failed to fetch post metadata: %s", e)

    # Build RSS
    rss = Element("rss")
    rss.set("version", "2.0")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = "Glad Labs Video"
    SubElement(channel, "link").text = SITE_URL
    SubElement(channel, "description").text = (
        "AI development video essays from Glad Labs. "
        "Narrated slideshows covering technology, infrastructure, and AI."
    )
    SubElement(channel, "language").text = "en-us"

    atom_link = SubElement(channel, "{http://www.w3.org/2005/Atom}link")
    atom_link.set("href", f"{MEDIA_BASE_URL}/api/video/feed.xml")
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for post in posts_meta:
        pid = post["id"]
        disk_info = episodes_on_disk.get(pid)
        if not disk_info:
            continue

        item = SubElement(channel, "item")
        SubElement(item, "title").text = post.get("title", "Untitled")
        SubElement(item, "link").text = f"{SITE_URL}/posts/{post.get('slug', pid)}"
        SubElement(item, "description").text = post.get("excerpt", "")
        SubElement(item, "guid").text = f"gladlabs-video-{pid}"

        pub_date = post.get("published_at")
        if pub_date:
            if isinstance(pub_date, str):
                pub_date = datetime.fromisoformat(pub_date)
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            SubElement(item, "pubDate").text = _rfc2822(pub_date)

        enclosure = SubElement(item, "enclosure")
        enclosure.set("url", f"{MEDIA_BASE_URL}/media/video/{pid}.mp4")
        enclosure.set("length", str(disk_info.get("file_size_bytes", 0)))
        enclosure.set("type", "video/mp4")

    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(rss, encoding="unicode")
    return Response(content=xml_content, media_type="application/rss+xml; charset=utf-8")


@router.get("/episodes")
async def list_video_episodes():
    """List all available video episodes as JSON."""
    episodes = []
    if VIDEO_DIR.exists():
        for mp4 in sorted(VIDEO_DIR.glob("*.mp4")):
            stat = mp4.stat()
            episodes.append({
                "post_id": mp4.stem,
                "file_path": str(mp4),
                "file_size_bytes": stat.st_size,
                "created_at": stat.st_ctime,
            })
    return {"episodes": episodes, "count": len(episodes)}


@router.get("/episodes/{post_id}.mp4")
async def stream_video(post_id: str):
    """Stream a video episode MP4 file."""
    safe_id = post_id.replace("/", "").replace("\\", "").replace("..", "")
    path = VIDEO_DIR / f"{safe_id}.mp4"

    if not path.resolve().is_relative_to(VIDEO_DIR.resolve()):
        raise HTTPException(status_code=404, detail="Episode not found")

    if not path.exists():
        raise HTTPException(status_code=404, detail="Episode not found")

    return FileResponse(
        path=str(path),
        media_type="video/mp4",
        filename=f"{safe_id}.mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=86400",
        },
    )


@router.post("/generate/{post_id}", dependencies=[Depends(verify_api_token)])
async def generate_video(post_id: str):
    """Manually trigger video generation for a published post."""
    from utils.route_utils import get_services

    db = get_services().get_database()
    pool = getattr(db, "cloud_pool", None) or (db.pool if db else None)
    if not pool:
        raise HTTPException(status_code=503, detail="Database not available")

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
        logger.error("Video generate DB error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")

    if not row:
        raise HTTPException(status_code=404, detail="Published post not found")

    from services.video_service import generate_video_for_post

    result = await generate_video_for_post(
        post_id=row["id"],
        title=row["title"],
        content=row["content"] or "",
        force=True,
    )

    if not result.success:
        logger.error("Video generation failed for %s: %s", post_id, result.error)
        raise HTTPException(status_code=500, detail=f"Video generation failed: {result.error}")

    return {
        "success": True,
        "post_id": post_id,
        "file_path": result.file_path,
        "duration_seconds": result.duration_seconds,
        "file_size_bytes": result.file_size_bytes,
        "images_used": result.images_used,
    }
