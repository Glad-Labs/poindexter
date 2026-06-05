"""
Static Export Service — push-only headless CMS output layer.

On publish, generates static JSON files and uploads to any S3-compatible
storage (R2, S3, MinIO, local folder). Any frontend can consume these
files directly — no API server needed for reads.

Output structure on storage:
    static/posts/index.json        — all published posts (metadata, no content)
    static/posts/{slug}.json       — full post with content
    static/feed.json               — JSON Feed 1.1 (https://jsonfeed.org)
    static/categories.json         — all categories
    static/authors.json            — all authors
    static/sitemap.json            — all URLs for sitemap generation
    static/manifest.json           — export metadata (version, timestamp, counts)

Usage:
    from services.static_export_service import export_post, export_full_rebuild

    # Incremental — called on each publish
    await export_post(pool, post_slug)

    # Full rebuild — regenerate everything
    await export_full_rebuild(pool)
"""

import json
import os
import re as _re_markdown
import tempfile
from datetime import datetime, timezone
from typing import Any

from services.logger_config import get_logger
from services.site_config import SiteConfig

# #272 Phase-2d: the module-level ``site_config`` global + ``set_site_config``
# setter were removed. ``export_post`` / ``export_full_rebuild`` / ``_upload_json``
# now REQUIRE a keyword-only ``site_config``. Callers thread it:
# ``publish_service`` passes its resolved ``_sc``; the CMS rebuild route uses
# ``Depends(get_site_config_dependency)``; the reconciliation job reads
# ``config.get("_site_config")``.

logger = get_logger(__name__)

_EXPORT_VERSION = "1.0"
_STATIC_PREFIX = "static"


def _json_serial(obj: Any) -> str:
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    raise TypeError(f"Type {type(obj)} not serializable")


def _to_json(data: Any) -> str:
    """Compact JSON with datetime support."""
    return json.dumps(data, default=_json_serial, ensure_ascii=False)


async def _upload_json(
    key: str,
    data: str,
    content_type: str = "application/json",
    *,
    site_config: SiteConfig,
) -> str | None:
    """Upload a JSON string to R2/S3-compatible storage. Returns public URL or None.

    #272 Phase-2d: ``site_config`` is REQUIRED (keyword-only) — the export
    entrypoints thread one config instance through every call.
    """
    from services.r2_upload_service import R2UploadService

    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        tmp.write(data)
        tmp.close()
        r2 = R2UploadService(site_config=site_config)
        url = await r2.upload_to_r2(tmp.name, f"{_STATIC_PREFIX}/{key}", content_type)
        return url
    except Exception as e:
        logger.exception("[STATIC_EXPORT] Upload failed for %s: %s", key, e)
        return None
    finally:
        if tmp and os.path.exists(tmp.name):
            os.unlink(tmp.name)


async def _fetch_published_posts(pool, include_content: bool = False) -> list[dict]:
    """Fetch all published posts, newest first. Includes tag slugs aggregated
    from the post_tags junction so the static export can populate
    posts.tags[] (drives frontend /tag/[slug] pages — internal tracker)."""
    content_col = ", p.content" if include_content else ""
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT p.id, p.title, p.slug, p.excerpt, p.featured_image_url, p.cover_image_url,
                   p.author_id, p.category_id, p.status, p.seo_title, p.seo_description,
                   p.seo_keywords, p.published_at, p.created_at, p.updated_at,
                   p.distributed_at,
                   p.metadata->>'featured_image_alt' AS featured_image_alt,
                   COALESCE(
                       ARRAY_AGG(t.slug ORDER BY t.slug) FILTER (WHERE t.slug IS NOT NULL),
                       ARRAY[]::text[]
                   ) AS tags
                   {content_col}
            FROM posts p
            LEFT JOIN post_tags pt ON pt.post_id = p.id
            LEFT JOIN tags t ON t.id = pt.tag_id
            WHERE p.status = 'published'
              AND (p.published_at IS NULL OR p.published_at <= NOW())
            GROUP BY p.id
            ORDER BY p.published_at DESC NULLS LAST
        """)  # nosec B608  # content_col is one of two hardcoded literals (", p.content" or "")
    return [dict(r) for r in rows]


async def _fetch_post_by_slug(pool, slug: str) -> dict | None:
    """Fetch a single post by slug with full content + tag slugs (internal tracker)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT p.id, p.title, p.slug, p.content, p.excerpt, p.featured_image_url,
                   p.cover_image_url, p.author_id, p.category_id, p.status,
                   p.seo_title, p.seo_description, p.seo_keywords,
                   p.metadata->>'featured_image_alt' AS featured_image_alt,
                   p.published_at, p.created_at, p.updated_at,
                   COALESCE(
                       ARRAY_AGG(t.slug ORDER BY t.slug) FILTER (WHERE t.slug IS NOT NULL),
                       ARRAY[]::text[]
                   ) AS tags
            FROM posts p
            LEFT JOIN post_tags pt ON pt.post_id = p.id
            LEFT JOIN tags t ON t.id = pt.tag_id
            WHERE p.slug = $1 AND p.status = 'published'
            GROUP BY p.id
        """, slug)
    return dict(row) if row else None


async def _fetch_categories(pool) -> list[dict]:
    """Fetch all categories."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, slug, description FROM categories ORDER BY name"
        )
    return [dict(r) for r in rows]


async def _fetch_authors(pool) -> list[dict]:
    """Fetch all authors."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM authors ORDER BY name")
    return [dict(r) for r in rows]


def _post_summary(post: dict) -> dict:
    """Strip content from a post dict for listing responses."""
    return {
        "id": str(post["id"]),
        "title": post["title"],
        "slug": post["slug"],
        "excerpt": post.get("excerpt"),
        "featured_image_url": post.get("featured_image_url") or post.get("cover_image_url"),
        "cover_image_url": post.get("cover_image_url"),
        # Vision-generated alt for the featured image (posts.metadata) — the
        # frontend uses it for og:image:alt, falling back to the post title.
        "featured_image_alt": post.get("featured_image_alt"),
        "author_id": str(post["author_id"]) if post.get("author_id") else None,
        "category_id": str(post["category_id"]) if post.get("category_id") else None,
        "status": post.get("status", "published"),
        "seo_title": post.get("seo_title"),
        "seo_description": post.get("seo_description"),
        "seo_keywords": post.get("seo_keywords"),
        "tags": list(post.get("tags") or []),
        "published_at": post["published_at"].isoformat() if post.get("published_at") else None,
        "created_at": post["created_at"].isoformat() if post.get("created_at") else None,
        "updated_at": post["updated_at"].isoformat() if post.get("updated_at") else None,
        "distributed_at": post["distributed_at"].isoformat() if post.get("distributed_at") else None,
    }


# Cheap heuristic: any of these markers means the content has markdown
# that MUST be converted even when the first character is `<` (a leading
# <img> tag followed by markdown body was shipping raw `##` / `**` to
# the frontend — #198 follow-up).
_MARKDOWN_MARKER_RE = _re_markdown.compile(
    r"(?m)(?:"
    r"^\#{1,6}\s"              # headers
    r"|\*\*[^*\n]{1,200}\*\*"   # bold
    r"|```"                     # code fence
    r"|^\s*[-*+]\s+\w"          # bulleted list
    r"|\[[^\]]+\]\([^)]+\)"    # markdown link
    r")"
)


def _markdown_to_html(content: str) -> str:
    """Convert markdown content to HTML for static export.

    Converts mixed content (leading HTML + markdown body) correctly —
    python-markdown passes HTML tags through unmodified. Only bypasses
    conversion when the content is pure HTML with no markdown markers.
    """
    if not content:
        return ""
    stripped = content.strip()
    # Only skip when the content has NO markdown markers anywhere.
    if stripped.startswith("<") and not _MARKDOWN_MARKER_RE.search(stripped):
        return content
    try:
        import markdown as md
        return md.markdown(
            stripped,
            extensions=["extra", "codehilite", "sane_lists", "smarty"],
            output_format="html",
        )
    except Exception:
        logger.warning("[STATIC_EXPORT] markdown conversion failed, returning raw content")
        return content


def _post_full(post: dict) -> dict:
    """Full post dict including content (converted to HTML)."""
    summary = _post_summary(post)
    summary["content"] = _markdown_to_html(post.get("content", ""))
    return summary


def _build_json_feed(posts: list[dict], site_url: str, site_title: str) -> dict:
    """Build a JSON Feed 1.1 compliant feed.

    Only includes posts with distributed_at set AND published after the
    feed cutoff (2026-04-12). Originally introduced to stop the legacy
    dlvr.it RSS bridge from re-distributing old/migrated posts; kept in
    place after GH-36 so any remaining RSS consumers (and our own direct
    Bluesky / Mastodon adapters) don't see historical content as "new".
    """
    from datetime import datetime, timezone
    FEED_CUTOFF = datetime(2026, 4, 12, tzinfo=timezone.utc)
    feed_posts = [
        p for p in posts
        if p.get("distributed_at")
        and p.get("published_at")
        and p["published_at"] >= FEED_CUTOFF
    ]
    items = []
    for post in feed_posts[:50]:
        item = {
            "id": f"{site_url}/posts/{post['slug']}",
            "url": f"{site_url}/posts/{post['slug']}",
            "title": post["title"],
            "summary": post.get("excerpt") or post.get("seo_description") or "",
            "date_published": post["published_at"].isoformat() if post.get("published_at") else None,
            "date_modified": post["updated_at"].isoformat() if post.get("updated_at") else None,
        }
        if post.get("featured_image_url"):
            item["image"] = post["featured_image_url"]
        if post.get("content"):
            item["content_html"] = _markdown_to_html(post["content"])
        items.append(item)

    return {
        "version": "https://jsonfeed.org/version/1.1",
        "title": site_title,
        "home_page_url": site_url,
        "feed_url": f"{site_url}/static/feed.json",
        "items": items,
    }


def _build_sitemap(posts: list[dict], categories: list[dict], site_url: str) -> dict:
    """Build sitemap data (URLs + last modified dates)."""
    urls = [
        {"url": site_url, "lastmod": datetime.now(timezone.utc).isoformat(), "priority": 1.0},
        {"url": f"{site_url}/posts", "lastmod": datetime.now(timezone.utc).isoformat(), "priority": 0.8},
        {"url": f"{site_url}/archive", "lastmod": datetime.now(timezone.utc).isoformat(), "priority": 0.7},
    ]
    for cat in categories:
        urls.append({
            "url": f"{site_url}/category/{cat['slug']}",
            "lastmod": datetime.now(timezone.utc).isoformat(),
            "priority": 0.6,
        })
    for post in posts:
        urls.append({
            "url": f"{site_url}/posts/{post['slug']}",
            "lastmod": post["updated_at"].isoformat() if post.get("updated_at") else None,
            "priority": 0.9,
        })
    return {"urls": urls, "total": len(urls)}


# ---------------------------------------------------------------------------
# Takedown / orphan cleanup (Glad-Labs/glad-labs-stack#1146)
#
# When a post leaves the published set (rejected / archived / deleted), its
# static/posts/<slug>.json on storage and its prerendered /posts/<slug> page
# linger as a stale soft-404 (HTTP 200 + the framework's auto-noindex), which
# Google files under "Excluded by 'noindex'" instead of dropping as a 404.
# Retiring a slug deletes the R2 JSON and busts the ISR cache so the next
# request renders fresh -> notFound() -> a true 404.
# ---------------------------------------------------------------------------

# static/posts/<slug>.json -> <slug>  (anchored so it won't match nested keys)
_POST_JSON_KEY_RE = _re_markdown.compile(r"(?:^|/)posts/([^/]+)\.json$")


async def _delete_json(key: str, *, site_config: SiteConfig) -> bool:
    """Delete a static JSON object from storage. Mirror of ``_upload_json``."""
    from services.r2_upload_service import R2UploadService

    return await R2UploadService(site_config=site_config).delete_object(
        f"{_STATIC_PREFIX}/{key}",
    )


async def _retire_slug(slug: str, *, site_config: SiteConfig) -> None:
    """Remove a de-published post's static JSON and bust its ISR cache.

    Order matters: delete the R2 JSON FIRST, then revalidate. The public
    page reads its content from R2, so revalidating before removal would just
    re-render the still-present content as a fresh 200. Never raises — a
    cleanup failure must not break a rebuild/sweep.
    """
    await _delete_json(f"posts/{slug}.json", site_config=site_config)
    try:
        from services.revalidation_service import trigger_isr_revalidate

        await trigger_isr_revalidate(slug, site_config=site_config)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[STATIC_EXPORT] revalidate after retiring %s failed: %s", slug, e,
        )


async def _list_exported_post_slugs(*, site_config: SiteConfig) -> list[str]:
    """List slugs that currently have a ``static/posts/<slug>.json`` on
    storage (excludes ``index.json``)."""
    from services.r2_upload_service import R2UploadService

    keys = await R2UploadService(site_config=site_config).list_keys(
        f"{_STATIC_PREFIX}/posts/",
    )
    slugs: list[str] = []
    for key in keys:
        match = _POST_JSON_KEY_RE.search(key)
        if match and match.group(1) != "index":
            slugs.append(match.group(1))
    return slugs


async def _sweep_orphan_post_jsons(
    published_slugs: set[str], *, site_config: SiteConfig,
) -> list[str]:
    """Retire every exported post slug NOT in ``published_slugs``.

    Returns the list of retired slugs. Shared by ``export_full_rebuild`` and
    the ``static_export_orphan_sweep`` janitor job.
    """
    exported = await _list_exported_post_slugs(site_config=site_config)
    retired: list[str] = []
    for slug in exported:
        if slug not in published_slugs:
            await _retire_slug(slug, site_config=site_config)
            retired.append(slug)
    return retired


async def export_post(
    pool,
    slug: str,
    *,
    site_config: SiteConfig,
) -> bool:
    """Incremental export — called when a single post is published or updated.

    #272 Phase-2d: ``site_config`` is REQUIRED (keyword-only) and threaded
    through every ``_upload_json`` call so one config instance flows through
    the export.
    """
    _sc = site_config

    site_url = _sc.get("public_site_url") or _sc.require("site_url")
    site_title = _sc.get("site_title") or _sc.require("site_name")
    success = True

    try:
        post = await _fetch_post_by_slug(pool, slug)
        if post:
            url = await _upload_json(f"posts/{slug}.json", _to_json(_post_full(post)), site_config=_sc)
            if not url:
                success = False
            logger.info("[STATIC_EXPORT] Exported post: %s", slug)
        else:
            # Slug is no longer published (rejected / archived / deleted).
            # Retire its static JSON + bust the ISR cache so /posts/<slug>
            # returns a true 404 instead of a stale soft-404 ghost (#1146),
            # then fall through to rebuild the index/feed/sitemap so the slug
            # also drops from the listings.
            logger.info(
                "[STATIC_EXPORT] Slug %s no longer published — retiring its "
                "static export", slug,
            )
            await _retire_slug(slug, site_config=_sc)

        # Single fetch WITH content — the index/sitemap derive content-stripped
        # summaries in-memory (_post_summary ignores the content column), and the
        # feed slices the newest 50 below. Avoids a second full posts JOIN/GROUP BY
        # scan per publish (#620).
        all_posts = await _fetch_published_posts(pool, include_content=True)
        index_data = {
            "posts": [_post_summary(p) for p in all_posts],
            "total": len(all_posts),
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        url = await _upload_json("posts/index.json", _to_json(index_data), site_config=_sc)
        if not url:
            success = False

        feed = _build_json_feed(all_posts[:50], site_url, site_title)
        url = await _upload_json("feed.json", _to_json(feed), site_config=_sc)
        if not url:
            success = False

        categories = await _fetch_categories(pool)
        sitemap = _build_sitemap(all_posts, categories, site_url)
        url = await _upload_json("sitemap.json", _to_json(sitemap), site_config=_sc)
        if not url:
            success = False

        manifest = {
            "version": _EXPORT_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "post_count": len(all_posts),
            "category_count": len(categories),
            "site_url": site_url,
            "last_published_slug": slug,
        }
        url = await _upload_json("manifest.json", _to_json(manifest), site_config=_sc)
        if not url:
            success = False

        logger.info(
            "[STATIC_EXPORT] Incremental export complete — %d posts, triggered by %s",
            len(all_posts), slug,
        )
        return success

    except Exception as e:
        logger.error("[STATIC_EXPORT] Incremental export failed: %s", e, exc_info=True)
        return False


async def export_full_rebuild(
    pool,
    *,
    site_config: SiteConfig,
) -> dict[str, Any]:
    """Full rebuild — regenerate ALL static files from scratch.

    #272 Phase-2d: ``site_config`` is REQUIRED (keyword-only) and threaded
    through every ``_upload_json`` call so one config instance flows through
    the rebuild.
    """
    _sc = site_config

    site_url = _sc.get("public_site_url") or _sc.require("site_url")
    site_title = _sc.get("site_title") or _sc.require("site_name")
    errors = []

    try:
        all_posts = await _fetch_published_posts(pool, include_content=True)
        categories = await _fetch_categories(pool)
        authors = await _fetch_authors(pool)

        index_data = {
            "posts": [_post_summary(p) for p in all_posts],
            "total": len(all_posts),
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        if not await _upload_json("posts/index.json", _to_json(index_data), site_config=_sc):
            errors.append("posts/index.json")

        for post in all_posts:
            key = f"posts/{post['slug']}.json"
            if not await _upload_json(key, _to_json(_post_full(post)), site_config=_sc):
                errors.append(key)

        # Retire any per-post JSON whose slug is no longer published — a
        # leftover from a takedown that would otherwise serve a stale
        # soft-404 (#1146). Best-effort: never let cleanup fail the rebuild.
        try:
            retired = await _sweep_orphan_post_jsons(
                {p["slug"] for p in all_posts}, site_config=_sc,
            )
            if retired:
                logger.info(
                    "[STATIC_EXPORT] Retired %d orphaned post JSON(s): %s",
                    len(retired), ", ".join(sorted(retired)[:20]),
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("[STATIC_EXPORT] orphan sweep failed: %s", e)

        cat_data = [{"id": str(c["id"]), "name": c["name"], "slug": c["slug"],
                      "description": c.get("description")} for c in categories]
        if not await _upload_json("categories.json", _to_json(cat_data), site_config=_sc):
            errors.append("categories.json")

        author_data = [{"id": str(a["id"]), "name": a.get("name", "Unknown")} for a in authors]
        if not await _upload_json("authors.json", _to_json(author_data), site_config=_sc):
            errors.append("authors.json")

        feed = _build_json_feed(all_posts[:50], site_url, site_title)
        if not await _upload_json("feed.json", _to_json(feed), site_config=_sc):
            errors.append("feed.json")

        sitemap = _build_sitemap(all_posts, categories, site_url)
        if not await _upload_json("sitemap.json", _to_json(sitemap), site_config=_sc):
            errors.append("sitemap.json")

        manifest = {
            "version": _EXPORT_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "post_count": len(all_posts),
            "category_count": len(categories),
            "author_count": len(authors),
            "site_url": site_url,
            "full_rebuild": True,
        }
        if not await _upload_json("manifest.json", _to_json(manifest), site_config=_sc):
            errors.append("manifest.json")

        total_files = len(all_posts) + 5
        logger.info(
            "[STATIC_EXPORT] Full rebuild complete — %d posts, %d categories, %d authors, %d errors",
            len(all_posts), len(categories), len(authors), len(errors),
        )

        return {
            "success": len(errors) == 0,
            "posts_exported": len(all_posts),
            "categories_exported": len(categories),
            "authors_exported": len(authors),
            "total_files": total_files,
            "errors": errors,
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error("[STATIC_EXPORT] Full rebuild failed: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}
