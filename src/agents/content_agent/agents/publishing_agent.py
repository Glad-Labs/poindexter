import logging
import re
from ..services.strapi_client import StrapiClient
from ..utils.data_models import BlogPost
from ..utils.markdown_utils import markdown_to_strapi_blocks


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
        self.tools = CrewAIToolsFactory.get_content_agent_tools()

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
            # 1. Replace image placeholders with markdown image tags
            content_with_images = self._replace_image_placeholders(post)

            # 2. Clean the final content
            final_content = self._clean_content(content_with_images)

            # 3. Convert the final markdown to Strapi's block format
            post.body_content_blocks = markdown_to_strapi_blocks(final_content)

            # 4. Create the post in Strapi
            post_id, post_url = self.strapi_client.create_post(post)
            if not post_id:
                raise Exception("Publishing agent failed to return a Strapi post ID.")

            post.strapi_id = post_id
            post.strapi_url = post_url
            logging.info(
                f"Successfully published post '{post.title}' to Strapi with ID: {post_id}"
            )

        except Exception as e:
            logging.error(
                f"An error occurred during the publishing process: {e}", exc_info=True
            )

        return post

    def _replace_image_placeholders(self, post: BlogPost) -> str:
        """Replaces markdown image placeholders with markdown image tags."""
        content = post.raw_content or ""
        if not post.images:
            return content

        for i, image_data in enumerate(post.images):
            placeholder = f"[IMAGE-{i+1}]"
            if image_data.public_url:
                markdown_image = f"![{image_data.alt_text}]({image_data.public_url})"
                content = content.replace(placeholder, markdown_image)
            else:
                logging.warning(
                    f"Image {i+1} has no public URL. Cannot replace placeholder."
                )
        return content

    def _clean_content(self, content: str) -> str:
        """Removes any leftover generation artifacts from the content."""
        # This regex removes lines that start with '### **Blog Post Draft**' and similar artifacts
        content = re.sub(r"^### \*\*.*\*\*$", "", content, flags=re.MULTILINE)
        # This removes any leading/trailing whitespace and multiple newlines
        return content.strip()
