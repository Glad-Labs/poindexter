import json
from crewai_tools import BaseTool
from services.strapi_client import StrapiClient
from utils.data_models import StrapiPost

class StrapiPublishTool(BaseTool):
    name: str = "Strapi Publisher"
    description: str = "Creates a new draft post in the Strapi CMS from a JSON object containing a headline and summary."

    def _run(self, argument: str) -> str:
        """
        The main execution method for the tool.
        """
        try:
            strapi_client = StrapiClient()
            
            # Parse the JSON output from the previous task
            content_data = json.loads(argument)
            headline = content_data.get('headline')
            summary = content_data.get('refined_summary')

            if not headline or not summary:
                return "Error: The provided content is missing a headline or summary."

            # Create a slug from the headline
            slug = headline.lower().replace(' ', '-').replace(':', '').replace('"', '')

            # Structure the body content for Strapi's Rich Text editor
            body_content = [{
                "type": "paragraph",
                "children": [{"type": "text", "text": summary}]
            }]

            # Create the post object using the Pydantic model for validation
            post_to_create = StrapiPost(
                Title=headline,
                Slug=slug,
                BodyContent=body_content,
                Author="Content Agent v1"
            )

            # Call the Strapi client to create the post
            response = strapi_client.create_post(post_to_create)

            if response:
                post_id = response.get('data', {}).get('id')
                return f"Successfully created draft post in Strapi with ID: {post_id}"
            else:
                return "Error: Failed to create post in Strapi."

        except json.JSONDecodeError:
            return "Error: Invalid JSON format in the input content."
        except Exception as e:
            return f"An unexpected error occurred: {e}"