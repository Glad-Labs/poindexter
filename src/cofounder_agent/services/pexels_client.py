"""
Pexels API Client for Free Stock Image Search

Provides access to millions of royalty-free images for content generation.
Free tier with unlimited searches.

Cost: $0/month (vs $0.02/image with DALL-E)

ASYNC-FIRST: All operations use httpx async client (no blocking I/O)
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class PexelsClient:
    """
    Pexels API client for searching and retrieving royalty-free images.

    Features:
    - Free API access to 500K+ images
    - Multiple orientations and sizes
    - Photographer attribution included
    - Fallback to curated images
    """

    BASE_URL = "https://api.pexels.com/v1"

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Pexels client.

        Args:
            api_key: Pexels API key (defaults to PEXELS_API_KEY env var)
        """
        self.api_key = api_key or os.getenv("PEXELS_API_KEY")
        if not self.api_key:
            logger.warning("Pexels API key not configured - image search will be unavailable")

        self.headers = {"Authorization": self.api_key} if self.api_key else {}

    def _is_content_appropriate(self, photo: Dict[str, Any]) -> bool:
        """
        Filter out inappropriate content based on available metadata.

        Returns:
            True if image is appropriate for blog content, False otherwise
        """
        # Check alt text and photographer for content warnings
        alt = (photo.get("alt", "") or "").lower()
        photographer = (photo.get("photographer", "") or "").lower()

        # Block known NSFW/inappropriate patterns
        inappropriate_patterns = [
            "nsfw",
            "adult",
            "nude",
            "sexy",
            "lingerie",
            "bikini",
            "swimsuit",
            "erotic",
            "sensual",
            "intimate",
            "private",
            "naked",
            "bare",
            "exposed",
            "provocative",
            "risque",
        ]

        for pattern in inappropriate_patterns:
            if pattern in alt or pattern in photographer:
                logger.debug(f"Filtering inappropriate image: {alt}")
                return False

        return True

    async def search_images(
        self, query: str, per_page: int = 5, orientation: str = "landscape", size: str = "medium"
    ) -> List[Dict[str, Any]]:
        """
        Search for images matching query (async-only via httpx).

        Args:
            query: Search keywords
            per_page: Number of results per page
            orientation: Image orientation
            size: Image size

        Returns:
            List of filtered, appropriate image dictionaries
        """
        if not self.api_key:
            logger.warning("Pexels API key not configured")
            return []

        try:
            params = {
                "query": query,
                "per_page": min(per_page * 2, 80),  # Fetch more to filter out inappropriate ones
                "orientation": orientation,
                "size": size,
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/search", headers=self.headers, params=params
                )
                response.raise_for_status()
                data = response.json()

                photos = data.get("photos", [])
                logger.info(f"Pexels search for '{query}' returned {len(photos)} results")

                # Filter for appropriate content
                appropriate_photos = [
                    photo for photo in photos if self._is_content_appropriate(photo)
                ]

                filtered_count = len(photos) - len(appropriate_photos)
                if filtered_count > 0:
                    logger.info(f"Filtered out {filtered_count} inappropriate images")

                return [
                    {
                        "url": photo["src"]["large"],
                        "thumbnail": photo["src"]["small"],
                        "photographer": photo.get("photographer", "Unknown"),
                        "photographer_url": photo.get("photographer_url", ""),
                        "width": photo.get("width"),
                        "height": photo.get("height"),
                        "alt": photo.get("alt", ""),
                        "source": "pexels",
                        "searched_query": query,
                    }
                    for photo in appropriate_photos[:per_page]
                ]

        except Exception as e:
            logger.error(f"Pexels search failed: {e}")
            return []

    async def get_featured_image(
        self, topic: str, keywords: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get featured image for blog post topic (async-only via httpx).

        Args:
            topic: Blog post topic
            keywords: Additional keywords to try

        Returns:
            First matching image dict or None
        """
        search_queries = [topic]
        if keywords:
            search_queries.extend(keywords[:3])

        for query in search_queries:
            try:
                images = await self.search_images(query, per_page=1)
                if images:
                    logger.info(f"Found featured image for '{query}' via Pexels")
                    return images[0]
            except Exception as e:
                logger.warning(f"Error searching for '{query}': {e}")

        logger.warning(f"No featured image found for topic: {topic}")
        return None

    async def get_images_for_gallery(
        self, topic: str, count: int = 5, keywords: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get multiple images for content gallery (async-only via httpx).

        Args:
            topic: Blog post topic
            count: Number of images needed
            keywords: Additional keywords

        Returns:
            List of image dicts
        """
        search_queries = [topic]
        if keywords:
            search_queries.extend(keywords)

        all_images = []

        for query in search_queries[:3]:  # Try up to 3 queries
            try:
                images = await self.search_images(query, per_page=count, page=1)
                all_images.extend(images)

                if len(all_images) >= count:
                    logger.info(f"Found {len(all_images)} images for gallery")
                    return all_images[:count]
            except Exception as e:
                logger.warning(f"Error searching for gallery images '{query}': {e}")

        logger.info(f"Found {len(all_images)} gallery images")
        return all_images

    @staticmethod
    def generate_image_markdown(image: Dict[str, Any], caption: str = "") -> str:
        """
        Generate markdown for image with attribution.

        Args:
            image: Image dictionary from search results
            caption: Optional caption for image

        Returns:
            Markdown formatted image with attribution
        """
        photographer_link = ""
        if image.get("photographer_url"):
            photographer_link = f"[{image['photographer']}]({image['photographer_url']})"
        else:
            photographer_link = image.get("photographer", "Unknown Photographer")

        md_caption = caption or image.get("alt", "")

        return f"""![{md_caption}]({image['url']})
*Photo by {photographer_link} on Pexels*"""


# Initialize client with API key from environment
def get_pexels_client() -> PexelsClient:
    """Factory function to create Pexels client."""
    return PexelsClient(os.getenv("PEXELS_API_KEY"))
