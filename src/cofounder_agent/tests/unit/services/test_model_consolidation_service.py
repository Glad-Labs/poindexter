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

from services.error_handler import ServiceError
from services.model_consolidation_service import (
    ModelConsolidationService,
    ModelResponse,
    ProviderStatus,
    ProviderType,
    get_model_consolidation_service,
)

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
            provider=ProviderType.OLLAMA,
            model="qwen3:8b",
            tokens_used=100,
            cost=0.0,
            response_time_ms=350.0,
        )
        assert r.text == "hello"
        assert r.provider == ProviderType.OLLAMA
        assert r.cost == 0.0


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
        svc.provider_status[ProviderType.HUGGINGFACE] = ProviderStatus(
            provider=ProviderType.HUGGINGFACE,
            is_available=False,
            last_checked=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        result = await svc._check_provider_availability(ProviderType.HUGGINGFACE)
        assert result is False

    @pytest.mark.asyncio
    async def test_error_in_check_returns_false(self):
        svc = make_service_no_adapters()
        adapter = AsyncMock()
        adapter.is_available = AsyncMock(side_effect=RuntimeError("network error"))
        svc.adapters[ProviderType.HUGGINGFACE] = adapter
        svc.provider_status[ProviderType.HUGGINGFACE] = ProviderStatus(
            provider=ProviderType.HUGGINGFACE,
            is_available=True,
            last_checked=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        result = await svc._check_provider_availability(ProviderType.HUGGINGFACE)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_provider_not_in_status(self):
        svc = make_service_no_adapters()
        result = await svc._check_provider_availability(ProviderType.HUGGINGFACE)
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
        response = make_response(provider=ProviderType.HUGGINGFACE)
        add_adapter(svc, ProviderType.HUGGINGFACE, is_available=True, response=response)
        svc.FALLBACK_CHAIN = [ProviderType.OLLAMA, ProviderType.HUGGINGFACE]

        result = await svc.generate("hello")
        assert result.provider == ProviderType.HUGGINGFACE

    @pytest.mark.asyncio
    async def test_skips_missing_adapters(self):
        svc = make_service_no_adapters()
        response = make_response(provider=ProviderType.HUGGINGFACE)
        add_adapter(svc, ProviderType.HUGGINGFACE, is_available=True, response=response)
        # OLLAMA not in adapters — remove it so only HuggingFace exists
        svc.adapters.pop(ProviderType.OLLAMA, None)
        svc.FALLBACK_CHAIN = [ProviderType.OLLAMA, ProviderType.HUGGINGFACE]

        result = await svc.generate("hello")
        assert result.provider == ProviderType.HUGGINGFACE

    @pytest.mark.asyncio
    async def test_preferred_provider_tried_first(self):
        svc = make_service_no_adapters()
        response = make_response(provider=ProviderType.HUGGINGFACE)
        add_adapter(svc, ProviderType.HUGGINGFACE, is_available=True, response=response)
        add_adapter(svc, ProviderType.OLLAMA, is_available=True, response=make_response())
        svc.FALLBACK_CHAIN = [ProviderType.OLLAMA, ProviderType.HUGGINGFACE]

        result = await svc.generate("hello", preferred_provider=ProviderType.HUGGINGFACE)
        assert result.provider == ProviderType.HUGGINGFACE

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
        response = make_response(provider=ProviderType.HUGGINGFACE)
        add_adapter(svc, ProviderType.HUGGINGFACE, is_available=True, response=response)
        svc.FALLBACK_CHAIN = [ProviderType.OLLAMA, ProviderType.HUGGINGFACE]

        result = await svc.generate("hello")
        assert result.provider == ProviderType.HUGGINGFACE

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
        svc.provider_status[ProviderType.HUGGINGFACE] = ProviderStatus(
            provider=ProviderType.HUGGINGFACE,
            is_available=False,
            last_checked=datetime.now(timezone.utc),
            last_error="API token missing",
        )
        status = svc.get_status()
        provider_info = status["providers"]["huggingface"]
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
        add_adapter(svc, ProviderType.HUGGINGFACE, is_available=True, response=None)
        models = await svc.list_models()
        assert "ollama" in models
        assert "huggingface" in models

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
        # Provider exists in enum but has no adapter registered
        models = await svc.list_models(provider=ProviderType.HUGGINGFACE)
        assert models.get("huggingface", []) == []


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


# ===========================================================================
# OllamaAdapter
# ===========================================================================


class TestOllamaAdapterIsAvailable:
    @pytest.mark.asyncio
    async def test_returns_true_on_200(self):
        from services.model_consolidation_service import OllamaAdapter, ProviderType

        with patch("services.model_consolidation_service.OllamaClient") if False else patch.object(
            OllamaAdapter, "__init__", lambda self: None
        ):
            adapter = OllamaAdapter()
            adapter.host = "http://localhost:11434"
            adapter.provider_type = ProviderType.OLLAMA
            adapter.client = MagicMock()
            mock_resp = MagicMock(status_code=200)
            adapter.client.client = MagicMock()
            adapter.client.client.get = AsyncMock(return_value=mock_resp)
            assert await adapter.is_available() is True

    @pytest.mark.asyncio
    async def test_returns_false_on_non_200(self):
        from services.model_consolidation_service import OllamaAdapter, ProviderType

        with patch.object(OllamaAdapter, "__init__", lambda self: None):
            adapter = OllamaAdapter()
            adapter.host = "http://localhost:11434"
            adapter.provider_type = ProviderType.OLLAMA
            adapter.client = MagicMock()
            mock_resp = MagicMock(status_code=503)
            adapter.client.client = MagicMock()
            adapter.client.client.get = AsyncMock(return_value=mock_resp)
            assert await adapter.is_available() is False

    @pytest.mark.asyncio
    async def test_returns_false_on_connection_error(self):
        from services.model_consolidation_service import OllamaAdapter, ProviderType

        with patch.object(OllamaAdapter, "__init__", lambda self: None):
            adapter = OllamaAdapter()
            adapter.host = "http://localhost:11434"
            adapter.provider_type = ProviderType.OLLAMA
            adapter.client = MagicMock()
            adapter.client.client = MagicMock()
            adapter.client.client.get = AsyncMock(side_effect=ConnectionError("refused"))

            # Patch the fallback fresh-client httpx path too
            mock_fallback = AsyncMock()
            mock_fallback.__aenter__ = AsyncMock(return_value=mock_fallback)
            mock_fallback.__aexit__ = AsyncMock(return_value=False)
            mock_fallback.get = AsyncMock(side_effect=ConnectionError("refused"))

            with patch("services.model_consolidation_service.httpx.AsyncClient", return_value=mock_fallback):
                assert await adapter.is_available() is False


class TestOllamaAdapterGenerate:
    @pytest.mark.asyncio
    async def test_returns_model_response_on_success(self):
        from services.model_consolidation_service import ModelResponse, OllamaAdapter, ProviderType

        with patch.object(OllamaAdapter, "__init__", lambda self: None):
            adapter = OllamaAdapter()
            adapter.provider_type = ProviderType.OLLAMA
            adapter.client = MagicMock()
            adapter.client.generate = AsyncMock(return_value={
                "response": "Hello world from Ollama",
                "prompt_eval_count": 5,
                "eval_count": 10,
            })

            # Pass model explicitly so the function doesn't fall through to
            # site_config — that's covered separately in the next test
            result = await adapter.generate("Test prompt", model="qwen3:8b")

            assert isinstance(result, ModelResponse)
            assert result.text == "Hello world from Ollama"
            assert result.tokens_used == 15  # prompt_eval_count + eval_count
            assert result.cost == 0.0
            assert result.provider == ProviderType.OLLAMA

    @pytest.mark.asyncio
    async def test_uses_default_model_when_not_specified(self):
        from services.model_consolidation_service import OllamaAdapter, ProviderType

        with patch.object(OllamaAdapter, "__init__", lambda self: None):
            adapter = OllamaAdapter()
            adapter.provider_type = ProviderType.OLLAMA
            adapter.client = MagicMock()
            adapter.client.generate = AsyncMock(return_value={"response": "ok"})

            with patch("services.site_config.site_config") as mock_sc:
                mock_sc.get.return_value = "default-model"
                await adapter.generate("Test prompt")

            # The model arg passed to client.generate should be the default
            call_kwargs = adapter.client.generate.await_args.kwargs
            assert call_kwargs["model"] == "default-model"

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        from services.model_consolidation_service import OllamaAdapter, ProviderType

        with patch.object(OllamaAdapter, "__init__", lambda self: None):
            adapter = OllamaAdapter()
            adapter.provider_type = ProviderType.OLLAMA
            adapter.client = MagicMock()
            adapter.client.generate = AsyncMock(side_effect=RuntimeError("ollama down"))

            with pytest.raises(RuntimeError, match="ollama down"):
                await adapter.generate("Test prompt", model="qwen3:8b")


class TestOllamaAdapterListModels:
    @pytest.mark.asyncio
    async def test_returns_live_model_names(self):
        from services.model_consolidation_service import OllamaAdapter, ProviderType

        with patch.object(OllamaAdapter, "__init__", lambda self: None):
            adapter = OllamaAdapter()
            adapter.host = "http://localhost:11434"
            adapter.provider_type = ProviderType.OLLAMA
            adapter.client = MagicMock()
            adapter.client.list_models = AsyncMock(return_value=[
                {"name": "qwen3:8b"},
                {"name": "gemma3:27b"},
            ])

            result = await adapter.list_models()
            assert result == ["qwen3:8b", "gemma3:27b"]

    @pytest.mark.asyncio
    async def test_returns_fallback_when_client_fails(self):
        from services.model_consolidation_service import OllamaAdapter, ProviderType

        with patch.object(OllamaAdapter, "__init__", lambda self: None):
            adapter = OllamaAdapter()
            adapter.host = "http://localhost:11434"
            adapter.provider_type = ProviderType.OLLAMA
            adapter.client = MagicMock()
            adapter.client.list_models = AsyncMock(side_effect=RuntimeError("conn error"))

            result = await adapter.list_models()
            # Falls back to known-installed list
            assert isinstance(result, list)
            assert "qwen3:8b" in result or "gemma3:27b" in result

    @pytest.mark.asyncio
    async def test_empty_live_list_uses_fallback(self):
        from services.model_consolidation_service import OllamaAdapter, ProviderType

        with patch.object(OllamaAdapter, "__init__", lambda self: None):
            adapter = OllamaAdapter()
            adapter.host = "http://localhost:11434"
            adapter.provider_type = ProviderType.OLLAMA
            adapter.client = MagicMock()
            adapter.client.list_models = AsyncMock(return_value=[])

            result = await adapter.list_models()
            assert len(result) > 0  # falls back to known list


# ===========================================================================
# HuggingFaceAdapter
# ===========================================================================


class TestHuggingFaceAdapter:
    @pytest.mark.asyncio
    async def test_is_available_delegates_to_provider_checker(self):
        from services.model_consolidation_service import HuggingFaceAdapter, ProviderType

        with patch.object(HuggingFaceAdapter, "__init__", lambda self: None):
            adapter = HuggingFaceAdapter()
            adapter.provider_type = ProviderType.HUGGINGFACE

            with patch("services.model_consolidation_service.ProviderChecker") as mock_pc:
                mock_pc.is_huggingface_available.return_value = True
                assert await adapter.is_available() is True

                mock_pc.is_huggingface_available.return_value = False
                assert await adapter.is_available() is False

    def test_list_models_returns_known_list(self):
        from services.model_consolidation_service import HuggingFaceAdapter, ProviderType

        with patch.object(HuggingFaceAdapter, "__init__", lambda self: None):
            adapter = HuggingFaceAdapter()
            adapter.provider_type = ProviderType.HUGGINGFACE

            models = adapter.list_models()
            assert isinstance(models, list)
            assert "mistralai/Mistral-7B-Instruct-v0.1" in models
            assert len(models) >= 3

    @pytest.mark.asyncio
    async def test_generate_returns_model_response(self):
        from services.model_consolidation_service import (
            HuggingFaceAdapter,
            ModelResponse,
            ProviderType,
        )

        with patch.object(HuggingFaceAdapter, "__init__", lambda self: None):
            adapter = HuggingFaceAdapter()
            adapter.provider_type = ProviderType.HUGGINGFACE
            adapter.api_token = ""  # no token = free tier path
            adapter.client = MagicMock()
            adapter.client.generate = AsyncMock(return_value="Generated text from HF")

            result = await adapter.generate("Test prompt", model="some-model")
            assert isinstance(result, ModelResponse)
            assert result.text == "Generated text from HF"
            assert result.provider == ProviderType.HUGGINGFACE
            assert result.cost == 0.0  # free tier (no token)

    @pytest.mark.asyncio
    async def test_generate_with_token_uses_paid_cost(self):
        """If api_token is set, the response cost reflects the minimal paid rate."""
        from services.model_consolidation_service import HuggingFaceAdapter, ProviderType

        with patch.object(HuggingFaceAdapter, "__init__", lambda self: None):
            adapter = HuggingFaceAdapter()
            adapter.provider_type = ProviderType.HUGGINGFACE
            adapter.api_token = "hf_real_token_value"
            adapter.client = MagicMock()
            adapter.client.generate = AsyncMock(return_value="paid response")

            result = await adapter.generate("Test prompt", model="some-model")
            assert result.cost == 0.0001

    @pytest.mark.asyncio
    async def test_generate_uses_default_model(self):
        from services.model_consolidation_service import HuggingFaceAdapter, ProviderType

        with patch.object(HuggingFaceAdapter, "__init__", lambda self: None):
            adapter = HuggingFaceAdapter()
            adapter.provider_type = ProviderType.HUGGINGFACE
            adapter.api_token = ""
            adapter.client = MagicMock()
            adapter.client.generate = AsyncMock(return_value="ok")

            await adapter.generate("Test prompt")
            call_kwargs = adapter.client.generate.await_args.kwargs
            assert call_kwargs["model"] == "mistralai/Mistral-7B-Instruct-v0.1"

    @pytest.mark.asyncio
    async def test_generate_exception_propagates(self):
        from services.model_consolidation_service import HuggingFaceAdapter, ProviderType

        with patch.object(HuggingFaceAdapter, "__init__", lambda self: None):
            adapter = HuggingFaceAdapter()
            adapter.provider_type = ProviderType.HUGGINGFACE
            adapter.api_token = ""
            adapter.client = MagicMock()
            adapter.client.generate = AsyncMock(side_effect=RuntimeError("hf api error"))

            with pytest.raises(RuntimeError, match="hf api error"):
                await adapter.generate("Test prompt")


# ===========================================================================
# ProviderType enum
# ===========================================================================


class TestProviderType:
    def test_ollama_value(self):
        from services.model_consolidation_service import ProviderType
        assert ProviderType.OLLAMA.value == "ollama"

    def test_huggingface_value(self):
        from services.model_consolidation_service import ProviderType
        assert ProviderType.HUGGINGFACE.value == "huggingface"

    def test_construct_from_value(self):
        from services.model_consolidation_service import ProviderType
        assert ProviderType("ollama") == ProviderType.OLLAMA
