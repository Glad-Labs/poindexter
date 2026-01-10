"""
Image Generation Error Handling & Fallback Service

Implements intelligent fallback chain for image generation:
1. Pexels (free, reliable)
2. SDXL (local GPU, high quality)
3. Placeholder/Default (always works)

Provides user feedback at each step.
"""

import logging
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from datetime import datetime
import os

logger = logging.getLogger(__name__)


@dataclass
class ImageFallbackResult:
    """Result of image generation attempt with fallback chain"""

    success: bool
    url: str
    source: str  # 'pexels', 'sdxl', 'placeholder'
    width: int
    height: int
    alt_text: str
    error_message: Optional[str] = None
    user_feedback: Optional[str] = None  # Message to show user
    metadata: Dict[str, Any] = None


class ImageFallbackHandler:
    """
    Manages intelligent fallback chain for image generation.

    Strategy:
    1. Try Pexels (free, fast, reliable)
    2. Try SDXL (local GPU, high quality)
    3. Return placeholder (always available)

    Each step has error handling and provides user feedback.
    """

    def __init__(self):
        """Initialize fallback handler"""
        self.pexels_api_key = os.getenv("PEXELS_API_KEY")
        self.sdxl_available = self._check_sdxl_availability()

        logger.info(f"✅ ImageFallbackHandler initialized")
        logger.info(
            f"   Pexels: {'✅ Available' if self.pexels_api_key else '⚠️  Disabled (no API key)'}"
        )
        logger.info(f"   SDXL: {'✅ Available' if self.sdxl_available else '❌ Not available'}")

    def _check_sdxl_availability(self) -> bool:
        """Check if SDXL model is available"""
        try:
            # Check if Ollama is running with SDXL
            import requests

            response = requests.get("http://localhost:11434/api/tags", timeout=2)
            if response.status_code == 200:
                models = response.json().get("models", [])
                sdxl_available = any("sdxl" in m.get("name", "").lower() for m in models)
                return sdxl_available
        except (requests.RequestException, ValueError, KeyError):
            pass
        return False

    async def generate_with_fallback(
        self,
        prompt: str,
        keywords: Optional[list] = None,
        task_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> ImageFallbackResult:
        """
        Try to generate/find image with fallback chain.

        Args:
            prompt: Search/generation prompt
            keywords: Keywords for enhancement
            task_id: Task ID for tracking
            title: Content title for alt text

        Returns:
            ImageFallbackResult with success status, URL, source, and user feedback
        """
        alt_text = title or prompt[:100]

        # Try Step 1: Pexels
        logger.info(f"🔍 STEP 1: Attempting Pexels search...")
        pexels_result = await self._try_pexels(prompt, keywords, alt_text)
        if pexels_result.success:
            logger.info(f"✅ STEP 1 SUCCESS: {pexels_result.user_feedback}")
            return pexels_result

        logger.warning(f"⚠️  STEP 1 FAILED: {pexels_result.error_message}")

        # Try Step 2: SDXL
        logger.info(f"🎨 STEP 2: Attempting SDXL generation...")
        sdxl_result = await self._try_sdxl(prompt, keywords, task_id, alt_text)
        if sdxl_result.success:
            logger.info(f"✅ STEP 2 SUCCESS: {sdxl_result.user_feedback}")
            return sdxl_result

        logger.warning(f"⚠️  STEP 2 FAILED: {sdxl_result.error_message}")

        # Step 3: Placeholder (always works)
        logger.info(f"📋 STEP 3: Using placeholder...")
        placeholder_result = self._get_placeholder(prompt, alt_text)
        logger.info(f"✅ STEP 3 SUCCESS: {placeholder_result.user_feedback}")
        return placeholder_result

    async def _try_pexels(
        self, prompt: str, keywords: Optional[list], alt_text: str
    ) -> ImageFallbackResult:
        """Try to find image via Pexels API"""
        try:
            if not self.pexels_api_key:
                return ImageFallbackResult(
                    success=False,
                    url="",
                    source="pexels",
                    width=0,
                    height=0,
                    alt_text=alt_text,
                    error_message="Pexels API key not configured",
                    user_feedback=None,
                )

            import requests

            # Build search query
            search_query = prompt
            if keywords:
                search_query = " ".join(keywords[:3])  # Use first 3 keywords

            logger.debug(f"   Searching Pexels for: {search_query}")

            response = requests.get(
                "https://api.pexels.com/v1/search",
                params={"query": search_query, "per_page": 1, "page": 1},
                headers={"Authorization": self.pexels_api_key},
                timeout=10,
            )

            if response.status_code != 200:
                return ImageFallbackResult(
                    success=False,
                    url="",
                    source="pexels",
                    width=0,
                    height=0,
                    alt_text=alt_text,
                    error_message=f"Pexels API error: {response.status_code}",
                    user_feedback=None,
                )

            data = response.json()
            photos = data.get("photos", [])

            if not photos:
                return ImageFallbackResult(
                    success=False,
                    url="",
                    source="pexels",
                    width=0,
                    height=0,
                    alt_text=alt_text,
                    error_message="No images found",
                    user_feedback=None,
                )

            photo = photos[0]
            image_url = photo["src"]["large2x"]  # Use large size

            return ImageFallbackResult(
                success=True,
                url=image_url,
                source="pexels",
                width=photo["width"],
                height=photo["height"],
                alt_text=f"{alt_text} (from Pexels)",
                error_message=None,
                user_feedback=f"✅ Found free stock image from Pexels",
                metadata={
                    "photographer": photo.get("photographer", "Unknown"),
                    "photographer_url": photo.get("photographer_url", ""),
                    "pexels_url": photo.get("url", ""),
                },
            )

        except Exception as e:
            logger.error(f"❌ Pexels error: {e}")
            return ImageFallbackResult(
                success=False,
                url="",
                source="pexels",
                width=0,
                height=0,
                alt_text=alt_text,
                error_message=str(e),
                user_feedback=None,
            )

    async def _try_sdxl(
        self, prompt: str, keywords: Optional[list], task_id: Optional[str], alt_text: str
    ) -> ImageFallbackResult:
        """Try to generate image via SDXL"""
        try:
            if not self.sdxl_available:
                return ImageFallbackResult(
                    success=False,
                    url="",
                    source="sdxl",
                    width=0,
                    height=0,
                    alt_text=alt_text,
                    error_message="SDXL not available (no GPU detected)",
                    user_feedback=None,
                )

            from services.image_service import ImageService

            image_service = ImageService()

            # Build generation prompt
            generation_prompt = prompt
            if keywords:
                generation_prompt = f"{prompt}, featuring {', '.join(keywords[:3])}"

            logger.debug(f"   Generating with SDXL: {generation_prompt}")

            # Create temp output path
            import tempfile
            from pathlib import Path

            output_dir = Path.home() / "Downloads" / "glad-labs-generated-images"
            output_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            task_id_str = task_id if task_id else "no-task"
            output_path = output_dir / f"sdxl_{timestamp}_{task_id_str}.png"

            logger.debug(f"   Output path: {output_path}")

            success = await image_service.generate_image(
                prompt=generation_prompt,
                output_path=str(output_path),
                num_inference_steps=20,  # Balanced quality/speed
                guidance_scale=7.5,
                use_refinement=False,
                high_quality=False,
            )

            if not success or not output_path.exists():
                return ImageFallbackResult(
                    success=False,
                    url="",
                    source="sdxl",
                    width=0,
                    height=0,
                    alt_text=alt_text,
                    error_message="SDXL generation failed",
                    user_feedback=None,
                )

            return ImageFallbackResult(
                success=True,
                url=str(output_path),  # Local path for preview
                source="sdxl",
                width=1024,
                height=1024,
                alt_text=f"{alt_text} (AI Generated)",
                error_message=None,
                user_feedback=f"✅ Generated custom image with SDXL (saved locally for preview)",
                metadata={
                    "generator": "SDXL",
                    "local_preview": True,
                    "needs_cloudinary_upload": True,
                },
            )

        except Exception as e:
            logger.error(f"❌ SDXL error: {e}")
            return ImageFallbackResult(
                success=False,
                url="",
                source="sdxl",
                width=0,
                height=0,
                alt_text=alt_text,
                error_message=str(e),
                user_feedback=None,
            )

    def _get_placeholder(self, prompt: str, alt_text: str) -> ImageFallbackResult:
        """Return no placeholder - skip placeholder images since they require external API"""
        # via.placeholder.com requires authentication and is not reliable
        # Instead, return None to gracefully skip image display
        placeholder_url = None

        logger.info(f"   Skipping placeholder image (external service not available)")

        return ImageFallbackResult(
            success=True,
            url=placeholder_url,
            source="placeholder",
            width=1200,
            height=800,
            alt_text=f"{alt_text} (placeholder)",
            error_message=None,
            user_feedback=f"⚠️  No image found - using placeholder. You can add a custom image later.",
            metadata={
                "type": "placeholder",
                "replaceable": True,
                "note": "User should replace with real image during content review",
            },
        )


# Singleton instance
_image_fallback_handler: Optional[ImageFallbackHandler] = None


def get_image_fallback_handler() -> ImageFallbackHandler:
    """Get or create the image fallback handler singleton"""
    global _image_fallback_handler
    if _image_fallback_handler is None:
        _image_fallback_handler = ImageFallbackHandler()
    return _image_fallback_handler
