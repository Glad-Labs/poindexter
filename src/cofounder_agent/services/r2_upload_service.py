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

import io
import os
from pathlib import Path

from services.logger_config import get_logger
from services.site_config import SiteConfig

logger = get_logger(__name__)

# Cache-Control header value applied to all image uploads so CDN and
# browsers cache them for a year. Images are content-addressed by UUID
# key so this is safe (a new image always gets a new key).
_IMAGE_CACHE_CONTROL = "public, max-age=31536000, immutable"

# Image MIME types that should be converted to WebP before upload.
_CONVERT_TO_WEBP_TYPES = {"image/png", "image/jpeg"}
_WEBP_QUALITY = 80


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


def _convert_to_webp(
    path: Path, *, max_width: int = 1920, max_height: int = 1920,
) -> "io.BytesIO | None":
    """Convert an image file to WebP at quality 80 and return an in-memory buffer.

    Downscaled to fit within ``(max_width, max_height)`` first if larger —
    aspect ratio preserved, never upscaled (``Image.thumbnail`` semantics).
    Defaults (1920x1920) match the largest ``deviceSizes`` entry in the
    public site's Next.js Image config, so this never discards resolution
    the responsive image pipeline could actually use (poindexter storage
    optimization follow-up).

    Returns ``None`` when Pillow is not installed or conversion fails —
    callers fall back to uploading the original file.  Never raises.
    """
    try:
        from PIL import Image  # type: ignore[import]

        with Image.open(path) as img:
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            # Convert palette/P mode images to RGBA first so WebP saves them
            # correctly; convert other non-RGB(A) modes to RGB.
            if img.mode in ("P", "RGBA"):
                img = img.convert("RGBA")  # type: ignore[assignment]
            elif img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")  # type: ignore[assignment]
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=_WEBP_QUALITY, method=4)
            buf.seek(0)
            return buf
    except Exception as exc:  # noqa: BLE001 — conversion is best-effort
        from utils.findings import emit_finding

        logger.debug("[STORAGE] WebP conversion failed for %s: %s", path.name, exc)
        emit_finding(
            source="r2_upload_service",
            kind="webp_conversion_fallback",
            title=f"WebP conversion fell back to original for {path.name}",
            body=(
                f"Pillow WebP conversion failed for {path.name}: {exc}. Uploaded "
                "the original file instead — non-blocking, but a systematic "
                "failure means every image ships un-optimised."
            ),
            severity="info",
            dedup_key=f"webp_conversion_fallback:{path.suffix}",
        )
        return None


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

    def _image_public_url_base(self) -> str:
        """Return the best base URL for image object keys.

        Preference order:
        1. ``storage_image_custom_domain`` — operator-configured custom
           domain (e.g. ``https://images.gladlabs.io``). Set this to
           serve images from a custom vanity domain instead of the
           rate-limited ``*.r2.dev`` public bucket URL.
        2. ``storage_public_url`` — the generic bucket public URL.

        Returns empty string when neither is configured.
        """
        custom = self._site_config.get("storage_image_custom_domain", "")
        if custom:
            return custom.rstrip("/")
        return self._storage("public_url").rstrip("/")

    async def upload_to_r2(
        self,
        local_path: str,
        r2_key: str,
        content_type: str | None = None,
    ) -> str | None:
        """Upload a file to Cloudflare R2 and return its public URL.

        Image files (PNG/JPEG) are converted to WebP at quality 80 before
        upload. The R2 object is tagged with
        ``Cache-Control: public, max-age=31536000, immutable`` so CDN and
        browsers cache it for a year. Image URLs use the custom domain
        from ``storage_image_custom_domain`` when set, falling back to the
        r2.dev public URL.

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

        # Convert PNG/JPEG images to WebP@80 before upload, downscaling
        # first if they exceed the configured max dimensions.
        # This saves ~60-70% bandwidth vs 1.5–1.7 MB PNGs (poindexter#732).
        upload_path = str(path)
        upload_content_type = content_type
        upload_r2_key = r2_key
        _webp_buf: io.BytesIO | None = None
        if content_type in _CONVERT_TO_WEBP_TYPES:
            max_width = self._site_config.get_int("storage_image_max_width", 1920)
            max_height = self._site_config.get_int("storage_image_max_height", 1920)
            _webp_buf = _convert_to_webp(
                path, max_width=max_width, max_height=max_height,
            )
            if _webp_buf is not None:
                upload_content_type = "image/webp"
                # Rewrite the R2 key extension so the object has the right
                # suffix in the bucket (avoids serving WebP under a .png key).
                stem = r2_key.rsplit(".", 1)[0] if "." in r2_key else r2_key
                upload_r2_key = f"{stem}.webp"
                logger.debug(
                    "[STORAGE] Converted %s → WebP (quality %d), key: %s",
                    path.name, _WEBP_QUALITY, upload_r2_key,
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

            extra_args: dict = {
                "ContentType": upload_content_type,
            }
            # Apply immutable Cache-Control to images so CDN and browsers
            # cache them for a full year (poindexter#732).
            if upload_content_type.startswith("image/"):
                extra_args["CacheControl"] = _IMAGE_CACHE_CONTROL

            if _webp_buf is not None:
                # Upload from in-memory buffer (avoids a temp file round-trip).
                size = _webp_buf.getbuffer().nbytes
                logger.info(
                    "[STORAGE] Uploading %s → %s (%s, %.1fMB, WebP)",
                    path.name, upload_r2_key, upload_content_type,
                    size / 1024 / 1024,
                )
                s3.upload_fileobj(_webp_buf, bucket, upload_r2_key, ExtraArgs=extra_args)
            else:
                size = path.stat().st_size
                logger.info(
                    "[STORAGE] Uploading %s → %s (%s, %.1fMB)",
                    path.name, upload_r2_key, upload_content_type,
                    size / 1024 / 1024,
                )
                s3.upload_file(upload_path, bucket, upload_r2_key, ExtraArgs=extra_args)

            # Prefer the custom image domain for image keys; fall back to the
            # generic public URL for non-image objects (audio, video, JSON).
            if upload_content_type.startswith("image/"):
                base_url = self._image_public_url_base()
            else:
                base_url = self._storage("public_url").rstrip("/")

            if not base_url:
                logger.warning(
                    "[STORAGE] storage_public_url not set — can't construct "
                    "public link for %s", upload_r2_key,
                )
                return None
            url = f"{base_url}/{upload_r2_key}"
            logger.info("[STORAGE] Uploaded: %s", url)
            return url

        except ImportError:
            logger.warning("[STORAGE] boto3 not installed — cannot upload")
            return None
        except Exception as e:
            logger.exception("[STORAGE] Upload failed for %s: %s", r2_key, e)
            return None

    async def _s3_client_and_bucket(self):
        """Build a boto3 S3 client + bucket name from app_settings.

        Returns ``(client, bucket)`` or ``(None, None)`` when credentials /
        config are missing or boto3 isn't installed. Used by ``delete_object``
        and ``list_keys``; ``upload_to_r2`` builds its own client inline and is
        left untouched to keep the publish-critical path unchanged.
        """
        access_key = self._storage("access_key")
        secret_key = await self._storage_secret("secret_key")
        endpoint_url = self._storage("endpoint")
        bucket = self._storage("bucket")
        if not (access_key and secret_key and endpoint_url and bucket):
            logger.warning(
                "[STORAGE] object-store creds/config incomplete — skipping op",
            )
            return None, None
        try:
            import boto3
        except ImportError:
            logger.warning(
                "[STORAGE] boto3 not installed — cannot reach object store",
            )
            return None, None
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )
        return s3, bucket

    async def delete_object(self, key: str) -> bool:
        """Delete an object by key (e.g. ``static/posts/foo.json``).

        Idempotent: S3 ``delete_object`` returns success even when the key is
        already absent, so a double-delete is a no-op. Returns True on
        success, False when creds/config are missing or the call raises.
        """
        s3, bucket = await self._s3_client_and_bucket()
        if not s3:
            return False
        try:
            s3.delete_object(Bucket=bucket, Key=key)
            logger.info("[STORAGE] Deleted: %s", key)
            return True
        except Exception as e:
            logger.exception("[STORAGE] Delete failed for %s: %s", key, e)
            return False

    async def list_keys(self, prefix: str) -> list[str]:
        """List every object key under ``prefix`` (paginated via
        ``ContinuationToken``). Returns [] on error or missing config."""
        s3, bucket = await self._s3_client_and_bucket()
        if not s3:
            return []
        keys: list[str] = []
        try:
            token: str | None = None
            while True:
                kwargs: dict = {"Bucket": bucket, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                resp = s3.list_objects_v2(**kwargs)
                for obj in resp.get("Contents") or []:
                    keys.append(obj["Key"])
                if resp.get("IsTruncated") and resp.get("NextContinuationToken"):
                    token = resp["NextContinuationToken"]
                else:
                    break
            return keys
        except Exception as e:
            logger.exception(
                "[STORAGE] list_keys failed for prefix %s: %s", prefix, e,
            )
            return []

    async def get_json(self, r2_key: str) -> dict | None:
        """Download an object from R2 via S3 API and return its parsed JSON.

        Uses the same boto3/S3 transport as uploads so it works inside the
        Docker container (the public ``pub-*.r2.dev`` CDN URL is not routable
        from private Docker networks; the S3 ``storage_endpoint`` is).

        Returns ``None`` when credentials/config are missing, boto3 isn't
        installed, the key doesn't exist, or any error occurs.
        """
        import json as _json

        s3, bucket = await self._s3_client_and_bucket()
        if not s3:
            return None
        try:
            response = s3.get_object(Bucket=bucket, Key=r2_key)
            content = response["Body"].read().decode("utf-8")
            return _json.loads(content)
        except Exception as e:
            logger.warning("[STORAGE] get_json failed for %s: %s", r2_key, e)
            return None

    async def list_objects(self, prefix: str) -> list[dict]:
        """List objects under ``prefix`` with size + last_modified (paginated).

        Returns ``[{"key": str, "size": int, "last_modified": datetime|None}]``.
        Fail-soft: returns ``[]`` on error or missing config, same contract as
        ``list_keys`` — callers treat ``[]`` as "nothing to do".
        """
        s3, bucket = await self._s3_client_and_bucket()
        if not s3:
            return []
        out: list[dict] = []
        try:
            token: str | None = None
            while True:
                kwargs: dict = {"Bucket": bucket, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                resp = s3.list_objects_v2(**kwargs)
                for obj in resp.get("Contents") or []:
                    out.append(
                        {
                            "key": obj["Key"],
                            "size": int(obj.get("Size", 0)),
                            "last_modified": obj.get("LastModified"),
                        },
                    )
                if resp.get("IsTruncated") and resp.get("NextContinuationToken"):
                    token = resp["NextContinuationToken"]
                else:
                    break
            return out
        except Exception as e:
            logger.exception(
                "[STORAGE] list_objects failed for prefix %s: %s", prefix, e,
            )
            return []

    async def get_object_text(self, r2_key: str) -> str | None:
        """Download an object and return its decoded text (utf-8, replace).

        Returns ``None`` when creds/config are missing, the key is absent, or any
        error occurs. Used to read feed XML for the media reaper's keep-set.
        """
        s3, bucket = await self._s3_client_and_bucket()
        if not s3:
            return None
        try:
            resp = s3.get_object(Bucket=bucket, Key=r2_key)
            return resp["Body"].read().decode("utf-8", "replace")
        except Exception as e:
            logger.warning(
                "[STORAGE] get_object_text failed for %s: %s", r2_key, e,
            )
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
            # The file IS uploaded to R2, but the row still points at a
            # stale/absent URL — and the site renders from that row, so this
            # surfaces to readers as a broken or outdated image while every log
            # looks healthy. media_assets is not audit_log, so a finding is the
            # right signal here.
            from utils.findings import emit_finding

            emit_finding(
                source="services.r2_upload_service",
                kind="media_asset_url_update_failed",
                title="media_assets URL stamp failed after R2 upload",
                body=(
                    f"Stamping the public URL onto media_assets for post "
                    f"{post_id} (type={asset_type}, path={storage_path}) raised "
                    f"{type(exc).__name__}: {exc}. The upload itself succeeded, "
                    f"so the asset row keeps a stale/absent url and the rendered "
                    f"page can show a broken or outdated image."
                ),
                severity="info",
                dedup_key="media_asset_url_update_failed",
            )
