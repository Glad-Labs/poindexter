import logging
import os
import json
from config import config
from services.llm_client import LLMClient
from services.pexels_client import PexelsClient
from services.gcs_client import GCSClient
from services.strapi_client import StrapiClient
from utils.data_models import BlogPost, ImageDetails
from utils.helpers import load_prompts_from_file, slugify, extract_json_from_string


logger = logging.getLogger(__name__)


class ImageAgent:
    """
    Generates and/or fetches images for a blog post and uploads them to GCS.
    """

    def __init__(self, llm_client: LLMClient, pexels_client: PexelsClient, gcs_client: GCSClient, strapi_client: StrapiClient):
        logging.info("Initializing Image Agent...")
        self.llm_client = llm_client
        self.pexels_client = pexels_client
        self.gcs_client = gcs_client
        self.strapi_client = strapi_client
        self.prompts = load_prompts_from_file(config.PROMPTS_PATH)
        os.makedirs(config.IMAGE_STORAGE_PATH, exist_ok=True)

    def run(self, post: BlogPost) -> BlogPost:
        """
        Generates image metadata, downloads images, uploads them to GCS,
        and then uploads them to Strapi.
        """
        if not post.title or not post.raw_content:
            logging.warning("ImageAgent: Post title or content is missing, skipping image processing.")
            return post

        logging.info(f"ImageAgent: Starting image processing for '{post.title}'.")
        
        try:
            # Generate metadata for all images first
            image_metadata = self._generate_image_metadata(post)
            if not image_metadata:
                logging.warning("No image metadata was generated.")
                return post

            # Process each image
            for i, meta in enumerate(image_metadata):
                image_details = self._process_single_image(meta, post, i)
                if image_details:
                    post.images.append(image_details)

        except Exception as e:
            logging.error(f"An error occurred during image processing: {e}", exc_info=True)

        logging.info(f"ImageAgent: Finished image processing for '{post.title}'.")
        return post

    def _generate_image_metadata(self, post: BlogPost) -> list[dict[str, str]]:
        """Generates a list of image metadata dicts using the LLM."""
        metadata_prompt = self.prompts['generate_image_metadata'].format(
            title=post.title,
            content=post.raw_content
        )
        logging.info("Generating image metadata...")
        try:
            metadata_text = self.llm_client.generate_text(metadata_prompt)
            # The response is expected to be a JSON list of objects
            return json.loads(metadata_text)
        except (json.JSONDecodeError, TypeError) as e:
            logging.error(f"Failed to parse image metadata from LLM response: {e}")
            return []

    def _process_single_image(self, metadata: dict[str, str], post: BlogPost, index: int) -> ImageDetails | None:
        """Downloads, uploads, and processes a single image."""
        if not post.title: return None

        try:
            query = metadata.get("query")
            if not query:
                logging.warning(f"No query found in image metadata at index {index}.")
                return None

            slug_title = slugify(post.title)
            local_filename = f"{slug_title}-{index}.jpg"
            local_path = os.path.join(config.IMAGE_STORAGE_PATH, local_filename)

            # Download the image
            if not self.pexels_client.search_and_download(query, local_path):
                logging.warning(f"Failed to download image for query: {query}")
                return None
            
            # Upload to GCS and get signed URL
            gcs_path = f"images/{local_filename}"
            signed_url = self.gcs_client.upload_file(local_path, gcs_path)
            if not signed_url:
                logging.error("Failed to upload to GCS or get signed URL.")
                return None

            # Create ImageDetails object
            image_details = ImageDetails(
                query=query,
                path=local_path,
                public_url=signed_url,
                alt_text=metadata.get("alt_text", f"Image for {post.title}"),
                caption=metadata.get("caption", post.title)
            )

            # Upload to Strapi and get the ID
            alt_text = image_details.alt_text or f"Image for {post.title}"
            caption = image_details.caption or post.title
            strapi_id = self.strapi_client.upload_image(local_path, alt_text, caption)
            if strapi_id:
                image_details.strapi_image_id = strapi_id
            
            return image_details

        except Exception as e:
            logging.error(f"Error processing image for query '{metadata.get('query')}': {e}", exc_info=True)
            return None
