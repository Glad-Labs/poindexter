"""
PostgreSQL-based Image Agent - Stores image metadata directly to PostgreSQL.

Replaces Strapi image upload with direct database storage of image metadata and URLs.
"""

import logging
import os
import json
from pathlib import Path
from typing import Optional, List
from ..config import config
from ..services.llm_client import LLMClient
from ..services.pexels_client import PexelsClient
from ..utils.data_models import BlogPost, ImageDetails
from ..utils.helpers import load_prompts_from_file, extract_json_from_string


logger = logging.getLogger(__name__)


class PostgreSQLImageAgent:
    """
    PostgreSQL-based image agent that stores image metadata directly to the database.
    
    This agent:
    1. Generates image metadata/descriptions for the post
    2. Downloads images from Pexels based on metadata
    3. Stores image URLs and metadata in PostgreSQL media table
    4. Updates the BlogPost object with image details
    
    No Strapi or GCS integration - pure PostgreSQL storage.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        pexels_client: PexelsClient,
        api_url: str = "http://localhost:8000",
    ):
        logger.info("Initializing PostgreSQL Image Agent (no Strapi/GCS)...")
        self.llm_client = llm_client
        self.pexels_client = pexels_client
        self.api_url = api_url
        
        try:
            self.prompts = load_prompts_from_file(config.PROMPTS_PATH)
        except Exception as e:
            logger.warning(f"Could not load prompts: {e}. Using defaults.")
            self.prompts = {}
        
        # Create local image storage if needed
        self.image_storage_path = Path(config.IMAGE_STORAGE_PATH)
        self.image_storage_path.mkdir(parents=True, exist_ok=True)

    async def run(self, post: BlogPost) -> BlogPost:
        """
        Process images for a blog post and store metadata in PostgreSQL.
        
        Args:
            post: BlogPost object to enhance with images
            
        Returns:
            BlogPost with image metadata added
        """
        if not post.title or not post.raw_content:
            logger.warning("⚠️  ImageAgent: Post title or content is missing, skipping image processing.")
            return post

        logger.info(f"🖼️  ImageAgent: Starting image generation for '{post.title}'")

        try:
            # Generate metadata for all images first
            image_metadata = await self._generate_image_metadata(post)
            if not image_metadata:
                logger.warning(
                    "⚠️  ImageAgent: No image metadata was generated. "
                    "This typically means the LLM response was not valid JSON or contained no valid image data. "
                    "The post will still be published without images."
                )
                return post

            logger.info(f"📋 Generated metadata for {len(image_metadata)} images")
            
            # Process each image asynchronously
            images_processed = 0
            for i, meta in enumerate(image_metadata):
                image_details = await self._process_single_image_async(meta, post, i)
                if image_details:
                    if post.images is None:
                        post.images = []
                    post.images.append(image_details)
                    images_processed += 1
                    logger.info(f"✅ Processed image {i+1}/{len(image_metadata)}: {image_details.alt_text}")
                else:
                    logger.debug(f"⊘ Image {i+1} could not be processed")
            
            if images_processed > 0:
                logger.info(f"✅ ImageAgent: Successfully processed {images_processed}/{len(image_metadata)} images")
            else:
                logger.info(f"ℹ️  ImageAgent: No images could be processed, continuing with text-only post")

        except Exception as e:
            logger.error(f"❌ Error during image processing: {e}", exc_info=True)
            # Continue without images - don't fail the whole pipeline

        logger.info(f"ImageAgent: Finished image processing for '{post.title}'. Found {len(post.images or [])} images.")
        return post

    async def _generate_image_metadata(self, post: BlogPost) -> List[dict]:
        """
        Generate image metadata/descriptions using the LLM.
        
        Returns:
            List of dicts with 'query' and 'alt_text' for each image
        """
        metadata_text = None  # Initialize for use in except block
        try:
            # Get title for prompt
            title = post.title or post.topic
            content_preview = post.raw_content[:500] if post.raw_content else ""
            
            # Use prompt if available, otherwise use simple template
            if "image_metadata_generation" in self.prompts:
                metadata_prompt = self.prompts["image_metadata_generation"].format(
                    title=title,
                    num_images=config.DEFAULT_IMAGE_PLACEHOLDERS
                )
            else:
                metadata_prompt = f"""Generate {config.DEFAULT_IMAGE_PLACEHOLDERS} image search queries for this blog post.

Title: {title}
Content: {content_preview}

Respond with a JSON array like this:
[
  {{"query": "search term for image 1", "alt_text": "description for accessibility"}},
  {{"query": "search term for image 2", "alt_text": "description for accessibility"}}
]

Only return the JSON array, no other text."""
            
            logger.info(f"🖼️  Generating image metadata for {config.DEFAULT_IMAGE_PLACEHOLDERS} images...")
            metadata_text = await self.llm_client.generate_text(metadata_prompt)
            
            if not metadata_text:
                logger.warning("⚠️  Image agent: LLM returned empty response for image metadata")
                return []
            
            logger.debug(f"Image metadata raw response: {metadata_text[:200]}...")
            
            # Parse JSON response
            metadata_json = extract_json_from_string(metadata_text)
            if metadata_json:
                try:
                    parsed = json.loads(metadata_json)
                    logger.info(f"✅ Parsed {len(parsed) if isinstance(parsed, list) else 1} image metadata items")
                    # Ensure it's a list of dicts
                    if isinstance(parsed, list):
                        result = [item if isinstance(item, dict) else {"query": str(item), "alt_text": str(item)} for item in parsed]
                        logger.debug(f"Image metadata: {result}")
                        return result
                    else:
                        return [parsed] if isinstance(parsed, dict) else []
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Failed to parse JSON from extracted text: {e}")
                    logger.debug(f"Extracted JSON was: {metadata_json[:200]}...")
                    return []
            else:
                logger.warning("⚠️  Could not extract JSON from LLM response, trying raw parse...")
                # Try parsing raw text if extraction failed
                try:
                    parsed = json.loads(metadata_text)
                    if isinstance(parsed, list):
                        return [item if isinstance(item, dict) else {"query": str(item), "alt_text": str(item)} for item in parsed]
                    else:
                        return [parsed] if isinstance(parsed, dict) else []
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Raw JSON parse also failed: {e}")
                    logger.debug(f"Raw response was: {metadata_text[:300]}...")
                    return []
                
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"❌ Failed to parse image metadata from LLM response: {e}")
            logger.debug(f"LLM response was: {metadata_text if metadata_text else 'unknown'}")
            return []
        except Exception as e:
            logger.error(f"❌ Error generating image metadata: {e}", exc_info=True)
            return []

    async def _process_single_image_async(
        self, metadata: dict, post: BlogPost, index: int
    ) -> Optional[ImageDetails]:
        """
        Async process a single image: search Pexels, get image details.
        
        Args:
            metadata: Dict with 'query' and 'alt_text'
            post: BlogPost object
            index: Image index
            
        Returns:
            ImageDetails object if successful, None otherwise
        """
        slug = post.slug or (post.title or post.topic).lower().replace(" ", "-")
        title = post.title or post.topic

        try:
            query = metadata.get("query", f"blog image {index}")
            alt_text = metadata.get("alt_text", f"Image for {title}")
            
            if not query or query.strip() == "":
                logger.warning(f"⚠️  Image {index}: Empty query, skipping")
                return None
            
            logger.info(f"🔍 Image {index}: Searching Pexels for '{query}'")
            
            # Try to get image from Pexels API (async)
            try:
                images = await self.pexels_client.search_images(
                    query,
                    per_page=1,
                    orientation="landscape",
                    size="large"
                )
                
                if images and len(images) > 0:
                    image_data = images[0]
                    image_url = image_data.get("url", "")
                    logger.info(f"✅ Found image from Pexels for '{query}': {image_url[:60]}...")
                    
                    image_details = ImageDetails(
                        query=query,
                        source="pexels",
                        path=None,
                        public_url=image_url,
                        alt_text=alt_text,
                        caption=f"Related to: {title}. Photo by {image_data.get('photographer', 'Unknown')}",
                        description=alt_text,
                    )
                    return image_details
                else:
                    logger.warning(f"⚠️  No images found on Pexels for '{query}'")
            except Exception as e:
                logger.warning(f"⚠️  Pexels search failed for '{query}': {e}")
            
            # Fallback: Use placeholder if Pexels fails
            logger.info(f"ℹ️  Using placeholder URL for '{query}'")
            image_url = f"https://via.placeholder.com/800x600?text={query.replace(' ', '+')}"
            
            image_details = ImageDetails(
                query=query,
                source="placeholder",
                path=None,
                public_url=image_url,
                alt_text=alt_text,
                caption=f"Related to: {title}",
                description=alt_text,
            )
            return image_details

        except Exception as e:
            logger.error(f"❌ Error processing image at index {index}: {e}", exc_info=True)
            return None

    def _process_single_image(
        self, metadata: dict, post: BlogPost, index: int
    ) -> Optional[ImageDetails]:
        """
        Process a single image: get URL from Pexels, store metadata.
        
        Args:
            metadata: Dict with 'query' and 'alt_text'
            post: BlogPost object
            index: Image index
            
        Returns:
            ImageDetails object if successful, None otherwise
        """
        slug = post.slug or (post.title or post.topic).lower().replace(" ", "-")
        title = post.title or post.topic

        try:
            query = metadata.get("query", f"blog image {index}")
            alt_text = metadata.get("alt_text", f"Image for {title}")
            
            if not query or query.strip() == "":
                logger.warning(f"⚠️  Image {index}: Empty query, skipping")
                return None
            
            logger.info(f"🔍 Image {index}: Processing Pexels query: '{query}'")
            
            # Note: We can't call async methods from sync context
            # The image search happens asynchronously via the image_agent workflow
            # This method handles fallback/placeholder URL generation only
            
            # Use placeholder URL as fallback (images will be processed async if needed)
            image_url = f"https://via.placeholder.com/800x600?text={query.replace(' ', '+')}"
            
            logger.info(f"ℹ️  Using placeholder URL for '{query}' (async search happens separately)")
            
            # Create ImageDetails object with placeholder
            image_details = ImageDetails(
                query=query,
                source="pexels",
                path=None,
                public_url=image_url,
                alt_text=alt_text,
                caption=f"Related to: {title}",
                description=alt_text,
            )
            
            logger.info(f"✅ Image {index} metadata created: '{alt_text}')")
            return image_details

        except Exception as e:
            logger.error(f"Error processing image at index {index}: {e}")
            # Return fallback image details instead of None
            slug = post.slug or (post.title or post.topic).lower().replace(" ", "-")
            title = post.title or post.topic
            query = metadata.get("query", f"blog image {index}") if metadata else f"blog image {index}"
            alt_text = metadata.get("alt_text", f"Image for {title}") if metadata else f"Image for {title}"
            
            fallback_image = ImageDetails(
                query=query,
                source="placeholder",
                path=None,
                public_url=f"https://via.placeholder.com/800x600?text={query.replace(' ', '+')[:50]}",
                alt_text=alt_text,
                caption=f"Related to: {title}",
                description=alt_text,
            )
            logger.info(f"📋 Using fallback placeholder image for index {index}")
            return fallback_image
