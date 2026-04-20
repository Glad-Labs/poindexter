"""
Blog Publisher Agent - Bridge agent for workflow system

Wraps database service to create blog posts.

This agent:
1. Takes workflow inputs (content, title, topic, featured_image, tags)
2. Calls database_service.create_post()
3. Returns results compatible with workflow executor
"""

from typing import Any
from uuid import uuid4

from services.logger_config import get_logger

logger = get_logger(__name__)


class BlogPublisherAgent:
    """
    Agent that creates and publishes blog posts to the database.

    Callable as a workflow phase with inputs:
    - content: str (required) - Blog post content (markdown)
    - title: str (optional) - Post title (defaults to topic)
    - topic: str (optional) - Blog topic
    - featured_image: dict (optional) - Featured image metadata
    - tags: list[str] (optional) - Post tags/keywords
    - category: str (optional) - Post category
    - publish: bool (optional) - Whether to publish immediately (default: True)
    """

    def __init__(self, database_service=None):
        """
        Initialize blog publisher agent

        Args:
            database_service: Optional DatabaseService instance. If not provided,
                            will be lazily initialized on first use.
        """
        logger.info("Initializing BlogPublisherAgent")
        self.database_service = database_service
        self._db_initialized = False if database_service is None else True

    async def _ensure_database_service(self):
        """Lazily initialize database service only when needed"""
        if self.database_service is None:
            logger.debug("[BlogPublisherAgent] Lazy-loading DatabaseService...")
            from services.database_service import DatabaseService

            self.database_service = DatabaseService()
            await self.database_service.initialize()
            self._db_initialized = True

    async def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        Create and publish a blog post.

        Args:
            inputs: Dict with keys:
                - content (required): str, blog post content
                - title (optional): str, post title
                - topic (optional): str, blog topic
                - featured_image (optional): dict, image metadata
                - tags (optional): list[str], post keywords
                - category (optional): str, post category
                - publish (optional): bool, publish immediately

        Returns:
            Dict with keys:
                - post_id: str, created post ID
                - slug: str, post URL slug
                - url: str, full post URL
                - title: str, post title
                - status: str, "success" or "failed"
                - error: str (if failed)
        """

        try:
            # Ensure database service is initialized
            await self._ensure_database_service()

            logger.info("[BlogPublisherAgent] Creating blog post")

            content = inputs.get("content")
            if not content or len(content.strip()) < 10:
                raise ValueError("Content must be at least 10 characters")

            # Extract parameters with defaults
            title = inputs.get("title")
            topic = inputs.get("topic", "Untitled")
            featured_image = inputs.get("featured_image")
            tags = inputs.get("tags", [])
            category = inputs.get("category", "News")
            publish = inputs.get("publish", True)

            # Use title if provided, otherwise use topic
            if not title:
                title = topic

            # Generate slug from title
            slug = title.lower().replace(" ", "-").replace("'", "").replace("?", "")

            # Prepare post data
            post_data = {
                "id": str(uuid4()),
                "title": title,
                "slug": slug,
                "content": content,
                "excerpt": content[:200] if len(content) > 200 else content,
                "category": category,
                "status": "published" if publish else "draft",
                "published": publish,
            }

            # Add featured image if provided
            if featured_image:
                if isinstance(featured_image, dict):
                    post_data["featured_image_url"] = featured_image.get("url")
                    post_data["featured_image_alt"] = featured_image.get(
                        "alt_text", "Featured Image"
                    )
                    post_data["featured_image_photographer"] = featured_image.get(
                        "photographer", "Unknown"
                    )
                else:
                    # If featured_image is just a URL string
                    post_data["featured_image_url"] = str(featured_image)

            # Add tags if provided
            if tags:
                if isinstance(tags, list):
                    post_data["seo_keywords"] = ", ".join(str(t) for t in tags)
                else:
                    post_data["seo_keywords"] = str(tags)

            # Add SEO metadata
            post_data["seo_description"] = content[:160] if len(content) > 160 else content
            post_data["seo_title"] = title

            logger.info(f"[BlogPublisherAgent] Creating post: {title}")

            # Create post in database
            result = await self.database_service.create_post(post_data)  # type: ignore[attr-defined]

            logger.info(f"[BlogPublisherAgent] Post created successfully: {slug}")

            # Extract result data
            post_id = result.get("id") if isinstance(result, dict) else str(result)

            return {
                "post_id": post_id,
                "slug": slug,
                "url": f"/posts/{slug}",
                "title": title,
                "status": "success",
            }

        except Exception as e:
            logger.error(f"[BlogPublisherAgent] Error: {str(e)}", exc_info=True)
            return {
                "status": "failed",
                "error": str(e),
                "post_id": None,
                "slug": None,
                "url": None,
            }


def get_blog_publisher_agent() -> BlogPublisherAgent:
    """Factory function for BlogPublisherAgent"""
    return BlogPublisherAgent()
