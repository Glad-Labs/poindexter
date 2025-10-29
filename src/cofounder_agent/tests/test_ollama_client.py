"""
Comprehensive tests for OllamaClient service.

Tests zero-cost local LLM inference capabilities for desktop operation.

Strategy: Mock httpx.AsyncClient at the module level using @patch decorator,
preventing real HTTP calls during unit tests while maintaining integration test support.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import httpx

from ..services.ollama_client import (
    OllamaClient,
    OllamaError,
    OllamaConnectionError,
    OllamaModelNotFoundError,
    initialize_ollama_client,
    MODEL_PROFILES,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
async def ollama_client():
    """Create OllamaClient instance for testing."""
    client = OllamaClient(base_url="http://localhost:11434", model="mistral")
    yield client
    await client.close()


@pytest.fixture
def mock_health_response():
    """Mock successful health check response (API/tags endpoint)."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"status": "ok"}
    return mock_response


@pytest.fixture
def mock_models_response():
    """Mock successful list models response."""
    mock_response = Mock()
    mock_response.status_code = 200
    # Actual Ollama /api/tags response format
    mock_response.json.return_value = {
        "models": [
            {
                "name": "llama2:latest",
                "size": 3826793677,
                "modified_at": "2024-01-15T10:00:00Z"
            },
            {
                "name": "mistral:latest",
                "size": 4109865159,
                "modified_at": "2024-01-16T10:00:00Z"
            },
            {
                "name": "codellama:latest",
                "size": 3825819519,
                "modified_at": "2024-01-17T10:00:00Z"
            }
        ]
    }
    return mock_response


@pytest.fixture
def mock_generate_response():
    """Mock successful generate response."""
    mock_response = Mock()
    mock_response.status_code = 200
    # Actual Ollama /api/generate response format
    mock_response.json.return_value = {
        "model": "mistral",
        "response": "This is a test response from Ollama.",
        "done": True,
        "context": [1, 2, 3],
        "total_duration": 5000000000,
        "load_duration": 1000000000,
        "prompt_eval_count": 10,
        "eval_count": 20
    }
    return mock_response


@pytest.fixture
def mock_chat_response():
    """Mock successful chat response."""
    mock_response = Mock()
    mock_response.status_code = 200
    # Actual Ollama /api/chat response format
    mock_response.json.return_value = {
        "model": "mistral",
        "message": {
            "role": "assistant",
            "content": "Hello! How can I help you today?"
        },
        "done": True,
        "total_duration": 3000000000
    }
    return mock_response


# ============================================================================
# TEST OLLAMA CLIENT INITIALIZATION
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestOllamaClientInitialization:
    """Test OllamaClient initialization and configuration."""

    async def test_default_initialization(self):
        """Test OllamaClient with default settings."""
        client = OllamaClient()

        assert client.base_url == DEFAULT_BASE_URL
        assert client.model == DEFAULT_MODEL
        assert client.timeout == 120  # Actual default is 120, not 300

        await client.close()

    async def test_custom_initialization(self):
        """Test OllamaClient with custom settings."""
        client = OllamaClient(
            base_url="http://custom:8080",
            model="llama2:13b",
            timeout=60
        )

        assert client.base_url == "http://custom:8080"
        assert client.model == "llama2:13b"
        assert client.timeout == 60

        await client.close()

    async def test_factory_initialization(self):
        """Test initialize_ollama_client factory function."""
        client = await initialize_ollama_client(
            base_url="http://localhost:11434",
            model="mistral"
        )

        assert isinstance(client, OllamaClient)
        assert client.model == "mistral"

        await client.close()


# ============================================================================
# TEST HEALTH CHECK
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestHealthCheck:
    """Test Ollama server health check functionality."""

    @patch("src.cofounder_agent.services.ollama_client.httpx.AsyncClient")
    async def test_health_check_success(self, mock_async_client_class, ollama_client, mock_health_response):
        """Test successful health check with mocked HTTP."""
        # Setup mock
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.get.return_value = mock_health_response

        # Execute
        result = await ollama_client.check_health()

        # Verify - health check returns True when status 200
        assert result is True
        mock_client_instance.get.assert_called_once()

    @patch("src.cofounder_agent.services.ollama_client.httpx.AsyncClient")
    async def test_health_check_failure(self, mock_async_client_class, ollama_client):
        """Test health check when server is down."""
        # Setup mock to raise connection error
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.get.side_effect = httpx.ConnectError("Connection refused")

        # Execute & verify - returns False on connection error (no exception raised)
        result = await ollama_client.check_health()
        assert result is False

    @patch("src.cofounder_agent.services.ollama_client.httpx.AsyncClient")
    async def test_health_check_timeout(self, mock_async_client_class, ollama_client):
        """Test health check timeout."""
        # Setup mock to raise timeout
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.get.side_effect = httpx.TimeoutException("Request timed out")

        # Execute & verify - returns False on timeout
        result = await ollama_client.check_health()
        assert result is False


# ============================================================================
# TEST LIST MODELS
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestListModels:
    """Test listing available Ollama models."""

    @patch("src.cofounder_agent.services.ollama_client.httpx.AsyncClient")
    async def test_list_models_success(self, mock_async_client_class, ollama_client, mock_models_response):
        """Test successful model listing."""
        # Setup mock
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.get.return_value = mock_models_response

        # Execute
        models = await ollama_client.list_models()

        # Verify - should be list of model dicts
        assert isinstance(models, list)
        assert len(models) == 3
        assert models[0]["name"] == "llama2:latest"
        assert models[1]["name"] == "mistral:latest"
        assert models[2]["name"] == "codellama:latest"

    @patch("src.cofounder_agent.services.ollama_client.httpx.AsyncClient")
    async def test_list_models_empty(self, mock_async_client_class, ollama_client):
        """Test listing models when none are installed."""
        # Setup mock to return empty models list
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}
        
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.get.return_value = mock_response

        # Execute
        models = await ollama_client.list_models()

        # Verify
        assert models == []

    @patch("src.cofounder_agent.services.ollama_client.httpx.AsyncClient")
    async def test_list_models_connection_error(self, mock_async_client_class, ollama_client):
        """Test list models with connection error."""
        # Setup mock to raise connection error
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.get.side_effect = httpx.ConnectError("Connection refused")

        # Execute
        models = await ollama_client.list_models()

        # Verify - returns empty list on error (no exception raised)
        assert models == []


# ============================================================================
# TEST GENERATE
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestGenerate:
    """Test text generation functionality."""

    @patch("src.cofounder_agent.services.ollama_client.httpx.AsyncClient")
    async def test_generate_simple_prompt(self, mock_async_client_class, ollama_client, mock_generate_response):
        """Test simple text generation."""
        # Setup mock
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.post.return_value = mock_generate_response

        # Execute
        response = await ollama_client.generate(
            prompt="Tell me a joke",
            model="mistral"
        )

        # Verify - response should be mapped to our format
        assert response["text"] == "This is a test response from Ollama."
        assert response["done"] is True
        assert response["model"] == "mistral"
        assert response["cost"] == 0.0  # Ollama is free!

    @patch("src.cofounder_agent.services.ollama_client.httpx.AsyncClient")
    async def test_generate_with_system_prompt(self, mock_async_client_class, ollama_client, mock_generate_response):
        """Test generation with system prompt."""
        # Setup mock
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.post.return_value = mock_generate_response

        # Execute
        await ollama_client.generate(
            prompt="Explain quantum physics",
            system="You are a physics professor",
            model="mistral"
        )

        # Verify - system prompt should be in request
        call_args = mock_client_instance.post.call_args
        assert call_args[1]["json"]["system"] == "You are a physics professor"

    @patch("src.cofounder_agent.services.ollama_client.httpx.AsyncClient")
    async def test_generate_with_temperature(self, mock_async_client_class, ollama_client, mock_generate_response):
        """Test generation with custom temperature."""
        # Setup mock
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.post.return_value = mock_generate_response

        # Execute
        await ollama_client.generate(
            prompt="Be creative",
            temperature=0.9,
            model="mistral"
        )

        # Verify - temperature should be in request options
        call_args = mock_client_instance.post.call_args
        assert call_args[1]["json"]["options"]["temperature"] == 0.9

    @patch("src.cofounder_agent.services.ollama_client.httpx.AsyncClient")
    async def test_generate_with_max_tokens(self, mock_async_client_class, ollama_client, mock_generate_response):
        """Test generation with token limit."""
        # Setup mock
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.post.return_value = mock_generate_response

        # Execute
        await ollama_client.generate(
            prompt="Write a story",
            max_tokens=500,
            model="mistral"
        )

        # Verify - max_tokens should map to num_predict
        call_args = mock_client_instance.post.call_args
        assert call_args[1]["json"]["options"]["num_predict"] == 500


# ============================================================================
# TEST CHAT
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestChat:
    """Test chat completion functionality."""

    @patch("src.cofounder_agent.services.ollama_client.httpx.AsyncClient")
    async def test_chat_single_message(self, mock_async_client_class, ollama_client, mock_chat_response):
        """Test chat with single message."""
        # Setup mock
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.post.return_value = mock_chat_response

        # Execute
        messages = [{"role": "user", "content": "Hello!"}]
        response = await ollama_client.chat(messages=messages, model="mistral")

        # Verify - chat response has role/content at top level
        assert response["role"] == "assistant"
        assert response["content"] == "Hello! How can I help you today?"
        assert response["done"] is True
        assert response["cost"] == 0.0

    @patch("src.cofounder_agent.services.ollama_client.httpx.AsyncClient")
    async def test_chat_conversation_history(self, mock_async_client_class, ollama_client, mock_chat_response):
        """Test chat with conversation history."""
        # Setup mock
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.post.return_value = mock_chat_response

        # Execute
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {"role": "assistant", "content": "I don't have weather data."},
            {"role": "user", "content": "Can you tell me a joke?"}
        ]
        response = await ollama_client.chat(messages=messages, model="mistral")

        # Verify - full conversation history sent
        call_args = mock_client_instance.post.call_args
        assert len(call_args[1]["json"]["messages"]) == 3

    @patch("src.cofounder_agent.services.ollama_client.httpx.AsyncClient")
    async def test_chat_with_temperature(self, mock_async_client_class, ollama_client, mock_chat_response):
        """Test chat with custom temperature."""
        # Setup mock
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.post.return_value = mock_chat_response

        # Execute
        messages = [{"role": "user", "content": "Be creative"}]
        await ollama_client.chat(
            messages=messages,
            temperature=0.8,
            model="mistral"
        )

        # Verify
        call_args = mock_client_instance.post.call_args
        assert call_args[1]["json"]["options"]["temperature"] == 0.8


# ============================================================================
# TEST MODEL PROFILES
# ============================================================================

@pytest.mark.unit
class TestModelProfiles:
    """Test model profile and recommendation functionality."""

    def test_get_model_profile_existing(self, ollama_client):
        """Test getting profile for existing model."""
        profile = ollama_client.get_model_profile("mistral")

        assert profile is not None
        # Profile keys are from MODEL_PROFILES dict
        assert "size" in profile
        assert "speed" in profile
        assert "quality" in profile
        assert profile["cost"] == 0.0

    def test_get_model_profile_nonexistent(self, ollama_client):
        """Test getting profile for non-existent model."""
        profile = ollama_client.get_model_profile("nonexistent")

        assert profile is None

    def test_recommend_model_code_task(self, ollama_client):
        """Test model recommendation for code tasks."""
        recommendation = ollama_client.recommend_model(task_type="code")

        assert recommendation == "codellama"

    def test_recommend_model_simple_task(self, ollama_client):
        """Test model recommendation for simple tasks."""
        recommendation = ollama_client.recommend_model(task_type="simple")

        assert recommendation == "phi"

    def test_recommend_model_complex_task(self, ollama_client):
        """Test model recommendation for complex tasks."""
        recommendation = ollama_client.recommend_model(task_type="complex")

        assert recommendation == "mixtral"

    def test_recommend_model_default(self, ollama_client):
        """Test default model recommendation."""
        recommendation = ollama_client.recommend_model(task_type="unknown")

        assert recommendation == "mistral"

    def test_all_model_profiles_have_zero_cost(self):
        """Verify all Ollama models are FREE."""
        for model_name, profile in MODEL_PROFILES.items():
            assert profile["cost"] == 0.0, f"{model_name} should be free"


# ============================================================================
# TEST INTEGRATION SCENARIOS
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.slow
class TestIntegrationScenarios:
    """Integration tests requiring actual Ollama server (marked slow)."""

    @pytest.mark.skip(reason="Requires running Ollama server")
    async def test_real_health_check(self):
        """Test actual health check against real Ollama server."""
        client = OllamaClient()

        try:
            is_healthy = await client.check_health()
            assert is_healthy is True
        finally:
            await client.close()

    @pytest.mark.skip(reason="Requires running Ollama server with models")
    async def test_real_generation(self):
        """Test actual generation against real Ollama server."""
        client = OllamaClient(model="mistral")

        try:
            response = await client.generate(
                prompt="Say 'test' and nothing else",
                max_tokens=10
            )

            assert "text" in response
            assert response["done"] is True
            assert response["cost"] == 0.0
        finally:
            await client.close()

    @pytest.mark.skip(reason="Requires running Ollama server")
    async def test_real_model_listing(self):
        """Test actual model listing against real Ollama server."""
        client = OllamaClient()

        try:
            models = await client.list_models()
            assert isinstance(models, list)
        finally:
            await client.close()


# ============================================================================
# TEST ERROR HANDLING
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestErrorHandling:
    """Test error handling and edge cases."""

    async def test_client_cleanup(self):
        """Test proper client cleanup."""
        client = OllamaClient()

        # Verify client is created
        assert client.client is not None

        # Close and verify cleanup works
        await client.close()

        # Client reference should still exist (httpx.AsyncClient.aclose() works)
        assert client.client is not None
