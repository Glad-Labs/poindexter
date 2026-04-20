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
        # logging is not yet available at this point, so use warnings module
        import warnings

        warnings.warn(f"Failed to fix sys.path for venv: {e}", stacklevel=2)


# Execute the fix immediately when this module is imported
_fix_sys_path_for_venv()

import asyncio
import hashlib
import json
import os
import time

from services.logger_config import get_logger

logger = get_logger(__name__)

import aiofiles
import httpx

from agents.content_agent.config import config
from agents.content_agent.utils.helpers import extract_json_from_string

# Import google-genai (official SDK — google-generativeai removed, see Issue #404)
genai = None
try:
    import google.genai as genai_module

    genai = genai_module
    logger.info("google.genai successfully imported")
except (ImportError, ModuleNotFoundError) as e:
    logger.warning(f"google.genai not available: {e}. Gemini provider will fall back to Ollama.")


class LLMClient:
    """Client for interacting with a configured Large Language Model."""

    def __init__(self, model_name: "str | None" = None):
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
        self._cleanup_stale_cache()

        try:
            if self.provider == "gemini":
                # Check if google-genai is available
                # If Poetry broke the import, fall back to Ollama gracefully
                if not genai:
                    logger.warning(
                        "⚠️ Gemini provider requested but google-genai module unavailable. "
                        "This is often due to Poetry's namespace package handling. "
                        "Falling back to Ollama for content generation."
                    )
                    self.provider = "ollama"
                else:
                    # Gemini is available - use it
                    if not config.GEMINI_API_KEY:
                        raise ValueError(
                            "GEMINI_API_KEY (or GOOGLE_API_KEY) not found in config for gemini provider."
                        )

                    os.environ["GOOGLE_API_KEY"] = config.GEMINI_API_KEY

                    # Use override model if provided, otherwise use config default
                    model_to_use = model_name if model_name else config.GEMINI_MODEL
                    self.model = genai.GenerativeModel(model_to_use)  # type: ignore[attr-defined]
                    self.summarizer_model = genai.GenerativeModel(config.SUMMARIZER_MODEL)  # type: ignore[attr-defined]
                    logger.info(f"✅ Initialized Gemini client with model: {model_to_use}")

            if self.provider == "local" or self.provider == "ollama":
                # Treat Ollama as a local provider - both use the same HTTP API endpoint
                logger.info(f"✅ Using local LLM provider (Ollama) at {config.LOCAL_LLM_API_URL}")

            elif self.provider == "gemini":
                pass  # Already handled above
            else:
                raise ValueError(f"Unsupported LLM provider: {self.provider}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}", exc_info=True)
            raise

    def _cleanup_stale_cache(self, max_age_days: int = 30, max_size_mb: int = 500) -> None:
        """Remove stale cache files older than max_age_days or if total exceeds max_size_mb."""
        try:
            cache_files = sorted(
                self.cache_dir.glob("*.cache"),
                key=lambda p: p.stat().st_mtime,
            )
            if not cache_files:
                return

            now = time.time()
            max_age_seconds = max_age_days * 86400
            removed = 0

            # Pass 1: remove files older than max_age_days
            for f in cache_files:
                if now - f.stat().st_mtime > max_age_seconds:
                    f.unlink(missing_ok=True)
                    removed += 1

            # Pass 2: if still over size limit, evict oldest first
            remaining = sorted(
                self.cache_dir.glob("*.cache"),
                key=lambda p: p.stat().st_mtime,
            )
            total_mb = sum(f.stat().st_size for f in remaining) / (1024 * 1024)
            while total_mb > max_size_mb and remaining:
                oldest = remaining.pop(0)
                total_mb -= oldest.stat().st_size / (1024 * 1024)
                oldest.unlink(missing_ok=True)
                removed += 1

            if removed > 0:
                logger.info(
                    f"Cache cleanup: removed {removed} stale files, "
                    f"{len(list(self.cache_dir.glob('*.cache')))} remaining"
                )
        except Exception as e:
            logger.warning(f"Cache cleanup failed (non-critical): {e}")

    def _get_cache_path(self, prompt: str, format: str) -> Path:
        """Generates a cache path for a given prompt and format."""
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        return self.cache_dir / f"{prompt_hash}.{format}.cache"

    async def generate_json(self, prompt: str) -> dict:
        """Generates JSON content using the configured LLM, with async caching."""
        cache_path = self._get_cache_path(prompt, "json")
        if cache_path.exists():
            logger.info("Returning cached JSON response for prompt.")
            async with aiofiles.open(cache_path) as f:
                content = await f.read()
                return json.loads(content)

        _llm_start = time.perf_counter()
        _status = "success"
        try:
            if self.provider == "gemini":
                # Run the synchronous Gemini SDK call in a thread so it does
                # not block the event loop (issue #780).
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, self._generate_json_gemini, prompt)
            elif self.provider == "local" or self.provider == "ollama":
                result = await self._generate_json_local(prompt)
            else:
                logger.error(f"Unsupported LLM provider: {self.provider}")
                return {}
        except Exception:
            _status = "error"
            raise
        finally:
            _llm_latency_ms = int((time.perf_counter() - _llm_start) * 1000)
            logger.info(
                f"[llm_call] provider={self.provider} method=generate_json "
                f"latency_ms={_llm_latency_ms} status={_status}"
            )

        if result:
            async with aiofiles.open(cache_path, "w") as f:
                await f.write(json.dumps(result))

        return result

    def _generate_json_gemini(self, prompt: str) -> dict:
        try:
            response = self.model.generate_content(prompt)  # type: ignore[union-attr]
            return json.loads(response.text)
        except json.JSONDecodeError:
            logger.error("Failed to decode JSON from Gemini response.", exc_info=True)
            return {}
        except Exception as e:
            logger.error(f"Error generating JSON content from Gemini: {e}", exc_info=True)
            return {}

    async def _generate_json_local(self, prompt: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{config.LOCAL_LLM_API_URL}/api/chat",
                    json={
                        "model": config.LOCAL_LLM_MODEL_NAME,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                    },
                )
                response.raise_for_status()
            response_json = response.json()
            msg = response_json.get("message", {})
            if msg.get("content"):
                raw_response = msg["content"]
                logger.debug(f"LLM raw response: {raw_response[:200]}...")
                # Try to extract JSON from the response
                extracted_json = extract_json_from_string(raw_response)
                if extracted_json:
                    logger.debug(f"Extracted JSON: {extracted_json[:200]}...")
                    return json.loads(extracted_json)
                # Fallback: try parsing the raw text directly
                logger.debug("No JSON block found, attempting direct parse...")
                return json.loads(raw_response)
            else:
                logger.error("'response' key not found in local LLM output.")
                raise ValueError("No 'response' key in LLM output")
        except httpx.HTTPError as e:
            logger.error(f"Error communicating with local LLM: {e}", exc_info=True)
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from local LLM response: {e}", exc_info=True)
            raise ValueError(f"LLM response was not valid JSON: {e!s}") from e

    async def generate_text(self, prompt: str) -> str:
        """Generates plain text content using the configured LLM, with caching (async)."""
        cache_path = self._get_cache_path(prompt, "txt")
        if cache_path.exists():
            logger.info("Returning cached text response for prompt.")
            # Use aiofiles to avoid blocking the event loop on file reads (issue #789).
            async with aiofiles.open(cache_path, encoding="utf-8") as f:
                return await f.read()

        _llm_start = time.perf_counter()
        _status = "success"
        try:
            if self.provider == "gemini":
                # Run the synchronous Gemini SDK call in a thread so it does
                # not block the event loop (issue #780).
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, self._generate_text_gemini, prompt)
            elif self.provider == "local" or self.provider == "ollama":
                result = await self._generate_text_local(prompt)
            else:
                logger.error(f"Unsupported LLM provider: {self.provider}")
                return ""
        except Exception:
            _status = "error"
            raise
        finally:
            _llm_latency_ms = int((time.perf_counter() - _llm_start) * 1000)
            logger.info(
                f"[llm_call] provider={self.provider} method=generate_text "
                f"latency_ms={_llm_latency_ms} status={_status}"
            )

        if result:
            try:
                # Use aiofiles to avoid blocking the event loop on file writes (issue #789).
                async with aiofiles.open(cache_path, "w", encoding="utf-8") as f:
                    await f.write(result)
            except Exception as e:
                logger.warning(f"Failed to cache result: {e}")

        return result

    def _generate_text_gemini(self, prompt: str) -> str:
        try:
            response = self.model.generate_content(prompt)  # type: ignore[union-attr]
            return response.text
        except Exception as e:
            logger.error(f"Error generating text content from Gemini: {e}", exc_info=True)
            return ""

    async def _generate_text_local(self, prompt: str) -> str:
        try:
            async with httpx.AsyncClient(
                timeout=120
            ) as client:  # Increased timeout for longer generation
                response = await client.post(
                    f"{config.LOCAL_LLM_API_URL}/api/chat",
                    json={
                        "model": config.LOCAL_LLM_MODEL_NAME,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "options": {"num_predict": 4096},
                    },
                )
                response.raise_for_status()
            return response.json().get("message", {}).get("content", "")
        except httpx.HTTPError as e:
            logger.error(f"Error communicating with local LLM: {e}", exc_info=True)
            return ""

    async def generate_summary(self, prompt: str) -> str:
        """Generates a summary using the configured summarizer model, with caching (async)."""
        cache_path = self._get_cache_path(prompt, "summary.txt")
        if cache_path.exists():
            logger.info("Returning cached summary for prompt.")
            # Use aiofiles to avoid blocking the event loop on file reads (issue #789).
            async with aiofiles.open(cache_path, encoding="utf-8") as f:
                return await f.read()

        _llm_start = time.perf_counter()
        _status = "success"
        try:
            if self.provider == "gemini":
                # Run the synchronous Gemini SDK call in a thread so it does
                # not block the event loop (issue #780).
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, self._generate_summary_gemini, prompt)
            elif self.provider == "local" or self.provider == "ollama":
                # For local/ollama provider, we can reuse the text generation with the summarizer model if needed
                # or use a specific endpoint if available. For now, we use the main model.
                logger.warning(
                    "Summarization with local/ollama provider falls back to the main model."
                )
                result = await self._generate_text_local(prompt)
            else:
                logger.error(f"Unsupported LLM provider: {self.provider}")
                return ""
        except Exception:
            _status = "error"
            raise
        finally:
            _llm_latency_ms = int((time.perf_counter() - _llm_start) * 1000)
            logger.info(
                f"[llm_call] provider={self.provider} method=generate_summary "
                f"latency_ms={_llm_latency_ms} status={_status}"
            )

        if result:
            try:
                # Use aiofiles to avoid blocking the event loop on file writes (issue #789).
                async with aiofiles.open(cache_path, "w", encoding="utf-8") as f:
                    await f.write(result)
            except Exception as e:
                logger.warning(f"Failed to cache summary: {e}")

        return result

    def _generate_summary_gemini(self, prompt: str) -> str:
        try:
            response = self.summarizer_model.generate_content(prompt)  # type: ignore[union-attr]
            return response.text
        except Exception as e:
            logger.error(f"Error generating summary from Gemini: {e}", exc_info=True)
            return ""
