"""
Unit tests for services/gemini_client.py

Tests GeminiClient initialization, list_models, generate, chat,
check_health, and get_pricing. SDK calls are mocked to avoid real API calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gemini_client import GeminiClient, get_gemini_client

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client_no_key(monkeypatch) -> GeminiClient:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return GeminiClient()


@pytest.fixture
def client_with_key() -> GeminiClient:
    return GeminiClient(api_key="test-api-key")


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestGeminiClientInit:
    def test_uses_explicit_api_key(self):
        c = GeminiClient(api_key="explicit-key")
        assert c.api_key == "explicit-key"
        assert c.is_configured() is True

    def test_uses_google_api_key_env(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        c = GeminiClient()
        assert c.api_key == "google-key"

    def test_uses_gemini_api_key_env_fallback(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        c = GeminiClient()
        assert c.api_key == "gemini-key"

    def test_no_key_is_not_configured(self, client_no_key):
        assert client_no_key.is_configured() is False

    def test_available_models_populated(self, client_with_key):
        assert len(client_with_key.available_models) > 0
        assert "gemini-2.5-flash" in client_with_key.available_models


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------


class TestGeminiListModels:
    @pytest.mark.asyncio
    async def test_not_configured_returns_empty(self, client_no_key):
        result = await client_no_key.list_models()
        assert result == []

    @pytest.mark.asyncio
    async def test_configured_returns_model_list(self, client_with_key):
        result = await client_with_key.list_models()
        assert isinstance(result, list)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


class TestGeminiGenerate:
    @pytest.mark.asyncio
    async def test_raises_if_not_configured(self, client_no_key):
        with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
            await client_no_key.generate("Hello")

    @pytest.mark.asyncio
    async def test_raises_if_sdk_not_available(self, client_with_key):
        with patch("services.gemini_client._GENAI_AVAILABLE", False):
            with pytest.raises(ImportError, match="google-genai"):
                await client_with_key.generate("Hello")

    @pytest.mark.asyncio
    async def test_successful_generation(self, client_with_key):
        mock_response = MagicMock()
        mock_response.text = "Generated text response"

        mock_model = AsyncMock()
        mock_model.generate_content = AsyncMock(return_value=mock_response)

        mock_aio = MagicMock()
        mock_aio.models = mock_model

        mock_sdk_client = MagicMock()
        mock_sdk_client.aio = mock_aio

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            with patch("google.genai.Client", return_value=mock_sdk_client):
                result = await client_with_key.generate("Say hello", model="gemini-pro")

        assert result == "Generated text response"

    @pytest.mark.asyncio
    async def test_generate_empty_response(self, client_with_key):
        mock_response = MagicMock()
        mock_response.text = None

        mock_model = AsyncMock()
        mock_model.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_model
        mock_sdk_client = MagicMock()
        mock_sdk_client.aio = mock_aio

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            with patch("google.genai.Client", return_value=mock_sdk_client):
                result = await client_with_key.generate("Hello")

        assert result == ""

    @pytest.mark.asyncio
    async def test_generate_sdk_error_raises_runtime_error(self, client_with_key):
        mock_sdk_client = MagicMock()
        mock_sdk_client.aio.models.generate_content = AsyncMock(
            side_effect=Exception("API quota exceeded")
        )

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            with patch("google.genai.Client", return_value=mock_sdk_client):
                with pytest.raises(RuntimeError, match="Gemini generation error"):
                    await client_with_key.generate("Hello")


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


class TestGeminiChat:
    @pytest.mark.asyncio
    async def test_raises_if_not_configured(self, client_no_key):
        with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
            await client_no_key.chat([{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_raises_if_sdk_not_available(self, client_with_key):
        with patch("services.gemini_client._GENAI_AVAILABLE", False):
            with pytest.raises(ImportError, match="google-genai"):
                await client_with_key.chat([{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_successful_chat(self, client_with_key):
        mock_response = MagicMock()
        mock_response.text = "Chat response"

        mock_model = AsyncMock()
        mock_model.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_model
        mock_sdk_client = MagicMock()
        mock_sdk_client.aio = mock_aio

        messages = [
            {"role": "user", "content": "What is AI?"},
            {"role": "assistant", "content": "AI stands for Artificial Intelligence."},
        ]

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            with patch("google.genai.Client", return_value=mock_sdk_client):
                with patch("google.genai.types") as mock_types:
                    mock_types.Content = MagicMock(return_value=MagicMock())
                    mock_types.Part.from_text = MagicMock(return_value=MagicMock())
                    result = await client_with_key.chat(messages)

        assert result == "Chat response"


# ---------------------------------------------------------------------------
# check_health
# ---------------------------------------------------------------------------


class TestGeminiCheckHealth:
    @pytest.mark.asyncio
    async def test_not_configured_returns_not_configured_status(self, client_no_key):
        health = await client_no_key.check_health()
        assert health["status"] == "not_configured"
        assert health["configured"] is False

    @pytest.mark.asyncio
    async def test_healthy_status(self, client_with_key):
        with patch.object(client_with_key, "generate", new=AsyncMock(return_value="OK")):
            health = await client_with_key.check_health()
        assert health["status"] == "healthy"
        assert health["configured"] is True
        assert "models" in health
        assert "timestamp" in health

    @pytest.mark.asyncio
    async def test_error_status_on_exception(self, client_with_key):
        with patch.object(
            client_with_key,
            "generate",
            new=AsyncMock(side_effect=Exception("Connection timeout")),
        ):
            health = await client_with_key.check_health()
        assert health["status"] == "error"
        assert "error" in health


# ---------------------------------------------------------------------------
# get_pricing
# ---------------------------------------------------------------------------


class TestGeminiPricing:
    def test_known_model_pricing(self, client_with_key):
        pricing = client_with_key.get_pricing("gemini-pro")
        assert "input" in pricing
        assert "output" in pricing
        assert pricing["input"] > 0

    def test_unknown_model_falls_back_to_gemini_pro(self, client_with_key):
        pricing = client_with_key.get_pricing("unknown-model")
        default = client_with_key.get_pricing("gemini-pro")
        assert pricing == default

    def test_all_known_models_have_pricing(self, client_with_key):
        for model in ["gemini-pro", "gemini-pro-vision", "gemini-1.5-pro", "gemini-1.5-flash"]:
            pricing = client_with_key.get_pricing(model)
            assert pricing["input"] > 0
            assert pricing["output"] > 0


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestGetGeminiClient:
    def test_returns_gemini_client_instance(self):
        client = get_gemini_client(api_key="factory-key")
        assert isinstance(client, GeminiClient)
        assert client.api_key == "factory-key"

    def test_returns_unconfigured_if_no_key(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        client = get_gemini_client()
        assert not client.is_configured()
