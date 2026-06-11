"""
backfill_image_webp.py — convert existing post images from PNG/JPG to WebP.

Scans posts whose HTML content contains <img src="..."> tags pointing to
`.png` or `.jpg` R2 URLs, downloads each image, converts it to WebP at
quality 80, re-uploads it to the same bucket under a `.webp` key with the
immutable Cache-Control header, then rewrites the URL in the post's content
column and in media_assets rows.

Usage
-----
    # Dry-run (default) — shows what would change, makes no mutations:
    python scripts/backfill_image_webp.py

    # Apply changes:
    python scripts/backfill_image_webp.py --execute

    # Limit to a specific post (by slug or post_id):
    python scripts/backfill_image_webp.py --execute --post-id <uuid>

    # Skip posts whose content references a particular URL pattern:
    python scripts/backfill_image_webp.py --dry-run --filter "r2.dev"

Environment / config
--------------------
The script reads the database URL from the standard resolution chain:
  CLI ``--database-url`` → bootstrap.toml → DATABASE_URL env var.
Object-store credentials come from app_settings rows
(storage_access_key / storage_secret_key / storage_endpoint / storage_bucket /
storage_public_url / storage_image_custom_domain), exactly as the live upload
service does.  Run ``poindexter settings`` to check what's configured.

Notes
-----
- The script never touches posts whose all image URLs already end in ``.webp``.
- On conflict (re-run): re-uploading the same WebP key is idempotent — the
  S3 API simply overwrites the object.  The DB update is also idempotent.
- The script does NOT rebuild the static R2 JSON export.  Run
  ``poindexter rebuild-static`` after applying to push the updated HTML to
  the CDN (or wait for the next scheduled export).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IMG_TAG_RE = re.compile(
    r'<img\s[^>]*src="([^"]+\.(?:png|jpg|jpeg))"[^>]*>',
    re.IGNORECASE,
)

_R2_URL_RE = re.compile(
    r"https://(?:[\w-]+\.r2\.dev|[\w.-]+)/(?:images/[^\s\"'<>]+\.(?:png|jpg|jpeg))",
    re.IGNORECASE,
)

_WEBP_QUALITY = 80
_IMAGE_CACHE_CONTROL = "public, max-age=31536000, immutable"

# ---------------------------------------------------------------------------
# Database URL resolution (mirrors brain.bootstrap logic)
# ---------------------------------------------------------------------------


def _resolve_database_url(cli_url: str | None) -> str:
    if cli_url:
        return cli_url
    # bootstrap.toml
    toml_path = Path(os.path.expanduser("~")) / ".poindexter" / "bootstrap.toml"
    if toml_path.exists():
        try:
            import tomllib  # Python 3.11+

            with open(toml_path, "rb") as f:
                cfg = tomllib.load(f)
            db_url = cfg.get("database_url", "")
            if db_url:
                return db_url
        except ImportError:
            # tomllib absent — try a simple grep
            content = toml_path.read_text(encoding="utf-8")
            m = re.search(r'database_url\s*=\s*"([^"]+)"', content)
            if m:
                return m.group(1)
    env_url = os.environ.get("DATABASE_URL") or os.environ.get("LOCAL_DATABASE_URL")
    if env_url:
        return env_url
    sys.exit(
        "No database URL found.  Pass --database-url or set DATABASE_URL."
    )


# ---------------------------------------------------------------------------
# App-settings helper
# ---------------------------------------------------------------------------


async def _load_storage_settings(conn: Any) -> dict[str, str]:
    """Fetch object-store keys from app_settings (storage_* + legacy fallbacks)."""
    rows = await conn.fetch(
        """
        SELECT key, value
        FROM app_settings
        WHERE key IN (
            'storage_access_key', 'storage_secret_key',
            'storage_endpoint', 'storage_bucket',
            'storage_public_url', 'storage_image_custom_domain',
            'cloudflare_r2_access_key', 'cloudflare_r2_secret_key',
            'cloudflare_r2_endpoint', 'cloudflare_r2_bucket',
            'cloudflare_r2_public_url'
        )
        """
    )
    return {r["key"]: (r["value"] or "") for r in rows}


def _storage(settings: dict[str, str], key: str) -> str:
    return settings.get(f"storage_{key}") or settings.get(
        f"cloudflare_r2_{key}", ""
    )


def _image_base_url(settings: dict[str, str]) -> str:
    custom = settings.get("storage_image_custom_domain", "").rstrip("/")
    if custom:
        return custom
    return _storage(settings, "public_url").rstrip("/")


# ---------------------------------------------------------------------------
# WebP conversion
# ---------------------------------------------------------------------------


def _to_webp(data: bytes) -> bytes | None:
    """Convert raw image bytes to WebP at quality 80. Returns None on failure."""
    try:
        from PIL import Image  # type: ignore[import]

        with Image.open(io.BytesIO(data)) as img:
            if img.mode in ("P", "RGBA"):
                img = img.convert("RGBA")
            elif img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=_WEBP_QUALITY, method=4)
            return buf.getvalue()
    except Exception as exc:
        logger.warning("WebP conversion failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# S3 client builder
# ---------------------------------------------------------------------------


def _build_s3(settings: dict[str, str]) -> Any | None:
    access_key = _storage(settings, "access_key")
    secret_key = _storage(settings, "secret_key")
    endpoint = _storage(settings, "endpoint")
    if not (access_key and secret_key and endpoint):
        logger.error(
            "Missing storage credentials in app_settings "
            "(storage_access_key / storage_secret_key / storage_endpoint)"
        )
        return None
    try:
        import boto3  # type: ignore[import]

        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )
    except ImportError:
        logger.error("boto3 is not installed — cannot upload to R2")
        return None


# ---------------------------------------------------------------------------
# Per-image processing
# ---------------------------------------------------------------------------


def _url_to_r2_key(url: str, base_url: str) -> str | None:
    """Derive the R2 object key from a public URL and the known base URL.

    Returns None when the URL doesn't match the base or can't be parsed.
    """
    url = url.rstrip("/")
    base_url = base_url.rstrip("/")
    if url.startswith(base_url + "/"):
        return url[len(base_url) + 1 :]
    # Try matching just the path fragment for r2.dev URLs where the base
    # may not be configured but the URL structure is known.
    parsed = urlparse(url)
    path = parsed.path.lstrip("/")
    if path.startswith("images/"):
        return path
    return None


async def _process_image_url(
    url: str,
    *,
    s3: Any,
    bucket: str,
    base_url: str,
    dry_run: bool,
    http_client: Any,
) -> str | None:
    """Download, convert, re-upload one image URL.  Returns new URL or None."""
    r2_key = _url_to_r2_key(url, base_url)
    if not r2_key:
        logger.warning("  Cannot derive R2 key for URL: %s — skipping", url)
        return None

    stem = r2_key.rsplit(".", 1)[0] if "." in r2_key else r2_key
    new_key = f"{stem}.webp"
    new_url = f"{base_url}/{new_key}"

    if dry_run:
        logger.info("  [DRY-RUN] %s → %s", url, new_url)
        return new_url

    # Download
    try:
        resp = await http_client.get(url, follow_redirects=True, timeout=30)
        resp.raise_for_status()
        raw_data = resp.content
    except Exception as exc:
        logger.warning("  Download failed for %s: %s", url, exc)
        return None

    # Convert
    webp_data = _to_webp(raw_data)
    if webp_data is None:
        return None

    orig_kb = len(raw_data) / 1024
    new_kb = len(webp_data) / 1024
    logger.info(
        "  %s → WebP  %.1f KB → %.1f KB  (%.0f%% reduction)",
        url.split("/")[-1],
        orig_kb,
        new_kb,
        100 * (1 - new_kb / orig_kb) if orig_kb else 0,
    )

    # Upload
    try:
        s3.put_object(
            Bucket=bucket,
            Key=new_key,
            Body=webp_data,
            ContentType="image/webp",
            CacheControl=_IMAGE_CACHE_CONTROL,
        )
    except Exception as exc:
        logger.warning("  Upload failed for %s: %s", new_key, exc)
        return None

    return new_url


# ---------------------------------------------------------------------------
# Post processing
# ---------------------------------------------------------------------------


async def _process_post(
    post: Any,
    *,
    conn: Any,
    s3: Any,
    bucket: str,
    base_url: str,
    dry_run: bool,
    http_client: Any,
) -> int:
    """Process all images in one post.  Returns count of images converted."""
    post_id = str(post["id"])
    slug = post["slug"] or post_id
    content = post["content"] or ""

    # Find all PNG/JPG image URLs in img tags and elsewhere in the content.
    candidate_urls: list[str] = []
    for m in _IMG_TAG_RE.finditer(content):
        candidate_urls.append(m.group(1))
    # Also catch src= in existing WebP tags that might have had their
    # src rewritten only partially; or bare R2 URLs outside img tags.
    for m in _R2_URL_RE.finditer(content):
        u = m.group(0)
        if u not in candidate_urls:
            candidate_urls.append(u)

    if not candidate_urls:
        return 0

    logger.info("Post %s (%s): %d image(s) to process", post_id[:8], slug, len(candidate_urls))

    new_content = content
    converted = 0
    for url in candidate_urls:
        new_url = await _process_image_url(
            url,
            s3=s3,
            bucket=bucket,
            base_url=base_url,
            dry_run=dry_run,
            http_client=http_client,
        )
        if new_url and new_url != url:
            new_content = new_content.replace(url, new_url)
            converted += 1

            # Update media_assets row if one exists.
            if not dry_run:
                try:
                    await conn.execute(
                        """
                        UPDATE media_assets
                           SET url = $2,
                               mime_type = 'image/webp',
                               updated_at = NOW()
                         WHERE url = $1
                        """,
                        url,
                        new_url,
                    )
                except Exception as exc:
                    logger.debug(
                        "media_assets update failed for %s: %s", url, exc
                    )

    if converted and new_content != content:
        if not dry_run:
            await conn.execute(
                "UPDATE posts SET content = $1, updated_at = NOW() WHERE id = $2::uuid",
                new_content,
                post_id,
            )
            logger.info("  Updated post %s: %d image(s) converted", slug, converted)
        else:
            logger.info(
                "  [DRY-RUN] Would update post %s: %d image(s)", slug, converted
            )

    return converted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(args: argparse.Namespace) -> None:
    try:
        import asyncpg  # type: ignore[import]
    except ImportError:
        sys.exit("asyncpg is not installed — pip install asyncpg")

    try:
        import httpx  # type: ignore[import]
    except ImportError:
        sys.exit("httpx is not installed — pip install httpx")

    database_url = _resolve_database_url(args.database_url)

    logger.info("Connecting to database…")
    conn = await asyncpg.connect(database_url)

    try:
        storage = await _load_storage_settings(conn)
        bucket = _storage(storage, "bucket")
        base_url = _image_base_url(storage)

        if not bucket:
            sys.exit(
                "storage_bucket not configured in app_settings.  "
                "Run: poindexter settings set storage_bucket <name>"
            )
        if not base_url:
            sys.exit(
                "storage_public_url not configured in app_settings.  "
                "Run: poindexter settings set storage_public_url <url>"
            )

        s3 = _build_s3(storage)
        if s3 is None and not args.dry_run:
            sys.exit("Cannot build S3 client — check storage credentials.")

        # Query posts with PNG/JPG image URLs in content.
        where_clauses = [
            "(content LIKE '%\\.png%' OR content LIKE '%.jpg%' OR content LIKE '%.jpeg%')"
        ]
        params: list[Any] = []
        if args.post_id:
            params.append(args.post_id)
            where_clauses.append(f"id::text = ${len(params)}")

        query = f"""
            SELECT id, slug, content
            FROM posts
            WHERE {' AND '.join(where_clauses)}
            ORDER BY published_at DESC
        """
        posts = await conn.fetch(query, *params)
        logger.info(
            "Found %d post(s) with potential PNG/JPG images",
            len(posts),
        )

        if not posts:
            logger.info("Nothing to do.")
            return

        total_converted = 0
        async with httpx.AsyncClient() as http_client:
            for post in posts:
                n = await _process_post(
                    post,
                    conn=conn,
                    s3=s3,
                    bucket=bucket,
                    base_url=base_url,
                    dry_run=args.dry_run,
                    http_client=http_client,
                )
                total_converted += n

        mode = "DRY-RUN" if args.dry_run else "EXECUTED"
        logger.info(
            "[%s] Done. Total images converted: %d across %d post(s).",
            mode,
            total_converted,
            len(posts),
        )
        if args.dry_run:
            logger.info("Re-run with --execute to apply changes.")

    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill post images: convert PNG/JPG → WebP on R2",
    )
    parser.add_argument(
        "--execute",
        dest="dry_run",
        action="store_false",
        default=True,
        help="Apply changes (default: dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Show what would change without making any mutations (default)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override the database URL (default: bootstrap.toml / DATABASE_URL env)",
    )
    parser.add_argument(
        "--post-id",
        default=None,
        help="Only process a specific post by UUID",
    )
    parser.add_argument(
        "--filter",
        dest="url_filter",
        default=None,
        help="Only process image URLs matching this substring",
    )
    parsed = parser.parse_args()
    asyncio.run(main(parsed))
