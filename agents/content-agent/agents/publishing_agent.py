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
        Converts a markdown string into Strapi's rich text block format using markdown-it-py.
        """
        md = MarkdownIt()
        tokens = md.parse(markdown_text)
        
        blocks = []
        current_list = None

        for token in tokens:
            if token.type == 'heading_open':
                level = int(token.tag[1])
                blocks.append({"type": "heading", "level": level, "children": []})
            elif token.type == 'paragraph_open':
                blocks.append({"type": "paragraph", "children": []})
            elif token.type == 'bullet_list_open':
                current_list = {"type": "list", "format": "unordered", "children": []}
            elif token.type == 'list_item_open':
                if current_list:
                    current_list["children"].append({"type": "list-item", "children": []})
            elif token.type == 'inline':
                # This is the content within a block
                if blocks and blocks[-1]["children"] is not None:
                    # Handle inline content for headings and paragraphs
                    if blocks[-1]['type'] in ['heading', 'paragraph']:
                         blocks[-1]["children"].append({"type": "text", "text": token.content})
                elif current_list and current_list["children"]:
                     # Handle inline content for list items
                    current_list["children"][-1]["children"].append({"type": "text", "text": token.content})

            elif token.type == 'bullet_list_close':
                if current_list:
                    blocks.append(current_list)
                    current_list = None
        
        return blocks