"""Unit tests for the AnthropicProvider plugin (issue #133).

Mocks the official ``anthropic`` SDK so these tests run without a
live API key or network. Coverage targets the four explicit
acceptance criteria from #133:

1. Disabled-by-default — ``complete()`` raises until enabled.
2. End-to-end ``complete()`` round trip against a mocked SDK.
3. Cost-guard pre-check is invoked with a per-model rate.
4. Cost-guard exhaustion raises a typed ``CostGuardExhausted``.

Plus the recommended:
5. Prompt caching annotation lands on the system message by default.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Stub the ``anthropic`` SDK BEFORE importing the provider so the
# lazy import inside ``_get_client`` resolves to a controllable mock
# regardless of whether the real SDK is installed in the test env.
_anthropic_module = MagicMock(name="anthropic")
_anthropic_module.AsyncAnthropic = MagicMock(name="AsyncAnthropic")
sys.modules.setdefault("anthropic", _anthropic_module)

from plugins import LLMProvider  # noqa: E402,I001 — tests must register the SDK stub above before importing the provider
from plugins.llm_providers.anthropic import (  # noqa: E402,I001
    _PER_MODEL_RATES,
    AnthropicProvider,
    AnthropicProviderDisabled,
    CostGuardExhausted,
    _calc_cost_usd,
    _rates_for_model,
    _split_system_and_messages,
)
from services.site_config import SiteConfig  # noqa: E402,I001


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    *,
    text: str = "ok",
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_read: int = 0,
    cache_creation: int = 0,
    model: str = "claude-haiku-4-5",
    stop_reason: str = "end_turn",
):
    """Build a SimpleNamespace shaped like ``anthropic.types.Message``."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        model=model,
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
        ),
    )


def _enabled_site_config(api_key: str = "sk-ant-test") -> SiteConfig:
    """SiteConfig pre-seeded so the provider sees ``enabled=true``."""
    sc = SiteConfig(initial_config={
        "plugin.llm_provider.anthropic.enabled": "true",
        "plugin.llm_provider.anthropic.default_model": "claude-haiku-4-5",
        "plugin.llm_provider.anthropic.request_timeout_s": "30",
    })
    # Patch get_secret to return our test key without hitting a DB.
    async def _fake_secret(key: str, default: str = "") -> str:
        if key == "plugin.llm_provider.anthropic.api_key":
            return api_key
        return default
    sc.get_secret = _fake_secret  # type: ignore[assignment]
    return sc


def _patch_provider_client(provider: AnthropicProvider, response):
    """Wire a mocked AsyncAnthropic client onto ``provider``.

    Skips the lazy import + cache-key tracking by overriding
    ``_get_client`` directly. Returns the ``messages.create`` mock so
    tests can assert on the SDK call.
    """
    create_mock = AsyncMock(return_value=response)
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=create_mock))
    provider._get_client = MagicMock(return_value=fake_client)  # type: ignore[assignment]
    return create_mock


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestAnthropicProviderProtocol:
    def test_conforms_to_llm_provider(self):
        assert isinstance(AnthropicProvider(), LLMProvider)

    def test_has_required_attributes(self):
        p = AnthropicProvider()
        assert p.name == "anthropic"
        # Streaming is intentionally False — issue #133 marks it OOS.
        assert p.supports_streaming is False
        # Anthropic doesn't sell embeddings.
        assert p.supports_embeddings is False


# ---------------------------------------------------------------------------
# 1. Disabled by default
# ---------------------------------------------------------------------------


class TestAnthropicDisabledByDefault:
    @pytest.mark.asyncio
    async def test_anthropic_disabled_by_default(self):
        """A fresh AnthropicProvider() with no config refuses calls.

        This is the load-bearing guarantee that ``pip install`` on a
        fresh box never sends a paid API call. Operators must flip
        ``enabled = true`` in app_settings before the provider does
        anything beyond log warnings.
        """
        p = AnthropicProvider()
        with pytest.raises(AnthropicProviderDisabled):
            await p.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="claude-haiku-4-5",
            )

    @pytest.mark.asyncio
    async def test_disabled_via_provider_config_flag(self):
        p = AnthropicProvider()
        with pytest.raises(AnthropicProviderDisabled):
            await p.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="claude-haiku-4-5",
                _provider_config={"enabled": False, "api_key": "sk-test"},
            )

    @pytest.mark.asyncio
    async def test_enabled_but_no_api_key_raises(self):
        p = AnthropicProvider()
        with pytest.raises(AnthropicProviderDisabled, match="api_key"):
            await p.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="claude-haiku-4-5",
                _provider_config={"enabled": True, "api_key": ""},
            )


# ---------------------------------------------------------------------------
# 2. End-to-end complete() round-trip
# ---------------------------------------------------------------------------


class TestAnthropicCompleteRoundTrip:
    @pytest.mark.asyncio
    async def test_anthropic_complete_round_trip(self):
        """SDK round-trip — provider → mocked client → Completion."""
        sc = _enabled_site_config()
        p = AnthropicProvider(site_config=sc)
        response = _make_response(
            text="hello world",
            input_tokens=42,
            output_tokens=7,
            model="claude-haiku-4-5",
            stop_reason="end_turn",
        )
        create_mock = _patch_provider_client(p, response)

        completion = await p.complete(
            messages=[
                {"role": "system", "content": "You are a helper."},
                {"role": "user", "content": "Say hi."},
            ],
            model="claude-haiku-4-5",
            max_tokens=100,
            temperature=0.5,
        )

        # Result mapping
        assert completion.text == "hello world"
        assert completion.model == "claude-haiku-4-5"
        assert completion.prompt_tokens == 42
        assert completion.completion_tokens == 7
        assert completion.total_tokens == 49
        assert completion.finish_reason == "end_turn"
        assert "cost_usd" in completion.raw

        # SDK was actually invoked — assert on the kwargs the provider
        # forwarded so future shape changes break loudly.
        assert create_mock.await_count == 1
        kwargs = create_mock.await_args.kwargs
        assert kwargs["model"] == "claude-haiku-4-5"
        assert kwargs["max_tokens"] == 100
        assert kwargs["temperature"] == 0.5
        # System prompt landed in the top-level ``system`` field.
        assert kwargs["system"] is not None
        # User messages don't include the system message.
        assert all(m["role"] != "system" for m in kwargs["messages"])
        assert kwargs["messages"][0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_default_model_used_when_caller_passes_empty(self):
        sc = _enabled_site_config()
        p = AnthropicProvider(site_config=sc)
        create_mock = _patch_provider_client(p, _make_response())

        await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="",
        )

        assert create_mock.await_args.kwargs["model"] == "claude-haiku-4-5"

    @pytest.mark.asyncio
    async def test_adjacent_user_messages_coalesced(self):
        sc = _enabled_site_config()
        p = AnthropicProvider(site_config=sc)
        create_mock = _patch_provider_client(p, _make_response())

        await p.complete(
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "user", "content": "world"},
            ],
            model="claude-haiku-4-5",
        )

        msgs = create_mock.await_args.kwargs["messages"]
        # Anthropic rejects consecutive same-role turns — provider must
        # coalesce them before sending.
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert "hello" in msgs[0]["content"] and "world" in msgs[0]["content"]


# ---------------------------------------------------------------------------
# 3. Cost-guard called with the per-model rate
# ---------------------------------------------------------------------------


class TestAnthropicCostGuardPerModelRate:
    @pytest.mark.asyncio
    async def test_anthropic_cost_guard_called_with_per_model_rate(self):
        """Cost-guard pre-check fires with an estimate based on the
        model's rate row. We assert the *order of magnitude* matches
        the rate table — exact-equality would couple this test to the
        char-based estimator's heuristics, which is overspecified."""
        sc = _enabled_site_config()
        p = AnthropicProvider(site_config=sc)
        _patch_provider_client(p, _make_response())

        check_mock = AsyncMock()
        p._cost_guard_check = check_mock  # type: ignore[assignment]

        await p.complete(
            messages=[{"role": "user", "content": "x" * 4000}],
            model="claude-opus-4-7",
            max_tokens=2000,
        )

        assert check_mock.await_count == 1
        call_kwargs = check_mock.await_args.kwargs
        assert call_kwargs["model"] == "claude-opus-4-7"
        # Opus 4.7 list price is the highest of the 4.x family — the
        # estimate must reflect that. With 1000 input tokens (4000 chars
        # / 4) at $15/M input + 2000 output tokens at $75/M output the
        # estimate is roughly $0.165 — definitely well above the haiku
        # estimate for the same payload.
        assert call_kwargs["estimated_cost_usd"] > 0.05
        # Sanity: the rate table entry exists for this model.
        assert "claude-opus-4-7" in _PER_MODEL_RATES

    @pytest.mark.asyncio
    async def test_post_call_cost_record_uses_actual_usage(self):
        sc = _enabled_site_config()
        p = AnthropicProvider(site_config=sc)
        _patch_provider_client(p, _make_response(
            input_tokens=100,
            output_tokens=50,
            cache_read=0,
            cache_creation=0,
            model="claude-haiku-4-5",
        ))

        record_mock = AsyncMock()
        p._cost_guard_record = record_mock  # type: ignore[assignment]

        await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-haiku-4-5",
        )

        record_mock.assert_awaited_once()
        kwargs = record_mock.await_args.kwargs
        # Haiku 4.5 = $1/M input + $5/M output
        # 100 in @ 1.00/M = $0.0001; 50 out @ 5.00/M = $0.00025; total $0.00035
        expected = 100 * 1.00 / 1_000_000 + 50 * 5.00 / 1_000_000
        assert kwargs["actual_cost_usd"] == pytest.approx(expected, rel=1e-6)
        assert kwargs["model"] == "claude-haiku-4-5"
        assert kwargs["input_tokens"] == 100
        assert kwargs["output_tokens"] == 50

    def test_rate_table_has_all_documented_4x_models(self):
        """Whitelist guard — refactors that drop a model from the
        table get caught here before they ship a silent fallback."""
        for required in (
            "claude-haiku-4-5",
            "claude-sonnet-4-6",
            "claude-opus-4-7",
        ):
            assert required in _PER_MODEL_RATES, (
                f"per-model rate row missing for {required}"
            )

    def test_unknown_model_falls_back_to_conservative_rate(self):
        rates = _rates_for_model("claude-unknown-model")
        # Conservative fallback matches the most expensive family
        # member so we never under-estimate.
        assert rates["input"] >= 15.0
        assert rates["output"] >= 75.0

    def test_dated_snapshot_resolves_to_base_model_rates(self):
        rates = _rates_for_model("claude-haiku-4-5-20260301")
        assert rates == _PER_MODEL_RATES["claude-haiku-4-5"]


# ---------------------------------------------------------------------------
# 4. Cost-guard exhausted raises typed exception
# ---------------------------------------------------------------------------


class TestAnthropicCostGuardExhausted:
    @pytest.mark.asyncio
    async def test_anthropic_cost_guard_exhausted_raises(self):
        """When the cost-guard refuses, the call surfaces
        ``CostGuardExhausted`` to the caller — NO silent fallback."""
        sc = _enabled_site_config()
        p = AnthropicProvider(site_config=sc)
        # SDK call should never happen — assert that by failing the
        # mock if it does.
        create_mock = _patch_provider_client(p, _make_response())

        async def _refuse(*, model: str, estimated_cost_usd: float) -> None:
            raise CostGuardExhausted(
                "budget blown",
                model=model,
                estimated_cost_usd=estimated_cost_usd,
            )

        p._cost_guard_check = _refuse  # type: ignore[assignment]

        with pytest.raises(CostGuardExhausted) as exc_info:
            await p.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="claude-haiku-4-5",
            )

        assert exc_info.value.provider == "anthropic"
        assert exc_info.value.model == "claude-haiku-4-5"
        assert exc_info.value.estimated_cost_usd > 0
        # Critically: SDK was never invoked.
        assert create_mock.await_count == 0


# ---------------------------------------------------------------------------
# 5. Prompt caching applied to the system message by default
# ---------------------------------------------------------------------------


class TestAnthropicPromptCaching:
    @pytest.mark.asyncio
    async def test_anthropic_prompt_caching_applied_to_system_message(self):
        """System prompt gets ``cache_control: {type: ephemeral}`` by
        default — that's the 5-min TTL prompt-caching breakpoint
        Anthropic exposes. Free latency + cost win for re-used
        system prompts (which the pipeline always does)."""
        sc = _enabled_site_config()
        p = AnthropicProvider(site_config=sc)
        create_mock = _patch_provider_client(p, _make_response())

        await p.complete(
            messages=[
                {"role": "system", "content": "You are a careful editor."},
                {"role": "user", "content": "hi"},
            ],
            model="claude-haiku-4-5",
        )

        system = create_mock.await_args.kwargs["system"]
        # System should be a list of content blocks (NOT a plain string)
        # so the cache_control breakpoint actually attaches.
        assert isinstance(system, list)
        assert system[0]["type"] == "text"
        assert system[0]["text"] == "You are a careful editor."
        assert system[0].get("cache_control") == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_prompt_caching_disabled_via_config_flag(self):
        sc = SiteConfig(initial_config={
            "plugin.llm_provider.anthropic.enabled": "true",
            "plugin.llm_provider.anthropic.prompt_caching": "false",
        })
        async def _fake_secret(key: str, default: str = "") -> str:
            return "sk-test" if "api_key" in key else default
        sc.get_secret = _fake_secret  # type: ignore[assignment]

        p = AnthropicProvider(site_config=sc)
        create_mock = _patch_provider_client(p, _make_response())

        await p.complete(
            messages=[
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": "hi"},
            ],
            model="claude-haiku-4-5",
        )

        system = create_mock.await_args.kwargs["system"]
        # Still a list (Anthropic accepts both string + list shapes;
        # we always emit a list for consistency), but no cache_control.
        assert isinstance(system, list)
        assert "cache_control" not in system[0]

    def test_split_helper_caches_when_flag_true(self):
        system, user = _split_system_and_messages(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ],
            prompt_caching=True,
        )
        assert system is not None
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert user == [{"role": "user", "content": "hi"}]

    def test_split_helper_no_system_returns_none(self):
        system, user = _split_system_and_messages(
            [{"role": "user", "content": "hi"}],
            prompt_caching=True,
        )
        assert system is None
        assert user == [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------------------
# Bonus: cost math sanity
# ---------------------------------------------------------------------------


class TestCostCalcMath:
    def test_calc_cost_usd_basic(self):
        rates = {"input": 1.00, "output": 5.00, "cached_input": 0.10}
        cost = _calc_cost_usd(
            rates,
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cached_input_tokens=0,
        )
        assert cost == pytest.approx(6.00)

    def test_calc_cost_usd_with_caching(self):
        rates = {"input": 1.00, "output": 5.00, "cached_input": 0.10}
        # 800k cached + 200k uncached input + 1M output
        cost = _calc_cost_usd(
            rates,
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cached_input_tokens=800_000,
        )
        # 200_000 * $1/M + 800_000 * $0.10/M + 1_000_000 * $5/M
        expected = 0.20 + 0.08 + 5.00
        assert cost == pytest.approx(expected)
