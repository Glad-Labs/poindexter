"""Pin that Ollama-only params never reach a cloud provider.

Regression guard for the 2026-07-07 cloud-writer canary's *second* failure
(after the api_base scoping fix, #2167): the writer path threads
``num_ctx`` (Ollama's context-window option) and #2163 added ``think``
(Ollama's reasoning-channel toggle) — both forwarded verbatim by litellm
because they aren't OpenAI-spec, so ``drop_params`` never strips them.
Anthropic 400s with ``num_ctx: Extra inputs are not permitted``.

``complete()`` drops every ``_OLLAMA_ONLY_PARAMS`` member for a non-local
prefix while leaving local Ollama calls untouched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_providers.litellm_provider import (
    _OLLAMA_ONLY_PARAMS,
    LiteLLMProvider,
)

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
# The set itself — catch a future param slipping in without a test.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ollama_only_param_set_contents():
    assert _OLLAMA_ONLY_PARAMS == frozenset({"num_ctx", "think"})


# ---------------------------------------------------------------------------
# _is_local_prefix — the gate
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "resolved_model,expected",
    [
        ("ollama/gemma-4-31B-it-qat:latest", True),
        ("ollama_chat/llama3.1:8b", True),
        ("vllm/x", True),
        ("http://localhost:11434/v1", True),
        ("http://host.docker.internal:11434", True),
        ("anthropic/claude-sonnet-5", False),
        ("openai/gpt-4o-mini", False),
        ("https://api.anthropic.com/v1", False),
    ],
)
def test_is_local_prefix(resolved_model, expected):
    provider = LiteLLMProvider()
    assert provider._is_local_prefix(resolved_model) is expected


# ---------------------------------------------------------------------------
# Through complete() — cloud drops, local keeps
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_drops_num_ctx_and_think_for_cloud():
    """THE regression test: the writer's num_ctx + think must not reach
    Anthropic (both 400 it)."""
    provider = LiteLLMProvider()
    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_completion_response(),
    ) as mock_acomp:
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-sonnet-5",
            num_ctx=8192,
            think=False,
            _provider_config={
                "api_base": _LOCAL_BASE,
                "allow_paid_base_url": "true",
            },
        )
    sent = mock_acomp.call_args.kwargs
    assert "num_ctx" not in sent
    assert "think" not in sent
    # max_tokens floor still applied; real OpenAI-spec params survive.
    assert sent["max_tokens"] == 8192  # _DEFAULT_CLOUD_MAX_TOKENS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_keeps_num_ctx_and_think_for_local():
    """Local Ollama path unchanged — num_ctx/think are exactly how the
    writer controls the local model."""
    provider = LiteLLMProvider()
    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_completion_response(),
    ) as mock_acomp:
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma-4-31B-it-qat:latest",
            num_ctx=16384,
            think=False,
            _provider_config={"api_base": _LOCAL_BASE},
        )
    sent = mock_acomp.call_args.kwargs
    assert sent["num_ctx"] == 16384
    assert sent["think"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_cloud_without_ollama_params_is_untouched():
    """A cloud call that never passed num_ctx/think is unaffected (no
    spurious keys, no crash)."""
    provider = LiteLLMProvider()
    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_completion_response(),
    ) as mock_acomp:
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-sonnet-5",
            _provider_config={"allow_paid_base_url": "true"},
        )
    sent = mock_acomp.call_args.kwargs
    assert "num_ctx" not in sent
    assert "think" not in sent
    assert sent["model"] == "anthropic/claude-sonnet-5"
