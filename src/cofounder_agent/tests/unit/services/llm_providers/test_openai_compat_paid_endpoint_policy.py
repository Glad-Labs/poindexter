"""Pin the paid-endpoint policy on OpenAICompatProvider.

Per ``feedback_no_paid_apis`` + cycle-4 audit finding:
``OpenAICompatProvider`` was the only provider with no cost-guard
integration. A one-row edit to swap ``base_url`` from Ollama to
Groq / OpenRouter / Together / Fireworks / Anthropic-OAI-compat
would have dispatched paid traffic with zero budget pre-check
— exactly the $300-Gemini-overnight class of incident.

This module tests the new policy: non-local ``base_url`` refuses
to fire unless ``plugin.llm_provider.openai_compat.allow_paid_base_url``
is explicitly ``true``. Local URLs (Ollama / vllm / llama.cpp) keep
working unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_providers.openai_compat import (
    OpenAICompatProvider,
    _coerce_bool,
)

# ---------------------------------------------------------------------------
# _coerce_bool — TEXT app_settings.value rows can be 'true', 'True', '1',
# 'yes', 'on'. Pins the parsing surface so future ops/tests aren't
# surprised by typo'd configs.
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
def test_coerce_bool_matches_site_config_pattern(value, expected):
    assert _coerce_bool(value) is expected


# ---------------------------------------------------------------------------
# _enforce_paid_endpoint_policy — the gate itself
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1",
        "http://host.docker.internal:11434/v1",
        "http://0.0.0.0:11434/v1",
    ],
)
def test_local_base_url_always_allowed(base_url):
    """Self-hosted backends (Ollama, vllm, llama.cpp, LM Studio) cost
    zero dollars — the policy never blocks them regardless of the
    allow_paid_base_url flag."""
    provider = OpenAICompatProvider()
    # Both flag values must pass — local is always free.
    provider._enforce_paid_endpoint_policy(base_url, allow_paid=False)
    provider._enforce_paid_endpoint_policy(base_url, allow_paid=True)


@pytest.mark.unit
@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.groq.com/openai/v1",
        "https://openrouter.ai/api/v1",
        "https://api.together.xyz/v1",
        "https://api.fireworks.ai/inference/v1",
        "https://api.anthropic.com/v1/openai",
    ],
)
def test_paid_base_url_refused_by_default(base_url):
    """Default (allow_paid_base_url=False) refuses any non-local URL
    with a RuntimeError naming the app_setting an operator must flip.
    This is the runaway-cost guardrail — closes the audit finding."""
    provider = OpenAICompatProvider()
    with pytest.raises(RuntimeError) as exc:
        provider._enforce_paid_endpoint_policy(base_url, allow_paid=False)
    msg = str(exc.value)
    assert base_url in msg
    assert "allow_paid_base_url=true" in msg
    assert "feedback_no_paid_apis" in msg


@pytest.mark.unit
def test_paid_base_url_allowed_with_opt_in_flag():
    """Operator explicitly authorising a paid endpoint can dispatch
    against it. The policy gate stops at refusing the unconfigured
    case — pre-flight budget checks layer on top via CostGuard, which
    is wired separately."""
    provider = OpenAICompatProvider()
    # Doesn't raise — that's the contract for opt-in.
    provider._enforce_paid_endpoint_policy(
        "https://api.groq.com/openai/v1", allow_paid=True,
    )


# ---------------------------------------------------------------------------
# Integration with complete() / embed() — the policy is checked BEFORE
# the HTTP call (an unauthorised paid endpoint never even sees a request).
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_refuses_paid_base_url_without_opt_in():
    provider = OpenAICompatProvider()
    with pytest.raises(RuntimeError, match="allow_paid_base_url=true"):
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="mixtral-8x7b-32768",
            _provider_config={
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": "gsk_dummy",
                "allow_paid_base_url": "false",
            },
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_allows_local_base_url_without_opt_in():
    """The default install path (Ollama on host.docker.internal) must
    keep working with no operator action. This is the regression guard
    against the policy accidentally blocking local traffic."""
    provider = OpenAICompatProvider()
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "choices": [{
            "message": {"content": "hello"},
            "finish_reason": "stop",
        }],
        "model": "llama3.1:8b",
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }
    fake_response.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=fake_response)
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        result = await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="llama3.1:8b",
            _provider_config={
                "base_url": "http://host.docker.internal:11434/v1",
                "api_key": "",
                "allow_paid_base_url": "false",
            },
        )
    assert result.text == "hello"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_embed_refuses_paid_base_url_without_opt_in():
    """embed() now accepts **kwargs so the dispatcher injects
    ``_provider_config`` symmetrically with complete(). Without that
    plumbing the embed path was an unguarded backdoor to paid endpoints.
    """
    provider = OpenAICompatProvider()
    with pytest.raises(RuntimeError, match="allow_paid_base_url=true"):
        await provider.embed(
            text="hi",
            model="text-embedding-3-small",
            _provider_config={
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-dummy",
                "allow_paid_base_url": "false",
            },
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_refuses_paid_base_url_without_opt_in():
    provider = OpenAICompatProvider()
    with pytest.raises(RuntimeError, match="allow_paid_base_url=true"):
        async for _ in provider.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="mixtral-8x7b-32768",
            _provider_config={
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": "gsk_dummy",
                "allow_paid_base_url": "false",
            },
        ):
            pass
