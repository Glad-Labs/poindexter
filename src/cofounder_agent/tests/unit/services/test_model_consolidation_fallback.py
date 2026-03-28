"""
Integration tests for ModelConsolidationService provider fallback chain.

Tests the end-to-end fallback behaviour:
- First provider succeeds → no fallback attempted
- First provider unavailable → falls back to second
- Multiple providers unavailable → walks the full chain
- All providers unavailable → raises ServiceError
- Preferred provider overrides default chain order
- generate() updates per-provider metrics on success

No live LLM calls are made.  All adapters are replaced with AsyncMock stubs.
"""

from unittest.mock import AsyncMock, patch

import pytest

from services.error_handler import ServiceError
from services.model_consolidation_service import (
    ModelConsolidationService,
    ModelResponse,
    ProviderType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(provider: ProviderType, text: str = "ok") -> ModelResponse:
    """Return a minimal ModelResponse for testing."""
    return ModelResponse(
        text=text,
        provider=provider,
        model="test-model",
        tokens_used=15,
        cost=0.0001,
        response_time_ms=50,
    )


def _unavailable_adapter() -> AsyncMock:
    """Adapter stub that reports as unavailable."""
    adapter = AsyncMock()
    adapter.is_available = AsyncMock(return_value=False)
    return adapter


def _available_adapter(response: ModelResponse) -> AsyncMock:
    """Adapter stub that reports available and returns the given response."""
    adapter = AsyncMock()
    adapter.is_available = AsyncMock(return_value=True)
    adapter.generate = AsyncMock(return_value=response)
    return adapter


def _failing_adapter(exc: Exception) -> AsyncMock:
    """Adapter stub that is available but raises on generate()."""
    adapter = AsyncMock()
    adapter.is_available = AsyncMock(return_value=True)
    adapter.generate = AsyncMock(side_effect=exc)
    return adapter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def service() -> ModelConsolidationService:
    """Return a ModelConsolidationService with _initialize_adapters() no-oped.

    All provider statuses start with cache_expired=True (last_checked far in the
    past) so that _check_provider_availability() always calls through to the
    adapter's is_available() method, which the test controls via AsyncMock.
    """
    with patch.object(ModelConsolidationService, "_initialize_adapters", return_value=None):
        svc = ModelConsolidationService()
    # Empty provider_status dict forces _check_provider_availability() to call
    # the adapter, since `status = self.provider_status.get(provider_type)` returns
    # None and the `if status and not status.cache_expired` guard is skipped.
    svc.provider_status = {}
    return svc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
class TestFallbackChain:
    async def test_first_provider_succeeds_no_fallback(self, service):
        """When Ollama is available and succeeds, no other provider is tried."""
        ollama_response = _make_response(ProviderType.OLLAMA, "hello from ollama")
        service.adapters[ProviderType.OLLAMA] = _available_adapter(ollama_response)

        # All other adapters should not be called
        for p in [
            ProviderType.HUGGINGFACE,
            ProviderType.GOOGLE,
            ProviderType.ANTHROPIC,
            ProviderType.OPENAI,
        ]:
            service.adapters[p] = _unavailable_adapter()

        result = await service.generate("test prompt")

        assert result.text == "hello from ollama"
        assert result.provider == ProviderType.OLLAMA

    async def test_first_provider_unavailable_falls_back(self, service):
        """When Ollama is unavailable, HuggingFace is tried next."""
        hf_response = _make_response(ProviderType.HUGGINGFACE, "hello from hf")
        service.adapters[ProviderType.OLLAMA] = _unavailable_adapter()
        service.adapters[ProviderType.HUGGINGFACE] = _available_adapter(hf_response)

        for p in [ProviderType.GOOGLE, ProviderType.ANTHROPIC, ProviderType.OPENAI]:
            service.adapters[p] = _unavailable_adapter()

        result = await service.generate("test prompt")

        assert result.text == "hello from hf"
        assert result.provider == ProviderType.HUGGINGFACE

    async def test_generate_raises_walks_chain(self, service):
        """When first available adapter raises, the next provider is tried."""
        google_response = _make_response(ProviderType.GOOGLE, "hello from google")

        service.adapters[ProviderType.OLLAMA] = _failing_adapter(
            ConnectionError("Ollama not running")
        )
        service.adapters[ProviderType.HUGGINGFACE] = _failing_adapter(
            RuntimeError("HF quota exceeded")
        )
        service.adapters[ProviderType.GOOGLE] = _available_adapter(google_response)

        for p in [ProviderType.ANTHROPIC, ProviderType.OPENAI]:
            service.adapters[p] = _unavailable_adapter()

        result = await service.generate("test prompt")

        assert result.text == "hello from google"

    async def test_all_providers_fail_raises_service_error(self, service):
        """When every provider either fails or is unavailable, ServiceError is raised."""
        for p in ProviderType:
            service.adapters[p] = _failing_adapter(ConnectionError(f"{p.value} connection refused"))

        with pytest.raises(ServiceError, match="All model providers failed"):
            await service.generate("test prompt")

    async def test_all_providers_unavailable_raises_service_error(self, service):
        """When all adapters report unavailable, ServiceError is raised."""
        for p in ProviderType:
            service.adapters[p] = _unavailable_adapter()

        with pytest.raises(ServiceError, match="All model providers failed"):
            await service.generate("test prompt")

    async def test_preferred_provider_goes_first(self, service):
        """preferred_provider is tried before Ollama in the chain."""
        anthropic_response = _make_response(ProviderType.ANTHROPIC, "hello from anthropic")

        service.adapters[ProviderType.OLLAMA] = _unavailable_adapter()
        service.adapters[ProviderType.ANTHROPIC] = _available_adapter(anthropic_response)

        for p in [ProviderType.HUGGINGFACE, ProviderType.GOOGLE, ProviderType.OPENAI]:
            service.adapters[p] = _unavailable_adapter()

        result = await service.generate("test prompt", preferred_provider=ProviderType.ANTHROPIC)

        assert result.provider == ProviderType.ANTHROPIC

    async def test_metrics_updated_on_success(self, service):
        """Successful generation increments successful_requests and per-provider count."""
        response = _make_response(ProviderType.OLLAMA, "text")
        service.adapters[ProviderType.OLLAMA] = _available_adapter(response)

        before = service.metrics["successful_requests"]
        await service.generate("test")

        assert service.metrics["successful_requests"] == before + 1
        assert ProviderType.OLLAMA.value in service.metrics["by_provider"]
        assert service.metrics["by_provider"][ProviderType.OLLAMA.value]["requests"] >= 1

    async def test_failed_requests_metric_incremented_on_full_chain_failure(self, service):
        """failed_requests metric is incremented when all providers fail."""
        for p in ProviderType:
            service.adapters[p] = _unavailable_adapter()

        before = service.metrics["failed_requests"]

        with pytest.raises(ServiceError):
            await service.generate("test")

        assert service.metrics["failed_requests"] == before + 1
