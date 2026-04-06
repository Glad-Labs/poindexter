"""
Content Management System API Routes

ASYNC REST endpoints for blog content, categories, and tags.
Using pure asyncpg for non-blocking database access.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from middleware.api_token_auth import verify_api_token, verify_api_token_optional
from services.logger_config import get_logger
from utils.rate_limiter import limiter
from utils.error_handler import handle_route_error
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)
router = APIRouter(tags=["cms"])


def convert_markdown_to_html(markdown_content: str) -> str:
    """
    Convert markdown content to HTML for safe rendering (#956).

    Uses the ``markdown`` library for reliable conversion (handles links,
    images, code blocks, blockquotes, tables, etc.) instead of fragile
    regex patterns.  Falls back to the raw content on error.

    The canonical content boundary is: **markdown in, HTML out**.
    Content is stored as markdown in the database; this function converts
    on read so the frontend always receives HTML.

    Args:
        markdown_content: Markdown formatted text

    Returns:
        HTML content safe for rendering
    """
    if not markdown_content:
        return ""

    try:
        import markdown as md

        # If content already looks like HTML (starts with a tag), return as-is
        stripped = markdown_content.strip()
        if stripped.startswith("<") and not stripped.startswith("<!["):
            return markdown_content

        html = md.markdown(
            stripped,
            extensions=["extra", "codehilite", "sane_lists", "smarty"],
            output_format="html",
        )
        return html
    except Exception as e:
        logger.error(f"Error converting markdown: {e}", exc_info=True)
        return markdown_content


def generate_excerpt_from_content(content: str, length: int = 200) -> str:
    """
    Generate an excerpt from markdown content while preserving **basic markdown formatting**.

    This keeps the excerpt as markdown so it can be rendered with formatting on the frontend.

    Args:
        content: Markdown content
        length: Maximum length of excerpt in characters (before markdown)

    Returns:
        Excerpt with markdown formatting preserved (e.g., **bold**, *italic*)
    """
    if not content:
        return ""

    # Remove markdown headers and get meaningful paragraphs
    lines = content.split("\n")
    excerpt_parts = []

    for line in lines:
        # Skip empty lines and markdown headers
        if not line.strip() or line.startswith("#"):
            continue

        # Keep line as-is to preserve **bold**, *italic* formatting
        # Only remove problematic markdown syntax
        cleaned = line.replace("[", "").replace("]", "").replace("(", "").replace(")", "")
        cleaned = cleaned.replace("`", "").replace("~", "")

        if cleaned.strip():
            excerpt_parts.append(cleaned.strip())

        # Stop when we have enough content
        if len(" ".join(excerpt_parts)) >= length:
            break

    excerpt = " ".join(excerpt_parts)[:length].strip()
    # Add ellipsis if truncated (before markdown closes)
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
    token: Optional[str] = Depends(verify_api_token_optional),
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
async def preview_post(preview_token: str):
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
                from services.site_config import site_config as _sc
                _r2_url = _sc.get("r2_public_url", "https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev")
                if post["has_podcast"]:
                    post["podcast_url"] = f"{_r2_url}/podcast/{post_id}.mp3"
                if post["has_video"]:
                    post["video_url"] = f"{_r2_url}/video/{post_id}.mp4"

                return post

            # No post row yet — check content_tasks (pre-approval preview)
            task_row = await conn.fetchrow(
                """
                SELECT task_id, topic AS title, content, excerpt, featured_image_url,
                       seo_title, seo_description, seo_keywords, category, quality_score,
                       status, created_at, updated_at, metadata
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
                    SELECT t.id, t.name, t.slug, t.color
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

        # Build parameterized SET clause using ParameterizedQueryBuilder
        # to validate column identifiers and prevent SQL injection
        from utils.sql_safety import ParameterizedQueryBuilder, SQLIdentifierValidator, SQLOperator

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
async def get_category_by_slug(request: Request, slug: str) -> Dict[str, Any]:
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
# HEALTH CHECK
# ============================================================================


@router.get("/api/cms/status")
@limiter.limit("30/minute")
async def cms_status(request: Request) -> Dict[str, Any]:
    """
    Check CMS database status and table existence (ASYNC).
    Public endpoint — no authentication required.
    Returns: {status: "healthy"|"error", tables: {...}}
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Allowlist of tables — never interpolate user-controlled values into SQL
            CMS_TABLES = frozenset(["posts", "categories", "tags", "post_tags"])
            tables = {}
            for table_name in CMS_TABLES:
                # Check if table exists
                exists_row = await conn.fetchrow(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM information_schema.tables 
                        WHERE table_name = $1
                    ) as exists
                """,
                    table_name,
                )
                exists = exists_row["exists"] if exists_row else False

                if exists:
                    # table_name is from CMS_TABLES allowlist above — safe to interpolate
                    count_row = await conn.fetchrow(f'SELECT COUNT(*) as cnt FROM "{table_name}"')
                    count = count_row["cnt"] if count_row else 0
                    tables[table_name] = {"exists": True, "count": count}
                else:
                    tables[table_name] = {"exists": False, "count": 0}

            all_exist = all(t["exists"] for t in tables.values())

            return {
                "status": "healthy" if all_exist else "degraded",
                "tables": tables,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
    except Exception:
        logger.error("[cms_status] CMS status check failed", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "detail": "CMS status check failed",
                "tables": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


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
        pass  # Never fail on tracking — non-critical

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
# UTILITY ENDPOINTS
# ============================================================================


@router.post("/api/cms/populate-missing-excerpts")
async def populate_missing_excerpts(token: str = Depends(verify_api_token)):
    """
    Populate missing excerpts in the database for existing posts.
    Requires: Valid JWT authentication (admin)
    Returns: {updated_count: int, success: bool}
    """
    try:
        # Solo operator — admin access granted by valid token

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Find posts with missing or empty excerpts
            posts = await conn.fetch("""
                SELECT id, content, excerpt
                FROM posts
                WHERE excerpt IS NULL OR excerpt = ''
            """)

            updated_count = 0
            for post in posts:
                if post["content"]:
                    new_excerpt = generate_excerpt_from_content(post["content"])
                    await conn.execute(
                        "UPDATE posts SET excerpt = $1 WHERE id = $2",
                        new_excerpt,
                        post["id"],
                    )
                    updated_count += 1

            return {
                "success": True,
                "updated_count": updated_count,
                "message": f"Successfully populated {updated_count} missing excerpts",
            }
    except HTTPException:
        raise
    except Exception as e:
        raise await handle_route_error(e, "populate_missing_excerpts", logger)
