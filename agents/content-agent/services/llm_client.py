import google.generativeai as genai
from config import config
import logging

class LLMClient:
    """Client for interacting with the Google Gemini API."""
    def __init__(self):
        if not config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set in the environment.")
        genai.configure(api_key=config.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(config.GEMINI_MODEL)

    def generate_text_content(self, prompt: str) -> str:
        """
        Generates text content using the configured Gemini model.

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
            return "" # Return empty string on failure
