"""
Content Management System API Routes

Simple REST endpoints for blog content, categories, and tags.
Using pure synchronous psycopg2 without async complications.
"""

import os
from fastapi import APIRouter, HTTPException, Query, status
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from typing import Optional, Any

router = APIRouter(tags=["cms"])

# Database configuration
DB_URL = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/glad_labs_dev')
DB_URL = DB_URL.replace('postgresql+asyncpg://', 'postgresql://')


def get_db():
    """Get a fresh database connection"""
    try:
        return psycopg2.connect(DB_URL)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {str(e)}")


# ============================================================================
# POSTS ENDPOINTS
# ============================================================================

@router.get("/api/posts")
def list_posts(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    published_only: bool = Query(True),
):
    """
    List all blog posts with pagination.
    Returns: {data: [...], meta: {pagination: {...}}}
    """
    try:
        print("[DEBUG] list_posts called")
        conn = get_db()
        print("[DEBUG] Connected to database")
        cur = conn.cursor(cursor_factory=RealDictCursor)
        print("[DEBUG] Cursor created")
        
        # Count total
        count_query = "SELECT COUNT(*) as total FROM posts"
        if published_only:
            count_query += " WHERE published_at IS NOT NULL"
        
        print(f"[DEBUG] Executing count query: {count_query}")
        cur.execute(count_query)
        result = cur.fetchone()
        total = result["total"] if result else 0
        print(f"[DEBUG] Total posts: {total}")
        
        # Get posts
        query = """
            SELECT id, title, slug, excerpt, featured_image_url, cover_image_url, category_id, published_at, created_at, updated_at,
                   seo_title, seo_description, seo_keywords, status, content, author_id, view_count
            FROM posts
        """
        if published_only:
            query += " WHERE published_at IS NOT NULL"
        query += " ORDER BY published_at DESC NULLS LAST"
        query += f" OFFSET {skip} LIMIT {limit}"
        
        print("[DEBUG] Executing posts query")
        cur.execute(query)
        rows = cur.fetchall()
        print(f"[DEBUG] Got {len(rows)} posts")
        
        posts = []
        for row in rows:
            post = dict(row)
            post["published_at"] = post["published_at"].isoformat() if post["published_at"] else None
            post["created_at"] = post["created_at"].isoformat() if post["created_at"] else None
            post["updated_at"] = post["updated_at"].isoformat() if post["updated_at"] else None
            posts.append(post)
        
        cur.close()
        conn.close()
        print("[DEBUG] Returning response")
        
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
        import traceback
        print(f"[ERROR] Exception in list_posts: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error fetching posts: {str(e)}")


@router.get("/api/posts/{slug}")
def get_post_by_slug(slug: str):
    """
    Get single post by slug with full content and tags.
    Returns: {data: {...}, meta: {tags: [...]}}
    """
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get post
        cur.execute("""
            SELECT id, title, slug, content, excerpt, featured_image_url, cover_image_url, category_id, published_at, created_at, updated_at,
                   seo_title, seo_description, seo_keywords, status, author_id, view_count
            FROM posts
            WHERE slug = %s
        """, (slug,))
        
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Post not found")
        
        post = dict(row)
        post_id = post["id"]
        post["published_at"] = post["published_at"].isoformat() if post["published_at"] else None
        post["created_at"] = post["created_at"].isoformat() if post["created_at"] else None
        post["updated_at"] = post["updated_at"].isoformat() if post["updated_at"] else None
        
        # Get tags
        cur.execute("""
            SELECT t.id, t.name, t.slug, t.color
            FROM tags t
            JOIN post_tags pt ON t.id = pt.tag_id
            WHERE pt.post_id = %s
        """, (post_id,))
        
        tags = [dict(row) for row in cur.fetchall()]
        
        # Get category
        category = None
        if post.get("category_id"):
            cur.execute("""
                SELECT id, name, slug
                FROM categories
                WHERE id = %s
            """, (post["category_id"],))
            cat_row = cur.fetchone()
            if cat_row:
                category = dict(cat_row)
        
        cur.close()
        conn.close()
        
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
        raise HTTPException(status_code=500, detail=f"Error fetching post: {str(e)}")


# ============================================================================
# CATEGORIES ENDPOINTS
# ============================================================================

@router.get("/api/categories")
def list_categories():
    """
    List all categories.
    Returns: {data: [...], meta: {}}
    """
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT id, name, slug, description, created_at, updated_at
            FROM categories
            ORDER BY name
        """)
        
        rows = cur.fetchall()
        categories = []
        for row in rows:
            cat = dict(row)
            cat["created_at"] = cat["created_at"].isoformat() if cat["created_at"] else None
            cat["updated_at"] = cat["updated_at"].isoformat() if cat["updated_at"] else None
            categories.append(cat)
        
        cur.close()
        conn.close()
        
        return {
            "data": categories,
            "meta": {}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching categories: {str(e)}")


# ============================================================================
# TAGS ENDPOINTS
# ============================================================================

@router.get("/api/tags")
def list_tags():
    """
    List all tags.
    Returns: {data: [...], meta: {}}
    """
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT id, name, slug, description, color, created_at, updated_at
            FROM tags
            ORDER BY name
        """)
        
        rows = cur.fetchall()
        tags = []
        for row in rows:
            tag = dict(row)
            tag["created_at"] = tag["created_at"].isoformat() if tag["created_at"] else None
            tag["updated_at"] = tag["updated_at"].isoformat() if tag["updated_at"] else None
            tags.append(tag)
        
        cur.close()
        conn.close()
        
        return {
            "data": tags,
            "meta": {}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching tags: {str(e)}")


# ============================================================================
# HEALTH CHECK
# ============================================================================

@router.get("/api/cms/status")
def cms_status():
    """
    Check CMS database status and table existence.
    Returns: {status: "healthy"|"error", tables: {...}}
    """
    try:
        conn = get_db()
        cur = conn.cursor()
        
        tables = {}
        for table_name in ["posts", "categories", "tags", "post_tags"]:
            cur.execute(f"""
                SELECT EXISTS(
                    SELECT 1 FROM information_schema.tables 
                    WHERE table_name = '{table_name}'
                ) as exists
            """)
            exists = cur.fetchone()[0]
            
            if exists:
                cur.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cur.fetchone()[0]
                tables[table_name] = {"exists": True, "count": count}
            else:
                tables[table_name] = {"exists": False, "count": 0}
        
        cur.close()
        conn.close()
        
        all_exist = all(t["exists"] for t in tables.values())
        
        return {
            "status": "healthy" if all_exist else "degraded",
            "tables": tables,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "status": "error",
            "detail": str(e),
            "tables": {},
            "timestamp": datetime.now().isoformat(),
        }
