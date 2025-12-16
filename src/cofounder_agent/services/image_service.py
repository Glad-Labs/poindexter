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

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

import numpy as np
from PIL import Image

# Try to import diffusers - optional for SDXL generation
try:
    from diffusers import StableDiffusionXLPipeline
    DIFFUSERS_AVAILABLE = True
except ImportError as e:
    DIFFUSERS_AVAILABLE = False
    StableDiffusionXLPipeline = None
    logging.warning(f"Diffusers library not available: {e}")

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
        self.sdxl_refiner_pipe = None
        self.sdxl_available = False
        self.use_refinement = True  # Use refinement for production quality
        self._initialize_sdxl()

        self.search_cache: Dict[str, List[FeaturedImageMetadata]] = {}

    def _initialize_sdxl(self) -> None:
        """Initialize Stable Diffusion XL model with refinement if GPU available"""
        # Check if diffusers is available first
        if not DIFFUSERS_AVAILABLE:
            logger.warning("Diffusers library not installed - SDXL image generation will be unavailable")
            self.sdxl_available = False
            return
        
        try:
            if not torch.cuda.is_available():
                logger.warning("CUDA not available - SDXL image generation will be skipped")
                return

            # 🖥️ Detect GPU capability for optimal precision
            gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)  # GB
            logger.info(f"GPU Memory: {gpu_memory:.1f}GB")

            # RTX 5090 with 32GB VRAM → Use fp32 for best quality
            # RTX 4090 with 24GB VRAM → Use fp32
            # RTX 3090 with 24GB VRAM → Use fp16
            # RTX 3060 with 12GB VRAM → Use fp16 + memory optimization
            if gpu_memory >= 20:
                torch_dtype = torch.float32  # Full precision for high VRAM
                logger.info("✅ Using fp32 (full precision) for best quality")
            else:
                torch_dtype = torch.float16  # Half precision for lower VRAM
                logger.info("✅ Using fp16 (half precision) for memory efficiency")

            # Load base SDXL model
            logger.info("🎨 Loading SDXL base model...")
            self.sdxl_pipe = StableDiffusionXLPipeline.from_pretrained(
                "stabilityai/stable-diffusion-xl-base-1.0",
                torch_dtype=torch_dtype,
                use_safetensors=True,
                variant="fp32" if torch_dtype == torch.float32 else "fp16",
            ).to("cuda")

            # Load refinement model for production quality
            # In newer diffusers versions, use the same pipeline class but with refiner model
            logger.info("🎨 Loading SDXL refinement model...")
            self.sdxl_refiner_pipe = StableDiffusionXLPipeline.from_pretrained(
                "stabilityai/stable-diffusion-xl-refiner-1.0",
                torch_dtype=torch_dtype,
                use_safetensors=True,
                variant="fp32" if torch_dtype == torch.float32 else "fp16",
            ).to("cuda")

            self.sdxl_available = True
            self.use_refinement = True
            logger.info("✅ SDXL base + refinement models loaded successfully")
            logger.info(f"   Using {'fp32 (full precision)' if torch_dtype == torch.float32 else 'fp16 (half precision)'}")
            logger.info(f"   Refinement: {'ENABLED' if self.use_refinement else 'DISABLED'}")

        except Exception as e:
            logger.error(f"Failed to load Stable Diffusion XL models: {e}")
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
        num_inference_steps: int = 50,
        guidance_scale: float = 8.0,
        use_refinement: bool = True,
        high_quality: bool = True,
    ) -> bool:
        """
        Generate image using Stable Diffusion XL with optional refinement (GPU required).

        Args:
            prompt: Image generation prompt
            output_path: Local path to save generated image
            negative_prompt: Negative prompt for quality improvement
            num_inference_steps: Number of inference steps (50+ for high quality)
            guidance_scale: Guidance scale for quality (7.5-8.5 recommended)
            use_refinement: Use refinement model for production quality
            high_quality: Optimize for high quality (more steps, higher guidance)

        Returns:
            True if successful, False otherwise
        """
        if not self.sdxl_available:
            logger.warning("SDXL model not available - image generation skipped")
            return False

        try:
            logger.info(f"🎨 Generating image for prompt: '{prompt}'")
            if high_quality:
                logger.info(f"   Mode: HIGH QUALITY (base steps={num_inference_steps}, guidance={guidance_scale})")
                if use_refinement and self.sdxl_refiner_pipe:
                    logger.info(f"   Refinement: ENABLED")

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
                use_refinement and self.use_refinement,
            )

            logger.info(f"✅ Image saved to {output_path}")
            return True

        except Exception as e:
            logger.error(f"❌ Error generating image: {e}")
            return False

    def _generate_image_sync(
        self,
        prompt: str,
        output_path: str,
        negative_prompt: Optional[str] = None,
        num_inference_steps: int = 50,
        guidance_scale: float = 8.0,
        use_refinement: bool = True,
    ) -> None:
        """
        Synchronous two-stage SDXL generation with optional refinement.

        Stage 1: Base model generates high-quality image with specified steps
        Stage 2: Refiner model applies additional detail refinement (if enabled)

        Runs in thread pool to avoid blocking async operations.
        """
        if not self.sdxl_pipe:
            raise RuntimeError("SDXL model not initialized")

        negative_prompt = negative_prompt or ""

        # =====================================================================
        # STAGE 1: Base Generation
        # =====================================================================
        logger.info(f"   ⏱️  Stage 1/2: Base generation ({num_inference_steps} steps)...")

        base_image = self.sdxl_pipe(
            prompt=prompt,
            negative_prompt=negative_prompt,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            output_type="latent",  # Keep as latent for refinement input
        ).images[0]

        logger.info(f"   ✓ Stage 1 complete: base image latent generated")

        # =====================================================================
        # STAGE 2: Refinement (Optional)
        # =====================================================================
        if use_refinement and self.sdxl_refiner_pipe:
            logger.info(f"   ⏱️  Stage 2/2: Refinement pass (30 additional steps)...")
            try:
                # Refiner pipeline expects image (can be latent or PIL)
                # The pipeline will handle the latent decoding and refinement
                refined_image = self.sdxl_refiner_pipe(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    image=base_image,  # Pass the latent from base model
                    num_inference_steps=30,  # Refinement doesn't need many steps
                    guidance_scale=guidance_scale,
                    output_type="pil",  # Get PIL image from refiner
                ).images[0]

                logger.info(f"   ✓ Stage 2 complete: refinement applied")
                refined_image.save(output_path)

            except Exception as refine_error:
                logger.warning(f"   ⚠️  Refinement failed, falling back to base image: {refine_error}")
                # Fallback: decode base latent manually and save
                try:
                    # Decode the latent tensor to an image
                    # Latents are in range [-1, 1], need to decode through VAE
                    base_image_decoded = self.sdxl_pipe.vae.decode(
                        (base_image / self.sdxl_pipe.vae.config.scaling_factor)
                    ).sample
                    
                    # Convert to PIL Image
                    base_image_pil = (base_image_decoded / 2 + 0.5).clamp(0, 1)
                    base_image_pil = base_image_pil.permute(0, 2, 3, 1).cpu().numpy()[0]
                    base_image_pil = (base_image_pil * 255).astype("uint8")
                    base_image_pil = Image.fromarray(base_image_pil)
                    
                    base_image_pil.save(output_path)
                    logger.info(f"   ✓ Saved base image (latent decoded via VAE)")

                except Exception as decode_error:
                    logger.error(f"   ❌ Fallback conversion also failed: {decode_error}")
                    raise

        else:
            # No refinement: decode latent to image directly
            logger.info(f"   ⏱️  Converting base latent to image...")
            try:
                # Decode the latent tensor using the VAE decoder
                base_image_decoded = self.sdxl_pipe.vae.decode(
                    (base_image / self.sdxl_pipe.vae.config.scaling_factor)
                ).sample
                
                # Convert to PIL Image
                base_image_pil = (base_image_decoded / 2 + 0.5).clamp(0, 1)
                base_image_pil = base_image_pil.permute(0, 2, 3, 1).cpu().numpy()[0]
                base_image_pil = (base_image_pil * 255).astype("uint8")
                base_image_pil = Image.fromarray(base_image_pil)

                base_image_pil.save(output_path)
                logger.info(f"   ✓ Saved base image (no refinement)")

            except Exception as decode_error:
                logger.error(f"   ❌ Latent decoding failed: {decode_error}")
                raise

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
