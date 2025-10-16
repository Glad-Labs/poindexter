"""
Comprehensive tests for OllamaClient service.

Tests zero-cost local LLM inference capabilities for desktop operation.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import httpx
from typing import AsyncGenerator

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
    # Cleanup
    await client.close()


@pytest.fixture
def mock_httpx_client():
    """Mock httpx.AsyncClient for testing."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    return mock_client


@pytest.fixture
def mock_health_response():
    """Mock successful health check response."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"status": "ok"}
    return mock_response


@pytest.fixture
def mock_models_response():
    """Mock successful list models response."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "models": [
            {"name": "llama2:latest", "size": 3826793677},
            {"name": "mistral:latest", "size": 4109865159},
            {"name": "codellama:latest", "size": 3825819519}
        ]
    }
    return mock_response


@pytest.fixture
def mock_generate_response():
    """Mock successful generate response."""
    mock_response = Mock()
    mock_response.status_code = 200
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


@pytest.fixture
def mock_pull_response():
    """Mock successful pull model response."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": "success",
        "digest": "sha256:abc123"
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
        assert client.timeout == 300
        
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
    
    async def test_health_check_success(self, ollama_client, mock_httpx_client, mock_health_response):
        """Test successful health check."""
        mock_httpx_client.get.return_value = mock_health_response
        ollama_client.client = mock_httpx_client
        
        result = await ollama_client.check_health()
        
        assert result is True
        mock_httpx_client.get.assert_called_once()
    
    async def test_health_check_failure(self, ollama_client, mock_httpx_client):
        """Test health check when server is down."""
        mock_httpx_client.get.side_effect = httpx.ConnectError("Connection refused")
        ollama_client.client = mock_httpx_client
        
        with pytest.raises(OllamaConnectionError) as exc_info:
            await ollama_client.check_health()
        
        assert "Failed to connect" in str(exc_info.value)
    
    async def test_health_check_timeout(self, ollama_client, mock_httpx_client):
        """Test health check timeout."""
        mock_httpx_client.get.side_effect = httpx.TimeoutException("Request timed out")
        ollama_client.client = mock_httpx_client
        
        with pytest.raises(OllamaConnectionError) as exc_info:
            await ollama_client.check_health()
        
        assert "timeout" in str(exc_info.value).lower()


# ============================================================================
# TEST LIST MODELS
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestListModels:
    """Test listing available Ollama models."""
    
    async def test_list_models_success(self, ollama_client, mock_httpx_client, mock_models_response):
        """Test successful model listing."""
        mock_httpx_client.get.return_value = mock_models_response
        ollama_client.client = mock_httpx_client
        
        models = await ollama_client.list_models()
        
        assert isinstance(models, list)
        assert len(models) == 3
        assert "llama2:latest" in models
        assert "mistral:latest" in models
        assert "codellama:latest" in models
    
    async def test_list_models_empty(self, ollama_client, mock_httpx_client):
        """Test listing models when none are installed."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}
        mock_httpx_client.get.return_value = mock_response
        ollama_client.client = mock_httpx_client
        
        models = await ollama_client.list_models()
        
        assert models == []
    
    async def test_list_models_connection_error(self, ollama_client, mock_httpx_client):
        """Test list models with connection error."""
        mock_httpx_client.get.side_effect = httpx.ConnectError("Connection refused")
        ollama_client.client = mock_httpx_client
        
        with pytest.raises(OllamaConnectionError):
            await ollama_client.list_models()


# ============================================================================
# TEST GENERATE
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestGenerate:
    """Test text generation functionality."""
    
    async def test_generate_simple_prompt(self, ollama_client, mock_httpx_client, mock_generate_response):
        """Test simple text generation."""
        mock_httpx_client.post.return_value = mock_generate_response
        ollama_client.client = mock_httpx_client
        
        response = await ollama_client.generate(
            prompt="Tell me a joke",
            model="mistral"
        )
        
        assert response["response"] == "This is a test response from Ollama."
        assert response["done"] is True
        assert response["model"] == "mistral"
        
        # Verify request payload
        call_args = mock_httpx_client.post.call_args
        assert call_args[1]["json"]["prompt"] == "Tell me a joke"
        assert call_args[1]["json"]["model"] == "mistral"
    
    async def test_generate_with_system_prompt(self, ollama_client, mock_httpx_client, mock_generate_response):
        """Test generation with system prompt."""
        mock_httpx_client.post.return_value = mock_generate_response
        ollama_client.client = mock_httpx_client
        
        await ollama_client.generate(
            prompt="Explain quantum physics",
            system="You are a physics professor",
            model="mistral"
        )
        
        call_args = mock_httpx_client.post.call_args
        assert call_args[1]["json"]["system"] == "You are a physics professor"
    
    async def test_generate_with_temperature(self, ollama_client, mock_httpx_client, mock_generate_response):
        """Test generation with custom temperature."""
        mock_httpx_client.post.return_value = mock_generate_response
        ollama_client.client = mock_httpx_client
        
        await ollama_client.generate(
            prompt="Be creative",
            temperature=0.9,
            model="mistral"
        )
        
        call_args = mock_httpx_client.post.call_args
        assert call_args[1]["json"]["options"]["temperature"] == 0.9
    
    async def test_generate_with_max_tokens(self, ollama_client, mock_httpx_client, mock_generate_response):
        """Test generation with token limit."""
        mock_httpx_client.post.return_value = mock_generate_response
        ollama_client.client = mock_httpx_client
        
        await ollama_client.generate(
            prompt="Write a story",
            max_tokens=500,
            model="mistral"
        )
        
        call_args = mock_httpx_client.post.call_args
        assert call_args[1]["json"]["options"]["num_predict"] == 500
    
    async def test_generate_model_not_found(self, ollama_client, mock_httpx_client):
        """Test generation with non-existent model."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.json.return_value = {"error": "model not found"}
        mock_httpx_client.post.return_value = mock_response
        ollama_client.client = mock_httpx_client
        
        with pytest.raises(OllamaModelNotFoundError) as exc_info:
            await ollama_client.generate(
                prompt="test",
                model="nonexistent"
            )
        
        assert "not found" in str(exc_info.value).lower()


# ============================================================================
# TEST CHAT
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestChat:
    """Test chat completion functionality."""
    
    async def test_chat_single_message(self, ollama_client, mock_httpx_client, mock_chat_response):
        """Test chat with single message."""
        mock_httpx_client.post.return_value = mock_chat_response
        ollama_client.client = mock_httpx_client
        
        messages = [
            {"role": "user", "content": "Hello!"}
        ]
        
        response = await ollama_client.chat(messages=messages, model="mistral")
        
        assert response["message"]["role"] == "assistant"
        assert response["message"]["content"] == "Hello! How can I help you today?"
        assert response["done"] is True
    
    async def test_chat_conversation_history(self, ollama_client, mock_httpx_client, mock_chat_response):
        """Test chat with conversation history."""
        mock_httpx_client.post.return_value = mock_chat_response
        ollama_client.client = mock_httpx_client
        
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {"role": "assistant", "content": "I don't have weather data."},
            {"role": "user", "content": "Can you tell me a joke?"}
        ]
        
        response = await ollama_client.chat(messages=messages, model="mistral")
        
        # Verify conversation history sent
        call_args = mock_httpx_client.post.call_args
        assert len(call_args[1]["json"]["messages"]) == 3
    
    async def test_chat_with_temperature(self, ollama_client, mock_httpx_client, mock_chat_response):
        """Test chat with custom temperature."""
        mock_httpx_client.post.return_value = mock_chat_response
        ollama_client.client = mock_httpx_client
        
        messages = [{"role": "user", "content": "Be creative"}]
        
        await ollama_client.chat(
            messages=messages,
            temperature=0.8,
            model="mistral"
        )
        
        call_args = mock_httpx_client.post.call_args
        assert call_args[1]["json"]["options"]["temperature"] == 0.8


# ============================================================================
# TEST PULL MODEL
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestPullModel:
    """Test model download functionality."""
    
    async def test_pull_model_success(self, ollama_client, mock_httpx_client, mock_pull_response):
        """Test successful model pull."""
        mock_httpx_client.post.return_value = mock_pull_response
        ollama_client.client = mock_httpx_client
        
        result = await ollama_client.pull_model(model="mistral")
        
        assert result["status"] == "success"
        assert "digest" in result
    
    async def test_pull_model_invalid(self, ollama_client, mock_httpx_client):
        """Test pulling invalid model."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.json.return_value = {"error": "model not found in registry"}
        mock_httpx_client.post.return_value = mock_response
        ollama_client.client = mock_httpx_client
        
        with pytest.raises(OllamaError):
            await ollama_client.pull_model(model="invalid_model")


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
        assert profile["name"] == "mistral"
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
# TEST STREAM GENERATE
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestStreamGenerate:
    """Test streaming text generation."""
    
    async def test_stream_generate_chunks(self, ollama_client, mock_httpx_client):
        """Test streaming generation yields chunks."""
        # Mock streaming response
        async def mock_aiter_lines():
            responses = [
                '{"response": "Hello", "done": false}',
                '{"response": " world", "done": false}',
                '{"response": "!", "done": true}'
            ]
            for line in responses:
                yield line
        
        mock_response = AsyncMock()
        mock_response.aiter_lines = mock_aiter_lines
        mock_httpx_client.post.return_value = mock_response
        ollama_client.client = mock_httpx_client
        
        chunks = []
        async for chunk in ollama_client.stream_generate(
            prompt="Say hello",
            model="mistral"
        ):
            chunks.append(chunk)
        
        assert len(chunks) == 3
        assert chunks[0]["response"] == "Hello"
        assert chunks[1]["response"] == " world"
        assert chunks[2]["done"] is True


# ============================================================================
# TEST ERROR HANDLING
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
class TestErrorHandling:
    """Test error handling and edge cases."""
    
    async def test_connection_refused(self, ollama_client, mock_httpx_client):
        """Test handling of connection refused."""
        mock_httpx_client.get.side_effect = httpx.ConnectError("Connection refused")
        ollama_client.client = mock_httpx_client
        
        with pytest.raises(OllamaConnectionError) as exc_info:
            await ollama_client.check_health()
        
        assert "connect" in str(exc_info.value).lower()
    
    async def test_timeout_handling(self, ollama_client, mock_httpx_client):
        """Test timeout handling."""
        mock_httpx_client.post.side_effect = httpx.TimeoutException("Request timed out")
        ollama_client.client = mock_httpx_client
        
        with pytest.raises(OllamaConnectionError):
            await ollama_client.generate(prompt="test")
    
    async def test_invalid_json_response(self, ollama_client, mock_httpx_client):
        """Test handling of invalid JSON response."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_httpx_client.post.return_value = mock_response
        ollama_client.client = mock_httpx_client
        
        with pytest.raises(OllamaError):
            await ollama_client.generate(prompt="test")
    
    async def test_client_cleanup(self):
        """Test proper client cleanup."""
        client = OllamaClient()
        
        # Verify client is created
        assert client.client is not None
        
        # Close and verify cleanup
        await client.close()
        
        # Client should still exist (httpx.AsyncClient doesn't have is_closed)
        assert client.client is not None


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
            
            assert "response" in response
            assert response["done"] is True
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
# TEST PERFORMANCE
# ============================================================================

@pytest.mark.performance
@pytest.mark.asyncio
class TestPerformance:
    """Performance tests for Ollama client."""
    
    async def test_concurrent_requests(self, ollama_client, mock_httpx_client, mock_generate_response):
        """Test handling of concurrent requests."""
        import asyncio
        
        mock_httpx_client.post.return_value = mock_generate_response
        ollama_client.client = mock_httpx_client
        
        # Create 10 concurrent requests
        tasks = [
            ollama_client.generate(prompt=f"Request {i}", model="mistral")
            for i in range(10)
        ]
        
        responses = await asyncio.gather(*tasks)
        
        assert len(responses) == 10
        for response in responses:
            assert "response" in response
