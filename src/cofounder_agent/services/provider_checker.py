"""
Provider Availability Checker

Centralized utility to check which AI providers are available and configured.
Eliminates duplicate environment variable checks across the codebase.
"""

import logging
import os
from typing import Dict, Set

logger = logging.getLogger(__name__)


class ProviderChecker:
    """
    Utility class for checking provider availability.

    This eliminates the need to repeat env var checks throughout the codebase.
    Usage:
        if ProviderChecker.is_gemini_available():
            # Use Gemini
        if ProviderChecker.is_openai_available():
            # Use OpenAI
    """

    # Cache for provider status (updated on first check)
    _cache: Dict[str, bool] = {}

    @staticmethod
    def _get_env(*keys: str) -> str:
        """Get first available environment variable from list of keys."""
        for key in keys:
            value = os.getenv(key)
            if value:
                return value
        return ""

    @classmethod
    def is_gemini_available(cls) -> bool:
        """Check if Gemini (Google) API is available and configured."""
        if "gemini" not in cls._cache:
            # Check both GEMINI_API_KEY and GOOGLE_API_KEY (aliases)
            key = cls._get_env("GEMINI_API_KEY", "GOOGLE_API_KEY")
            cls._cache["gemini"] = bool(key)
            if cls._cache["gemini"]:
                logger.debug("✅ Gemini provider available")
            else:
                logger.debug("❌ Gemini provider not configured")
        return cls._cache["gemini"]

    @classmethod
    def is_openai_available(cls) -> bool:
        """Check if OpenAI API is available and configured."""
        if "openai" not in cls._cache:
            key = os.getenv("OPENAI_API_KEY", "")
            cls._cache["openai"] = bool(key)
            if cls._cache["openai"]:
                logger.debug("✅ OpenAI provider available")
            else:
                logger.debug("❌ OpenAI provider not configured")
        return cls._cache["openai"]

    @classmethod
    def is_anthropic_available(cls) -> bool:
        """Check if Anthropic Claude API is available and configured."""
        if "anthropic" not in cls._cache:
            key = os.getenv("ANTHROPIC_API_KEY", "")
            cls._cache["anthropic"] = bool(key)
            if cls._cache["anthropic"]:
                logger.debug("✅ Anthropic provider available")
            else:
                logger.debug("❌ Anthropic provider not configured")
        return cls._cache["anthropic"]

    @classmethod
    def is_huggingface_available(cls) -> bool:
        """Check if HuggingFace API is available and configured."""
        if "huggingface" not in cls._cache:
            key = os.getenv("HUGGINGFACE_API_TOKEN", "")
            cls._cache["huggingface"] = bool(key)
            if cls._cache["huggingface"]:
                logger.debug("✅ HuggingFace provider available")
            else:
                logger.debug("❌ HuggingFace provider not configured")
        return cls._cache["huggingface"]

    @classmethod
    def is_ollama_available(cls) -> bool:
        """Check if Ollama is configured (always available as local fallback)."""
        # Ollama is always "available" as a provider (even if server isn't running)
        return True

    @classmethod
    def get_available_providers(cls) -> Set[str]:
        """Get set of all available providers."""
        providers = {"ollama"}  # Ollama always available

        if cls.is_gemini_available():
            providers.add("gemini")
        if cls.is_openai_available():
            providers.add("openai")
        if cls.is_anthropic_available():
            providers.add("anthropic")
        if cls.is_huggingface_available():
            providers.add("huggingface")

        return providers

    @classmethod
    def get_preferred_provider(cls) -> str:
        """
        Get the preferred provider based on availability.

        Priority:
        1. Gemini (reliable, good quality)
        2. Ollama (free, local)
        3. OpenAI (expensive)
        4. Anthropic
        5. HuggingFace (rate limited)
        """
        if cls.is_gemini_available():
            return "gemini"
        if cls.is_ollama_available():
            return "ollama"
        if cls.is_openai_available():
            return "openai"
        if cls.is_anthropic_available():
            return "anthropic"
        if cls.is_huggingface_available():
            return "huggingface"

        logger.warning("No providers configured, falling back to Ollama")
        return "ollama"

    @classmethod
    def get_gemini_api_key(cls) -> str:
        """Get Gemini API key (checks both GEMINI_API_KEY and GOOGLE_API_KEY)."""
        return cls._get_env("GEMINI_API_KEY", "GOOGLE_API_KEY")

    @classmethod
    def get_openai_api_key(cls) -> str:
        """Get OpenAI API key."""
        return os.getenv("OPENAI_API_KEY", "")

    @classmethod
    def get_anthropic_api_key(cls) -> str:
        """Get Anthropic API key."""
        return os.getenv("ANTHROPIC_API_KEY", "")

    @classmethod
    def get_huggingface_token(cls) -> str:
        """Get HuggingFace API token."""
        return os.getenv("HUGGINGFACE_API_TOKEN", "")

    @classmethod
    def get_huggingface_api_key(cls) -> str:
        """Alias for get_huggingface_token for consistency with other methods."""
        return cls.get_huggingface_token()

    @classmethod
    def clear_cache(cls):
        """Clear provider cache (useful for testing)."""
        cls._cache.clear()
