"""Per-model api_base overrides on LiteLLMProvider (glad-labs-stack#2051).

qwen3-vl (vision QA + shot-render scoring) is eviction-prone on the
shared Ollama instance: a cold-load mid-pipeline times out and
``qa.vision`` passes open. The fix is placement pinning — a second host
Ollama instance pinned to GPU 1 — which needs the app side to route
*specific models* to a different endpoint.

``plugin.llm_provider.litellm.config.model_api_base_overrides`` is a
JSON map of resolved model name → api_base, e.g.::

    {"ollama/qwen3-vl:30b": "http://host.docker.internal:11435"}

Contract pinned here:

- a matching resolved model dispatches with the override api_base;
- everything else keeps the default api_base (empty map = no-op —
  the OSS / consumer-stack path is untouched);
- the map may arrive as a dict (PluginConfig JSONB) or a JSON string;
  garbage is logged + ignored, never fatal;
- the paid-endpoint policy evaluates the EFFECTIVE api_base, so an
  override can't smuggle a cloud URL past ``feedback_no_paid_apis``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_providers.litellm_provider import LiteLLMProvider

_DEFAULT_BASE = "http://host.docker.internal:11434"
_VISION_BASE = "http://host.docker.internal:11435"


def _fake_response(text: str = "ok"):
    choice = MagicMock()
    choice.message.content = text
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = 3
    resp.usage.completion_tokens = 1
    resp.usage.total_tokens = 4
    resp.model_dump.return_value = {}
    return resp


def _config(overrides):
    return {
        "api_base": _DEFAULT_BASE,
        "allow_paid_base_url": "false",
        "model_api_base_overrides": overrides,
    }


async def _complete(provider, model, overrides):
    with patch(
        "litellm.acompletion", new_callable=AsyncMock,
        return_value=_fake_response(),
    ) as mock_acomp:
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model=model,
            _provider_config=_config(overrides),
        )
    return mock_acomp.await_args.kwargs


@pytest.mark.unit
@pytest.mark.asyncio
async def test_override_routes_matching_model():
    kwargs = await _complete(
        LiteLLMProvider(), "ollama/qwen3-vl:30b",
        {"ollama/qwen3-vl:30b": _VISION_BASE},
    )
    assert kwargs["api_base"] == _VISION_BASE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_override_matches_after_default_prefix_resolution():
    # Callers often pass bare model names; the override is keyed on the
    # RESOLVED name, so "qwen3-vl:30b" → "ollama/qwen3-vl:30b" must match.
    kwargs = await _complete(
        LiteLLMProvider(), "qwen3-vl:30b",
        {"ollama/qwen3-vl:30b": _VISION_BASE},
    )
    assert kwargs["api_base"] == _VISION_BASE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_matching_model_keeps_default_base():
    kwargs = await _complete(
        LiteLLMProvider(), "ollama/gemma3:27b",
        {"ollama/qwen3-vl:30b": _VISION_BASE},
    )
    assert kwargs["api_base"] == _DEFAULT_BASE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_json_string_map_accepted():
    # app_settings TEXT rows arrive as strings; PluginConfig may hand the
    # raw JSON through un-decoded.
    kwargs = await _complete(
        LiteLLMProvider(), "ollama/qwen3-vl:30b",
        f'{{"ollama/qwen3-vl:30b": "{_VISION_BASE}"}}',
    )
    assert kwargs["api_base"] == _VISION_BASE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_json_ignored_not_fatal():
    kwargs = await _complete(
        LiteLLMProvider(), "ollama/qwen3-vl:30b", "{not json",
    )
    assert kwargs["api_base"] == _DEFAULT_BASE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_override_cannot_smuggle_paid_endpoint():
    provider = LiteLLMProvider()
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
        with pytest.raises(RuntimeError, match="non-local api_base"):
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="ollama/qwen3-vl:30b",
                _provider_config=_config(
                    {"ollama/qwen3-vl:30b": "https://api.openai.com/v1"},
                ),
            )
        mock_acomp.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_honours_override():
    provider = LiteLLMProvider()

    async def _chunks():
        chunk = MagicMock()
        choice = MagicMock()
        choice.delta.content = "hi"
        choice.finish_reason = None
        chunk.choices = [choice]
        yield chunk

    with patch(
        "litellm.acompletion", new_callable=AsyncMock,
        return_value=_chunks(),
    ) as mock_acomp:
        async for _ in provider.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/qwen3-vl:30b",
            _provider_config=_config({"ollama/qwen3-vl:30b": _VISION_BASE}),
        ):
            break
    assert mock_acomp.await_args.kwargs["api_base"] == _VISION_BASE
