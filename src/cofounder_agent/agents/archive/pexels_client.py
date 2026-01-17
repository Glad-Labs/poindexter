# ARCHIVED - Use src/cofounder_agent/services/image_service.py instead
# This file is deprecated and kept for reference only
# Migration guide: See README.md in this directory

"""
LEGACY - ARCHIVED

Original PexelsClient from src/agents/content_agent/services/pexels_client.py
Now consolidated into unified ImageService

Migration:
    OLD: from src.agents.content_agent.services.pexels_client import PexelsClient
    NEW: from src.cofounder_agent.services.image_service import get_image_service

    OLD: pexels_client = PexelsClient()
         result = await pexels_client.search_and_download(query, path)
    NEW: image_service = get_image_service()
         featured_image = await image_service.search_featured_image(topic=query)
"""

import httpx
import logging
from ..config import config


class PexelsClient:
    """
    Client for fetching images from the Pexels API.

    ASYNC-FIRST: All HTTP operations use httpx (no blocking I/O)
    """

    BASE_URL = "https://api.pexels.com/v1/search"

    def __init__(self):
        if not config.PEXELS_API_KEY:
            raise ValueError("PEXELS_API_KEY is not set in the environment.")
        self.headers = {"Authorization": config.PEXELS_API_KEY}

    async def search_and_download(self, query: str, file_path: str) -> bool:
        """
        Searches for an image on Pexels and downloads the first result.

        Args:
            query (str): The search term for the image.
            file_path (str): The local path to save the downloaded image.

        Returns:
            bool: True if the download was successful, False otherwise.
        """
        params = {"query": query, "per_page": 1}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self.BASE_URL, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()

                if data["photos"]:
                    photo = data["photos"][0]
                    image_url = photo["src"]["large"]

                    img_response = await client.get(image_url)
                    img_response.raise_for_status()

                    with open(file_path, "wb") as f:
                        f.write(img_response.content)
                    logging.info(
                        f"Successfully downloaded image for query '{query}' to {file_path}."
                    )
                    return True
                else:
                    logging.warning(f"No photos found on Pexels for query: '{query}'")
                    return False
        except httpx.HTTPError as e:
            logging.error(f"Error fetching image from Pexels: {e}")
            return False
