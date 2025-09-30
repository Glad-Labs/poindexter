import logging
import config  # Add this import
import json

from crewai import Agent
from services.wordpress_client import WordPressClient # FIX: Use absolute import
from services.strapi_client import StrapiClient
from utils.data_models import BlogPost, StrapiPost # FIX: Use absolute import

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