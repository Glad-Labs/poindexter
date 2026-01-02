import logging
import os
import json
import httpx
from ..config import config
from ..services.llm_client import LLMClient
from ..services.pexels_client import PexelsClient
from ..services.strapi_client import StrapiClient
from ..utils.data_models import BlogPost, ImageDetails
from ..utils.helpers import load_prompts_from_file, slugify, extract_json_from_string


logger = logging.getLogger(__name__)


class ImageAgent:
    """
    Generates and/or fetches images for a blog post and uploads them to GCS.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        pexels_client: PexelsClient,
        strapi_client: StrapiClient,
        api_url: str = "http://localhost:8000",
    ):
        logging.info("Initializing Image Agent (REST API mode - no GCS)...")
        self.llm_client = llm_client
        self.pexels_client = pexels_client
        self.strapi_client = strapi_client
        self.tools = CrewAIToolsFactory.get_content_agent_tools()
        self.api_url = api_url
        self.prompts = load_prompts_from_file(config.PROMPTS_PATH)
        os.makedirs(config.IMAGE_STORAGE_PATH, exist_ok=True)

    async def run(self, post: BlogPost) -> BlogPost:
        """
        Generates image metadata, downloads images, uploads them to GCS,
        and then uploads them to Strapi (async).
        """
        if not post.title or not post.raw_content:
            logging.warning(
                "ImageAgent: Post title or content is missing, skipping image processing."
            )
            return post

        logging.info(f"ImageAgent: Starting image processing for '{post.title}'.")

        try:
            # Generate metadata for all images first
            image_metadata = await self._generate_image_metadata(post)
            if not image_metadata:
                logging.warning("No image metadata was generated.")
                return post

            # Process each image
            for i, meta in enumerate(image_metadata):
                image_details = await self._process_single_image(meta, post, i)
                if image_details:
                    if post.images is None:
                        post.images = []
                    post.images.append(image_details)

        except Exception as e:
            logging.error(
                f"An error occurred during image processing: {e}", exc_info=True
            )

        logging.info(f"ImageAgent: Finished image processing for '{post.title}'.")
        return post

    async def _generate_image_metadata(self, post: BlogPost) -> list[dict[str, str]]:
        """Generates a list of image metadata dicts using the LLM (async)."""
        metadata_prompt = self.prompts["image_metadata_generation"].format(
            title=post.title, num_images=config.DEFAULT_IMAGE_PLACEHOLDERS
        )
        logging.info("Generating image metadata...")
        try:
            metadata_text = await self.llm_client.generate_text(metadata_prompt)
            # The response is expected to be a JSON list of objects
            metadata_json = extract_json_from_string(metadata_text)
            if metadata_json:
                return json.loads(metadata_json)
            else:
                # Fallback: try parsing the raw text if extract failed (e.g. no markdown)
                return json.loads(metadata_text)
        except (json.JSONDecodeError, TypeError) as e:
            logging.error(f"Failed to parse image metadata from LLM response: {e}")
            return []

    async def _process_single_image(
        self, metadata: dict[str, str], post: BlogPost, index: int
    ) -> ImageDetails | None:
        """Downloads, uploads, and processes a single image (async)."""
        if not post.title:
            return None

        try:
            query = metadata.get("query")
            if not query:
                logging.warning(f"No query found in image metadata at index {index}.")
                return None

            slug_title = slugify(post.title)
            local_filename = f"{slug_title}-{index}.jpg"
            local_path = os.path.join(config.IMAGE_STORAGE_PATH, local_filename)

            # Download the image
            if not await self.pexels_client.search_and_download(query, local_path):
                logging.warning(f"Failed to download image for query: {query}")
                return None

            # Upload image via REST API (GCS deprecated, using REST API instead)
            try:
                with open(local_path, 'rb') as f:
                    file_content = f.read()
                files = {'file': (os.path.basename(local_path), file_content, 'image/jpeg')}
                async with httpx.AsyncClient(timeout=30) as client:
                    upload_response = await client.post(
                        f"{self.api_url}/api/upload",
                        files=files,
                    )
                    upload_response.raise_for_status()
                    result = upload_response.json()
                    signed_url = result.get('url', local_path)
            except Exception as e:
                logging.warning(f"REST API upload failed ({e}), using local path")
                signed_url = local_path

            # Create ImageDetails object
            image_details = ImageDetails(
                query=query,
                path=local_path,
                public_url=signed_url,
                alt_text=metadata.get("alt_text", f"Image for {post.title}"),
                caption=metadata.get("caption", post.title),
            )

            # Upload to Strapi and get the ID
            alt_text = image_details.alt_text or f"Image for {post.title}"
            caption = image_details.caption or post.title
            strapi_id = await self.strapi_client.upload_image(local_path, alt_text, caption)
            if strapi_id:
                image_details.strapi_image_id = strapi_id

            return image_details

        except Exception as e:
            logging.error(
                f"Error processing image for query '{metadata.get('query')}': {e}",
                exc_info=True,
            )
            return None
