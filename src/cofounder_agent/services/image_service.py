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
import time
from typing import Optional, Dict, List, Any, Callable
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

# Optional optimization packages
try:
    import xformers

    XFORMERS_AVAILABLE = True
except ImportError:
    XFORMERS_AVAILABLE = False

try:
    from optimum.intel import OVModelForFeatureExtraction

    OPTIMUM_AVAILABLE = True
except ImportError:
    OPTIMUM_AVAILABLE = False

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
            logger.warning(
                "Pexels API key not configured - featured image search will be unavailable"
            )

        self.pexels_base_url = "https://api.pexels.com/v1"
        self.pexels_headers = {"Authorization": self.pexels_api_key} if self.pexels_api_key else {}

        # SDXL Image Generation (optional, GPU-dependent)
        self.sdxl_pipe = None
        self.sdxl_refiner_pipe = None
        self.sdxl_available = False
        self.sdxl_initialized = False  # Track if we've attempted initialization
        self.use_refinement = True  # Use refinement for production quality
        self.use_device = "cpu"  # Will be updated during lazy initialization
        # NOTE: SDXL is lazily initialized only when generate_image() is called
        # This avoids loading huge models if only Pexels search is needed

        self.search_cache: Dict[str, List[FeaturedImageMetadata]] = {}

    def _initialize_sdxl(self) -> None:
        """Initialize Stable Diffusion XL model with optimization and refinement if GPU available"""
        # Check if diffusers is available first
        if not DIFFUSERS_AVAILABLE:
            logger.warning(
                "Diffusers library not installed - SDXL image generation will be unavailable"
            )
            self.sdxl_available = False
            return

        try:
            # Determine device: CUDA (if compatible) or CPU
            use_device = "cpu"
            torch_dtype = torch.float32

            if torch.cuda.is_available():
                try:
                    # Check compute capability compatibility
                    capability = torch.cuda.get_device_capability(0)
                    device_name = torch.cuda.get_device_name(0)
                    current_cap = capability[0] * 10 + capability[1]
                    supported_caps = [
                        50,
                        60,
                        61,
                        70,
                        75,
                        80,
                        86,
                        90,
                        120,
                    ]  # Added sm_120 (RTX 5090 Blackwell)

                    logger.info(
                        f"GPU: {device_name}, Capability: sm_{capability[0]}{capability[1]}"
                    )

                    if current_cap in supported_caps:
                        use_device = "cuda"
                        gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                        logger.info(f"✅ GPU Memory: {gpu_memory:.1f}GB - Using CUDA acceleration")

                        # Precision selection for CUDA
                        if gpu_memory >= 20:
                            torch_dtype = torch.float32
                            logger.info("✅ Using fp32 (full precision) for best quality")
                        else:
                            torch_dtype = torch.float16
                            logger.info("✅ Using fp16 (half precision) for memory efficiency")
                    else:
                        logger.warning(
                            f"⚠️  GPU capability sm_{capability[0]}{capability[1]} not officially supported. "
                            f"Falling back to CPU mode."
                        )
                except Exception as e:
                    logger.warning(f"Could not verify GPU capability: {e}. Using CPU mode.")
            else:
                logger.warning("CUDA not available - using CPU mode (slower)")

            # For CPU inference, always use fp32
            if use_device == "cpu":
                torch_dtype = torch.float32
                logger.info("ℹ️  CPU mode: using fp32 (full precision)")

            # Load base SDXL model with optimizations
            logger.info(f"🎨 Loading SDXL base model (device: {use_device})...")
            self.sdxl_pipe = StableDiffusionXLPipeline.from_pretrained(
                "stabilityai/stable-diffusion-xl-base-1.0",
                torch_dtype=torch_dtype,
                use_safetensors=True,
                variant="fp16" if torch_dtype == torch.float16 else None,
            ).to(use_device)

            # Apply CPU/GPU optimizations
            self._apply_model_optimizations(self.sdxl_pipe, use_device)

            # Load refinement model for production quality
            logger.info(f"🎨 Loading SDXL refinement model (device: {use_device})...")
            self.sdxl_refiner_pipe = StableDiffusionXLPipeline.from_pretrained(
                "stabilityai/stable-diffusion-xl-refiner-1.0",
                torch_dtype=torch_dtype,
                use_safetensors=True,
                variant="fp16" if torch_dtype == torch.float16 else None,
            ).to(use_device)

            # Apply optimizations to refiner too
            self._apply_model_optimizations(self.sdxl_refiner_pipe, use_device)

            self.use_device = use_device  # Store device for later use
            self.sdxl_available = True
            self.use_refinement = True
            logger.info("✅ SDXL base + refinement models loaded successfully")
            logger.info(f"   Device: {use_device.upper()}")
            logger.info(
                f"   Precision: {'fp32 (full precision)' if torch_dtype == torch.float32 else 'fp16 (half precision)'}"
            )
            logger.info(f"   Refinement: {'ENABLED' if self.use_refinement else 'DISABLED'}")
            logger.info(
                f"   Optimizations: {'ENABLED (xformers, flash attention)' if XFORMERS_AVAILABLE else 'BASIC (no xformers)'}"
            )

        except Exception as e:
            logger.error(f"Failed to load Stable Diffusion XL models: {e}")
            self.sdxl_available = False

    def _apply_model_optimizations(self, pipe, device: str) -> None:
        """
        Apply performance optimizations to SDXL pipeline.

        Optimizations:
        - Memory-efficient attention (xformers if available)
        - Flash Attention v2
        - Model CPU offloading for 16GB GPU
        - Reduced precision where safe

        These work on both CPU and GPU and will benefit future GPU usage.
        """
        try:
            # 1. Enable attention slicing for memory efficiency
            pipe.enable_attention_slicing()
            logger.info("   ✓ Attention slicing enabled")

            # 2. Use xformers memory efficient attention if available
            if XFORMERS_AVAILABLE:
                try:
                    pipe.enable_xformers_memory_efficient_attention()
                    logger.info("   ✓ xformers memory-efficient attention enabled (2-4x faster)")
                except Exception as e:
                    logger.warning(f"   ⚠️  Could not enable xformers: {e}")

            # 3. Enable Flash Attention v2 if available (PyTorch 2.0+)
            try:
                if hasattr(pipe.unet, "enable_flash_attn"):
                    pipe.unet.enable_flash_attn(use_flash_attention_v2=True)
                    logger.info("   ✓ Flash Attention v2 enabled (30-50% faster)")
            except Exception as e:
                logger.debug(f"   Flash Attention v2 not available: {e}")

            # 4. Enable sequential CPU offloading for GPU mode (frees VRAM between steps)
            if device == "cuda":
                try:
                    pipe.enable_sequential_cpu_offload()
                    logger.info("   ✓ Sequential CPU offloading enabled (GPU memory saver)")
                except Exception as e:
                    logger.debug(f"   Sequential CPU offload not available: {e}")

            # 5. Enable model CPU offload for memory-constrained GPUs
            if device == "cuda":
                try:
                    gpu_mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                    if gpu_mem < 20:
                        pipe.enable_model_cpu_offload()
                        logger.info("   ✓ Model CPU offload enabled (constrained GPU memory)")
                except Exception as e:
                    logger.debug(f"   Model CPU offload not available: {e}")

        except Exception as e:
            logger.warning(f"Error applying optimizations: {e}")

    # =========================================================================
    # FEATURED IMAGE SEARCH (Pexels - Free, Unlimited)
    # =========================================================================

    async def search_featured_image(
        self,
        topic: str,
        keywords: Optional[List[str]] = None,
        orientation: str = "landscape",
        size: str = "medium",
        page: int = 1,
    ) -> Optional[FeaturedImageMetadata]:
        """
        Search for featured image using Pexels API.

        Args:
            topic: Main search topic
            keywords: Additional keywords to try if topic search fails
            orientation: Image orientation (landscape, portrait, square)
            size: Image size (small, medium, large)
            page: Results page number for pagination (default 1, use higher for different results)

        Returns:
            FeaturedImageMetadata or None if no image found
        """
        if not self.pexels_api_key:
            logger.warning("Pexels API key not configured")
            return None

        # Build search queries prioritizing concept/topic over people
        search_queries = [topic]

        # Add concept-based fallbacks (no people)
        concept_keywords = [
            "technology",
            "digital",
            "abstract",
            "modern",
            "innovation",
            "data",
            "network",
            "background",
            "desktop",
            "workspace",
            "object",
            "product",
            "design",
            "pattern",
            "texture",
            "nature",
            "landscape",
            "environment",
            "system",
            "interface",
        ]

        # Add user keywords but avoid person/people related terms
        if keywords:
            for kw in keywords[:3]:
                # Avoid portrait/people searches
                if not any(
                    term in kw.lower() for term in ["person", "people", "portrait", "face", "human"]
                ):
                    search_queries.append(kw)

        # Add combined searches (topic + concept)
        search_queries.append(f"{topic} technology")
        search_queries.append(f"{topic} abstract")
        search_queries.extend(concept_keywords[:2])

        for query in search_queries:
            try:
                logger.info(f"Searching Pexels for: '{query}' (page {page})")
                images = await self._pexels_search(
                    query, per_page=3, orientation=orientation, size=size, page=page
                )
                if images:
                    metadata = images[0]
                    logger.info(
                        f"✅ Found featured image for '{topic}' using query '{query}' (page {page})"
                    )
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
        page: int = 1,
    ) -> List[FeaturedImageMetadata]:
        """
        Internal method to search Pexels API (async-only).

        Args:
            query: Search keywords
            per_page: Results per page
            orientation: Image orientation
            size: Image size
            page: Results page number (for pagination)

        Returns:
            List of FeaturedImageMetadata objects
        """
        # Skip search if API key is not configured
        if not self.pexels_api_key:
            logger.debug(f"Pexels API key not configured - skipping search for '{query}'")
            return []
        
        try:
            params = {
                "query": query,
                "per_page": min(per_page, 80),
                "orientation": orientation,
                "size": size,
                "page": page,
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
                logger.info(
                    f"Pexels search for '{query}' (page {page}) returned {len(photos)} results"
                )

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
        task_id: Optional[str] = None,
    ) -> bool:
        """
        Generate image using Stable Diffusion XL with full refinement for maximum quality.

        Quality is prioritized over speed. Refinement is ALWAYS enabled to ensure
        the highest quality output.

        Args:
            prompt: Image generation prompt
            output_path: Local path to save generated image
            negative_prompt: Negative prompt for quality improvement
            num_inference_steps: Number of inference steps (50+ for high quality)
            guidance_scale: Guidance scale for quality (7.5-8.5 recommended)
            use_refinement: Use refinement model (ALWAYS enabled for quality)
            high_quality: Optimize for high quality (more steps, higher guidance)
            task_id: Optional task ID for progress tracking via WebSocket

        Returns:
            True if successful, False otherwise

        Note:
            - Refinement adds 30+ additional steps (2-stage pipeline)
            - CPU mode will be slower but maintains maximum quality
            - GPU mode (when available in PyTorch 2.9.2+) will be 20-40x faster
            - If task_id provided, progress updates are sent via progress service
        """
        # Lazy initialize SDXL only when actually needed for generation
        if not self.sdxl_initialized:
            logger.info("🎨 First generation request detected - initializing SDXL models...")
            self._initialize_sdxl()
            self.sdxl_initialized = True

        if not self.sdxl_available:
            logger.warning("SDXL model not available - image generation skipped")
            return False

        try:
            logger.info(f"🎨 Generating image for prompt: '{prompt}'")
            if high_quality:
                logger.info(
                    f"   Mode: HIGH QUALITY (base steps={num_inference_steps}, guidance={guidance_scale})"
                )
                if use_refinement and self.sdxl_refiner_pipe:
                    logger.info(f"   Refinement: ENABLED (quality priority)")
                    logger.info(
                        f"   Device: {self.use_device.upper()} - Note: CPU refinement will take longer"
                    )

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
                use_refinement
                and self.use_refinement,  # Enable refinement on all devices for quality
                task_id,
            )

            logger.info(f"✅ Image saved to {output_path}")

            # Mark progress as complete if tracking
            if task_id:
                from services.progress_service import get_progress_service

                progress_service = get_progress_service()
                progress_service.mark_complete(task_id, "Image generation complete")

                # Broadcast via WebSocket
                from routes.websocket_routes import broadcast_progress

                progress = progress_service.get_progress(task_id)
                await broadcast_progress(task_id, progress)

            return True

        except Exception as e:
            logger.error(f"❌ Error generating image: {e}")

            # Mark progress as failed if tracking
            if task_id:
                from services.progress_service import get_progress_service

                progress_service = get_progress_service()
                progress_service.mark_failed(task_id, str(e))

                # Broadcast via WebSocket
                from routes.websocket_routes import broadcast_progress

                progress = progress_service.get_progress(task_id)
                await broadcast_progress(task_id, progress)

            return False

    def _generate_image_sync(
        self,
        prompt: str,
        output_path: str,
        negative_prompt: Optional[str] = None,
        num_inference_steps: int = 50,
        guidance_scale: float = 8.0,
        use_refinement: bool = True,
        task_id: Optional[str] = None,
    ) -> None:
        """
        Synchronous two-stage SDXL generation with optional refinement.

        Stage 1: Base model generates high-quality image with specified steps
        Stage 2: Refiner model applies additional detail refinement (if enabled)

        Runs in thread pool to avoid blocking async operations.

        Emits progress updates if task_id provided (for WebSocket streaming).
        """
        if not self.sdxl_pipe:
            raise RuntimeError("SDXL model not initialized")

        negative_prompt = negative_prompt or ""
        start_time = time.time()

        # Initialize progress tracking if task_id provided
        progress_service = None
        if task_id:
            from services.progress_service import get_progress_service

            progress_service = get_progress_service()
            total_steps = num_inference_steps + (
                30 if use_refinement and self.sdxl_refiner_pipe else 0
            )
            progress_service.create_progress(task_id, total_steps)

        def progress_callback(step: int, timestep: Any, latents: Any) -> None:
            """Callback for each generation step"""
            if progress_service and task_id:
                elapsed = time.time() - start_time
                progress_service.update_progress(
                    task_id,
                    step + 1,  # 1-indexed for display
                    stage="base_model",
                    elapsed_time=elapsed,
                    message=f"Base model generation: step {step + 1}/{num_inference_steps}",
                )

        # =====================================================================
        # STAGE 1: Base Generation
        # =====================================================================
        logger.info(f"   ⏱️  Stage 1/2: Base generation ({num_inference_steps} steps)...")

        if progress_service and task_id:
            progress_service.update_progress(
                task_id, 0, stage="base_model", message=f"Starting base model generation..."
            )

        # Generate base image - always output PIL for safety
        # We'll pass PIL to refiner which handles it correctly
        logger.info(f"   ⏱️  Stage 1/2: Base generation ({num_inference_steps} steps)...")

        if progress_service and task_id:
            progress_service.update_progress(
                task_id, 0, stage="base_model", message=f"Starting base model generation..."
            )

        base_result = self.sdxl_pipe(
            prompt=prompt,
            negative_prompt=negative_prompt,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            output_type="pil",  # Always use PIL for compatibility
            callback=progress_callback if task_id else None,
            callback_steps=1 if task_id else None,
        )
        base_image_pil = base_result.images[0]
        logger.info(f"   ✓ Stage 1 complete: Base image generated")

        # =====================================================================
        # STAGE 2: Refinement (Quality Priority - Per HuggingFace Recommendation)
        # =====================================================================
        if use_refinement and self.sdxl_refiner_pipe:
            logger.info(f"   ⏱️  Stage 2/2: Refinement pass (30 additional steps)...")

            if progress_service and task_id:
                progress_service.update_progress(
                    task_id,
                    num_inference_steps,
                    stage="refiner_model",
                    message=f"Starting refinement pass...",
                )

            def refiner_progress_callback(step: int, timestep: Any, latents: Any) -> None:
                """Callback for refinement steps"""
                if progress_service and task_id:
                    elapsed = time.time() - start_time
                    current_step = num_inference_steps + step + 1
                    progress_service.update_progress(
                        task_id,
                        current_step,
                        stage="refiner_model",
                        elapsed_time=elapsed,
                        message=f"Refinement: step {step + 1}/30",
                    )

            try:
                # Pass PIL image to refiner
                # The refiner will internally convert to latents if needed
                refined_image = self.sdxl_refiner_pipe(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    image=base_image_pil,  # Pass PIL image directly
                    num_inference_steps=30,
                    guidance_scale=guidance_scale,
                    output_type="pil",
                    callback=refiner_progress_callback if task_id else None,
                    callback_steps=1 if task_id else None,
                ).images[0]

                logger.info(f"   ✓ Stage 2 complete: Refinement applied successfully")
                refined_image.save(output_path)

            except Exception as refine_error:
                logger.warning(
                    f"   ⚠️  Refinement failed, falling back to base image: {refine_error}"
                )
                # Fallback: save base PIL image without refinement
                try:
                    base_image_pil.save(output_path)
                    logger.info(f"   ✓ Saved base image without refinement (fallback)")

                except Exception as save_error:
                    logger.error(f"   ❌ Save failed: {save_error}")
                    raise

        else:
            # Refinement disabled: save base image directly
            logger.info(f"   ⏱️  Saving base image...")
            try:
                base_image_pil.save(output_path)
                logger.info(f"   ✓ Saved base image (refinement disabled)")

            except Exception as save_error:
                logger.error(f"   ❌ Save failed: {save_error}")
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
