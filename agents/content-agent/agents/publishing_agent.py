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
        source of truth for all content and metadata and includes the new
        content type relationships (author, category, tags).

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

        # Get author, category, and tag IDs from Strapi
        author_id = self._get_or_create_author("Content Agent", True, "v1.0.0")
        category_id = self._get_category_id(post.topic or "AI Development")
        tag_ids = self._get_tag_ids(post.keywords)

        # Calculate reading time (rough estimate: 200 words per minute)
        word_count = len(post.raw_content.split()) if post.raw_content else 0
        reading_time = max(1, round(word_count / 200))

        # Generate excerpt from content
        excerpt = self._generate_excerpt(post.raw_content)

        # Assemble the data into a StrapiPost Pydantic model for validation.
        strapi_post_data = StrapiPost(
            Title=post.generated_title,
            Slug=slugify(post.generated_title),
            MetaDescription=post.meta_description,
            BodyContent=body_content_blocks,
            FeaturedImage=featured_image_id,
            Keywords=", ".join(post.keywords) if post.keywords else "",
            ReadingTime=reading_time,
            Excerpt=excerpt,
            author=author_id,
            category=category_id,
            tags=tag_ids,
            PostStatus="Draft"  # Add the PostStatus field
        )

        try:
            # The Strapi client handles the actual API call.
            response_data = self.strapi_client.create_post(strapi_post_data)
            
            if response_data and response_data.get('data'):
                post.strapi_post_id = response_data['data']['id']
                # Construct the final URL based on the Strapi response.
                post.strapi_url = f"http://localhost:1337/api/posts/{post.strapi_post_id}"
                post.status = "Published"
                
                # Create content metrics entry
                if post.strapi_post_id:
                    self._create_content_metrics(post.strapi_post_id, post)
                
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

    def _get_or_create_author(self, name: str, is_ai_agent: bool, agent_version: Optional[str] = None) -> Optional[int]:
        """
        Retrieves an existing author by name from Strapi or creates a new one if not found.
        This ensures that content is always attributed to a valid author.
        """
        try:
            # First, try to find existing author
            authors_response = self.strapi_client._make_request('GET', '/authors?filters[Name][$eq]=' + name)
            
            if authors_response and authors_response.get('data'):
                return authors_response['data'][0]['id']
            
            # Create new author if not found
            author_data = {
                "Name": name,
                "IsAIAgent": is_ai_agent,
                "Bio": f"AI-powered content generation agent" if is_ai_agent else "Human author"
            }
            if agent_version:
                author_data["AgentVersion"] = agent_version
                
            response = self.strapi_client._make_request('POST', '/authors', {"data": author_data})
            return response['data']['id'] if response and response.get('data') else None
            
        except Exception as e:
            logging.warning(f"Could not get/create author {name}: {e}")
            return None

    def _get_category_id(self, category_name: str) -> Optional[int]:
        """Get category ID by name from Strapi."""
        try:
            response = self.strapi_client._make_request('GET', f'/categories?filters[Name][$eq]={category_name}')
            if response and response.get('data'):
                return response['data'][0]['id']
            
            # Default to first available category if not found
            response = self.strapi_client._make_request('GET', '/categories?pagination[limit]=1')
            if response and response.get('data'):
                return response['data'][0]['id']
                
        except Exception as e:
            logging.warning(f"Could not get category {category_name}: {e}")
        return None

    def _get_tag_ids(self, keywords: list) -> list[int]:
        """Get tag IDs for the given keywords."""
        if not keywords:
            return []
            
        tag_ids = []
        try:
            for keyword in keywords[:5]:  # Limit to 5 tags
                response = self.strapi_client._make_request('GET', f'/tags?filters[Name][$eq]={keyword}')
                if response and response.get('data'):
                    tag_ids.append(response['data'][0]['id'])
                    
        except Exception as e:
            logging.warning(f"Could not get tags for keywords {keywords}: {e}")
            
        return tag_ids

    def _generate_excerpt(self, content: str, max_length: int = 300) -> str:
        """Generate an excerpt from the content."""
        if not content:
            return ""
            
        # Remove markdown formatting and get first paragraph
        clean_content = content.replace('#', '').replace('*', '').replace('`', '')
        sentences = clean_content.split('.')
        
        excerpt = ""
        for sentence in sentences:
            if len(excerpt + sentence) < max_length:
                excerpt += sentence + "."
            else:
                break
                
        return excerpt.strip()

    def _create_content_metrics(self, post_id: int, post) -> None:
        """Create initial content metrics entry for the post."""
        try:
            metrics_data = {
                "Views": 0,
                "Likes": 0,
                "Shares": 0,
                "Comments": 0,
                "EngagementRate": 0.0,
                "AgentVersion": "v1.0.0",
                "post": post_id
            }
            
            # Add generation time if available
            if hasattr(post, 'generation_time_ms'):
                metrics_data["GenerationTimeMs"] = post.generation_time_ms
                
            self.strapi_client._make_request('POST', '/content-metrics', {"data": metrics_data})
            logging.info(f"Created content metrics for post {post_id}")
            
        except Exception as e:
            logging.warning(f"Could not create content metrics for post {post_id}: {e}")