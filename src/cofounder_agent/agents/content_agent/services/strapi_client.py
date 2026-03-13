import json
from services.logger_config import get_logger
import os
from typing import Optional

import httpx

from ..config import config
from ..utils.data_models import BlogPost, StrapiPost

logger = get_logger(__name__)

# Module-level shared AsyncClient — avoids per-request connection overhead.
# Timeout matches previous per-request defaults (30 s).
_shared_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    """Return the module-level shared httpx.AsyncClient, creating it on first call."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(timeout=30)
    return _shared_client


class StrapiClient:
    """
    A client for interacting with the Strapi CMS API.

    ASYNC-FIRST: All HTTP operations use httpx (no blocking I/O)
    """

    def __init__(self):
        """
        Initializes the Strapi client with the API URL and token from the config.
        """
        self.api_url = config.STRAPI_API_URL  # type: ignore[attr-defined]
        self.api_token = config.STRAPI_API_TOKEN  # type: ignore[attr-defined]
        if not self.api_url or not self.api_token:
            raise ValueError("STRAPI_API_URL and STRAPI_API_TOKEN must be set in the environment.")
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
        }
        # Add a diagnostic log to confirm which token is being used.
        token_preview = (
            f"{self.api_token[:5]}...{self.api_token[-4:]}" if self.api_token else "None"
        )
        logger.info(f"Strapi client initialized. Using token: {token_preview}")

    async def upload_image(self, file_path: str, alt_text: str, caption: str) -> Optional[int]:
        """
        Uploads an image to the Strapi media library (async).

        Args:
            file_path (str): The local path to the image file.
            alt_text (str): The alt text for the image.
            caption (str): The caption for the image.

        Returns:
            Optional[int]: The ID of the uploaded image in Strapi, or None on failure.
        """
        upload_url = f"{self.api_url}/upload"
        try:
            with open(file_path, "rb") as f:
                file_content = f.read()

            files = {"files": (os.path.basename(file_path), file_content, "image/jpeg")}
            data = {"fileInfo": json.dumps({"alternativeText": alt_text, "caption": caption})}

            client = _get_client()
            response = await client.post(
                upload_url,
                headers={"Authorization": f"Bearer {self.api_token}"},
                files=files,
                data=data,
            )
            response.raise_for_status()
            logger.info(f"Successfully uploaded image {file_path} to Strapi.")
            return response.json()[0]["id"]
        except httpx.HTTPError as e:
            logger.error(f"[upload_image] Error uploading image to Strapi: {e}", exc_info=True)
            return None

    async def _make_request(
        self, method: str, endpoint: str, data: Optional[dict] = None
    ) -> Optional[dict]:
        """
        Generic method to make HTTP requests to Strapi API (async).

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
            client = _get_client()
            if method.upper() == "GET":
                response = await client.get(url, headers=headers)
            elif method.upper() == "POST":
                headers["Content-Type"] = "application/json"
                response = await client.post(url, headers=headers, json=data if data else None)
            elif method.upper() == "PUT":
                headers["Content-Type"] = "application/json"
                response = await client.put(url, headers=headers, json=data if data else None)
            elif method.upper() == "DELETE":
                response = await client.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return response.json()

        except httpx.HTTPError as e:
            logger.error(f"[make_request] Error making {method} request to {endpoint}: {e}", exc_info=True)
            if hasattr(e, "response") and e.response is not None:  # type: ignore
                logger.error(f"Response status: {e.response.status_code}")  # type: ignore
                logger.error(f"Response text: {e.response.text}")  # type: ignore
            return None

    async def create_post(self, post_data: BlogPost) -> tuple[Optional[int], Optional[str]]:
        """
        Creates a new post in Strapi using the final processed data (async).

        Args:
            post_data (BlogPost): The BlogPost object containing all necessary data.

        Returns:
            tuple[Optional[int], Optional[str]]: The ID and URL of the created post, or (None, None) on failure.
        """
        if not self.api_token:
            logger.error("Strapi API token is not set. Cannot create post.")
            return None, None

        endpoint = f"{self.api_url}/api/posts"

        # Map the BlogPost model to the structure Strapi expects
        payload = {
            "data": {
                "Title": post_data.title,
                "Slug": post_data.slug,
                "BodyContent": post_data.body_content_blocks,
                "PostStatus": "Draft",  # Capitalized "Draft"
                "Keywords": post_data.primary_keyword,
                "MetaDescription": post_data.meta_description,
                # Assuming the first image is the featured image
                "FeaturedImage": (
                    post_data.images[0].strapi_image_id if post_data.images else None
                ),
            }
        }

        try:
            client = _get_client()
            response = await client.post(endpoint, headers=self.headers, json=payload)
            response.raise_for_status()

            data = response.json()
            post_id = data.get("data", {}).get("id")
            post_url = (
                f"{self.api_url}/api/posts/{post_id}"  # This is the API URL, not the frontend URL
            )

            logger.info(f"Successfully created post in Strapi with ID: {post_id}")
            return post_id, post_url

        except httpx.HTTPError as e:
            logger.error(f"Error creating post in Strapi: {str(e)}")
            return None, None

    async def get_all_published_posts(self) -> dict[str, str]:
        """
        Fetches all published posts from Strapi to build a map of titles to URLs
        for internal linking purposes (async).
        """
        try:
            response = await self._make_request(
                "GET",
                "/posts?fields[0]=Title&fields[1]=Slug&filters[PostStatus][$eq]=Published",
            )
            if not response or "data" not in response:
                return {}

            published_posts = {}
            for post in response["data"]:
                attrs = post.get("attributes", {})
                title = attrs.get("Title")
                slug = attrs.get("Slug")
                if title and slug:
                    published_posts[title] = f"/posts/{slug}"  # Assuming this URL structure

            logger.info(f"Fetched {len(published_posts)} published posts from Strapi.")
            return published_posts
        except Exception as e:
            logger.error(f"Failed to get published posts from Strapi: {e}")
            return {}


# Example of how to use the client
if __name__ == "__main__":
    import asyncio

    async def main():
        # This is a simple test block. For a real application, this should be in a separate test file.
        logger.info("Running StrapiClient test...")
        strapi_client = StrapiClient()

        # Create a sample BlogPost object for testing
        sample_post = BlogPost(
            topic="Test Topic",
            primary_keyword="test",
            target_audience="testers",
            category="testing",
            title="Test Post Title",
            slug="test-post-title",
            body_content_blocks=[
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "text": "This is a test."}],
                }
            ],
            meta_description="This is a test meta description.",
        )

        post_id, post_url = await strapi_client.create_post(sample_post)
        logger.info(f"Created post: ID={post_id}, URL={post_url}")

        # Fetch all published posts for internal linking
        published_posts = await strapi_client.get_all_published_posts()
        logger.info("Published Posts: %s", published_posts)

    asyncio.run(main())
