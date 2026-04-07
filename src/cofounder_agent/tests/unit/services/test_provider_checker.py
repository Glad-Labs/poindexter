"""
Unit tests for ProviderChecker service.

Tests provider availability checks, env-var reading, cache behaviour,
preferred-provider selection, and API key retrieval — no network calls.

Policy: Ollama-only. Gemini, OpenAI, Anthropic permanently disabled.
"""

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
# is_gemini_available — permanently disabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsGeminiAvailable:
    """Gemini is permanently disabled to avoid API costs."""

    def test_always_returns_false(self):
        assert ProviderChecker.is_gemini_available() is False


# ---------------------------------------------------------------------------
# is_openai_available — permanently disabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsOpenAIAvailable:
    """OpenAI is permanently disabled to avoid API costs."""

    def test_always_returns_false(self):
        assert ProviderChecker.is_openai_available() is False


# ---------------------------------------------------------------------------
# is_anthropic_available — permanently disabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsAnthropicAvailable:
    """Anthropic is permanently disabled to avoid API costs."""

    def test_always_returns_false(self):
        assert ProviderChecker.is_anthropic_available() is False


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
        monkeypatch.delenv("HUGGINGFACE_API_TOKEN", raising=False)
        providers = ProviderChecker.get_available_providers()
        assert "ollama" in providers

    def test_gemini_excluded_even_when_key_set(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        providers = ProviderChecker.get_available_providers()
        assert "gemini" not in providers  # Paid APIs disabled

    def test_openai_excluded_even_when_key_set(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        providers = ProviderChecker.get_available_providers()
        assert "openai" not in providers  # Paid APIs disabled

    def test_anthropic_excluded_even_when_key_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")
        providers = ProviderChecker.get_available_providers()
        assert "anthropic" not in providers  # Paid APIs disabled

    def test_huggingface_included_when_token_set(self, monkeypatch):
        monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf-test")
        providers = ProviderChecker.get_available_providers()
        assert "huggingface" in providers

    def test_returns_set_type(self, monkeypatch):
        providers = ProviderChecker.get_available_providers()
        assert isinstance(providers, set)

    def test_no_cloud_keys_returns_only_ollama(self, monkeypatch):
        monkeypatch.delenv("HUGGINGFACE_API_TOKEN", raising=False)
        providers = ProviderChecker.get_available_providers()
        assert providers == {"ollama"}


# ---------------------------------------------------------------------------
# get_preferred_provider
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPreferredProvider:
    def test_ollama_always_preferred(self):
        """Ollama is always available and always preferred (local, free)."""
        assert ProviderChecker.get_preferred_provider() == "ollama"

    def test_returns_string(self):
        result = ProviderChecker.get_preferred_provider()
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# API key retrieval methods
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApiKeyRetrieval:
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
    def test_clear_cache_empties_internal_dict(self):
        ProviderChecker._cache["test_key"] = True
        ProviderChecker.clear_cache()
        assert "test_key" not in ProviderChecker._cache
