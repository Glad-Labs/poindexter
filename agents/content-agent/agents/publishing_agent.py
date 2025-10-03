import logging
from services.strapi_client import StrapiClient
from typing import Optional

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

    def run(self, final_content: dict) -> Optional[int]:
        """
        Validates and publishes the final content to Strapi.
        Returns the post ID on success, or None on failure.
        """
        title = final_content.get('title')
        slug = final_content.get('slug')
        meta_description = final_content.get('meta_description')
        body_content_raw = final_content.get('body_content')
        featured_image_id = final_content.get('featured_image_id')

        logging.info(f"PublishingAgent: Preparing to publish '{title}' to Strapi.")

        # --- Defensive Validation ---
        required_fields = {
            'title': title,
            'slug': slug,
            'meta_description': meta_description,
            'body_content': body_content_raw
        }
        missing_fields = [key for key, value in required_fields.items() if not value]

        if missing_fields:
            error_message = f"Publishing failed. AI failed to generate required fields: {', '.join(missing_fields)}"
            logging.error(error_message)
            raise ValueError(error_message)
        
        # Assert to satisfy the static type checker after validation
        assert isinstance(body_content_raw, str)
        # --- End Validation ---

        body_content_blocks = self._format_body_content_for_strapi(body_content_raw)

        # Assemble the data into a single dictionary for the Strapi client
        post_data = {
            "Title": title,
            "Slug": slug,
            "MetaDescription": meta_description,
            "BodyContent": body_content_blocks,
            "FeaturedImage": featured_image_id
        }

        try:
            post_id = self.strapi_client.create_post(post_data)
            if post_id:
                logging.info(f"Successfully published post '{title}' with ID: {post_id}")
                return post_id
            else:
                logging.error("Publishing agent received no post ID from Strapi client.")
                return None
        except Exception as e:
            logging.error(f"An exception occurred during publishing: {e}")
            # Re-raise the exception to be caught by the orchestrator
            raise

    def _format_body_content_for_strapi(self, body_content: str) -> list:
        """
        Converts a markdown string into a simple Strapi rich text block format.
        This approach sends the entire markdown content as a single paragraph block.
        The frontend will then be responsible for rendering the markdown.
        """
        if not body_content:
            return []
            
        # Create a single paragraph block containing the entire markdown text
        # This is a robust way to ensure the content is accepted by Strapi,
        # and the frontend is already set up to render markdown.
        return [
            {
                "type": "paragraph",
                "children": [{"type": "text", "text": body_content}]
            }
        ]