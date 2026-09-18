"""The per-call timeout must BIND on the dispatcher path (poindexter#969).

2026-08-01 chat-plan shakedown: ``pipeline_architect_timeout_seconds=120`` was
passed to ``ollama_chat_text``, and the call still sat **315s+** inside httpx
waiting for response headers. The per-call setting was honoured only by the
direct-httpx bootstrap fallback; on the pool path litellm's own
``config.timeout_seconds`` (300) was the only effective cap, so an Ollama cold
load under VRAM pressure hung far past the caller's budget.

The mechanism now exists end to end, but nothing pinned it — which is exactly
the shape of the original failure, since a dropped kwarg is silent. These tests
pin each hop of the chain:

    ollama_chat_text(timeout_setting=…)
      → site_config.get_float(…)
      → dispatch_complete(timeout_s=…)
      → **kwargs
      → LiteLLMProvider.complete(timeout_s=…)
      → litellm.acompletion(timeout=…)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services.llm_providers.litellm_provider import LiteLLMProvider

pytestmark = pytest.mark.unit

_LOCAL = "ollama/gemma-4-31b"


def _fake_response(text: str = "ok"):
    choice = MagicMock()
    choice.message.content = text
    choice.message.reasoning_content = ""
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = 3
    resp.usage.completion_tokens = 1
    resp.usage.total_tokens = 4
    resp.model_dump.return_value = {}
    return resp


# ---------------------------------------------------------------------------
# Hop 3: provider.complete(timeout_s=…) → litellm.acompletion(timeout=…)
# ---------------------------------------------------------------------------


async def test_per_call_timeout_reaches_litellm():
    provider = LiteLLMProvider()
    with patch(
        "litellm.acompletion", new_callable=AsyncMock, return_value=_fake_response(),
    ) as acomp:
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model=_LOCAL,
            timeout_s=120,
        )
    assert acomp.await_args.kwargs["timeout"] == 120.0


async def test_per_call_timeout_overrides_the_provider_default():
    """The #969 failure in one assertion: the caller's budget must win over
    the provider-config default, not lose to it."""
    provider = LiteLLMProvider()
    provider._timeout = 300.0
    with patch(
        "litellm.acompletion", new_callable=AsyncMock, return_value=_fake_response(),
    ) as acomp:
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model=_LOCAL,
            timeout_s=120,
        )
    assert acomp.await_args.kwargs["timeout"] == 120.0


async def test_provider_default_applies_when_the_caller_sets_none():
    provider = LiteLLMProvider()
    provider._timeout = 300.0
    with patch(
        "litellm.acompletion", new_callable=AsyncMock, return_value=_fake_response(),
    ) as acomp:
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}], model=_LOCAL,
        )
    assert acomp.await_args.kwargs["timeout"] == 300.0


async def test_timeout_s_is_consumed_and_never_forwarded_as_an_extra_param():
    """``timeout_s`` is our spelling, not litellm's. Leaking it through would
    reach the provider as an unknown body param — the class of bug that made
    Anthropic 400 on ``num_ctx``."""
    provider = LiteLLMProvider()
    with patch(
        "litellm.acompletion", new_callable=AsyncMock, return_value=_fake_response(),
    ) as acomp:
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model=_LOCAL,
            timeout_s=45,
        )
    assert "timeout_s" not in acomp.await_args.kwargs


# ---------------------------------------------------------------------------
# Hops 1-2: ollama_chat_text(timeout_setting=…) → dispatch_complete(timeout_s=…)
# ---------------------------------------------------------------------------


async def test_ollama_chat_text_threads_the_setting_to_the_dispatcher(monkeypatch):
    """The caller-side hop. ``pipeline_architect_timeout_seconds`` is the
    setting the #969 report used, and its value must arrive as ``timeout_s``."""
    from poindexter.services import llm_text

    seen: dict[str, Any] = {}

    async def _fake_dispatch(**kwargs):
        seen.update(kwargs)
        return MagicMock(text="ok", prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(
        "poindexter.services.llm_providers.dispatcher.dispatch_complete", _fake_dispatch,
    )
    monkeypatch.setattr(
        "poindexter.services.ollama_client.resolve_num_ctx", lambda *a, **k: None,
    )

    class _SC:
        def get(self, key, default=""):
            return default

        def get_float(self, key, default):
            return 120.0 if key == "pipeline_architect_timeout_seconds" else default

        def get_int(self, key, default):
            return default

    await llm_text.ollama_chat_text(
        "compose a plan",
        model="gemma-4-31b",
        timeout_setting="pipeline_architect_timeout_seconds",
        timeout_default=600.0,
        site_config=_SC(),
        pool=MagicMock(),
    )

    assert seen.get("timeout_s") == 120, (
        "the per-call budget must reach the dispatcher; without it litellm's "
        "own config default is the only cap and a cold load hangs past it"
    )
