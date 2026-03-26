"""
📸 Media Routes - Image Generation & Search

Provides API endpoints for:
- Featured image generation/search using Pexels or SDXL
- Image health check

Cost:
- Pexels: FREE (unlimited)
- SDXL: FREE if GPU available (else gracefully skipped)
- Much cheaper than DALL-E ($0.02/image)
"""

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from utils.rate_limiter import limiter
from pydantic import BaseModel, Field

from routes.auth_unified import get_current_user

# Cloud storage imports
try:
    import cloudinary
    import cloudinary.uploader

    CLOUDINARY_AVAILABLE = True
except ImportError:
    CLOUDINARY_AVAILABLE = False

from services.image_service import FeaturedImageMetadata, ImageModel, ImageService

logger = logging.getLogger(__name__)
media_router = APIRouter(prefix="/api/media", tags=["Media"])


# ═══════════════════════════════════════════════════════════════════════════
# CLOUDINARY SETUP (Primary - Free Tier)
# ═══════════════════════════════════════════════════════════════════════════

if CLOUDINARY_AVAILABLE and os.getenv("CLOUDINARY_CLOUD_NAME"):
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    )
    logger.info("✅ Cloudinary configured for image storage")
else:
    logger.info("ℹ️ Cloudinary not configured (use for local dev or production)")


async def upload_to_cloudinary(file_path: str, task_id: Optional[str] = None) -> Optional[str]:
    """
    Upload generated image to Cloudinary and return public URL.

    Args:
        file_path: Local path to image file
        task_id: Task ID for metadata (optional)

    Returns:
        Public URL if successful, None if Cloudinary not configured or upload fails
    """
    if not CLOUDINARY_AVAILABLE or not os.getenv("CLOUDINARY_CLOUD_NAME"):
        return None

    try:
        result = cloudinary.uploader.upload(
            file_path,
            folder="generated",  # Organize in folder
            resource_type="image",
            invalidate=True,  # Invalidate CDN cache
            tags=["blog-generated"] + ([task_id] if task_id else []),
            context={"task_id": task_id or "unknown", "generated_date": datetime.now().isoformat()},
        )

        public_url = result["secure_url"]  # HTTPS URL
        logger.info(f"✅ Uploaded to Cloudinary: {public_url}")
        return public_url

    except Exception as e:
        logger.error(f"❌ Cloudinary upload failed: {e}", exc_info=True)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# REQUEST/RESPONSE SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════


class ImageGenerationRequest(BaseModel):
    """Request to generate or search for featured image"""

    prompt: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Primary search/generation prompt (e.g., primary keyword 'AI' for better Pexels results than full title)",
    )
    title: Optional[str] = Field(
        None,
        max_length=200,
        description="Content title (full title for metadata, used as fallback search term)",
    )
    keywords: Optional[List[str]] = Field(
        default=None,
        description="Additional keywords for search refinement (first keyword is primary topic)",
    )
    use_pexels: bool = Field(
        True, description="Search Pexels for free stock images first (recommended)"
    )
    use_generation: bool = Field(
        False, description="Generate custom image with SDXL if Pexels fails (requires GPU)"
    )
    image_model: Optional[str] = Field(
        None,
        description="Image generation model to use (sdxl_base, sdxl_lightning, flux_schnell). Defaults to IMAGE_MODEL env var.",
    )
    high_quality: bool = Field(
        True,
        description="Optimize for high quality output",
    )
    num_inference_steps: Optional[int] = Field(
        None, ge=1, le=100, description="Override inference steps (defaults to model's recommended steps)"
    )
    guidance_scale: Optional[float] = Field(
        None, ge=0.0, le=20.0, description="Override guidance scale (defaults to model's recommended value)"
    )
    task_id: Optional[str] = Field(
        None, description="Optional task ID for WebSocket progress tracking"
    )
    page: int = Field(
        1,
        ge=1,
        le=100,
        description="Pexels search results page (for fetching different images on retry)",
    )


class ImageMetadata(BaseModel):
    """Image metadata response"""

    url: str = Field(..., description="Image URL")
    source: str = Field(..., description="Image source (pexels, sdxl, etc)")
    photographer: Optional[str] = Field(None, description="Photographer name (Pexels)")
    photographer_url: Optional[str] = Field(None, description="Photographer profile URL")
    width: Optional[int] = Field(None, description="Image width in pixels")
    height: Optional[int] = Field(None, description="Image height in pixels")


class ImageGenerationResponse(BaseModel):
    """Response from image generation/search"""

    success: bool = Field(..., description="Whether operation was successful")
    image_url: str = Field(..., description="Direct image URL for use")
    image: Optional[ImageMetadata] = Field(None, description="Image metadata")
    message: Optional[str] = Field(None, description="Status message or error")
    generation_time: Optional[float] = Field(None, description="Time taken in seconds")
    local_path: Optional[str] = Field(
        None, description="Local file path (for generated images in Downloads)"
    )
    preview_mode: Optional[bool] = Field(
        False, description="Whether this is a preview (not yet in CDN)"
    )


class HealthResponse(BaseModel):
    """Health check response"""

    status: str = Field(..., description="overall status")
    pexels_available: bool = Field(..., description="Pexels API configured")
    sdxl_available: bool = Field(..., description="SDXL GPU available")
    active_model: Optional[str] = Field(None, description="Currently loaded model name")
    message: str = Field(..., description="Detailed status message")


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON IMAGE SERVICE
# ═══════════════════════════════════════════════════════════════════════════

_image_service: Optional[ImageService] = None


async def get_image_service() -> ImageService:
    """Get or create ImageService singleton"""
    global _image_service
    if _image_service is None:
        _image_service = ImageService()
        logger.info("✅ ImageService initialized")
    return _image_service


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════


def build_enhanced_search_prompt(
    base_prompt: str,
    keywords: Optional[List[str]] = None,
) -> str:
    """
    Build an enhanced search prompt by combining title with SEO keywords.

    This creates more specific, targeted search queries that are more likely
    to find relevant images.

    Args:
        base_prompt: Main prompt (usually the title)
        keywords: Optional SEO keywords to enhance the prompt

    Returns:
        Enhanced prompt string optimized for image search

    Examples:
        >>> build_enhanced_search_prompt("Best Eats in Northeast USA", ["seafood", "boston", "food"])
        "Best Eats in Northeast USA seafood"

        >>> build_enhanced_search_prompt("AI Gaming NPCs")
        "AI Gaming NPCs"
    """
    if not keywords or len(keywords) == 0:
        return base_prompt

    # Take top keyword for specificity
    primary_keyword = keywords[0] if keywords else None

    if not primary_keyword:
        return base_prompt

    # Combine title with primary keyword for more specific search
    enhanced = f"{base_prompt} {primary_keyword}"

    logger.debug(
        f"📝 Enhanced prompt: '{base_prompt}' → '{enhanced}' (using keyword: {primary_keyword})"
    )

    return enhanced


# ═══════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════


@media_router.post(
    "/generate-image",
    response_model=ImageGenerationResponse,
    summary="Generate or search for featured image",
    description="Search Pexels for free stock images, with optional SDXL fallback",
)
@limiter.limit("10/minute")
async def generate_featured_image(
    request: Request,
    image_request: ImageGenerationRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Generate or search for a featured image.
    
    **Strategy:**
    1. Try Pexels API first (free, unlimited, high quality)
    2. Fall back to SDXL generation if needed (GPU required)
    3. Return image URL for use in post
    
    **Cost:**
    - Pexels: FREE (unlimited)
    - SDXL: FREE if GPU available (gracefully skipped otherwise)
    - vs DALL-E: $0.02/image
    
    **Examples:**
    
    ```bash
    # Search for image (recommended)
    curl -X POST http://localhost:8000/api/media/generate-image \\
      -H "Content-Type: application/json" \\
      -d '{
        "prompt": "AI gaming NPCs futuristic virtual reality",
        "title": "How AI-Powered NPCs are Making Games More Immersive",
        "use_pexels": true,
        "use_generation": false
      }'
    
    # Generate custom image (requires GPU)
    curl -X POST http://localhost:8000/api/media/generate-image \\
      -H "Content-Type: application/json" \\
      -d '{
        "prompt": "futuristic AI NPCs in gaming",
        "use_pexels": false,
        "use_generation": true
      }'
    ```
    """
    start_time = time.time()

    try:
        image_service = await get_image_service()
    except Exception as e:
        logger.error(f"❌ Failed to initialize image service: {e}", exc_info=True)
        elapsed = time.time() - start_time
        return ImageGenerationResponse(
            success=False,
            image_url="",
            image=None,
            message="Image service initialization failed",
            generation_time=elapsed,
        )

    try:
        image = None

        # Log the request configuration
        logger.info(
            f"📸 Image generation request: use_pexels={image_request.use_pexels}, use_generation={image_request.use_generation}"
        )

        # Step 1: Try Pexels search first (recommended)
        if image_request.use_pexels:
            keywords = image_request.keywords or []

            # Build enhanced search prompt using keywords if available
            search_prompt = build_enhanced_search_prompt(image_request.prompt, keywords)

            logger.info(f"🔍 STEP 1: Searching Pexels for: {search_prompt}")
            if keywords:
                logger.debug(f"   Keywords: {', '.join(keywords)}")

            try:
                image = await image_service.search_featured_image(
                    topic=search_prompt, keywords=keywords, page=image_request.page
                )

                if image:
                    logger.info(f"✅ STEP 1 SUCCESS: Found image via Pexels: {image.url}")
                else:
                    logger.warning(f"⚠️ STEP 1 FAILED: No Pexels image found for: {search_prompt}")
            except Exception as e:
                logger.warning(f"⚠️ STEP 1 ERROR: Pexels search failed: {e}", exc_info=True)
        else:
            logger.info(f"ℹ️ STEP 1 SKIPPED: use_pexels=false")

        # Step 2: Fall back to SDXL generation
        if not image and image_request.use_generation:
            keywords = image_request.keywords or []

            # Build enhanced generation prompt using keywords if available
            generation_prompt = build_enhanced_search_prompt(image_request.prompt, keywords)

            logger.info(f"🎨 STEP 2: Generating image: {generation_prompt}")
            if keywords:
                logger.debug(f"   Keywords: {', '.join(keywords)}")

            try:
                import os
                from pathlib import Path

                # ═══════════════════════════════════════════════════════════
                # SAVE TO USER'S DOWNLOADS FOLDER (For Preview & Approval)
                # ═══════════════════════════════════════════════════════════
                # Instead of temp folder, save to Downloads for user access
                downloads_path = str(Path.home() / "Downloads" / "glad-labs-generated-images")
                os.makedirs(downloads_path, exist_ok=True)

                # Create filename with timestamp and task_id for traceability
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                task_id_str = image_request.task_id if image_request.task_id else "no-task"
                output_file = f"sdxl_{timestamp}_{task_id_str}.png"
                output_path = os.path.join(downloads_path, output_file)

                logger.info(f"📁 Will save generated image to: {output_path}")

                success = await image_service.generate_image(
                    prompt=generation_prompt,
                    output_path=output_path,
                    model=ImageModel(image_request.image_model) if image_request.image_model else None,
                    num_inference_steps=image_request.num_inference_steps,
                    guidance_scale=image_request.guidance_scale,
                    high_quality=image_request.high_quality,
                    task_id=image_request.task_id,  # Pass task_id for progress tracking
                )

                if success and os.path.exists(output_path):
                    # Generated image successfully - create metadata for it
                    logger.info(f"✅ STEP 2 SUCCESS: Generated image: {output_path}")

                    # Get file size for metadata
                    file_size = os.path.getsize(output_path)
                    logger.info(f"   File size: {file_size} bytes")

                    # ═══════════════════════════════════════════════════════════
                    # FOR NOW: Keep locally in Downloads (for preview & approval)
                    # ═══════════════════════════════════════════════════════════
                    # The local path will be stored in task metadata
                    # On approval, the image will be uploaded to Cloudinary
                    # This allows users to preview and iterate before publishing

                    logger.info(f"📁 Image saved locally to: {output_path}")
                    logger.info(f"⏳ Image will be uploaded to CDN after approval")

                    # Create metadata object for generated image
                    # URL is local file path for now (frontend can construct file:// URL)
                    image = FeaturedImageMetadata(
                        url=output_path,  # Local path for preview
                        thumbnail=output_path,  # Local path for preview
                        photographer="SDXL (AI Generated)",
                        photographer_url="",
                        width=1024,  # SDXL standard output
                        height=1024,
                        alt_text=image_request.prompt,
                        source="sdxl-local-preview",  # Mark as local preview
                        search_query=image_request.prompt,
                    )
                    logger.info(f"✅ Created image metadata (local preview): {output_path}")
            except Exception as e:
                logger.warning(f"⚠️ SDXL generation failed: {e}", exc_info=True)
        elif image and not image_request.use_generation:
            logger.info(f"ℹ️ STEP 2 SKIPPED: Pexels found image, use_generation=false")
        elif not image and not image_request.use_generation:
            logger.info(
                f"ℹ️ STEP 2 SKIPPED: use_generation=false (Pexels search failed but SDXL disabled)"
            )

        # Return result
        if image:
            elapsed = time.time() - start_time

            # ═══════════════════════════════════════════════════════════
            # Unload SDXL models after generation to free memory
            # ═══════════════════════════════════════════════════════════
            if hasattr(image_service, "_unload_model"):
                image_service._unload_model()

            # ═══════════════════════════════════════════════════════════
            # NOTE: Image is in Downloads folder for preview/approval
            # Frontend should store local_path in task metadata
            # Approval endpoint will upload to Cloudinary and update posts table
            # ═══════════════════════════════════════════════════════════

            return ImageGenerationResponse(
                success=True,
                image_url=image.url,  # Local path for preview
                local_path=(
                    image.url if image.source == "sdxl-local-preview" else None
                ),  # Path to local file
                preview_mode=image.source == "sdxl-local-preview",  # Mark as preview mode
                image=ImageMetadata(
                    url=image.url,
                    source=image.source,
                    photographer=image.photographer,
                    photographer_url=image.photographer_url,
                    width=image.width,
                    height=image.height,
                ),
                message=f"✅ Image generated and saved locally (preview mode). Review and approve to publish.",
                generation_time=elapsed,
            )

        # ═══════════════════════════════════════════════════════════
        # Unload SDXL models if generation was requested but failed
        # ═══════════════════════════════════════════════════════════
        if image_request.use_generation and hasattr(image_service, "_unload_model"):
            image_service._unload_model()

        elapsed = time.time() - start_time
        return ImageGenerationResponse(
            success=False,
            image_url="",
            image=None,
            message="❌ No image found. Ensure PEXELS_API_KEY is set in environment or GPU available for SDXL.",
            generation_time=elapsed,
            preview_mode=False,
        )

    except Exception as e:
        logger.error(f"❌ Image generation error: {e}", exc_info=True)
        elapsed = time.time() - start_time
        return ImageGenerationResponse(
            success=False,
            image_url="",
            image=None,
            message="An internal error occurred",
            generation_time=elapsed,
        )


@media_router.get(
    "/images/search",
    response_model=ImageGenerationResponse,
    summary="Search for images",
    description="Search Pexels for images by query",
)
async def search_images(
    query: str = Query(..., min_length=3, description="Search query"),
    count: int = Query(1, ge=1, le=20, description="Number of images (1-20)"),
    current_user: dict = Depends(get_current_user),
):
    """
    Search for images by query.

    Returns a single image URL (or multiple if count > 1).

    **Examples:**
    ```bash
    # Get one image
    curl "http://localhost:8000/api/media/images/search?query=AI%20gaming&count=1"

    # Get multiple images for gallery
    curl "http://localhost:8000/api/media/images/search?query=futuristic%20tech&count=5"
    ```
    """
    start_time = time.time()
    image_service = await get_image_service()

    try:
        logger.info(f"🔍 Searching for: {query}")

        if count == 1:
            # Single image
            image = await image_service.search_featured_image(topic=query)
            if image:
                elapsed = time.time() - start_time
                return ImageGenerationResponse(
                    success=True,
                    image_url=image.url,
                    image=ImageMetadata(
                        url=image.url,
                        source=image.source,
                        photographer=image.photographer,
                        photographer_url=image.photographer_url,
                        width=image.width,
                        height=image.height,
                    ),
                    message=f"✅ Found image: {image.photographer}",
                    generation_time=elapsed,
                )
        else:
            # Multiple images for gallery
            images = await image_service.get_images_for_gallery(topic=query, count=count)
            if images:
                # Return first image in response
                first = images[0]
                elapsed = time.time() - start_time
                return ImageGenerationResponse(
                    success=True,
                    image_url=first.url,
                    image=ImageMetadata(
                        url=first.url,
                        source=first.source,
                        photographer=first.photographer,
                        photographer_url=first.photographer_url,
                        width=first.width,
                        height=first.height,
                    ),
                    message=f"✅ Found {len(images)} images",
                    generation_time=elapsed,
                )

        # Not found
        elapsed = time.time() - start_time
        return ImageGenerationResponse(
            success=False,
            image_url="",
            image=None,
            message=f"❌ No images found for: {query}",
            generation_time=elapsed,
        )

    except Exception as e:
        logger.error(f"❌ Search error: {e}", exc_info=True)
        elapsed = time.time() - start_time
        return ImageGenerationResponse(
            success=False,
            image_url="",
            image=None,
            message="An internal error occurred",
            generation_time=elapsed,
        )


@media_router.get(
    "/health",
    response_model=HealthResponse,
    summary="Check image services health",
    description="Verify Pexels and SDXL availability",
)
async def health_check():
    """
    Check health of image services.

    Returns which image sources are available:
    - Pexels: FREE stock image API (unlimited)
    - SDXL: GPU-based generation (if CUDA available)

    **Example:**
    ```bash
    curl http://localhost:8000/api/media/health
    ```
    """
    image_service = await get_image_service()

    try:
        pexels_ok = bool(image_service.pexels_api_key)
        sdxl_ok = image_service.sdxl_available

        status = "healthy" if (pexels_ok or sdxl_ok) else "degraded"

        message_parts = []
        if pexels_ok:
            message_parts.append("✅ Pexels API available")
        else:
            message_parts.append("❌ Pexels API not configured (set PEXELS_API_KEY)")

        if sdxl_ok:
            message_parts.append("✅ SDXL GPU available")
        else:
            message_parts.append("❌ SDXL not available (requires CUDA GPU)")

        # Check if a model is currently loaded
        active_model = None
        if hasattr(image_service, "_active_model") and image_service._active_model is not None:
            active_model = image_service._active_model.value

        return HealthResponse(
            status=status,
            pexels_available=pexels_ok,
            sdxl_available=sdxl_ok,
            active_model=active_model,
            message=" | ".join(message_parts),
        )

    except Exception as e:
        logger.error(f"❌ Health check error: {e}", exc_info=True)
        return HealthResponse(
            status="error",
            pexels_available=False,
            sdxl_available=False,
            message="Error checking services",
        )
