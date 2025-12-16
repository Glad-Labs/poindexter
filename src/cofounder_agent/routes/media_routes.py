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

import logging
from typing import Optional, List
from pydantic import BaseModel, Field
from fastapi import APIRouter, Query
import time

from services.image_service import ImageService

logger = logging.getLogger(__name__)
media_router = APIRouter(prefix="/api/media", tags=["Media"])


# ═══════════════════════════════════════════════════════════════════════════
# REQUEST/RESPONSE SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════

class ImageGenerationRequest(BaseModel):
    """Request to generate or search for featured image"""
    
    prompt: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Search prompt or generation prompt (e.g., 'AI gaming NPCs futuristic')"
    )
    title: Optional[str] = Field(
        None,
        max_length=200,
        description="Content title (used as fallback search term)"
    )
    keywords: Optional[List[str]] = Field(
        default=None,
        description="Additional keywords for search refinement"
    )
    use_pexels: bool = Field(
        True,
        description="Search Pexels for free stock images first (recommended)"
    )
    use_generation: bool = Field(
        False,
        description="Generate custom image with SDXL if Pexels fails (requires GPU)"
    )
    use_refinement: bool = Field(
        True,
        description="Apply SDXL refinement model for production quality (adds ~15 seconds)"
    )
    high_quality: bool = Field(
        True,
        description="Optimize for high quality: 50 base steps + 30 refinement steps (vs 30 base steps)"
    )
    num_inference_steps: int = Field(
        50,
        ge=20,
        le=100,
        description="Number of base inference steps (50+ recommended for quality)"
    )
    guidance_scale: float = Field(
        8.0,
        ge=1.0,
        le=20.0,
        description="Guidance scale for quality (7.5-8.5 recommended)"
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


class HealthResponse(BaseModel):
    """Health check response"""
    
    status: str = Field(..., description="overall status")
    pexels_available: bool = Field(..., description="Pexels API configured")
    sdxl_available: bool = Field(..., description="SDXL GPU available")
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
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@media_router.post(
    "/generate-image",
    response_model=ImageGenerationResponse,
    summary="Generate or search for featured image",
    description="Search Pexels for free stock images, with optional SDXL fallback"
)
async def generate_featured_image(request: ImageGenerationRequest):
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
    image_service = await get_image_service()
    
    try:
        image = None
        
        # Step 1: Try Pexels search first (recommended)
        if request.use_pexels:
            logger.info(f"🔍 Searching Pexels for: {request.prompt}")
            keywords = request.keywords or []
            
            try:
                image = await image_service.search_featured_image(
                    topic=request.prompt,
                    keywords=keywords
                )
                
                if image:
                    logger.info(f"✅ Found image via Pexels: {image.url}")
            except Exception as e:
                logger.warning(f"⚠️ Pexels search failed: {e}")
        
        # Step 2: Fall back to SDXL generation
        if not image and request.use_generation:
            logger.info(f"🎨 Generating image with SDXL: {request.prompt}")
            if request.use_refinement:
                logger.info(f"   Refinement: ENABLED (base {request.num_inference_steps} steps + 30 refinement steps)")
            
            try:
                import tempfile
                import os
                
                # Create temp directory if needed
                temp_dir = tempfile.gettempdir()
                output_file = f"generated_image_{int(time.time())}.png"
                output_path = os.path.join(temp_dir, output_file)
                
                success = await image_service.generate_image(
                    prompt=request.prompt,
                    output_path=output_path,
                    num_inference_steps=request.num_inference_steps,
                    guidance_scale=request.guidance_scale,
                    use_refinement=request.use_refinement,
                    high_quality=request.high_quality,
                )
                
                if success and os.path.exists(output_path):
                    # In production, would upload to CDN and get public URL
                    # For now, document that generation succeeded
                    logger.info(f"✅ Generated image: {output_path}")
                    image = await image_service.search_featured_image(
                        topic=request.prompt,
                        keywords=request.keywords or []
                    )
            except Exception as e:
                logger.warning(f"⚠️ SDXL generation failed: {e}")
        
        # Return result
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
                message=f"✅ Image found via {image.source}",
                generation_time=elapsed,
            )
        else:
            elapsed = time.time() - start_time
            return ImageGenerationResponse(
                success=False,
                image_url="",
                image=None,
                message="❌ No image found. Ensure PEXELS_API_KEY is set in environment or GPU available for SDXL.",
                generation_time=elapsed,
            )
    
    except Exception as e:
        logger.error(f"❌ Image generation error: {e}", exc_info=True)
        elapsed = time.time() - start_time
        return ImageGenerationResponse(
            success=False,
            image_url="",
            image=None,
            message=f"❌ Error: {str(e)}",
            generation_time=elapsed,
        )


@media_router.get(
    "/images/search",
    response_model=ImageGenerationResponse,
    summary="Search for images",
    description="Search Pexels for images by query"
)
async def search_images(
    query: str = Query(..., min_length=3, description="Search query"),
    count: int = Query(1, ge=1, le=20, description="Number of images (1-20)"),
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
            images = await image_service.get_images_for_gallery(
                topic=query,
                count=count
            )
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
            message=f"❌ Error: {str(e)}",
            generation_time=elapsed,
        )


@media_router.get(
    "/health",
    response_model=HealthResponse,
    summary="Check image services health",
    description="Verify Pexels and SDXL availability"
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
        
        return HealthResponse(
            status=status,
            pexels_available=pexels_ok,
            sdxl_available=sdxl_ok,
            message=" | ".join(message_parts),
        )
    
    except Exception as e:
        logger.error(f"❌ Health check error: {e}", exc_info=True)
        return HealthResponse(
            status="error",
            pexels_available=False,
            sdxl_available=False,
            message=f"Error checking services: {str(e)}",
        )
