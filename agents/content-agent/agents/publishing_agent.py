import logging
from markdown_it import MarkdownIt
from mdit_py_plugins.front_matter import front_matter_plugin

from services.strapi_client import StrapiClient
from utils.data_models import BlogPost, StrapiPost
from utils.helpers import slugify

logger = logging.getLogger(__name__)

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
        Takes a completed BlogPost object, formats it for Strapi,
        and uses the StrapiClient to create a new draft post.

        Args:
            post: The BlogPost object containing the final content.

        Returns:
            The updated BlogPost object with Strapi ID and URL if successful.
        """
        if not post.generated_title or not post.raw_content:
            logging.error("PublishingAgent: Post is missing a title or content. Aborting.")
            post.status = "Error"
            post.rejection_reason = "Missing title or content before publishing."
            return post

        logging.info(f"PublishingAgent: Preparing to publish '{post.generated_title}' to Strapi.")

        # Use a robust Markdown parser to convert content to Strapi's block format
        body_content = self._markdown_to_strapi_blocks(post.raw_content)

        # Get the ID of the first image to use as the featured image
        featured_image_id = post.images[0].strapi_image_id if post.images and post.images[0].strapi_image_id else None

        # Create the final post object that matches the Strapi content type
        strapi_post = StrapiPost(
            Title=post.generated_title,
            Slug=slugify(post.generated_title),
            BodyContent=body_content,
            Keywords=", ".join(post.related_keywords),
            MetaDescription=post.meta_description,
            FeaturedImage=featured_image_id,
            ImageAltText=post.image_alt_text,
            PostStatus="draft",
            Author="AI Content Agent"
        )

        response = self.strapi_client.create_post(strapi_post)
        if response and response.get('data'):
            post.strapi_post_id = response['data']['id']
            # Construct a potential frontend URL (adjust if your frontend has a different structure)
            post.strapi_url = f"http://localhost:3000/blog/{response['data']['attributes']['Slug']}"
            post.status = "Published"
            logging.info(f"Successfully created draft in Strapi with ID: {post.strapi_post_id}")
        else:
            logging.error("Failed to create post in Strapi.")
            post.status = "Error"
            post.rejection_reason = "Failed to publish to Strapi."

        return post

    def _markdown_to_strapi_blocks(self, markdown_text: str) -> list[dict]:
        """
        Converts a markdown string into a simple Strapi rich text block format.
        This approach sends the entire markdown content as a single paragraph block.
        The frontend will then be responsible for rendering the markdown.
        """
        if not markdown_text:
            return []
            
        # Create a single paragraph block containing the entire markdown text
        # This is a robust way to ensure the content is accepted by Strapi,
        # and the frontend is already set up to render markdown.
        return [
            {
                "type": "paragraph",
                "children": [{"type": "text", "text": markdown_text}]
            }
        ]