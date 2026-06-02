"""Backfill orchestrator for the gladlabs.io SEO remediation (2026-06-02).

Three modes (``--mode``):

* ``alt`` (default) — re-caption every inline ``<img>`` in published posts
  with the qwen3-vl vision captioner so the alt describes the ACTUAL image,
  not the generation prompt. Also re-captions the featured image into
  ``posts.metadata->>'featured_image_alt'``. Idempotent via the
  ``alt_vision_backfilled_at`` metadata marker (``--force`` to re-run).
* ``seo-desc`` — generate ``seo_description`` (+ ``excerpt`` if empty) for
  published posts that have none (the 27 dev_diary posts shipped with empty
  meta descriptions). Uses the same SEO generator the pipeline uses.
* ``titles`` — strip leaked test-harness batch/debug suffixes from
  ``posts.title`` (e.g. ``(2026-05-11 17:48 batch C #5)``). Slug is left
  unchanged to preserve live URLs / inbound links.

Every mutating mode re-exports the touched post to R2 (``export_post``) and,
at the end of the run, triggers ONE ISR revalidation of the ``posts`` tag —
which every per-slug page also carries — so the live site picks up the
changes (the Next.js data cache has no TTL; see revalidation_service +
web/public-site/lib/posts.ts).

MUST run where the configured Ollama + R2 + revalidate endpoints resolve —
i.e. inside the worker container:

    docker exec poindexter-worker python scripts/backfill-image-alt-vision.py --mode alt --dry-run --limit 3
    docker exec poindexter-worker python scripts/backfill-image-alt-vision.py --mode alt
    docker exec poindexter-worker python scripts/backfill-image-alt-vision.py --mode seo-desc
    docker exec poindexter-worker python scripts/backfill-image-alt-vision.py --mode titles

DB URL resolution mirrors scripts/backfill-alt-text.py.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SERVICES_PARENT = _REPO_ROOT / "src" / "cofounder_agent"
if str(_SERVICES_PARENT) not in sys.path:
    sys.path.insert(0, str(_SERVICES_PARENT))

from services.alt_text import _IMG_ALT_RE  # noqa: E402
from services.image_captioner import caption_image  # noqa: E402
from services.publish_service import sanitize_published_title  # noqa: E402

_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_IMG_SRC_RE = re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"', re.IGNORECASE)

DEFAULT_DB_URL = (
    "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain"
)


def _resolve_db_url(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    for env_key in ("POINDEXTER_BRAIN_URL", "GLADLABS_BRAIN_URL", "DATABASE_URL"):
        val = os.getenv(env_key)
        if val:
            return val
    return DEFAULT_DB_URL


async def _build_site_config(pool):
    """Load a SiteConfig from the pool and wire its _pool (for get_secret +
    dispatch + R2). Mirrors the in-container bootstrap used by tests."""
    from services.site_config import SiteConfig

    sc = SiteConfig()
    await sc.load(pool)
    try:
        sc._pool = pool  # noqa: SLF001 — get_secret / dispatch need the pool
    except Exception as exc:  # noqa: BLE001 — best-effort; continue without the wired pool
        # Tolerated: get_secret / dispatch_complete fall back to their own
        # resolution. Surface it so a silent misconfig doesn't masquerade as
        # "0 changed" later (no silent failure).
        print(
            f"[backfill] warning: could not wire SiteConfig._pool ({exc}); "
            "continuing — get_secret/dispatch will use their own resolution",
            file=sys.stderr,
        )
    return sc


def _clean_for_attr(alt: str) -> str:
    return alt.replace('"', "'").strip()


# ---------------------------------------------------------------------------
# mode: alt
# ---------------------------------------------------------------------------


async def _recaption_content(content: str, topic: str, sc, pool, budget: int) -> tuple[str, int]:
    """Re-caption every inline <img> alt. Returns (new_content, n_changed)."""
    out: list[str] = []
    last = 0
    n = 0
    for m in _IMG_TAG_RE.finditer(content):
        tag = m.group(0)
        out.append(content[last : m.start()])
        src_m = _IMG_SRC_RE.search(tag)
        new_tag = tag
        if src_m and _IMG_ALT_RE.search(tag):
            new_alt = await caption_image(
                image_url=src_m.group(1), topic=topic, budget=budget,
                site_config=sc, pool=pool,
            )
            if new_alt:
                safe = _clean_for_attr(new_alt)
                new_tag = _IMG_ALT_RE.sub(
                    lambda a: f"{a.group(1)}{safe}{a.group(3)}", tag, count=1
                )
                if new_tag != tag:
                    n += 1
        out.append(new_tag)
        last = m.end()
    out.append(content[last:])
    return "".join(out), n


async def mode_alt(conn, sc, pool, *, dry_run, post_id, limit, force) -> set[str]:
    import json

    budget = sc.get_int("alt_text_budget", 120) if sc is not None else 120
    where = "status='published' AND content ~* '<img'"
    params: list = []
    if post_id:
        where += " AND id = $1::uuid"
        params.append(post_id)
    if not force:
        where += " AND (metadata->>'alt_vision_backfilled_at') IS NULL"
    sql = (
        "SELECT id, slug, title, content, "
        "COALESCE(metadata->>'topic', title) AS topic, "
        "featured_image_url "
        f"FROM posts WHERE {where} ORDER BY published_at DESC NULLS LAST"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = await conn.fetch(sql, *params)
    print(f"[alt] {len(rows)} post(s) to process (dry_run={dry_run}, force={force})")

    touched: set[str] = set()
    for r in rows:
        topic = r["topic"] or r["title"]
        new_content, n_inline = await _recaption_content(
            r["content"] or "", topic, sc, pool, budget
        )
        feat_alt = None
        if r["featured_image_url"]:
            feat_alt = await caption_image(
                image_url=r["featured_image_url"], topic=topic, budget=budget,
                site_config=sc, pool=pool,
            )
        changed = (new_content != (r["content"] or "")) or bool(feat_alt)
        print(f"  [{r['slug']}] inline_recaptioned={n_inline} featured_alt={'yes' if feat_alt else 'no'} changed={changed}")
        if dry_run:
            if feat_alt:
                print(f"      featured -> {feat_alt!r}")
            for alt in _IMG_ALT_RE.findall(new_content)[:4]:
                print(f"      inline   -> {alt[1]!r}")
            continue
        if not changed:
            continue
        meta_patch = {"alt_vision_backfilled_at": datetime.now(timezone.utc).isoformat()}
        if feat_alt:
            meta_patch["featured_image_alt"] = _clean_for_attr(feat_alt)
        await conn.execute(
            "UPDATE posts SET content=$1, metadata = COALESCE(metadata,'{}'::jsonb) || $2::jsonb, "
            "updated_at=now() WHERE id=$3",
            new_content, json.dumps(meta_patch), r["id"],
        )
        touched.add(r["slug"])
        await _export(pool, r["slug"], sc)
    return touched


# ---------------------------------------------------------------------------
# mode: seo-desc
# ---------------------------------------------------------------------------


async def mode_seo_desc(conn, sc, pool, *, dry_run, post_id, limit) -> set[str]:
    from services.ai_content_generator import get_content_generator
    from services.seo_content_generator import get_seo_content_generator

    where = "status='published' AND (seo_description IS NULL OR seo_description='')"
    params: list = []
    if post_id:
        where += " AND id = $1::uuid"
        params.append(post_id)
    sql = (
        "SELECT id, slug, title, content, excerpt, "
        "COALESCE(metadata->>'topic', title) AS topic "
        f"FROM posts WHERE {where} ORDER BY published_at DESC NULLS LAST"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = await conn.fetch(sql, *params)
    print(f"[seo-desc] {len(rows)} post(s) missing meta description (dry_run={dry_run})")

    gen = get_seo_content_generator(get_content_generator(site_config=sc), site_config=sc)
    touched: set[str] = set()
    for r in rows:
        topic = r["topic"] or r["title"]
        try:
            assets = gen.metadata_gen.generate_seo_assets(
                title=r["title"], content=r["content"] or "", topic=topic
            )
        except Exception as e:  # noqa: BLE001 — fail-soft per post
            print(f"  [{r['slug']}] SEO gen failed: {e}")
            continue
        desc = (assets or {}).get("meta_description") or ""
        desc = desc[:160].strip()
        if not desc:
            print(f"  [{r['slug']}] no description produced — skipped")
            continue
        excerpt = r["excerpt"] or desc
        print(f"  [{r['slug']}] desc -> {desc!r}")
        if dry_run:
            continue
        await conn.execute(
            "UPDATE posts SET seo_description=$1, excerpt=COALESCE(NULLIF(excerpt,''),$2), "
            "updated_at=now() WHERE id=$3",
            desc, excerpt, r["id"],
        )
        touched.add(r["slug"])
        await _export(pool, r["slug"], sc)
    return touched


# ---------------------------------------------------------------------------
# mode: titles
# ---------------------------------------------------------------------------


async def mode_titles(conn, sc, pool, *, dry_run, post_id, limit) -> set[str]:
    where = "status='published'"
    params: list = []
    if post_id:
        where += " AND id = $1::uuid"
        params.append(post_id)
    sql = f"SELECT id, slug, title, seo_title FROM posts WHERE {where}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = await conn.fetch(sql, *params)
    touched: set[str] = set()
    n_seen = 0
    for r in rows:
        clean = sanitize_published_title(r["title"])
        if clean == (r["title"] or ""):
            continue
        n_seen += 1
        clean_seo = sanitize_published_title(r["seo_title"]) if r["seo_title"] else None
        print(f"  [{r['slug']}] title:\n      - {r['title']!r}\n      + {clean!r}")
        if dry_run:
            continue
        await conn.execute(
            "UPDATE posts SET title=$1, seo_title=COALESCE($2, seo_title), updated_at=now() "
            "WHERE id=$3",
            clean, clean_seo, r["id"],
        )
        touched.add(r["slug"])
        await _export(pool, r["slug"], sc)
    print(f"[titles] {n_seen} contaminated title(s) (dry_run={dry_run}). Slugs left unchanged to preserve URLs.")
    return touched


# ---------------------------------------------------------------------------
# shared: export + revalidate
# ---------------------------------------------------------------------------


async def _export(pool, slug: str, sc) -> None:
    try:
        from services.static_export_service import export_post

        await export_post(pool, slug, site_config=sc)
    except Exception as e:  # noqa: BLE001 — non-fatal; bulk revalidate still runs
        print(f"      [warn] export_post failed for {slug}: {e}")


async def _revalidate_all(sc) -> None:
    """One bulk ISR revalidation — the 'posts' tag is on every per-slug page,
    so this refreshes the index AND every post page."""
    from services.revalidation_service import trigger_nextjs_revalidation

    ok = await trigger_nextjs_revalidation(
        paths=["/", "/archive", "/posts", "/sitemap.xml", "/feed.xml"],
        tags=["posts", "post-index"],
        site_config=sc,
    )
    print(f"[revalidate] posts/post-index tags busted: success={ok}")


async def run(args) -> int:
    db_url = _resolve_db_url(args.database_url)
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=4)
    try:
        sc = await _build_site_config(pool)
        async with pool.acquire() as conn:
            if args.mode == "alt":
                touched = await mode_alt(
                    conn, sc, pool, dry_run=args.dry_run, post_id=args.post_id,
                    limit=args.limit, force=args.force,
                )
            elif args.mode == "seo-desc":
                touched = await mode_seo_desc(
                    conn, sc, pool, dry_run=args.dry_run, post_id=args.post_id,
                    limit=args.limit,
                )
            elif args.mode == "titles":
                touched = await mode_titles(
                    conn, sc, pool, dry_run=args.dry_run, post_id=args.post_id,
                    limit=args.limit,
                )
            else:
                print(f"unknown mode {args.mode!r}")
                return 2
        print(f"[{args.mode}] touched {len(touched)} post(s)")
        if touched and not args.dry_run:
            await _revalidate_all(sc)
        return 0
    finally:
        await pool.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["alt", "seo-desc", "titles"], default="alt")
    p.add_argument("--dry-run", action="store_true", help="Print proposed changes, don't write.")
    p.add_argument("--post-id", help="Limit to a single post UUID.")
    p.add_argument("--limit", type=int, help="Cap the number of posts processed.")
    p.add_argument("--force", action="store_true", help="(alt) re-caption even if already backfilled.")
    p.add_argument("--database-url", help="Override DB URL.")
    args = p.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
