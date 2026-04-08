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
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.logger_config import get_logger
from services.site_config import site_config

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


async def _upload_json(key: str, data: str, content_type: str = "application/json") -> Optional[str]:
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
        logger.error("[STATIC_EXPORT] Upload failed for %s: %s", key, e)
        return None
    finally:
        if tmp and os.path.exists(tmp.name):
            os.unlink(tmp.name)


async def _fetch_published_posts(pool, include_content: bool = False) -> List[Dict]:
    """Fetch all published posts, newest first."""
    content_col = ", content" if include_content else ""
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT id, title, slug, excerpt, featured_image_url, cover_image_url,
                   author_id, category_id, status, seo_title, seo_description,
                   seo_keywords, published_at, created_at, updated_at
                   {content_col}
            FROM posts
            WHERE status = 'published'
              AND (published_at IS NULL OR published_at <= NOW())
            ORDER BY published_at DESC NULLS LAST
        """)
    return [dict(r) for r in rows]


async def _fetch_post_by_slug(pool, slug: str) -> Optional[Dict]:
    """Fetch a single post by slug with full content."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, title, slug, content, excerpt, featured_image_url,
                   cover_image_url, author_id, category_id, status,
                   seo_title, seo_description, seo_keywords,
                   published_at, created_at, updated_at
            FROM posts
            WHERE slug = $1 AND status = 'published'
        """, slug)
    return dict(row) if row else None


async def _fetch_categories(pool) -> List[Dict]:
    """Fetch all categories."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, slug, description FROM categories ORDER BY name"
        )
    return [dict(r) for r in rows]


async def _fetch_authors(pool) -> List[Dict]:
    """Fetch all authors."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM authors ORDER BY name")
    return [dict(r) for r in rows]


def _post_summary(post: Dict) -> Dict:
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
        "published_at": post["published_at"].isoformat() if post.get("published_at") else None,
        "created_at": post["created_at"].isoformat() if post.get("created_at") else None,
        "updated_at": post["updated_at"].isoformat() if post.get("updated_at") else None,
    }


def _post_full(post: Dict) -> Dict:
    """Full post dict including content."""
    summary = _post_summary(post)
    summary["content"] = post.get("content", "")
    return summary


def _build_json_feed(posts: List[Dict], site_url: str, site_title: str) -> Dict:
    """Build a JSON Feed 1.1 compliant feed."""
    items = []
    for post in posts[:50]:
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
            item["content_html"] = post["content"]
        items.append(item)

    return {
        "version": "https://jsonfeed.org/version/1.1",
        "title": site_title,
        "home_page_url": site_url,
        "feed_url": f"{site_url}/static/feed.json",
        "items": items,
    }


def _build_sitemap(posts: List[Dict], categories: List[Dict], site_url: str) -> Dict:
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
    site_url = site_config.get("public_site_url", "https://www.gladlabs.io")
    site_title = site_config.get("site_title", "Glad Labs")
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


async def export_full_rebuild(pool) -> Dict[str, Any]:
    """Full rebuild — regenerate ALL static files from scratch."""
    site_url = site_config.get("public_site_url", "https://www.gladlabs.io")
    site_title = site_config.get("site_title", "Glad Labs")
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
