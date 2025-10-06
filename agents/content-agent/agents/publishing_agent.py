import logging
from services.strapi_client import StrapiClient
from utils.data_models import BlogPost
from utils.markdown_utils import markdown_to_strapi_blocks

class PublishingAgent:
    """Handles the final step of formatting and publishing the content to Strapi."""

    def __init__(self, strapi_client: StrapiClient):
        """
        Initializes the PublishingAgent with a StrapiClient.

        Args:
            strapi_client: An instance of the StrapiClient service.
        """
        logging.info("Initializing Publishing Agent...")
        self.strapi_client = strapi_client

    def run(self, post: BlogPost) -> BlogPost:
        """
        Processes the final content and publishes it to Strapi.

        Args:
            post (BlogPost): The central BlogPost object.

        Returns:
            BlogPost: The updated BlogPost object with the Strapi ID and URL.
        """
        logging.info(f"PublishingAgent: Preparing to publish '{post.title}' to Strapi.")

        try:
            # 1. Replace image placeholders with actual image data
            final_content = self._replace_image_placeholders(post)

            # 2. Convert the final markdown to Strapi's block format
            post.body_content_blocks = markdown_to_strapi_blocks(final_content)

            # 3. Create the post in Strapi
            post_id, post_url = self.strapi_client.create_post(post)
            if not post_id:
                raise Exception("Failed to create post in Strapi.")

            post.strapi_id = post_id
            post.strapi_url = post_url
            logging.info(f"Successfully published post '{post.title}' to Strapi with ID: {post_id}")

        except Exception as e:
            logging.error(f"An error occurred during the publishing process: {e}", exc_info=True)
            # We return the post object even if publishing fails so the orchestrator can log the error
        
        return post

    def _replace_image_placeholders(self, post: BlogPost) -> str:
        """Replaces markdown image placeholders with Strapi image blocks."""
        content = post.raw_content or ""
        if not post.images:
            return content

        for i, image_data in enumerate(post.images):
            placeholder = f"[IMAGE-{i+1}]"
            
            if image_data.strapi_image_id:
                strapi_image_url = image_data.public_url
                alt_text = image_data.alt_text or "image"
                
                # Create a markdown image tag
                markdown_image = f"![{alt_text}]({strapi_image_url})"
                content = content.replace(placeholder, markdown_image)
            else:
                logging.warning(f"Image {i+1} has no Strapi ID. Cannot replace placeholder.")

        return content