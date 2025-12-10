"""
Unified Image Service

Consolidates all image processing functionality:
- Featured image sourcing (Pexels API - free, unlimited)
- Image generation (Stable Diffusion XL - local GPU, fallback)
- Image optimization and attribution
- Gallery image sourcing
- Metadata generation

Architecture:
- All operations are async (httpx for Pexels, GPU for SDXL)
- Proper error handling and fallback chains
- PostgreSQL persistence for image metadata
- Automatic photographer attribution from Pexels
- Graceful degradation when images unavailable

Cost Optimization:
- Pexels: FREE (unlimited searches, no credits)
- SDXL Local: FREE (if GPU available, else skipped)
- Result: $0/month vs $0.02/image with DALL-E
"""

import os
import logging
import asyncio
from typing import Optional, Dict, List, Any
from datetime import datetime

import httpx
import torch
from diffusers import StableDiffusionXLPipeline

logger = logging.getLogger(__name__)


class FeaturedImageMetadata:
    """Metadata for a featured image"""
    
    def __init__(
        self,
        url: str,
        thumbnail: Optional[str] = None,
        photographer: str = "Unknown",
        photographer_url: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        alt_text: str = "",
        caption: str = "",
        source: str = "pexels",
        search_query: str = "",
    ):
        self.url = url
        self.thumbnail = thumbnail or url
        self.photographer = photographer
        self.photographer_url = photographer_url
        self.width = width
        self.height = height
        self.alt_text = alt_text
        self.caption = caption
        self.source = source
        self.search_query = search_query
        self.retrieved_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage"""
        return {
            "url": self.url,
            "thumbnail": self.thumbnail,
            "photographer": self.photographer,
            "photographer_url": self.photographer_url,
            "width": self.width,
            "height": self.height,
            "alt_text": self.alt_text,
            "caption": self.caption,
            "source": self.source,
            "search_query": self.search_query,
            "retrieved_at": self.retrieved_at.isoformat(),
        }

    def to_markdown(self, caption_override: Optional[str] = None) -> str:
        """Generate markdown with photographer attribution"""
        caption = caption_override or self.caption or self.alt_text or "Featured Image"
        
        photographer_link = self.photographer
        if self.photographer_url:
            photographer_link = f"[{self.photographer}]({self.photographer_url})"
        
        return f"""![{caption}]({self.url})
*Photo by {photographer_link} on {self.source.capitalize()}*"""


class ImageService:
    """
    Unified service for all image operations.
    
    Consolidates:
    - PexelsClient functionality (featured image, gallery)
    - ImageGenClient functionality (SDXL generation)
    - ImageAgent functionality (orchestration, metadata)
    
    All operations are async-first to prevent blocking in FastAPI event loop.
    """

    def __init__(self):
        """Initialize image service"""
        self.pexels_api_key = os.getenv("PEXELS_API_KEY")
        if not self.pexels_api_key:
            logger.warning("Pexels API key not configured - featured image search will be unavailable")

        self.pexels_base_url = "https://api.pexels.com/v1"
        self.pexels_headers = {"Authorization": self.pexels_api_key} if self.pexels_api_key else {}

        # SDXL Image Generation (optional, GPU-dependent)
        self.sdxl_pipe = None
        self.sdxl_available = False
        self._initialize_sdxl()

        self.search_cache: Dict[str, List[FeaturedImageMetadata]] = {}

    def _initialize_sdxl(self) -> None:
        """Initialize Stable Diffusion XL model if GPU available"""
        try:
            if torch.cuda.is_available():
                logger.info("Initializing Stable Diffusion XL model...")
                self.sdxl_pipe = StableDiffusionXLPipeline.from_pretrained(
                    "stabilityai/stable-diffusion-xl-base-1.0",
                    torch_dtype=torch.float16,
                    use_safetensors=True,
                    variant="fp16",
                ).to("cuda")
                self.sdxl_available = True
                logger.info("Stable Diffusion XL model loaded successfully")
            else:
                logger.warning("CUDA not available - SDXL image generation will be skipped")
        except Exception as e:
            logger.error(f"Failed to load Stable Diffusion XL model: {e}")
            self.sdxl_available = False

    # =========================================================================
    # FEATURED IMAGE SEARCH (Pexels - Free, Unlimited)
    # =========================================================================

    async def search_featured_image(
        self,
        topic: str,
        keywords: Optional[List[str]] = None,
        orientation: str = "landscape",
        size: str = "medium",
    ) -> Optional[FeaturedImageMetadata]:
        """
        Search for featured image using Pexels API.

        Args:
            topic: Main search topic
            keywords: Additional keywords to try if topic search fails
            orientation: Image orientation (landscape, portrait, square)
            size: Image size (small, medium, large)

        Returns:
            FeaturedImageMetadata or None if no image found
        """
        if not self.pexels_api_key:
            logger.warning("Pexels API key not configured")
            return None

        search_queries = [topic]
        if keywords:
            search_queries.extend(keywords[:3])

        for query in search_queries:
            try:
                images = await self._pexels_search(query, per_page=1, orientation=orientation, size=size)
                if images:
                    metadata = images[0]
                    logger.info(f"Found featured image for '{topic}' using query '{query}'")
                    return metadata
            except Exception as e:
                logger.warning(f"Error searching for '{query}': {e}")

        logger.warning(f"No featured image found for topic: {topic}")
        return None

    async def get_images_for_gallery(
        self,
        topic: str,
        count: int = 5,
        keywords: Optional[List[str]] = None,
    ) -> List[FeaturedImageMetadata]:
        """
        Get multiple images for content gallery.

        Args:
            topic: Gallery search topic
            count: Number of images needed
            keywords: Additional keywords

        Returns:
            List of FeaturedImageMetadata objects
        """
        if not self.pexels_api_key:
            logger.warning("Pexels API key not configured")
            return []

        search_queries = [topic]
        if keywords:
            search_queries.extend(keywords)

        all_images = []

        for query in search_queries[:3]:  # Try up to 3 queries
            try:
                images = await self._pexels_search(query, per_page=count)
                all_images.extend(images)

                if len(all_images) >= count:
                    logger.info(f"Found {len(all_images)} gallery images")
                    return all_images[:count]

            except Exception as e:
                logger.warning(f"Error searching for gallery images '{query}': {e}")

        logger.info(f"Found {len(all_images)} gallery images (less than requested)")
        return all_images

    async def _pexels_search(
        self,
        query: str,
        per_page: int = 5,
        orientation: str = "landscape",
        size: str = "medium",
    ) -> List[FeaturedImageMetadata]:
        """
        Internal method to search Pexels API (async-only).

        Args:
            query: Search keywords
            per_page: Results per page
            orientation: Image orientation
            size: Image size

        Returns:
            List of FeaturedImageMetadata objects
        """
        try:
            params = {
                "query": query,
                "per_page": min(per_page, 80),
                "orientation": orientation,
                "size": size,
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.pexels_base_url}/search",
                    headers=self.pexels_headers,
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

                photos = data.get("photos", [])
                logger.info(f"Pexels search for '{query}' returned {len(photos)} results")

                return [
                    FeaturedImageMetadata(
                        url=photo["src"]["large"],
                        thumbnail=photo["src"]["small"],
                        photographer=photo.get("photographer", "Unknown"),
                        photographer_url=photo.get("photographer_url", ""),
                        width=photo.get("width"),
                        height=photo.get("height"),
                        alt_text=photo.get("alt", ""),
                        search_query=query,
                        source="pexels",
                    )
                    for photo in photos
                ]

        except Exception as e:
            logger.error(f"Pexels search error: {e}")
            return []

    # =========================================================================
    # IMAGE GENERATION (Stable Diffusion XL - Local GPU)
    # =========================================================================

    async def generate_image(
        self,
        prompt: str,
        output_path: str,
        negative_prompt: Optional[str] = None,
        num_inference_steps: int = 30,
        guidance_scale: float = 7.5,
    ) -> bool:
        """
        Generate image using Stable Diffusion XL (GPU required).

        Args:
            prompt: Image generation prompt
            output_path: Local path to save generated image
            negative_prompt: Negative prompt for quality improvement
            num_inference_steps: Number of inference steps
            guidance_scale: Guidance scale for quality

        Returns:
            True if successful, False otherwise
        """
        if not self.sdxl_available:
            logger.warning("SDXL model not available - image generation skipped")
            return False

        try:
            logger.info(f"Generating image for prompt: '{prompt}'")

            # Run generation in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._generate_image_sync,
                prompt,
                output_path,
                negative_prompt,
                num_inference_steps,
                guidance_scale,
            )

            logger.info(f"Image saved to {output_path}")
            return True

        except Exception as e:
            logger.error(f"Error generating image: {e}")
            return False

    def _generate_image_sync(
        self,
        prompt: str,
        output_path: str,
        negative_prompt: Optional[str] = None,
        num_inference_steps: int = 30,
        guidance_scale: float = 7.5,
    ) -> None:
        """Synchronous SDXL generation (runs in thread pool)"""
        if not self.sdxl_pipe:
            raise RuntimeError("SDXL model not initialized")

        negative_prompt = negative_prompt or ""

        image = self.sdxl_pipe(
            prompt=prompt,
            negative_prompt=negative_prompt,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
        ).images[0]

        image.save(output_path)

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def generate_image_markdown(
        self,
        image: FeaturedImageMetadata,
        caption: Optional[str] = None,
    ) -> str:
        """Generate markdown for image with attribution"""
        return image.to_markdown(caption)

    async def optimize_image_for_web(
        self,
        image_url: str,
        max_width: int = 1200,
        max_height: int = 630,
    ) -> Optional[Dict[str, Any]]:
        """
        Optimize image for web delivery.

        Args:
            image_url: URL of image to optimize
            max_width: Maximum width
            max_height: Maximum height

        Returns:
            Optimization result dict or None
        """
        # Placeholder for future image optimization
        # Could integrate with imgix, Cloudinary, or local optimization
        logger.info(f"Image optimization placeholder for {image_url}")
        return {
            "url": image_url,
            "optimized": False,
            "note": "Image optimization not yet implemented",
        }

    def get_search_cache(self, query: str) -> Optional[List[FeaturedImageMetadata]]:
        """Get cached search results"""
        return self.search_cache.get(query)

    def set_search_cache(self, query: str, results: List[FeaturedImageMetadata]) -> None:
        """Cache search results (24-hour TTL in production)"""
        self.search_cache[query] = results


def get_image_service() -> ImageService:
    """Factory function for dependency injection"""
    return ImageService()
