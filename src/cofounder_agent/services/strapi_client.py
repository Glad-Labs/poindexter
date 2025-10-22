"""
Strapi Client Service

Handles all interactions with Strapi CMS:
- Blog post creation
- Blog post publishing (draft/published states)
- Blog post retrieval
- Strapi API authentication
- Support for multiple environments (prod/staging)
"""

import os
import aiohttp
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


logger = logging.getLogger(__name__)


class StrapiEnvironment(str, Enum):
    """Supported Strapi environments"""
    PRODUCTION = "production"
    STAGING = "staging"


@dataclass
class BlogPostData:
    """Blog post structure for Strapi"""
    title: str
    content: str
    summary: str
    tags: List[str] = None
    categories: List[str] = None
    featured_image_url: Optional[str] = None
    author: str = "AI Co-Founder"
    status: str = "draft"  # 'draft' or 'published'

    def to_strapi_format(self) -> Dict[str, Any]:
        """Convert to Strapi content API format"""
        return {
            "data": {
                "title": self.title,
                "content": self.content,
                "summary": self.summary,
                "tags": self.tags or [],
                "categories": self.categories or [],
                "featured_image": self.featured_image_url,
                "author": self.author,
                "publishedAt": datetime.now().isoformat() if self.status == "published" else None,
            }
        }


class StrapiClient:
    """Client for interacting with Strapi CMS"""

    def __init__(self, environment: StrapiEnvironment = StrapiEnvironment.PRODUCTION):
        """
        Initialize Strapi client

        Args:
            environment: 'production' or 'staging'
        """
        self.environment = environment
        self.base_url = self._get_base_url(environment)
        self.api_token = self._get_api_token(environment)
        
        if not self.base_url or not self.api_token:
            raise ValueError(
                f"Strapi {environment} credentials not configured. "
                f"Set STRAPI_API_URL and STRAPI_API_TOKEN (or STRAPI_STAGING_* for staging)"
            )

        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def _get_base_url(self, environment: StrapiEnvironment) -> Optional[str]:
        """Get Strapi base URL from environment variables"""
        if environment == StrapiEnvironment.PRODUCTION:
            return os.getenv("STRAPI_API_URL") or "https://glad-labs-website-production.up.railway.app/api"
        else:
            return os.getenv("STRAPI_STAGING_URL") or os.getenv("STRAPI_API_URL")

    def _get_api_token(self, environment: StrapiEnvironment) -> Optional[str]:
        """Get Strapi API token from environment variables"""
        if environment == StrapiEnvironment.PRODUCTION:
            return os.getenv("STRAPI_API_TOKEN")
        else:
            return os.getenv("STRAPI_STAGING_TOKEN") or os.getenv("STRAPI_API_TOKEN")

    async def create_blog_post(
        self,
        title: str,
        content: str,
        summary: str,
        tags: Optional[List[str]] = None,
        categories: Optional[List[str]] = None,
        featured_image_url: Optional[str] = None,
        publish: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a blog post in Strapi

        Args:
            title: Blog post title
            content: Blog post content (markdown or HTML)
            summary: Short summary/excerpt
            tags: List of tag names
            categories: List of category names
            featured_image_url: URL to featured image
            publish: Whether to publish immediately (True) or save as draft (False)

        Returns:
            Response from Strapi containing created post data

        Raises:
            aiohttp.ClientError: If Strapi API call fails
            ValueError: If response is invalid
        """
        blog_post = BlogPostData(
            title=title,
            content=content,
            summary=summary,
            tags=tags or [],
            categories=categories or [],
            featured_image_url=featured_image_url,
            status="published" if publish else "draft",
        )

        payload = blog_post.to_strapi_format()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/articles",
                    json=payload,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status not in (200, 201):
                        error_text = await response.text()
                        logger.error(f"Strapi API error: {response.status} - {error_text}")
                        raise ValueError(f"Strapi API error: {response.status} - {error_text}")

                    result = await response.json()
                    logger.info(f"Blog post created in Strapi: {result['data']['id']}")
                    return result

        except aiohttp.ClientError as e:
            logger.error(f"Strapi connection error: {e}")
            raise

    async def publish_blog_post(self, post_id: int) -> Dict[str, Any]:
        """
        Publish a draft blog post

        Args:
            post_id: Strapi article ID

        Returns:
            Updated article data with publishedAt timestamp

        Raises:
            aiohttp.ClientError: If Strapi API call fails
        """
        payload = {
            "data": {
                "publishedAt": datetime.now().isoformat(),
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{self.base_url}/articles/{post_id}",
                    json=payload,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status not in (200, 201):
                        error_text = await response.text()
                        logger.error(f"Strapi publish error: {response.status} - {error_text}")
                        raise ValueError(f"Strapi publish error: {response.status} - {error_text}")

                    result = await response.json()
                    logger.info(f"Blog post published: {post_id}")
                    return result

        except aiohttp.ClientError as e:
            logger.error(f"Strapi connection error: {e}")
            raise

    async def get_blog_post(self, post_id: int) -> Dict[str, Any]:
        """
        Retrieve a blog post by ID

        Args:
            post_id: Strapi article ID

        Returns:
            Article data

        Raises:
            aiohttp.ClientError: If Strapi API call fails
            ValueError: If post not found
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/articles/{post_id}",
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status == 404:
                        raise ValueError(f"Blog post not found: {post_id}")

                    if response.status not in (200, 201):
                        error_text = await response.text()
                        logger.error(f"Strapi API error: {response.status} - {error_text}")
                        raise ValueError(f"Strapi API error: {response.status} - {error_text}")

                    result = await response.json()
                    return result

        except aiohttp.ClientError as e:
            logger.error(f"Strapi connection error: {e}")
            raise

    async def list_blog_posts(
        self,
        limit: int = 10,
        offset: int = 0,
        published_only: bool = False,
    ) -> Dict[str, Any]:
        """
        List blog posts

        Args:
            limit: Number of posts to return (default 10)
            offset: Pagination offset (default 0)
            published_only: Filter to published posts only

        Returns:
            List of articles with pagination metadata

        Raises:
            aiohttp.ClientError: If Strapi API call fails
        """
        filters = "&filters[publishedAt][$notNull]=true" if published_only else ""
        url = f"{self.base_url}/articles?pagination[limit]={limit}&pagination[start]={offset}{filters}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status not in (200, 201):
                        error_text = await response.text()
                        logger.error(f"Strapi API error: {response.status} - {error_text}")
                        raise ValueError(f"Strapi API error: {response.status} - {error_text}")

                    result = await response.json()
                    return result

        except aiohttp.ClientError as e:
            logger.error(f"Strapi connection error: {e}")
            raise

    async def delete_blog_post(self, post_id: int) -> bool:
        """
        Delete a blog post

        Args:
            post_id: Strapi article ID

        Returns:
            True if successful

        Raises:
            aiohttp.ClientError: If Strapi API call fails
            ValueError: If deletion failed
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{self.base_url}/articles/{post_id}",
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status == 204:
                        logger.info(f"Blog post deleted: {post_id}")
                        return True

                    if response.status not in (200, 201):
                        error_text = await response.text()
                        logger.error(f"Strapi delete error: {response.status} - {error_text}")
                        raise ValueError(f"Strapi delete error: {response.status} - {error_text}")

                    result = await response.json()
                    logger.info(f"Blog post deleted: {post_id}")
                    return True

        except aiohttp.ClientError as e:
            logger.error(f"Strapi connection error: {e}")
            raise


# Example usage in main.py:
# from strapi_client import StrapiClient, StrapiEnvironment
#
# # Initialize for production
# strapi = StrapiClient(StrapiEnvironment.PRODUCTION)
#
# # Create a blog post
# result = await strapi.create_blog_post(
#     title="How to Optimize AI Costs",
#     content="# How to Optimize AI Costs\n\n...",
#     summary="A guide to reducing AI API costs",
#     tags=["ai", "cost-optimization"],
#     categories=["Technical Guides"],
#     publish=False  # Save as draft first
# )
#
# post_id = result['data']['id']
#
# # Later, publish the post
# await strapi.publish_blog_post(post_id)
