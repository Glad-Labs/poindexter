import logging
import os
import json
from config import config
from services.llm_client import LLMClient
from services.pexels_client import PexelsClient
from services.gcs_client import GCSClient
from services.strapi_client import StrapiClient  # Import StrapiClient
from utils.data_models import BlogPost, ImageDetails
from utils.helpers import load_prompts_from_file, slugify


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
        self.strapi_client = strapi_client  # Add StrapiClient
        self.prompts = load_prompts_from_file(config.PROMPTS_PATH)
        os.makedirs(config.IMAGE_STORAGE_PATH, exist_ok=True)

    def run(self, post: BlogPost) -> BlogPost:
        if not post.generated_title:
            logging.warning("ImageAgent: Post is missing a generated title. Skipping image processing.")
            return post

        logging.info(f"ImageAgent: Starting image processing for '{post.generated_title}'.")

        # 1. Generate Image Metadata
        metadata_prompt = self.prompts['image_metadata_generation'].format(
            title=post.generated_title,
            num_images=config.DEFAULT_IMAGE_PLACEHOLDERS
        )
        metadata_text = self.llm_client.generate_text_content(metadata_prompt)
        
        try:
            image_metadata_list = json.loads(metadata_text)
            for metadata in image_metadata_list:
                post.images.append(ImageDetails(**metadata))
        except json.JSONDecodeError:
            logging.error("Failed to parse image metadata from LLM response.")
            return post

        # 2. Search, Download, Upload to GCS, and Upload to Strapi
        for i, image in enumerate(post.images):
            if not image.query:
                continue
            
            slug_title = slugify(post.generated_title)
            local_path = os.path.join(config.IMAGE_STORAGE_PATH, f"{slug_title}-{i}.jpg")
            
            if self.pexels_client.search_and_download(image.query, local_path):
                # Upload to GCS for embedding in content
                gcs_path = f"images/{slug_title}-{i}.jpg"
                image.public_url = self.gcs_client.upload_file(local_path, gcs_path)
                image.path = gcs_path

                # Upload to Strapi to get an ID for the featured image
                if i == 0: # Assume the first image is the featured one
                    alt_text = image.alt_text or "Image for " + post.generated_title
                    caption = image.caption or post.generated_title
                    image.strapi_image_id = self.strapi_client.upload_image(local_path, alt_text, caption)

        # 3. Replace Placeholders in Content
        if post.raw_content:
            for i, image in enumerate(post.images):
                if image.public_url:
                    placeholder = f"[IMAGE-{i+1}]"
                    alt_text = image.alt_text or "Blog post image"
                    caption = image.caption or ""
                    markdown_tag = f"![{alt_text}]({image.public_url})\n*Caption: {caption}*"
                    post.raw_content = post.raw_content.replace(placeholder, markdown_tag)

        logging.info(f"ImageAgent: Finished image processing for '{post.generated_title}'.")
        return post
