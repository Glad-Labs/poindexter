"""
Unit tests for services/huggingface_client.py

Tests HuggingFaceClient: initialization, headers, generate, chat_completion,
class methods, and session management. aiohttp calls are mocked.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.huggingface_client import HuggingFaceClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_aiohttp_response(data: Any = None, text: str = "", status: int = 200) -> MagicMock:
    """Build an async context-manager mock for aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=data)
    resp.text = AsyncMock(return_value=text)
    return resp


def make_session_context(response: MagicMock) -> MagicMock:
    """Build a session mock where .get() / .post() return async context managers."""
    session = MagicMock()

    @asynccontextmanager
    async def _cm(*args, **kwargs):
        yield response

    session.get = lambda *a, **kw: _cm()
    session.post = lambda *a, **kw: _cm()
    session.close = AsyncMock()
    return session


MODEL_ID = "mistralai/Mistral-7B-Instruct-v0.1"


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestHuggingFaceClientInit:
    def test_uses_env_api_token(self, monkeypatch):
        monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf_env_token")
        client = HuggingFaceClient()
        assert client.api_token == "hf_env_token"

    def test_explicit_token_overrides_env(self, monkeypatch):
        monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf_env_token")
        client = HuggingFaceClient(api_token="explicit_token")
        assert client.api_token == "explicit_token"

    def test_no_token_allowed(self, monkeypatch):
        monkeypatch.delenv("HUGGINGFACE_API_TOKEN", raising=False)
        client = HuggingFaceClient()
        assert client.api_token is None

    def test_base_url_set(self):
        client = HuggingFaceClient()
        assert "huggingface.co" in client.base_url

    def test_session_starts_as_none(self):
        client = HuggingFaceClient()
        assert client.session is None


# ---------------------------------------------------------------------------
# _get_headers
# ---------------------------------------------------------------------------


class TestGetHeaders:
    def test_includes_user_agent(self):
        client = HuggingFaceClient(api_token="token")
        headers = client._get_headers()
        assert "User-Agent" in headers

    def test_includes_auth_when_token_set(self):
        client = HuggingFaceClient(api_token="hf_mytoken")
        headers = client._get_headers()
        assert headers["Authorization"] == "Bearer hf_mytoken"

    def test_no_auth_when_no_token(self, monkeypatch):
        monkeypatch.delenv("HUGGINGFACE_API_TOKEN", raising=False)
        client = HuggingFaceClient()
        headers = client._get_headers()
        assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# Class methods
# ---------------------------------------------------------------------------


class TestHuggingFaceClassMethods:
    def test_get_free_models_returns_dict(self):
        models = HuggingFaceClient.get_free_models()
        assert isinstance(models, dict)
        assert len(models) > 0

    def test_get_free_models_returns_copy(self):
        models = HuggingFaceClient.get_free_models()
        models["fake_model"] = {}
        # Should not affect class attribute
        assert "fake_model" not in HuggingFaceClient.get_free_models()

    def test_is_free_model_true(self):
        model = "mistralai/Mistral-7B-Instruct-v0.1"
        assert HuggingFaceClient.is_free_model(model) is True

    def test_is_free_model_false(self):
        assert HuggingFaceClient.is_free_model("openai/gpt-4") is False


# ---------------------------------------------------------------------------
# _ensure_session
# ---------------------------------------------------------------------------


class TestEnsureSession:
    @pytest.mark.asyncio
    async def test_creates_session_if_none(self):
        client = HuggingFaceClient()
        mock_session = AsyncMock()
        with patch("aiohttp.ClientSession", return_value=mock_session):
            session = await client._ensure_session()
        assert session is mock_session
        await client.close()

    @pytest.mark.asyncio
    async def test_reuses_existing_session(self):
        client = HuggingFaceClient()
        mock_session = MagicMock()
        client.session = mock_session
        session = await client._ensure_session()
        assert session is mock_session


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestHuggingFaceClientClose:
    @pytest.mark.asyncio
    async def test_close_closes_session(self):
        client = HuggingFaceClient()
        mock_session = AsyncMock()
        client.session = mock_session
        await client.close()
        mock_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_with_no_session_does_not_raise(self):
        client = HuggingFaceClient()
        await client.close()  # Should not raise


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


class TestHuggingFaceGenerate:
    @pytest.mark.asyncio
    async def test_successful_generation(self):
        client = HuggingFaceClient(api_token="token")
        resp_data = [{"generated_text": "My prompt text and then the answer."}]
        mock_resp = make_aiohttp_response(data=resp_data, status=200)
        mock_session = make_session_context(mock_resp)
        client.session = mock_session

        result = await client.generate(MODEL_ID, "My prompt text")
        # Should strip the prompt from the response
        assert "answer" in result

    @pytest.mark.asyncio
    async def test_prompt_stripped_from_response(self):
        client = HuggingFaceClient(api_token="token")
        prompt = "Hello"
        resp_data = [{"generated_text": "Hello World"}]
        mock_resp = make_aiohttp_response(data=resp_data, status=200)
        mock_session = make_session_context(mock_resp)
        client.session = mock_session

        result = await client.generate(MODEL_ID, prompt)
        assert result == "World"

    @pytest.mark.asyncio
    async def test_api_error_raises_service_error(self):
        client = HuggingFaceClient(api_token="token")
        mock_resp = make_aiohttp_response(text="Rate limit exceeded", status=429)
        mock_session = make_session_context(mock_resp)
        client.session = mock_session

        from services.error_handler import ServiceError

        with pytest.raises(ServiceError, match="HuggingFace error: 429"):
            await client.generate(MODEL_ID, "prompt")

    @pytest.mark.asyncio
    async def test_unexpected_response_format_raises_value_error(self):
        client = HuggingFaceClient(api_token="token")
        # API returns unexpected format (not a list)
        mock_resp = make_aiohttp_response(data={"error": "model not loaded"}, status=200)
        mock_session = make_session_context(mock_resp)
        client.session = mock_session

        with pytest.raises(ValueError, match="Unexpected response format"):
            await client.generate(MODEL_ID, "prompt")

    @pytest.mark.asyncio
    async def test_timeout_raises_timeout_error(self):
        client = HuggingFaceClient(api_token="token")
        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=asyncio.TimeoutError())
        client.session = mock_session

        with pytest.raises(TimeoutError, match="timed out"):
            await client.generate(MODEL_ID, "prompt")


# ---------------------------------------------------------------------------
# stream_generate
# ---------------------------------------------------------------------------


class TestHuggingFaceStreamGenerate:
    @pytest.mark.asyncio
    async def test_yields_generated_text(self):
        client = HuggingFaceClient(api_token="token")
        with patch.object(client, "generate", new=AsyncMock(return_value="Streamed text")):
            chunks = []
            async for chunk in client.stream_generate(MODEL_ID, "prompt"):
                chunks.append(chunk)
        assert chunks == ["Streamed text"]

    @pytest.mark.asyncio
    async def test_propagates_exception(self):
        client = HuggingFaceClient(api_token="token")
        with patch.object(
            client,
            "generate",
            new=AsyncMock(side_effect=ValueError("API failure")),
        ):
            with pytest.raises(ValueError, match="API failure"):
                async for _ in client.stream_generate(MODEL_ID, "prompt"):
                    pass


# ---------------------------------------------------------------------------
# chat_completion
# ---------------------------------------------------------------------------


class TestHuggingFaceChatCompletion:
    @pytest.mark.asyncio
    async def test_formats_messages_into_prompt(self):
        client = HuggingFaceClient(api_token="token")
        captured_prompt = []

        async def mock_generate(model, prompt, *args, **kwargs):
            captured_prompt.append(prompt)
            return "Response text"

        with patch.object(client, "generate", side_effect=mock_generate):
            await client.chat_completion(
                MODEL_ID,
                [
                    {"role": "user", "content": "What is AI?"},
                    {"role": "assistant", "content": "AI is..."},
                ],
            )

        prompt = captured_prompt[0]
        assert "User: What is AI?" in prompt
        assert "Assistant: AI is..." in prompt
        assert "Assistant:" in prompt  # Final "Assistant:" suffix

    @pytest.mark.asyncio
    async def test_returns_assistant_response(self):
        client = HuggingFaceClient(api_token="token")
        with patch.object(client, "generate", new=AsyncMock(return_value="AI response text")):
            result = await client.chat_completion(MODEL_ID, [{"role": "user", "content": "Hello"}])
        assert result == "AI response text"

    @pytest.mark.asyncio
    async def test_propagates_exception(self):
        client = HuggingFaceClient(api_token="token")
        with patch.object(
            client, "generate", new=AsyncMock(side_effect=RuntimeError("Model down"))
        ):
            with pytest.raises(RuntimeError, match="Model down"):
                await client.chat_completion(MODEL_ID, [{"role": "user", "content": "Hi"}])
