import google.generativeai as genai
from config import config
import logging
import json
import os # Import the os module

class LLMClient:
    """Client for interacting with the Google Gemini API."""
    def __init__(self):
        """Initializes the Gemini client."""
        try:
            # The google-generativeai library automatically looks for the GOOGLE_API_KEY env var.
            # We set it here from the config to ensure authentication.
            if config.GEMINI_API_KEY:
                os.environ['GOOGLE_API_KEY'] = config.GEMINI_API_KEY
            else:
                raise ValueError("GEMINI_API_KEY not found in config.")
                
            self.model = genai.GenerativeModel(config.GEMINI_MODEL)
        except Exception as e:
            logging.error(f"Failed to initialize Gemini client: {e}")
            raise

    def generate_json(self, prompt: str) -> dict:
        """
        Generates JSON content using the configured Gemini model.

        Args:
            prompt (str): The prompt to send to the language model.

        Returns:
            dict: The generated JSON content.
        """
        try:
            response = self.model.generate_content(prompt)
            # Attempt to parse the response text as JSON
            return json.loads(response.text)
        except json.JSONDecodeError:
            logging.error("Failed to decode JSON from Gemini response.")
            return {}  # Return empty dict on JSON decode failure
        except Exception as e:
            logging.error(f"Error generating JSON content from Gemini: {e}")
            return {}  # Return empty dict on failure
