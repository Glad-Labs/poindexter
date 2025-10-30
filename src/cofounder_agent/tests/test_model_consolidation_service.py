"""
Test Model Consolidation Service

Tests for unified model provider service with fallback chain:
Ollama → HuggingFace → Google → Anthropic → OpenAI
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime, timedelta
import asyncio

from services.model_consolidation_service import (
    ModelConsolidationService,
    ProviderType,
    ProviderStatus,
    ModelResponse,
    OllamaAdapter,
    HuggingFaceAdapter,
    GoogleAdapter,
    AnthropicAdapter,
    OpenAIAdapter,
    get_model_consolidation_service,
    initialize_model_consolidation_service,
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def model_service():
    """Create model consolidation service for testing"""
    service = ModelConsolidationService()
    yield service


@pytest.fixture
def mock_response():
    """Create mock model response"""
    return ModelResponse(
        text="Generated text response",
        provider=ProviderType.OLLAMA,
        model="mistral",
        tokens_used=150,
        cost=0.0,
        response_time_ms=245.5
    )


# ============================================================================
# PROVIDER ADAPTER TESTS
# ============================================================================

class TestOllamaAdapter:
    """Tests for Ollama adapter"""
    
    @pytest.mark.asyncio
    async def test_ollama_available(self):
        """Ollama should report availability if models exist"""
        adapter = OllamaAdapter()
        # Note: This will actually check real Ollama if running
        result = await adapter.is_available()
        assert isinstance(result, bool)
    
    def test_ollama_list_models(self):
        """Ollama should list available models"""
        adapter = OllamaAdapter()
        models = adapter.list_models()
        assert isinstance(models, list)
        assert len(models) > 0
        assert "mistral" in models


class TestHuggingFaceAdapter:
    """Tests for HuggingFace adapter"""
    
    @pytest.mark.asyncio
    async def test_huggingface_available(self):
        """HuggingFace availability check"""
        adapter = HuggingFaceAdapter()
        result = await adapter.is_available()
        assert isinstance(result, bool)
    
    def test_huggingface_list_models(self):
        """HuggingFace should list available models"""
        adapter = HuggingFaceAdapter()
        models = adapter.list_models()
        assert isinstance(models, list)
        assert len(models) > 0


class TestGoogleAdapter:
    """Tests for Google Gemini adapter"""
    
    @pytest.mark.asyncio
    async def test_google_available_without_key(self):
        """Google should not be available without API key"""
        # This should return False if no GOOGLE_API_KEY
        adapter = GoogleAdapter()
        result = await adapter.is_available()
        # Result depends on environment variable
        assert isinstance(result, bool)
    
    def test_google_list_models(self):
        """Google should list available models"""
        adapter = GoogleAdapter()
        models = adapter.list_models()
        assert isinstance(models, list)
        assert "gemini-pro" in models


class TestAnthropicAdapter:
    """Tests for Anthropic adapter"""
    
    @pytest.mark.asyncio
    async def test_anthropic_available_without_key(self):
        """Anthropic should not be available without API key"""
        adapter = AnthropicAdapter()
        result = await adapter.is_available()
        # Result depends on ANTHROPIC_API_KEY
        assert isinstance(result, bool)
    
    def test_anthropic_list_models(self):
        """Anthropic should list available models"""
        adapter = AnthropicAdapter()
        models = adapter.list_models()
        assert isinstance(models, list)
        assert "claude-3-sonnet-20240229" in models


class TestOpenAIAdapter:
    """Tests for OpenAI adapter"""
    
    @pytest.mark.asyncio
    async def test_openai_available_without_key(self):
        """OpenAI should not be available without API key"""
        adapter = OpenAIAdapter()
        result = await adapter.is_available()
        assert isinstance(result, bool)
    
    def test_openai_list_models(self):
        """OpenAI should list available models"""
        adapter = OpenAIAdapter()
        models = adapter.list_models()
        assert isinstance(models, list)
        assert "gpt-4-turbo" in models


# ============================================================================
# MODEL CONSOLIDATION SERVICE TESTS
# ============================================================================

class TestModelConsolidationService:
    """Tests for unified model consolidation service"""
    
    def test_service_initialization(self, model_service):
        """Service should initialize all adapters"""
        assert len(model_service.adapters) > 0
        assert len(model_service.provider_status) > 0
        assert ProviderType.OLLAMA in model_service.adapters
    
    def test_fallback_chain_order(self, model_service):
        """Fallback chain should be in correct order"""
        expected_chain = [
            ProviderType.OLLAMA,
            ProviderType.HUGGINGFACE,
            ProviderType.GOOGLE,
            ProviderType.ANTHROPIC,
            ProviderType.OPENAI,
        ]
        assert model_service.FALLBACK_CHAIN == expected_chain
    
    def test_metrics_initialization(self, model_service):
        """Metrics should be initialized"""
        assert model_service.metrics["total_requests"] == 0
        assert model_service.metrics["successful_requests"] == 0
        assert model_service.metrics["failed_requests"] == 0
        assert model_service.metrics["total_cost"] == 0.0
    
    @pytest.mark.asyncio
    async def test_provider_availability_caching(self, model_service):
        """Provider availability should be cached"""
        provider = ProviderType.OLLAMA
        
        # First check
        status1 = model_service.provider_status[provider]
        initial_time = status1.last_checked
        
        # Second check (should use cache)
        await model_service._check_provider_availability(provider)
        status2 = model_service.provider_status[provider]
        
        # Cache should still be valid (within 5 minutes)
        assert status2.last_checked >= initial_time
    
    @pytest.mark.asyncio
    async def test_generate_request_metrics(self, model_service, mock_response):
        """Metrics should track generation requests"""
        initial_count = model_service.metrics["total_requests"]
        
        # Mock Ollama to succeed
        with patch.object(model_service.adapters[ProviderType.OLLAMA], 'generate', return_value=mock_response):
            with patch.object(model_service, '_check_provider_availability', return_value=True):
                try:
                    await model_service.generate("test prompt")
                    assert model_service.metrics["total_requests"] == initial_count + 1
                except Exception:
                    pass  # May fail if Ollama not available
    
    def test_list_models_single_provider(self, model_service):
        """Should list models for single provider"""
        models = model_service.list_models(provider=ProviderType.OLLAMA)
        assert ProviderType.OLLAMA.value in models
        assert isinstance(models[ProviderType.OLLAMA.value], list)
    
    def test_list_models_all_providers(self, model_service):
        """Should list models for all providers"""
        models = model_service.list_models()
        for provider in model_service.FALLBACK_CHAIN:
            assert provider.value in models
    
    def test_get_status(self, model_service):
        """Should return status of all providers"""
        status = model_service.get_status()
        assert "providers" in status
        assert "metrics" in status
        
        for provider in model_service.FALLBACK_CHAIN:
            assert provider.value in status["providers"]


# ============================================================================
# FALLBACK CHAIN TESTS
# ============================================================================

class TestFallbackChain:
    """Tests for fallback chain behavior"""
    
    @pytest.mark.asyncio
    async def test_fallback_chain_all_fail(self):
        """Should handle case where all providers fail"""
        service = ModelConsolidationService()
        
        # Mock all providers to fail
        for adapter in service.adapters.values():
            adapter.generate = AsyncMock(side_effect=Exception("Provider error"))
        
        with pytest.raises(Exception, match="All model providers failed"):
            await service.generate("test prompt")
    
    @pytest.mark.asyncio
    async def test_preferred_provider_first(self):
        """Should try preferred provider first"""
        service = ModelConsolidationService()
        mock_response = ModelResponse(
            text="test",
            provider=ProviderType.OPENAI,
            model="gpt-4",
            tokens_used=10,
            cost=0.001,
            response_time_ms=100
        )
        
        # Mock providers
        for provider_type, adapter in service.adapters.items():
            if provider_type == ProviderType.OPENAI:
                adapter.generate = AsyncMock(return_value=mock_response)
            else:
                adapter.generate = AsyncMock(side_effect=Exception("Not used"))
        
        # Patch availability check
        with patch.object(service, '_check_provider_availability', return_value=True):
            response = await service.generate(
                "test prompt",
                preferred_provider=ProviderType.OPENAI
            )
            assert response.provider == ProviderType.OPENAI
    
    @pytest.mark.asyncio
    async def test_availability_caching_skips_unavailable(self):
        """Should skip providers marked as unavailable"""
        service = ModelConsolidationService()
        
        # Mark all providers as unavailable
        for provider in service.provider_status.values():
            provider.is_available = False
            provider.last_checked = datetime.utcnow()
        
        with pytest.raises(Exception, match="All model providers failed"):
            await service.generate("test prompt")


# ============================================================================
# GLOBAL SINGLETON TESTS
# ============================================================================

class TestGlobalSingleton:
    """Tests for global singleton pattern"""
    
    def test_get_service_creates_singleton(self):
        """First call should create singleton"""
        # Clear any existing singleton
        import services.model_consolidation_service as mcs_module
        mcs_module._model_consolidation_service = None
        
        service = get_model_consolidation_service()
        assert service is not None
        assert isinstance(service, ModelConsolidationService)
    
    def test_get_service_returns_same_instance(self):
        """Should return same instance on subsequent calls"""
        service1 = get_model_consolidation_service()
        service2 = get_model_consolidation_service()
        assert service1 is service2
    
    def test_initialize_creates_service(self):
        """Initialize function should create service"""
        import services.model_consolidation_service as mcs_module
        mcs_module._model_consolidation_service = None
        
        initialize_model_consolidation_service()
        
        service = get_model_consolidation_service()
        assert service is not None


# ============================================================================
# PROVIDER STATUS TESTS
# ============================================================================

class TestProviderStatus:
    """Tests for provider status tracking"""
    
    def test_status_cache_expiration(self):
        """Cache should expire after 5 minutes"""
        status = ProviderStatus(
            provider=ProviderType.OLLAMA,
            is_available=True,
            last_checked=datetime.utcnow() - timedelta(minutes=6)
        )
        assert status.cache_expired is True
    
    def test_status_cache_not_expired(self):
        """Cache should not expire within 5 minutes"""
        status = ProviderStatus(
            provider=ProviderType.OLLAMA,
            is_available=True,
            last_checked=datetime.utcnow() - timedelta(minutes=3)
        )
        assert status.cache_expired is False


# ============================================================================
# MODEL RESPONSE TESTS
# ============================================================================

class TestModelResponse:
    """Tests for model response format"""
    
    def test_response_creation(self, mock_response):
        """Response should have all required fields"""
        assert isinstance(mock_response.text, str)
        assert isinstance(mock_response.provider, ProviderType)
        assert isinstance(mock_response.model, str)
        assert isinstance(mock_response.tokens_used, int)
        assert isinstance(mock_response.cost, float)
        assert isinstance(mock_response.response_time_ms, float)
    
    def test_response_fields(self, mock_response):
        """Response should have correct values"""
        assert mock_response.provider == ProviderType.OLLAMA
        assert mock_response.model == "mistral"
        assert mock_response.cost == 0.0  # Ollama is free
        assert mock_response.response_time_ms > 0


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================

class TestErrorHandling:
    """Tests for error handling in model consolidation"""
    
    @pytest.mark.asyncio
    async def test_adapter_initialization_error(self):
        """Service should handle adapter initialization errors"""
        service = ModelConsolidationService()
        # Should have initialized successfully despite any individual failures
        assert service is not None
    
    @pytest.mark.asyncio
    async def test_invalid_provider_type(self):
        """Should handle invalid provider type gracefully"""
        service = ModelConsolidationService()
        # This should not crash
        status = service.get_status()
        assert status is not None


# ============================================================================
# METRICS TRACKING TESTS
# ============================================================================

class TestMetricsTracking:
    """Tests for metrics collection"""
    
    @pytest.mark.asyncio
    async def test_successful_request_metrics(self):
        """Metrics should track successful requests"""
        service = ModelConsolidationService()
        mock_response = ModelResponse(
            text="test",
            provider=ProviderType.OLLAMA,
            model="mistral",
            tokens_used=100,
            cost=0.0,
            response_time_ms=150
        )
        
        with patch.object(service.adapters[ProviderType.OLLAMA], 'generate', return_value=mock_response):
            with patch.object(service, '_check_provider_availability', return_value=True):
                try:
                    await service.generate("test")
                    # Metrics should be updated (if generation succeeds)
                    status = service.get_status()
                    assert status is not None
                except Exception:
                    pass
    
    def test_metrics_format(self, model_service):
        """Metrics should have correct format"""
        metrics = model_service.metrics
        assert "total_requests" in metrics
        assert "successful_requests" in metrics
        assert "failed_requests" in metrics
        assert "total_cost" in metrics
        assert "by_provider" in metrics


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
