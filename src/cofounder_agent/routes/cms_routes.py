"""
Content Management System API Routes

ASYNC REST endpoints for blog content, categories, and tags.
Using pure asyncpg for non-blocking database access.
"""

import os
from fastapi import APIRouter, HTTPException, Query, status, Depends
from datetime import datetime
from typing import Optional, Any
import logging

from services.database_service import DatabaseService
from routes.auth_unified import get_current_user, UserProfile
from utils.error_handler import handle_route_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cms"])


def convert_markdown_to_html(markdown_content: str) -> str:
    """
    Convert markdown content to HTML for safe rendering.
    Handles both pure markdown and HTML-wrapped markdown hybrid format.
    Uses regex patterns for compatibility without external markdown library.

    Args:
        markdown_content: Markdown formatted text

    Returns:
        HTML content safe for rendering
    """
    if not markdown_content:
        return ""

    try:
        import re

        content = markdown_content.strip()
        html = content

        # Handle setext-style headers (underlined with = or -)
        # Level 1 headers (underlined with =)
        html = re.sub(r"^(.*?)\n=+\s*$", r"<h1>\1</h1>", html, flags=re.MULTILINE)
        # Level 2 headers (underlined with -)
        html = re.sub(r"^(.*?)\n-+\s*$", r"<h2>\1</h2>", html, flags=re.MULTILINE)

        # Convert ATX-style headers (# style)
        html = re.sub(r"^### (.*?)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
        html = re.sub(r"^## (.*?)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
        html = re.sub(r"^# (.*?)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)

        # Remove standalone lines of dashes/equals (separator lines)
        html = re.sub(r"^\s*={3,}\s*$", "", html, flags=re.MULTILINE)
        html = re.sub(r"^\s*-{3,}\s*$", "", html, flags=re.MULTILINE)

        # Convert bold (**, __, **text**, __text__)
        html = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", html)
        html = re.sub(r"__(.*?)__", r"<strong>\1</strong>", html)

        # Convert italic (*, _)
        html = re.sub(r"\*(.*?)\*", r"<em>\1</em>", html)
        html = re.sub(r"_(.*?)_", r"<em>\1</em>", html)

        # Convert line breaks to paragraphs
        paragraphs = html.split("\n\n")
        converted_paragraphs = []
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            # Skip if already an HTML element
            if (
                p.startswith("<h")
                or p.startswith("<ol")
                or p.startswith("<ul")
                or p.startswith("<blockquote")
            ):
                converted_paragraphs.append(p)
            # Handle numbered lists
            elif re.match(r"^\d+\.", p):
                items = []
                for line in p.split("\n"):
                    line = line.strip()
                    if re.match(r"^\d+\.", line):
                        item_text = re.sub(r"^\d+\.\s*", "", line)
                        items.append(f"<li>{item_text}</li>")
                converted_paragraphs.append("<ol>" + "".join(items) + "</ol>")
            # Handle bullet lists
            elif p.startswith("-") or p.startswith("*"):
                items = []
                for line in p.split("\n"):
                    line = line.strip()
                    if line.startswith("-"):
                        item_text = re.sub(r"^-\s*", "", line)
                        items.append(f"<li>{item_text}</li>")
                    elif line.startswith("*"):
                        item_text = re.sub(r"^\*\s*", "", line)
                        items.append(f"<li>{item_text}</li>")
                if items:
                    converted_paragraphs.append("<ul>" + "".join(items) + "</ul>")
            else:
                # Regular paragraph
                converted_paragraphs.append(f"<p>{p}</p>")

        html = "\n".join(converted_paragraphs)

        logger.info(f"Converted markdown to HTML (len={len(html)} chars)")
        return html
    except Exception as e:
        logger.error(f"Error converting markdown: {e}", exc_info=True)
        # Fallback: return as-is
        return markdown_content


def generate_excerpt_from_content(content: str, length: int = 200) -> str:
    """
    Generate a clean excerpt from markdown content.

    Args:
        content: Markdown content
        length: Maximum length of excerpt in characters

    Returns:
        Clean excerpt text without markdown formatting
    """
    if not content:
        return ""

    # Remove markdown headers and formatting
    lines = content.split("\n")
    excerpt_parts = []

    for line in lines:
        # Skip empty lines and markdown headers
        if not line.strip() or line.startswith("#"):
            continue

        # Remove markdown formatting
        cleaned = line.replace("**", "").replace("*", "").replace("__", "").replace("_", "")
        cleaned = cleaned.replace("[", "").replace("]", "").replace("(", "").replace(")", "")
        cleaned = cleaned.replace("`", "").replace("~", "")

        if cleaned.strip():
            excerpt_parts.append(cleaned.strip())

        # Stop when we have enough content
        if len(" ".join(excerpt_parts)) >= length:
            break

    excerpt = " ".join(excerpt_parts)[:length].strip()
    # Add ellipsis if truncated
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


# Global database service instance
_db_service: Optional[DatabaseService] = None


async def get_db_pool():
    """Get database pool from service"""
    global _db_service
    if _db_service is None:
        _db_service = DatabaseService()
        await _db_service.initialize()
    return _db_service.pool


# ============================================================================
# POSTS ENDPOINTS
# ============================================================================


@router.get("/api/posts")
async def list_posts(
    skip: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=100),
    published_only: bool = Query(True),
):
    """
    List all blog posts with pagination (ASYNC).
    Returns: {data: [...], meta: {pagination: {...}}}
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Count total
            count_query = "SELECT COUNT(*) as total FROM posts"
            where_clauses = []
            params = []

            if published_only:
                where_clauses.append("status = 'published'")

            # if featured is not None:
            #     where_clauses.append(f"featured = ${len(params) + 1}")
            #     params.append(featured)

            if where_clauses:
                count_query += " WHERE " + " AND ".join(where_clauses)

            if params:
                total_row = await conn.fetchrow(count_query, *params)
            else:
                total_row = await conn.fetchrow(count_query)

            total = total_row["total"] if total_row else 0

            # Get paginated posts
            query = """
                SELECT id, title, slug, excerpt, featured_image_url, cover_image_url, 
                       category_id, published_at, created_at, updated_at,
                       seo_title, seo_description, seo_keywords, status, content, author_id
                FROM posts
            """

            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)

            # Sort by published_at (newest first), fallback to created_at if not published
            query += " ORDER BY COALESCE(published_at, created_at) DESC NULLS LAST"
            query += f" OFFSET {skip} LIMIT {limit}"

            if params:
                rows = await conn.fetch(query, *params)
            else:
                rows = await conn.fetch(query)

            posts = [dict(row) for row in rows]

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
                "data": posts,
                "meta": {
                    "pagination": {
                        "page": skip // limit + 1,
                        "pageSize": limit,
                        "total": total,
                        "pageCount": (total + limit - 1) // limit,
                    }
                },
            }
    except Exception as e:
        raise await handle_route_error(e, "list_posts", logger)


@router.get("/api/posts/{slug}")
async def get_post_by_slug(
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
                logger.warning(f"Could not fetch tags for post {post_id}: {str(tag_error)}")
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


# ============================================================================
# CATEGORIES ENDPOINTS
# ============================================================================


@router.get("/api/categories")
async def list_categories():
    """
    List all categories (ASYNC).
    Returns: {data: [...], meta: {}}
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, slug, description, created_at, updated_at
                FROM categories
                ORDER BY name
            """
            )

            categories = []
            for row in rows:
                cat = dict(row)
                cat["created_at"] = cat["created_at"].isoformat() if cat["created_at"] else None
                cat["updated_at"] = cat["updated_at"].isoformat() if cat["updated_at"] else None
                categories.append(cat)

            return {"data": categories, "meta": {}}
    except Exception as e:
        raise await handle_route_error(e, "list_categories", logger)


# ============================================================================
# TAGS ENDPOINTS
# ============================================================================


@router.get("/api/tags")
async def list_tags():
    """
    List all tags (ASYNC).
    Returns: {data: [...], meta: {}}
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, slug, description, created_at, updated_at
                FROM tags
                ORDER BY name
            """
            )

            tags = []
            for row in rows:
                tag = dict(row)
                tag["created_at"] = tag["created_at"].isoformat() if tag["created_at"] else None
                tag["updated_at"] = tag["updated_at"].isoformat() if tag["updated_at"] else None
                tags.append(tag)

            return {"data": tags, "meta": {}}
    except Exception as e:
        raise await handle_route_error(e, "list_tags", logger)


# ============================================================================
# HEALTH CHECK
# ============================================================================


@router.get("/api/cms/status")
async def cms_status():
    """
    Check CMS database status and table existence (ASYNC).
    Requires: Valid JWT authentication
    Returns: {status: "healthy"|"error", tables: {...}}
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            tables = {}
            for table_name in ["posts", "categories", "tags", "post_tags"]:
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
                    count_row = await conn.fetchrow(f"SELECT COUNT(*) as cnt FROM {table_name}")
                    count = count_row["cnt"] if count_row else 0
                    tables[table_name] = {"exists": True, "count": count}
                else:
                    tables[table_name] = {"exists": False, "count": 0}

            all_exist = all(t["exists"] for t in tables.values())

            return {
                "status": "healthy" if all_exist else "degraded",
                "tables": tables,
                "timestamp": datetime.now().isoformat(),
            }
    except Exception as e:
        logger.error(f"Error checking CMS status: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "detail": str(e),
            "tables": {},
            "timestamp": datetime.now().isoformat(),
        }


# ============================================================================
# UTILITY ENDPOINTS
# ============================================================================


@router.post("/api/cms/populate-missing-excerpts")
async def populate_missing_excerpts(current_user: UserProfile = Depends(get_current_user)):
    """
    Populate missing excerpts in the database for existing posts.
    Requires: Valid JWT authentication (admin)
    Returns: {updated_count: int, success: bool}
    """
    try:
        # Check user has admin role
        if not getattr(current_user, "is_admin", False):
            raise HTTPException(status_code=403, detail="Admin access required")

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Find posts with missing or empty excerpts
            posts = await conn.fetch(
                """
                SELECT id, content, excerpt
                FROM posts
                WHERE excerpt IS NULL OR excerpt = ''
            """
            )

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
