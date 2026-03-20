"""
Blog Image Agent - Bridge agent for workflow system

Wraps image_service to be callable as a workflow phase.

This agent:
1. Takes workflow inputs (topic, keywords, image_count)
2. Calls image_service.search_featured_image()
3. Returns results compatible with workflow executor
"""

from services.logger_config import get_logger
from typing import Any, Dict

from services.image_service import get_image_service

logger = get_logger(__name__)
class BlogImageAgent:
    """
    Agent that searches for featured images for blog posts.

    Callable as a workflow phase with inputs:
    - topic: str (required) - Blog topic to search for images
    - keywords: list[str] (optional) - Additional keywords to try
    - orientation: str (optional) - Image orientation (landscape, portrait, square)
    - size: str (optional) - Image size (small, medium, large)
    - image_count: int (optional) - Number of images to retrieve for gallery
    - page: int (optional) - Results page number for pagination
    """

    def __init__(self):
        """Initialize blog image agent"""
        logger.info("Initializing BlogImageAgent")
        self.image_service = get_image_service()

    async def run(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Search for featured image for blog post.

        Args:
            inputs: Dict with keys:
                - topic (required): str, blog topic
                - keywords (optional): list[str], additional keywords
                - orientation (optional): str, image orientation
                - size (optional): str, image size
                - image_count (optional): int, number of gallery images
                - page (optional): int, results page number

        Returns:
            Dict with keys:
                - featured_image: dict, featured image metadata or None
                  - url: str, image URL
                  - thumbnail: str, thumbnail URL
                  - photographer: str, photographer name
                  - photographer_url: str, photographer profile URL
                  - alt_text: str, alt text
                  - caption: str, image caption
                  - source: str, image source (pexels)
                - gallery_images: list[dict], gallery images (if image_count specified)
                - image_markdown: str, markdown with featured image and attribution
                - status: str, "success" or "failed"
                - error: str (if failed)
        """

        try:
            logger.info(f"[BlogImageAgent] Searching for images for topic: {inputs.get('topic')}")

            topic = inputs.get("topic")
            if not topic or len(topic.strip()) < 3:
                raise ValueError("Topic must be at least 3 characters")

            # Extract parameters with defaults
            keywords = inputs.get("keywords", [])
            orientation = inputs.get("orientation", "landscape")
            size = inputs.get("size", "medium")
            image_count = inputs.get("image_count", 1)
            page = inputs.get("page", 1)

            # Search for featured image
            featured_image = await self.image_service.search_featured_image(
                topic=topic,
                keywords=keywords,
                orientation=orientation,
                size=size,
                page=page,
            )

            # Search for gallery images if requested
            gallery_images = []
            if image_count > 1:
                gallery_images = await self.image_service.get_images_for_gallery(
                    topic=topic,
                    count=image_count,
                    keywords=keywords,
                )

            # Generate markdown for featured image
            image_markdown = ""
            if featured_image:
                image_markdown = featured_image.to_markdown()
                logger.info(f"[BlogImageAgent] Found featured image for '{topic}'")
            else:
                logger.warning(f"[BlogImageAgent] No featured image found for '{topic}'")

            return {
                "featured_image": featured_image.to_dict() if featured_image else None,
                "gallery_images": (
                    [img.to_dict() for img in gallery_images] if gallery_images else []
                ),
                "image_markdown": image_markdown,
                "image_count_found": len(gallery_images),
                "status": "success",
            }

        except Exception as e:
            logger.error(f"[BlogImageAgent] Error: {str(e)}", exc_info=True)
            return {
                "status": "failed",
                "error": str(e),
                "featured_image": None,
                "gallery_images": [],
                "image_markdown": "",
            }


def get_blog_image_agent() -> BlogImageAgent:
    """Factory function for BlogImageAgent"""
    return BlogImageAgent()
