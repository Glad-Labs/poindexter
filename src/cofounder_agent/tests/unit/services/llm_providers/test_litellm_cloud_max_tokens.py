"""Pin the cloud completion-budget floor on LiteLLMProvider.

LiteLLM defaults ``anthropic/*`` calls to ``max_tokens=4096``, and on
adaptive-thinking Claude models (Sonnet 5+) thinking and visible text
draw from that ONE budget — in the 2026-07-06 A/B writer run
(glad-labs-stack#2153) ~3K thinking tokens starved a long-form draft
mid-word. ``_apply_cloud_max_tokens`` lifts the floor to 8192 (DB-tunable
via the ``cloud_max_tokens`` key on the ``plugin.llm_provider.litellm``
config row) for CLOUD prefixes only.

Contracts pinned here:

- cloud prefix + no caller ``max_tokens`` → floor applied
- caller-passed ``max_tokens`` always wins
- local prefixes / inline-http bases are NEVER capped (Ollama's default
  is unbounded generation — capping it would be a silent regression)
- the config key is parsed from TEXT app_settings values and survives
  garbage input
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_providers.litellm_provider import (
    _DEFAULT_CLOUD_MAX_TOKENS,
    LiteLLMProvider,
)


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
# _apply_cloud_max_tokens — pure unit surface
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "resolved_model",
    [
        "anthropic/claude-sonnet-5",
        "anthropic/claude-haiku-4-5",
        "openai/gpt-4o-mini",
        "gemini/gemini-2.0-flash",
        "openrouter/anthropic/claude-haiku-4-5",
    ],
)
def test_cloud_prefix_gets_floor_when_caller_passed_nothing(resolved_model):
    provider = LiteLLMProvider()
    kwargs: dict = {}
    provider._apply_cloud_max_tokens(resolved_model, kwargs)
    assert kwargs["max_tokens"] == _DEFAULT_CLOUD_MAX_TOKENS


@pytest.mark.unit
def test_caller_passed_max_tokens_wins():
    provider = LiteLLMProvider()
    kwargs = {"max_tokens": 512}
    provider._apply_cloud_max_tokens("anthropic/claude-sonnet-5", kwargs)
    assert kwargs["max_tokens"] == 512


@pytest.mark.unit
@pytest.mark.parametrize(
    "resolved_model",
    [
        "ollama/gemma-4-31B-it-qat:latest",
        "ollama_chat/llama3.1:8b",
        "vllm/meta-llama/Llama-3.1-8B-Instruct",
        "lm_studio/qwen2.5-coder",
        "openai_compat/local-model",
        "custom/anything",
        "http://localhost:8080/v1",
    ],
)
def test_local_targets_are_never_capped(resolved_model):
    """Regression guard: local backends keep their provider defaults
    (unbounded for Ollama). The floor must not silently cap the default
    install's writer."""
    provider = LiteLLMProvider()
    kwargs: dict = {}
    provider._apply_cloud_max_tokens(resolved_model, kwargs)
    assert "max_tokens" not in kwargs


# ---------------------------------------------------------------------------
# _configure_from — DB-tunable via the plugin config row (TEXT values)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_configure_from_reads_cloud_max_tokens():
    provider = LiteLLMProvider()
    provider._configure_from({"cloud_max_tokens": "16000"})
    kwargs: dict = {}
    provider._apply_cloud_max_tokens("anthropic/claude-sonnet-5", kwargs)
    assert kwargs["max_tokens"] == 16000
    # int passthrough (JSONB config can hand a real int through)
    provider._configure_from({"cloud_max_tokens": 12000})
    kwargs = {}
    provider._apply_cloud_max_tokens("anthropic/claude-sonnet-5", kwargs)
    assert kwargs["max_tokens"] == 12000


@pytest.mark.unit
def test_configure_from_defaults_and_survives_garbage():
    provider = LiteLLMProvider()
    provider._configure_from({})
    assert provider._cloud_max_tokens == _DEFAULT_CLOUD_MAX_TOKENS
    # Garbage degrades to the previous value, never breaks dispatch —
    # same posture as _coerce_override_map.
    provider._configure_from({"cloud_max_tokens": "not-a-number"})
    assert provider._cloud_max_tokens == _DEFAULT_CLOUD_MAX_TOKENS


# ---------------------------------------------------------------------------
# Through complete() — the floor lands in the litellm.acompletion kwargs
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_applies_floor_for_cloud_model():
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
    assert sent["max_tokens"] == _DEFAULT_CLOUD_MAX_TOKENS
    # The writer path never passes sampling params — pin that we don't
    # invent one here (Sonnet 5+ rejects non-default temperature/top_p).
    assert "temperature" not in sent
    assert "top_p" not in sent


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_respects_caller_max_tokens_for_cloud_model():
    provider = LiteLLMProvider()
    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_completion_response(),
    ) as mock_acomp:
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-sonnet-5",
            max_tokens=2048,
            _provider_config={"allow_paid_base_url": "true"},
        )
    assert mock_acomp.call_args.kwargs["max_tokens"] == 2048


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_leaves_local_model_uncapped():
    provider = LiteLLMProvider()
    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_fake_completion_response(),
    ) as mock_acomp:
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma-4-31B-it-qat:latest",
            _provider_config={
                "api_base": "http://host.docker.internal:11434",
            },
        )
    assert "max_tokens" not in mock_acomp.call_args.kwargs


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
async def test_stream_applies_floor_for_cloud_model():
    provider = LiteLLMProvider()
    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_EmptyStream(),
    ) as mock_acomp:
        async for _ in provider.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-sonnet-5",
            _provider_config={"allow_paid_base_url": "true"},
        ):
            pass
    assert mock_acomp.call_args.kwargs["max_tokens"] == _DEFAULT_CLOUD_MAX_TOKENS
