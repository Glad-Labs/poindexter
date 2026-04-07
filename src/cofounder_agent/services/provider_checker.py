"""
Provider Availability Checker

Centralized utility to check which AI providers are available and configured.
Eliminates duplicate environment variable checks across the codebase.
"""

import os
from typing import Dict, Set

from services.logger_config import get_logger

logger = get_logger(__name__)


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
        """Get first available value from site_config or environment variables."""
        try:
            from services.site_config import site_config
            for key in keys:
                value = site_config.get(key.lower())
                if value:
                    return value
        except Exception:
            pass
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
        """OpenAI API disabled — using local Ollama only to avoid API costs."""
        return False

    @classmethod
    def is_anthropic_available(cls) -> bool:
        """Anthropic Claude API disabled — using local Ollama only to avoid API costs."""
        return False

    @classmethod
    def is_huggingface_available(cls) -> bool:
        """Check if HuggingFace API is available and configured."""
        if "huggingface" not in cls._cache:
            key = cls._get_env("HUGGINGFACE_API_TOKEN")
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
        if cls.is_huggingface_available():
            providers.add("huggingface")
        # Anthropic and OpenAI removed — local Ollama only

        return providers

    @classmethod
    def get_preferred_provider(cls) -> str:
        """
        Get the preferred provider based on availability.

        Priority:
        1. Ollama (free, local, preferred)
        2. HuggingFace (free tier, rate limited)
        Paid APIs (Anthropic, OpenAI) removed to avoid costs.
        """
        if cls.is_ollama_available():
            return "ollama"
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
        return cls._get_env("OPENAI_API_KEY")

    @classmethod
    def get_anthropic_api_key(cls) -> str:
        """Get Anthropic API key."""
        return cls._get_env("ANTHROPIC_API_KEY")

    @classmethod
    def get_huggingface_token(cls) -> str:
        """Get HuggingFace API token."""
        return cls._get_env("HUGGINGFACE_API_TOKEN")

    @classmethod
    def get_huggingface_api_key(cls) -> str:
        """Alias for get_huggingface_token for consistency with other methods."""
        return cls.get_huggingface_token()

    @classmethod
    def clear_cache(cls):
        """Clear provider cache (useful for testing)."""
        cls._cache.clear()
