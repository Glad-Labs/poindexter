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
        The main method for the ImageAgent. It orchestrates the entire process
        of generating image ideas, fetching images, uploading them, and updating
        the blog post content with the final image URLs.

        Args:
            post (BlogPost): The central BlogPost object containing all data.

        Returns:
            BlogPost: The updated BlogPost object with image details and modified content.
        """
        if not post.generated_title or not post.raw_content:
            logging.warning("ImageAgent: Post is missing a title or content. Skipping image processing.")
            return post

        logging.info(f"ImageAgent: Starting image processing for '{post.generated_title}'.")

        # 1. Generate Image Ideas (Metadata)
        # This step uses an LLM to brainstorm relevant images based on the content.
        self._generate_image_metadata(post)
        if not post.images:
            logging.warning("No image metadata was generated. Skipping image fetching.")
            return post

        # 2. Process Each Image Idea
        # This loop fetches, downloads, and uploads each image.
        self._process_images(post)

        # 3. Replace Placeholders in Content
        # The final step is to replace the `[IMAGE-n]` placeholders in the
        # markdown content with the actual image tags.
        self._replace_content_placeholders(post)

        logging.info(f"ImageAgent: Finished image processing for '{post.generated_title}'.")
        return post

    def _generate_image_metadata(self, post: BlogPost):
        """
        Calls the LLM to generate a list of search queries, alt texts, and captions
        for the images that will be placed in the blog post.
        """
        logging.info("Generating image metadata...")
        # Ensure content exists before trying to count placeholders
        num_images = post.raw_content.count("[IMAGE-") if post.raw_content else 0
        if num_images == 0:
            logging.warning("No [IMAGE-n] placeholders found in content. Skipping metadata generation.")
            return

        metadata_prompt = self.prompts['image_metadata_generation'].format(
            title=post.generated_title,
            content=post.raw_content, # Provide full content for better context
            num_images=num_images
        )
        metadata_text = self.llm_client.generate_text_content(metadata_prompt)
        
        json_string = extract_json_from_string(metadata_text)
        if not json_string:
            logging.error("Failed to extract JSON metadata from LLM response.")
            return

        try:
            image_metadata_list = json.loads(json_string)
            for metadata in image_metadata_list:
                post.images.append(ImageDetails(**metadata))
            logging.info(f"Successfully generated metadata for {len(post.images)} images.")
        except (json.JSONDecodeError, TypeError) as e:
            logging.error(f"Failed to decode or process image metadata JSON: {e}")

    def _process_images(self, post: BlogPost):
        """
        Iterates through the generated image metadata, searches for each image,
        downloads it, and uploads it to both GCS and Strapi.
        """
        if not post.generated_title: return

        slug_title = slugify(post.generated_title)
        for i, image_details in enumerate(post.images):
            if not image_details.query:
                logging.warning(f"Image {i+1} is missing a search query. Skipping.")
                continue

            logging.info(f"Processing image {i+1}: '{image_details.query}'")
            local_path = os.path.join(config.IMAGE_STORAGE_PATH, f"{slug_title}-{i}.jpg")
            
            # Search and download from Pexels
            if self.pexels_client.search_and_download(image_details.query, local_path):
                image_details.source = "pexels"
                
                # Upload to GCS for long-term storage and direct linking in content
                gcs_path = f"images/{os.path.basename(local_path)}"
                public_url = self.gcs_client.upload_file(local_path, gcs_path)
                if public_url:
                    image_details.public_url = public_url
                    image_details.path = gcs_path
                    logging.info(f"Uploaded image to GCS: {public_url}")

                # Upload to Strapi Media Library to get an ID.
                # This is crucial for assigning a featured image or using Strapi's API.
                alt_text = image_details.alt_text or f"Image for {post.generated_title}"
                caption = image_details.caption or post.generated_title
                strapi_id = self.strapi_client.upload_image(local_path, alt_text, caption)
                if strapi_id:
                    image_details.strapi_image_id = strapi_id
                    logging.info(f"Uploaded image to Strapi with ID: {strapi_id}")

    def _replace_content_placeholders(self, post: BlogPost):
        """
        Replaces all `[IMAGE-n]` placeholders in the post's raw_content
        with the corresponding Markdown image tag.
        """
        if not post.raw_content: return

        logging.info("Replacing image placeholders in content...")
        for i, image in enumerate(post.images):
            if image.public_url:
                placeholder = f"[IMAGE-{i+1}]"
                alt_text = image.alt_text or "Blog post image"
                # Create a clean markdown tag without a caption, as captions are often
                # handled by the frontend or CMS styling.
                markdown_tag = f"![{alt_text}]({image.public_url})"
                post.raw_content = post.raw_content.replace(placeholder, markdown_tag)
        logging.info("Finished replacing placeholders.")
