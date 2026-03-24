"""
Unit tests for services/ollama_client.py

Tests OllamaClient initialization, health check, list_models, generate, chat,
pull_model, model profiles, model recommendation, and retry logic.
HTTP calls are mocked to avoid requiring a real Ollama server.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.ollama_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    MODEL_PROFILES,
    OllamaClient,
    OllamaConnectionError,
    OllamaError,
    OllamaModelNotFoundError,
    initialize_ollama_client,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    c = OllamaClient()
    # Replace the real httpx.AsyncClient with a mock so no network calls are made
    c.client = AsyncMock(spec=httpx.AsyncClient)
    return c


def make_mock_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


SAMPLE_GENERATE_RESPONSE = {
    "response": "This is the generated text.",
    "eval_count": 50,
    "prompt_eval_count": 10,
    "total_duration": 2_000_000_000,  # 2 seconds in ns
    "done": True,
}


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestOllamaClientInit:
    def test_default_base_url(self):
        c = OllamaClient()
        assert c.base_url == DEFAULT_BASE_URL

    def test_default_model(self):
        c = OllamaClient()
        assert c.model == DEFAULT_MODEL

    def test_custom_base_url(self):
        c = OllamaClient(base_url="http://remote:11434")
        assert c.base_url == "http://remote:11434"

    def test_custom_model(self):
        c = OllamaClient(model="mistral")
        assert c.model == "mistral"

    def test_custom_timeout(self):
        c = OllamaClient(timeout=60)
        assert c.timeout == 60


# ---------------------------------------------------------------------------
# check_health
# ---------------------------------------------------------------------------


class TestOllamaCheckHealth:
    @pytest.mark.asyncio
    async def test_healthy_returns_true(self, client):
        mock_resp = make_mock_response({"models": []}, status_code=200)
        client.client.get = AsyncMock(return_value=mock_resp)

        result = await client.check_health()
        assert result is True

    @pytest.mark.asyncio
    async def test_unhealthy_status_returns_false(self, client):
        mock_resp = make_mock_response({}, status_code=500)
        client.client.get = AsyncMock(return_value=mock_resp)

        result = await client.check_health()
        assert result is False

    @pytest.mark.asyncio
    async def test_connection_error_returns_false(self, client):
        client.client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        result = await client.check_health()
        assert result is False


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------


class TestOllamaListModels:
    @pytest.mark.asyncio
    async def test_returns_model_list(self, client):
        models = [{"name": "llama2"}, {"name": "mistral"}]
        mock_resp = make_mock_response({"models": models})
        client.client.get = AsyncMock(return_value=mock_resp)

        result = await client.list_models()
        assert result == models

    @pytest.mark.asyncio
    async def test_error_returns_empty_list(self, client):
        client.client.get = AsyncMock(side_effect=Exception("API down"))

        result = await client.list_models()
        assert result == []


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


class TestOllamaGenerate:
    @pytest.mark.asyncio
    async def test_successful_generation(self, client):
        mock_resp = make_mock_response(SAMPLE_GENERATE_RESPONSE)
        client.client.post = AsyncMock(return_value=mock_resp)

        result = await client.generate("Write a poem")

        assert result["text"] == "This is the generated text."
        assert result["tokens"] == 50
        assert result["cost"] == 0.0
        assert result["done"] is True

    @pytest.mark.asyncio
    async def test_uses_specified_model(self, client):
        mock_resp = make_mock_response(SAMPLE_GENERATE_RESPONSE)
        client.client.post = AsyncMock(return_value=mock_resp)

        await client.generate("Test", model="mistral")

        call_kwargs = client.client.post.call_args[1]
        assert call_kwargs["json"]["model"] == "mistral"

    @pytest.mark.asyncio
    async def test_system_prompt_included(self, client):
        mock_resp = make_mock_response(SAMPLE_GENERATE_RESPONSE)
        client.client.post = AsyncMock(return_value=mock_resp)

        await client.generate("Test", system="You are a helpful assistant.")

        call_kwargs = client.client.post.call_args[1]
        assert call_kwargs["json"]["system"] == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_max_tokens_included(self, client):
        mock_resp = make_mock_response(SAMPLE_GENERATE_RESPONSE)
        client.client.post = AsyncMock(return_value=mock_resp)

        await client.generate("Test", max_tokens=500)

        call_kwargs = client.client.post.call_args[1]
        assert call_kwargs["json"]["options"]["num_predict"] == 500

    @pytest.mark.asyncio
    async def test_http_error_raised(self, client):
        client.client.post = AsyncMock(
            side_effect=httpx.HTTPError("404 model not found")
        )

        with pytest.raises(httpx.HTTPError):
            await client.generate("Test")

    @pytest.mark.asyncio
    async def test_duration_converted_from_nanoseconds(self, client):
        mock_resp = make_mock_response(SAMPLE_GENERATE_RESPONSE)
        client.client.post = AsyncMock(return_value=mock_resp)

        result = await client.generate("Test")

        # 2_000_000_000 ns = 2.0 seconds
        assert abs(result["duration_seconds"] - 2.0) < 0.001


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


class TestOllamaChat:
    @pytest.mark.asyncio
    async def test_chat_returns_assistant_content(self, client):
        mock_resp = make_mock_response({
            **SAMPLE_GENERATE_RESPONSE,
            "response": "Assistant: I'm here to help.",
        })
        client.client.post = AsyncMock(return_value=mock_resp)

        result = await client.chat([
            {"role": "user", "content": "Hello!"}
        ])

        assert result["role"] == "assistant"
        assert "here to help" in result["content"]

    @pytest.mark.asyncio
    async def test_chat_cost_is_zero(self, client):
        mock_resp = make_mock_response(SAMPLE_GENERATE_RESPONSE)
        client.client.post = AsyncMock(return_value=mock_resp)

        result = await client.chat([{"role": "user", "content": "Hi"}])

        assert result["cost"] == 0.0

    @pytest.mark.asyncio
    async def test_chat_formats_multi_turn_messages(self, client):
        mock_resp = make_mock_response(SAMPLE_GENERATE_RESPONSE)
        client.client.post = AsyncMock(return_value=mock_resp)

        await client.chat([
            {"role": "user", "content": "What is AI?"},
            {"role": "assistant", "content": "AI is..."},
            {"role": "user", "content": "Tell me more."},
        ])

        call_kwargs = client.client.post.call_args[1]
        prompt = call_kwargs["json"]["prompt"]
        assert "User: What is AI?" in prompt
        assert "Assistant: AI is..." in prompt
        assert "User: Tell me more." in prompt


# ---------------------------------------------------------------------------
# pull_model
# ---------------------------------------------------------------------------


class TestOllamaPullModel:
    @pytest.mark.asyncio
    async def test_successful_pull(self, client):
        mock_resp = make_mock_response({})
        client.client.post = AsyncMock(return_value=mock_resp)

        result = await client.pull_model("llama2")
        assert result is True

    @pytest.mark.asyncio
    async def test_failed_pull_returns_false(self, client):
        client.client.post = AsyncMock(side_effect=Exception("Network error"))

        result = await client.pull_model("nonexistent")
        assert result is False


# ---------------------------------------------------------------------------
# get_model_profile / recommend_model
# ---------------------------------------------------------------------------


class TestOllamaModelProfiles:
    def test_known_model_profile(self):
        c = OllamaClient()
        profile = c.get_model_profile("llama2")
        assert profile is not None
        assert profile["cost"] == 0.0
        assert "use_cases" in profile

    def test_tagged_model_strips_tag(self):
        c = OllamaClient()
        profile = c.get_model_profile("llama2:13b")
        assert profile is not None  # Base "llama2" profile found

    def test_unknown_model_returns_none(self):
        c = OllamaClient()
        profile = c.get_model_profile("gpt-4")
        assert profile is None

    def test_model_profiles_all_have_cost_zero(self):
        for model_name, profile in MODEL_PROFILES.items():
            assert profile["cost"] == 0.0


class TestOllamaRecommendModel:
    def test_code_task_returns_codellama(self):
        c = OllamaClient()
        assert c.recommend_model("debug Python code") == "codellama"

    def test_simple_task_returns_phi(self):
        c = OllamaClient()
        assert c.recommend_model("classify email") == "phi"

    def test_complex_task_returns_mixtral(self):
        c = OllamaClient()
        assert c.recommend_model("complex reasoning task") == "mixtral"

    def test_default_returns_mistral(self):
        c = OllamaClient()
        assert c.recommend_model("write a blog post") == "mistral"

    def test_case_insensitive_matching(self):
        c = OllamaClient()
        assert c.recommend_model("CODE GENERATION") == "codellama"


# ---------------------------------------------------------------------------
# generate_with_retry
# ---------------------------------------------------------------------------


class TestOllamaGenerateWithRetry:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self, client):
        # generate() returns a dict with "text" key (not "response")
        expected = {"text": "Success", "tokens": 10, "cost": 0.0, "done": True}
        with patch.object(client, "generate", new=AsyncMock(return_value=expected)):
            result = await client.generate_with_retry("Test", max_retries=3, base_delay=0.0)
        assert result["text"] == "Success"

    @pytest.mark.asyncio
    async def test_retries_on_connect_error_then_succeeds(self, client):
        success_result = {"text": "Done!", "tokens": 10, "cost": 0.0, "done": True}
        responses = [
            httpx.ConnectError("refused"),
            httpx.ConnectError("refused"),
            success_result,
        ]
        call_count = 0

        async def mock_generate(*args, **kwargs):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            if isinstance(resp, Exception):
                raise resp
            return resp

        with patch.object(client, "generate", side_effect=mock_generate):
            with patch("asyncio.sleep", new=AsyncMock()):
                result = await client.generate_with_retry("Test", max_retries=3, base_delay=0.0)

        assert result["text"] == "Done!"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_exhausted(self, client):
        with patch.object(
            client, "generate", new=AsyncMock(side_effect=httpx.ConnectError("refused"))
        ):
            with patch("asyncio.sleep", new=AsyncMock()):
                with pytest.raises(httpx.ConnectError):
                    await client.generate_with_retry("Test", max_retries=2, base_delay=0.0)


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestOllamaClientClose:
    @pytest.mark.asyncio
    async def test_close_calls_aclose(self):
        c = OllamaClient()
        c.client = AsyncMock()
        c.client.aclose = AsyncMock()
        await c.close()
        c.client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# initialize_ollama_client
# ---------------------------------------------------------------------------


class TestInitializeOllamaClient:
    @pytest.mark.asyncio
    async def test_returns_ollama_client_instance(self):
        client = await initialize_ollama_client()
        assert isinstance(client, OllamaClient)
        await client.close()

    @pytest.mark.asyncio
    async def test_custom_parameters(self):
        client = await initialize_ollama_client(
            base_url="http://remote:11434", model="codellama"
        )
        assert client.base_url == "http://remote:11434"
        assert client.model == "codellama"
        await client.close()
