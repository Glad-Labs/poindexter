import logging
from services.strapi_client import StrapiClient
from typing import Optional
from utils.data_models import BlogPost, StrapiPost
from utils.helpers import slugify

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
        Validates, formats, and publishes the final content to Strapi.
        This method now uses the comprehensive BlogPost object as the single
        source of truth for all content and metadata.

        Args:
            post (BlogPost): The central BlogPost object.

        Returns:
            BlogPost: The updated BlogPost object with the Strapi ID and URL.
        """
        if not post.generated_title or not post.raw_content:
            error_message = "Publishing failed: Title or content is missing."
            logging.error(error_message)
            post.status = "Failed"
            post.rejection_reason = error_message
            return post

        logging.info(f"PublishingAgent: Preparing to publish '{post.generated_title}' to Strapi.")

        # Format the raw markdown content into Strapi's rich text block structure.
        body_content_blocks = self._format_body_content_for_strapi(post.raw_content)

        # Get the Strapi ID for the featured image (the first image in the list).
        featured_image_id = post.images[0].strapi_image_id if post.images and post.images[0].strapi_image_id else None

        # Assemble the data into a StrapiPost Pydantic model for validation.
        strapi_post_data = StrapiPost(
            Title=post.generated_title,
            Slug=slugify(post.generated_title),
            MetaDescription=post.meta_description,
            BodyContent=body_content_blocks,
            FeaturedImage=featured_image_id,
            Keywords=", ".join(post.keywords) # Use the 'keywords' field
        )

        try:
            # The Strapi client handles the actual API call.
            response_data = self.strapi_client.create_post(strapi_post_data)
            
            if response_data and response_data.get('data'):
                post.strapi_post_id = response_data['data']['id']
                # Construct the final URL based on the Strapi response.
                # Recommendation: Make the base URL configurable.
                post.strapi_url = f"http://localhost:1337/api/posts/{post.strapi_post_id}"
                post.status = "Published"
                logging.info(f"Successfully published post '{post.generated_title}' with ID: {post.strapi_post_id}")
            else:
                raise Exception("Publishing failed: Strapi client returned no data or an invalid response.")

        except Exception as e:
            error_message = f"An exception occurred during publishing: {e}"
            logging.error(error_message, exc_info=True)
            post.status = "Failed"
            post.rejection_reason = error_message
            # Do not re-raise; allow the orchestrator to handle logging and status updates.
        
        return post

    def _format_body_content_for_strapi(self, body_content: str) -> list[dict]:
        """
        Converts a markdown string into Strapi's rich text block format.
        This implementation sends the entire markdown content as a single block,
        relying on the frontend to render it correctly. This is a simple and
        robust method.
        """
        if not body_content:
            return []
            
        # This structure is the standard for Strapi's Rich Text field.
        # It wraps the entire markdown content in a single paragraph block.
        return [
            {
                "type": "paragraph",
                "children": [{"type": "text", "text": body_content}]
            }
        ]