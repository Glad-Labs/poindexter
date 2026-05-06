"""
Object Store Upload Service — uploads media files to an S3-compatible bucket.

Works with Cloudflare R2, AWS S3, Backblaze B2, MinIO, Wasabi — any
provider that speaks the S3 API. Reads all config from app_settings
(DB-first, no env vars) so the operator can swap providers without
touching code.

Usage:
    from services.r2_upload_service import upload_to_r2

    url = await upload_to_r2("/path/to/file.mp3", "podcast/abc123.mp3")
"""

import os
from pathlib import Path

from services.logger_config import get_logger
from services.site_config import site_config

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


def _storage(key: str, default: str = "") -> str:
    """Read a NON-SECRET object-store setting. Prefers the generic
    ``storage_*`` namespace; falls back to the legacy ``cloudflare_r2_*``
    keys so an in-flight deployment keeps working during the rename (#198).
    """
    return site_config.get(f"storage_{key}") or site_config.get(
        f"cloudflare_r2_{key}", default
    )


async def _storage_secret(key: str, default: str = "") -> str:
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
) -> str | None:
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

    # Get credentials from DB (storage_* preferred, cloudflare_r2_* fallback).
    # access_key is NOT marked is_secret (it's paired with the secret and
    # can't do damage alone), so site_config has it cached. secret_key
    # and token ARE secrets — fetched via on-demand DB query.
    access_key = _storage("access_key")
    secret_key = await _storage_secret("secret_key")

    if not access_key or not secret_key:
        logger.warning(
            "[STORAGE] No object-store credentials in app_settings "
            "(storage_access_key / storage_secret_key) — skipping upload"
        )
        return None

    endpoint_url = _storage("endpoint")
    bucket = _storage("bucket")
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

        public_url = _storage("public_url") or site_config.get("r2_public_url", "")
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


async def upload_podcast_episode(post_id: str) -> str | None:
    """Upload a podcast episode MP3 to R2. Returns public URL or None.

    Uses versioned path (podcast_cdn_version) for cache-busting.

    On success, also stamps the public URL onto the corresponding
    ``media_assets`` row so cleanup / retention / cost-attribution
    sees the live URL (Glad-Labs/poindexter#161).
    """
    try:
        from services.site_config import site_config
        cdn_ver = site_config.get("podcast_cdn_version", "v2")
    except Exception:
        cdn_ver = "v2"
    podcast_dir = Path(os.path.expanduser("~")) / ".poindexter" / "podcast"
    mp3_path = podcast_dir / f"{post_id}.mp3"
    if not mp3_path.exists():
        return None
    url = await upload_to_r2(str(mp3_path), f"podcast/{cdn_ver}/{post_id}.mp3")
    if url:
        await _update_media_asset_url(
            post_id=post_id,
            asset_type="podcast",
            storage_path=str(mp3_path),
            public_url=url,
        )
    return url


async def upload_video_episode(post_id: str) -> str | None:
    """Upload a video episode MP4 to R2. Returns public URL or None.

    On success, also stamps the public URL onto the corresponding
    ``media_assets`` row so cleanup / retention / cost-attribution
    sees the live URL (Glad-Labs/poindexter#161).
    """
    video_dir = Path(os.path.expanduser("~")) / ".poindexter" / "video"
    mp4_path = video_dir / f"{post_id}.mp4"
    if not mp4_path.exists():
        return None
    url = await upload_to_r2(str(mp4_path), f"video/{post_id}.mp4")
    if url:
        # Legacy path stores rows as asset_type='video' (no _long/_short).
        await _update_media_asset_url(
            post_id=post_id,
            asset_type="video",
            storage_path=str(mp4_path),
            public_url=url,
        )
    return url


async def _update_media_asset_url(
    *,
    post_id: str,
    asset_type: str,
    storage_path: str,
    public_url: str,
) -> None:
    """Best-effort: stamp the public URL onto an existing media_assets row.

    Used after a successful object-storage upload to keep the row in
    sync with the live URL (Glad-Labs/poindexter#161). Failures log
    and never propagate — the upload itself was the operator-visible
    success.

    Reads the asyncpg pool from the module-level ``site_config``
    singleton (set by ``site_config.load(pool)`` during app startup);
    no-ops cleanly when the pool is None (test environments, fresh
    boots before lifespan completes).
    """
    pool = getattr(site_config, "_pool", None)
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE media_assets
                   SET url = $4,
                       storage_provider = 'cloudflare_r2',
                       updated_at = NOW()
                 WHERE post_id::text = $1
                   AND type = $2
                   AND storage_path = $3
                """,
                str(post_id), asset_type, storage_path, public_url,
            )
    except Exception as exc:
        logger.debug(
            "[STORAGE] media_assets URL update failed (post_id=%s type=%s): %s",
            post_id, asset_type, exc,
        )
