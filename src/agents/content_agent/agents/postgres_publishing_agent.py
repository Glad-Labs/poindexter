"""
PostgreSQL-based Publishing Agent - Stores content directly to PostgreSQL.

Replaces Strapi publishing with direct database inserts to posts table.
"""

import logging
from typing import Optional, Tuple
from ..config import config
from ..utils.data_models import BlogPost
from ..services.postgres_cms_client import PostgresCMSClient


logger = logging.getLogger(__name__)


class PostgreSQLPublishingAgent:
    """
    PostgreSQL-based publishing agent that stores content directly to the database.
    
    This agent:
    1. Validates final content
    2. Creates/updates post record in PostgreSQL posts table
    3. Stores associated tags and images in junction tables
    4. Returns post ID and slug for confirmation
    
    No Strapi integration - pure PostgreSQL storage.
    """

    def __init__(self, cms_client: Optional[PostgresCMSClient] = None):
        """
        Initialize PostgreSQL Publishing Agent.
        
        Args:
            cms_client: PostgresCMSClient instance. If not provided, creates new instance.
        """
        logger.info("Initializing PostgreSQL Publishing Agent...")
        self.cms_client = cms_client or PostgresCMSClient()
        
    async def initialize(self):
        """Initialize database connections"""
        if not hasattr(self.cms_client, 'pool') or self.cms_client.pool is None:
            await self.cms_client.initialize()

    def run(self, post: BlogPost) -> BlogPost:
        """
        Process and prepare post for publishing to PostgreSQL.
        
        This is the synchronous wrapper. For actual publishing, use run_async().
        
        Args:
            post: BlogPost object with final content
            
        Returns:
            BlogPost with database ID and URL populated
        """
        logger.info(f"PublishingAgent: Preparing to publish '{post.title or post.topic}' to PostgreSQL.")
        
        try:
            # Validate post has required fields
            if not post.raw_content:
                raise ValueError("Post must have raw_content")
            
            # Ensure slug is set
            if not post.slug:
                title = post.title or post.topic
                post.slug = title.lower().replace(" ", "-").replace("'", "").replace("?", "")
            
            # Summary of what will be stored
            logger.info(f"  ✅ Title: {post.title or post.topic}")
            logger.info(f"  ✅ Slug: {post.slug}")
            logger.info(f"  ✅ Content length: {len(post.raw_content)} chars")
            logger.info(f"  ✅ Category: {post.category}")
            logger.info(f"  ✅ Images: {len(post.images or [])} images")
            
            # Set default meta_description if not provided
            if not post.meta_description:
                content_preview = post.raw_content[:200] if post.raw_content else ""
                post.meta_description = content_preview + "..." if len(post.raw_content or "") > 200 else content_preview
            
            logger.info(f"✅ Content prepared and ready for PostgreSQL storage")
            
            # Store database connection info in post for later async processing
            post.strapi_id = None  # Will be replaced with actual UUID after async insert
            post.strapi_url = f"/posts/{post.slug}"
            
            return post

        except Exception as e:
            logger.error(f"❌ Publishing preparation failed: {e}")
            raise

    async def run_async(self, post: BlogPost) -> Tuple[str, str]:
        """
        Actually publish the post to PostgreSQL (async version).
        
        Args:
            post: BlogPost object with final content
            
        Returns:
            Tuple of (post_id, post_slug)
        """
        try:
            # Ensure client is initialized
            await self.initialize()
            
            # Create post in PostgreSQL
            post_id, post_slug = await self.cms_client.create_post(post)
            
            logger.info(f"✅ Post published to PostgreSQL: {post_slug}")
            logger.info(f"   ID: {post_id}")
            logger.info(f"   URL: /posts/{post_slug}")
            
            return post_id, post_slug

        except Exception as e:
            logger.error(f"❌ Failed to publish post: {e}")
            raise

    async def close(self):
        """Close database connections"""
        if hasattr(self.cms_client, 'close'):
            await self.cms_client.close()
