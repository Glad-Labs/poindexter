import requests
import json
from config import config
from utils.data_models import StrapiPost
from typing import Optional

class StrapiClient:
    """
    A client for interacting with the Strapi CMS API.
    """
    def __init__(self):
        """
        Initializes the Strapi client with the API URL and token from the config.
        """
        self.api_url = config.STRAPI_API_URL
        self.api_token = config.STRAPI_API_TOKEN
        if not self.api_url or not self.api_token:
            raise ValueError("STRAPI_API_URL and STRAPI_API_TOKEN must be set in the environment.")
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }
        print("Strapi client initialized.")

    def create_post(self, post_data: StrapiPost) -> Optional[dict]:
        """
        Creates a new post in Strapi as a draft.

        Args:
            post_data (StrapiPost): A Pydantic model instance of the post.

        Returns:
            Optional[dict]: The JSON response from the Strapi API, or None on failure.
        """
        posts_url = f"{self.api_url}/posts"
        
        # The Pydantic model is converted to a dictionary.
        # The `by_alias=True` ensures the keys match Strapi's field names.
        # The `exclude_none=True` prevents sending empty fields.
        payload = {
            "data": post_data.model_dump(by_alias=True, exclude_none=True)
        }

        try:
            response = requests.post(posts_url, headers=self.headers, data=json.dumps(payload))
            response.raise_for_status()  # Raises an HTTPError for bad responses (4xx or 5xx)
            print(f"Successfully created post draft in Strapi. Post ID: {response.json()['data']['id']}")
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error creating post in Strapi: {e}")
            # In a real application, you might want to handle different error types
            # or implement retry logic here.
            return None

# Example of how to use the client
if __name__ == '__main__':
    # This block is for testing purposes.
    # Ensure you have set the Strapi URL and Token in your .env file.
    strapi_client = StrapiClient()

    # Create a sample post using the Pydantic model
    sample_post = StrapiPost(
        Title="Test Post from Strapi Client",
        Slug="test-post-from-strapi-client",
        BodyContent=[{"type": "paragraph", "children": [{"type": "text", "text": "This is a test."}]}],
        Author="StrapiClientTest"
    )

    # Call the create_post method
    strapi_client.create_post(sample_post)
