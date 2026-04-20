"""
HuggingFace Integration Service

Handles inference via HuggingFace Inference API (free tier)
Supports open-source models for blog post generation
"""

import asyncio
from collections.abc import AsyncGenerator

import aiohttp

from services.logger_config import get_logger

from .error_handler import ServiceError

logger = get_logger(__name__)


class HuggingFaceClient:
    """Client for HuggingFace Inference API"""

    # Free tier models (no API key required, but rate limited)
    FREE_MODELS = {
        "mistralai/Mistral-7B-Instruct-v0.1": {
            "name": "Mistral 7B Instruct",
            "size": "7B",
            "speed": "fast",
            "quality": "excellent",
        },
        "meta-llama/Llama-2-7b-chat": {
            "name": "Llama 2 7B Chat",
            "size": "7B",
            "speed": "fast",
            "quality": "excellent",
        },
        "tiiuae/falcon-7b-instruct": {
            "name": "Falcon 7B Instruct",
            "size": "7B",
            "speed": "fast",
            "quality": "good",
        },
    }

    def __init__(self, api_token: str | None = None):
        """Initialize HuggingFace client

        Args:
            api_token: HuggingFace API token (optional, for higher rate limits)
        """
        from services.site_config import site_config
        self.api_token = api_token or site_config.get("huggingface_api_token", "")
        self.base_url = "https://api-inference.huggingface.co/models"
        self.session: aiohttp.ClientSession | None = None

        if not self.api_token:
            logger.warning("No HuggingFace API token provided. Using free tier (rate limited).")
        else:
            logger.info("HuggingFace API token configured.")

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure aiohttp session is created"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()

    def _get_headers(self) -> dict:
        """Get request headers with auth if available"""
        headers = {"User-Agent": "CofounderAgent/1.0"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    async def is_available(self) -> bool:
        """Check if HuggingFace API is reachable"""
        try:
            session = await self._ensure_session()
            # Try a simple model status check
            headers = self._get_headers()
            async with session.get(
                f"{self.base_url}/meta-llama/Llama-2-7b-chat/status",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                return response.status in [200, 302]  # 302 = model loading
        except Exception as e:
            logger.error("[_is_available] HuggingFace not available: %s", e, exc_info=True)
            return False

    async def generate(
        self,
        model: str,
        prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        top_p: float = 0.9,
    ) -> str:
        """Generate text using HuggingFace model

        Args:
            model: Model ID (e.g., "mistralai/Mistral-7B-Instruct-v0.1")
            prompt: Input prompt
            max_tokens: Maximum tokens to generate
            temperature: Creativity (0.0-1.0)
            top_p: Diversity (0.0-1.0)

        Returns:
            Generated text
        """
        try:
            session = await self._ensure_session()
            headers = self._get_headers()

            # Build request for text generation task
            payload = {
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": max_tokens,
                    "temperature": temperature,
                    "top_p": top_p,
                    "do_sample": True,
                },
                "options": {
                    "wait_for_model": True,  # Wait if model is loading
                    "use_cache": False,
                },
            }

            logger.debug("HuggingFace generate: model=%s, prompt_len=%d", model, len(prompt))

            async with session.post(
                f"{self.base_url}/{model}",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=300),  # 5 min timeout
            ) as response:
                if response.status == 200:
                    data = await response.json()

                    # Response format: [{"generated_text": "..."}]
                    if isinstance(data, list) and len(data) > 0:
                        generated_text = data[0].get("generated_text", "")
                        # Remove the prompt from the response
                        if generated_text.startswith(prompt):
                            generated_text = generated_text[len(prompt) :].strip()
                        logger.debug(
                            f"HuggingFace generation complete: {len(generated_text)} chars"
                        )
                        return generated_text

                    logger.error("Unexpected response format: %s", data)
                    raise ValueError(f"Unexpected response format: {data}")
                error_text = await response.text()
                logger.error("HuggingFace error (%s): %s", response.status, error_text)
                raise ServiceError(f"HuggingFace error: {response.status} - {error_text}")

        except asyncio.TimeoutError as exc:
            logger.error("HuggingFace request timeout for model %s", model, exc_info=True)
            raise TimeoutError("HuggingFace request timed out") from exc
        except Exception as e:
            logger.error("[_generate] HuggingFace generation failed: %s", e, exc_info=True)
            raise

    async def stream_generate(
        self,
        model: str,
        prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        top_p: float = 0.9,
    ) -> AsyncGenerator[str, None]:
        """Stream text generation using HuggingFace

        Note: HuggingFace Inference API doesn't support streaming for most models,
        so this returns the full result in one chunk.

        Args:
            model: Model ID
            prompt: Input prompt
            max_tokens: Maximum tokens
            temperature: Creativity
            top_p: Diversity

        Yields:
            Generated text chunks
        """
        try:
            result = await self.generate(model, prompt, max_tokens, temperature, top_p)
            yield result
        except Exception as e:
            logger.error("[_stream_generate] HuggingFace stream failed: %s", e, exc_info=True)
            raise

    async def chat_completion(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int = 2000,
        temperature: float = 0.7,
        top_p: float = 0.9,
    ) -> str:
        """Chat-style completion using HuggingFace

        Args:
            model: Model ID
            messages: List of message dicts with "role" and "content"
            max_tokens: Maximum tokens
            temperature: Creativity
            top_p: Diversity

        Returns:
            Assistant response
        """
        try:
            # Convert messages to prompt format
            prompt = ""
            for msg in messages:
                role = msg.get("role", "user").capitalize()
                content = msg.get("content", "")
                prompt += f"{role}: {content}\n"

            prompt += "Assistant:"

            return await self.generate(model, prompt, max_tokens, temperature, top_p)

        except Exception as e:
            logger.error(
                "[_chat_completion] HuggingFace chat completion failed: %s", e, exc_info=True
            )
            raise

    @classmethod
    def get_free_models(cls) -> dict[str, dict]:
        """Get available free tier models"""
        return cls.FREE_MODELS.copy()

    @classmethod
    def is_free_model(cls, model: str) -> bool:
        """Check if a model is available in free tier"""
        return model in cls.FREE_MODELS


async def test_huggingface():
    """Test HuggingFace connection and generation"""
    client = HuggingFaceClient()

    logger.info("Testing HuggingFace connection...")
    available = await client.is_available()
    logger.info("HuggingFace available: %s", available)

    if available:
        logger.info("Available free models:")
        for model_id, info in client.get_free_models().items():
            logger.info("  - %s: %s (%s)", model_id, info["name"], info["size"])

        # Try generation
        model = "mistralai/Mistral-7B-Instruct-v0.1"
        logger.info("Generating with %s...", model)
        try:
            result = await client.generate(
                model=model,
                prompt="Write a short blog title about AI",
                max_tokens=100,
            )
            logger.info("Result: %s", result)
        except Exception as e:
            logger.error("Generation failed: %s", e, exc_info=True)

    await client.close()


# Module-level cleanup helper for lifespan shutdown
_active_clients: list[HuggingFaceClient] = []


async def _session_cleanup() -> None:
    """Cleanup all active HuggingFace client sessions on shutdown"""
    for client in _active_clients:
        try:
            await client.close()
        except Exception as e:
            logger.error("[_session_cleanup] Error closing HuggingFace client: %s", e, exc_info=True)
    _active_clients.clear()


if __name__ == "__main__":
    asyncio.run(test_huggingface())
