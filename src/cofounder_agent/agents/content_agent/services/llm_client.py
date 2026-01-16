import sys
from pathlib import Path

# ============================================================================
# CRITICAL: Fix sys.path for namespace packages FIRST before any imports
# This must happen before we even try to import google.generativeai
# Poetry breaks namespace package resolution, so we manually fix it here
# ============================================================================
def _fix_sys_path_for_venv():
    """Fix sys.path to prioritize venv site-packages for namespace package resolution."""
    try:
        venv_site_packages = Path(sys.prefix) / "Lib" / "site-packages"
        if venv_site_packages.exists():
            venv_site_packages_str = str(venv_site_packages)
            # Create new sys.path with venv site-packages FIRST
            new_path = [venv_site_packages_str]
            for p in sys.path:
                if p != venv_site_packages_str and p != "":
                    new_path.append(p)
            sys.path[:] = new_path
            # Force Python to reload the module cache
            import importlib
            importlib.invalidate_caches()
            import site
            site.main()  # Reinitialize site package processing
    except Exception as e:
        # If sys.path fixing fails, log but continue - fallback imports may still work
        print(f"[WARNING] Failed to fix sys.path for venv: {e}")

# Execute the fix immediately when this module is imported
_fix_sys_path_for_venv()

import httpx
from agents.content_agent.config import config
from agents.content_agent.utils.helpers import extract_json_from_string
import logging
import json
import os
import hashlib

# Now try to import google-genai (new package, replaces deprecated google.generativeai)
# With the sys.path fix above, this should work even with poetry run
genai = None
try:
    import google.genai as genai_module
    genai = genai_module
    logging.info("✅ google.genai successfully imported")
except (ImportError, ModuleNotFoundError) as e:
    # Fallback to old deprecated package if new one not available
    try:
        import google.generativeai as genai_module
        genai = genai_module
        logging.warning(f"⚠️  Using deprecated google.generativeai. Please upgrade to google.genai: {e}")
    except (ImportError, ModuleNotFoundError) as e2:
        logging.warning(f"⚠️ Could not import google.genai or google.generativeai: {e2}. Will fall back to Ollama.")
        genai = None

class LLMClient:
    """Client for interacting with a configured Large Language Model."""

    def __init__(self, model_name: str = None):
        """
        Initializes the LLM client based on the provider specified in the config.
        
        Args:
            model_name: Optional specific model to use (e.g., "gemini-2.5-flash", "gpt-4")
                       If not provided, uses the configured default model.
        """
        self.provider = config.LLM_PROVIDER
        self.model_name_override = model_name  # Store for potential use
        self.model = None
        self.summarizer_model = None
        self.cache_dir = Path(config.BASE_DIR) / ".cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            if self.provider == "gemini":
                # Check if google-generativeai is available
                # If Poetry broke the import, fall back to Ollama gracefully
                if not genai:
                    logging.warning(
                        f"⚠️ Gemini provider requested but google-generativeai module unavailable. "
                        f"This is often due to Poetry's namespace package handling. "
                        f"Falling back to Ollama for content generation."
                    )
                    self.provider = "ollama"
                else:
                    # Gemini is available - use it
                    if not config.GEMINI_API_KEY:
                        raise ValueError("GEMINI_API_KEY (or GOOGLE_API_KEY) not found in config for gemini provider.")
                    
                    os.environ["GOOGLE_API_KEY"] = config.GEMINI_API_KEY
                    
                    # Use override model if provided, otherwise use config default
                    model_to_use = model_name if model_name else config.GEMINI_MODEL
                    self.model = genai.GenerativeModel(model_to_use)
                    self.summarizer_model = genai.GenerativeModel(config.SUMMARIZER_MODEL)
                    logging.info(f"✅ Initialized Gemini client with model: {model_to_use}")
            
            if self.provider == "local" or self.provider == "ollama":
                # Treat Ollama as a local provider - both use the same HTTP API endpoint
                logging.info(f"✅ Using local LLM provider (Ollama) at {config.LOCAL_LLM_API_URL}")
            
            elif self.provider == "gemini":
                pass  # Already handled above
            else:
                raise ValueError(f"Unsupported LLM provider: {self.provider}")
        except Exception as e:
            logging.error(f"Failed to initialize LLM client: {e}")
            raise

    def _get_cache_path(self, prompt: str, format: str) -> Path:
        """Generates a cache path for a given prompt and format."""
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        return self.cache_dir / f"{prompt_hash}.{format}.cache"

    async def generate_json(self, prompt: str) -> dict:
        """Generates JSON content using the configured LLM, with caching (async)."""
        cache_path = self._get_cache_path(prompt, "json")
        if cache_path.exists():
            logging.info(f"Returning cached JSON response for prompt.")
            with open(cache_path, "r") as f:
                return json.load(f)

        if self.provider == "gemini":
            result = self._generate_json_gemini(prompt)
        elif self.provider == "local" or self.provider == "ollama":
            result = await self._generate_json_local(prompt)
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

    async def _generate_json_local(self, prompt: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{config.LOCAL_LLM_API_URL}/api/generate",
                    json={"model": config.LOCAL_LLM_MODEL_NAME, "prompt": prompt, "stream": False},
                )
                response.raise_for_status()
            response_json = response.json()
            if "response" in response_json:
                raw_response = response_json["response"]
                logging.debug(f"LLM raw response: {raw_response[:200]}...")
                # Try to extract JSON from the response
                extracted_json = extract_json_from_string(raw_response)
                if extracted_json:
                    logging.debug(f"Extracted JSON: {extracted_json[:200]}...")
                    return json.loads(extracted_json)
                else:
                    # Fallback: try parsing the raw text directly
                    logging.debug("No JSON block found, attempting direct parse...")
                    return json.loads(raw_response)
            else:
                logging.error("'response' key not found in local LLM output.")
                raise ValueError("No 'response' key in LLM output")
        except httpx.HTTPError as e:
            logging.error(f"Error communicating with local LLM: {e}")
            raise
        except json.JSONDecodeError as e:
            logging.error(f"Failed to decode JSON from local LLM response: {e}")
            raise ValueError(f"LLM response was not valid JSON: {str(e)}")

    async def generate_text(self, prompt: str) -> str:
        """Generates plain text content using the configured LLM, with caching (async)."""
        cache_path = self._get_cache_path(prompt, "txt")
        if cache_path.exists():
            logging.info(f"Returning cached text response for prompt.")
            return cache_path.read_text()

        if self.provider == "gemini":
            result = self._generate_text_gemini(prompt)
        elif self.provider == "local" or self.provider == "ollama":
            result = await self._generate_text_local(prompt)
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

    async def _generate_text_local(self, prompt: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{config.LOCAL_LLM_API_URL}/api/generate",
                    json={"model": config.LOCAL_LLM_MODEL_NAME, "prompt": prompt, "stream": False},
                )
                response.raise_for_status()
            return response.json().get("response", "")
        except httpx.HTTPError as e:
            logging.error(f"Error communicating with local LLM: {e}")
            return ""

    async def generate_summary(self, prompt: str) -> str:
        """Generates a summary using the configured summarizer model, with caching (async)."""
        cache_path = self._get_cache_path(prompt, "summary.txt")
        if cache_path.exists():
            logging.info(f"Returning cached summary for prompt.")
            return cache_path.read_text()

        if self.provider == "gemini":
            result = self._generate_summary_gemini(prompt)
        elif self.provider == "local" or self.provider == "ollama":
            # For local/ollama provider, we can reuse the text generation with the summarizer model if needed
            # or use a specific endpoint if available. For now, we use the main model.
            logging.warning("Summarization with local/ollama provider falls back to the main model.")
            result = await self._generate_text_local(prompt)
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
