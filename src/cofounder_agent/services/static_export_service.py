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

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Defaults to a fresh env-fallback instance until the lifespan setter
# fires. Tests can either patch this attribute directly or call
# set_site_config() for explicit wiring.
site_config: SiteConfig = SiteConfig()


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global site_config
    site_config = sc


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


async def _upload_json(key: str, data: str, content_type: str = "application/json") -> str | None:
    """Upload a JSON string to R2/S3-compatible storage. Returns public URL or None."""
    from services.r2_upload_service import upload_to_r2

    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        tmp.write(data)
        tmp.close()
        url = await upload_to_r2(tmp.name, f"{_STATIC_PREFIX}/{key}", content_type)
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
    posts.tags[] (drives frontend /tag/[slug] pages — gitea#267)."""
    content_col = ", p.content" if include_content else ""
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT p.id, p.title, p.slug, p.excerpt, p.featured_image_url, p.cover_image_url,
                   p.author_id, p.category_id, p.status, p.seo_title, p.seo_description,
                   p.seo_keywords, p.published_at, p.created_at, p.updated_at,
                   p.distributed_at,
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
    """Fetch a single post by slug with full content + tag slugs (gitea#267)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT p.id, p.title, p.slug, p.content, p.excerpt, p.featured_image_url,
                   p.cover_image_url, p.author_id, p.category_id, p.status,
                   p.seo_title, p.seo_description, p.seo_keywords,
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


async def export_post(pool, slug: str) -> bool:
    """Incremental export — called when a single post is published or updated."""
    site_url = site_config.get("public_site_url") or site_config.require("site_url")
    site_title = site_config.get("site_title") or site_config.require("site_name")
    success = True

    try:
        post = await _fetch_post_by_slug(pool, slug)
        if post:
            url = await _upload_json(f"posts/{slug}.json", _to_json(_post_full(post)))
            if not url:
                success = False
            logger.info("[STATIC_EXPORT] Exported post: %s", slug)
        else:
            logger.warning("[STATIC_EXPORT] Post not found for slug: %s", slug)
            return False

        all_posts = await _fetch_published_posts(pool)
        index_data = {
            "posts": [_post_summary(p) for p in all_posts],
            "total": len(all_posts),
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        url = await _upload_json("posts/index.json", _to_json(index_data))
        if not url:
            success = False

        posts_with_content = await _fetch_published_posts(pool, include_content=True)
        feed = _build_json_feed(posts_with_content[:50], site_url, site_title)
        url = await _upload_json("feed.json", _to_json(feed))
        if not url:
            success = False

        categories = await _fetch_categories(pool)
        sitemap = _build_sitemap(all_posts, categories, site_url)
        url = await _upload_json("sitemap.json", _to_json(sitemap))
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
        url = await _upload_json("manifest.json", _to_json(manifest))
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


async def export_full_rebuild(pool) -> dict[str, Any]:
    """Full rebuild — regenerate ALL static files from scratch."""
    site_url = site_config.get("public_site_url") or site_config.require("site_url")
    site_title = site_config.get("site_title") or site_config.require("site_name")
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
        if not await _upload_json("posts/index.json", _to_json(index_data)):
            errors.append("posts/index.json")

        for post in all_posts:
            key = f"posts/{post['slug']}.json"
            if not await _upload_json(key, _to_json(_post_full(post))):
                errors.append(key)

        cat_data = [{"id": str(c["id"]), "name": c["name"], "slug": c["slug"],
                      "description": c.get("description")} for c in categories]
        if not await _upload_json("categories.json", _to_json(cat_data)):
            errors.append("categories.json")

        author_data = [{"id": str(a["id"]), "name": a.get("name", "Unknown")} for a in authors]
        if not await _upload_json("authors.json", _to_json(author_data)):
            errors.append("authors.json")

        feed = _build_json_feed(all_posts[:50], site_url, site_title)
        if not await _upload_json("feed.json", _to_json(feed)):
            errors.append("feed.json")

        sitemap = _build_sitemap(all_posts, categories, site_url)
        if not await _upload_json("sitemap.json", _to_json(sitemap)):
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
        if not await _upload_json("manifest.json", _to_json(manifest)):
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
