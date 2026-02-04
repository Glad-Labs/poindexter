"""
PostgreSQL-based CMS client for storing content directly to the database.

Replaces Strapi with direct PostgreSQL storage for posts, categories, tags, and media.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

import asyncpg

from ..config import config
from ..utils.data_models import BlogPost, ImageDetails

logger = logging.getLogger(__name__)


class PostgresCMSClient:
    """
    Direct PostgreSQL CMS client for storing and retrieving content.

    Replaces Strapi with PostgreSQL tables for:
    - posts: Blog articles and content
    - categories: Content categories
    - tags: Content tags
    - media: Images and media files
    """

    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize PostgreSQL CMS client.

        Args:
            database_url: PostgreSQL connection string. If not provided, uses config.DATABASE_URL
        """
        self.database_url = database_url or config.DATABASE_URL
        self.pool: Optional[asyncpg.Pool] = None
        logger.info(
            f"PostgresCMSClient initialized (Database: {self._mask_url(self.database_url)})"
        )

    def _mask_url(self, url: str) -> str:
        """Mask password in database URL for logging"""
        if "://" not in url:
            return url
        try:
            parts = url.split("://")
            if "@" in parts[1]:
                creds, rest = parts[1].split("@", 1)
                return f"{parts[0]}://***@{rest}"
            return url
        except (IndexError, ValueError):
            return url

    async def initialize(self):
        """Initialize database connection pool"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=10,
            )
            logger.info("✅ PostgreSQL CMS client pool initialized")
            await self._ensure_schema()
        except Exception as e:
            logger.error(f"❌ Failed to initialize PostgreSQL pool: {e}")
            raise

    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("PostgreSQL CMS client pool closed")

    async def _ensure_schema(self):
        """Ensure CMS tables exist"""
        if not self.pool:
            logger.warning("Pool not initialized, skipping schema creation")
            return

        async with self.pool.acquire() as conn:
            # Create categories table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS categories (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name VARCHAR(255) NOT NULL,
                    slug VARCHAR(255) UNIQUE NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """
            )

            # Create tags table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tags (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name VARCHAR(255) NOT NULL,
                    slug VARCHAR(255) UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """
            )

            # Create posts table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS posts (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    title VARCHAR(255) NOT NULL,
                    slug VARCHAR(255) UNIQUE NOT NULL,
                    content TEXT NOT NULL,
                    excerpt VARCHAR(500),
                    featured_image_url VARCHAR(500),
                    category_id UUID REFERENCES categories(id),
                    status VARCHAR(50) DEFAULT 'draft',
                    seo_title VARCHAR(255),
                    seo_description VARCHAR(500),
                    seo_keywords VARCHAR(255),
                    published_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """
            )

            # Create post_tags junction table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS post_tags (
                    post_id UUID REFERENCES posts(id) ON DELETE CASCADE,
                    tag_id UUID REFERENCES tags(id) ON DELETE CASCADE,
                    PRIMARY KEY (post_id, tag_id)
                )
            """
            )

            # Create media table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    url VARCHAR(500) NOT NULL,
                    alt_text VARCHAR(255),
                    caption TEXT,
                    description TEXT,
                    post_id UUID REFERENCES posts(id) ON DELETE SET NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """
            )

            logger.info("✅ CMS schema verified/created")

    async def create_post(self, post: BlogPost) -> tuple[str, str]:
        """
        Create a new post in PostgreSQL.

        Args:
            post: BlogPost object with content

        Returns:
            Tuple of (post_id, post_slug)
        """
        if not self.pool:
            raise RuntimeError("Database pool not initialized. Call initialize() first.")

        try:
            post_id = str(uuid4())
            # Use title for slug if slug not provided
            title = post.title or post.topic
            slug = post.slug or (title.lower().replace(" ", "-") if title else "untitled")

            # Get content from raw_content
            content = post.raw_content or ""

            async with self.pool.acquire() as conn:
                # Insert post
                await conn.execute(
                    """
                    INSERT INTO posts (id, title, slug, content, excerpt, featured_image_url, 
                                      seo_title, seo_description, seo_keywords, status)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                    post_id,
                    title,
                    slug,
                    content,
                    post.meta_description
                    or (content[:200] + "..." if len(content) > 200 else content),
                    None,  # featured_image_url handled separately below
                    post.title or post.topic,  # SEO title
                    post.meta_description,  # SEO description
                    post.primary_keyword,  # Keywords from primary_keyword
                    "published",
                )

                # Add tags if provided (from category)
                if post.category:
                    tag_id = await self._get_or_create_tag(conn, post.category)
                    await conn.execute(
                        """
                        INSERT INTO post_tags (post_id, tag_id) VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                    """,
                        post_id,
                        tag_id,
                    )

                # Add images if provided
                if post.images:
                    for image in post.images:
                        await conn.execute(
                            """
                            INSERT INTO media (url, alt_text, caption, description, post_id)
                            VALUES ($1, $2, $3, $4, $5)
                        """,
                            image.public_url,
                            image.alt_text,
                            image.caption,
                            image.description,
                            post_id,
                        )

            logger.info(f"✅ Post created in PostgreSQL: {slug} (ID: {post_id})")
            return post_id, slug

        except Exception as e:
            logger.error(f"❌ Failed to create post: {e}")
            raise

    async def _get_or_create_tag(self, conn: asyncpg.Connection, tag_name: str) -> str:
        """Get or create a tag by name"""
        slug = tag_name.lower().replace(" ", "-")

        # Try to get existing tag
        result = await conn.fetchval("SELECT id FROM tags WHERE slug = $1", slug)

        if result:
            return result

        # Create new tag
        tag_id = str(uuid4())
        await conn.execute(
            "INSERT INTO tags (id, name, slug) VALUES ($1, $2, $3)", tag_id, tag_name, slug
        )
        return tag_id

    async def get_post_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Retrieve a post by slug"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        try:
            async with self.pool.acquire() as conn:
                post = await conn.fetchrow("SELECT * FROM posts WHERE slug = $1", slug)

                if post:
                    # Convert to dict and fetch related data
                    post_dict = dict(post)

                    # Fetch tags
                    tags = await conn.fetch(
                        """
                        SELECT t.name FROM tags t
                        JOIN post_tags pt ON t.id = pt.tag_id
                        WHERE pt.post_id = $1
                    """,
                        post_dict["id"],
                    )
                    post_dict["tags"] = [t["name"] for t in tags]

                    # Fetch images
                    images = await conn.fetch(
                        "SELECT url, alt_text, caption FROM media WHERE post_id = $1",
                        post_dict["id"],
                    )
                    post_dict["images"] = [dict(img) for img in images]

                    return post_dict

                return None

        except Exception as e:
            logger.error(f"❌ Failed to get post: {e}")
            return None

    async def upload_image_metadata(
        self, image_details: ImageDetails, post_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Store image metadata in PostgreSQL media table.

        Args:
            image_details: ImageDetails object
            post_id: Optional post ID to associate image with

        Returns:
            Image ID if successful, None otherwise
        """
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        try:
            image_id = str(uuid4())

            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO media (id, url, alt_text, caption, description, post_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                """,
                    image_id,
                    image_details.public_url,
                    image_details.alt_text,
                    image_details.caption,
                    image_details.description,
                    post_id,
                )

            logger.info(f"✅ Image metadata stored: {image_id}")
            return image_id

        except Exception as e:
            logger.error(f"❌ Failed to store image metadata: {e}")
            return None

    async def get_or_create_category(self, category_name: str) -> str:
        """Get or create a category by name"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        slug = category_name.lower().replace(" ", "-")

        try:
            async with self.pool.acquire() as conn:
                # Try to get existing
                result = await conn.fetchval("SELECT id FROM categories WHERE slug = $1", slug)

                if result:
                    return result

                # Create new
                category_id = str(uuid4())
                await conn.execute(
                    "INSERT INTO categories (id, name, slug) VALUES ($1, $2, $3)",
                    category_id,
                    category_name,
                    slug,
                )
                return category_id

        except Exception as e:
            logger.error(f"❌ Failed to get/create category: {e}")
            raise

    async def health_check(self) -> bool:
        """Check if database is accessible"""
        if not self.pool:
            return False

        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1")
                return result == 1
        except Exception as e:
            logger.error(f"❌ Health check failed: {e}")
            return False
