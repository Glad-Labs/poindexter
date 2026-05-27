"""Pin the paid-endpoint policy on LiteLLMProvider.

Per ``feedback_no_paid_apis`` + cycle-5 audit finding:
``LiteLLMProvider`` is the default LLM router for every cost tier
(``plugin.llm_provider.primary.{free,budget,standard,premium,flagship}
='litellm'``) and LiteLLM auto-discovers ``OPENAI_API_KEY`` /
``ANTHROPIC_API_KEY`` / ``GEMINI_API_KEY`` from env. A one-row edit
to ``cost_tier.standard.model`` swapping ``ollama/glm-4.7-5090`` for
``openai/gpt-4o`` would have dispatched paid traffic with zero budget
pre-check — exactly the $300-Gemini-overnight class of incident that
cycle-4's #615 fix closed on OpenAICompatProvider, but one layer up.

This module tests the new policy. The gate has TWO axes:

1. ``api_base`` — same as the OpenAICompatProvider check
2. Model namespace prefix — ``openai/``, ``anthropic/``, ``gemini/``,
   ``openrouter/``, ``groq/``, ... LiteLLM routes by prefix and pulls
   credentials from env, so this is a separate paid-target seam.

Both refuse unless ``plugin.llm_provider.litellm.allow_paid_base_url``
is explicitly ``true``. Local prefixes (``ollama``, ``vllm``,
``lm_studio``) keep working unchanged.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_providers.litellm_provider import (
    LiteLLMProvider,
    _LOCAL_MODEL_PREFIXES,
    _coerce_bool,
)


# ---------------------------------------------------------------------------
# _coerce_bool — TEXT app_settings.value rows. Pins the parsing surface so
# 'True' vs 'true' vs '1' don't ship subtly different gate behaviour.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True), (False, False),
        ("true", True), ("True", True), ("TRUE", True),
        ("1", True), ("yes", True), ("on", True),
        ("false", False), ("0", False), ("", False),
        ("nope", False), (None, False),
    ],
)
def test_coerce_bool_matches_openai_compat_pattern(value, expected):
    assert _coerce_bool(value) is expected


# ---------------------------------------------------------------------------
# _enforce_paid_endpoint_policy — axis 1: model prefix
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "resolved_model",
    [
        "ollama/glm-4.7-5090:latest",
        "ollama/gemma3:27b",
        "ollama_chat/llama3.1:8b",
        "vllm/meta-llama/Llama-3.1-8B-Instruct",
        "lm_studio/qwen2.5-coder",
        "openai_compat/local-model",
        "custom/anything",
    ],
)
def test_local_model_prefix_allowed_without_opt_in(resolved_model):
    """Self-hosted prefixes never trip the gate — local backends cost
    zero dollars regardless of traffic. This is the regression guard
    against the policy accidentally blocking the default install."""
    provider = LiteLLMProvider()
    provider._allow_paid_base_url = False
    # Doesn't raise — local is always free.
    provider._enforce_paid_endpoint_policy(resolved_model)


@pytest.mark.unit
@pytest.mark.parametrize(
    "resolved_model",
    [
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "anthropic/claude-haiku-4-5",
        "anthropic/claude-opus-4-7",
        "gemini/gemini-2.0-flash",
        "vertex_ai/gemini-2.0-pro",
        "openrouter/anthropic/claude-haiku-4-5",
        "groq/mixtral-8x7b-32768",
        "together_ai/meta-llama/Llama-3.1-405B-Instruct",
        "fireworks_ai/accounts/fireworks/models/llama-v3p1-70b-instruct",
        "deepseek/deepseek-chat",
        "mistral/mistral-large-latest",
        "cohere/command-r-plus",
        "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
        "azure/gpt-4o",
    ],
)
def test_paid_model_prefix_refused_by_default(resolved_model):
    """Default (allow_paid_base_url=False) refuses every paid-vendor
    prefix LiteLLM knows about. The auto-env-discovery seam means a
    stray ``OPENAI_API_KEY`` + the wrong model string is enough to
    dispatch paid traffic without this gate."""
    provider = LiteLLMProvider()
    provider._allow_paid_base_url = False
    with pytest.raises(RuntimeError) as exc:
        provider._enforce_paid_endpoint_policy(resolved_model)
    msg = str(exc.value)
    prefix = resolved_model.split("/", 1)[0].lower()
    assert prefix in msg
    assert "allow_paid_base_url=true" in msg
    assert "feedback_no_paid_apis" in msg


@pytest.mark.unit
def test_unknown_prefix_refused_by_default():
    """Conservative-deny: anything not in the local allowlist is treated
    as paid. LiteLLM keeps adding cloud vendors — an unknown prefix
    today might be the next $300-overnight if we silently allowed it."""
    provider = LiteLLMProvider()
    provider._allow_paid_base_url = False
    with pytest.raises(RuntimeError, match="allow_paid_base_url=true"):
        provider._enforce_paid_endpoint_policy("brand_new_vendor/some-model")


@pytest.mark.unit
def test_paid_prefix_allowed_with_opt_in_flag():
    """Operator explicitly authorising the paid path can dispatch.
    The gate stops at refusing the unconfigured case — pre-flight
    budget checks layer on top via CostGuard."""
    provider = LiteLLMProvider()
    provider._allow_paid_base_url = True
    provider._enforce_paid_endpoint_policy("openai/gpt-4o")
    provider._enforce_paid_endpoint_policy("anthropic/claude-opus-4-7")
    provider._enforce_paid_endpoint_policy("gemini/gemini-2.0-flash")


# ---------------------------------------------------------------------------
# _enforce_paid_endpoint_policy — axis 2: api_base
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "api_base",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434/v1",
        "http://host.docker.internal:11434/v1",
        "http://0.0.0.0:11434",
    ],
)
def test_local_api_base_allowed_without_opt_in(api_base):
    """Local api_base targets pass on both axes — the policy never
    blocks zero-cost destinations regardless of model prefix."""
    provider = LiteLLMProvider()
    provider._allow_paid_base_url = False
    provider._api_base = api_base
    provider._enforce_paid_endpoint_policy("ollama/gemma3:27b")


@pytest.mark.unit
@pytest.mark.parametrize(
    "api_base",
    [
        "https://api.openai.com/v1",
        "https://api.anthropic.com/v1",
        "https://generativelanguage.googleapis.com",
        "https://api.groq.com/openai/v1",
        "https://openrouter.ai/api/v1",
        "https://api.together.xyz/v1",
        "https://api.fireworks.ai/inference/v1",
    ],
)
def test_paid_api_base_refused_by_default(api_base):
    """Even with a local-looking model prefix, a paid ``api_base`` URL
    refuses. LiteLLM's ``api_base`` overrides the prefix-derived
    endpoint, so this axis has to gate independently."""
    provider = LiteLLMProvider()
    provider._allow_paid_base_url = False
    provider._api_base = api_base
    with pytest.raises(RuntimeError) as exc:
        provider._enforce_paid_endpoint_policy("ollama/gemma3:27b")
    msg = str(exc.value)
    assert api_base in msg
    assert "allow_paid_base_url=true" in msg


@pytest.mark.unit
def test_paid_api_base_allowed_with_opt_in_flag():
    provider = LiteLLMProvider()
    provider._allow_paid_base_url = True
    provider._api_base = "https://api.openai.com/v1"
    provider._enforce_paid_endpoint_policy("openai/gpt-4o")


@pytest.mark.unit
def test_inline_http_model_refused_when_paid():
    """Model strings starting with ``http://`` flow through axis 1 —
    LiteLLM treats them as an inline base URL. A paid host in that
    field refuses just like a paid ``self._api_base``."""
    provider = LiteLLMProvider()
    provider._allow_paid_base_url = False
    with pytest.raises(RuntimeError, match="allow_paid_base_url=true"):
        provider._enforce_paid_endpoint_policy("https://api.openai.com/v1")


@pytest.mark.unit
def test_inline_http_local_model_allowed():
    provider = LiteLLMProvider()
    provider._allow_paid_base_url = False
    # No raise — local inline URL passes.
    provider._enforce_paid_endpoint_policy("http://localhost:8080/v1")


# ---------------------------------------------------------------------------
# Bare model name (no slash) — _resolve_model prepends the default prefix
# (ollama/ by default). Pin that the resolved string passes the gate.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bare_model_with_default_ollama_prefix_passes():
    """The legacy code path: callers pass ``"gemma3:27b"`` without a
    namespace, ``_resolve_model`` prepends ``ollama/`` (the default
    prefix), and the gate sees the local form. Regression guard for
    the migration that has many call sites still using bare names."""
    provider = LiteLLMProvider()
    provider._allow_paid_base_url = False
    resolved = provider._resolve_model("gemma3:27b")
    assert resolved == "ollama/gemma3:27b"
    provider._enforce_paid_endpoint_policy(resolved)


# ---------------------------------------------------------------------------
# _configure_from — re-reads the flag on every call (operator can flip
# the app_setting without a worker restart, same as other config keys).
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_configure_from_reads_allow_paid_flag_each_call():
    provider = LiteLLMProvider()
    provider._configure_from({"allow_paid_base_url": "false"})
    assert provider._allow_paid_base_url is False
    provider._configure_from({"allow_paid_base_url": "true"})
    assert provider._allow_paid_base_url is True
    # Default (key omitted) is False — fails closed.
    provider2 = LiteLLMProvider()
    provider2._configure_from({})
    assert provider2._allow_paid_base_url is False


# ---------------------------------------------------------------------------
# Integration with complete() / stream() / embed() — the policy fires
# BEFORE the litellm call (an unauthorised paid endpoint never even
# sees ``acompletion`` / ``aembedding``).
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_refuses_paid_prefix_without_opt_in():
    provider = LiteLLMProvider()
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
        with pytest.raises(RuntimeError, match="allow_paid_base_url=true"):
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="openai/gpt-4o",
                _provider_config={"allow_paid_base_url": "false"},
            )
        mock_acomp.assert_not_called()  # gate fires BEFORE the network call


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_refuses_paid_api_base_without_opt_in():
    provider = LiteLLMProvider()
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
        with pytest.raises(RuntimeError, match="allow_paid_base_url=true"):
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="ollama/gemma3:27b",  # local prefix
                _provider_config={
                    "api_base": "https://api.openai.com/v1",
                    "allow_paid_base_url": "false",
                },
            )
        mock_acomp.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_allows_local_prefix_without_opt_in():
    """The default install path (Ollama on host.docker.internal) keeps
    working with no operator action — this is the regression guard
    against the policy accidentally blocking local traffic."""
    provider = LiteLLMProvider()

    fake_choice = MagicMock()
    fake_choice.message.content = "hello"
    fake_choice.finish_reason = "stop"
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    fake_response.usage.prompt_tokens = 3
    fake_response.usage.completion_tokens = 1
    fake_response.usage.total_tokens = 4
    fake_response.model_dump.return_value = {}

    with patch(
        "litellm.acompletion", new_callable=AsyncMock, return_value=fake_response,
    ) as mock_acomp:
        result = await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma3:27b",
            _provider_config={
                "api_base": "http://host.docker.internal:11434",
                "allow_paid_base_url": "false",
            },
        )
    assert result.text == "hello"
    mock_acomp.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_embed_refuses_paid_prefix_without_opt_in():
    """``embed`` now accepts ``**kwargs`` so the dispatcher injects
    ``_provider_config`` symmetrically with ``complete``. Without that
    plumbing, the embed path was an unguarded backdoor to paid endpoints
    — same runaway-cost class the policy was added to prevent."""
    provider = LiteLLMProvider()
    with patch("litellm.aembedding", new_callable=AsyncMock) as mock_aemb:
        with pytest.raises(RuntimeError, match="allow_paid_base_url=true"):
            await provider.embed(
                text="hi",
                model="openai/text-embedding-3-small",
                _provider_config={"allow_paid_base_url": "false"},
            )
        mock_aemb.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_embed_allows_local_prefix_without_opt_in():
    provider = LiteLLMProvider()

    embedding_item = MagicMock()
    embedding_item.embedding = [0.1, 0.2, 0.3]
    fake_response = MagicMock()
    fake_response.data = [embedding_item]

    with patch(
        "litellm.aembedding", new_callable=AsyncMock, return_value=fake_response,
    ) as mock_aemb:
        result = await provider.embed(
            text="hi",
            model="ollama/nomic-embed-text",
            _provider_config={
                "api_base": "http://host.docker.internal:11434",
                "allow_paid_base_url": "false",
            },
        )
    assert result == [0.1, 0.2, 0.3]
    mock_aemb.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_refuses_paid_prefix_without_opt_in():
    provider = LiteLLMProvider()
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
        with pytest.raises(RuntimeError, match="allow_paid_base_url=true"):
            async for _ in provider.stream(
                messages=[{"role": "user", "content": "hi"}],
                model="anthropic/claude-haiku-4-5",
                _provider_config={"allow_paid_base_url": "false"},
            ):
                pass
        mock_acomp.assert_not_called()


# ---------------------------------------------------------------------------
# Sanity — the allowlist contains the prefixes we actually use in prod.
# Catches a future commit that accidentally drops a local prefix.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_local_prefix_allowlist_includes_production_set():
    must_have = {"ollama", "ollama_chat", "vllm", "lm_studio", "openai_compat"}
    missing = must_have - _LOCAL_MODEL_PREFIXES
    assert not missing, f"local-prefix allowlist lost entries: {missing}"


# ---------------------------------------------------------------------------
# Env-var scenario — the auto-discovery seam. LiteLLM reads
# OPENAI_API_KEY from env at call time. The gate has to fire even when
# the operator has the env var set (this is the actual bypass cycle-4
# closed for openai_compat and cycle-5 closes for litellm).
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_paid_prefix_refused_even_with_env_api_key():
    """Smoking-gun scenario: operator has ``OPENAI_API_KEY`` set in env
    (CI / local dev / leftover from a different project) and someone
    sets ``cost_tier.standard.model='openai/gpt-4o'``. Without the gate,
    the next dispatch fires a real paid call. With the gate, it refuses
    loud BEFORE litellm's auto-discovery kicks in."""
    provider = LiteLLMProvider()
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-dummy"}, clear=False):
        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            with pytest.raises(RuntimeError, match="allow_paid_base_url=true"):
                await provider.complete(
                    messages=[{"role": "user", "content": "hi"}],
                    model="openai/gpt-4o",
                    _provider_config={"allow_paid_base_url": "false"},
                )
            mock_acomp.assert_not_called()
