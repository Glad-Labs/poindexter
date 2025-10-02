from google.cloud import aiplatform
from google.auth import default
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

# Get the dedicated logger for prompts
prompts_logger = logging.getLogger('prompts')

class LLMClient:
    """Client for interacting with Google Cloud's Vertex AI (Gemini)."""
    def __init__(self):
        """Initializes the Vertex AI client."""
        try:
            # Initialize the Vertex AI client
            aiplatform.init(project=config.GCP_PROJECT_ID, location=config.GCP_REGION)
            self.model = aiplatform.GenerativeModel(config.GEMINI_MODEL)
            logging.info("Vertex AI client initialized successfully.")
        except Exception as e:
            logging.error(f"Failed to initialize Vertex AI client: {e}")
            raise

    @retry_with_backoff()
    def generate_text_content(self, prompt: str) -> str:
        """
        Generates text content using the configured Vertex AI model.

        Args:
            prompt (str): The prompt to send to the language model.

        Returns:
            str: The generated text content.
        """
        try:
            prompts_logger.debug(f"--- PROMPT SENT to Vertex AI ---\\n{prompt}\\n--- END PROMPT ---")
            response = self.model.generate_content(prompt)
            prompts_logger.debug(f"--- RESPONSE RECEIVED from Vertex AI ---\\n{response.text}\\n--- END RESPONSE ---")
            return response.text
        except Exception as e:
            logging.error(f"Error generating text content from Vertex AI: {e}")
            raise
