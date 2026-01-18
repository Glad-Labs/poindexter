"""
Cloudinary CMS Integration Service

Handles image management, optimization, and publishing to Cloudinary
for the oversight-hub CMS application.

Strategy:
- Store text content in PostgreSQL database (local control)
- Store images/video in Cloudinary (CDN + optimization)
- Oversight-hub acts as CMS interface for content management

Features:
- Image upload and optimization
- Responsive image variants
- Video hosting
- SEO metadata
- Content delivery optimization
"""

import logging
import os
from typing import Optional, Dict, Any, List, Tuple
import httpx
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class ImageAsset:
    """Represents an image asset stored in Cloudinary"""

    public_id: str  # Cloudinary public ID
    url: str  # Full image URL
    secure_url: str  # HTTPS URL
    width: int
    height: int
    format: str
    size_bytes: int
    created_at: datetime

    # Responsive variants
    thumbnail_url: str = None  # 300x200
    preview_url: str = None  # 600x400
    full_url: str = None  # 1200x800

    # SEO metadata
    alt_text: str = None
    title: str = None


@dataclass
class PublishedContent:
    """Represents content published to the CMS"""

    id: str
    title: str
    slug: str
    content: str
    excerpt: Optional[str]
    featured_image_url: Optional[str]
    featured_image_public_id: Optional[str]  # For Cloudinary management
    author_id: str
    category_id: Optional[str]
    tag_ids: Optional[List[str]]
    status: str  # draft, published
    published_at: Optional[datetime]
    updated_at: datetime


class CloudinaryCMSService:
    """
    Manages image assets and content publishing for the oversight-hub CMS.

    Coordinates:
    - PostgreSQL for content/metadata storage
    - Cloudinary for image/video delivery
    """

    def __init__(self):
        """Initialize Cloudinary service with environment configuration"""
        self.cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
        self.api_key = os.getenv("CLOUDINARY_API_KEY")
        self.api_secret = os.getenv("CLOUDINARY_API_SECRET")

        # Check if Cloudinary is configured
        if not self.cloud_name:
            logger.warning("⚠️  CLOUDINARY_CLOUD_NAME not configured - image optimization disabled")
            self.enabled = False
        else:
            logger.info(f"✅ Cloudinary configured: {self.cloud_name}")
            self.enabled = True

        # Base URLs
        self.api_base_url = (
            f"https://api.cloudinary.com/v1_1/{self.cloud_name}" if self.cloud_name else None
        )
        self.cdn_base_url = (
            f"https://res.cloudinary.com/{self.cloud_name}" if self.cloud_name else None
        )

    async def upload_image(
        self,
        image_url: str,
        folder: str = "oversight-hub",
        alt_text: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Optional[ImageAsset]:
        """
        Upload an image from URL to Cloudinary.

        Args:
            image_url: Public URL of image to upload
            folder: Cloudinary folder path (e.g., "oversight-hub/featured")
            alt_text: Image alt text for SEO
            title: Image title

        Returns:
            ImageAsset with Cloudinary URLs and metadata, or None if disabled
        """
        if not self.enabled:
            logger.warning("⚠️  Cloudinary disabled - returning original URL")
            return ImageAsset(
                public_id=image_url,
                url=image_url,
                secure_url=image_url,
                width=0,
                height=0,
                format="unknown",
                size_bytes=0,
                created_at=datetime.utcnow(),
                alt_text=alt_text,
                title=title,
            )

        try:
            logger.info(f"📤 Uploading image to Cloudinary: {image_url[:80]}...")

            # Generate unique public ID
            public_id = f"{folder}/{uuid4().hex}"

            # Upload using Cloudinary API with async httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_base_url}/image/upload",
                    data={
                        "file": image_url,
                        "public_id": public_id,
                        "folder": folder,
                        "resource_type": "auto",
                        "upload_preset": "oversight-hub",  # Must be configured in Cloudinary
                        "context": f"alt={alt_text}|title={title}" if alt_text or title else None,
                    },
                )

            if response.status_code not in [200, 201]:
                logger.error(
                    f"❌ Cloudinary upload failed: {response.status_code} - {response.text}"
                )
                return None

            data = response.json()
            logger.info(f"✅ Image uploaded: {data.get('public_id')}")

            # Create responsive variants
            thumbnail_url = self._generate_variant_url(
                data["public_id"], width=300, height=200, crop="fill"
            )
            preview_url = self._generate_variant_url(
                data["public_id"], width=600, height=400, crop="fill"
            )
            full_url = self._generate_variant_url(
                data["public_id"], width=1200, height=800, crop="fill"
            )

            asset = ImageAsset(
                public_id=data["public_id"],
                url=data["url"],
                secure_url=data["secure_url"],
                width=data["width"],
                height=data["height"],
                format=data["format"],
                size_bytes=data["bytes"],
                created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")),
                thumbnail_url=thumbnail_url,
                preview_url=preview_url,
                full_url=full_url,
                alt_text=alt_text,
                title=title,
            )

            return asset

        except Exception as e:
            logger.error(f"❌ Error uploading image to Cloudinary: {e}", exc_info=True)
            return None

    def _generate_variant_url(
        self, public_id: str, width: int, height: int, crop: str = "fill"
    ) -> str:
        """Generate a Cloudinary image variant URL with transformations"""
        if not self.enabled:
            return ""

        transformation = f"w_{width},h_{height},c_{crop},q_auto:good,f_auto"
        return f"{self.cdn_base_url}/image/fetch/{transformation}/{public_id}"

    async def optimize_featured_image(
        self, featured_image_url: str, content_title: Optional[str] = None
    ) -> Tuple[str, Dict[str, str]]:
        """
        Optimize a featured image for web delivery.

        Args:
            featured_image_url: URL of image to optimize
            content_title: Title for alt text generation

        Returns:
            Tuple of (optimized_url, metadata_dict)
        """
        if not self.enabled:
            logger.debug("Cloudinary disabled - returning original URL")
            return featured_image_url, {"source": "original", "optimized": False}

        try:
            asset = await self.upload_image(
                featured_image_url,
                folder="oversight-hub/featured-images",
                alt_text=(
                    f"Featured image for {content_title}" if content_title else "Featured image"
                ),
            )

            if not asset:
                return featured_image_url, {
                    "source": "fallback",
                    "optimized": False,
                    "error": "Failed to upload to Cloudinary",
                }

            return asset.secure_url, {
                "source": "cloudinary",
                "optimized": True,
                "public_id": asset.public_id,
                "width": asset.width,
                "height": asset.height,
                "responsive_urls": {
                    "thumbnail": asset.thumbnail_url,
                    "preview": asset.preview_url,
                    "full": asset.full_url,
                },
            }

        except Exception as e:
            logger.error(f"❌ Error optimizing featured image: {e}")
            return featured_image_url, {"source": "fallback", "optimized": False, "error": str(e)}

    async def delete_image(self, public_id: str) -> bool:
        """
        Delete an image from Cloudinary.

        Args:
            public_id: Cloudinary public ID

        Returns:
            True if deleted, False if failed
        """
        if not self.enabled:
            logger.warning("Cloudinary disabled - cannot delete image")
            return False

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.delete(
                    f"{self.api_base_url}/image/destroy",
                    data={"public_id": public_id},
                    auth=(self.api_key, self.api_secret),
                )

            if response.status_code == 200:
                logger.info(f"✅ Image deleted: {public_id}")
                return True
            else:
                logger.error(f"❌ Failed to delete image: {response.text}")
                return False

        except Exception as e:
            logger.error(f"❌ Error deleting image: {e}")
            return False

    async def get_usage_stats(self) -> Optional[Dict[str, Any]]:
        """Get Cloudinary account usage statistics"""
        if not self.enabled:
            return None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.api_base_url}/usage", auth=(self.api_key, self.api_secret)
                )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get usage stats: {response.text}")
                return None

        except Exception as e:
            logger.error(f"Error getting usage stats: {e}")
            return None


# Singleton instance
_cloudinary_service: Optional[CloudinaryCMSService] = None


def get_cloudinary_cms_service() -> CloudinaryCMSService:
    """Get or create the Cloudinary CMS service singleton"""
    global _cloudinary_service
    if _cloudinary_service is None:
        _cloudinary_service = CloudinaryCMSService()
    return _cloudinary_service
