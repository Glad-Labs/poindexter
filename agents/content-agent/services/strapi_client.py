import requests
import json
import logging
from config import config
from utils.data_models import StrapiPost
from typing import Optional
import os

logger = logging.getLogger(__name__)

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
        }
        print("Strapi client initialized.")

    def upload_image(self, file_path: str, alt_text: str, caption: str) -> Optional[int]:
        """
        Uploads an image to the Strapi media library.

        Args:
            file_path (str): The local path to the image file.
            alt_text (str): The alt text for the image.
            caption (str): The caption for the image.

        Returns:
            Optional[int]: The ID of the uploaded image in Strapi, or None on failure.
        """
        upload_url = f"{self.api_url}/api/upload"
        try:
            with open(file_path, 'rb') as f:
                files = {'files': (os.path.basename(file_path), f, 'image/jpeg')}
                data = {'fileInfo': json.dumps({'alternativeText': alt_text, 'caption': caption})}
                response = requests.post(upload_url, headers={"Authorization": f"Bearer {self.api_token}"}, files=files, data=data)
                response.raise_for_status()
                logger.info(f"Successfully uploaded image {file_path} to Strapi.")
                return response.json()[0]['id']
        except requests.exceptions.RequestException as e:
            logger.error(f"Error uploading image to Strapi: {e}")
            return None

    def create_post(self, post_data: StrapiPost) -> Optional[dict]:
        """
        Creates a new post in Strapi as a draft.

        Args:
            post_data (StrapiPost): A Pydantic model instance of the post.

        Returns:
            Optional[dict]: The JSON response from the Strapi API, or None on failure.
        """
        posts_url = f"{self.api_url}/api/posts"
        
        payload = {
            "data": post_data.model_dump(by_alias=True, exclude_none=True)
        }

        try:
            headers = self.headers.copy()
            headers["Content-Type"] = "application/json"
            response = requests.post(posts_url, headers=headers, data=json.dumps(payload))
            response.raise_for_status()
            logger.info(f"Successfully created post draft in Strapi. Post ID: {response.json()['data']['id']}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating post in Strapi: {e}")
            # --- Enhanced Error Logging ---
            if e.response is not None:
                logger.error(f"Strapi Response Status Code: {e.response.status_code}")
                try:
                    # Try to log the detailed error message from Strapi
                    strapi_error = e.response.json()
                    logger.error(f"Strapi Error Details: {json.dumps(strapi_error, indent=2)}")
                except json.JSONDecodeError:
                    # If the response isn't JSON, log the raw text
                    logger.error(f"Strapi Raw Response: {e.response.text}")
            logger.error(f"Data payload sent to Strapi: {json.dumps(payload, indent=2)}")
            # --- End Enhanced Error Logging ---
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
        #Author="StrapiClientTest"
    )

    # Call the create_post method
    strapi_client.create_post(sample_post)
