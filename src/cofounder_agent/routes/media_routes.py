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
import os
import uuid
import base64
from datetime import datetime
from io import BytesIO

# Cloud storage imports
try:
    import cloudinary
    import cloudinary.uploader
    CLOUDINARY_AVAILABLE = True
except ImportError:
    CLOUDINARY_AVAILABLE = False

try:
    import boto3
    from botocore.config import Config
    S3_AVAILABLE = True
except ImportError:
    S3_AVAILABLE = False

from services.image_service import ImageService, FeaturedImageMetadata

logger = logging.getLogger(__name__)
media_router = APIRouter(prefix="/api/media", tags=["Media"])


# ═══════════════════════════════════════════════════════════════════════════
# CLOUDINARY SETUP (Primary - Free Tier)
# ═══════════════════════════════════════════════════════════════════════════

if CLOUDINARY_AVAILABLE and os.getenv('CLOUDINARY_CLOUD_NAME'):
    cloudinary.config(
        cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
        api_key=os.getenv('CLOUDINARY_API_KEY'),
        api_secret=os.getenv('CLOUDINARY_API_SECRET')
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
    if not CLOUDINARY_AVAILABLE or not os.getenv('CLOUDINARY_CLOUD_NAME'):
        return None
    
    try:
        result = cloudinary.uploader.upload(
            file_path,
            folder="generated",  # Organize in folder
            resource_type="image",
            invalidate=True,  # Invalidate CDN cache
            tags=["blog-generated"] + ([task_id] if task_id else []),
            context={
                "task_id": task_id or "unknown",
                "generated_date": datetime.now().isoformat()
            }
        )
        
        public_url = result['secure_url']  # HTTPS URL
        logger.info(f"✅ Uploaded to Cloudinary: {public_url}")
        return public_url
        
    except Exception as e:
        logger.error(f"❌ Cloudinary upload failed: {e}", exc_info=True)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# S3 CLIENT SETUP (Fallback/Future)
# ═══════════════════════════════════════════════════════════════════════════

_s3_client = None

def get_s3_client():
    """Get or create S3 client for image uploads (fallback option)"""
    global _s3_client
    if _s3_client is None:
        # Check if AWS credentials are configured
        if S3_AVAILABLE and os.getenv('AWS_ACCESS_KEY_ID') and os.getenv('AWS_S3_BUCKET'):
            try:
                _s3_client = boto3.client(
                    's3',
                    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                    region_name=os.getenv('AWS_S3_REGION', 'us-east-1'),
                    config=Config(signature_version='s3v4') if S3_AVAILABLE else None
                )
                logger.info("✅ S3 client initialized (fallback)")
            except Exception as e:
                logger.warning(f"⚠️ S3 client initialization failed: {e}")
                _s3_client = False  # Mark as explicitly disabled
        else:
            logger.info("ℹ️ AWS S3 not configured (optional fallback)")
            _s3_client = False
    
    return _s3_client if _s3_client else None

async def upload_to_s3(file_path: str, task_id: Optional[str] = None) -> Optional[str]:
    """
    Upload generated image to S3 and return public URL.
    
    Args:
        file_path: Local path to image file
        task_id: Task ID for metadata (optional)
        
    Returns:
        Public URL if successful, None if S3 not configured
    """
    s3 = get_s3_client()
    if not s3:
        return None
    
    try:
        bucket = os.getenv('AWS_S3_BUCKET')
        if not bucket:
            logger.warning("S3 bucket not configured")
            return None
        
        # Generate unique key
        image_key = f"generated/{int(time.time())}-{uuid.uuid4()}.png"
        
        # Read file
        with open(file_path, 'rb') as f:
            file_data = f.read()
        
        # Prepare metadata
        metadata = {'generated-date': datetime.now().isoformat()}
        if task_id:
            metadata['task-id'] = task_id
        
        # Upload to S3
        s3.upload_fileobj(
            BytesIO(file_data),
            bucket,
            image_key,
            ExtraArgs={
                'ContentType': 'image/png',
                'CacheControl': 'max-age=31536000, immutable',  # Cache 1 year
                'Metadata': metadata
            }
        )
        
        logger.info(f"✅ Uploaded to S3: s3://{bucket}/{image_key}")
        
        # Return CloudFront URL if configured, otherwise S3 URL
        cdn_domain = os.getenv('AWS_CLOUDFRONT_DOMAIN')
        if cdn_domain:
            public_url = f"https://{cdn_domain}/{image_key}"
            logger.info(f"✅ CloudFront URL: {public_url}")
        else:
            public_url = f"https://s3.amazonaws.com/{bucket}/{image_key}"
            logger.info(f"✅ S3 URL: {public_url}")
        
        return public_url
        
    except Exception as e:
        logger.error(f"❌ S3 upload failed: {e}", exc_info=True)
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
    task_id: Optional[str] = Field(
        None,
        description="Optional task ID for WebSocket progress tracking"
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
                    task_id=request.task_id,  # Pass task_id for progress tracking
                )
                
                if success and os.path.exists(output_path):
                    # Generated image successfully - create metadata for it
                    logger.info(f"✅ Generated image: {output_path}")
                    
                    # Get file size for metadata
                    file_size = os.path.getsize(output_path)
                    logger.info(f"   File size: {file_size} bytes")
                    
                    # ═══════════════════════════════════════════════════════════
                    # UPLOAD TO CLOUDINARY (Primary) or S3 (Fallback)
                    # ═══════════════════════════════════════════════════════════
                    
                    task_id_str = request.task_id if request.task_id else None
                    
                    # Try Cloudinary first (free tier for dev, fast)
                    image_url_path = await upload_to_cloudinary(output_path, task_id_str)
                    image_source = "sdxl-cloudinary"
                    
                    # Fall back to S3 if Cloudinary not available
                    if not image_url_path:
                        logger.info("ℹ️ Cloudinary not available, trying S3...")
                        image_url_path = await upload_to_s3(output_path, task_id_str)
                        image_source = "sdxl-s3"
                    
                    # Fall back to local filesystem as last resort
                    if not image_url_path:
                        logger.info("ℹ️ Cloud storage not available, using local filesystem fallback")
                        image_filename = f"post-{uuid.uuid4()}.png"
                        image_url_path = f"/images/generated/{image_filename}"
                        full_disk_path = f"web/public-site/public{image_url_path}"
                        
                        # Ensure directory exists
                        os.makedirs(os.path.dirname(full_disk_path), exist_ok=True)
                        
                        # Copy from temp location to persistent storage
                        with open(output_path, 'rb') as f:
                            image_bytes = f.read()
                        
                        with open(full_disk_path, 'wb') as f:
                            f.write(image_bytes)
                        
                        logger.info(f"💾 Saved image to: {full_disk_path}")
                        image_source = "sdxl-local"
                    
                    # Create metadata object for generated image
                    image = FeaturedImageMetadata(
                        url=image_url_path,  # Return URL (Cloudinary/S3 or local)
                        thumbnail=image_url_path,
                        photographer="SDXL (AI Generated)",
                        photographer_url="",
                        width=1024,  # SDXL standard output
                        height=1024,
                        alt_text=request.prompt,
                        source=image_source,
                        search_query=request.prompt,
                    )
                    logger.info(f"✅ Created image metadata: {image_url_path}")
            except Exception as e:
                logger.warning(f"⚠️ SDXL generation failed: {e}")
        
        # Return result
        if image:
            elapsed = time.time() - start_time
            
            # ═══════════════════════════════════════════════════════════
            # NOTE: Image URL is returned to frontend
            # Frontend should store this in task metadata when saving the task
            # Approval endpoint will find it in task_metadata and write to posts table
            # ═══════════════════════════════════════════════════════════
            
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
