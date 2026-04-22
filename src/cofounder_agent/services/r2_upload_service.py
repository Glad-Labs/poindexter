"""
Object Store Upload Service — uploads media files to an S3-compatible bucket.

Works with Cloudflare R2, AWS S3, Backblaze B2, MinIO, Wasabi — any
provider that speaks the S3 API. Reads all config from app_settings
(DB-first, no env vars) so the operator can swap providers without
touching code.

Usage:
    from services.r2_upload_service import upload_to_r2

    url = await upload_to_r2(
        "/path/to/file.mp3", "podcast/abc123.mp3", site_config=site_config,
    )
"""

import os
from pathlib import Path
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


# Content type mapping
_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _storage(site_config: Any, key: str, default: str = "") -> str:
    """Read a NON-SECRET object-store setting. Prefers the generic
    ``storage_*`` namespace; falls back to the legacy ``cloudflare_r2_*``
    keys so an in-flight deployment keeps working during the rename (#198).
    """
    return site_config.get(f"storage_{key}") or site_config.get(
        f"cloudflare_r2_{key}", default
    )


async def _storage_secret(site_config: Any, key: str, default: str = "") -> str:
    """Read a SECRET object-store setting via on-demand DB query.

    Secrets aren't kept in the in-memory site_config cache (is_secret=true
    filters them out of load()). This mirrors how revalidate_secret is
    fetched by routes/revalidate_routes.py.
    """
    val = await site_config.get_secret(f"storage_{key}")
    if val:
        return val
    val = await site_config.get_secret(f"cloudflare_r2_{key}")
    return val or default


async def upload_to_r2(
    local_path: str,
    r2_key: str,
    content_type: str | None = None,
    *,
    site_config: Any,
) -> str | None:
    """Upload a file to Cloudflare R2 and return its public URL.

    Args:
        local_path: Absolute path to the local file.
        r2_key: Object key in R2 (e.g. "podcast/abc123.mp3").
        content_type: MIME type. Auto-detected from extension if not provided.
        site_config: SiteConfig instance (DI — Phase H, GH#95). Must be
            passed explicitly — the module-level singleton import was
            removed.

    Returns:
        Public URL of the uploaded file, or None on failure.
    """
    path = Path(local_path)
    if not path.exists():
        logger.warning("[R2] File not found: %s", local_path)
        return None

    # Get credentials from DB (storage_* preferred, cloudflare_r2_* fallback).
    # access_key is NOT marked is_secret (it's paired with the secret and
    # can't do damage alone), so site_config has it cached. secret_key
    # and token ARE secrets — fetched via on-demand DB query.
    access_key = _storage(site_config, "access_key")
    secret_key = await _storage_secret(site_config, "secret_key")

    if not access_key or not secret_key:
        logger.warning(
            "[STORAGE] No object-store credentials in app_settings "
            "(storage_access_key / storage_secret_key) — skipping upload"
        )
        return None

    endpoint_url = _storage(site_config, "endpoint")
    bucket = _storage(site_config, "bucket")
    if not endpoint_url or not bucket:
        logger.warning(
            "[STORAGE] storage_endpoint or storage_bucket not configured — "
            "skipping upload"
        )
        return None

    # Auto-detect content type
    if not content_type:
        content_type = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")

    try:
        import boto3

        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )
        size = path.stat().st_size

        logger.info("[STORAGE] Uploading %s → %s (%s, %.1fMB)",
                    path.name, r2_key, content_type, size / 1024 / 1024)

        s3.upload_file(
            str(path), bucket, r2_key,
            ExtraArgs={"ContentType": content_type},
        )

        public_url = _storage(site_config, "public_url") or site_config.get(
            "r2_public_url", "",
        )
        if not public_url:
            logger.warning(
                "[STORAGE] storage_public_url not set — can't construct "
                "public link for %s", r2_key
            )
            return None
        url = f"{public_url.rstrip('/')}/{r2_key}"
        logger.info("[STORAGE] Uploaded: %s", url)
        return url

    except ImportError:
        logger.warning("[STORAGE] boto3 not installed — cannot upload")
        return None
    except Exception as e:
        logger.exception("[STORAGE] Upload failed for %s: %s", r2_key, e)
        return None


async def upload_podcast_episode(post_id: str, *, site_config: Any) -> str | None:
    """Upload a podcast episode MP3 to R2. Returns public URL or None.
    Uses versioned path (podcast_cdn_version) for cache-busting.

    Args:
        post_id: Post UUID (used as episode file stem).
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    try:
        cdn_ver = site_config.get("podcast_cdn_version", "v2")
    except Exception:
        cdn_ver = "v2"
    podcast_dir = Path(os.path.expanduser("~")) / ".poindexter" / "podcast"
    mp3_path = podcast_dir / f"{post_id}.mp3"
    if mp3_path.exists():
        return await upload_to_r2(
            str(mp3_path),
            f"podcast/{cdn_ver}/{post_id}.mp3",
            site_config=site_config,
        )
    return None


async def upload_video_episode(post_id: str, *, site_config: Any) -> str | None:
    """Upload a video episode MP4 to R2. Returns public URL or None.

    Args:
        post_id: Post UUID (used as episode file stem).
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    video_dir = Path(os.path.expanduser("~")) / ".poindexter" / "video"
    mp4_path = video_dir / f"{post_id}.mp4"
    if mp4_path.exists():
        return await upload_to_r2(
            str(mp4_path),
            f"video/{post_id}.mp4",
            site_config=site_config,
        )
    return None
