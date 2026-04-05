"""
Video Routes — Serve generated video episodes.

Endpoints:
    GET /api/video/episodes              — JSON list of all video episodes
    GET /api/video/episodes/{post_id}.mp4 — Stream a video MP4
    POST /api/video/generate/{post_id}   — Manually trigger video generation
"""

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from middleware.api_token_auth import verify_api_token
from services.logger_config import get_logger
from services.video_service import VIDEO_DIR

logger = get_logger(__name__)

router = APIRouter(prefix="/api/video", tags=["video"])


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
