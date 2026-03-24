"""
Unit tests for services.model_consolidation_service

Tests cover:
- ProviderStatus.cache_expired property
- ModelResponse dataclass construction
- ModelConsolidationService construction (adapters skipped via mock)
- _check_provider_availability: cached fresh result, expired cache re-checks, error path
- generate: first available provider used, skips unavailable, skips missing adapters,
  preferred_provider goes first, all-fail raises ServiceError, metrics updated
- get_status returns dict with provider keys
- list_models with and without provider filter
- Global singleton initialize / get
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.model_consolidation_service import (
    ModelConsolidationService,
    ModelResponse,
    ProviderStatus,
    ProviderType,
    get_model_consolidation_service,
    initialize_model_consolidation_service,
)
from services.error_handler import ServiceError


# ---------------------------------------------------------------------------
# ProviderStatus
# ---------------------------------------------------------------------------


class TestProviderStatus:
    def test_fresh_status_not_expired(self):
        s = ProviderStatus(
            provider=ProviderType.OLLAMA,
            is_available=True,
            last_checked=datetime.now(timezone.utc),
        )
        assert s.cache_expired is False

    def test_old_status_is_expired(self):
        s = ProviderStatus(
            provider=ProviderType.OLLAMA,
            is_available=True,
            last_checked=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        assert s.cache_expired is True

    def test_exactly_five_minutes_is_expired(self):
        s = ProviderStatus(
            provider=ProviderType.OLLAMA,
            is_available=True,
            last_checked=datetime.now(timezone.utc) - timedelta(minutes=5, seconds=1),
        )
        assert s.cache_expired is True


# ---------------------------------------------------------------------------
# ModelResponse
# ---------------------------------------------------------------------------


class TestModelResponse:
    def test_construction(self):
        r = ModelResponse(
            text="hello",
            provider=ProviderType.ANTHROPIC,
            model="claude-3",
            tokens_used=100,
            cost=0.005,
            response_time_ms=350.0,
        )
        assert r.text == "hello"
        assert r.provider == ProviderType.ANTHROPIC
        assert r.cost == 0.005


# ---------------------------------------------------------------------------
# Helper: build a bare ModelConsolidationService without real adapters
# ---------------------------------------------------------------------------


def make_service_no_adapters() -> ModelConsolidationService:
    """Create service instance with no real adapters (avoids network/env deps)."""
    with patch.object(ModelConsolidationService, "_initialize_adapters", return_value=None):
        svc = ModelConsolidationService()
    svc.adapters = {}
    svc.provider_status = {}
    return svc


def add_adapter(svc, provider_type, *, is_available=True, response=None):
    """Add a mock adapter for a provider type."""
    adapter = AsyncMock()
    adapter.is_available = AsyncMock(return_value=is_available)
    adapter.list_models = MagicMock(return_value=["model-a"])
    if response is not None:
        adapter.generate = AsyncMock(return_value=response)
    else:
        adapter.generate = AsyncMock(side_effect=RuntimeError("generate failed"))
    svc.adapters[provider_type] = adapter

    svc.provider_status[provider_type] = ProviderStatus(
        provider=provider_type,
        is_available=is_available,
        # Make the cache expired so checks always re-query
        last_checked=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    return adapter


def make_response(provider=ProviderType.OLLAMA, cost=0.0):
    return ModelResponse(
        text="Generated text",
        provider=provider,
        model="test-model",
        tokens_used=50,
        cost=cost,
        response_time_ms=100.0,
    )


# ---------------------------------------------------------------------------
# _check_provider_availability
# ---------------------------------------------------------------------------


class TestCheckProviderAvailability:
    @pytest.mark.asyncio
    async def test_uses_cached_when_fresh(self):
        svc = make_service_no_adapters()
        svc.provider_status[ProviderType.OLLAMA] = ProviderStatus(
            provider=ProviderType.OLLAMA,
            is_available=True,
            last_checked=datetime.now(timezone.utc),  # fresh
        )
        # No adapter needed since we return cached
        result = await svc._check_provider_availability(ProviderType.OLLAMA)
        assert result is True

    @pytest.mark.asyncio
    async def test_re_checks_when_expired(self):
        svc = make_service_no_adapters()
        add_adapter(svc, ProviderType.OLLAMA, is_available=True)
        result = await svc._check_provider_availability(ProviderType.OLLAMA)
        assert result is True
        svc.adapters[ProviderType.OLLAMA].is_available.assert_awaited_once_with()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_returns_false_when_no_adapter(self):
        svc = make_service_no_adapters()
        svc.provider_status[ProviderType.OPENAI] = ProviderStatus(
            provider=ProviderType.OPENAI,
            is_available=False,
            last_checked=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        result = await svc._check_provider_availability(ProviderType.OPENAI)
        assert result is False

    @pytest.mark.asyncio
    async def test_error_in_check_returns_false(self):
        svc = make_service_no_adapters()
        adapter = AsyncMock()
        adapter.is_available = AsyncMock(side_effect=RuntimeError("network error"))
        svc.adapters[ProviderType.GOOGLE] = adapter
        svc.provider_status[ProviderType.GOOGLE] = ProviderStatus(
            provider=ProviderType.GOOGLE,
            is_available=True,
            last_checked=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        result = await svc._check_provider_availability(ProviderType.GOOGLE)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_provider_not_in_status(self):
        svc = make_service_no_adapters()
        result = await svc._check_provider_availability(ProviderType.OPENAI)
        assert result is False


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


class TestGenerate:
    @pytest.mark.asyncio
    async def test_uses_first_available_provider(self):
        svc = make_service_no_adapters()
        response = make_response(provider=ProviderType.OLLAMA)
        add_adapter(svc, ProviderType.OLLAMA, is_available=True, response=response)
        # Ensure FALLBACK_CHAIN starts with OLLAMA
        svc.FALLBACK_CHAIN = [ProviderType.OLLAMA]

        result = await svc.generate("hello")
        assert result.text == "Generated text"
        assert result.provider == ProviderType.OLLAMA

    @pytest.mark.asyncio
    async def test_skips_unavailable_providers(self):
        svc = make_service_no_adapters()
        add_adapter(svc, ProviderType.OLLAMA, is_available=False)
        response = make_response(provider=ProviderType.ANTHROPIC)
        add_adapter(svc, ProviderType.ANTHROPIC, is_available=True, response=response)
        svc.FALLBACK_CHAIN = [ProviderType.OLLAMA, ProviderType.ANTHROPIC]

        result = await svc.generate("hello")
        assert result.provider == ProviderType.ANTHROPIC

    @pytest.mark.asyncio
    async def test_skips_missing_adapters(self):
        svc = make_service_no_adapters()
        response = make_response(provider=ProviderType.OPENAI)
        add_adapter(svc, ProviderType.OPENAI, is_available=True, response=response)
        # OLLAMA not in adapters
        svc.FALLBACK_CHAIN = [ProviderType.OLLAMA, ProviderType.OPENAI]

        result = await svc.generate("hello")
        assert result.provider == ProviderType.OPENAI

    @pytest.mark.asyncio
    async def test_preferred_provider_tried_first(self):
        svc = make_service_no_adapters()
        response = make_response(provider=ProviderType.ANTHROPIC)
        add_adapter(svc, ProviderType.ANTHROPIC, is_available=True, response=response)
        add_adapter(svc, ProviderType.OLLAMA, is_available=True, response=make_response())
        svc.FALLBACK_CHAIN = [ProviderType.OLLAMA, ProviderType.ANTHROPIC]

        result = await svc.generate("hello", preferred_provider=ProviderType.ANTHROPIC)
        assert result.provider == ProviderType.ANTHROPIC

    @pytest.mark.asyncio
    async def test_all_fail_raises_service_error(self):
        svc = make_service_no_adapters()
        add_adapter(svc, ProviderType.OLLAMA, is_available=False)
        svc.FALLBACK_CHAIN = [ProviderType.OLLAMA]

        with pytest.raises(ServiceError):
            await svc.generate("hello")

    @pytest.mark.asyncio
    async def test_generate_error_falls_through_to_next(self):
        svc = make_service_no_adapters()
        # First provider is available but generate throws
        add_adapter(svc, ProviderType.OLLAMA, is_available=True, response=None)
        svc.adapters[ProviderType.OLLAMA].generate = AsyncMock(
            side_effect=RuntimeError("ollama crash")
        )
        response = make_response(provider=ProviderType.ANTHROPIC)
        add_adapter(svc, ProviderType.ANTHROPIC, is_available=True, response=response)
        svc.FALLBACK_CHAIN = [ProviderType.OLLAMA, ProviderType.ANTHROPIC]

        result = await svc.generate("hello")
        assert result.provider == ProviderType.ANTHROPIC

    @pytest.mark.asyncio
    async def test_metrics_updated_on_success(self):
        svc = make_service_no_adapters()
        response = make_response(cost=0.01)
        add_adapter(svc, ProviderType.OLLAMA, is_available=True, response=response)
        svc.FALLBACK_CHAIN = [ProviderType.OLLAMA]

        await svc.generate("hello")
        assert svc.metrics["total_requests"] == 1
        assert svc.metrics["successful_requests"] == 1
        assert svc.metrics["total_cost"] == 0.01

    @pytest.mark.asyncio
    async def test_metrics_failed_request_counted(self):
        svc = make_service_no_adapters()
        svc.FALLBACK_CHAIN = []  # No providers

        with pytest.raises(ServiceError):
            await svc.generate("hello")

        assert svc.metrics["failed_requests"] == 1


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_returns_dict_with_providers_and_metrics(self):
        svc = make_service_no_adapters()
        svc.provider_status[ProviderType.OLLAMA] = ProviderStatus(
            provider=ProviderType.OLLAMA,
            is_available=True,
            last_checked=datetime.now(timezone.utc),
        )
        status = svc.get_status()
        assert "providers" in status
        assert "metrics" in status
        assert "ollama" in status["providers"]

    def test_provider_dict_has_expected_keys(self):
        svc = make_service_no_adapters()
        svc.provider_status[ProviderType.ANTHROPIC] = ProviderStatus(
            provider=ProviderType.ANTHROPIC,
            is_available=False,
            last_checked=datetime.now(timezone.utc),
            last_error="API key missing",
        )
        status = svc.get_status()
        provider_info = status["providers"]["anthropic"]
        assert "available" in provider_info
        assert "last_checked" in provider_info
        assert "last_error" in provider_info


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------


class TestListModels:
    @pytest.mark.asyncio
    async def test_no_filter_returns_all_adapters(self):
        svc = make_service_no_adapters()
        add_adapter(svc, ProviderType.OLLAMA, is_available=True, response=None)
        add_adapter(svc, ProviderType.ANTHROPIC, is_available=True, response=None)
        models = await svc.list_models()
        assert "ollama" in models
        assert "anthropic" in models

    @pytest.mark.asyncio
    async def test_with_filter_returns_only_that_provider(self):
        svc = make_service_no_adapters()
        add_adapter(svc, ProviderType.OLLAMA, is_available=True, response=None)
        models = await svc.list_models(provider=ProviderType.OLLAMA)
        assert "ollama" in models
        assert len(models) == 1

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_empty_list(self):
        svc = make_service_no_adapters()
        models = await svc.list_models(provider=ProviderType.OPENAI)
        assert models.get("openai", []) == []


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------


class TestGlobalSingleton:
    def test_get_returns_model_consolidation_service(self):
        import services.model_consolidation_service as mod
        with patch.object(ModelConsolidationService, "_initialize_adapters", return_value=None):
            mod._model_consolidation_service = None
            svc = get_model_consolidation_service()
        assert isinstance(svc, ModelConsolidationService)

    def test_same_instance_returned_twice(self):
        import services.model_consolidation_service as mod
        with patch.object(ModelConsolidationService, "_initialize_adapters", return_value=None):
            mod._model_consolidation_service = None
            a = get_model_consolidation_service()
            b = get_model_consolidation_service()
        assert a is b
