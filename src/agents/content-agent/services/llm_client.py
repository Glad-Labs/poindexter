import google.generativeai as genai
from config import config
import logging
import json
import os
import requests
import hashlib
from pathlib import Path

class LLMClient:
    """Client for interacting with a configured Large Language Model."""

    def __init__(self):
        """Initializes the LLM client based on the provider specified in the config."""
        self.provider = config.LLM_PROVIDER
        self.model = None
        self.summarizer_model = None
        self.cache_dir = Path(config.BASE_DIR) / "content-agent" / ".cache"
        self.cache_dir.mkdir(exist_ok=True)

        try:
            if self.provider == "gemini":
                if config.GEMINI_API_KEY:
                    os.environ["GOOGLE_API_KEY"] = config.GEMINI_API_KEY
                else:
                    raise ValueError("GEMINI_API_KEY not found in config for gemini provider.")
                self.model = genai.GenerativeModel(config.GEMINI_MODEL)
                self.summarizer_model = genai.GenerativeModel(config.SUMMARIZER_MODEL)
                logging.info("Initialized Gemini client.")
            elif self.provider == "local":
                logging.info(f"Using local LLM provider at {config.LOCAL_LLM_API_URL}")
            else:
                raise ValueError(f"Unsupported LLM provider: {self.provider}")
        except Exception as e:
            logging.error(f"Failed to initialize LLM client: {e}")
            raise

    def _get_cache_path(self, prompt: str, format: str) -> Path:
        """Generates a cache path for a given prompt and format."""
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        return self.cache_dir / f"{prompt_hash}.{format}.cache"

    def generate_json(self, prompt: str) -> dict:
        """Generates JSON content using the configured LLM, with caching."""
        cache_path = self._get_cache_path(prompt, "json")
        if cache_path.exists():
            logging.info(f"Returning cached JSON response for prompt.")
            with open(cache_path, "r") as f:
                return json.load(f)

        if self.provider == "gemini":
            result = self._generate_json_gemini(prompt)
        elif self.provider == "local":
            result = self._generate_json_local(prompt)
        else:
            logging.error(f"Unsupported LLM provider: {self.provider}")
            return {}

        if result:
            with open(cache_path, "w") as f:
                json.dump(result, f)

        return result

    def _generate_json_gemini(self, prompt: str) -> dict:
        try:
            response = self.model.generate_content(prompt)
            return json.loads(response.text)
        except json.JSONDecodeError:
            logging.error("Failed to decode JSON from Gemini response.")
            return {}
        except Exception as e:
            logging.error(f"Error generating JSON content from Gemini: {e}")
            return {}

    def _generate_json_local(self, prompt: str) -> dict:
        try:
            response = requests.post(
                f"{config.LOCAL_LLM_API_URL}/api/generate",
                json={"model": config.LOCAL_LLM_MODEL_NAME, "prompt": prompt},
            )
            response.raise_for_status()
            response_json = response.json()
            if "response" in response_json:
                return json.loads(response_json["response"])
            else:
                logging.error("'response' key not found in local LLM output.")
                return {}
        except requests.exceptions.RequestException as e:
            logging.error(f"Error communicating with local LLM: {e}")
            return {}
        except json.JSONDecodeError:
            logging.error("Failed to decode JSON from local LLM response.")
            return {}

    def generate_text(self, prompt: str) -> str:
        """Generates plain text content using the configured LLM, with caching."""
        cache_path = self._get_cache_path(prompt, "txt")
        if cache_path.exists():
            logging.info(f"Returning cached text response for prompt.")
            return cache_path.read_text()

        if self.provider == "gemini":
            result = self._generate_text_gemini(prompt)
        elif self.provider == "local":
            result = self._generate_text_local(prompt)
        else:
            logging.error(f"Unsupported LLM provider: {self.provider}")
            return ""

        if result:
            cache_path.write_text(result)

        return result

    def _generate_text_gemini(self, prompt: str) -> str:
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logging.error(f"Error generating text content from Gemini: {e}")
            return ""

    def _generate_text_local(self, prompt: str) -> str:
        try:
            response = requests.post(
                f"{config.LOCAL_LLM_API_URL}/api/generate",
                json={"model": config.LOCAL_LLM_MODEL_NAME, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            return response.json().get("response", "")
        except requests.exceptions.RequestException as e:
            logging.error(f"Error communicating with local LLM: {e}")
            return ""

    def generate_summary(self, prompt: str) -> str:
        """Generates a summary using the configured summarizer model, with caching."""
        cache_path = self._get_cache_path(prompt, "summary.txt")
        if cache_path.exists():
            logging.info(f"Returning cached summary for prompt.")
            return cache_path.read_text()

        if self.provider == "gemini":
            result = self._generate_summary_gemini(prompt)
        elif self.provider == "local":
            # For local provider, we can reuse the text generation with the summarizer model if needed
            # or use a specific endpoint if available. For now, we use the main model.
            logging.warning("Summarization with local provider falls back to the main model.")
            result = self._generate_text_local(prompt)
        else:
            logging.error(f"Unsupported LLM provider: {self.provider}")
            return ""

        if result:
            cache_path.write_text(result)

        return result

    def _generate_summary_gemini(self, prompt: str) -> str:
        try:
            response = self.summarizer_model.generate_content(prompt)
            return response.text
        except Exception as e:
            logging.error(f"Error generating summary from Gemini: {e}")
            return ""
