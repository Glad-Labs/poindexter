"""Pin the reasoning-model content fallback on LiteLLMProvider.

The 2026-05-28 content-generation stall: a reasoning model
(``glm-4.7-5090``) under ``response_format=json_object`` emitted all its
tokens into a thinking channel and returned an EMPTY ``content`` field.
Every ``json.loads`` caller in topic discovery then crashed on
``json.loads("")``.

When ``reasoning_content_fallback`` is on (default) and ``content`` is
empty, the provider recovers the payload from the response's
``reasoning_content`` (stripping any ``<think>`` wrapper) so downstream
JSON parsing has something to work with. DB-configurable via
``plugin.llm_provider.litellm.config.reasoning_content_fallback``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_providers.litellm_provider import (
    LiteLLMProvider,
    _recover_reasoning_text,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# _recover_reasoning_text — pure helper
# ---------------------------------------------------------------------------


class TestRecoverReasoningText:
    def test_returns_empty_when_no_reasoning_content(self):
        msg = MagicMock(spec=[])  # no reasoning_content attribute
        assert _recover_reasoning_text(msg) == ""

    def test_returns_empty_when_reasoning_content_blank(self):
        msg = MagicMock()
        msg.reasoning_content = "   "
        assert _recover_reasoning_text(msg) == ""

    def test_returns_reasoning_content_when_present(self):
        msg = MagicMock()
        msg.reasoning_content = '{"topic": "X", "angle": "Y"}'
        assert _recover_reasoning_text(msg) == '{"topic": "X", "angle": "Y"}'

    def test_strips_think_wrapper_and_returns_trailing_answer(self):
        msg = MagicMock()
        msg.reasoning_content = (
            "<think>let me reason about this</think>"
            '{"topic": "X", "angle": "Y"}'
        )
        assert _recover_reasoning_text(msg) == '{"topic": "X", "angle": "Y"}'

    def test_returns_raw_reasoning_when_strip_leaves_nothing(self):
        # Model never closed a think tag — the reasoning body IS the
        # candidate payload, return it raw for the caller's parser.
        msg = MagicMock()
        msg.reasoning_content = '<think>{"topic": "X"}'
        assert _recover_reasoning_text(msg) == '<think>{"topic": "X"}'


# ---------------------------------------------------------------------------
# complete() — integration with the fallback
# ---------------------------------------------------------------------------


def _response(content, reasoning_content=None, completion_tokens=40):
    """Build a LiteLLM-shaped acompletion response."""
    msg = MagicMock()
    msg.content = content
    if reasoning_content is not None:
        msg.reasoning_content = reasoning_content
    else:
        # Simulate a response with no reasoning_content field.
        del msg.reasoning_content
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = completion_tokens
    usage.total_tokens = 10 + completion_tokens
    resp.usage = usage
    resp.model_dump = lambda: {}
    return resp


class TestCompleteReasoningFallback:
    async def test_empty_content_recovers_from_reasoning(self):
        provider = LiteLLMProvider()
        resp = _response("", reasoning_content='{"topic": "Recovered"}')
        with patch("litellm.acompletion", AsyncMock(return_value=resp)):
            result = await provider.complete(
                messages=[{"role": "user", "content": "go"}],
                model="ollama/glm-4.7-5090:latest",
                _provider_config={"api_base": "http://host.docker.internal:11434"},
            )
        assert result.text == '{"topic": "Recovered"}'

    async def test_nonempty_content_is_not_overwritten(self):
        provider = LiteLLMProvider()
        resp = _response('{"topic": "Direct"}', reasoning_content="ignored thinking")
        with patch("litellm.acompletion", AsyncMock(return_value=resp)):
            result = await provider.complete(
                messages=[{"role": "user", "content": "go"}],
                model="ollama/gemma3:27b",
                _provider_config={"api_base": "http://host.docker.internal:11434"},
            )
        assert result.text == '{"topic": "Direct"}'

    async def test_fallback_disabled_leaves_content_empty(self):
        provider = LiteLLMProvider()
        resp = _response("", reasoning_content='{"topic": "Recovered"}')
        with patch("litellm.acompletion", AsyncMock(return_value=resp)):
            result = await provider.complete(
                messages=[{"role": "user", "content": "go"}],
                model="ollama/glm-4.7-5090:latest",
                _provider_config={
                    "api_base": "http://host.docker.internal:11434",
                    "reasoning_content_fallback": False,
                },
            )
        assert result.text == ""
