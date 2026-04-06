"""
Unit tests for services/gemini_client.py

Tests GeminiClient: initialization, configuration checks, generate, chat,
check_health, get_pricing, and model listing. google.genai calls are mocked.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gemini_client import GeminiClient, get_gemini_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_genai_response(text: str = "Hello world") -> MagicMock:
    """Build a mock genai response object."""
    resp = MagicMock()
    resp.text = text
    return resp


def _make_mock_client(response: MagicMock | None = None) -> MagicMock:
    """Build a mock google.genai.Client with async generate_content."""
    if response is None:
        response = _make_genai_response()
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=response)
    return client


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestGeminiClientInit:
    def test_uses_explicit_api_key(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with patch("services.gemini_client.site_config", create=True):
            client = GeminiClient(api_key="test-key-123")
        assert client.api_key == "test-key-123"

    def test_uses_env_google_api_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "env-google-key")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        client = GeminiClient(api_key="direct-key")
        assert client.api_key == "direct-key"

    def test_falls_back_to_env_vars(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "env-key")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        # Patch site_config import to fail so it falls through to env
        with patch.dict("sys.modules", {"services.site_config": None}):
            client = GeminiClient()
        assert client.api_key == "env-key"

    def test_no_key_allowed(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        # Make site_config import fail
        with patch.dict("sys.modules", {"services.site_config": None}):
            client = GeminiClient()
        assert client.api_key is None

    def test_client_starts_as_none(self):
        client = GeminiClient(api_key="k")
        assert client._client is None

    def test_available_models_populated(self):
        client = GeminiClient(api_key="k")
        assert len(client.available_models) > 0
        assert "gemini-2.5-flash" in client.available_models

    def test_base_url_set(self):
        client = GeminiClient(api_key="k")
        assert "generativelanguage.googleapis.com" in client.base_url


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------


class TestIsConfigured:
    def test_configured_with_key(self):
        client = GeminiClient(api_key="k")
        assert client.is_configured() is True

    def test_not_configured_without_key(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with patch.dict("sys.modules", {"services.site_config": None}):
            client = GeminiClient()
        assert client.is_configured() is False


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------


class TestListModels:
    @pytest.mark.asyncio
    async def test_returns_models_when_configured(self):
        client = GeminiClient(api_key="k")
        models = await client.list_models()
        assert models == client.available_models

    @pytest.mark.asyncio
    async def test_returns_empty_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with patch.dict("sys.modules", {"services.site_config": None}):
            client = GeminiClient()
        models = await client.list_models()
        assert models == []


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


class TestGenerate:
    @pytest.mark.asyncio
    async def test_raises_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with patch.dict("sys.modules", {"services.site_config": None}):
            client = GeminiClient()
        with pytest.raises(ValueError, match="not configured"):
            await client.generate("hello")

    @pytest.mark.asyncio
    async def test_raises_when_genai_not_available(self):
        client = GeminiClient(api_key="k")
        with patch("services.gemini_client._GENAI_AVAILABLE", False):
            with pytest.raises(ImportError, match="google-genai"):
                await client.generate("hello")

    @pytest.mark.asyncio
    async def test_successful_generation(self):
        client = GeminiClient(api_key="k")
        mock_response = _make_genai_response("Generated text")
        mock_genai_client = _make_mock_client(mock_response)

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            with patch("google.genai.Client", return_value=mock_genai_client):
                result = await client.generate("test prompt")

        assert result == "Generated text"
        mock_genai_client.aio.models.generate_content.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generate_passes_model_and_prompt(self):
        client = GeminiClient(api_key="k")
        mock_genai_client = _make_mock_client()

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            with patch("google.genai.Client", return_value=mock_genai_client):
                await client.generate("my prompt", model="gemini-2.5-pro")

        call_kwargs = mock_genai_client.aio.models.generate_content.call_args
        assert call_kwargs.kwargs["model"] == "gemini-2.5-pro"
        assert call_kwargs.kwargs["contents"] == "my prompt"

    @pytest.mark.asyncio
    async def test_generate_returns_empty_on_none_text(self):
        client = GeminiClient(api_key="k")
        mock_response = _make_genai_response(None)
        mock_response.text = None
        mock_genai_client = _make_mock_client(mock_response)

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            with patch("google.genai.Client", return_value=mock_genai_client):
                result = await client.generate("prompt")

        assert result == ""

    @pytest.mark.asyncio
    async def test_generate_reuses_client(self):
        client = GeminiClient(api_key="k")
        mock_genai_client = _make_mock_client()
        client._client = mock_genai_client

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            await client.generate("prompt")

        # Should not create a new client since _client is already set
        mock_genai_client.aio.models.generate_content.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generate_timeout_raises_runtime_error(self):
        client = GeminiClient(api_key="k")
        mock_genai_client = MagicMock()
        mock_genai_client.aio.models.generate_content = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        client._client = mock_genai_client

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            # Patch wait_for to raise TimeoutError
            with patch("services.gemini_client.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
                with pytest.raises(RuntimeError, match="timed out"):
                    await client.generate("prompt")

    @pytest.mark.asyncio
    async def test_generate_generic_error_raises_runtime_error(self):
        client = GeminiClient(api_key="k")
        mock_genai_client = MagicMock()
        mock_genai_client.aio.models.generate_content = AsyncMock(
            side_effect=ConnectionError("network fail")
        )
        client._client = mock_genai_client

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            with patch(
                "services.gemini_client.asyncio.wait_for",
                side_effect=ConnectionError("network fail"),
            ):
                with pytest.raises(RuntimeError, match="Gemini generation error"):
                    await client.generate("prompt")


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


class TestChat:
    MESSAGES = [
        {"role": "user", "content": "Hello"},
        {"role": "model", "content": "Hi there"},
        {"role": "user", "content": "How are you?"},
    ]

    @pytest.mark.asyncio
    async def test_raises_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with patch.dict("sys.modules", {"services.site_config": None}):
            client = GeminiClient()
        with pytest.raises(ValueError, match="not configured"):
            await client.chat(self.MESSAGES)

    @pytest.mark.asyncio
    async def test_raises_when_genai_not_available(self):
        client = GeminiClient(api_key="k")
        with patch("services.gemini_client._GENAI_AVAILABLE", False):
            with pytest.raises(ImportError, match="google-genai"):
                await client.chat(self.MESSAGES)

    @pytest.mark.asyncio
    async def test_successful_chat(self):
        client = GeminiClient(api_key="k")
        mock_response = _make_genai_response("I'm doing well!")
        mock_genai_client = _make_mock_client(mock_response)

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            with patch("google.genai.Client", return_value=mock_genai_client):
                with patch("google.genai.types") as mock_types:
                    mock_types.Content = MagicMock()
                    mock_types.Part.from_text = MagicMock()
                    result = await client.chat(self.MESSAGES)

        assert result == "I'm doing well!"

    @pytest.mark.asyncio
    async def test_chat_returns_empty_on_none_text(self):
        client = GeminiClient(api_key="k")
        mock_response = MagicMock()
        mock_response.text = None
        mock_genai_client = _make_mock_client(mock_response)

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            with patch("google.genai.Client", return_value=mock_genai_client):
                with patch("google.genai.types") as mock_types:
                    mock_types.Content = MagicMock()
                    mock_types.Part.from_text = MagicMock()
                    result = await client.chat(self.MESSAGES)

        assert result == ""

    @pytest.mark.asyncio
    async def test_chat_timeout_raises_runtime_error(self):
        client = GeminiClient(api_key="k")
        mock_genai_client = _make_mock_client()
        client._client = mock_genai_client

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            with patch("google.genai.types") as mock_types:
                mock_types.Content = MagicMock()
                mock_types.Part.from_text = MagicMock()
                with patch(
                    "services.gemini_client.asyncio.wait_for",
                    side_effect=asyncio.TimeoutError(),
                ):
                    with pytest.raises(RuntimeError, match="timed out"):
                        await client.chat(self.MESSAGES)

    @pytest.mark.asyncio
    async def test_chat_generic_error_raises_runtime_error(self):
        client = GeminiClient(api_key="k")
        mock_genai_client = _make_mock_client()
        client._client = mock_genai_client

        with patch("services.gemini_client._GENAI_AVAILABLE", True):
            with patch("google.genai.types") as mock_types:
                mock_types.Content = MagicMock()
                mock_types.Part.from_text = MagicMock()
                with patch(
                    "services.gemini_client.asyncio.wait_for",
                    side_effect=Exception("something broke"),
                ):
                    with pytest.raises(RuntimeError, match="Gemini chat error"):
                        await client.chat(self.MESSAGES)


# ---------------------------------------------------------------------------
# check_health
# ---------------------------------------------------------------------------


class TestCheckHealth:
    @pytest.mark.asyncio
    async def test_not_configured(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with patch.dict("sys.modules", {"services.site_config": None}):
            client = GeminiClient()
        result = await client.check_health()
        assert result["status"] == "not_configured"
        assert result["configured"] is False

    @pytest.mark.asyncio
    async def test_healthy(self):
        client = GeminiClient(api_key="k")
        with patch.object(client, "generate", new_callable=AsyncMock, return_value="OK"):
            result = await client.check_health()
        assert result["status"] == "healthy"
        assert result["configured"] is True
        assert "models" in result
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_health_error(self):
        client = GeminiClient(api_key="k")
        with patch.object(
            client, "generate", new_callable=AsyncMock, side_effect=RuntimeError("fail")
        ):
            result = await client.check_health()
        assert result["status"] == "error"
        assert result["configured"] is True
        assert "error" in result
        assert "timestamp" in result


# ---------------------------------------------------------------------------
# get_pricing
# ---------------------------------------------------------------------------


class TestGetPricing:
    def test_known_model_pricing(self):
        client = GeminiClient(api_key="k")
        pricing = client.get_pricing("gemini-pro")
        assert "input" in pricing
        assert "output" in pricing
        assert pricing["input"] > 0
        assert pricing["output"] > 0

    def test_flash_model_pricing(self):
        client = GeminiClient(api_key="k")
        pricing = client.get_pricing("gemini-1.5-flash")
        assert pricing["input"] < pricing["output"]

    def test_unknown_model_returns_default(self):
        client = GeminiClient(api_key="k")
        pricing = client.get_pricing("nonexistent-model")
        default = client.get_pricing("gemini-pro")
        assert pricing == default


# ---------------------------------------------------------------------------
# get_gemini_client convenience function
# ---------------------------------------------------------------------------


class TestGetGeminiClient:
    def test_returns_gemini_client_instance(self):
        client = get_gemini_client(api_key="test-key")
        assert isinstance(client, GeminiClient)
        assert client.api_key == "test-key"

    def test_returns_client_without_key(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with patch.dict("sys.modules", {"services.site_config": None}):
            client = get_gemini_client()
        assert isinstance(client, GeminiClient)
