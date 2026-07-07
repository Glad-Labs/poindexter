"""Pin paid-call cost capture on LiteLLMProvider.

Regression guard for the 2026-07-07 Sonnet cutover finding: LiteLLM stores
the computed call cost on ``response._hidden_params["response_cost"]``, NOT
as a top-level ``response.response_cost`` attribute. The provider read the
(always-absent) attribute, so every PAID cloud call logged ``cost_usd=0``.
The cost_guard cap sums ``cost_logs.cost_usd``, so a table of zeros could
never trip the daily/monthly limit — a silent spend-safety hole that only
surfaced once real paid calls started flowing (the first canary draft,
8k in / 3.9k out, logged $0).

``_extract_response_cost`` reads the hidden-params value, falling back to
``litellm.completion_cost()``.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_providers.litellm_provider import (
    LiteLLMProvider,
    _extract_response_cost,
)


def _resp_with_hidden(cost):
    r = MagicMock()
    r._hidden_params = {"response_cost": cost}
    # Ensure no top-level attr shadows the hidden-params read.
    del r.response_cost
    return r


# ---------------------------------------------------------------------------
# _extract_response_cost — unit
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reads_from_hidden_params():
    assert _extract_response_cost(_resp_with_hidden(6.8e-05)) == pytest.approx(6.8e-05)


@pytest.mark.unit
def test_hidden_params_string_coerced():
    assert _extract_response_cost(_resp_with_hidden("0.055")) == pytest.approx(0.055)


@pytest.mark.unit
def test_falls_back_to_completion_cost_when_hidden_absent():
    r = MagicMock()
    r._hidden_params = {}  # no response_cost key
    fake_litellm = types.SimpleNamespace(
        completion_cost=lambda completion_response=None: 0.042
    )
    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        assert _extract_response_cost(r) == pytest.approx(0.042)


@pytest.mark.unit
def test_returns_none_when_nothing_recoverable():
    r = MagicMock()
    r._hidden_params = {}

    def _raise(completion_response=None):
        raise RuntimeError("no cost table entry")

    fake_litellm = types.SimpleNamespace(completion_cost=_raise)
    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        assert _extract_response_cost(r) is None


@pytest.mark.unit
def test_hidden_params_none_is_safe():
    r = MagicMock()
    r._hidden_params = None
    fake_litellm = types.SimpleNamespace(
        completion_cost=lambda completion_response=None: 0.01
    )
    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        assert _extract_response_cost(r) == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Through complete() — raw["response_cost"] is populated from hidden params
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_stamps_response_cost_from_hidden_params():
    provider = LiteLLMProvider()

    choice = MagicMock()
    choice.message.content = "hello"
    choice.message.reasoning_content = ""
    choice.finish_reason = "stop"
    response = MagicMock()
    response.choices = [choice]
    response.usage.prompt_tokens = 8084
    response.usage.completion_tokens = 3922
    response.usage.total_tokens = 12006
    response.model_dump.return_value = {}
    response._hidden_params = {"response_cost": 0.0551}
    del response.response_cost  # mimic real litellm: no top-level attr

    with patch(
        "litellm.acompletion", new_callable=AsyncMock, return_value=response,
    ):
        result = await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-sonnet-5",
            _provider_config={"allow_paid_base_url": "true"},
        )
    assert result.raw["response_cost"] == pytest.approx(0.0551)
