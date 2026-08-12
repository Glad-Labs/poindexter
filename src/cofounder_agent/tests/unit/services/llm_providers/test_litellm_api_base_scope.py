"""Pin api_base scoping on LiteLLMProvider — cloud prefixes never inherit it.

Regression guard for the 2026-07-07 cloud-writer canary failure: the
configured ``plugin.llm_provider.litellm`` ``api_base`` row is the LOCAL
backend endpoint (``http://host.docker.internal:11434``), and ``complete()``
attached it to EVERY call. litellm treats an explicit ``api_base`` as
authoritative over the model prefix's canonical endpoint, so
``anthropic/claude-sonnet-5`` was POSTed to local Ollama's
Anthropic-compatible ``/v1/messages`` — which replied with an
Anthropic-SHAPED 404 ("model 'claude-sonnet-5' not found", request_id and
all), masquerading as a real cloud API error.

Contracts pinned here:

- cloud prefixes fall through to litellm's per-provider default endpoint
  (NO api_base kwarg) even when a local api_base is configured
- local prefixes keep routing to the configured api_base (the default
  install path — Ollama behind host.docker.internal)
- an explicit per-model override attaches for ANY prefix (operator says
  "this exact model goes here"; the paid-endpoint policy validates the URL)
- inline ``http(s)://`` model strings are untouched (litellm parses those
  itself)

**Every egress method is covered — complete(), stream(), AND embed()**
(poindexter#878). This file previously pinned only the first two, which is
exactly how ``embed()`` shipped without attaching api_base at all: it called
``_configure_from(provider_config)`` and then ignored the resolved endpoint, so
litellm fell back to its built-in ollama default (``localhost:11434``) —
unreachable from inside the worker container. Every ``dispatch_embed`` call
failed, which silently killed the ``self_consistency`` QA rail for 9+ days
(the rail's fail-open scored the un-run check 100 — Glad-Labs/poindexter#2655).
A new egress method gets a section here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_providers.litellm_provider import LiteLLMProvider

_LOCAL_BASE = "http://host.docker.internal:11434"


def _fake_completion_response(text: str = "hello"):
    fake_choice = MagicMock()
    fake_choice.message.content = text
    fake_choice.message.reasoning_content = ""
    fake_choice.finish_reason = "stop"
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    fake_response.usage.prompt_tokens = 3
    fake_response.usage.completion_tokens = 1
    fake_response.usage.total_tokens = 4
    fake_response.model_dump.return_value = {}
    return fake_response


# ---------------------------------------------------------------------------
# _api_base_applies — pure unit surface
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "resolved_model,expected",
    [
        ("ollama/gemma-4-31B-it-qat:latest", True),
        ("ollama_chat/llama3.1:8b", True),
        ("vllm/meta-llama/Llama-3.1-8B-Instruct", True),
        ("lm_studio/qwen2.5-coder", True),
        ("anthropic/claude-sonnet-5", False),
        ("openai/gpt-4o-mini", False),
        ("gemini/gemini-2.0-flash", False),
        ("openrouter/anthropic/claude-haiku-4-5", False),
    ],
)
def test_api_base_applies_by_prefix(resolved_model, expected):
    provider = LiteLLMProvider()
    provider._api_base = _LOCAL_BASE
    assert provider._api_base_applies(resolved_model) is expected


@pytest.mark.unit
def test_api_base_applies_for_explicit_per_model_override():
    """An operator's per-model override is explicit routing — it applies
    even to a cloud prefix (the paid-endpoint policy already vets the
    override URL on axis 1)."""
    provider = LiteLLMProvider()
    provider._model_api_base_overrides = {
        "anthropic/claude-sonnet-5": "http://localhost:9999/proxy",
    }
    assert provider._api_base_applies("anthropic/claude-sonnet-5") is True
    # Sibling cloud model WITHOUT an override still falls through.
    assert provider._api_base_applies("anthropic/claude-haiku-4-5") is False


# ---------------------------------------------------------------------------
# Through complete() — what litellm.acompletion actually receives
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_cloud_model_does_not_inherit_local_api_base():
    """THE regression test: anthropic model + configured local api_base →
    the acompletion call carries NO api_base, so litellm routes to
    api.anthropic.com instead of local Ollama's /v1/messages."""
    provider = LiteLLMProvider()
    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_completion_response(),
    ) as mock_acomp:
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-sonnet-5",
            _provider_config={
                "api_base": _LOCAL_BASE,
                "allow_paid_base_url": "true",
            },
        )
    sent = mock_acomp.call_args.kwargs
    assert "api_base" not in sent
    assert sent["model"] == "anthropic/claude-sonnet-5"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_local_model_keeps_configured_api_base():
    """Default install path unchanged: ollama models still route to the
    configured local endpoint."""
    provider = LiteLLMProvider()
    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_completion_response(),
    ) as mock_acomp:
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma-4-31B-it-qat:latest",
            _provider_config={"api_base": _LOCAL_BASE},
        )
    assert mock_acomp.call_args.kwargs["api_base"] == _LOCAL_BASE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_per_model_override_attaches_for_cloud_prefix():
    provider = LiteLLMProvider()
    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_completion_response(),
    ) as mock_acomp:
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-sonnet-5",
            _provider_config={
                "api_base": _LOCAL_BASE,
                "allow_paid_base_url": "true",
                "model_api_base_overrides": {
                    "anthropic/claude-sonnet-5": "http://localhost:4000/litellm-proxy",
                },
            },
        )
    sent = mock_acomp.call_args.kwargs
    assert sent["api_base"] == "http://localhost:4000/litellm-proxy"


# ---------------------------------------------------------------------------
# Through stream() — symmetric with complete()
# ---------------------------------------------------------------------------


class _EmptyStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_cloud_model_does_not_inherit_local_api_base():
    provider = LiteLLMProvider()
    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_EmptyStream(),
    ) as mock_acomp:
        async for _ in provider.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-sonnet-5",
            _provider_config={
                "api_base": _LOCAL_BASE,
                "allow_paid_base_url": "true",
            },
        ):
            pass
    sent = mock_acomp.call_args.kwargs
    assert "api_base" not in sent


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_local_model_keeps_configured_api_base():
    provider = LiteLLMProvider()
    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_EmptyStream(),
    ) as mock_acomp:
        async for _ in provider.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma-4-31B-it-qat:latest",
            _provider_config={"api_base": _LOCAL_BASE},
        ):
            pass
    assert mock_acomp.call_args.kwargs["api_base"] == _LOCAL_BASE


# ---------------------------------------------------------------------------
# Through embed() — symmetric with complete() / stream() (poindexter#878)
# ---------------------------------------------------------------------------


def _fake_embedding_response(dim: int = 4):
    fake_response = MagicMock()
    fake_response.data = [{"embedding": [0.1] * dim}]
    return fake_response


@pytest.mark.unit
@pytest.mark.asyncio
async def test_embed_local_model_keeps_configured_api_base():
    """THE #878 regression test.

    embed() loaded _provider_config and then never attached api_base, so
    litellm fell back to its ollama default (localhost:11434) — unreachable
    from the worker container. Reproduced live before the fix:
    WITHOUT api_base -> APIConnectionError; WITH -> OK, dim=768.
    """
    provider = LiteLLMProvider()
    with patch(
        "litellm.aembedding",
        new_callable=AsyncMock,
        return_value=_fake_embedding_response(),
    ) as mock_aembed:
        await provider.embed(
            text="a sentence",
            model="nomic-embed-text",
            _provider_config={"api_base": _LOCAL_BASE},
        )
    sent = mock_aembed.call_args.kwargs
    assert sent["api_base"] == _LOCAL_BASE
    # Bare names still get the ollama/ prefix — same resolution as complete().
    assert sent["model"] == "ollama/nomic-embed-text"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_embed_cloud_model_does_not_inherit_local_api_base():
    """Symmetric with complete(): a cloud embedding model must fall through
    to litellm's provider default, not get POSTed at local Ollama."""
    provider = LiteLLMProvider()
    with patch(
        "litellm.aembedding",
        new_callable=AsyncMock,
        return_value=_fake_embedding_response(),
    ) as mock_aembed:
        await provider.embed(
            text="a sentence",
            model="openai/text-embedding-3-small",
            _provider_config={
                "api_base": _LOCAL_BASE,
                "allow_paid_base_url": "true",
            },
        )
    assert "api_base" not in mock_aembed.call_args.kwargs


@pytest.mark.unit
@pytest.mark.asyncio
async def test_embed_per_model_override_attaches_for_cloud_prefix():
    """An explicit per-model override is the operator pinning an endpoint —
    it wins for any prefix, exactly as in complete()."""
    provider = LiteLLMProvider()
    with patch(
        "litellm.aembedding",
        new_callable=AsyncMock,
        return_value=_fake_embedding_response(),
    ) as mock_aembed:
        await provider.embed(
            text="a sentence",
            model="openai/text-embedding-3-small",
            _provider_config={
                "api_base": _LOCAL_BASE,
                "allow_paid_base_url": "true",
                "model_api_base_overrides": {
                    "openai/text-embedding-3-small": "http://localhost:4000/proxy",
                },
            },
        )
    assert mock_aembed.call_args.kwargs["api_base"] == "http://localhost:4000/proxy"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_embed_inline_http_model_gets_no_api_base():
    """An inline http(s):// model string is litellm's to parse — attaching a
    second endpoint would fight it. Mirrors complete()'s startswith("http")
    guard.

    Uses a localhost URL deliberately: the paid-endpoint policy (which embed()
    already enforces) refuses a non-local inline base without
    allow_paid_base_url, and that guard is pinned in
    test_litellm_paid_endpoint_policy.py — this test is about api_base
    attachment, not spend authorisation.
    """
    provider = LiteLLMProvider()
    with patch(
        "litellm.aembedding",
        new_callable=AsyncMock,
        return_value=_fake_embedding_response(),
    ) as mock_aembed:
        await provider.embed(
            text="a sentence",
            model="http://localhost:8080/embed",
            _provider_config={"api_base": _LOCAL_BASE},
        )
    assert "api_base" not in mock_aembed.call_args.kwargs
