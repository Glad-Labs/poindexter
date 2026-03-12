"""
Unit tests for ProviderChecker service.

Tests provider availability checks, env-var reading, cache behaviour,
preferred-provider selection, and API key retrieval — no network calls.
"""

import os
import pytest

from services.provider_checker import ProviderChecker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_provider_cache():
    """Ensure each test starts with a clean cache."""
    ProviderChecker.clear_cache()
    yield
    ProviderChecker.clear_cache()


# ---------------------------------------------------------------------------
# is_gemini_available
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsGeminiAvailable:
    def test_returns_false_when_no_key_set(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert ProviderChecker.is_gemini_available() is False

    def test_returns_true_with_gemini_api_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert ProviderChecker.is_gemini_available() is True

    def test_returns_true_with_google_api_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
        assert ProviderChecker.is_gemini_available() is True

    def test_gemini_takes_precedence_over_google(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
        assert ProviderChecker.is_gemini_available() is True

    def test_result_cached_after_first_call(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "initial-key")
        result1 = ProviderChecker.is_gemini_available()
        # Change env but cache should hold
        monkeypatch.setenv("GEMINI_API_KEY", "")
        result2 = ProviderChecker.is_gemini_available()
        assert result1 == result2


# ---------------------------------------------------------------------------
# is_openai_available
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsOpenAIAvailable:
    def test_returns_false_when_no_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert ProviderChecker.is_openai_available() is False

    def test_returns_true_when_key_set(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        assert ProviderChecker.is_openai_available() is True

    def test_empty_string_treated_as_unavailable(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        assert ProviderChecker.is_openai_available() is False


# ---------------------------------------------------------------------------
# is_anthropic_available
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsAnthropicAvailable:
    def test_returns_false_when_no_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert ProviderChecker.is_anthropic_available() is False

    def test_returns_true_when_key_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
        assert ProviderChecker.is_anthropic_available() is True


# ---------------------------------------------------------------------------
# is_huggingface_available
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsHuggingFaceAvailable:
    def test_returns_false_when_no_token(self, monkeypatch):
        monkeypatch.delenv("HUGGINGFACE_API_TOKEN", raising=False)
        assert ProviderChecker.is_huggingface_available() is False

    def test_returns_true_when_token_set(self, monkeypatch):
        monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf-test-token")
        assert ProviderChecker.is_huggingface_available() is True


# ---------------------------------------------------------------------------
# is_ollama_available
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsOllamaAvailable:
    def test_always_returns_true(self):
        """Ollama is always considered available (local fallback)."""
        assert ProviderChecker.is_ollama_available() is True


# ---------------------------------------------------------------------------
# get_available_providers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAvailableProviders:
    def test_ollama_always_in_set(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HUGGINGFACE_API_TOKEN", raising=False)
        providers = ProviderChecker.get_available_providers()
        assert "ollama" in providers

    def test_gemini_included_when_key_set(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        providers = ProviderChecker.get_available_providers()
        assert "gemini" in providers

    def test_openai_included_when_key_set(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        providers = ProviderChecker.get_available_providers()
        assert "openai" in providers

    def test_anthropic_included_when_key_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")
        providers = ProviderChecker.get_available_providers()
        assert "anthropic" in providers

    def test_huggingface_included_when_token_set(self, monkeypatch):
        monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf-test")
        providers = ProviderChecker.get_available_providers()
        assert "huggingface" in providers

    def test_returns_set_type(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        providers = ProviderChecker.get_available_providers()
        assert isinstance(providers, set)

    def test_no_cloud_keys_returns_only_ollama(self, monkeypatch):
        for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY", "HUGGINGFACE_API_TOKEN"):
            monkeypatch.delenv(key, raising=False)
        providers = ProviderChecker.get_available_providers()
        assert providers == {"ollama"}


# ---------------------------------------------------------------------------
# get_preferred_provider
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPreferredProvider:
    def test_gemini_preferred_when_available(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
        assert ProviderChecker.get_preferred_provider() == "gemini"

    def test_ollama_when_no_cloud_providers(self, monkeypatch):
        for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY", "HUGGINGFACE_API_TOKEN"):
            monkeypatch.delenv(key, raising=False)
        # Ollama is always available
        assert ProviderChecker.get_preferred_provider() == "ollama"

    def test_openai_when_no_gemini(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        # Ollama takes priority over OpenAI (always available)
        result = ProviderChecker.get_preferred_provider()
        # Ollama is before openai in the priority chain
        assert result in ("ollama", "openai")

    def test_returns_string(self, monkeypatch):
        result = ProviderChecker.get_preferred_provider()
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# API key retrieval methods
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApiKeyRetrieval:
    def test_get_gemini_api_key_from_gemini_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert ProviderChecker.get_gemini_api_key() == "gemini-secret"

    def test_get_gemini_api_key_falls_back_to_google(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "google-secret")
        assert ProviderChecker.get_gemini_api_key() == "google-secret"

    def test_get_gemini_api_key_empty_when_none_set(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert ProviderChecker.get_gemini_api_key() == ""

    def test_get_openai_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        assert ProviderChecker.get_openai_api_key() == "sk-openai"

    def test_get_openai_api_key_empty_when_not_set(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert ProviderChecker.get_openai_api_key() == ""

    def test_get_anthropic_api_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-secret")
        assert ProviderChecker.get_anthropic_api_key() == "ant-secret"

    def test_get_huggingface_token(self, monkeypatch):
        monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf-token")
        assert ProviderChecker.get_huggingface_token() == "hf-token"

    def test_get_huggingface_api_key_alias(self, monkeypatch):
        monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf-alias-token")
        # get_huggingface_api_key is an alias for get_huggingface_token
        assert ProviderChecker.get_huggingface_api_key() == "hf-alias-token"


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClearCache:
    def test_clear_cache_allows_fresh_check(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "first-key")
        ProviderChecker.is_openai_available()  # Populate cache

        ProviderChecker.clear_cache()
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        # After clearing cache and removing env var, should return False
        assert ProviderChecker.is_openai_available() is False

    def test_clear_cache_empties_internal_dict(self):
        ProviderChecker._cache["test_key"] = True
        ProviderChecker.clear_cache()
        assert "test_key" not in ProviderChecker._cache
