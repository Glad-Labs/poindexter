"""
Strapi CMS Client - Simple posting interface for blog articles

Handles publishing generated content to Strapi CMS.
"""

import logging
import httpx
import os
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)


class StrapiPublisher:
    """Simple client for posting blog articles to Strapi CMS"""

    def __init__(self, strapi_url: Optional[str] = None, api_token: Optional[str] = None):
        """
        Initialize Strapi publisher

        Args:
            strapi_url: Strapi instance URL (e.g., http://localhost:1337 or https://cms.example.com)
            api_token: Strapi API token for authentication (from Strapi admin settings)
        """
        self.strapi_url = strapi_url or os.getenv("STRAPI_URL", "http://localhost:1337")
        self.api_token = api_token or os.getenv("STRAPI_API_TOKEN", "")
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_token}" if self.api_token else ""
        }

        logger.info(f"StrapiPublisher initialized: {self.strapi_url}")

        if not self.api_token:
            logger.warning("⚠️ No STRAPI_API_TOKEN provided - posting may fail")

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
        Create and publish a blog post to Strapi

        Args:
            title: Post title
            content: Post content (markdown or HTML)
            slug: URL slug (auto-generated from title if not provided)
            excerpt: Short excerpt/summary
            category: Category name or slug
            tags: List of tag names
            featured_image_url: URL to featured image

        Returns:
            Dict with post ID, URL, and status
        """
        try:
            # Auto-generate slug if not provided
            if not slug:
                slug = title.lower().replace(" ", "-").replace("_", "-")[:100]
                slug = "".join(c for c in slug if c.isalnum() or c == "-")

            # Prepare post data for Strapi
            post_data = {
                "data": {
                    "title": title,
                    "content": content,
                    "slug": slug,
                    "excerpt": excerpt or content[:200],
                    "status": "published",  # Publish immediately
                }
            }

            # Add optional fields if provided
            if category:
                post_data["data"]["category"] = category
            if tags:
                post_data["data"]["tags"] = tags
            if featured_image_url:
                post_data["data"]["featured_image_url"] = featured_image_url

            # POST to Strapi API
            async with httpx.AsyncClient() as client:
                endpoint = f"{self.strapi_url}/api/articles"
                
                logger.debug(f"Posting to Strapi: {endpoint}")
                logger.debug(f"Title: {title}, Slug: {slug}")

                response = await client.post(
                    endpoint,
                    json=post_data,
                    headers=self.headers,
                    timeout=30.0,
                )

                response.raise_for_status()

                result = response.json()
                post_id = result.get("data", {}).get("id")
                post_url = f"{self.strapi_url}/articles/{slug}"

                logger.info(f"✅ Post created in Strapi: ID={post_id}, URL={post_url}")

                return {
                    "success": True,
                    "post_id": post_id,
                    "post_url": post_url,
                    "slug": slug,
                    "strapi_response": result,
                }

        except httpx.HTTPError as e:
            error_msg = f"HTTP error posting to Strapi: {e}"
            logger.error(f"❌ {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "post_id": None,
                "post_url": None,
            }
        except Exception as e:
            error_msg = f"Error posting to Strapi: {e}"
            logger.error(f"❌ {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "post_id": None,
                "post_url": None,
            }

    def create_post_from_content(
        self,
        title: str,
        content: str,
        slug: Optional[str] = None,
        excerpt: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Synchronous wrapper for creating a post (for compatibility with existing code)

        Uses asyncio to run the async method.
        """
        import asyncio

        # Check if there's already an event loop running
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop, create one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                self.create_post(
                    title=title,
                    content=content,
                    slug=slug,
                    excerpt=excerpt,
                    category=category,
                    tags=tags,
                )
            )
            loop.close()
            return result
        else:
            # Event loop already running, create a task
            raise RuntimeError(
                "Cannot use synchronous create_post_from_content with async event loop running. "
                "Use create_post instead."
            )

    def test_connection(self) -> bool:
        """Test connection to Strapi"""
        try:
            import httpx

            response = httpx.get(
                f"{self.strapi_url}/api/articles",
                headers=self.headers,
                timeout=10.0,
            )
            if response.status_code == 200 or response.status_code == 401:
                logger.info(f"✅ Strapi connection successful: {self.strapi_url}")
                return True
            else:
                logger.error(f"❌ Strapi connection failed: HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"❌ Cannot connect to Strapi: {e}")
            return False
