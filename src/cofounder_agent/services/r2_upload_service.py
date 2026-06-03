"""
Object Store Upload Service — uploads media files to an S3-compatible bucket.

Works with Cloudflare R2, AWS S3, Backblaze B2, MinIO, Wasabi — any
provider that speaks the S3 API. Reads all config from app_settings
(DB-first, no env vars) so the operator can swap providers without
touching code.

Constructor-DI migration (PR 4, design doc
``docs/architecture/2026-05-28-site-config-di-migration.md``): the
former module-level ``site_config`` singleton + ``set_site_config``
setter + free functions are gone. Callers construct an instance with
``R2UploadService(site_config=...)`` (typically via
``AppContainer.r2_upload_service``) and call methods on it.

Usage::

    svc = R2UploadService(site_config=site_config)
    url = await svc.upload_to_r2("/path/to/file.mp3", "podcast/abc123.mp3")
"""

import os
from pathlib import Path

from services.logger_config import get_logger
from services.site_config import SiteConfig

logger = get_logger(__name__)


# Content type mapping — module-level so callers / tests that only need
# the lookup table don't have to construct a service.
_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class R2UploadService:
    """S3-compatible object-store uploader (R2 / S3 / B2 / MinIO / Wasabi).

    All settings come from the injected ``SiteConfig`` instance —
    non-secret values via ``site_config.get(...)`` (sync), secrets via
    ``site_config.get_secret(...)`` (async, hits DB each call).
    Reading settings without constructing the class with a SiteConfig
    is a ``TypeError`` at the construction site, by design (fail-loud
    per ``feedback_no_silent_defaults``).
    """

    def __init__(self, *, site_config: SiteConfig) -> None:
        self._site_config = site_config

    # ------------------------------------------------------------------
    # Internal storage-config helpers
    # ------------------------------------------------------------------

    def _storage(self, key: str, default: str = "") -> str:
        """Read a NON-SECRET object-store setting. Prefers the generic
        ``storage_*`` namespace; falls back to the legacy
        ``cloudflare_r2_*`` keys so an in-flight deployment keeps
        working during the rename (#198).
        """
        sc = self._site_config
        return sc.get(f"storage_{key}") or sc.get(
            f"cloudflare_r2_{key}", default,
        )

    async def _storage_secret(self, key: str, default: str = "") -> str:
        """Read a SECRET object-store setting via on-demand DB query.

        Secrets aren't kept in the in-memory site_config cache
        (is_secret=true filters them out of load()). This mirrors how
        revalidate_secret is fetched by routes/revalidate_routes.py.
        """
        sc = self._site_config
        val = await sc.get_secret(f"storage_{key}")
        if val:
            return val
        val = await sc.get_secret(f"cloudflare_r2_{key}")
        return val or default

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def upload_to_r2(
        self,
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
        access_key = self._storage("access_key")
        secret_key = await self._storage_secret("secret_key")

        if not access_key or not secret_key:
            logger.warning(
                "[STORAGE] No object-store credentials in app_settings "
                "(storage_access_key / storage_secret_key) — skipping upload",
            )
            return None

        endpoint_url = self._storage("endpoint")
        bucket = self._storage("bucket")
        if not endpoint_url or not bucket:
            logger.warning(
                "[STORAGE] storage_endpoint or storage_bucket not configured — "
                "skipping upload",
            )
            return None

        # Auto-detect content type
        if not content_type:
            content_type = _CONTENT_TYPES.get(
                path.suffix.lower(), "application/octet-stream",
            )

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

            logger.info(
                "[STORAGE] Uploading %s → %s (%s, %.1fMB)",
                path.name, r2_key, content_type, size / 1024 / 1024,
            )

            s3.upload_file(
                str(path), bucket, r2_key,
                ExtraArgs={"ContentType": content_type},
            )

            public_url = self._storage("public_url")
            if not public_url:
                logger.warning(
                    "[STORAGE] storage_public_url not set — can't construct "
                    "public link for %s", r2_key,
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

    async def upload_podcast_episode(self, post_id: str) -> str | None:
        """Upload a podcast episode MP3 to R2. Returns public URL or None.

        Uses versioned path (podcast_cdn_version) for cache-busting.

        On success, also stamps the public URL onto the corresponding
        ``media_assets`` row so cleanup / retention / cost-attribution
        sees the live URL (Glad-Labs/poindexter#161).
        """
        sc = self._site_config
        try:
            cdn_ver = sc.get("podcast_cdn_version", "v2")
        except Exception:
            cdn_ver = "v2"
        podcast_dir = Path(os.path.expanduser("~")) / ".poindexter" / "podcast"
        mp3_path = podcast_dir / f"{post_id}.mp3"
        if not mp3_path.exists():
            return None
        url = await self.upload_to_r2(
            str(mp3_path), f"podcast/{cdn_ver}/{post_id}.mp3",
        )
        if url:
            await self._update_media_asset_url(
                post_id=post_id,
                asset_type="podcast",
                storage_path=str(mp3_path),
                public_url=url,
            )
        return url

    async def upload_video_episode(self, post_id: str) -> str | None:
        """Upload a video episode MP4 to R2. Returns public URL or None.

        On success, also stamps the public URL onto the corresponding
        ``media_assets`` row so cleanup / retention / cost-attribution
        sees the live URL (Glad-Labs/poindexter#161).
        """
        video_dir = Path(os.path.expanduser("~")) / ".poindexter" / "video"
        mp4_path = video_dir / f"{post_id}.mp4"
        if not mp4_path.exists():
            return None
        url = await self.upload_to_r2(str(mp4_path), f"video/{post_id}.mp4")
        if url:
            # Legacy path stores rows as asset_type='video' (no _long/_short).
            await self._update_media_asset_url(
                post_id=post_id,
                asset_type="video",
                storage_path=str(mp4_path),
                public_url=url,
            )
        return url

    async def _update_media_asset_url(
        self,
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

        Reads the asyncpg pool from ``site_config._pool`` (set by
        ``site_config.load(pool)`` during app startup); no-ops cleanly
        when the pool is None (test environments, fresh boots before
        lifespan completes).
        """
        sc = self._site_config
        pool = getattr(sc, "_pool", None)
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
