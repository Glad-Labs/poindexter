import logging
import config  # Add this import
import json
import markdown

from crewai import Agent
from services.wordpress_client import WordPressClient # FIX: Use absolute import
from services.strapi_client import StrapiClient
from utils.data_models import BlogPost, StrapiPost # FIX: Use absolute import
from utils.helpers import slugify

logger = logging.getLogger(__name__)

class PublishingAgent:
    """Handles the final step of publishing the content to WordPress."""
    def __init__(self):
        logging.info("Initializing Publishing Agent...")
        self.wp_client = WordPressClient(
            url=config.WORDPRESS_URL,
            username=config.WORDPRESS_USERNAME,
            password=config.WORDPRESS_PASSWORD
        )

    def publish_post(self, post_data: BlogPost) -> BlogPost:
        """
        Publishes the given blog post data to WordPress.
        Delegates the actual publishing logic to the WordPressClient.
        """
        logging.info(f"PublishingAgent: Attempting to publish '{post_data.topic}'...")
        return self.wp_client.post_article(post_data)

    def run(self, post: BlogPost) -> BlogPost:
        if not post.generated_title or not post.raw_content:
            logging.error("PublishingAgent: Post is missing a title or content. Aborting.")
            post.status = "Error"
            post.rejection_reason = "Missing title or content before publishing."
            return post

        logging.info(f"PublishingAgent: Preparing to publish '{post.generated_title}' to Strapi.")

        # Convert final Markdown to a simple HTML string for the body
        html_content = markdown.markdown(post.raw_content)
        body_content = [{"type": "paragraph", "children": [{"type": "text", "text": html_content}]}]

        # Get the ID of the first image to use as the featured image
        featured_image_id = post.images[0].strapi_image_id if post.images and post.images[0].strapi_image_id else None

        strapi_post = StrapiPost(
            Title=post.generated_title,
            Slug=slugify(post.generated_title),
            BodyContent=body_content,
            Keywords=", ".join(post.related_keywords),
            MetaDescription=post.meta_description,
            FeaturedImage=featured_image_id,
            ImageAltText=post.images[0].alt_text if post.images else None
        )

        response = self.strapi_client.create_post(strapi_post)
        if response and response.get('data'):
            post.strapi_post_id = response['data']['id']
            # Construct a potential frontend URL (adjust if your frontend has a different structure)
            post.strapi_url = f"http://localhost:3000/blog/{response['data']['attributes']['Slug']}"
            logging.info(f"Successfully created draft in Strapi with ID: {post.strapi_post_id}")
        else:
            logging.error("Failed to create post in Strapi.")
            post.status = "Error"
            post.rejection_reason = "Failed to publish to Strapi."

        return post

def create_publishing_agent(tool):
    """
    Creates the Publishing Agent.
    This agent is responsible for creating the draft post in Strapi.
    """
    return Agent(
        role='Digital Publishing Specialist',
        goal='Take the finalized content (headline and summary) and create a new post draft in the Strapi CMS using the provided tool.',
        backstory=(
            "You are a detail-oriented publishing specialist who ensures that content is correctly formatted "
            "and uploaded to the content management system. You are the final gatekeeper before content "
            "goes live, ensuring all fields are correctly populated."
        ),
        tools=[tool], # Assign the tool to the agent
        verbose=True,
        allow_delegation=False
    )