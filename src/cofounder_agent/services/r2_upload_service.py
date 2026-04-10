"""
R2 Upload Service — uploads media files to Cloudflare R2.

Handles podcast MP3s, video MP4s, and images.
Credentials loaded from app_settings (DB-first, no env vars).

Usage:
    from services.r2_upload_service import upload_to_r2

    url = await upload_to_r2("/path/to/file.mp3", "podcast/abc123.mp3")
    # Returns: "https://pub-1432fd...r2.dev/podcast/abc123.mp3"
"""

import os
from pathlib import Path
from typing import Optional

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)

_R2_PUBLIC_URL = "https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev"
_R2_ENDPOINT = "https://01ddb679184ebe59cc7f03f8171d76ee.r2.cloudflarestorage.com"
_R2_BUCKET = "gladlabs-media"

# Content type mapping
_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


async def upload_to_r2(
    local_path: str,
    r2_key: str,
    content_type: Optional[str] = None,
) -> Optional[str]:
    """Upload a file to Cloudflare R2 and return its public URL.

    Args:
        local_path: Absolute path to the local file.
        r2_key: Object key in R2 (e.g. "podcast/abc123.mp3").
        content_type: MIME type. Auto-detected from extension if not provided.

    Returns:
        Public URL of the uploaded file, or None on failure.
    """
    path = Path(local_path)
    if not path.exists():
        logger.warning("[R2] File not found: %s", local_path)
        return None

    # Get credentials from DB
    access_key = site_config.get("cloudflare_r2_access_key", "")
    secret_key = site_config.get("cloudflare_r2_secret_key", "")

    if not access_key or not secret_key:
        logger.warning("[R2] No R2 credentials in app_settings — skipping upload")
        return None

    # Auto-detect content type
    if not content_type:
        content_type = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")

    try:
        import boto3

        s3 = boto3.client(
            "s3",
            endpoint_url=site_config.get("cloudflare_r2_endpoint", _R2_ENDPOINT),
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )

        bucket = site_config.get("cloudflare_r2_bucket", _R2_BUCKET)
        size = path.stat().st_size

        logger.info("[R2] Uploading %s → %s (%s, %.1fMB)",
                    path.name, r2_key, content_type, size / 1024 / 1024)

        s3.upload_file(
            str(path), bucket, r2_key,
            ExtraArgs={"ContentType": content_type},
        )

        public_url = site_config.get("r2_public_url", _R2_PUBLIC_URL)
        url = f"{public_url}/{r2_key}"
        logger.info("[R2] Uploaded: %s", url)
        return url

    except ImportError:
        logger.warning("[R2] boto3 not installed — cannot upload to R2")
        return None
    except Exception as e:
        logger.error("[R2] Upload failed for %s: %s", r2_key, e)
        return None


async def upload_podcast_episode(post_id: str) -> Optional[str]:
    """Upload a podcast episode MP3 to R2. Returns public URL or None.
    Uses versioned path (podcast_cdn_version) for cache-busting."""
    try:
        from services.site_config import site_config
        cdn_ver = site_config.get("podcast_cdn_version", "v2")
    except Exception:
        cdn_ver = "v2"
    podcast_dir = Path(os.path.expanduser("~")) / ".poindexter" / "podcast"
    mp3_path = podcast_dir / f"{post_id}.mp3"
    if mp3_path.exists():
        return await upload_to_r2(str(mp3_path), f"podcast/{cdn_ver}/{post_id}.mp3")
    return None


async def upload_video_episode(post_id: str) -> Optional[str]:
    """Upload a video episode MP4 to R2. Returns public URL or None."""
    video_dir = Path(os.path.expanduser("~")) / ".poindexter" / "video"
    mp4_path = video_dir / f"{post_id}.mp4"
    if mp4_path.exists():
        return await upload_to_r2(str(mp4_path), f"video/{post_id}.mp4")
    return None
