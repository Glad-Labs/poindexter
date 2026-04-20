"""YouTube adapter — uploads videos to YouTube via the Data API v3.

Free (quota-based, ~6 uploads/day on default quota). Requires:
    app_settings:
        youtube_client_id       — from Google Cloud Console
        youtube_client_secret   — from Google Cloud Console
        youtube_refresh_token   — obtained via OAuth 2.0 flow

The first-time OAuth flow must be done manually (browser-based).
After that, the refresh token handles automatic re-auth.

Usage:
    from services.social_adapters.youtube import upload_to_youtube
    result = await upload_to_youtube(
        video_path="/root/.poindexter/video/abc123.mp4",
        title="My Blog Post",
        description="Full article at https://gladlabs.io/posts/my-post",
        tags=["ai", "content", "automation"],
    )
"""

import httpx

from services.logger_config import get_logger
from services.site_config import site_config


def _read_bytes(path: str) -> bytes:
    """Sync file-read helper for ``asyncio.to_thread`` — keeps the event
    loop free while we pull a multi-MB video into memory (ASYNC230)."""
    with open(path, "rb") as f:
        return f.read()

logger = get_logger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"


async def _refresh_access_token() -> str | None:
    """Exchange refresh token for a fresh access token."""
    client_id = await site_config.get_secret("youtube_client_id", "")
    client_secret = await site_config.get_secret("youtube_client_secret", "")
    refresh_token = await site_config.get_secret("youtube_refresh_token", "")

    if not all([client_id, client_secret, refresh_token]):
        return None

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        if resp.status_code == 200:
            return resp.json().get("access_token")
        logger.warning("[YOUTUBE] Token refresh failed: %s", resp.text[:200])
        return None


async def upload_to_youtube(
    video_path: str,
    title: str,
    description: str,
    tags: list[str] | None = None,
    category_id: str = "28",
    privacy: str = "public",
    **kwargs,
) -> dict:
    """Upload a video to YouTube. Returns {"success", "post_id", "error"}."""
    access_token = await _refresh_access_token()
    if not access_token:
        return {
            "success": False,
            "post_id": None,
            "error": "youtube_client_id, youtube_client_secret, or youtube_refresh_token not configured",
        }

    try:
        import os
        if not os.path.exists(video_path):
            return {"success": False, "post_id": None, "error": f"Video file not found: {video_path}"}

        file_size = os.path.getsize(video_path)
        logger.info("[YOUTUBE] Uploading %s (%d MB)", title[:50], file_size // 1024 // 1024)

        metadata = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": (tags or [])[:30],
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15)) as client:
            # Resumable upload: init
            init_resp = await client.post(
                f"{YOUTUBE_UPLOAD_URL}?uploadType=resumable&part=snippet,status",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(file_size),
                },
                json=metadata,
            )

            if init_resp.status_code != 200:
                return {"success": False, "post_id": None, "error": f"Init failed: {init_resp.text[:200]}"}

            upload_url = init_resp.headers.get("location")
            if not upload_url:
                return {"success": False, "post_id": None, "error": "No upload URL returned"}

            # Upload the file. Read off the event loop — video MP4s are
            # typically tens of MB; a blocking read at that size stalls
            # every other coroutine (ASYNC230).
            import asyncio
            content = await asyncio.to_thread(_read_bytes, video_path)
            upload_resp = await client.put(
                upload_url,
                headers={"Content-Type": "video/mp4"},
                content=content,
            )

            if upload_resp.status_code in (200, 201):
                data = upload_resp.json()
                video_id = data.get("id", "")
                logger.info("[YOUTUBE] Uploaded: https://youtube.com/watch?v=%s", video_id)
                return {
                    "success": True,
                    "post_id": video_id,
                    "url": f"https://youtube.com/watch?v={video_id}",
                    "error": None,
                }
            else:
                return {"success": False, "post_id": None, "error": f"Upload failed: {upload_resp.text[:200]}"}

    except Exception as e:
        logger.exception("[YOUTUBE] Error: %s", e)
        return {"success": False, "post_id": None, "error": str(e)}
