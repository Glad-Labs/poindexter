"""Unit tests for LLMProvider plugin implementations.

Heavy reliance on mocked HTTP so these tests don't need a live Ollama
or llama.cpp server. Integration verification (real Ollama + real
swap) happens separately in the refactor smoke test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins import LLMProvider
from services.llm_providers.ollama_native import OllamaNativeProvider
from services.llm_providers.openai_compat import OpenAICompatProvider


class TestOllamaNativeProtocol:
    def test_conforms_to_llm_provider(self):
        assert isinstance(OllamaNativeProvider(), LLMProvider)

    def test_has_required_attributes(self):
        p = OllamaNativeProvider()
        assert p.name == "ollama_native"
        assert p.supports_streaming is True
        assert p.supports_embeddings is True


class TestOpenAICompatProtocol:
    def test_conforms_to_llm_provider(self):
        assert isinstance(OpenAICompatProvider(), LLMProvider)

    def test_has_required_attributes(self):
        p = OpenAICompatProvider()
        assert p.name == "openai_compat"
        assert p.supports_streaming is True
        assert p.supports_embeddings is True


# ---------------------------------------------------------------------------
# OpenAICompatProvider — full behavior coverage
# ---------------------------------------------------------------------------


class _MockAsyncClient:
    """Context-managed httpx AsyncClient stand-in."""

    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, json=None, headers=None):
        return self._response


class TestOpenAICompatComplete:
    @pytest.mark.asyncio
    async def test_builds_request_to_base_url_plus_chat_completions(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            "model": "gemma3:27b",
        })

        post_mock = AsyncMock(return_value=mock_resp)

        class FakeClient:
            async def __aenter__(self):
                self.post = post_mock
                return self

            async def __aexit__(self, *exc):
                return None

        provider = OpenAICompatProvider()

        with patch("services.llm_providers.openai_compat.httpx.AsyncClient", return_value=FakeClient()):
            completion = await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="gemma3:27b",
                _provider_config={"base_url": "http://vllm-host:8080/v1"},
            )

        assert completion.text == "hello"
        assert completion.prompt_tokens == 5
        assert completion.completion_tokens == 3
        assert completion.finish_reason == "stop"

        # Verify the URL is base_url + /chat/completions (no double slashes).
        call_args = post_mock.await_args
        assert call_args.args[0] == "http://vllm-host:8080/v1/chat/completions"
        body = call_args.kwargs["json"]
        assert body["model"] == "gemma3:27b"
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        assert body["stream"] is False

    @pytest.mark.asyncio
    async def test_includes_api_key_header_when_set(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": {},
        })
        post_mock = AsyncMock(return_value=mock_resp)

        class FakeClient:
            async def __aenter__(self):
                self.post = post_mock
                return self

            async def __aexit__(self, *exc):
                return None

        provider = OpenAICompatProvider()

        with patch("services.llm_providers.openai_compat.httpx.AsyncClient", return_value=FakeClient()):
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="x",
                _provider_config={"base_url": "http://api.groq.com/openai/v1", "api_key": "gsk_test"},
            )

        headers = post_mock.await_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer gsk_test"

    @pytest.mark.asyncio
    async def test_no_authorization_header_without_key(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": {},
        })
        post_mock = AsyncMock(return_value=mock_resp)

        class FakeClient:
            async def __aenter__(self):
                self.post = post_mock
                return self

            async def __aexit__(self, *exc):
                return None

        provider = OpenAICompatProvider()

        with patch("services.llm_providers.openai_compat.httpx.AsyncClient", return_value=FakeClient()):
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="x",
                _provider_config={"base_url": "http://localhost:11434/v1"},
            )

        headers = post_mock.await_args.kwargs["headers"]
        assert "Authorization" not in headers

    @pytest.mark.asyncio
    async def test_default_base_url_when_config_missing(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
            "usage": {},
        })
        post_mock = AsyncMock(return_value=mock_resp)

        class FakeClient:
            async def __aenter__(self):
                self.post = post_mock
                return self

            async def __aexit__(self, *exc):
                return None

        provider = OpenAICompatProvider()

        with patch("services.llm_providers.openai_compat.httpx.AsyncClient", return_value=FakeClient()):
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="x",
            )

        url = post_mock.await_args.args[0]
        assert url.endswith("/v1/chat/completions")


class TestOpenAICompatEmbed:
    @pytest.mark.asyncio
    async def test_calls_v1_embeddings_endpoint(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "data": [{"embedding": [0.1, 0.2, 0.3]}],
            "model": "nomic-embed-text",
        })
        post_mock = AsyncMock(return_value=mock_resp)

        class FakeClient:
            async def __aenter__(self):
                self.post = post_mock
                return self

            async def __aexit__(self, *exc):
                return None

        provider = OpenAICompatProvider()

        with patch("services.llm_providers.openai_compat.httpx.AsyncClient", return_value=FakeClient()):
            vec = await provider.embed("hello", model="nomic-embed-text")

        assert vec == [0.1, 0.2, 0.3]
        url = post_mock.await_args.args[0]
        assert url.endswith("/v1/embeddings")

    @pytest.mark.asyncio
    async def test_raises_on_empty_response(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"data": []})
        post_mock = AsyncMock(return_value=mock_resp)

        class FakeClient:
            async def __aenter__(self):
                self.post = post_mock
                return self

            async def __aexit__(self, *exc):
                return None

        provider = OpenAICompatProvider()

        with patch("services.llm_providers.openai_compat.httpx.AsyncClient", return_value=FakeClient()):
            with pytest.raises(ValueError, match="embed response"):
                await provider.embed("hello", model="x")


# ---------------------------------------------------------------------------
# OllamaNativeProvider — delegation coverage
# ---------------------------------------------------------------------------


class TestOllamaNativeDelegation:
    @pytest.mark.asyncio
    async def test_complete_converts_messages_to_prompt_system_split(self):
        provider = OllamaNativeProvider()

        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value={
            "response": "ok",
            "model": "gemma3:27b",
            "prompt_eval_count": 10,
            "eval_count": 5,
            "done_reason": "stop",
        })
        provider._client = mock_client

        completion = await provider.complete(
            messages=[
                {"role": "system", "content": "you are a helper"},
                {"role": "user", "content": "hi"},
            ],
            model="gemma3:27b",
            temperature=0.5,
        )

        assert completion.text == "ok"
        assert completion.prompt_tokens == 10
        assert completion.completion_tokens == 5
        assert completion.total_tokens == 15

        # Verify system and user messages were split correctly.
        call = mock_client.generate.await_args.kwargs
        assert call["system"] == "you are a helper"
        assert "hi" in call["prompt"]
        assert call["model"] == "gemma3:27b"
        assert call["temperature"] == 0.5

    @pytest.mark.asyncio
    async def test_embed_delegates_to_client(self):
        provider = OllamaNativeProvider()

        mock_client = MagicMock()
        mock_client.embed = AsyncMock(return_value=[0.5, 0.6])
        provider._client = mock_client

        vec = await provider.embed("some text", model="nomic-embed-text")

        assert vec == [0.5, 0.6]
        mock_client.embed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_complete_forwards_timeout_s_kwarg(self):
        """v2.1: ``timeout_s`` kwarg should flow through to
        OllamaClient.generate(timeout=...) so callers can pin a
        per-call timeout without constructing a new client."""
        provider = OllamaNativeProvider()
        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value={
            "response": "ok", "model": "gemma3:27b",
            "prompt_eval_count": 1, "eval_count": 1, "done_reason": "stop",
        })
        provider._client = mock_client

        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="gemma3:27b",
            timeout_s=90,
        )
        call = mock_client.generate.await_args.kwargs
        assert call["timeout"] == 90

    @pytest.mark.asyncio
    async def test_complete_omits_timeout_when_not_set(self):
        """When caller doesn't pass ``timeout_s``, the provider should
        pass ``timeout=None`` so OllamaClient uses its configured
        default (600s)."""
        provider = OllamaNativeProvider()
        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value={
            "response": "ok", "model": "gemma3:27b",
            "prompt_eval_count": 1, "eval_count": 1, "done_reason": "stop",
        })
        provider._client = mock_client

        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="gemma3:27b",
        )
        call = mock_client.generate.await_args.kwargs
        assert call["timeout"] is None


class TestOpenAICompatTimeoutPrecedence:
    """v2.1: ``timeout_s`` kwarg should override config.timeout_seconds."""

    @pytest.mark.asyncio
    async def test_kwarg_beats_provider_config(self):
        provider = OpenAICompatProvider()
        cfg = provider._resolve_config({
            "_provider_config": {"timeout_seconds": 200},
            "timeout_s": 15,
        })
        assert cfg["timeout"] == 15

    @pytest.mark.asyncio
    async def test_config_used_when_no_kwarg(self):
        provider = OpenAICompatProvider()
        cfg = provider._resolve_config({
            "_provider_config": {"timeout_seconds": 200},
        })
        assert cfg["timeout"] == 200

    @pytest.mark.asyncio
    async def test_default_when_neither_set(self):
        provider = OpenAICompatProvider()
        cfg = provider._resolve_config({})
        assert cfg["timeout"] == 120  # default
