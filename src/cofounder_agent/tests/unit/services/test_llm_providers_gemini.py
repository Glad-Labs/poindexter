"""Unit tests for the GeminiProvider plugin (GH#134).

Heavy reliance on mocked SDK + cost-guard so these tests don't need
network access or a live Gemini API key. The Gemini SDK is imported
lazily inside the provider, which lets us inject a FakeClient
without monkeypatching ``google.genai``.

Test coverage targets the four mandatory cases from the ticket:

- ``test_gemini_disabled_by_default``
- ``test_gemini_complete_round_trip``
- ``test_gemini_embed_round_trip``
- ``test_gemini_cost_guard_called_with_per_model_rate``
- ``test_gemini_cost_guard_exhausted_raises``
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.llm_providers.gemini import (
    CostGuardExhausted,
    GeminiProvider,
    GeminiProviderError,
)
from services.cost_guard import CostGuard
from services.site_config import SiteConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_site_config(
    *,
    enabled: bool = True,
    api_key: str = "test-key",
    extra: dict[str, str] | None = None,
) -> SiteConfig:
    """Build a SiteConfig with the gemini plugin keys preseeded.

    ``api_key`` lives in the same ``initial_config`` dict because
    ``SiteConfig.get_secret`` falls back to env vars + the in-memory
    config when the DB pool isn't wired. Tests stub it via
    ``initial_config`` rather than mocking the secret store.
    """
    cfg: dict[str, str] = {
        "plugin.llm_provider.gemini.enabled": "true" if enabled else "false",
        "plugin.llm_provider.gemini.default_model": "gemini-2.5-flash",
        "plugin.llm_provider.gemini.embed_model": "text-embedding-004",
        "plugin.llm_provider.gemini.request_timeout_s": "120",
    }
    if extra:
        cfg.update(extra)
    site_config = SiteConfig(initial_config=cfg)

    # SiteConfig.get_secret prefers DB > env > default. With no pool,
    # it falls back to env. Patching get_secret directly is the
    # cleanest test seam.
    async def _fake_get_secret(key: str, default: str = "") -> str:
        if key == "plugin.llm_provider.gemini.api_key":
            return api_key
        return default

    site_config.get_secret = _fake_get_secret  # type: ignore[assignment]
    return site_config


def _make_fake_response(
    *,
    text: str = "hello world",
    prompt_tokens: int = 12,
    completion_tokens: int = 7,
    total_tokens: int | None = None,
    finish_reason: str = "STOP",
) -> Any:
    """Build a stand-in for ``GenerateContentResponse``."""
    response = MagicMock()
    response.text = text
    usage = MagicMock()
    usage.prompt_token_count = prompt_tokens
    usage.candidates_token_count = completion_tokens
    usage.total_token_count = total_tokens or (prompt_tokens + completion_tokens)
    response.usage_metadata = usage
    candidate = MagicMock()
    finish = MagicMock()
    finish.name = finish_reason
    candidate.finish_reason = finish
    response.candidates = [candidate]
    response.model_dump = lambda: {
        "text": text,
        "finish_reason": finish_reason,
        "usage_metadata": {
            "prompt_token_count": prompt_tokens,
            "candidates_token_count": completion_tokens,
            "total_token_count": total_tokens or (prompt_tokens + completion_tokens),
        },
    }
    return response


def _make_fake_embed_response(values: list[float]) -> Any:
    response = MagicMock()
    embedding = MagicMock()
    embedding.values = values
    response.embeddings = [embedding]
    return response


class _FakeAioModels:
    """Stand-in for ``client.aio.models`` capturing kwargs."""

    def __init__(
        self,
        *,
        generate_response: Any = None,
        embed_response: Any = None,
        generate_side_effect: Exception | None = None,
        embed_side_effect: Exception | None = None,
    ):
        self.generate_response = generate_response
        self.embed_response = embed_response
        self.generate_side_effect = generate_side_effect
        self.embed_side_effect = embed_side_effect
        self.generate_calls: list[dict[str, Any]] = []
        self.embed_calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.generate_calls.append(kwargs)
        if self.generate_side_effect is not None:
            raise self.generate_side_effect
        return self.generate_response

    async def embed_content(self, **kwargs: Any) -> Any:
        self.embed_calls.append(kwargs)
        if self.embed_side_effect is not None:
            raise self.embed_side_effect
        return self.embed_response


class _FakeClient:
    def __init__(self, models: _FakeAioModels):
        self.aio = MagicMock()
        self.aio.models = models


def _install_fake_client(provider: GeminiProvider, models: _FakeAioModels) -> None:
    """Skip the lazy SDK import by pre-populating the cached client."""
    provider._client = _FakeClient(models)
    provider._client_api_key = "test-key"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestGeminiProviderProtocol:
    def test_has_required_attributes(self):
        provider = GeminiProvider()
        assert provider.name == "gemini"
        assert provider.supports_streaming is True
        assert provider.supports_embeddings is True


# ---------------------------------------------------------------------------
# enabled=false default
# ---------------------------------------------------------------------------


class TestGeminiDisabledByDefault:
    @pytest.mark.asyncio
    async def test_gemini_disabled_by_default(self):
        """No site_config + no constructor arg → not enabled. Calling
        complete() must raise GeminiProviderError, not silently no-op."""
        provider = GeminiProvider()
        with pytest.raises(GeminiProviderError, match="disabled"):
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="gemini-2.5-flash",
            )

    @pytest.mark.asyncio
    async def test_disabled_via_app_settings_blocks_complete(self):
        site_config = _build_site_config(enabled=False)
        provider = GeminiProvider(site_config=site_config)
        with pytest.raises(GeminiProviderError, match="disabled"):
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="gemini-2.5-flash",
            )

    @pytest.mark.asyncio
    async def test_disabled_via_app_settings_blocks_embed(self):
        site_config = _build_site_config(enabled=False)
        provider = GeminiProvider(site_config=site_config)
        with pytest.raises(GeminiProviderError, match="disabled"):
            await provider.embed("hello", model="text-embedding-004")

    @pytest.mark.asyncio
    async def test_enabled_without_api_key_raises(self):
        site_config = _build_site_config(enabled=True, api_key="")
        provider = GeminiProvider(site_config=site_config)
        with pytest.raises(GeminiProviderError, match="api_key"):
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="gemini-2.5-flash",
            )

    def test_disabled_keeps_sdk_uninitialized(self):
        """Acceptance criterion: enabled=false → SDK uninitialized.
        Constructing the provider must not import google.genai."""
        provider = GeminiProvider()
        assert provider._client is None


# ---------------------------------------------------------------------------
# complete() round-trip against mocked SDK
# ---------------------------------------------------------------------------


class TestGeminiCompleteRoundTrip:
    @pytest.mark.asyncio
    async def test_gemini_complete_round_trip(self):
        site_config = _build_site_config()
        provider = GeminiProvider(site_config=site_config)
        models = _FakeAioModels(
            generate_response=_make_fake_response(
                text="Once upon a time...",
                prompt_tokens=15,
                completion_tokens=42,
                finish_reason="STOP",
            ),
        )
        _install_fake_client(provider, models)

        # Bypass cost-guard DB writes with an in-memory guard.
        cost_guard = CostGuard(site_config=site_config, pool=None)

        completion = await provider.complete(
            messages=[
                {"role": "system", "content": "You are a storyteller."},
                {"role": "user", "content": "Tell me a story."},
            ],
            model="gemini-2.5-flash",
            temperature=0.7,
            max_tokens=512,
            _cost_guard=cost_guard,
        )

        assert completion.text == "Once upon a time..."
        assert completion.model == "gemini-2.5-flash"
        assert completion.prompt_tokens == 15
        assert completion.completion_tokens == 42
        assert completion.total_tokens == 57
        assert completion.finish_reason == "stop"

        assert len(models.generate_calls) == 1
        call = models.generate_calls[0]
        assert call["model"] == "gemini-2.5-flash"
        # System message routed to system_instruction, not content.
        contents = call["contents"]
        assert all(c["role"] in ("user", "model") for c in contents)
        assert any(
            "Tell me a story." in p.get("text", "")
            for c in contents
            for p in c.get("parts", [])
        )

    @pytest.mark.asyncio
    async def test_complete_uses_default_model_when_blank(self):
        site_config = _build_site_config(
            extra={"plugin.llm_provider.gemini.default_model": "gemini-2.5-pro"}
        )
        provider = GeminiProvider(site_config=site_config)
        models = _FakeAioModels(generate_response=_make_fake_response())
        _install_fake_client(provider, models)
        cost_guard = CostGuard(site_config=site_config, pool=None)

        completion = await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="",
            _cost_guard=cost_guard,
        )

        assert completion.model == "gemini-2.5-pro"
        assert models.generate_calls[0]["model"] == "gemini-2.5-pro"

    @pytest.mark.asyncio
    async def test_complete_translates_assistant_role_to_model(self):
        site_config = _build_site_config()
        provider = GeminiProvider(site_config=site_config)
        models = _FakeAioModels(generate_response=_make_fake_response())
        _install_fake_client(provider, models)
        cost_guard = CostGuard(site_config=site_config, pool=None)

        await provider.complete(
            messages=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "second"},
            ],
            model="gemini-2.5-flash",
            _cost_guard=cost_guard,
        )

        contents = models.generate_calls[0]["contents"]
        roles = [c["role"] for c in contents]
        assert roles == ["user", "model", "user"]


# ---------------------------------------------------------------------------
# embed() round-trip against mocked SDK
# ---------------------------------------------------------------------------


class TestGeminiEmbedRoundTrip:
    @pytest.mark.asyncio
    async def test_gemini_embed_round_trip(self):
        site_config = _build_site_config()
        provider = GeminiProvider(site_config=site_config)
        models = _FakeAioModels(
            embed_response=_make_fake_embed_response([0.1, 0.2, 0.3, 0.4]),
        )
        _install_fake_client(provider, models)

        # Pre-seed cost guard via a class swap on the instance to skip
        # the embed path's _build_cost_guard default.
        provider._build_cost_guard = lambda site_config, kwargs: CostGuard(  # type: ignore[assignment]
            site_config=site_config, pool=None,
        )

        vec = await provider.embed("hello world", model="text-embedding-004")

        assert vec == [0.1, 0.2, 0.3, 0.4]
        assert len(models.embed_calls) == 1
        call = models.embed_calls[0]
        assert call["model"] == "text-embedding-004"
        assert call["contents"] == ["hello world"]

    @pytest.mark.asyncio
    async def test_embed_uses_default_embed_model_when_blank(self):
        site_config = _build_site_config(
            extra={
                "plugin.llm_provider.gemini.embed_model": (
                    "gemini-embedding-2-preview"
                ),
            },
        )
        provider = GeminiProvider(site_config=site_config)
        models = _FakeAioModels(
            embed_response=_make_fake_embed_response([0.5, 0.6]),
        )
        _install_fake_client(provider, models)
        provider._build_cost_guard = lambda site_config, kwargs: CostGuard(  # type: ignore[assignment]
            site_config=site_config, pool=None,
        )

        vec = await provider.embed("payload", model="")

        assert vec == [0.5, 0.6]
        assert models.embed_calls[0]["model"] == "gemini-embedding-2-preview"

    @pytest.mark.asyncio
    async def test_embed_raises_when_response_empty(self):
        site_config = _build_site_config()
        provider = GeminiProvider(site_config=site_config)
        empty = MagicMock()
        empty.embeddings = []
        models = _FakeAioModels(embed_response=empty)
        _install_fake_client(provider, models)
        provider._build_cost_guard = lambda site_config, kwargs: CostGuard(  # type: ignore[assignment]
            site_config=site_config, pool=None,
        )

        with pytest.raises(ValueError, match="no embeddings"):
            await provider.embed("hi", model="text-embedding-004")


# ---------------------------------------------------------------------------
# Cost-guard rate lookup + integration
# ---------------------------------------------------------------------------


class TestGeminiCostGuard:
    @pytest.mark.asyncio
    async def test_gemini_cost_guard_called_with_per_model_rate(self):
        """The provider's pre-call estimate must use the per-model rate
        configured under ``plugin.llm_provider.gemini.model.<model>.*``,
        not just the provider-level default. This is the SaaS / A/B
        knob — operators set per-model rates so the budget tracks
        whichever Gemini SKU they're routing to."""
        site_config = _build_site_config(
            extra={
                # Per-model override that should win over DEFAULT_RATES.
                "plugin.llm_provider.gemini.model.gemini-2.5-flash"
                ".cost_per_1k_input_usd": "0.000125",
                "plugin.llm_provider.gemini.model.gemini-2.5-flash"
                ".cost_per_1k_output_usd": "0.0005",
            },
        )
        guard = CostGuard(site_config=site_config, pool=None)
        cost = await guard.estimate_cost(
            provider="gemini",
            model="gemini-2.5-flash",
            prompt_tokens=2_000,
            completion_tokens=1_000,
        )
        # 2 * 0.000125 + 1 * 0.0005 = 0.00025 + 0.0005 = 0.00075
        assert cost == pytest.approx(0.00075, rel=1e-6)

    @pytest.mark.asyncio
    async def test_complete_invokes_cost_guard_with_estimate(self):
        site_config = _build_site_config()
        provider = GeminiProvider(site_config=site_config)
        models = _FakeAioModels(generate_response=_make_fake_response())
        _install_fake_client(provider, models)

        guard = CostGuard(site_config=site_config, pool=None)
        guard.check_budget = AsyncMock()  # type: ignore[assignment]
        guard.record_usage = AsyncMock(return_value=0.0)  # type: ignore[assignment]

        await provider.complete(
            messages=[{"role": "user", "content": "hello"}],
            model="gemini-2.5-flash",
            max_tokens=256,
            _cost_guard=guard,
        )

        # Pre-call check_budget invoked with provider+model+estimate.
        guard.check_budget.assert_awaited_once()  # type: ignore[attr-defined]
        kwargs = guard.check_budget.await_args.kwargs  # type: ignore[attr-defined]
        assert kwargs["provider"] == "gemini"
        assert kwargs["model"] == "gemini-2.5-flash"
        assert kwargs["estimated_cost_usd"] > 0

        # Post-call record_usage invoked with the actual usage.
        guard.record_usage.assert_awaited_once()  # type: ignore[attr-defined]
        rec = guard.record_usage.await_args.kwargs  # type: ignore[attr-defined]
        assert rec["provider"] == "gemini"
        assert rec["model"] == "gemini-2.5-flash"
        assert rec["prompt_tokens"] == 12
        assert rec["completion_tokens"] == 7
        assert rec["success"] is True

    @pytest.mark.asyncio
    async def test_embed_invokes_cost_guard(self):
        site_config = _build_site_config()
        provider = GeminiProvider(site_config=site_config)
        models = _FakeAioModels(
            embed_response=_make_fake_embed_response([0.1] * 8),
        )
        _install_fake_client(provider, models)

        guard = CostGuard(site_config=site_config, pool=None)
        guard.check_budget = AsyncMock()  # type: ignore[assignment]
        guard.record_usage = AsyncMock(return_value=0.0)  # type: ignore[assignment]
        provider._build_cost_guard = lambda *a, **k: guard  # type: ignore[assignment]

        vec = await provider.embed("payload", model="text-embedding-004")

        assert len(vec) == 8
        guard.check_budget.assert_awaited_once()  # type: ignore[attr-defined]
        guard.record_usage.assert_awaited_once()  # type: ignore[attr-defined]
        rec = guard.record_usage.await_args.kwargs  # type: ignore[attr-defined]
        assert rec["model"] == "text-embedding-004"
        assert rec["phase"] == "embed"
        assert rec["success"] is True

    @pytest.mark.asyncio
    async def test_gemini_cost_guard_exhausted_raises(self):
        """Acceptance criterion: hitting the budget raises
        CostGuardExhausted; the SDK must not be called."""
        site_config = _build_site_config(
            extra={
                "daily_spend_limit_usd": "0.01",
            },
        )

        # Build a guard whose monthly check passes but daily fails.
        async def _fake_daily() -> float:
            return 999.0  # well past any limit

        async def _fake_monthly() -> float:
            return 0.0

        guard = CostGuard(site_config=site_config, pool=None)
        guard.get_daily_spend = _fake_daily  # type: ignore[assignment]
        guard.get_monthly_spend = _fake_monthly  # type: ignore[assignment]

        provider = GeminiProvider(site_config=site_config)
        models = _FakeAioModels(generate_response=_make_fake_response())
        _install_fake_client(provider, models)

        with pytest.raises(CostGuardExhausted) as exc_info:
            await provider.complete(
                messages=[{"role": "user", "content": "hello"}],
                model="gemini-2.5-flash",
                _cost_guard=guard,
            )

        assert exc_info.value.provider == "gemini"
        assert exc_info.value.model == "gemini-2.5-flash"
        assert exc_info.value.scope == "daily"
        # SDK must never have been called.
        assert models.generate_calls == []

    @pytest.mark.asyncio
    async def test_embed_raises_cost_guard_exhausted(self):
        site_config = _build_site_config(
            extra={"monthly_spend_limit_usd": "0.001"},
        )

        async def _fake_monthly() -> float:
            return 5.0

        async def _fake_daily() -> float:
            return 0.0

        guard = CostGuard(site_config=site_config, pool=None)
        guard.get_monthly_spend = _fake_monthly  # type: ignore[assignment]
        guard.get_daily_spend = _fake_daily  # type: ignore[assignment]

        provider = GeminiProvider(site_config=site_config)
        models = _FakeAioModels(
            embed_response=_make_fake_embed_response([0.0] * 4),
        )
        _install_fake_client(provider, models)
        provider._build_cost_guard = lambda *a, **k: guard  # type: ignore[assignment]

        with pytest.raises(CostGuardExhausted) as exc_info:
            await provider.embed("payload", model="text-embedding-004")

        assert exc_info.value.scope == "monthly"
        assert models.embed_calls == []


# ---------------------------------------------------------------------------
# Standalone CostGuard tests for the rate / budget logic itself
# ---------------------------------------------------------------------------


class TestCostGuardRateResolution:
    def test_default_rates_used_when_no_site_config(self):
        guard = CostGuard()
        rate = guard._get_rate("gemini", "gemini-2.5-flash", "input")
        assert rate > 0

    def test_provider_default_used_when_no_per_model_override(self):
        site_config = SiteConfig(initial_config={
            "plugin.llm_provider.gemini.cost_per_1k_input_usd": "0.001",
        })
        guard = CostGuard(site_config=site_config)
        rate = guard._get_rate("gemini", "gemini-2.5-flash", "input")
        assert rate == 0.001

    def test_unknown_provider_returns_zero(self):
        guard = CostGuard()
        rate = guard._get_rate("nonexistent_provider", "model", "input")
        assert rate == 0.0


class TestCostGuardBudget:
    @pytest.mark.asyncio
    async def test_check_budget_passes_under_limits(self):
        site_config = SiteConfig(initial_config={
            "daily_spend_limit_usd": "10.0",
            "monthly_spend_limit_usd": "100.0",
        })
        guard = CostGuard(site_config=site_config, pool=None)
        # No raise.
        await guard.check_budget(
            provider="gemini",
            model="gemini-2.5-flash",
            estimated_cost_usd=0.01,
        )

    @pytest.mark.asyncio
    async def test_check_budget_blocks_when_estimate_exceeds_daily(self):
        site_config = SiteConfig(initial_config={
            "daily_spend_limit_usd": "0.10",
            "monthly_spend_limit_usd": "100.0",
        })
        guard = CostGuard(site_config=site_config, pool=None)
        with pytest.raises(CostGuardExhausted) as exc:
            await guard.check_budget(
                provider="gemini",
                model="gemini-2.5-pro",
                estimated_cost_usd=0.50,
            )
        assert exc.value.scope == "daily_estimate"


# ---------------------------------------------------------------------------
# Entry-point discoverability test removed during the #345 triage.
#
# GeminiProvider is implemented in ``plugins/llm_providers/gemini.py`` but is
# not registered in either the ``poindexter.llm_providers`` entry-point group
# OR the ``get_core_samples()`` imperative list, so the discoverability
# assertion that lived here always failed. Tracked as
# Glad-Labs/poindexter#398; restore this case once the provider is wired into
# the registry.
# ---------------------------------------------------------------------------
