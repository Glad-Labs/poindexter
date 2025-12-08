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

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cms"])

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
    current_user: UserProfile = Depends(get_current_user),
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
                
            total = total_row['total'] if total_row else 0
            
            # Get paginated posts
            query = """
                SELECT id, title, slug, excerpt, featured_image_url, cover_image_url, 
                       category_id, published_at, created_at, updated_at,
                       seo_title, seo_description, seo_keywords, status, content, author_id, view_count
                FROM posts
            """
            
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
                
            query += " ORDER BY published_at DESC NULLS LAST"
            query += f" OFFSET {skip} LIMIT {limit}"
            
            if params:
                rows = await conn.fetch(query, *params)
            else:
                rows = await conn.fetch(query)
            
            posts = [dict(row) for row in rows]
            
            # Format timestamps
            for post in posts:
                post["published_at"] = post["published_at"].isoformat() if post["published_at"] else None
                post["created_at"] = post["created_at"].isoformat() if post["created_at"] else None
                post["updated_at"] = post["updated_at"].isoformat() if post["updated_at"] else None
            
            return {
                "data": posts,
                "meta": {
                    "pagination": {
                        "page": skip // limit + 1,
                        "pageSize": limit,
                        "total": total,
                        "pageCount": (total + limit - 1) // limit,
                    }
                }
            }
    except Exception as e:
        logger.error(f"Error fetching posts: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching posts: {str(e)}")


@router.get("/api/posts/{slug}")
async def get_post_by_slug(
    slug: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Get single post by slug with full content and tags (ASYNC).
    Returns: {data: {...}, meta: {tags: [...]}}
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Get post
            post_row = await conn.fetchrow("""
                SELECT id, title, slug, content, excerpt, featured_image_url, cover_image_url, 
                       category_id, published_at, created_at, updated_at,
                       seo_title, seo_description, seo_keywords, status, author_id, view_count
                FROM posts
                WHERE slug = $1
            """, slug)
            
            if not post_row:
                raise HTTPException(status_code=404, detail="Post not found")
            
            post = dict(post_row)
            post_id = post["id"]
            post["published_at"] = post["published_at"].isoformat() if post["published_at"] else None
            post["created_at"] = post["created_at"].isoformat() if post["created_at"] else None
            post["updated_at"] = post["updated_at"].isoformat() if post["updated_at"] else None
            
            # Get tags
            tag_rows = await conn.fetch("""
                SELECT t.id, t.name, t.slug, t.color
                FROM tags t
                JOIN post_tags pt ON t.id = pt.tag_id
                WHERE pt.post_id = $1
            """, post_id)
            tags = [dict(row) for row in tag_rows]
            
            # Get category
            category = None
            if post.get("category_id"):
                cat_row = await conn.fetchrow("""
                    SELECT id, name, slug
                    FROM categories
                    WHERE id = $1
                """, post["category_id"])
                if cat_row:
                    category = dict(cat_row)
            
            return {
                "data": post,
                "meta": {
                    "tags": tags,
                    "category": category,
                }
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching post: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching post: {str(e)}")


# ============================================================================
# CATEGORIES ENDPOINTS
# ============================================================================

@router.get("/api/categories")
async def list_categories(current_user: UserProfile = Depends(get_current_user)):
    """
    List all categories (ASYNC).
    Returns: {data: [...], meta: {}}
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, name, slug, description, created_at, updated_at
                FROM categories
                ORDER BY name
            """)
            
            categories = []
            for row in rows:
                cat = dict(row)
                cat["created_at"] = cat["created_at"].isoformat() if cat["created_at"] else None
                cat["updated_at"] = cat["updated_at"].isoformat() if cat["updated_at"] else None
                categories.append(cat)
            
            return {
                "data": categories,
                "meta": {}
            }
    except Exception as e:
        logger.error(f"Error fetching categories: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching categories: {str(e)}")


# ============================================================================
# TAGS ENDPOINTS
# ============================================================================

@router.get("/api/tags")
async def list_tags(current_user: UserProfile = Depends(get_current_user)):
    """
    List all tags (ASYNC).
    Returns: {data: [...], meta: {}}
    """
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, name, slug, description, color, created_at, updated_at
                FROM tags
                ORDER BY name
            """)
            
            tags = []
            for row in rows:
                tag = dict(row)
                tag["created_at"] = tag["created_at"].isoformat() if tag["created_at"] else None
                tag["updated_at"] = tag["updated_at"].isoformat() if tag["updated_at"] else None
                tags.append(tag)
            
            return {
                "data": tags,
                "meta": {}
            }
    except Exception as e:
        logger.error(f"Error fetching tags: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching tags: {str(e)}")


# ============================================================================
# HEALTH CHECK
# ============================================================================

@router.get("/api/cms/status")
async def cms_status(current_user: UserProfile = Depends(get_current_user)):
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
                exists_row = await conn.fetchrow("""
                    SELECT EXISTS(
                        SELECT 1 FROM information_schema.tables 
                        WHERE table_name = $1
                    ) as exists
                """, table_name)
                exists = exists_row['exists'] if exists_row else False
                
                if exists:
                    count_row = await conn.fetchrow(f"SELECT COUNT(*) as cnt FROM {table_name}")
                    count = count_row['cnt'] if count_row else 0
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
