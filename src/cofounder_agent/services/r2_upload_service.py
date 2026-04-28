"""
Object Store Upload Service — uploads media files to an S3-compatible bucket.

Works with Cloudflare R2, AWS S3, Backblaze B2, MinIO, Wasabi — any
provider that speaks the S3 API. Configuration lives in two places:

1. **Per-store rows** in the ``object_stores`` table (GH-113). Each row
   carries provider/endpoint/bucket/public_url/credentials_ref. New
   destinations are added by inserting a row, not by editing Python.
2. **Legacy ``storage_*`` keys** in ``app_settings`` (kept as a
   back-compat path — the seeded ``primary`` row is built from these
   on first migration boot).

Usage (preferred, declarative):

    from services.r2_upload_service import upload_to_store

    url = await upload_to_store(
        "primary", "/path/to/file.mp3", "podcast/abc123.mp3",
        site_config=site_config,
    )

Usage (back-compat shim — delegates to upload_to_store("primary", ...)):

    from services.r2_upload_service import upload_to_r2

    url = await upload_to_r2(
        "/path/to/file.mp3", "podcast/abc123.mp3", site_config=site_config,
    )
"""

import json
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


# ---------------------------------------------------------------------------
# Declarative path — object_stores table (GH-113)
# ---------------------------------------------------------------------------


class _StoreNotFound(Exception):
    """Internal — raised when looking up a store row that doesn't exist."""


async def _resolve_store_credentials(
    site_config: Any, credentials_ref: str
) -> tuple[str, str]:
    """Resolve {access_key, secret_key} from the credentials_ref pointer.

    Two storage formats are supported, in priority order:

    1. **JSON blob** under ``credentials_ref`` (preferred): set via
       ``poindexter stores set-secret <name>`` — encrypted-at-rest as a
       single JSON string ``{"access_key": "...", "secret_key": "..."}``.
    2. **Legacy split keys** for ``primary`` only: ``storage_access_key``
       (non-secret) + ``storage_secret_key`` (secret) — pre-#113 setup.
       Falls back to ``cloudflare_r2_*``.

    Returns ``(access_key, secret_key)``; either may be empty string if
    not configured (caller decides whether that's fatal).
    """
    blob = await site_config.get_secret(credentials_ref)
    if blob:
        try:
            parsed = json.loads(blob)
            access_key = parsed.get("access_key", "") or ""
            secret_key = parsed.get("secret_key", "") or ""
            if access_key or secret_key:
                return access_key, secret_key
        except (ValueError, TypeError):
            # Treat malformed JSON as "not set" and fall through to legacy
            logger.debug(
                "[STORAGE] credentials_ref=%r is not valid JSON; "
                "falling through to legacy storage_* keys",
                credentials_ref,
            )

    # Legacy fallback — only meaningful for the seeded ``primary`` row.
    access_key = _storage(site_config, "access_key")
    secret_key = await _storage_secret(site_config, "secret_key")
    return access_key, secret_key


async def _lookup_store(site_config: Any, name: str) -> dict[str, Any] | None:
    """Fetch a row from ``object_stores`` by name. Returns ``None`` if
    the row doesn't exist or the table doesn't exist yet (pre-migration).
    """
    pool = getattr(site_config, "_pool", None)
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM object_stores WHERE name = $1", name,
            )
            return dict(row) if row else None
    except Exception as e:
        # Table doesn't exist yet (migration hasn't run) or transient DB
        # error — back-compat path picks up via storage_* settings, no
        # need to fail loudly here.
        logger.debug("[STORAGE] _lookup_store(%s) failed: %s", name, e)
        return None


def _apply_cache_busting(key: str, strategy: str, config: Any) -> str:
    """Mutate the object key based on the row's cache-busting strategy."""
    if strategy == "version_prefix":
        cfg = config if isinstance(config, dict) else {}
        version = cfg.get("version", "v1")
        # Prepend version after the first path segment if present, else
        # at the start. ``podcast/abc.mp3`` → ``podcast/v2/abc.mp3``.
        if "/" in key:
            head, tail = key.split("/", 1)
            return f"{head}/{version}/{tail}"
        return f"{version}/{key}"
    return key


async def _record_upload_outcome(
    site_config: Any,
    store_name: str,
    *,
    success: bool,
    bytes_uploaded: int = 0,
    error: str | None = None,
) -> None:
    """Best-effort observability counter bump on the store row.

    Failure to update the row is non-fatal — we never want metrics
    bookkeeping to break a working upload.
    """
    pool = getattr(site_config, "_pool", None)
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            if success:
                await conn.execute(
                    """
                    UPDATE object_stores
                       SET total_uploads = total_uploads + 1,
                           total_bytes_uploaded = total_bytes_uploaded + $2,
                           last_upload_at = NOW(),
                           last_upload_status = 'success',
                           last_error = NULL
                     WHERE name = $1
                    """,
                    store_name, bytes_uploaded,
                )
            else:
                await conn.execute(
                    """
                    UPDATE object_stores
                       SET total_failures = total_failures + 1,
                           last_upload_at = NOW(),
                           last_upload_status = 'failed',
                           last_error = $2
                     WHERE name = $1
                    """,
                    store_name, (error or "")[:500],
                )
    except Exception as e:
        logger.debug("[STORAGE] _record_upload_outcome(%s) failed: %s", store_name, e)


async def upload_to_store(
    store_name: str,
    local_path: str,
    key: str,
    content_type: str | None = None,
    *,
    site_config: Any,
) -> str | None:
    """Upload a file to the named object_stores row.

    Args:
        store_name: ``name`` column of an ``object_stores`` row, e.g.
            ``"primary"`` or ``"podcast_cdn"``. If no row exists by that
            name AND the name is ``"primary"``, falls back to the legacy
            ``storage_*`` settings — that's what makes the migration
            non-breaking.
        local_path: Absolute path to the local file.
        key: Object key (path within the bucket).
        content_type: MIME type. Auto-detected from extension if omitted.
        site_config: SiteConfig instance (DI — Phase H, GH#95).

    Returns:
        Public URL of the uploaded file, or ``None`` on failure (file
        missing, no credentials, boto3 error, store disabled, etc.).
    """
    path = Path(local_path)
    if not path.exists():
        logger.warning("[STORAGE] File not found: %s", local_path)
        return None

    row = await _lookup_store(site_config, store_name)

    if row is not None:
        if not row.get("enabled"):
            logger.warning(
                "[STORAGE] Store %r exists but is disabled — skipping upload",
                store_name,
            )
            return None
        provider = row.get("provider") or "cloudflare_r2"
        endpoint_url = row.get("endpoint_url") or ""
        bucket = row.get("bucket") or ""
        public_url = row.get("public_url") or ""
        credentials_ref = row.get("credentials_ref") or "storage_credentials"
        strategy = row.get("cache_busting_strategy") or "none"
        # asyncpg returns jsonb as a string, not a dict — parse defensively
        raw_cfg = row.get("cache_busting_config") or {}
        if isinstance(raw_cfg, str):
            try:
                cb_config: dict[str, Any] = json.loads(raw_cfg)
            except (ValueError, TypeError):
                cb_config = {}
        else:
            cb_config = raw_cfg
        access_key, secret_key = await _resolve_store_credentials(
            site_config, credentials_ref
        )
    else:
        # Back-compat fallback: only the ``primary`` name has a legacy
        # path. Other names that don't have rows are configuration bugs.
        if store_name != "primary":
            logger.warning(
                "[STORAGE] No object_stores row named %r and no legacy "
                "fallback for non-'primary' names — skipping upload",
                store_name,
            )
            return None
        provider = "cloudflare_r2"
        endpoint_url = _storage(site_config, "endpoint")
        bucket = _storage(site_config, "bucket")
        public_url = _storage(site_config, "public_url") or site_config.get(
            "r2_public_url", "",
        )
        access_key = _storage(site_config, "access_key")
        secret_key = await _storage_secret(site_config, "secret_key")
        strategy = "none"
        cb_config = {}

    if not access_key or not secret_key:
        logger.warning(
            "[STORAGE] No credentials for store %r — skipping upload "
            "(set via `poindexter stores set-secret %s` or storage_access_key / "
            "storage_secret_key in app_settings)",
            store_name, store_name,
        )
        return None

    if not bucket:
        logger.warning(
            "[STORAGE] Store %r has no bucket configured — skipping upload",
            store_name,
        )
        return None

    munged_key = _apply_cache_busting(key, strategy, cb_config)

    if not content_type:
        content_type = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")

    try:
        import boto3

        # Build the boto3 client. ``endpoint_url=None`` is the AWS S3
        # default (no override); R2/B2/MinIO/Wasabi require a URL. Region
        # is "auto" for R2 and ignored by most other S3-compatible providers.
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )
        size = path.stat().st_size

        logger.info(
            "[STORAGE] Uploading %s → %s/%s (%s, %.1fMB, store=%s, provider=%s)",
            path.name, bucket, munged_key, content_type,
            size / 1024 / 1024, store_name, provider,
        )

        s3.upload_file(
            str(path), bucket, munged_key,
            ExtraArgs={"ContentType": content_type},
        )

        if not public_url:
            logger.warning(
                "[STORAGE] Store %r has no public_url — uploaded but can't "
                "construct public link for %s", store_name, munged_key,
            )
            await _record_upload_outcome(
                site_config, store_name, success=True, bytes_uploaded=size,
            )
            return None
        url = f"{public_url.rstrip('/')}/{munged_key}"
        logger.info("[STORAGE] Uploaded: %s", url)
        await _record_upload_outcome(
            site_config, store_name, success=True, bytes_uploaded=size,
        )
        return url

    except ImportError:
        logger.warning("[STORAGE] boto3 not installed — cannot upload")
        return None
    except Exception as e:
        logger.exception("[STORAGE] Upload failed for %s: %s", munged_key, e)
        await _record_upload_outcome(
            site_config, store_name, success=False, error=str(e),
        )
        return None


# ---------------------------------------------------------------------------
# Back-compat shim — upload_to_r2 delegates to upload_to_store("primary")
# ---------------------------------------------------------------------------


async def upload_to_r2(
    local_path: str,
    r2_key: str,
    content_type: str | None = None,
    *,
    site_config: Any,
) -> str | None:
    """Back-compat shim — delegates to ``upload_to_store("primary", ...)``.

    Pre-existing call sites keep working unchanged. New code should
    prefer ``upload_to_store`` so per-store routing is explicit.
    """
    return await upload_to_store(
        "primary", local_path, r2_key, content_type, site_config=site_config,
    )


async def upload_podcast_episode(post_id: str, *, site_config: Any) -> str | None:
    """Upload a podcast episode MP3 to object storage.

    Looks up the ``podcast_cdn`` store row first; if that doesn't exist
    or is disabled, falls back to ``primary``. The dedicated
    ``podcast_cdn`` row is opt-in — operators insert it when they want
    podcast files in a different bucket from the main media store.

    The cache-busting version prefix that used to be applied here
    (``podcast_cdn_version`` setting) is now an attribute of the
    ``podcast_cdn`` row (``cache_busting_strategy='version_prefix'``,
    ``cache_busting_config={"version": "v2"}``). The fallback path on
    ``primary`` still applies the legacy version prefix to keep
    existing public RSS URLs stable.

    On success, also updates the corresponding ``media_assets`` row so
    its ``url`` reflects the public URL (Glad-Labs/poindexter#161).

    Args:
        post_id: Post UUID (used as episode file stem).
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    podcast_dir = Path(os.path.expanduser("~")) / ".poindexter" / "podcast"
    mp3_path = podcast_dir / f"{post_id}.mp3"
    if not mp3_path.exists():
        return None

    # Prefer a dedicated podcast_cdn row if the operator has wired one
    # up. ``upload_to_store`` checks enabled/credentials internally;
    # if it returns None for a real reason (disabled, missing creds),
    # we don't silently fall back to primary — that would mask the
    # operator's intent.
    podcast_row = await _lookup_store(site_config, "podcast_cdn")
    if podcast_row is not None and podcast_row.get("enabled"):
        url = await upload_to_store(
            "podcast_cdn",
            str(mp3_path),
            f"podcast/{post_id}.mp3",
            site_config=site_config,
        )
    else:
        # Fallback to primary — preserve the legacy ``podcast_cdn_version``
        # behavior so existing RSS feeds don't break.
        try:
            cdn_ver = site_config.get("podcast_cdn_version", "v2")
        except Exception:
            cdn_ver = "v2"
        url = await upload_to_store(
            "primary",
            str(mp3_path),
            f"podcast/{cdn_ver}/{post_id}.mp3",
            site_config=site_config,
        )

    if url:
        await _update_media_asset_url(
            site_config=site_config,
            post_id=post_id,
            asset_type="podcast",
            storage_path=str(mp3_path),
            public_url=url,
        )
    return url


async def upload_video_episode(post_id: str, *, site_config: Any) -> str | None:
    """Upload a video episode MP4 to object storage.

    On success, also updates the corresponding ``media_assets`` row so
    its ``url`` reflects the public URL (Glad-Labs/poindexter#161).

    Args:
        post_id: Post UUID (used as episode file stem).
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    video_dir = Path(os.path.expanduser("~")) / ".poindexter" / "video"
    mp4_path = video_dir / f"{post_id}.mp4"
    if not mp4_path.exists():
        return None

    url = await upload_to_store(
        "primary",
        str(mp4_path),
        f"video/{post_id}.mp4",
        site_config=site_config,
    )
    if url:
        # Legacy path stores rows as asset_type='video' (no _long/_short).
        await _update_media_asset_url(
            site_config=site_config,
            post_id=post_id,
            asset_type="video",
            storage_path=str(mp4_path),
            public_url=url,
        )
    return url


async def _update_media_asset_url(
    *,
    site_config: Any,
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
