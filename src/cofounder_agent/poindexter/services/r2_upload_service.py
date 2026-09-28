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

import asyncio
import io
from pathlib import Path

from poindexter.services.logger_config import get_logger
from poindexter.services.site_config import SiteConfig
from poindexter.utils.exception_format import describe_exception

logger = get_logger(__name__)


def video_episode_key(post_id: str) -> str:
    """The object key a post's long-form video lives at: ``video/{post_id}.mp4``.

    The one place this key is spelled. The video RSS feed advertises it as the
    enclosure fallback (``routes/video_routes.py``) and ``media_distribute``'s
    mirror pass writes it (``services/video_r2_mirror.py``). They used to spell
    it separately, and after the task-keyed cutover (#1460) the feed kept
    advertising a key that nothing wrote any more: every enclosure rendered
    since then 404ed (Glad-Labs/poindexter#1085).

    Unversioned, unlike :func:`podcast_episode_key`: a post holds one long-form
    video (``uniq_media_assets_post_video_type``), so the object is written
    once and there is no stale render to bust. It is also the key the 59
    pre-cutover rows were stamped with, so every enclosure URL a subscriber
    already holds stays valid.
    """
    return f"video/{post_id}.mp4"


def podcast_episode_key(post_id: str, cdn_version: str) -> str:
    """The object key a post's podcast episode lives at: ``podcast/{cdn_version}/{post_id}.mp3``.

    The one place this key is spelled, the podcast twin of
    :func:`video_episode_key`. ``podcast_distribute`` uploads each approved
    episode here and stamps the URL. ``media_reconciliation`` checks the object
    and re-uploads it. The podcast RSS feed and the draft preview fall back to
    it for an approved episode whose row has no URL. The preview used to spell
    it by hand, without the version segment, so every podcast link it built
    pointed at an object nothing wrote (Glad-Labs/poindexter#1089).

    ``cdn_version`` is ``app_settings.podcast_cdn_version``. Bumping it moves
    every episode to a new key, which makes podcast apps re-download episodes
    that were re-recorded.
    """
    return f"podcast/{cdn_version}/{post_id}.mp3"


class ObjectStoreUnavailable(RuntimeError):
    """The object store could not say whether an object exists.

    Raised by :meth:`R2UploadService.object_size` for missing config or
    credentials, a missing ``boto3``, or any error other than "no such key".
    Kept distinct from an absent object on purpose: a caller that read
    "couldn't ask" as "not there" would re-upload, or report a file lost, on
    every network blip.
    """


def _is_not_found(exc: Exception) -> bool:
    """True when a botocore ``ClientError`` means the key does not exist.

    ``head_object`` has no response body, so a missing key surfaces as a bare
    ``404`` code rather than ``NoSuchKey``. Duck-typed on ``exc.response`` so
    this module never imports botocore.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = str((response.get("Error") or {}).get("Code") or "")
    status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return code in {"404", "NoSuchKey", "NotFound"} or status == 404


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
        from poindexter.utils.findings import emit_finding

        logger.debug("[STORAGE] WebP conversion failed for %s: %s", path.name, exc)
        emit_finding(
            source="r2_upload_service",
            kind="webp_conversion_fallback",
            title=f"WebP conversion fell back to original for {path.name}",
            body=(
                f"Pillow WebP conversion failed for {path.name}: {describe_exception(exc)}. Uploaded "
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

    def object_url(self, key: str) -> str:
        """Public URL of a non-image object at ``key``, exactly as
        ``upload_to_r2`` would return it, or ``""`` when no public base is set.

        Lets a caller record the URL of an object that is already in the
        bucket without uploading it again.
        """
        base = self._storage("public_url").rstrip("/")
        return f"{base}/{key}" if base else ""

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

            # The transfer runs in a worker thread. boto3 is blocking, and
            # the worker's event loop also answers /api/health: a 100 MB video
            # takes ~10 s at the measured ~10 MB/s uplink (stalls of 20-50 s
            # happen even on 5 MB podcasts), long enough for the brain to call
            # the API down and restart the worker mid-upload. boto3 clients
            # are thread-safe.
            if _webp_buf is not None:
                # Upload from in-memory buffer (avoids a temp file round-trip).
                size = _webp_buf.getbuffer().nbytes
                logger.info(
                    "[STORAGE] Uploading %s → %s (%s, %.1fMB, WebP)",
                    path.name, upload_r2_key, upload_content_type,
                    size / 1024 / 1024,
                )
                await asyncio.to_thread(
                    s3.upload_fileobj, _webp_buf, bucket, upload_r2_key,
                    ExtraArgs=extra_args,
                )
            else:
                size = path.stat().st_size
                logger.info(
                    "[STORAGE] Uploading %s → %s (%s, %.1fMB)",
                    path.name, upload_r2_key, upload_content_type,
                    size / 1024 / 1024,
                )
                await asyncio.to_thread(
                    s3.upload_file, upload_path, bucket, upload_r2_key,
                    ExtraArgs=extra_args,
                )

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
        config are missing or boto3 isn't installed. Used by every read/delete
        helper (``delete_object``, ``list_keys``, ``list_objects``,
        ``get_json``, ``get_object_text``, ``object_size``). ``upload_to_r2``
        still builds its own client inline, so a change here can't reach the
        publish-critical path.
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

    async def object_size(self, key: str) -> int | None:
        """Size in bytes of the object at ``key``, or ``None`` if there is none.

        An authenticated S3 ``HeadObject``, not a request to the public URL:
        ``pub-*.r2.dev`` is rate-limited and cached, and a public HEAD that
        flakes reads as "object gone" (``media_reconciliation`` re-uploads the
        same delivered podcasts every few hours on exactly that).

        Tri-state on purpose. ``None`` means the store answered "no such key";
        :class:`ObjectStoreUnavailable` means it could not answer (config or
        credentials missing, ``boto3`` absent, network, 403, 5xx). Folding the
        second into the first would make every blip look like a lost file.
        """
        s3, bucket = await self._s3_client_and_bucket()
        if not s3:
            raise ObjectStoreUnavailable(
                "object store not configured (storage_endpoint / storage_bucket "
                "/ storage_access_key / storage_secret_key), or boto3 missing",
            )
        try:
            head = await asyncio.to_thread(s3.head_object, Bucket=bucket, Key=key)
        except Exception as exc:  # noqa: BLE001 — classified below, never swallowed
            if _is_not_found(exc):
                return None
            raise ObjectStoreUnavailable(
                f"HEAD {key} failed: {describe_exception(exc)}",
            ) from exc
        return int(head.get("ContentLength") or 0)
