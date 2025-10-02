import logging
import markdown

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

        # Convert Markdown to Strapi's Rich Text "blocks" format
        body_content = self._markdown_to_strapi_blocks(post.raw_content)

        # Get the ID of the first image to use as the featured image
        featured_image_id = post.images[0].strapi_image_id if post.images and post.images[0].strapi_image_id else None

        strapi_post = StrapiPost(
            Title=post.generated_title,
            Slug=slugify(post.generated_title),
            BodyContent=body_content,
            Keywords=", ".join(post.related_keywords),
            MetaDescription=post.meta_description,
            FeaturedImage=featured_image_id,
            ImageAltText=post.images[0].alt_text if post.images else None,
            PostStatus="draft" # Explicitly set the post status in Strapi
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
        A more sophisticated converter from Markdown to Strapi's Rich Text format.
        This handles paragraphs, headings, and lists.
        """
        blocks = []
        for line in markdown_text.split('\\n'):
            line = line.strip()
            if not line:
                continue
            
            # Headings
            if line.startswith('#'):
                level = len(line.split(' ')[0])
                content = line.lstrip('# ').strip()
                blocks.append({
                    "type": "heading",
                    "level": level,
                    "children": [{"type": "text", "text": content}]
                })
            # Unordered Lists
            elif line.startswith(('* ', '- ')):
                content = line.lstrip('* ').lstrip('- ').strip()
                # If the previous block was a list, add to it
                if blocks and blocks[-1]["type"] == "list" and blocks[-1]["format"] == "unordered":
                    blocks[-1]["children"].append({
                        "type": "list-item",
                        "children": [{"type": "text", "text": content}]
                    })
                else: # Otherwise, create a new list
                    blocks.append({
                        "type": "list",
                        "format": "unordered",
                        "children": [{
                            "type": "list-item",
                            "children": [{"type": "text", "text": content}]
                        }]
                    })
            # Paragraphs
            else:
                blocks.append({
                    "type": "paragraph",
                    "children": [{"type": "text", "text": line}]
                })
        return blocks