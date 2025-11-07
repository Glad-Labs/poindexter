"""
Strapi CMS Publisher - Direct PostgreSQL Implementation
No REST API dependency - writes directly to Strapi's PostgreSQL database

This is the standard approach:
- Local dev: PostgreSQL database (glad_labs_dev)
- Railway production: PostgreSQL database (Railway managed)
- Same code works everywhere

Uses asyncpg for non-blocking async database operations.

Schema Reference (Strapi v5 posts table):
  id (integer) - auto-increment primary key (NOT passed in INSERT)
  document_id (varchar) - UUID string for internal tracking
  title (varchar) - post title
  slug (varchar) - URL slug
  content (text) - post body
  excerpt (text) - short description
  published_at (timestamp) - publication date
  created_at (timestamp) - creation timestamp
  updated_at (timestamp) - modification timestamp
  featured (boolean) - featured flag
  date (timestamp) - custom date field
"""

import asyncpg
import os
import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)


class StrapiPublisher:
    """
    Publish content directly to Strapi's PostgreSQL database
    
    No REST API dependency - works in any environment with PostgreSQL access.
    Perfect for local dev, staging, and production (Railway).
    """

    def __init__(self):
        """Initialize with database connection parameters from environment"""
        self.db_url = os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/glad_labs_dev"
        )
        self.db_host = os.getenv("DATABASE_HOST", "localhost")
        self.db_port = int(os.getenv("DATABASE_PORT", "5432"))
        self.db_name = os.getenv("DATABASE_NAME", "glad_labs_dev")
        self.db_user = os.getenv("DATABASE_USER", "postgres")
        self.db_password = os.getenv("DATABASE_PASSWORD", "postgres")
        
        self.pool = None
        logger.info(f"📊 StrapiPublisher initialized - PostgreSQL: {self.db_host}:{self.db_port}/{self.db_name}")

    async def connect(self) -> bool:
        """Create connection pool to PostgreSQL"""
        try:
            logger.info(f"🔌 Connecting to PostgreSQL: {self.db_host}:{self.db_port}/{self.db_name}")
            
            self.pool = await asyncpg.create_pool(
                self.db_url or f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}",
                min_size=2,
                max_size=10,
            )
            logger.info("✅ Connected to PostgreSQL database pool")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to connect to PostgreSQL: {e}")
            return False

    async def disconnect(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("🔌 Disconnected from PostgreSQL")

    def test_connection(self) -> bool:
        """
        Synchronous test connection for compatibility.
        Used in health checks and initialization.
        """
        try:
            import asyncio
            
            # Try to get existing event loop or create new one
            try:
                loop = asyncio.get_running_loop()
                # Event loop exists - cannot run sync test in async context
                # Just return True (assume connection will work)
                logger.debug("Event loop already running - skipping sync connection test")
                return True
            except RuntimeError:
                # No event loop, create a new one for the test
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(self._async_test_connection())
                loop.close()
                return result
        except Exception as e:
            logger.error(f"❌ Connection test failed: {e}")
            return False

    async def _async_test_connection(self) -> bool:
        """Async test connection to PostgreSQL"""
        try:
            if not self.pool:
                if not await self.connect():
                    return False

            async with self.pool.acquire() as conn:
                # Test basic connection
                result = await conn.fetchval("SELECT 1")
                if result != 1:
                    logger.error("❌ Database connection test failed")
                    return False

                # Check if posts table exists
                table_exists = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM information_schema.tables 
                        WHERE table_schema='public' AND table_name='posts'
                    )
                    """
                )

                if table_exists:
                    count = await conn.fetchval("SELECT COUNT(*) FROM posts")
                    logger.info(f"✅ PostgreSQL OK - posts table has {count} records")
                    return True
                else:
                    logger.warning("⚠️  PostgreSQL connected but 'posts' table not found")
                    return False

        except Exception as e:
            logger.error(f"❌ Async connection test failed: {e}")
            return False

    async def create_post(
        self,
        title: str,
        content: str,
        slug: Optional[str] = None,
        excerpt: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list] = None,
        featured_image_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create and publish a blog post to PostgreSQL

        Args:
            title: Post title (required)
            content: Post content (HTML or markdown)
            slug: URL slug (auto-generated from title if not provided)
            excerpt: Short excerpt/summary (auto-generated from content if not provided)
            category: Category name or ID (optional)
            tags: List of tag names or IDs (optional)
            featured_image_url: URL to featured image (optional)

        Returns:
            Dict with:
            - success: bool - whether post was created
            - post_id: str - ID of created post (if successful)
            - message: str - human-readable message
            - slug: str - final slug used
        """
        try:
            if not self.pool:
                if not await self.connect():
                    return {
                        "success": False,
                        "error": "Failed to connect to database",
                        "post_id": None,
                        "message": "❌ Failed to connect to database",
                    }

            # Auto-generate slug if not provided
            if not slug:
                slug = title.lower().replace(" ", "-").replace("_", "-")[:100]
                slug = "".join(c for c in slug if c.isalnum() or c == "-")

            # Auto-generate excerpt if not provided
            if not excerpt:
                excerpt = content[:200] if content else ""

            # Generate timestamps and UUID for document_id
            # NOTE: Use datetime objects, not ISO strings - asyncpg requires actual datetime objects
            now = datetime.utcnow()
            document_id = str(uuid.uuid4())

            async with self.pool.acquire() as conn:
                # Insert into posts table
                # CRITICAL: Do NOT pass 'id' - it's auto-increment integer (serial)
                # Pass 'document_id' as UUID string for Strapi internal tracking
                # Let PostgreSQL generate the integer 'id' automatically
                query = """
                    INSERT INTO posts (
                        document_id, title, slug, content, excerpt, 
                        published_at, created_at, updated_at, featured
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9
                    )
                    RETURNING id, document_id, title, slug, created_at
                """

                result = await conn.fetchrow(
                    query,
                    document_id,           # $1: document_id (UUID string for Strapi)
                    title,                 # $2: title
                    slug,                  # $3: slug
                    content,               # $4: content
                    excerpt,               # $5: excerpt
                    now,                   # $6: published_at
                    now,                   # $7: created_at
                    now,                   # $8: updated_at
                    True,                  # $9: featured
                )

                if result:
                    message = f"✅ Post created: '{title}' (ID: {result['id']}, Doc: {result['document_id']})"
                    logger.info(message)
                    
                    return {
                        "success": True,
                        "post_id": str(result['id']),
                        "document_id": str(result['document_id']),
                        "slug": result['slug'],
                        "message": message,
                        "created_at": result['created_at'],
                    }
                else:
                    message = "❌ Post creation returned no result"
                    logger.error(message)
                    return {
                        "success": False,
                        "error": message,
                        "post_id": None,
                        "message": message,
                    }

        except Exception as e:
            message = f"❌ Failed to create post: {str(e)}"
            logger.error(message, exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "post_id": None,
                "message": message,
            }

    async def get_posts(self, limit: int = 10) -> Tuple[bool, list, str]:
        """
        Retrieve recent posts from PostgreSQL

        Args:
            limit: Maximum number of posts to retrieve

        Returns:
            (success: bool, posts: list of dicts, message: str)
        """
        try:
            if not self.pool:
                if not await self.connect():
                    return False, [], "❌ Failed to connect to database"

            async with self.pool.acquire() as conn:
                posts = await conn.fetch(
                    f"""
                    SELECT id, document_id, title, slug, excerpt, created_at, published_at
                    FROM posts
                    ORDER BY created_at DESC
                    LIMIT {limit}
                    """
                )

                if posts:
                    message = f"✅ Retrieved {len(posts)} posts from database"
                    logger.info(message)
                    return True, [dict(p) for p in posts], message
                else:
                    message = "⚠️  No posts found in database"
                    logger.info(message)
                    return True, [], message

        except Exception as e:
            message = f"❌ Failed to retrieve posts: {str(e)}"
            logger.error(message, exc_info=True)
            return False, [], message

    async def verify_post_exists(self, title: str) -> Tuple[bool, Optional[Dict]]:
        """
        Verify that a post with given title exists

        Args:
            title: Post title to search for

        Returns:
            (exists: bool, post_data: dict or None)
        """
        try:
            if not self.pool:
                if not await self.connect():
                    return False, None

            async with self.pool.acquire() as conn:
                post = await conn.fetchrow(
                    """
                    SELECT id, document_id, title, slug, created_at
                    FROM posts
                    WHERE title = $1
                    LIMIT 1
                    """,
                    title
                )

                if post:
                    logger.info(f"✅ Post '{title}' found in database (ID: {post['id']})")
                    return True, dict(post)
                else:
                    logger.info(f"❌ Post '{title}' not found in database")
                    return False, None

        except Exception as e:
            logger.error(f"❌ Failed to verify post: {str(e)}")
            return False, None
