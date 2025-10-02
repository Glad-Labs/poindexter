import google.generativeai as genai
from config import config
import logging
import time
from functools import wraps
import os # Import the os module

# Decorator for retries with exponential backoff
def retry_with_backoff(retries=3, backoff_in_seconds=1):
    def rwb(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            x = 0
            while True:
                try:
                    return f(*args, **kwargs)
                except Exception as e:
                    if x < retries:
                        sleep = backoff_in_seconds * 2 ** x
                        logging.warning(f"API call failed, retrying in {sleep} seconds... Error: {e}")
                        time.sleep(sleep)
                        x += 1
                    else:
                        logging.error(f"API call failed after {retries} retries.")
                        raise
        return wrapper
    return rwb

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
            
            # The linter may have trouble with dynamic attributes.
            # This check ensures the class exists before trying to use it.
            if not hasattr(genai, 'GenerativeModel'):
                raise AttributeError("The installed google.generativeai library is missing the 'GenerativeModel' class.")
                
            genai.configure(api_key=config.GEMINI_API_KEY)
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

    @retry_with_backoff()
    def generate_text_content(self, prompt: str) -> str:
        """
        Generates text content using the configured Gemini model, with retries.

        Args:
            prompt (str): The prompt to send to the language model.

        Returns:
            str: The generated text content.
        """
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logging.error(f"Error generating text content from Gemini: {e}")
            # Re-raise the exception to be caught by the retry decorator
            raise
