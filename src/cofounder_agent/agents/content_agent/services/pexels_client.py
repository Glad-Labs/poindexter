from services.logger_config import get_logger

logger = get_logger(__name__)

import aiofiles
import httpx

from ..config import config


class PexelsClient:
    """
    Client for fetching images from the Pexels API.

    ASYNC-FIRST: All HTTP operations use httpx (no blocking I/O)
    """

    def __init__(self, *, site_config=None):
        if not config.PEXELS_API_KEY:
            raise ValueError("PEXELS_API_KEY is not set in the environment.")
        self.headers = {"Authorization": config.PEXELS_API_KEY}
        # #198: tunable via app_settings.pexels_api_base. Optional kwarg to
        # keep the ``postgres_image_agent`` factory — the only caller in the
        # workflow-executor path — unchanged until its surrounding agent
        # framework migrates (separate GH#72 cleanup).
        if site_config is not None:
            _base = site_config.get("pexels_api_base", "https://api.pexels.com/v1")
        else:
            _base = "https://api.pexels.com/v1"
        self.BASE_URL = f"{_base.rstrip('/')}/search"

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

                    async with aiofiles.open(file_path, "wb") as f:
                        await f.write(img_response.content)
                    logger.info(
                        f"Successfully downloaded image for query '{query}' to {file_path}."
                    )
                    return True
                else:
                    logger.warning(f"No photos found on Pexels for query: '{query}'")
                    return False
        except httpx.HTTPError as e:
            logger.error(f"Error fetching image from Pexels: {e}", exc_info=True)
            return False
