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
        Validates and publishes the final content to Strapi.
        Returns the updated BlogPost object with the Strapi ID and URL.
        """
        if not post.generated_title or not post.raw_content:
            error_message = "Publishing failed. AI failed to generate title or content."
            logging.error(error_message)
            post.status = "Error"
            post.rejection_reason = error_message
            return post

        logging.info(f"PublishingAgent: Preparing to publish '{post.generated_title}' to Strapi.")

        body_content_blocks = self._format_body_content_for_strapi(post.raw_content)

        # Assemble the data into a StrapiPost Pydantic model
        strapi_post_data = StrapiPost(
            Title=post.generated_title,
            Slug=slugify(post.generated_title),
            MetaDescription=post.meta_description,
            BodyContent=body_content_blocks,
            FeaturedImage=post.images[0].strapi_image_id if post.images else None,
            Keywords=", ".join(post.related_keywords)
        )

        try:
            response_data = self.strapi_client.create_post(strapi_post_data)
            if response_data and response_data.get('data'):
                post.strapi_post_id = response_data['data']['id']
                post.strapi_url = f"http://localhost:3001/posts/{response_data['data']['attributes']['Slug']}"
                post.status = "Published"
                logging.info(f"Successfully published post '{post.generated_title}' with ID: {post.strapi_post_id}")
            else:
                post.status = "Error"
                post.rejection_reason = "Publishing agent received no post ID from Strapi client."
                logging.error(post.rejection_reason)
        except Exception as e:
            logging.error(f"An exception occurred during publishing: {e}")
            post.status = "Error"
            post.rejection_reason = str(e)
            # Do not re-raise, allow the orchestrator to handle logging/status updates
        
        return post

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