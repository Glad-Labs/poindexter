from google.cloud import aiplatform
from google.auth import default
from config import config
import logging
import time
from functools import wraps
import vertexai
from vertexai.preview.generative_models import GenerativeModel, GenerationConfig

# Decorator for retries with exponential backoff
def retry_with_backoff(retries=3, backoff_in_seconds=1):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            attempts = 0
            while attempts < retries:
                try:
                    return f(*args, **kwargs)
                except Exception as e:
                    attempts += 1
                    if attempts >= retries:
                        logging.error(f"API call failed after {retries} retries.")
                        raise
                    sleep = backoff_in_seconds * (2 ** (attempts - 1))
                    logging.warning(f"API call failed, retrying in {sleep} seconds... Error: {e}")
                    time.sleep(sleep)
        return wrapper
    return decorator

# Get the dedicated logger for prompts
prompts_logger = logging.getLogger('prompts')

class LLMClient:
    """Client for interacting with Google Cloud's Vertex AI (Gemini)."""
    def __init__(self):
        """Initializes the Vertex AI client using the modern Generative AI SDK."""
        try:
            # Initialize the Vertex AI SDK
            vertexai.init(project=config.GCP_PROJECT_ID, location=config.GCP_REGION)
            
            # Load the generative model using the new 'preview' namespace
            self.model = GenerativeModel(config.GEMINI_MODEL)
            
            # Optional: Configure generation parameters
            self.generation_config = GenerationConfig(
                temperature=0.7,
                top_p=1.0,
                max_output_tokens=8192,
            )
            
            logging.info("Vertex AI client (Preview Generative AI SDK) initialized successfully.")
        except Exception as e:
            logging.error(f"Failed to initialize Vertex AI client: {e}")
            raise

    @retry_with_backoff()
    def generate_text_content(self, prompt: str) -> str:
        """
        Generates text content using the configured Vertex AI Gemini model.

        Args:
            prompt (str): The prompt to send to the language model.

        Returns:
            str: The generated text content.
        """
        try:
            prompts_logger.debug(f"--- PROMPT SENT to Vertex AI ---\\n{prompt}\\n--- END PROMPT ---")
            
            response = self.model.generate_content(
                prompt,
                generation_config=self.generation_config
            )
            
            content = response.text
            
            prompts_logger.debug(f"--- RESPONSE RECEIVED from Vertex AI ---\\n{content}\\n--- END RESPONSE ---")
            return content
        except Exception as e:
            logging.error(f"Error generating text content from Vertex AI: {e}")
            raise
