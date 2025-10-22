"""
HuggingFace Integration Service

Handles inference via HuggingFace Inference API (free tier)
Supports open-source models for blog post generation
"""

import os
import logging
from typing import Optional, AsyncGenerator
import asyncio
import aiohttp

logger = logging.getLogger(__name__)


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

    def __init__(self, api_token: Optional[str] = None):
        """Initialize HuggingFace client
        
        Args:
            api_token: HuggingFace API token (optional, for higher rate limits)
        """
        self.api_token = api_token or os.getenv("HUGGINGFACE_API_TOKEN")
        self.base_url = "https://api-inference.huggingface.co/models"
        self.session: Optional[aiohttp.ClientSession] = None
        
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
            logger.debug(f"HuggingFace not available: {e}")
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

            logger.debug(f"HuggingFace generate: model={model}, prompt_len={len(prompt)}")

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
                            generated_text = generated_text[len(prompt):].strip()
                        logger.debug(f"HuggingFace generation complete: {len(generated_text)} chars")
                        return generated_text
                    
                    logger.error(f"Unexpected response format: {data}")
                    raise Exception(f"Unexpected response format: {data}")
                else:
                    error_text = await response.text()
                    logger.error(f"HuggingFace error ({response.status}): {error_text}")
                    raise Exception(f"HuggingFace error: {response.status} - {error_text}")

        except asyncio.TimeoutError:
            logger.error(f"HuggingFace request timeout for model {model}")
            raise Exception("HuggingFace request timed out")
        except Exception as e:
            logger.error(f"HuggingFace generation failed: {e}")
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
            logger.error(f"HuggingFace stream failed: {e}")
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
            logger.error(f"HuggingFace chat completion failed: {e}")
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

    print("Testing HuggingFace connection...")
    available = await client.is_available()
    print(f"HuggingFace available: {available}")

    if available:
        print("\nAvailable free models:")
        for model_id, info in client.get_free_models().items():
            print(f"  - {model_id}")
            print(f"    {info['name']} ({info['size']})")

        # Try generation
        model = "mistralai/Mistral-7B-Instruct-v0.1"
        print(f"\nGenerating with {model}...")
        try:
            result = await client.generate(
                model=model,
                prompt="Write a short blog title about AI",
                max_tokens=100,
            )
            print(f"Result: {result}")
        except Exception as e:
            print(f"Generation failed: {e}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(test_huggingface())
