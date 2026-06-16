#!/usr/bin/env python3
"""
DR tool — re-import published posts from R2 static export into a fresh
poindexter_brain database. Used in DB-2 (volume-loss) recovery when no
pg_restore backup is available.

Source: static/posts/index.json + static/posts/<slug>.json on R2/CDN.
Target: `posts` table in poindexter_brain.

Usage:
  python scripts/dr-reimport-posts-from-r2.py \\
      --r2-url https://pub-xxx.r2.dev/static \\
      --database-url postgresql://poindexter:<pw>@localhost:15432/poindexter_brain

If --r2-url is omitted, reads NEXT_PUBLIC_STATIC_URL env var (same source the
Next.js frontend uses). If --database-url is omitted, reads bootstrap.toml via
brain.bootstrap.resolve_database_url().

Idempotent: uses INSERT ... ON CONFLICT (slug) DO UPDATE, so re-running against
a partially-populated DB fills gaps without overwriting newer rows.
"""

import argparse
import asyncio
import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("dr-reimport")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_DEFAULT_R2_URL = "https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static"


def _fetch_json(url: str) -> Any:
    logger.info("  GET %s", url)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def _resolve_r2_url(arg: str | None) -> str:
    if arg:
        return arg.rstrip("/")
    env = os.environ.get("NEXT_PUBLIC_STATIC_URL")
    if env:
        return env.rstrip("/")
    return _DEFAULT_R2_URL


def _resolve_database_url(arg: str | None) -> str:
    if arg:
        return arg
    try:
        from brain.bootstrap import resolve_database_url  # type: ignore[import]

        maybe = resolve_database_url()
        url = asyncio.run(maybe) if asyncio.iscoroutine(maybe) else maybe  # type: ignore[arg-type]
        if url:
            return url
    except Exception:
        pass
    # Last resort: standard env vars
    for key in ("DATABASE_URL", "LOCAL_DATABASE_URL", "POINDEXTER_MEMORY_DSN"):
        val = os.environ.get(key)
        if val:
            return val
    raise SystemExit(
        "Cannot resolve database URL. Pass --database-url or set DATABASE_URL."
    )


_UPSERT_SQL = """
INSERT INTO posts (
    id, title, slug, content, excerpt,
    featured_image_url, featured_image_alt, cover_image_url,
    author_id, category_id,
    status, view_count,
    seo_title, seo_description, seo_keywords,
    published_at, created_at, updated_at
)
VALUES (
    $1::uuid, $2, $3, $4, $5,
    $6, $7, $8,
    $9::uuid, $10::uuid,
    'published', $11,
    $12, $13, $14,
    $15::timestamptz, $16::timestamptz, $17::timestamptz
)
ON CONFLICT (slug) DO UPDATE SET
    title              = EXCLUDED.title,
    content            = EXCLUDED.content,
    excerpt            = EXCLUDED.excerpt,
    featured_image_url = EXCLUDED.featured_image_url,
    featured_image_alt = EXCLUDED.featured_image_alt,
    cover_image_url    = EXCLUDED.cover_image_url,
    seo_title          = EXCLUDED.seo_title,
    seo_description    = EXCLUDED.seo_description,
    seo_keywords       = EXCLUDED.seo_keywords,
    published_at       = EXCLUDED.published_at,
    updated_at         = EXCLUDED.updated_at
WHERE posts.status = 'published'
"""


def _parse_ts(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        return None


async def _reimport(r2_url: str, database_url: str, dry_run: bool) -> None:
    import asyncpg  # type: ignore[import]

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=3)

    index = _fetch_json(f"{r2_url}/posts/index.json")
    # index.json is either a list of post objects or {"posts": [...]}
    posts_meta: list[dict] = index if isinstance(index, list) else index.get("posts", [])
    logger.info("Index contains %d post(s)", len(posts_meta))

    imported = skipped = errors = 0
    now = datetime.now(tz=timezone.utc)

    for meta in posts_meta:
        slug = meta.get("slug", "")
        if not slug:
            logger.warning("  Skipping entry with no slug: %s", meta)
            skipped += 1
            continue

        try:
            full = _fetch_json(f"{r2_url}/posts/{slug}.json")
        except Exception as exc:
            logger.error("  FAILED to fetch posts/%s.json: %s", slug, exc)
            errors += 1
            continue

        post_id = full.get("id") or meta.get("id")
        if not post_id:
            logger.warning("  Skipping %s — no id in JSON", slug)
            skipped += 1
            continue

        args = (
            post_id,
            full.get("title", ""),
            slug,
            full.get("content", ""),
            full.get("excerpt"),
            full.get("featured_image_url"),
            full.get("featured_image_alt"),
            full.get("cover_image_url"),
            full.get("author_id"),
            full.get("category_id"),
            int(full.get("view_count", 0)),
            full.get("seo_title"),
            full.get("seo_description"),
            full.get("seo_keywords"),
            _parse_ts(full.get("published_at")),
            _parse_ts(full.get("created_at")) or now,
            _parse_ts(full.get("updated_at")) or now,
        )

        if dry_run:
            logger.info("  [DRY-RUN] would upsert %s (%s)", slug, post_id)
            imported += 1
            continue

        async with pool.acquire() as conn:
            await conn.execute(_UPSERT_SQL, *args)
        logger.info("  upserted %s", slug)
        imported += 1

    await pool.close()

    logger.info(
        "Done. imported=%d  skipped=%d  errors=%d%s",
        imported,
        skipped,
        errors,
        "  (DRY-RUN — nothing written)" if dry_run else "",
    )
    if errors:
        raise SystemExit(f"{errors} post(s) failed to fetch — check logs above.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r2-url", help="Base R2 static URL (without trailing /)")
    parser.add_argument("--database-url", help="PostgreSQL DSN")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and validate but do not write to the database",
    )
    args = parser.parse_args()

    r2_url = _resolve_r2_url(args.r2_url)
    database_url = _resolve_database_url(args.database_url)

    logger.info("R2 source : %s", r2_url)
    logger.info("DB target : %s", database_url.split("@")[-1])  # hide creds

    asyncio.run(_reimport(r2_url, database_url, args.dry_run))


if __name__ == "__main__":
    main()
