"""Wiring tests: LiteLLMProvider ↔ the cold-load VRAM guard (2026-08-25).

The guard's own behavior is pinned in ``test_coldload_guard.py``; these pin
the provider seam:

- ``complete()`` and ``stream()`` invoke the guard exactly when a local
  api_base attaches, passing the RESOLVED model + effective base and the
  config-row tunables (``coldload_reclaim_enabled`` /
  ``coldload_reclaim_min_gb``);
- a cloud-prefix call (no api_base attach) never invokes it;
- the conftest autouse isolation (``_isolate_coldload_reclaim_guard``)
  keeps every OTHER provider test off the real guard — these tests
  re-patch the symbol locally, which wins inside their ``with`` blocks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_providers.litellm_provider import LiteLLMProvider

_LOCAL_BASE = "http://host.docker.internal:11434"


def _fake_response(text: str = "ok"):
    choice = MagicMock()
    choice.message.content = text
    choice.message.tool_calls = None
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = 3
    resp.usage.completion_tokens = 1
    resp.usage.total_tokens = 4
    resp.model_dump.return_value = {}
    return resp


class _FakeStreamResponse:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def _guard_patch():
    return patch(
        "services.llm_providers.litellm_provider.maybe_reclaim_before_coldload",
        new_callable=AsyncMock,
        return_value=False,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_invokes_guard_for_local_model():
    provider = LiteLLMProvider()
    with _guard_patch() as guard, patch(
        "litellm.acompletion", new_callable=AsyncMock,
        return_value=_fake_response(),
    ):
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma-4-31B-it-qat:latest",
            _provider_config={
                "api_base": _LOCAL_BASE,
                "allow_paid_base_url": "false",
            },
        )
    guard.assert_awaited_once_with(
        resolved_model="ollama/gemma-4-31B-it-qat:latest",
        api_base=_LOCAL_BASE,
        enabled=True,
        min_gb=8.0,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_passes_config_row_tunables():
    provider = LiteLLMProvider()
    with _guard_patch() as guard, patch(
        "litellm.acompletion", new_callable=AsyncMock,
        return_value=_fake_response(),
    ):
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma-4-31B-it-qat:latest",
            _provider_config={
                "api_base": _LOCAL_BASE,
                "allow_paid_base_url": "false",
                "coldload_reclaim_enabled": "false",
                "coldload_reclaim_min_gb": "12",
            },
        )
    guard.assert_awaited_once_with(
        resolved_model="ollama/gemma-4-31B-it-qat:latest",
        api_base=_LOCAL_BASE,
        enabled=False,
        min_gb=12.0,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_cloud_prefix_never_invokes_guard():
    # A cloud prefix never attaches the local api_base (``_api_base_applies``),
    # so the guard must not fire — reclaim on a cloud call would evict local
    # media models for a request that never touches the render GPU.
    provider = LiteLLMProvider()
    with _guard_patch() as guard, patch(
        "litellm.acompletion", new_callable=AsyncMock,
        return_value=_fake_response(),
    ):
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-sonnet-5",
            _provider_config={
                "api_base": _LOCAL_BASE,
                "allow_paid_base_url": "true",
            },
        )
    guard.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_invokes_guard_for_local_model():
    provider = LiteLLMProvider()
    with _guard_patch() as guard, patch(
        "litellm.acompletion", new_callable=AsyncMock,
        return_value=_FakeStreamResponse(),
    ):
        async for _token in provider.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma-4-31B-it-qat:latest",
            _provider_config={
                "api_base": _LOCAL_BASE,
                "allow_paid_base_url": "false",
            },
        ):
            pass
    guard.assert_awaited_once_with(
        resolved_model="ollama/gemma-4-31B-it-qat:latest",
        api_base=_LOCAL_BASE,
        enabled=True,
        min_gb=8.0,
    )
