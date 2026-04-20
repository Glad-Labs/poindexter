"""
Content Management System API Routes

ASYNC REST endpoints for blog content, categories, and tags.
Using pure asyncpg for non-blocking database access.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from middleware.api_token_auth import verify_api_token, verify_api_token_optional
from services.logger_config import get_logger
from utils.error_handler import handle_route_error
from utils.rate_limiter import limiter
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)
router = APIRouter(tags=["cms"])


@router.get("/images/generated/{filename}")
async def serve_generated_image(filename: str):
    """Serve SDXL-generated images from the local output directory."""
    import os

    from fastapi.responses import FileResponse

    # Sanitize filename to prevent directory traversal
    safe_name = os.path.basename(filename)
    image_dir = os.path.join(os.path.expanduser("~"), "Downloads", "glad-labs-generated-images")
    path = os.path.join(image_dir, safe_name)

    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type="image/png")


def convert_markdown_to_html(markdown_content: str) -> str:
    """Convert markdown content to HTML. Falls back to raw content on error.

    Content is stored as markdown in the DB; converted to HTML on read.

    Historical bug (#198 follow-up): this function used to early-return
    when content started with `<`, assuming the whole thing was already
    HTML. That broke posts with a leading `<img>` tag followed by
    markdown — they shipped raw `##` and `**` markers to the frontend.
    The markdown library passes HTML through unmodified, so we can
    safely convert mixed content.
    """
    if not markdown_content:
        return ""

    try:
        import markdown as md

        stripped = markdown_content.strip()

        # Only skip conversion if the content has NO markdown markers —
        # i.e. it's pure HTML / plain text. If markdown is present
        # anywhere, convert the whole thing; python-markdown passes
        # existing HTML tags through unchanged.
        _has_markdown = bool(
            _MARKDOWN_MARKER_RE.search(stripped)
        )
        if stripped.startswith("<") and not _has_markdown:
            return markdown_content

        html = md.markdown(
            stripped,
            extensions=["extra", "codehilite", "sane_lists", "smarty"],
            output_format="html",
        )
        return html
    except Exception as e:
        logger.error("Error converting markdown: %s", e, exc_info=True)
        return markdown_content


# Cheap heuristic: look for any of `##` headers, `**bold**`, fenced code
# blocks, markdown links, or list bullets. Much faster than a full parse
# and good enough to gate the HTML-passthrough shortcut.
_MARKDOWN_MARKER_RE = __import__("re").compile(
    r"(?m)"  # multiline
    r"(?:"
    r"^\#{1,6}\s"              # headers
    r"|\*\*[^*\n]{1,200}\*\*"   # bold
    r"|```"                     # code fence
    r"|^\s*[-*+]\s+\w"          # bulleted list
    r"|\[[^\]]+\]\([^)]+\)"    # markdown link
    r")"
)


_STRIP_MD_RE = __import__("re").compile(
    r"\*{1,3}|_{1,3}"           # bold/italic markers
    r"|!\[[^\]]*\]\([^)]*\)"    # images
    r"|\[[^\]]*\]\([^)]*\)"     # links → keeps anchor text below
    r"|```[^`]*```"             # fenced code blocks
    r"|`[^`]+`"                 # inline code
    r"|^\s*>+\s?"               # blockquotes
    r"|^\s*[-*+]\s"             # list markers
    r"|^\#{1,6}\s"              # headers
)

_LINK_TEXT_RE = __import__("re").compile(r"\[([^\]]+)\]\([^)]*\)")


def generate_excerpt_from_content(content: str, length: int = 200) -> str:
    """Generate a plain-text excerpt from markdown content."""
    if not content:
        return ""

    lines = content.split("\n")
    excerpt_parts = []

    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue

        cleaned = _LINK_TEXT_RE.sub(r"\1", line)
        cleaned = _STRIP_MD_RE.sub("", cleaned).strip()

        if cleaned:
            excerpt_parts.append(cleaned)

        if len(" ".join(excerpt_parts)) >= length:
            break

    excerpt = " ".join(excerpt_parts)[:length].strip()
    if len(" ".join(excerpt_parts)) > length:
        excerpt = excerpt.rsplit(" ", 1)[0] + "..."

    return excerpt


def map_featured_image_to_coverimage(post: dict) -> dict:
    """
    Map database featured_image_url to Strapi-compatible coverImage format.

    Frontend expects: coverImage.data.attributes.url
    Database returns: featured_image_url

    This ensures compatibility with existing frontend components.
    """
    if post.get("featured_image_url"):
        post["coverImage"] = {
            "data": {
                "attributes": {
                    "url": post["featured_image_url"],
                    "alternativeText": f"Cover image for {post.get('title', 'post')}",
                }
            }
        }
    else:
        post["coverImage"] = None

    return post


async def get_db_pool():
    """Get database pool from the shared DatabaseService (injected at startup).

    Uses the centralized service container instead of instantiating a new
    DatabaseService mid-request, which would bypass the connection pool.
    """
    db_service = get_database_dependency()
    return getattr(db_service, "cloud_pool", None) or db_service.pool


# ============================================================================
# POSTS ENDPOINTS
# ============================================================================


@router.get("/api/posts")
@limiter.limit("60/minute")
async def list_posts(
    request: Request,
    offset: int = Query(0, ge=0, le=10000, description="Number of posts to skip"),
    skip: int = Query(0, ge=0, le=10000, description="Alias for offset (deprecated — use offset)"),
    limit: int = Query(20, ge=1, le=100),
    published_only: bool = Query(True),
    token: str | None = Depends(verify_api_token_optional),
):
    """
    List all blog posts with pagination (ASYNC).
    Returns: {posts: [...], total: N, offset: N, limit: N}

    Note: 'skip' is accepted as a deprecated alias for 'offset' for backwards compatibility.
    Unauthenticated callers always receive published posts only (published_only=True enforced).
    """
    # Resolve offset: explicit 'offset' param wins; fall back to legacy 'skip'
    offset = offset if offset != 0 else skip
    # Unauthenticated callers cannot request draft/unpublished posts
    if token is None:
        published_only = True
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            where_clauses = []
            params = []

            if published_only:
                where_clauses.append("status = 'published'")

            where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            # Single query: COUNT(*) OVER () avoids a separate COUNT round-trip.
            # Same pattern used in tasks_db.py (line ~506).
            params.append(limit)
            params.append(offset)
            query = f"""
                SELECT id, title, slug, excerpt, featured_image_url, cover_image_url,
                       category_id, published_at, created_at, updated_at,
                       seo_title, seo_description, seo_keywords, status, content, author_id,
                       COUNT(*) OVER () AS total_count
                FROM posts
                {where_sql}
                ORDER BY COALESCE(published_at, created_at) DESC NULLS LAST
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """

            rows = await conn.fetch(query, *params)
            total = rows[0]["total_count"] if rows else 0

            # Exclude the internal window-function column from API output
            posts = [{k: v for k, v in dict(row).items() if k != "total_count"} for row in rows]

            # Format timestamps, generate missing excerpts, and convert markdown to HTML
            for post in posts:
                post["published_at"] = (
                    post["published_at"].isoformat() if post["published_at"] else None
                )
                post["created_at"] = post["created_at"].isoformat() if post["created_at"] else None
                post["updated_at"] = post["updated_at"].isoformat() if post["updated_at"] else None

                # Generate excerpt if missing
                if not post.get("excerpt") and post.get("content"):
                    post["excerpt"] = generate_excerpt_from_content(post["content"])

                # Convert markdown content to HTML for safe rendering
                if post.get("content"):
                    post["content"] = convert_markdown_to_html(post["content"])

                map_featured_image_to_coverimage(post)

            return {
                "posts": posts,
                "total": total,
                "offset": offset,
                "limit": limit,
            }
    except Exception as e:
        raise await handle_route_error(e, "list_posts", logger)


@router.get("/api/posts/preview/{preview_token}")
async def preview_post(
    preview_token: str,
    site_config_dep = Depends(get_site_config_dependency),
):
    """
    Preview a draft post using a secret token. No auth required — the token IS the auth.
    Used for mobile preview before publishing.
    """
    import re
    # Validate token format (hex only, prevent injection)
    if not re.match(r'^[a-f0-9]{32}$', preview_token):
        raise HTTPException(status_code=404, detail="Post not found")
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, title, slug, content, excerpt, featured_image_url, cover_image_url,
                       category_id, published_at, created_at, updated_at,
                       seo_title, seo_description, seo_keywords, status, author_id,
                       preview_token, metadata
                FROM posts
                WHERE preview_token = $1
                """,
                preview_token,
            )
            if row:
                post = dict(row)
                for dt_field in ("published_at", "created_at", "updated_at"):
                    if post.get(dt_field):
                        post[dt_field] = post[dt_field].isoformat()
                # Include podcast/video availability
                from services.podcast_service import PODCAST_DIR
                from services.video_service import VIDEO_DIR
                post_id = str(post["id"])
                post["has_podcast"] = (PODCAST_DIR / f"{post_id}.mp3").exists()
                post["has_video"] = (VIDEO_DIR / f"{post_id}.mp4").exists()
                post["is_preview"] = True
                # Include direct media URLs for preview players
                _r2_url = site_config_dep.get(
                    "r2_public_url",
                    "https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev",
                )
                if post["has_podcast"]:
                    post["podcast_url"] = f"{_r2_url}/podcast/{post_id}.mp3"
                if post["has_video"]:
                    post["video_url"] = f"{_r2_url}/video/{post_id}.mp4"

                return post

            # No post row yet — check content_tasks (pre-approval preview)
            # Use COALESCE(title, topic) so the generated canonical title
            # is preferred over the raw topic string.
            task_row = await conn.fetchrow(
                """
                SELECT task_id, COALESCE(title, topic) AS title, content, excerpt,
                       featured_image_url, seo_title, seo_description, seo_keywords,
                       category, quality_score, status, created_at, updated_at, metadata
                FROM content_tasks
                WHERE metadata->>'preview_token' = $1
                """,
                preview_token,
            )
            if not task_row:
                raise HTTPException(status_code=404, detail="Post not found")

            task = dict(task_row)
            for dt_field in ("created_at", "updated_at"):
                if task.get(dt_field):
                    task[dt_field] = task[dt_field].isoformat()
            # Convert markdown content to HTML for frontend rendering
            if task.get("content"):
                task["content"] = convert_markdown_to_html(task["content"])
            task["id"] = task.get("task_id", "")  # Frontend expects 'id'
            task["is_preview"] = True
            task["is_task_preview"] = True  # Flag: this is a task, not a published post
            task["slug"] = None
            task["has_podcast"] = False
            task["has_video"] = False
            return task
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Preview fetch error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch preview")


@router.get("/preview/{preview_token}", response_class=Response)
async def preview_post_html(preview_token: str):
    """
    Render a mobile-friendly HTML preview page for a draft post.
    Accessible over Tailscale — no Next.js needed.
    """
    import re as _re
    if not _re.match(r'^[a-f0-9]{32}$', preview_token):
        return Response(content="Not found", status_code=404, media_type="text/html")

    # Reuse the JSON preview endpoint logic
    post = await preview_post(preview_token)
    if not post:
        return Response(content="Not found", status_code=404, media_type="text/html")

    # Handle both dict (from task preview) and response model
    if hasattr(post, "body"):
        import json as _json
        post = _json.loads(post.body)

    title = post.get("title", "Untitled")
    content = post.get("content", "")
    status = post.get("status", "unknown")
    quality = post.get("quality_score", "?")
    excerpt = post.get("excerpt", "")
    from html import escape as _esc
    featured_img = _esc(post.get("featured_image_url", ""))
    has_podcast = post.get("has_podcast", False)
    has_video = post.get("has_video", False)
    podcast_url = _esc(post.get("podcast_url", ""))
    video_url = _esc(post.get("video_url", ""))
    safe_title = _esc(title)

    # Build podcast/video players
    media_html = ""
    if podcast_url:
        media_html += f'<div style="margin:16px 0;padding:12px;background:#1a2332;border:1px solid #22c55e44;border-radius:8px"><h3 style="color:#22c55e;font-size:12px;text-transform:uppercase;margin:0 0 8px">Podcast</h3><audio controls style="width:100%" preload="metadata"><source src="{podcast_url}" type="audio/mpeg"></audio></div>'
    if video_url:
        media_html += f'<div style="margin:16px 0;padding:12px;background:#1a2332;border:1px solid #3b82f644;border-radius:8px"><h3 style="color:#3b82f6;font-size:12px;text-transform:uppercase;margin:0 0 8px">Video</h3><video controls style="width:100%;border-radius:6px" preload="metadata" playsinline><source src="{video_url}" type="video/mp4"></video></div>'

    img_html = ""
    if featured_img:
        img_html = f'<img src="{featured_img}" style="width:100%;border-radius:12px;margin:16px 0" alt="{safe_title}">'

    # Clean up preview content — strip the same junk the publish pipeline removes
    import re as _clean_re
    # Remove "External Resources" / "Further Reading" sections with empty links
    content = _clean_re.sub(
        r'(?:^|\n)#{1,4}\s*(?:External\s+Resources|Further\s+Reading|References|Suggested\s+Resources)[^\n]*\n(?:\s*[-*]\s+[^\n]*\n)*',
        '\n', content, flags=_clean_re.IGNORECASE,
    )
    # Remove bullet items that are just labels with colons but no URLs
    content = _clean_re.sub(r'^\s*[-*]\s+[^(\[]*:\s*$', '', content, flags=_clean_re.MULTILINE)
    # Remove leaked SDXL prompts after images
    content = _clean_re.sub(r'(!\[[^\]]*\]\([^\)]+\))\s*\n\s*:\s+[^\n]+', r'\1', content)
    # Remove unresolved placeholders
    content = _clean_re.sub(r'\[IMAGE-\d+[^\]]*\]', '', content)
    # Remove dead link references (title with colon but no URL following)
    content = _clean_re.sub(r'^\s*[-*]\s+\[[^\]]+\]\s*$', '', content, flags=_clean_re.MULTILINE)
    # Strip photo attribution lines
    content = _clean_re.sub(r'\n\s*\*?Photo by [^\n]+(?:Pexels|Unsplash|Pixabay)\*?\s*\n', '\n', content, flags=_clean_re.IGNORECASE)
    # Strip empty "External Resources" / "Suggested Resources" sections with no URLs
    content = _clean_re.sub(
        r'(?:^|\n)#{1,4}\s*(?:Suggested\s+)?(?:External\s+)?(?:Resources?|References?|Further\s+Reading)[^\n]*\n(?:\s*[-*]\s+[^\n]*\n)*',
        '\n', content, flags=_clean_re.IGNORECASE,
    )

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>[PREVIEW] {title}</title>
<style>
body{{font-family:-apple-system,system-ui,sans-serif;background:#0f172a;color:#cbd5e1;margin:0;padding:0}}
.banner{{background:#f59e0b;color:#000;text-align:center;padding:8px;font-weight:bold;font-size:13px;position:sticky;top:0;z-index:50}}
.banner small{{font-weight:normal;opacity:.7;margin-left:8px}}
.container{{max-width:720px;margin:0 auto;padding:16px}}
h1{{color:#fff;font-size:28px;line-height:1.3;margin:16px 0 8px}}
.excerpt{{color:#94a3b8;font-size:16px;line-height:1.6;margin-bottom:16px}}
.badges span{{display:inline-block;padding:4px 10px;border-radius:20px;font-size:12px;margin:0 4px 8px 0}}
.badge-status{{background:#f59e0b33;color:#fbbf24;border:1px solid #f59e0b44}}
.badge-quality{{background:#22c55e33;color:#4ade80;border:1px solid #22c55e44}}
.badge-podcast{{background:#22c55e22;color:#22c55e;border:1px solid #22c55e33}}
.badge-video{{background:#3b82f622;color:#3b82f6;border:1px solid #3b82f633}}
article{{color:#e2e8f0;line-height:1.8;font-size:16px}}
article h1,article h2,article h3{{color:#fff}}
article h2{{font-size:22px;margin:24px 0 12px;border-bottom:1px solid #334155;padding-bottom:8px}}
article h3{{font-size:18px;margin:20px 0 8px}}
article a{{color:#22d3ee}}
article code{{background:#1e293b;padding:2px 6px;border-radius:4px;font-size:14px;color:#67e8f9}}
article pre{{background:#1e293b;padding:16px;border-radius:8px;overflow-x:auto;border:1px solid #334155}}
article blockquote{{border-left:3px solid #22d3ee55;background:#1e293b44;padding:8px 16px;margin:16px 0;border-radius:0 8px 8px 0}}
article ul,article ol{{padding-left:24px}}
article li{{margin:4px 0}}
article img{{max-width:100%;height:auto;aspect-ratio:auto;border-radius:8px;margin:12px 0}}
.approve{{margin:24px 0;padding:16px;background:#1e293b;border-radius:12px;text-align:center}}
.approve a{{display:inline-block;padding:12px 32px;background:#22c55e;color:#000;text-decoration:none;border-radius:8px;font-weight:bold;font-size:16px}}
</style></head><body>
<div class="banner">PREVIEW MODE<small>{status.upper()} | Q: {quality}</small></div>
<div class="container">
{img_html}
<h1>{title}</h1>
{"<p class='excerpt'>" + excerpt + "</p>" if excerpt else ""}
<div class="badges">
<span class="badge-status">{status.upper()}</span>
<span class="badge-quality">Quality: {quality}</span>
{"<span class='badge-podcast'>Podcast Ready</span>" if has_podcast else ""}
{"<span class='badge-video'>Video Ready</span>" if has_video else ""}
</div>
{media_html}
<article>{content}</article>
</div></body></html>"""

    return Response(content=html, media_type="text/html; charset=utf-8")


@router.get("/api/posts/search")
@limiter.limit("30/minute")
async def search_posts(
    request: Request,
    q: str = Query("", description="Search term to match against title, content, or slug"),
    limit: int = Query(50, ge=1, le=100, description="Maximum posts to return"),
):
    """
    Search published posts by title, content, or slug (ASYNC).
    Returns: {posts: [...], total: N, offset: 0, limit: N}
    No auth required (public endpoint).
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            if not q.strip():
                return {"posts": [], "total": 0, "offset": 0, "limit": limit}

            search_term = f"%{q}%"
            rows = await conn.fetch(
                """
                SELECT id, title, slug, excerpt, featured_image_url, cover_image_url,
                       category_id, published_at, created_at, updated_at,
                       seo_title, seo_description, seo_keywords, status, content, author_id
                FROM posts
                WHERE status = 'published'
                  AND (title ILIKE $1 OR content ILIKE $1 OR slug ILIKE $1)
                ORDER BY updated_at DESC
                LIMIT $2
                """,
                search_term,
                limit,
            )

            posts = [dict(row) for row in rows]

            for post in posts:
                post["published_at"] = (
                    post["published_at"].isoformat() if post["published_at"] else None
                )
                post["created_at"] = post["created_at"].isoformat() if post["created_at"] else None
                post["updated_at"] = post["updated_at"].isoformat() if post["updated_at"] else None

                if not post.get("excerpt") and post.get("content"):
                    post["excerpt"] = generate_excerpt_from_content(post["content"])

                if post.get("content"):
                    post["content"] = convert_markdown_to_html(post["content"])

                map_featured_image_to_coverimage(post)

            return {
                "posts": posts,
                "total": len(posts),
                "offset": 0,
                "limit": limit,
            }
    except Exception as e:
        raise await handle_route_error(e, "search_posts", logger)


@router.get("/api/posts/{slug}")
@limiter.limit("60/minute")
async def get_post_by_slug(
    request: Request,
    slug: str,
):
    """
    Get single post by slug with full content and tags (ASYNC).
    Returns: {data: {...}, meta: {tags: [...]}}
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Get post
            post_row = await conn.fetchrow(
                """
                SELECT id, title, slug, content, excerpt, featured_image_url, cover_image_url, 
                       category_id, published_at, created_at, updated_at,
                       seo_title, seo_description, seo_keywords, status, author_id
                FROM posts
                WHERE slug = $1
            """,
                slug,
            )

            if not post_row:
                raise HTTPException(status_code=404, detail="Post not found")

            post = dict(post_row)
            post_id = post["id"]
            post["published_at"] = (
                post["published_at"].isoformat() if post["published_at"] else None
            )
            post["created_at"] = post["created_at"].isoformat() if post["created_at"] else None
            post["updated_at"] = post["updated_at"].isoformat() if post["updated_at"] else None

            # Generate excerpt if missing
            if not post.get("excerpt") and post.get("content"):
                post["excerpt"] = generate_excerpt_from_content(post["content"])

            # Convert markdown content to HTML for safe rendering
            if post.get("content"):
                post["content"] = convert_markdown_to_html(post["content"])

            # Map featured_image_url to coverImage in Strapi-compatible format
            map_featured_image_to_coverimage(post)

            # Get tags (gracefully handle missing table)
            tags = []
            try:
                tag_rows = await conn.fetch(
                    """
                    SELECT t.id, t.name, t.slug
                    FROM tags t
                    JOIN post_tags pt ON t.id = pt.tag_id
                    WHERE pt.post_id = $1
                """,
                    post_id,
                )
                tags = [dict(row) for row in tag_rows]
            except Exception as tag_error:
                # If tags table doesn't exist or query fails, just return empty tags
                logger.warning(
                    f"Could not fetch tags for post {post_id}: {str(tag_error)}", exc_info=True
                )
                tags = []

            # Get category
            category = None
            if post.get("category_id"):
                cat_row = await conn.fetchrow(
                    """
                    SELECT id, name, slug
                    FROM categories
                    WHERE id = $1
                """,
                    post["category_id"],
                )
                if cat_row:
                    category = dict(cat_row)

            return {
                "data": post,
                "meta": {
                    "tags": tags,
                    "category": category,
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        raise await handle_route_error(e, "get_post_by_slug", logger)


@router.patch("/api/posts/{post_id}")
async def update_post(
    post_id: str,
    updates: dict,
    token: str = Depends(verify_api_token),
):
    """
    Update a blog post by ID.
    Allowed fields: title, slug, content, excerpt, featured_image_url, status,
    tags, seo_title, seo_description, seo_keywords, published_at.

    When status is set to 'scheduled', published_at must be a valid future
    ISO 8601 datetime. When status is 'published' and published_at is not
    provided, it defaults to the current UTC time.
    """
    try:
        allowed = {
            "title",
            "slug",
            "content",
            "excerpt",
            "featured_image_url",
            "cover_image_url",
            "status",
            "tags",
            "seo_title",
            "seo_description",
            "seo_keywords",
            "published_at",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            raise HTTPException(status_code=400, detail="No valid fields to update")

        # Parse and validate published_at if provided
        parsed_published_at = None
        if "published_at" in filtered:
            raw = filtered["published_at"]
            if isinstance(raw, datetime):
                parsed = raw
            elif isinstance(raw, str):
                value = raw.strip()
                if value.endswith("Z"):
                    value = value[:-1] + "+00:00"
                try:
                    parsed = datetime.fromisoformat(value)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400, detail="published_at must be a valid ISO 8601 datetime"
                    ) from exc
            else:
                raise HTTPException(
                    status_code=400, detail="published_at must be a datetime or ISO 8601 string"
                )

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            parsed_published_at = parsed
            filtered["published_at"] = parsed_published_at

        # Handle scheduling: if status is 'scheduled', published_at must be a future date
        if filtered.get("status") == "scheduled":
            if parsed_published_at is None:
                raise HTTPException(
                    status_code=400, detail="published_at is required when scheduling a post"
                )
            if parsed_published_at <= datetime.now(timezone.utc):
                raise HTTPException(
                    status_code=400,
                    detail="published_at must be a future datetime when status is 'scheduled'",
                )
        # When publishing immediately, set published_at to now if not provided
        elif filtered.get("status") == "published" and "published_at" not in filtered:
            filtered["published_at"] = datetime.now(timezone.utc)

        # Validate column identifiers to prevent SQL injection
        from utils.sql_safety import SQLIdentifierValidator

        set_parts = []
        params = []
        for i, (col, val) in enumerate(filtered.items(), 1):
            col = SQLIdentifierValidator.safe_identifier(col, "column")
            set_parts.append(f"{col} = ${i}")
            params.append(val)
        params.append(post_id)
        set_clause = ", ".join(set_parts)

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                f"UPDATE posts SET {set_clause}, updated_at = NOW() WHERE id = ${len(params)}",
                *params,
            )
            if result == "UPDATE 0":
                raise HTTPException(status_code=404, detail="Post not found")
        return {"success": True, "message": "Post updated"}
    except HTTPException:
        raise
    except Exception as e:
        raise await handle_route_error(e, "update_post", logger)


@router.delete("/api/posts/{post_id}", status_code=204)
async def delete_post(
    post_id: str,
    token: str = Depends(verify_api_token),
):
    """Delete a blog post by ID."""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            result = await conn.execute("DELETE FROM posts WHERE id = $1", post_id)
            if result == "DELETE 0":
                raise HTTPException(status_code=404, detail="Post not found")
    except HTTPException:
        raise
    except Exception as e:
        raise await handle_route_error(e, "delete_post", logger)


# ============================================================================
# CATEGORIES ENDPOINTS
# ============================================================================


@router.get("/api/categories")
@limiter.limit("60/minute")
async def list_categories(
    request: Request,
    offset: int = Query(0, ge=0, description="Number of categories to skip"),
    limit: int = Query(100, ge=1, le=500, description="Maximum categories to return"),
):
    """
    List categories with optional pagination (ASYNC).
    Returns: {categories: [...], total: N, offset: N, limit: N}
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, name, slug, description, created_at, updated_at
                FROM categories
                ORDER BY name
            """)

            all_categories = []
            for row in rows:
                cat = dict(row)
                cat["created_at"] = cat["created_at"].isoformat() if cat["created_at"] else None
                cat["updated_at"] = cat["updated_at"].isoformat() if cat["updated_at"] else None
                all_categories.append(cat)

            total = len(all_categories)
            categories = all_categories[offset : offset + limit]
            return {"categories": categories, "total": total, "offset": offset, "limit": limit}
    except Exception as e:
        raise await handle_route_error(e, "list_categories", logger)


@router.get("/api/categories/{slug}")
@limiter.limit("60/minute")
async def get_category_by_slug(request: Request, slug: str) -> dict[str, Any]:
    """
    Get a single category by slug (ASYNC).
    Returns: {data: {...}}
    No auth required (public endpoint).
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, name, slug, description, created_at, updated_at
                FROM categories
                WHERE slug = $1
                """,
                slug,
            )

            if not row:
                raise HTTPException(status_code=404, detail="Category not found")

            category = dict(row)
            category["created_at"] = (
                category["created_at"].isoformat() if category["created_at"] else None
            )
            category["updated_at"] = (
                category["updated_at"].isoformat() if category["updated_at"] else None
            )

            return {"data": category}
    except HTTPException:
        raise
    except Exception as e:
        raise await handle_route_error(e, "get_category_by_slug", logger)


# ============================================================================
# TAGS ENDPOINTS
# ============================================================================


@router.get("/api/tags")
@limiter.limit("60/minute")
async def list_tags(
    request: Request,
    offset: int = Query(0, ge=0, description="Number of tags to skip"),
    limit: int = Query(100, ge=1, le=500, description="Maximum tags to return"),
):
    """
    List tags with optional pagination (ASYNC).
    Returns: {tags: [...], total: N, offset: N, limit: N}
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, name, slug, description, created_at, updated_at
                FROM tags
                ORDER BY name
            """)

            all_tags = []
            for row in rows:
                tag = dict(row)
                tag["created_at"] = tag["created_at"].isoformat() if tag["created_at"] else None
                tag["updated_at"] = tag["updated_at"].isoformat() if tag["updated_at"] else None
                all_tags.append(tag)

            total = len(all_tags)
            tags = all_tags[offset : offset + limit]
            return {"tags": tags, "total": total, "offset": offset, "limit": limit}
    except Exception as e:
        raise await handle_route_error(e, "list_tags", logger)


# ============================================================================
# ANALYTICS — lightweight view tracking (feeds Grafana + revenue engine)
# ============================================================================


@router.post("/api/track/view")
@limiter.limit("120/minute")
async def track_page_view(request: Request) -> JSONResponse:
    """Track a page view. Called by the frontend beacon. No auth required.

    Body: {"path": "/posts/slug-here", "slug": "slug-here", "referrer": "..."}
    Returns: 204 No Content
    """
    try:
        body = await request.json()
        path = body.get("path", "")[:500]
        slug = body.get("slug", "")[:500]
        referrer = body.get("referrer", "")[:1000]
        ua = (request.headers.get("user-agent") or "")[:500]

        if not path:
            return JSONResponse(status_code=204, content=None)

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO page_views (path, slug, referrer, user_agent) VALUES ($1, $2, $3, $4)",
                path, slug, referrer, ua,
            )

        # Also increment view_count on the post for quick access
        if slug:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE posts SET view_count = COALESCE(view_count, 0) + 1 WHERE slug = $1",
                    slug,
                )
    except Exception:
        logger.warning("[TRACK_VIEW] Page view tracking failed (non-fatal)", exc_info=True)

    return JSONResponse(status_code=204, content=None)


@router.get("/api/analytics/views")
@limiter.limit("30/minute")
async def get_view_stats(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    token: str = Depends(verify_api_token),
):
    """Get page view statistics. Auth required."""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Views per day
            daily = await conn.fetch(
                "SELECT date_trunc('day', created_at)::date as day, COUNT(*) as views "
                "FROM page_views WHERE created_at > NOW() - ($1 || ' days')::interval "
                "GROUP BY 1 ORDER BY 1",
                str(days),
            )
            # Top posts
            top = await conn.fetch(
                "SELECT slug, COUNT(*) as views FROM page_views "
                "WHERE slug IS NOT NULL AND slug != '' "
                "AND created_at > NOW() - ($1 || ' days')::interval "
                "GROUP BY slug ORDER BY views DESC LIMIT 20",
                str(days),
            )
            # Top referrers
            refs = await conn.fetch(
                "SELECT referrer, COUNT(*) as views FROM page_views "
                "WHERE referrer IS NOT NULL AND referrer != '' "
                "AND created_at > NOW() - ($1 || ' days')::interval "
                "GROUP BY referrer ORDER BY views DESC LIMIT 10",
                str(days),
            )
        return {
            "period_days": days,
            "daily": [{"day": str(r["day"]), "views": r["views"]} for r in daily],
            "top_posts": [{"slug": r["slug"], "views": r["views"]} for r in top],
            "top_referrers": [{"referrer": r["referrer"], "views": r["views"]} for r in refs],
        }
    except Exception as e:
        logger.error("[ANALYTICS] Failed: %s", e, exc_info=True)
        return {"error": "analytics_unavailable"}


# ============================================================================
# STATIC EXPORT — push-only headless CMS output
# ============================================================================


@router.post("/api/export/rebuild", dependencies=[Depends(verify_api_token)])
async def rebuild_static_export(db_service=Depends(get_database_dependency)):
    """Full rebuild of all static JSON files on CDN.

    Regenerates: posts index, individual post files, JSON feed,
    categories, authors, sitemap, and manifest.
    """
    pool = getattr(db_service, "cloud_pool", None) or db_service.pool

    try:
        from services.static_export_service import export_full_rebuild
        result = await export_full_rebuild(pool)
        status_code = 200 if result.get("success") else 207
        return JSONResponse(content=result, status_code=status_code)
    except Exception as e:
        logger.error("[STATIC_EXPORT] Rebuild failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
