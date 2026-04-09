"""
Unified Image Service

Consolidates all image processing functionality:
- Featured image sourcing (Pexels API - free, unlimited)
- Image generation (switchable models: SDXL, SDXL Lightning, Flux)
- Image optimization and attribution
- Gallery image sourcing
- Metadata generation

Architecture:
- All operations are async (httpx for Pexels, GPU for generation)
- Model registry pattern — switch models like LLM model_router
- Lazy loading — models only loaded on first generation request
- Proper error handling and fallback chains
- Automatic photographer attribution from Pexels

Supported Models:
- sdxl_base: stabilityai/stable-diffusion-xl-base-1.0 (50 steps, ~6GB VRAM)
- sdxl_lightning: SDXL base + ByteDance Lightning LoRA (4 steps, ~6GB VRAM)
- flux_schnell: black-forest-labs/FLUX.1-schnell (4 steps, ~12GB VRAM)

Cost: $0/month for all options (local GPU or CPU fallback)
"""

import asyncio
import importlib
import logging
from services.logger_config import get_logger
import os
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

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

# Diffusers pipelines — imported lazily per model type
DIFFUSERS_AVAILABLE = False
try:
    from diffusers import StableDiffusionXLPipeline

    DIFFUSERS_AVAILABLE = True
except (ImportError, RuntimeError) as e:
    StableDiffusionXLPipeline = None
    logging.warning("Diffusers library not available: %s", e)

# Optional optimization packages
try:
    import xformers  # noqa: F401

    XFORMERS_AVAILABLE = True
except ImportError:
    XFORMERS_AVAILABLE = False

logger = get_logger(__name__)


# =============================================================================
# IMAGE MODEL REGISTRY
# =============================================================================


class ImageModel(str, Enum):
    """Available image generation models."""

    SDXL_BASE = "sdxl_base"
    SDXL_LIGHTNING = "sdxl_lightning"
    FLUX_SCHNELL = "flux_schnell"


@dataclass(frozen=True)
class ImageModelConfig:
    """Configuration for an image generation model."""

    model_id: str
    display_name: str
    default_steps: int
    default_guidance_scale: float
    pipeline_class: str  # dotted import path within diffusers
    lora_repo: Optional[str] = None
    lora_weight_name: Optional[str] = None
    scheduler_override: Optional[str] = None  # e.g. "EulerDiscreteScheduler"
    scheduler_kwargs: Optional[Dict[str, Any]] = None
    torch_dtype_str: str = "float16"  # "float16" or "bfloat16"
    vram_gb: float = 6.0
    notes: str = ""


IMAGE_MODEL_REGISTRY: Dict[ImageModel, ImageModelConfig] = {
    ImageModel.SDXL_BASE: ImageModelConfig(
        model_id="stabilityai/stable-diffusion-xl-base-1.0",
        display_name="SDXL Base",
        default_steps=30,
        default_guidance_scale=7.5,
        pipeline_class="diffusers.StableDiffusionXLPipeline",
        vram_gb=6.5,
        notes="Original SDXL, high quality at 30-50 steps",
    ),
    ImageModel.SDXL_LIGHTNING: ImageModelConfig(
        model_id="stabilityai/stable-diffusion-xl-base-1.0",
        display_name="SDXL Lightning",
        default_steps=4,
        default_guidance_scale=0.0,
        pipeline_class="diffusers.StableDiffusionXLPipeline",
        lora_repo="ByteDance/SDXL-Lightning",
        lora_weight_name="sdxl_lightning_4step_lora.safetensors",
        scheduler_override="EulerDiscreteScheduler",
        scheduler_kwargs={"timestep_spacing": "trailing"},
        vram_gb=6.5,
        notes="4-step distilled LoRA — 10x faster, great quality",
    ),
    ImageModel.FLUX_SCHNELL: ImageModelConfig(
        model_id="black-forest-labs/FLUX.1-schnell",
        display_name="Flux.1 Schnell",
        default_steps=4,
        default_guidance_scale=0.0,
        pipeline_class="diffusers.FluxPipeline",
        torch_dtype_str="bfloat16",
        vram_gb=12.0,
        notes="Best quality, needs ~12GB VRAM",
    ),
}


def get_default_image_model() -> ImageModel:
    """Get the default image model from config or fallback."""
    from services.site_config import site_config
    model_name = site_config.get("image_model", "sdxl_lightning")
    try:
        return ImageModel(model_name)
    except ValueError:
        logger.warning("Unknown IMAGE_MODEL '%s', falling back to sdxl_lightning", model_name)
        return ImageModel.SDXL_LIGHTNING


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
        from services.site_config import site_config
        self.pexels_api_key = site_config.get("pexels_api_key")
        self._pexels_key_checked_db = bool(self.pexels_api_key)

        if not self.pexels_api_key:
            logger.info(
                "Pexels API key not in config or env — will check app_settings DB on first use"
            )

        self.pexels_available = bool(self.pexels_api_key)
        self.pexels_base_url = "https://api.pexels.com/v1"
        self.pexels_headers = {"Authorization": self.pexels_api_key} if self.pexels_api_key else {}

        # Image generation state (lazy-loaded on first generate_image call)
        self._gen_pipe = None  # Active generation pipeline
        self._active_model: Optional[ImageModel] = None  # Currently loaded model
        self.sdxl_available = False  # Kept for backward compat (True when any model loaded)
        self.sdxl_initialized = False  # Track if we've attempted initialization
        self.use_device = "cpu"  # Updated during model initialization
        # NOTE: Models are lazily initialized only when generate_image() is called.
        # This avoids loading huge models if only Pexels search is needed.

        self.search_cache: Dict[str, List[FeaturedImageMetadata]] = {}

    def _initialize_model(self, model: Optional[ImageModel] = None) -> None:
        """
        Initialize or switch the active image generation model.

        Supports lazy loading, hot-swapping between models, and automatic
        cleanup of previously loaded models to free VRAM.

        Args:
            model: Which model to load. Defaults to get_default_image_model().
        """
        if model is None:
            model = get_default_image_model()

        # Already loaded — nothing to do
        if self._active_model == model and self._gen_pipe is not None:
            logger.debug("Model %s already loaded, skipping init", model.value)
            return

        # Check prerequisites
        if not DIFFUSERS_AVAILABLE:
            logger.warning("Diffusers library not installed - image generation will be unavailable")
            self.sdxl_available = False
            return

        if not TORCH_AVAILABLE:
            logger.warning("PyTorch not installed - image generation will be unavailable")
            self.sdxl_available = False
            return

        # Unload any previously loaded model first
        if self._gen_pipe is not None:
            logger.info(
                "Switching model: %s -> %s",
                self._active_model.value if self._active_model else 'none',
                model.value,
            )
            self._unload_model()

        config = IMAGE_MODEL_REGISTRY[model]

        try:
            # Determine device: CUDA (if compatible) or CPU
            use_device = "cpu"
            torch_dtype = torch.float32

            if torch.cuda.is_available():
                try:
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
                    ]

                    logger.info(
                        "GPU: %s, Capability: sm_%s%s",
                        device_name, capability[0], capability[1],
                    )

                    if current_cap in supported_caps:
                        use_device = "cuda"
                        gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                        logger.info("GPU Memory: %.1fGB - Using CUDA acceleration", gpu_memory)
                    else:
                        logger.warning(
                            "GPU capability sm_%s%s not officially supported. "
                            "Falling back to CPU mode.",
                            capability[0], capability[1],
                        )
                except Exception as e:
                    logger.warning(
                        "Could not verify GPU capability: %s. Using CPU mode.", e, exc_info=True
                    )
            else:
                logger.warning("CUDA not available - using CPU mode (slower)")

            # Determine torch dtype from config
            if use_device == "cpu":
                torch_dtype = torch.float32
                logger.info("CPU mode: using fp32 (full precision)")
            elif config.torch_dtype_str == "bfloat16":
                torch_dtype = torch.bfloat16
                logger.info("Using bfloat16 precision")
            else:
                torch_dtype = torch.float16
                logger.info("Using fp16 (half precision) for memory efficiency")

            # Dynamically import the pipeline class
            pipeline_cls = self._import_pipeline_class(config.pipeline_class)

            # Load model
            logger.info("Loading %s (%s) on %s...", config.display_name, config.model_id, use_device)
            load_kwargs = {
                "torch_dtype": torch_dtype,
                "use_safetensors": True,
            }
            # Only pass variant for fp16 models (not bfloat16 or fp32)
            if torch_dtype == torch.float16:
                load_kwargs["variant"] = "fp16"

            pipe = pipeline_cls.from_pretrained(config.model_id, **load_kwargs).to(use_device)

            # Apply LoRA weights if configured (e.g. SDXL Lightning)
            if config.lora_repo:
                logger.info("Loading LoRA weights from %s...", config.lora_repo)
                pipe.load_lora_weights(config.lora_repo, weight_name=config.lora_weight_name)
                pipe.fuse_lora()
                logger.info("LoRA weights fused successfully")

            # Override scheduler if configured (e.g. EulerDiscreteScheduler for Lightning)
            if config.scheduler_override:
                logger.info("Applying scheduler override: %s", config.scheduler_override)
                from diffusers import EulerDiscreteScheduler

                sched_kwargs = config.scheduler_kwargs or {}
                pipe.scheduler = EulerDiscreteScheduler.from_config(
                    pipe.scheduler.config, **sched_kwargs
                )

            # Apply performance optimizations
            self._apply_model_optimizations(pipe, use_device)

            # Store state
            self._gen_pipe = pipe
            self._active_model = model
            self.use_device = use_device
            self.sdxl_available = True

            logger.info("%s loaded successfully", config.display_name)
            logger.info("   Device: %s", use_device.upper())
            logger.info(
                "   Default steps: %s, guidance: %s",
                config.default_steps, config.default_guidance_scale,
            )
            logger.info(
                "   Optimizations: %s",
                'ENABLED (xformers)' if XFORMERS_AVAILABLE else 'BASIC (no xformers)',
            )

        except Exception as e:
            logger.error("Failed to load %s: %s", config.display_name, e, exc_info=True)
            self.sdxl_available = False

    def _initialize_sdxl(self) -> None:
        """Backward-compatible alias for _initialize_model()."""
        self._initialize_model()

    def _unload_model(self) -> None:
        """Unload the current model and free VRAM/RAM."""
        if self._gen_pipe is not None:
            model_name = self._active_model.value if self._active_model else "unknown"
            logger.info("Unloading model: %s", model_name)
            del self._gen_pipe

        self._gen_pipe = None
        self._active_model = None
        self.sdxl_available = False

        if TORCH_AVAILABLE and torch.cuda.is_available():
            torch.cuda.empty_cache()
            logger.debug("CUDA cache cleared")

    @staticmethod
    def _import_pipeline_class(dotted_path: str):
        """
        Dynamically import a pipeline class from a dotted path.

        Args:
            dotted_path: e.g. "diffusers.StableDiffusionXLPipeline"

        Returns:
            The imported class.
        """
        parts = dotted_path.rsplit(".", 1)
        if len(parts) != 2:
            raise ImportError(f"Invalid pipeline class path: {dotted_path}")
        module_path, class_name = parts
        module = importlib.import_module(module_path)
        return getattr(module, class_name)

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
            logger.info("   Attention slicing enabled")

            # 2. Use xformers memory efficient attention if available
            if XFORMERS_AVAILABLE:
                try:
                    pipe.enable_xformers_memory_efficient_attention()
                    logger.info("   xformers memory-efficient attention enabled (2-4x faster)")
                except Exception as e:
                    logger.warning("   Could not enable xformers: %s", e, exc_info=True)

            # 3. Enable Flash Attention v2 if available (PyTorch 2.0+)
            try:
                if hasattr(pipe.unet, "enable_flash_attn"):
                    pipe.unet.enable_flash_attn(use_flash_attention_v2=True)
                    logger.info("   Flash Attention v2 enabled (30-50% faster)")
            except Exception as e:
                logger.debug("   Flash Attention v2 not available: %s", e)

            # 4. Enable sequential CPU offloading for GPU mode (frees VRAM between steps)
            if device == "cuda":
                try:
                    pipe.enable_sequential_cpu_offload()
                    logger.info("   Sequential CPU offloading enabled (GPU memory saver)")
                except Exception as e:
                    logger.debug("   Sequential CPU offload not available: %s", e)

            # 5. Enable model CPU offload for memory-constrained GPUs
            if device == "cuda":
                try:
                    gpu_mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                    if gpu_mem < 20:
                        pipe.enable_model_cpu_offload()
                        logger.info("   Model CPU offload enabled (constrained GPU memory)")
                except Exception as e:
                    logger.debug("   Model CPU offload not available: %s", e)

        except Exception as e:
            logger.warning("Error applying optimizations: %s", e, exc_info=True)

    # =========================================================================
    # DB-FIRST KEY LOADING
    # =========================================================================

    async def _ensure_pexels_key(self) -> None:
        """Load Pexels API key from app_settings DB if not already set.

        DB-first: the key lives in app_settings (key='pexels_api_key').
        Falls back to PEXELS_API_KEY env var (already checked in __init__).
        Only queries the DB once per service lifetime to avoid repeated lookups.
        """
        # Refresh from site_config in case it loaded after __init__
        if not self.pexels_api_key:
            from services.site_config import site_config
            self.pexels_api_key = site_config.get("pexels_api_key", "")
            if self.pexels_api_key:
                self.pexels_available = True
                self.pexels_headers = {"Authorization": self.pexels_api_key}
                return

        if self.pexels_api_key or self._pexels_key_checked_db:
            return

        self._pexels_key_checked_db = True

        try:
            import asyncpg

            db_url = os.getenv(
                "LOCAL_DATABASE_URL",
                os.getenv("DATABASE_URL", ""),
            )
            if not db_url:
                logger.warning("No DATABASE_URL available — cannot load pexels_api_key from DB")
                return

            conn = await asyncpg.connect(db_url)
            try:
                row = await conn.fetchrow(
                    "SELECT value FROM app_settings WHERE key = $1",
                    "pexels_api_key",
                )
                if row and row["value"]:
                    self.pexels_api_key = row["value"]
                    self.pexels_available = True
                    self.pexels_headers = {"Authorization": self.pexels_api_key}
                    logger.info("Pexels API key loaded from app_settings DB")
                else:
                    logger.warning("pexels_api_key not found in app_settings table")
            finally:
                await conn.close()
        except Exception:
            logger.error("Failed to load pexels_api_key from DB", exc_info=True)

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
        import random

        await self._ensure_pexels_key()

        if not self.pexels_api_key:
            logger.warning("Pexels API key not configured (checked env + DB)")
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
                logger.info("Searching Pexels for: '%s' (page %s)", query, page)
                images = await self._pexels_search(
                    query, per_page=5, orientation=orientation, size=size, page=page
                )
                if images:
                    # RANDOMIZE IMAGE SELECTION: Pick random image from results instead of always first
                    # This prevents all posts from using the same image when topics are similar
                    metadata = random.choice(images)
                    logger.info(
                        "Found featured image for '%s' using query '%s' (page %s) - randomly selected from %s results",
                        topic, query, page, len(images),
                    )
                    return metadata
            except Exception as e:
                logger.warning("Error searching for '%s': %s", query, e, exc_info=True)

        logger.warning("No featured image found for topic: %s", topic)
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
        await self._ensure_pexels_key()

        if not self.pexels_api_key:
            logger.warning("Pexels API key not configured (checked env + DB)")
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
                    logger.info("Found %s gallery images", len(all_images))
                    return all_images[:count]

            except Exception as e:
                logger.warning("Error searching for gallery images '%s': %s", query, e, exc_info=True)

        logger.info("Found %s gallery images (less than requested)", len(all_images))
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
            logger.debug("Pexels API key not configured - skipping search for '%s'", query)
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
                    "Pexels search for '%s' (page %s) returned %s results",
                    query, page, len(photos),
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
            logger.error("Pexels search error: %s", e, exc_info=True)
            return []

    # =========================================================================
    # IMAGE GENERATION (Stable Diffusion XL - Local GPU)
    # =========================================================================

    async def generate_image(
        self,
        prompt: str,
        output_path: str,
        negative_prompt: Optional[str] = None,
        num_inference_steps: Optional[int] = None,
        guidance_scale: Optional[float] = None,
        high_quality: bool = True,
        task_id: Optional[str] = None,
        model: Optional[ImageModel] = None,
    ) -> bool:
        """
        Generate an image using the configured (or specified) model.

        Args:
            prompt: Image generation prompt
            output_path: Local path to save generated image
            negative_prompt: Negative prompt for quality improvement
            num_inference_steps: Override inference steps (defaults to model config)
            guidance_scale: Override guidance scale (defaults to model config)
            high_quality: Optimize for high quality
            task_id: Optional task ID for progress tracking via WebSocket
            model: Which model to use (defaults to IMAGE_MODEL env or sdxl_lightning)

        Returns:
            True if successful, False otherwise
        """
        # Strategy 1: Try host SDXL server (runs on GPU outside Docker)
        from services.site_config import site_config as _sc
        sdxl_server_url = _sc.get("sdxl_server_url", "http://host.docker.internal:9836")
        try:
            import httpx
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{sdxl_server_url}/generate", json={
                    "prompt": prompt,
                    "negative_prompt": negative_prompt or "",
                    "steps": num_inference_steps or 4,
                    "guidance_scale": guidance_scale or 1.0,
                })
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
                    with open(output_path, "wb") as f:
                        f.write(resp.content)
                    elapsed = resp.headers.get("X-Elapsed-Seconds", "?")
                    logger.info("SDXL image generated via host server in %ss: %s", elapsed, output_path)
                    return True
                else:
                    logger.warning("SDXL server returned %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.info("SDXL host server unavailable (%s), trying local diffusers...", e)

        # Strategy 2: Try local diffusers (if available)
        # Lazy initialize on first generation request
        if not self.sdxl_initialized or (model is not None and model != self._active_model):
            target = model or get_default_image_model()
            logger.info("First generation request detected - initializing %s...", target.value)
            self._initialize_model(target)
            self.sdxl_initialized = True

        if not self.sdxl_available:
            logger.warning("Image generation model not available - generation skipped")
            return False

        # Resolve defaults from the active model config
        config = IMAGE_MODEL_REGISTRY[self._active_model]
        if num_inference_steps is None:
            num_inference_steps = config.default_steps
        if guidance_scale is None:
            guidance_scale = config.default_guidance_scale

        try:
            logger.info("Generating image for prompt: '%s'", prompt)
            logger.info(
                "   Model: %s, steps=%s, guidance=%s, device=%s",
                config.display_name, num_inference_steps, guidance_scale, self.use_device.upper(),
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
                task_id,
            )

            logger.info("Image saved to %s", output_path)

            # Mark progress as complete if tracking
            if task_id:
                from services.progress_service import get_progress_service

                progress_service = get_progress_service()
                progress_service.mark_complete(task_id, "Image generation complete")

                # Broadcast via WebSocket
                from services.progress_broadcaster import broadcast_progress

                progress = progress_service.get_progress(task_id)
                await broadcast_progress(task_id, progress)

            return True

        except Exception as e:
            logger.error("Error generating image: %s", e, exc_info=True)

            # Mark progress as failed if tracking
            if task_id:
                from services.progress_service import get_progress_service

                progress_service = get_progress_service()
                progress_service.mark_failed(task_id, str(e))

                # Broadcast via WebSocket
                from services.progress_broadcaster import broadcast_progress

                progress = progress_service.get_progress(task_id)
                if progress is not None:
                    await broadcast_progress(task_id, progress)

            return False

    def _generate_image_sync(
        self,
        prompt: str,
        output_path: str,
        negative_prompt: Optional[str] = None,
        num_inference_steps: int = 30,
        guidance_scale: float = 7.5,
        task_id: Optional[str] = None,
    ) -> None:
        """
        Synchronous image generation using the active model pipeline.

        Runs in a thread pool to avoid blocking async operations.
        Emits progress updates if task_id is provided (for WebSocket streaming).
        """
        if not self._gen_pipe:
            raise RuntimeError("Image generation model not initialized")

        negative_prompt = negative_prompt or ""
        start_time = time.time()

        # Initialize progress tracking if task_id provided
        progress_service = None
        if task_id:
            from services.progress_service import get_progress_service

            progress_service = get_progress_service()
            progress_service.create_progress(task_id, num_inference_steps)

        def progress_callback(step: int, timestep: Any, latents: Any) -> None:
            """Callback for each generation step."""
            if progress_service and task_id:
                elapsed = time.time() - start_time
                progress_service.update_progress(
                    task_id,
                    step + 1,  # 1-indexed for display
                    stage="generation",
                    elapsed_time=elapsed,
                    message=f"Generating: step {step + 1}/{num_inference_steps}",
                )

        model_name = self._active_model.value if self._active_model else "unknown"
        logger.info("   Generating with %s (%s steps)...", model_name, num_inference_steps)

        if progress_service and task_id:
            progress_service.update_progress(
                task_id, 0, stage="generation", message="Starting image generation..."
            )

        result = self._gen_pipe(
            prompt=prompt,
            negative_prompt=negative_prompt,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            output_type="pil",
            callback=progress_callback if task_id else None,
            callback_steps=1 if task_id else None,
        )

        image = result.images[0]
        logger.info("   Generation complete, saving image...")

        try:
            image.save(output_path)
            elapsed = time.time() - start_time
            logger.info("   Image saved to %s (%.1fs)", output_path, elapsed)
        except Exception as save_error:
            logger.error("   Save failed: %s", save_error, exc_info=True)
            raise

    # =========================================================================
    # MODEL INTROSPECTION
    # =========================================================================

    def get_active_model(self) -> Optional[ImageModel]:
        """Return the currently loaded model enum, or None if no model is loaded."""
        return self._active_model

    @staticmethod
    def list_available_models() -> Dict[str, Dict[str, Any]]:
        """Return metadata for all registered image models."""
        return {
            m.value: {
                "display_name": c.display_name,
                "default_steps": c.default_steps,
                "vram_gb": c.vram_gb,
                "notes": c.notes,
            }
            for m, c in IMAGE_MODEL_REGISTRY.items()
        }

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
        logger.warning("[image_service] optimize_image called but not implemented — returning unoptimized")
        logger.info("Image optimization placeholder for %s", image_url)
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
