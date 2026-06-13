"""PostsService — single SQL owner for posts, categories, and tags.

Route handlers and MCP tools delegate here instead of writing inline SQL.
See Glad-Labs/poindexter#1341 for context.

Design principles:
- Takes an asyncpg Pool at construction time (no SiteConfig dependency —
  none of these queries need settings).
- Returns plain dicts that match the existing HTTP response shape so
  callers (routes, MCP) can return them directly.
- Raises no HTTP-layer exceptions; callers map None returns and False
  booleans to 404s as appropriate.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

from utils.content_formatting import (
    convert_markdown_to_html,
    generate_excerpt_from_content,
    map_featured_image_to_coverimage,
)

logger = logging.getLogger(__name__)


def _format_timestamps(row: dict) -> dict:
    """Isoformat the three standard timestamp columns in place."""
    for col in ("published_at", "created_at", "updated_at"):
        if col in row and row[col] is not None:
            row[col] = row[col].isoformat()
        elif col in row:
            row[col] = None
    return row


def _enrich_post(post: dict) -> dict:
    """Apply excerpt generation, markdown→HTML, and coverImage mapping."""
    if not post.get("excerpt") and post.get("content"):
        post["excerpt"] = generate_excerpt_from_content(post["content"])
    if post.get("content"):
        post["content"] = convert_markdown_to_html(post["content"])
    map_featured_image_to_coverimage(post)
    return post


class PostsService:
    """SQL owner for the posts, categories, and tags tables.

    Accepts the asyncpg pool directly. One instance per request is fine —
    the pool handles connection reuse.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    async def get_published_post_count(self) -> int:
        """Return the count of live published posts."""
        async with self._pool.acquire() as conn:
            return int(
                await conn.fetchval("SELECT COUNT(*) FROM posts WHERE status = 'published'") or 0
            )

    async def list_posts(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        published_only: bool = True,
    ) -> dict[str, Any]:
        """Paginated post listing. Returns ``{posts, total, offset, limit}``."""
        async with self._pool.acquire() as conn:
            where_clauses: list[str] = []
            params: list = []

            if published_only:
                where_clauses.append("status = 'published'")

            where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
            params.append(limit)
            params.append(offset)
            # COUNT(*) OVER () avoids a separate COUNT round-trip.
            query = f"""
                SELECT id, title, slug, excerpt, featured_image_url, cover_image_url,
                       category_id, published_at, created_at, updated_at,
                       seo_title, seo_description, seo_keywords, status, content, author_id,
                       COUNT(*) OVER () AS total_count
                FROM posts
                {where_sql}
                ORDER BY COALESCE(published_at, created_at) DESC NULLS LAST
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """  # nosec B608 — where_sql is "" or " WHERE status = 'published'" (literal);
                 # LIMIT/OFFSET use $N placeholders

            rows = await conn.fetch(query, *params)

        total = int(rows[0]["total_count"]) if rows else 0
        posts = [{k: v for k, v in dict(row).items() if k != "total_count"} for row in rows]
        for post in posts:
            _format_timestamps(post)
            _enrich_post(post)

        return {"posts": posts, "total": total, "offset": offset, "limit": limit}

    async def search_posts(self, *, q: str, limit: int = 50) -> dict[str, Any]:
        """ILIKE search over published posts. Returns ``{posts, total, offset, limit}``."""
        if not q.strip():
            return {"posts": [], "total": 0, "offset": 0, "limit": limit}

        search_term = f"%{q}%"
        async with self._pool.acquire() as conn:
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
            _format_timestamps(post)
            _enrich_post(post)

        return {"posts": posts, "total": len(posts), "offset": 0, "limit": limit}

    async def get_post_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Return ``{data: post, meta: {tags, category}}`` or ``None`` if not found."""
        async with self._pool.acquire() as conn:
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
                return None

            post = dict(post_row)
            post_id = post["id"]
            _format_timestamps(post)
            _enrich_post(post)

            tags: list[dict] = []
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
                logger.warning(
                    "Could not fetch tags for post %s: %s", post_id, tag_error, exc_info=True
                )

            category: dict | None = None
            if post.get("category_id"):
                cat_row = await conn.fetchrow(
                    "SELECT id, name, slug FROM categories WHERE id = $1",
                    post["category_id"],
                )
                if cat_row:
                    category = dict(cat_row)

        return {"data": post, "meta": {"tags": tags, "category": category}}

    async def delete_post(self, post_id: str) -> bool:
        """Delete a post by ID. Returns ``True`` if deleted, ``False`` if not found."""
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM posts WHERE id = $1", post_id)
        return result != "DELETE 0"

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    async def list_categories(
        self, *, offset: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        """Paginated category listing. Returns ``{categories, total, offset, limit}``."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, name, slug, description, created_at, updated_at
                FROM categories
                ORDER BY name
            """)

        all_categories: list[dict] = []
        for row in rows:
            cat = dict(row)
            _format_timestamps(cat)
            all_categories.append(cat)

        total = len(all_categories)
        return {
            "categories": all_categories[offset : offset + limit],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    async def list_tags(self, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """Paginated tag listing. Returns ``{tags, total, offset, limit}``."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, name, slug, description, created_at, updated_at
                FROM tags
                ORDER BY name
            """)

        all_tags: list[dict] = []
        for row in rows:
            tag = dict(row)
            _format_timestamps(tag)
            all_tags.append(tag)

        total = len(all_tags)
        return {
            "tags": all_tags[offset : offset + limit],
            "total": total,
            "offset": offset,
            "limit": limit,
        }
