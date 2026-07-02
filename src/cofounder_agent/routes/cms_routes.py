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
from modules.content.api import PostsService
from services.logger_config import get_logger
from utils.content_formatting import convert_markdown_to_html  # still used by preview_post
from utils.error_handler import handle_route_error
from utils.rate_limiter import limiter
from utils.route_utils import get_database_dependency, get_site_config_dependency
from utils.uuid_prefix import resolve_uuid_prefix

logger = get_logger(__name__)
router = APIRouter(tags=["cms"])


@router.get("/images/generated/{filename}")
async def serve_generated_image(filename: str):
    """Serve image-gen-generated images from the local output directory."""
    import os

    from fastapi.responses import FileResponse

    # Sanitize filename to prevent directory traversal: basename strips
    # separators, and the realpath containment check catches what basename
    # can't (e.g. a bare ".." or symlink tricks).
    safe_name = os.path.basename(filename)
    image_dir = os.path.realpath(
        os.path.join(os.path.expanduser("~"), "Downloads", "glad-labs-generated-images"),
    )
    path = os.path.realpath(os.path.join(image_dir, safe_name))

    if os.path.commonpath([path, image_dir]) != image_dir or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type="image/png")


# convert_markdown_to_html, generate_excerpt_from_content, and
# map_featured_image_to_coverimage are imported from utils.content_formatting
# (Glad-Labs/poindexter#1341 — shared with PostsService to avoid duplication).


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
    skip: int = Query(
        0,
        ge=0,
        le=10000,
        # `deprecated=True` → OpenAPI param `deprecated: true` (poindexter#752
        # item 4), so the legacy alias is machine-visible, not prose-only.
        deprecated=True,
        description="Alias for offset (deprecated — use offset)",
    ),
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
        svc = PostsService(pool=pool)
        return await svc.list_posts(offset=offset, limit=limit, published_only=published_only)
    except Exception as e:
        raise await handle_route_error(e, "list_posts", logger) from e


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
                # Include direct media URLs for preview players. 2026-05-12
                # (poindexter#485): removed the hardcoded R2 bucket fallback —
                # when storage_public_url isn't configured, omit the URLs so
                # the preview UI just hides the media section rather than
                # serving broken links to Matt's bucket.
                _r2_url = (site_config_dep.get("storage_public_url", "") or "").rstrip("/")
                if _r2_url:
                    if post["has_podcast"]:
                        post["podcast_url"] = f"{_r2_url}/podcast/{post_id}.mp3"
                    if post["has_video"]:
                        post["video_url"] = f"{_r2_url}/video/{post_id}.mp4"

                # Render content the SAME way as the task path (and the
                # published page): unwrap any leaked writer JSON envelope, then
                # markdown -> HTML so headings/emphasis AND inline images (both
                # `![](…)` markdown and embedded `<img>` HTML) render — instead
                # of returning raw content where `###` shows as literal text and
                # markdown images never appear. This is what made the preview
                # diverge from the published output. (#540)
                if post.get("content"):
                    from services.llm_text import maybe_unwrap_json
                    post["content"] = convert_markdown_to_html(
                        maybe_unwrap_json(post["content"])
                    )

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
            # Unwrap any leaked writer JSON envelope (e.g. a ```json-fenced
            # {"title": ..., "post_body": "<markdown>"} that slipped past the
            # generation-time unwrap) BEFORE rendering, so the preview shows
            # the article — not a raw JSON code block — matching the published
            # output. Then convert markdown to HTML for frontend rendering.
            if task.get("content"):
                from services.llm_text import maybe_unwrap_json
                task["content"] = convert_markdown_to_html(
                    maybe_unwrap_json(task["content"])
                )
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
        raise HTTPException(status_code=500, detail="Failed to fetch preview") from e


@router.get("/preview/{preview_token}", response_class=Response)
async def preview_post_html(
    preview_token: str,
    site_config_dep = Depends(get_site_config_dependency),
):
    """
    Render a mobile-friendly HTML preview page for a draft post.
    Accessible over Tailscale — no Next.js needed.
    """
    import re as _re
    if not _re.match(r'^[a-f0-9]{32}$', preview_token):
        return Response(content="Not found", status_code=404, media_type="text/html")

    # Reuse the JSON preview endpoint logic. Forward the resolved SiteConfig —
    # calling preview_post() bare left site_config_dep as an unresolved
    # Depends sentinel, 500-ing the posts path at the storage_public_url read.
    post = await preview_post(preview_token, site_config_dep)
    if not post:
        return Response(content="Not found", status_code=404, media_type="text/html")

    # Handle both dict (from task preview) and response model
    if hasattr(post, "body"):
        import json as _json
        post = _json.loads(post.body)

    # dict.get(key, default) returns None when the key exists with a None value,
    # so coerce to fallback strings explicitly with `or` before any string ops.
    title = post.get("title") or "Untitled"
    content = post.get("content") or ""
    status = post.get("status") or "unknown"
    quality = post.get("quality_score") if post.get("quality_score") is not None else "?"
    excerpt = post.get("excerpt") or ""
    from html import escape as _esc
    featured_img = _esc(post.get("featured_image_url") or "")
    has_podcast = post.get("has_podcast", False)
    has_video = post.get("has_video", False)
    podcast_url = _esc(post.get("podcast_url") or "")
    video_url = _esc(post.get("video_url") or "")
    safe_title = _esc(title)
    # 2026-05-12 security audit P0 #6: title/excerpt/status are operator-
    # facing strings derived from LLM output (via research_service) which
    # an attacker-controlled web page could poison through a prompt-
    # injection vector. They flowed into the HTML body raw pre-fix.
    # Escape them explicitly before any string interpolation below.
    safe_excerpt = _esc(excerpt)
    safe_status = _esc(status)
    safe_quality = _esc(str(quality))

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
    # Remove leaked image-gen prompts after images
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
<title>[PREVIEW] {safe_title}</title>
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
<div class="banner">PREVIEW MODE<small>{safe_status.upper()} | Q: {safe_quality}</small></div>
<div class="container">
{img_html}
<h1>{safe_title}</h1>
{"<p class='excerpt'>" + safe_excerpt + "</p>" if excerpt else ""}
<div class="badges">
<span class="badge-status">{safe_status.upper()}</span>
<span class="badge-quality">Quality: {safe_quality}</span>
{"<span class='badge-podcast'>Podcast Ready</span>" if has_podcast else ""}
{"<span class='badge-video'>Video Ready</span>" if has_video else ""}
</div>
{media_html}
<article>{content}</article>
</div></body></html>"""

    # 2026-05-12 security audit P0 #6: strict Content-Security-Policy
    # blocks every script execution path even if an attacker manages
    # to inject markup through the markdown body (the LLM writer reads
    # from web research and could echo back attacker-controlled
    # `<script>` tags). The preview page doesn't need any JS, so the
    # policy can be aggressively narrow: no scripts at all (no
    # 'unsafe-inline' or 'unsafe-eval'), no fonts, no XHR. Style stays
    # inline-allowed because the page CSS lives in a <style> block.
    csp = (
        "default-src 'none'; "
        "style-src 'unsafe-inline'; "
        "img-src https: data:; "
        "media-src https: blob:; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    )
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": csp,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            # No-cache so a rotated/revoked preview token doesn't sit
            # in a CDN edge after the operator burns it.
            "Cache-Control": "private, no-cache, no-store, must-revalidate",
        },
    )


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
        svc = PostsService(pool=pool)
        return await svc.search_posts(q=q, limit=limit)
    except Exception as e:
        raise await handle_route_error(e, "search_posts", logger) from e


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
        svc = PostsService(pool=pool)
        result = await svc.get_post_by_slug(slug)
        if result is None:
            raise HTTPException(status_code=404, detail="Post not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise await handle_route_error(e, "get_post_by_slug", logger) from e


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

        pool = await get_db_pool()
        # Operators paste 8-char id prefixes from the dashboards / `poindexter
        # posts list`. posts.id is a real `uuid`, so a bare prefix in the
        # exact-match `WHERE id = $N` below would crash asyncpg's client-side
        # cast (a 500). Expand it to the full id first — 404 if it matches no
        # post, 409 if the prefix is ambiguous. A full UUID passes through
        # untouched (no DB round trip), keeping behaviour identical for the
        # MCP server / brain daemon callers.
        post_id = await resolve_uuid_prefix(
            pool, table="posts", column="id", value=post_id, noun="post"
        )

        set_parts = []
        params = []
        for i, (raw_col, val) in enumerate(filtered.items(), 1):
            col = SQLIdentifierValidator.safe_identifier(raw_col, "column")
            set_parts.append(f"{col} = ${i}")
            params.append(val)
        params.append(post_id)
        set_clause = ", ".join(set_parts)

        async with pool.acquire() as conn:
            result = await conn.execute(
                f"UPDATE posts SET {set_clause}, updated_at = NOW() WHERE id = ${len(params)}",  # nosec B608  # set_clause built from `allowed` allowlist (line 637) + SQLIdentifierValidator.safe_identifier (line 702); values use $N params
                *params,
            )
            if result == "UPDATE 0":
                raise HTTPException(status_code=404, detail="Post not found")
        return {"success": True, "message": "Post updated"}
    except HTTPException:
        raise
    except Exception as e:
        raise await handle_route_error(e, "update_post", logger) from e


@router.post("/api/posts/{post_id}/unpublish")
async def unpublish_post_route(
    post_id: str,
    token: str = Depends(verify_api_token),
    site_config_dep=Depends(get_site_config_dependency),
):
    """Take a published post offline — immediate rollback for a bad publish.

    Flips ``status`` ``published`` → ``draft`` and retires the post's static
    JSON from storage + busts its ISR cache, so the live site drops it
    immediately (the PATCH route flips the column but leaves the post served
    from storage). Idempotent: a post that isn't currently published returns
    ``unpublished: false`` with a reason. Accepts a full UUID or 8-char prefix.
    """
    try:
        from services.publish_service import unpublish_post

        pool = await get_db_pool()
        # 404 if the prefix matches no post, 409 if ambiguous; a full UUID
        # passes through untouched (same as update_post / delete_post).
        post_id = await resolve_uuid_prefix(
            pool, table="posts", column="id", value=post_id, noun="post"
        )
        return await unpublish_post(pool, post_id, site_config=site_config_dep)
    except HTTPException:
        raise
    except Exception as e:
        raise await handle_route_error(e, "unpublish_post", logger) from e


@router.delete("/api/posts/{post_id}", status_code=204)
async def delete_post(
    post_id: str,
    token: str = Depends(verify_api_token),
):
    """Delete a blog post by ID."""
    try:
        pool = await get_db_pool()
        svc = PostsService(pool=pool)
        deleted = await svc.delete_post(post_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Post not found")
    except HTTPException:
        raise
    except Exception as e:
        raise await handle_route_error(e, "delete_post", logger) from e


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
        svc = PostsService(pool=pool)
        return await svc.list_categories(offset=offset, limit=limit)
    except Exception as e:
        raise await handle_route_error(e, "list_categories", logger) from e


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
        raise await handle_route_error(e, "get_category_by_slug", logger) from e


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
        svc = PostsService(pool=pool)
        return await svc.list_tags(offset=offset, limit=limit)
    except Exception as e:
        raise await handle_route_error(e, "list_tags", logger) from e


# ============================================================================
# ANALYTICS — page-view aggregates from CF Analytics Engine
# ============================================================================
#
# The legacy /api/track/view endpoint was deleted 2026-05-28 along with the
# Vercel proxy that fronted it (web/public-site/app/api/page-views/route.ts).
# The endpoint had been receiving zero traffic since 2026-04-09 because the
# Vercel serverless functions that fronted it cannot reach the operator's
# local Docker network (poindexter-worker:8002).
#
# The replacement is a Cloudflare Worker beacon at
# infrastructure/cloudflare/page-views-beacon/ that writes one data point
# per view to Cloudflare Analytics Engine. Backend job
# services/jobs/sync_cloudflare_analytics.py pulls aggregated rows out
# via the CF AE SQL HTTP API every 5 minutes and inserts them into the
# existing page_views table — the consumer surfaces (Grafana panels,
# posts.view_count updates, lab_outcomes_v1.views_*_post_publish columns)
# all keep reading from page_views unchanged.


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
        # Signal the failure via the HTTP status, not a 200 with an {error}
        # body that monitors/clients read as success (poindexter#637).
        raise HTTPException(status_code=503, detail="analytics_unavailable") from e


# ============================================================================
# STATIC EXPORT — push-only headless CMS output
# ============================================================================


@router.post("/api/export/rebuild", dependencies=[Depends(verify_api_token)])
async def rebuild_static_export(
    db_service=Depends(get_database_dependency),
    site_config_dep=Depends(get_site_config_dependency),
):
    """Full rebuild of all static JSON files on CDN.

    Regenerates: posts index, individual post files, JSON feed,
    categories, authors, sitemap, and manifest.
    """
    pool = getattr(db_service, "cloud_pool", None) or db_service.pool

    try:
        from services.static_export_service import export_full_rebuild
        # #272 Phase-2d: export_full_rebuild requires an explicit site_config.
        result = await export_full_rebuild(pool, site_config=site_config_dep)

        # A full rebuild re-uploads every JSON file to R2, but the Next.js
        # data cache has NO TTL — it serves the old JSON until a tag
        # invalidation fires (see web/public-site/lib/posts.ts). Without
        # this, an operator rebuild silently leaves the live site stale:
        # the export path was decoupled from revalidation, so only the
        # publish path ever busted the cache. Revalidate the canonical post
        # tags — per-slug pages are tagged 'posts' too, so one revalidate
        # refreshes the index AND every post page. Non-fatal: a revalidation
        # failure must not fail the rebuild (the R2 write already landed).
        revalidation_success = False
        if result.get("success"):
            try:
                from services.revalidation_service import (
                    trigger_nextjs_revalidation,
                )

                revalidation_success = await trigger_nextjs_revalidation(
                    paths=["/", "/archive", "/posts", "/sitemap.xml", "/feed.xml"],
                    tags=["posts", "post-index"],
                    site_config=site_config_dep,
                )
                if not revalidation_success:
                    logger.warning(
                        "[STATIC_EXPORT] rebuild succeeded but ISR "
                        "revalidation returned failure — live site may "
                        "serve stale cached JSON until the next publish"
                    )
            except Exception as reval_err:  # noqa: BLE001 — non-fatal
                logger.warning(
                    "[STATIC_EXPORT] rebuild revalidation error (non-fatal): %s",
                    reval_err,
                )
        result["revalidation_success"] = revalidation_success
        status_code = 200 if result.get("success") else 207
        return JSONResponse(content=result, status_code=status_code)
    except Exception as e:
        logger.error("[STATIC_EXPORT] Rebuild failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Static export rebuild failed") from e
