"""One-shot backfill for Glad-Labs/poindexter#161.

Walks every published post and inserts missing ``media_assets`` rows
for the audio / video / image files the pipeline already produced.

Pre-fix, the V0 video stitch Stages were the only producers landing
DB rows. The legacy podcast generator, the legacy slideshow video
path, the featured-image stage, and the inline-image replacer all
wrote files (or registered URLs on ``posts``) without recording
``media_assets`` rows. Cleanup / retention / cost-attribution then
ignored them. This script catches up on every existing post.

What it inserts (per published post):

* ``podcast`` — when ``~/.poindexter/podcast/<post_id>.mp3`` exists
* ``video``   — when ``~/.poindexter/video/<post_id>.mp4`` exists
* ``video_short`` — when ``~/.poindexter/video/<post_id>-short.mp4`` exists
* ``featured_image`` — when ``posts.featured_image_url`` is populated
* ``inline_image``   — one row per ``<img src=...>`` in ``posts.content``

Idempotent: re-runs skip rows that already have a matching
``(post_id, type, storage_path)`` or ``(post_id, type, url)``.

Usage (from repo root)::

    python scripts/backfill-media-assets.py --dry-run    # count, don't insert
    python scripts/backfill-media-assets.py              # apply to live DB
    python scripts/backfill-media-assets.py --post-id <uuid>  # limit to one post

DB connection resolves in the same order as the other backfill
scripts: ``--database-url`` flag → ``POINDEXTER_BRAIN_URL`` /
``GLADLABS_BRAIN_URL`` / ``DATABASE_URL`` env vars → local default.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any

import asyncpg


DEFAULT_DB_URL = (
    "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain"
)


# Filesystem layout — matches services.podcast_service.PODCAST_DIR /
# services.video_service.VIDEO_DIR (without importing the modules so
# the backfill script can run on a checkout that doesn't have the
# full backend Python deps installed).
def _data_root() -> Path:
    override = os.environ.get("POINDEXTER_DATA_ROOT")
    if override:
        return Path(override)
    root_mount = Path("/root/.poindexter")
    if root_mount.is_dir():
        return root_mount
    return Path(os.path.expanduser("~")) / ".poindexter"


PODCAST_DIR = _data_root() / "podcast"
VIDEO_DIR = _data_root() / "video"


# Match <img src="..."> in post HTML/markdown — same alphabet the
# pipeline emits via _inject_html_image (single quotes are not used
# but accepted defensively).
_IMG_SRC_RE = re.compile(r'<img\s+[^>]*src=["\']([^"\']+)["\']', re.IGNORECASE)


def _resolve_db_url(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    for env_key in ("POINDEXTER_BRAIN_URL", "GLADLABS_BRAIN_URL", "DATABASE_URL"):
        val = os.getenv(env_key)
        if val:
            return val
    return DEFAULT_DB_URL


# ---------------------------------------------------------------------------
# Dedupe predicates — match record_media_asset's expected keys
# ---------------------------------------------------------------------------


async def _row_exists_for_path(
    conn: asyncpg.Connection, post_id: str, asset_type: str, storage_path: str,
) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT 1 FROM media_assets
            WHERE post_id = $1::uuid
              AND type = $2
              AND storage_path = $3
            LIMIT 1
            """,
            post_id, asset_type, storage_path,
        )
    )


async def _row_exists_for_url(
    conn: asyncpg.Connection, post_id: str, asset_type: str, public_url: str,
) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT 1 FROM media_assets
            WHERE post_id = $1::uuid
              AND type = $2
              AND url = $3
            LIMIT 1
            """,
            post_id, asset_type, public_url,
        )
    )


# ---------------------------------------------------------------------------
# Insert helpers — match the column set used by record_media_asset
# ---------------------------------------------------------------------------


async def _insert_row(
    conn: asyncpg.Connection,
    *,
    post_id: str,
    asset_type: str,
    storage_path: str,
    public_url: str,
    mime_type: str,
    file_size_bytes: int,
    width: int | None,
    height: int | None,
    duration_ms: int | None,
    provider_plugin: str,
    storage_provider: str,
    metadata_json: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO media_assets (
            type, source, storage_provider, url, storage_path,
            metadata, post_id, provider_plugin,
            width, height, duration_ms, file_size_bytes,
            mime_type, cost_usd, electricity_kwh
        ) VALUES (
            $1, 'backfill', $2, $3, $4,
            $5::jsonb, $6::uuid, $7,
            $8, $9, $10, $11,
            $12, 0, 0
        )
        """,
        asset_type, storage_provider,
        public_url, storage_path,
        metadata_json, post_id, provider_plugin,
        width, height, duration_ms, file_size_bytes,
        mime_type,
    )


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _classify_storage_provider(url: str) -> str:
    if not url:
        return "local"
    if url.startswith("http"):
        if "r2" in url or "cloudflarestorage" in url:
            return "cloudflare_r2"
        return "external"
    if url.startswith("/"):
        return "local"
    return "external"


# ---------------------------------------------------------------------------
# Per-post backfill
# ---------------------------------------------------------------------------


async def _backfill_podcast(
    conn: asyncpg.Connection,
    post_id: str,
    *,
    dry_run: bool,
) -> int:
    mp3 = PODCAST_DIR / f"{post_id}.mp3"
    if not mp3.exists():
        return 0
    storage_path = str(mp3)
    if await _row_exists_for_path(conn, post_id, "podcast", storage_path):
        return 0
    if dry_run:
        return 1
    await _insert_row(
        conn,
        post_id=post_id,
        asset_type="podcast",
        storage_path=storage_path,
        public_url="",
        mime_type="audio/mpeg",
        file_size_bytes=_file_size(mp3),
        width=None, height=None,
        duration_ms=None,
        provider_plugin="tts.edge_tts",
        storage_provider="local",
        metadata_json='{"backfill_source": "filesystem"}',
    )
    return 1


async def _backfill_video(
    conn: asyncpg.Connection,
    post_id: str,
    *,
    dry_run: bool,
) -> int:
    mp4 = VIDEO_DIR / f"{post_id}.mp4"
    if not mp4.exists():
        return 0
    storage_path = str(mp4)
    if await _row_exists_for_path(conn, post_id, "video", storage_path):
        return 0
    # Also dedupe against the V0 names (video_long) — same file, different
    # type label — so we don't double-count when the V0 stitch already
    # ran for this post.
    if await _row_exists_for_path(conn, post_id, "video_long", storage_path):
        return 0
    if dry_run:
        return 1
    await _insert_row(
        conn,
        post_id=post_id,
        asset_type="video",
        storage_path=storage_path,
        public_url="",
        mime_type="video/mp4",
        file_size_bytes=_file_size(mp4),
        width=1920, height=1080,
        duration_ms=None,
        provider_plugin="video.ken_burns_slideshow",
        storage_provider="local",
        metadata_json='{"backfill_source": "filesystem"}',
    )
    return 1


async def _backfill_video_short(
    conn: asyncpg.Connection,
    post_id: str,
    *,
    dry_run: bool,
) -> int:
    mp4 = VIDEO_DIR / f"{post_id}-short.mp4"
    if not mp4.exists():
        return 0
    storage_path = str(mp4)
    if await _row_exists_for_path(conn, post_id, "video_short", storage_path):
        return 0
    if dry_run:
        return 1
    await _insert_row(
        conn,
        post_id=post_id,
        asset_type="video_short",
        storage_path=storage_path,
        public_url="",
        mime_type="video/mp4",
        file_size_bytes=_file_size(mp4),
        width=1080, height=1920,
        duration_ms=None,
        provider_plugin="video.ken_burns_slideshow",
        storage_provider="local",
        metadata_json='{"backfill_source": "filesystem"}',
    )
    return 1


async def _backfill_featured_image(
    conn: asyncpg.Connection,
    post_id: str,
    featured_image_url: str | None,
    *,
    dry_run: bool,
) -> int:
    if not featured_image_url:
        return 0
    if await _row_exists_for_url(
        conn, post_id, "featured_image", featured_image_url,
    ):
        return 0
    if dry_run:
        return 1
    storage_provider = _classify_storage_provider(featured_image_url)
    mime_type = (
        "image/png" if featured_image_url.lower().endswith(".png")
        else "image/jpeg"
    )
    provider_plugin = (
        "image.sdxl"
        if "/sdxl" in featured_image_url or "/featured/" in featured_image_url
        else "image.pexels" if "pexels" in featured_image_url
        else "image.unknown"
    )
    await _insert_row(
        conn,
        post_id=post_id,
        asset_type="featured_image",
        storage_path="",
        public_url=featured_image_url,
        mime_type=mime_type,
        file_size_bytes=0,
        width=None, height=None,
        duration_ms=None,
        provider_plugin=provider_plugin,
        storage_provider=storage_provider,
        metadata_json='{"backfill_source": "posts.featured_image_url"}',
    )
    return 1


async def _backfill_inline_images(
    conn: asyncpg.Connection,
    post_id: str,
    content: str | None,
    *,
    dry_run: bool,
) -> int:
    if not content:
        return 0
    inserted = 0
    seen: set[str] = set()
    for url in _IMG_SRC_RE.findall(content):
        url = url.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        if await _row_exists_for_url(conn, post_id, "inline_image", url):
            continue
        if dry_run:
            inserted += 1
            continue
        storage_provider = _classify_storage_provider(url)
        mime_type = (
            "image/png" if url.lower().endswith(".png")
            else "image/jpeg" if url.lower().endswith((".jpg", ".jpeg"))
            else "image/webp" if url.lower().endswith(".webp")
            else "image/png"
        )
        provider_plugin = (
            "image.sdxl"
            if "/inline/" in url or "/glad-labs-generated-images/" in url
            else "image.pexels" if "pexels" in url
            else "image.unknown"
        )
        await _insert_row(
            conn,
            post_id=post_id,
            asset_type="inline_image",
            storage_path="",
            public_url=url,
            mime_type=mime_type,
            file_size_bytes=0,
            width=None, height=None,
            duration_ms=None,
            provider_plugin=provider_plugin,
            storage_provider=storage_provider,
            metadata_json='{"backfill_source": "posts.content"}',
        )
        inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def _run(
    db_url: str, *, dry_run: bool, single_post_id: str | None,
) -> dict[str, int]:
    counts: dict[str, int] = {
        "podcast": 0,
        "video": 0,
        "video_short": 0,
        "featured_image": 0,
        "inline_image": 0,
        "posts_visited": 0,
    }
    conn = await asyncpg.connect(db_url)
    try:
        if single_post_id:
            rows = await conn.fetch(
                """
                SELECT id::text AS id, content, featured_image_url
                FROM posts WHERE id = $1::uuid
                """,
                single_post_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id::text AS id, content, featured_image_url
                FROM posts
                WHERE status = 'published'
                ORDER BY published_at DESC NULLS LAST
                """,
            )
        for row in rows:
            counts["posts_visited"] += 1
            pid = row["id"]
            counts["podcast"] += await _backfill_podcast(
                conn, pid, dry_run=dry_run,
            )
            counts["video"] += await _backfill_video(
                conn, pid, dry_run=dry_run,
            )
            counts["video_short"] += await _backfill_video_short(
                conn, pid, dry_run=dry_run,
            )
            counts["featured_image"] += await _backfill_featured_image(
                conn, pid, row.get("featured_image_url"), dry_run=dry_run,
            )
            counts["inline_image"] += await _backfill_inline_images(
                conn, pid, row.get("content"), dry_run=dry_run,
            )
    finally:
        await conn.close()
    return counts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Count what would be inserted, don't write.",
    )
    parser.add_argument(
        "--database-url", default=None,
        help="Override DB connection. Defaults to env / local.",
    )
    parser.add_argument(
        "--post-id", default=None,
        help="Limit backfill to a single post UUID.",
    )
    return parser.parse_args()


def _print_summary(counts: dict[str, int], *, dry_run: bool) -> None:
    verb = "would insert" if dry_run else "inserted"
    print()
    print("=" * 60)
    print(f"backfill-media-assets — {'DRY RUN' if dry_run else 'APPLIED'}")
    print("=" * 60)
    print(f"posts visited     : {counts['posts_visited']}")
    print(f"{verb} podcast      : {counts['podcast']}")
    print(f"{verb} video        : {counts['video']}")
    print(f"{verb} video_short  : {counts['video_short']}")
    print(f"{verb} featured_image: {counts['featured_image']}")
    print(f"{verb} inline_image : {counts['inline_image']}")
    total = sum(v for k, v in counts.items() if k != "posts_visited")
    print(f"{verb} TOTAL        : {total}")
    print("=" * 60)


def main() -> int:
    args = _parse_args()
    db_url = _resolve_db_url(args.database_url)
    print(f"DB: {db_url.rsplit('@', 1)[-1]}  (dry_run={args.dry_run})")
    if args.post_id:
        print(f"Single-post mode: {args.post_id}")
    counts = asyncio.run(
        _run(db_url, dry_run=args.dry_run, single_post_id=args.post_id),
    )
    _print_summary(counts, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
