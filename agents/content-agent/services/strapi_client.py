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
        # Add a diagnostic log to confirm which token is being used.
        token_preview = f"{self.api_token[:5]}...{self.api_token[-4:]}" if self.api_token else "None"
        logging.info(f"Strapi client initialized. Using token: {token_preview}")

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
        upload_url = f"{self.api_url}/upload"
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

    def _make_request(self, method: str, endpoint: str, data: Optional[dict] = None) -> Optional[dict]:
        """
        Generic method to make HTTP requests to Strapi API.
        
        Args:
            method (str): HTTP method (GET, POST, PUT, DELETE)
            endpoint (str): API endpoint (e.g., '/posts', '/categories')
            data (dict): Data to send with POST/PUT requests
            
        Returns:
            Optional[dict]: JSON response from Strapi API
        """
        url = f"{self.api_url}/api{endpoint}"
        headers = self.headers.copy()
        
        try:
            if method.upper() == 'GET':
                response = requests.get(url, headers=headers)
            elif method.upper() == 'POST':
                headers["Content-Type"] = "application/json"
                response = requests.post(url, headers=headers, data=json.dumps(data) if data else None)
            elif method.upper() == 'PUT':
                headers["Content-Type"] = "application/json"
                response = requests.put(url, headers=headers, data=json.dumps(data) if data else None)
            elif method.upper() == 'DELETE':
                response = requests.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
                
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error making {method} request to {endpoint}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response text: {e.response.text}")
            return None

    def create_post(self, post_data: StrapiPost) -> Optional[dict]:
        """
        Creates a new post in Strapi as a draft.

        Args:
            post_data (StrapiPost): A Pydantic model instance of the post.

        Returns:
            Optional[dict]: The JSON response from the Strapi API, or None on failure.
        """
        posts_url = f"{self.api_url}/posts"
        
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

    def get_all_published_posts(self) -> dict[str, str]:
        """
        Fetches all published posts from Strapi to build a map of titles to URLs
        for internal linking purposes.
        """
        try:
            response = self._make_request(
                "GET",
                "/posts?fields[0]=Title&fields[1]=Slug&filters[PostStatus][$eq]=Published"
            )
            if not response or "data" not in response:
                return {}

            published_posts = {}
            for post in response["data"]:
                attrs = post.get("attributes", {})
                title = attrs.get("Title")
                slug = attrs.get("Slug")
                if title and slug:
                    published_posts[title] = f"/posts/{slug}" # Assuming this URL structure
            
            logging.info(f"Fetched {len(published_posts)} published posts from Strapi.")
            return published_posts
        except Exception as e:
            logging.error(f"Failed to get published posts from Strapi: {e}")
            return {}

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

    # Fetch all published posts for internal linking
    published_posts = strapi_client.get_all_published_posts()
    print("Published Posts:", published_posts)
