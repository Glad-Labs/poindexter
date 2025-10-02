import requests
import logging
from config import config

class PexelsClient:
    """Client for fetching images from the Pexels API."""
    BASE_URL = "https://api.pexels.com/v1/search"

    def __init__(self):
        if not config.PEXELS_API_KEY:
            raise ValueError("PEXELS_API_KEY is not set in the environment.")
        self.headers = {
            "Authorization": config.PEXELS_API_KEY
        }

    def search_and_download(self, query: str, file_path: str) -> bool:
        """
        Searches for an image on Pexels and downloads the first result.

        Args:
            query (str): The search term for the image.
            file_path (str): The local path to save the downloaded image.

        Returns:
            bool: True if the download was successful, False otherwise.
        """
        params = {'query': query, 'per_page': 1}
        try:
            response = requests.get(self.BASE_URL, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data['photos']:
                photo = data['photos'][0]
                image_url = photo['src']['large']
                
                img_response = requests.get(image_url, stream=True)
                img_response.raise_for_status()
                
                with open(file_path, 'wb') as f:
                    for chunk in img_response.iter_content(1024):
                        f.write(chunk)
                logging.info(f"Successfully downloaded image for query '{query}' to {file_path}.")
                return True
            else:
                logging.warning(f"No photos found on Pexels for query: '{query}'")
                return False
        except requests.exceptions.RequestException as e:
            logging.error(f"Error fetching image from Pexels: {e}")
            return False