"""
Google Gemini Client
Provides interface to Google's Gemini AI models
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import google.generativeai  # noqa: F401
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False


_GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "120"))


class GeminiClient:
    """
    Client for interacting with Google Gemini models.
    Supports: gemini-pro, gemini-pro-vision, gemini-ultra
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Gemini client.

        Args:
            api_key: Google API key (defaults to GOOGLE_API_KEY env var)
        """
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.base_url = "https://generativelanguage.googleapis.com/v1"
        # NOTE: As of Jan 2025, these are the available Gemini models
        # gemini-1.5-pro and gemini-1.5-flash are DEPRECATED
        self.available_models = [
            "gemini-2.5-flash",  # Latest fast model (CURRENT)
            "gemini-2.5-pro",  # Latest high-quality model (CURRENT)
            "gemini-2.0-flash",  # Previous fast model
            "gemini-pro-latest",  # Alias for latest pro
            "gemini-flash-latest",  # Alias for latest flash
        ]

        if not self.api_key:
            logger.warning("Gemini API key not found. Set GOOGLE_API_KEY environment variable.")

    def is_configured(self) -> bool:
        """Check if Gemini is properly configured."""
        return bool(self.api_key)

    async def list_models(self) -> List[str]:
        """
        List available Gemini models.

        Returns:
            List of model names
        """
        if not self.is_configured():
            return []

        return self.available_models

    async def generate(
        self,
        prompt: str,
        model: str = "gemini-2.5-flash",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> str:
        """
        Generate text using Gemini.

        Args:
            prompt: Input text prompt
            model: Model to use (default: gemini-pro)
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0.0-1.0)
            **kwargs: Additional generation parameters

        Returns:
            Generated text response

        Raises:
            Exception: If generation fails
        """
        if not self.is_configured():
            raise ValueError("GOOGLE_API_KEY not configured for Gemini")

        if not _GENAI_AVAILABLE:
            raise ImportError(
                "google-genai library not installed. "
                "Install with: pip install google-generativeai"
            )

        try:
            import google.genai as genai

            client = genai.Client(api_key=self.api_key)
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                ),
                timeout=_GEMINI_TIMEOUT,
            )

            return response.text or ""

        except asyncio.TimeoutError:
            logger.error(
                f"[generate] Gemini API call timed out after {_GEMINI_TIMEOUT}s",
                exc_info=True,
            )
            raise RuntimeError(f"Gemini generation timed out after {_GEMINI_TIMEOUT}s")
        except (ValueError, ImportError):
            raise
        except Exception as e:
            logger.error(f"Gemini generation failed: {e}", exc_info=True)
            raise RuntimeError(f"Gemini generation error: {str(e)}")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "gemini-2.5-flash",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> str:
        """
        Have a chat conversation with Gemini.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model to use
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            **kwargs: Additional parameters

        Returns:
            Generated response text
        """
        if not self.is_configured():
            raise ValueError("GOOGLE_API_KEY not configured for Gemini")

        if not _GENAI_AVAILABLE:
            raise ImportError(
                "google-genai library not installed. "
                "Install with: pip install google-generativeai"
            )

        try:
            import google.genai as genai
            import google.genai.types as genai_types

            client = genai.Client(api_key=self.api_key)
            contents = [
                genai_types.Content(
                    role=msg["role"],
                    parts=[genai_types.Part.from_text(text=msg["content"])],
                )
                for msg in messages
            ]
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                ),
                timeout=_GEMINI_TIMEOUT,
            )

            return response.text or ""

        except asyncio.TimeoutError:
            logger.error(
                f"[chat] Gemini API call timed out after {_GEMINI_TIMEOUT}s",
                exc_info=True,
            )
            raise RuntimeError(f"Gemini chat timed out after {_GEMINI_TIMEOUT}s")
        except (ValueError, ImportError):
            raise
        except Exception as e:
            logger.error(f"Gemini chat failed: {e}", exc_info=True)
            raise RuntimeError(f"Gemini chat error: {str(e)}")

    async def check_health(self) -> Dict[str, Any]:
        """
        Check Gemini service health.

        Returns:
            Dict with status and details
        """
        if not self.is_configured():
            return {
                "status": "not_configured",
                "configured": False,
                "message": "GOOGLE_API_KEY not set",
            }

        try:
            # Try a simple generation to verify connectivity
            test_response = await self.generate(
                prompt="Say 'OK' if you can read this.", model="gemini-pro", max_tokens=10
            )

            return {
                "status": "healthy",
                "configured": True,
                "models": self.available_models,
                "test_response": test_response[:50],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            return {
                "status": "error",
                "configured": True,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def get_pricing(self, model: str = "gemini-2.5-flash") -> Dict[str, float]:
        """
        Get pricing information for Gemini models.

        Args:
            model: Model name

        Returns:
            Dict with pricing per 1K tokens
        """
        pricing = {
            "gemini-pro": {
                "input": 0.000125,  # $0.125 per 1M tokens
                "output": 0.000375,  # $0.375 per 1M tokens
            },
            "gemini-pro-vision": {"input": 0.000125, "output": 0.000375},
            "gemini-1.5-pro": {
                "input": 0.00035,  # $0.35 per 1M tokens
                "output": 0.00105,  # $1.05 per 1M tokens
            },
            "gemini-1.5-flash": {
                "input": 0.000035,  # $0.035 per 1M tokens (cheapest!)
                "output": 0.000105,  # $0.105 per 1M tokens
            },
        }

        return pricing.get(model, pricing["gemini-pro"])


# Convenience function for quick initialization
def get_gemini_client(api_key: Optional[str] = None) -> GeminiClient:
    """Get a configured Gemini client instance."""
    return GeminiClient(api_key=api_key)
