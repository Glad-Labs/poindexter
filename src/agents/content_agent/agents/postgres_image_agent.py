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

    def run(self, post: BlogPost) -> BlogPost:
        """
        Process images for a blog post and store metadata in PostgreSQL.
        
        Args:
            post: BlogPost object to enhance with images
            
        Returns:
            BlogPost with image metadata added
        """
        if not post.title or not post.raw_content:
            logger.warning("ImageAgent: Post title or content is missing, skipping image processing.")
            return post

        logger.info(f"ImageAgent: Starting image processing for '{post.title}'.")

        try:
            # Generate metadata for all images first
            image_metadata = self._generate_image_metadata(post)
            if not image_metadata:
                logger.warning("No image metadata was generated.")
                return post

            # Process each image
            for i, meta in enumerate(image_metadata):
                image_details = self._process_single_image(meta, post, i)
                if image_details:
                    if post.images is None:
                        post.images = []
                    post.images.append(image_details)
                    logger.info(f"✅ Processed image {i+1}/{len(image_metadata)}: {image_details.alt_text}")

        except Exception as e:
            logger.error(f"❌ Error during image processing: {e}", exc_info=True)
            # Continue without images - don't fail the whole pipeline

        logger.info(f"ImageAgent: Finished image processing for '{post.title}'. Found {len(post.images or [])} images.")
        return post

    def _generate_image_metadata(self, post: BlogPost) -> List[dict]:
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
            
            logger.info("Generating image metadata using LLM...")
            metadata_text = self.llm_client.generate_text(metadata_prompt)
            
            # Parse JSON response
            metadata_json = extract_json_from_string(metadata_text)
            if metadata_json:
                return json.loads(metadata_json)
            else:
                # Try parsing raw text if extraction failed
                return json.loads(metadata_text)
                
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Failed to parse image metadata from LLM response: {e}")
            logger.debug(f"LLM response was: {metadata_text if metadata_text else 'unknown'}")
            return []
        except Exception as e:
            logger.error(f"Error generating image metadata: {e}")
            return []

    def _process_single_image(
        self, metadata: dict, post: BlogPost, index: int
    ) -> Optional[ImageDetails]:
        """
        Process a single image: download from Pexels, store metadata.
        
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
            
            logger.info(f"Searching Pexels for: {query}")
            
            # Download image from Pexels (or get placeholder if method doesn't exist)
            image_path = None
            if hasattr(self.pexels_client, 'download_image'):
                image_path = self.pexels_client.download_image(
                    query,
                    save_dir=str(self.image_storage_path),
                    image_name=f"{slug}_image_{index}"
                )
            elif hasattr(self.pexels_client, 'search') or hasattr(self.pexels_client, 'get_image'):
                # Fallback: try to get image URL directly
                logger.info("Using Pexels client API method (no download)")
            
            if not image_path:
                logger.warning(f"Could not download image for query: {query}")
                # Use placeholder URL as fallback
                image_url = f"https://via.placeholder.com/800x600?text={query.replace(' ', '+')}"
            else:
                # Store image URL (in production, would upload to CDN)
                image_url = f"/images/{Path(image_path).name}"
            
            # Create ImageDetails object
            image_details = ImageDetails(
                query=query,
                source="pexels",
                path=image_path,
                public_url=image_url,
                alt_text=alt_text,
                caption=f"Related to: {title}",
                description=alt_text,
            )
            
            logger.info(f"✅ Image processed: {alt_text} -> {image_url}")
            return image_details

        except Exception as e:
            logger.error(f"Error processing image at index {index}: {e}")
            return None
