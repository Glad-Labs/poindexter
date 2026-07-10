"""Pin the aiohttp-transport opt-out on LiteLLMProvider.

Root cause (GlitchTip issue 736, 13 events 2026-07-04→09): litellm 1.89.2
defaults to its custom aiohttp transport
(``litellm/llms/custom_httpx/aiohttp_transport.py``). aiohttp pools keep-alive
connections to Ollama; in the sparse nightly pipeline window Ollama closes idle
connections server-side, and aiohttp's next read on the pooled-but-dead socket
raises ``SocketTimeoutError: Timeout on reading data from socket`` *instantly*
(``Timeout passed=90.0, time taken=0.001 seconds``). litellm's
``exception_type`` mislabels it ``litellm.Timeout`` → ``APIConnectionError:
OllamaException``, which our ``complete()`` logs (→ GlitchTip) and re-raises.

The fix: set ``litellm.disable_aiohttp_transport = True`` so litellm uses httpx,
which discards closed pooled connections on reuse instead of failing instantly.
DB-configurable (default true) via
``plugin.llm_provider.litellm.disable_aiohttp_transport`` — flip to ``false`` to
restore litellm's aiohttp default.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import litellm
import pytest

from services.llm_providers.litellm_provider import LiteLLMProvider

# No module-level asyncio mark: ``asyncio_mode = "auto"`` auto-marks coroutines.


@pytest.fixture(autouse=True)
def _restore_transport_global():
    """``litellm.disable_aiohttp_transport`` is process-wide — save/restore so
    a test that flips it can't leak into the rest of the suite."""
    original = getattr(litellm, "disable_aiohttp_transport", False)
    try:
        yield
    finally:
        litellm.disable_aiohttp_transport = original


def _ok_response():
    msg = MagicMock()
    msg.content = "ok"
    del msg.reasoning_content
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    usage = MagicMock()
    usage.prompt_tokens = 5
    usage.completion_tokens = 5
    usage.total_tokens = 10
    resp.usage = usage
    resp.model_dump = lambda: {}
    return resp


class TestDisableAiohttpTransport:
    async def test_default_disables_aiohttp_transport(self):
        # A default-config dispatch must leave litellm on the httpx transport.
        litellm.disable_aiohttp_transport = False  # simulate a fresh process
        provider = LiteLLMProvider()
        with patch("litellm.acompletion", AsyncMock(return_value=_ok_response())):
            await provider.complete(
                messages=[{"role": "user", "content": "go"}],
                model="ollama/gemma3:27b",
                _provider_config={"api_base": "http://host.docker.internal:11434"},
            )
        assert litellm.disable_aiohttp_transport is True

    async def test_config_false_restores_aiohttp_transport(self):
        # Operator opt-back-in to litellm's aiohttp default.
        litellm.disable_aiohttp_transport = True
        provider = LiteLLMProvider()
        with patch("litellm.acompletion", AsyncMock(return_value=_ok_response())):
            await provider.complete(
                messages=[{"role": "user", "content": "go"}],
                model="ollama/gemma3:27b",
                _provider_config={
                    "api_base": "http://host.docker.internal:11434",
                    "disable_aiohttp_transport": False,
                },
            )
        assert litellm.disable_aiohttp_transport is False

    async def test_string_true_is_coerced(self):
        # app_settings values arrive as TEXT — "true"/"false" must coerce.
        litellm.disable_aiohttp_transport = False
        provider = LiteLLMProvider()
        with patch("litellm.acompletion", AsyncMock(return_value=_ok_response())):
            await provider.complete(
                messages=[{"role": "user", "content": "go"}],
                model="ollama/gemma3:27b",
                _provider_config={
                    "api_base": "http://host.docker.internal:11434",
                    "disable_aiohttp_transport": "true",
                },
            )
        assert litellm.disable_aiohttp_transport is True

    async def test_string_false_is_coerced(self):
        litellm.disable_aiohttp_transport = True
        provider = LiteLLMProvider()
        with patch("litellm.acompletion", AsyncMock(return_value=_ok_response())):
            await provider.complete(
                messages=[{"role": "user", "content": "go"}],
                model="ollama/gemma3:27b",
                _provider_config={
                    "api_base": "http://host.docker.internal:11434",
                    "disable_aiohttp_transport": "false",
                },
            )
        assert litellm.disable_aiohttp_transport is False
