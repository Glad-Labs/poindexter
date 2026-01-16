"""
Google Gemini Client
Provides interface to Google's Gemini AI models
"""

import os
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


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
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        self.base_url = "https://generativelanguage.googleapis.com/v1"
        self.available_models = [
            "gemini-pro",
            "gemini-pro-vision",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
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
        model: str = "gemini-pro",
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
            raise Exception("Gemini API key not configured")

        try:
            # Import google-genai library (new package, replaces deprecated google.generativeai)
            try:
                import google.genai as genai
            except ImportError:
                # Fallback to old deprecated package if new one not available
                import google.generativeai as genai

            # Configure the API key
            genai.configure(api_key=self.api_key)

            # Get the model
            gemini_model = genai.GenerativeModel(model)

            # Generate content
            response = await gemini_model.generate_content_async(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=max_tokens, temperature=temperature, **kwargs
                ),
            )

            return response.text

        except ImportError:
            raise Exception(
                "google-generativeai library not installed. "
                "Install with: pip install google-generativeai"
            )
        except Exception as e:
            logger.error(f"Gemini generation failed: {e}")
            raise Exception(f"Gemini generation error: {str(e)}")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "gemini-pro",
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
            raise Exception("Gemini API key not configured")

        try:
            # Import google-genai library (new package, replaces deprecated google.generativeai)
            try:
                import google.genai as genai
            except ImportError:
                # Fallback to old deprecated package if new one not available
                import google.generativeai as genai

            genai.configure(api_key=self.api_key)
            gemini_model = genai.GenerativeModel(model)

            # Start chat session
            chat = gemini_model.start_chat(history=[])

            # Add previous messages to context
            for msg in messages[:-1]:
                if msg["role"] == "user":
                    chat.send_message(msg["content"])

            # Send final message and get response
            response = await chat.send_message_async(
                messages[-1]["content"],
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=max_tokens, temperature=temperature, **kwargs
                ),
            )

            return response.text

        except ImportError:
            raise Exception(
                "google-generativeai library not installed. "
                "Install with: pip install google-generativeai"
            )
        except Exception as e:
            logger.error(f"Gemini chat failed: {e}")
            raise Exception(f"Gemini chat error: {str(e)}")

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
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            return {
                "status": "error",
                "configured": True,
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }

    def get_pricing(self, model: str = "gemini-pro") -> Dict[str, float]:
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
