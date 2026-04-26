"""Unit tests for LLMProvider plugin implementations.

Two providers under test:

- ``OllamaNativeProvider`` — wraps the existing OllamaClient.
- ``OpenAICompatProvider`` — generic OpenAI-compat client built on top
  of the ``openai`` Python SDK with mandatory cost-guard integration
  (Glad-Labs/poindexter#132).

Heavy reliance on mocked SDK / mocked HTTP so these tests don't need a
live Ollama or vLLM. Integration verification (real Ollama + real swap)
happens separately in the refactor smoke test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins import LLMProvider
from services.cost_guard import CostGuard, CostGuardExhausted
from services.llm_providers.ollama_native import OllamaNativeProvider
from services.llm_providers.openai_compat import OpenAICompatProvider

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


_ENABLED_LOCAL_CFG = {
    "enabled": True,
    "base_url": "http://localhost:11434/v1",
    "request_timeout_s": 60,
}

_ENABLED_CLOUD_CFG = {
    "enabled": True,
    "base_url": "https://api.openai.com/v1",
    "request_timeout_s": 30,
}


class _FakeChatResponse:
    """Stand-in for ``openai.types.chat.ChatCompletion`` — only the
    ``model_dump()`` call site is exercised by the provider, so a dict
    payload + a ``model_dump`` method is sufficient.
    """

    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return self._payload


def _ok_chat_payload(text: str = "hello") -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
                "index": 0,
            },
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "total_tokens": 20,
        },
    }


def _ok_embed_payload() -> dict[str, Any]:
    return {
        "data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }


def _build_fake_async_openai(*, chat_payload: dict | None = None,
                             embed_payload: dict | None = None,
                             chat_side_effect: Exception | None = None,
                             embed_side_effect: Exception | None = None):
    """Construct an ``AsyncOpenAI``-shaped MagicMock with chat + embed
    create methods returning the supplied payloads (or raising)."""
    fake = MagicMock(name="AsyncOpenAI")
    fake.chat = MagicMock()
    fake.chat.completions = MagicMock()
    if chat_side_effect is not None:
        fake.chat.completions.create = AsyncMock(side_effect=chat_side_effect)
    else:
        fake.chat.completions.create = AsyncMock(
            return_value=_FakeChatResponse(chat_payload or _ok_chat_payload()),
        )
    fake.embeddings = MagicMock()
    if embed_side_effect is not None:
        fake.embeddings.create = AsyncMock(side_effect=embed_side_effect)
    else:
        fake.embeddings.create = AsyncMock(
            return_value=_FakeChatResponse(embed_payload or _ok_embed_payload()),
        )
    return fake


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


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
# OpenAICompatProvider — disabled-by-default policy
# ---------------------------------------------------------------------------


class TestOpenAICompatDisabledByDefault:
    """The plugin must ship inert. Operators opt in explicitly by
    writing ``plugin.llm_provider.openai_compat.enabled=true``.
    """

    @pytest.mark.asyncio
    async def test_openai_compat_disabled_by_default(self):
        """No SDK side effects when enabled=false."""
        provider = OpenAICompatProvider()

        # If the SDK were actually instantiated this patch would raise.
        with patch(
            "services.llm_providers.openai_compat.OpenAICompatProvider._build_sdk_client",
        ) as build:
            build.side_effect = AssertionError("SDK must not be built when disabled")
            with pytest.raises(RuntimeError, match="disabled"):
                await provider.complete(
                    messages=[{"role": "user", "content": "hi"}],
                    model="gpt-4o-mini",
                    _provider_config={},  # enabled defaults to False
                )

    @pytest.mark.asyncio
    async def test_disabled_blocks_embed_too(self):
        provider = OpenAICompatProvider()
        with pytest.raises(RuntimeError, match="disabled"):
            await provider.embed_with(
                text="hello", model="text-embedding-3-small",
                _provider_config={},
            )


# ---------------------------------------------------------------------------
# OpenAICompatProvider — complete() round-trip
# ---------------------------------------------------------------------------


class TestOpenAICompatComplete:
    @pytest.mark.asyncio
    async def test_openai_compat_complete_round_trip(self):
        """Verify request shape + result mapping when the SDK is mocked."""
        provider = OpenAICompatProvider()
        fake_client = _build_fake_async_openai(chat_payload=_ok_chat_payload("hi back"))

        with patch.object(provider, "_build_sdk_client", return_value=fake_client):
            result = await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="gpt-4o-mini",
                temperature=0.5,
                max_tokens=128,
                _provider_config=dict(_ENABLED_LOCAL_CFG),
            )

        # Result mapping
        assert result.text == "hi back"
        assert result.model == "gpt-4o-mini"
        assert result.prompt_tokens == 12
        assert result.completion_tokens == 8
        assert result.total_tokens == 20
        assert result.finish_reason == "stop"

        # Request mapping
        fake_client.chat.completions.create.assert_awaited_once()
        call = fake_client.chat.completions.create.await_args
        assert call.kwargs["model"] == "gpt-4o-mini"
        assert call.kwargs["messages"] == [{"role": "user", "content": "hi"}]
        assert call.kwargs["temperature"] == 0.5
        assert call.kwargs["max_tokens"] == 128

    @pytest.mark.asyncio
    async def test_complete_falls_back_to_default_model(self):
        provider = OpenAICompatProvider()
        cfg = dict(_ENABLED_LOCAL_CFG, default_model="meta-llama-3-70b")
        fake_client = _build_fake_async_openai()

        with patch.object(provider, "_build_sdk_client", return_value=fake_client):
            await provider.complete(
                messages=[{"role": "user", "content": "x"}],
                model="",
                _provider_config=cfg,
            )

        call = fake_client.chat.completions.create.await_args
        assert call.kwargs["model"] == "meta-llama-3-70b"

    @pytest.mark.asyncio
    async def test_complete_requires_model_or_default(self):
        provider = OpenAICompatProvider()
        with pytest.raises(ValueError, match="model"):
            await provider.complete(
                messages=[{"role": "user", "content": "x"}],
                model="",
                _provider_config=dict(_ENABLED_LOCAL_CFG),
            )


# ---------------------------------------------------------------------------
# OpenAICompatProvider — cost-guard integration
# ---------------------------------------------------------------------------


class _RecordingCostGuard(CostGuard):
    """CostGuard test double that captures every preflight + record call."""

    def __init__(self, *, daily_spend: float = 0.0, monthly_spend: float = 0.0):
        super().__init__(site_config=None, pool=None)
        self._daily_spend = daily_spend
        self._monthly_spend = monthly_spend
        self.preflighted: list = []
        self.recorded: list = []

    def _limit(self, key: str, default: float) -> float:
        return default  # use defaults

    async def get_daily_spend(self) -> float:
        return self._daily_spend

    async def get_monthly_spend(self) -> float:
        return self._monthly_spend

    async def preflight(self, estimate):
        self.preflighted.append(estimate)
        await super().preflight(estimate)

    async def record(self, **kwargs):
        self.recorded.append(kwargs)


class TestOpenAICompatCostGuard:
    @pytest.mark.asyncio
    async def test_openai_compat_cost_guard_local_zero(self):
        """Local base_url → preflight estimate is $0 and never blocks."""
        provider = OpenAICompatProvider()
        guard = _RecordingCostGuard(daily_spend=999.0, monthly_spend=999.0)
        fake_client = _build_fake_async_openai()

        with patch.object(provider, "_build_sdk_client", return_value=fake_client):
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="gemma3:27b",
                _provider_config=dict(_ENABLED_LOCAL_CFG),
                _cost_guard=guard,
            )

        assert len(guard.preflighted) == 1
        est = guard.preflighted[0]
        assert est.is_local is True
        assert est.estimated_usd == 0.0

        # Even with the spend already over a hypothetical cap, local
        # backends never raise. Recording happens with cost=$0.
        assert len(guard.recorded) == 1
        assert guard.recorded[0]["cost_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_openai_compat_cost_guard_exhausted_raises(self):
        """Cloud backend over budget → CostGuardExhausted, NOT fallback."""
        provider = OpenAICompatProvider()
        # Already at the cap so the next call must trip the guard.
        guard = _RecordingCostGuard(daily_spend=999.0, monthly_spend=999.0)
        # Use cloud base_url so is_local=False.
        cfg = dict(_ENABLED_CLOUD_CFG)

        # SDK must NEVER be called when the guard rejects.
        fake_client = _build_fake_async_openai()
        fake_client.chat.completions.create = AsyncMock(
            side_effect=AssertionError("SDK was called despite budget exhaustion"),
        )

        with patch.object(provider, "_build_sdk_client", return_value=fake_client):
            with pytest.raises(CostGuardExhausted) as excinfo:
                await provider.complete(
                    messages=[{"role": "user", "content": "hi"}],
                    model="gpt-4o",
                    _provider_config=cfg,
                    _cost_guard=guard,
                )

        # Typed exception, NOT a fallback string. Carries the budget snapshot.
        assert excinfo.value.scope in ("daily", "monthly")
        assert excinfo.value.spent_usd > 0
        assert excinfo.value.limit_usd > 0
        # Fake SDK was never called.
        fake_client.chat.completions.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cloud_within_budget_records_actual_cost(self):
        """Cloud backend within budget → SDK fires + actual cost recorded."""
        provider = OpenAICompatProvider()
        guard = _RecordingCostGuard(daily_spend=0.0, monthly_spend=0.0)
        fake_client = _build_fake_async_openai()

        with patch.object(provider, "_build_sdk_client", return_value=fake_client):
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="gpt-4o-mini",
                _provider_config=dict(_ENABLED_CLOUD_CFG),
                _cost_guard=guard,
            )

        assert len(guard.recorded) == 1
        rec = guard.recorded[0]
        assert rec["provider"] == "openai_compat"
        assert rec["model"] == "gpt-4o-mini"
        assert rec["prompt_tokens"] == 12
        assert rec["completion_tokens"] == 8
        # gpt-4o-mini rates: $0.00015 input, $0.0006 output per 1K tokens.
        # 12/1000 * 0.00015 + 8/1000 * 0.0006 = 1.8e-6 + 4.8e-6 = 6.6e-6
        assert rec["cost_usd"] == pytest.approx(0.0000066, rel=1e-3)


# ---------------------------------------------------------------------------
# OpenAICompatProvider — embed() round-trip
# ---------------------------------------------------------------------------


class TestOpenAICompatEmbed:
    @pytest.mark.asyncio
    async def test_openai_compat_embed_round_trip(self):
        """Embeddings call goes through the SDK + cost-guard wraps it."""
        provider = OpenAICompatProvider()
        guard = _RecordingCostGuard()
        fake_client = _build_fake_async_openai(embed_payload=_ok_embed_payload())

        with patch.object(provider, "_build_sdk_client", return_value=fake_client):
            vec = await provider.embed_with(
                text="hello world",
                model="text-embedding-3-small",
                _provider_config=dict(_ENABLED_CLOUD_CFG),
                _cost_guard=guard,
            )

        assert vec == [0.1, 0.2, 0.3]
        fake_client.embeddings.create.assert_awaited_once()
        call = fake_client.embeddings.create.await_args
        assert call.kwargs["input"] == "hello world"
        assert call.kwargs["model"] == "text-embedding-3-small"

        # Cost-guard wrapped the call.
        assert len(guard.preflighted) == 1
        assert len(guard.recorded) == 1
        assert guard.recorded[0]["phase"] == "openai_compat.embed"

    @pytest.mark.asyncio
    async def test_embed_raises_on_empty_response(self):
        provider = OpenAICompatProvider()
        fake_client = _build_fake_async_openai(embed_payload={"data": []})

        with patch.object(provider, "_build_sdk_client", return_value=fake_client):
            with pytest.raises(ValueError, match="embed response"):
                await provider.embed_with(
                    text="hi",
                    model="text-embedding-3-small",
                    _provider_config=dict(_ENABLED_LOCAL_CFG),
                    _cost_guard=_RecordingCostGuard(),
                )


# ---------------------------------------------------------------------------
# OpenAICompatProvider — config resolution + secret handling
# ---------------------------------------------------------------------------


class _FakeSiteConfig:
    """Site-config stub that returns pre-seeded secrets via get_secret."""

    def __init__(self, secrets: dict[str, str] | None = None):
        self._secrets = secrets or {}
        self._pool = None  # mirrors SiteConfig._pool

    def get(self, key: str, default: Any = None) -> Any:
        return default

    async def get_secret(self, key: str, default: str = "") -> str:
        return self._secrets.get(key, default)


class TestOpenAICompatConfig:
    @pytest.mark.asyncio
    async def test_api_key_pulled_via_get_secret_first(self):
        """Encrypted secret beats the plaintext config row."""
        provider = OpenAICompatProvider()
        sc = _FakeSiteConfig(
            secrets={"plugin.llm_provider.openai_compat.api_key": "sk-from-secret"},
        )
        cfg = await provider._resolve_config({
            "_site_config": sc,
            "_provider_config": dict(_ENABLED_CLOUD_CFG, api_key="sk-plain-fallback"),
        })
        assert cfg["api_key"] == "sk-from-secret"

    @pytest.mark.asyncio
    async def test_api_key_falls_back_to_plain_config(self):
        provider = OpenAICompatProvider()
        sc = _FakeSiteConfig(secrets={})
        cfg = await provider._resolve_config({
            "_site_config": sc,
            "_provider_config": dict(_ENABLED_CLOUD_CFG, api_key="sk-plain"),
        })
        assert cfg["api_key"] == "sk-plain"

    @pytest.mark.asyncio
    async def test_timeout_kwarg_beats_config(self):
        provider = OpenAICompatProvider()
        cfg = await provider._resolve_config({
            "_provider_config": dict(_ENABLED_LOCAL_CFG, request_timeout_s=200),
            "timeout_s": 15,
        })
        assert cfg["timeout"] == 15

    @pytest.mark.asyncio
    async def test_default_when_neither_set(self):
        provider = OpenAICompatProvider()
        cfg = await provider._resolve_config({})
        # When both _provider_config and timeout_s kwarg absent, fall
        # back to the module default (120).
        assert cfg["timeout"] == 120

    def test_is_local_helper(self):
        assert OpenAICompatProvider.is_local("http://localhost:11434/v1") is True
        assert OpenAICompatProvider.is_local("http://127.0.0.1:8080") is True
        assert OpenAICompatProvider.is_local("http://host.docker.internal:9999") is True
        assert OpenAICompatProvider.is_local("https://api.openai.com/v1") is False


# ---------------------------------------------------------------------------
# OllamaNativeProvider — delegation coverage (unchanged)
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
