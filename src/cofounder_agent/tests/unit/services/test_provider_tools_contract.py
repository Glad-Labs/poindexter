"""Cross-provider tool-calling contract (poindexter#947).

The chat agent probes ``getattr(provider, "supports_tools", False)`` and
trusts the answer, so the flag must be honest per provider:

  - litellm / openai_compat: True, and tool_calls normalize to the same
    plain-dict shape (litellm's own suite covers its extraction; the
    openai_compat path is covered here),
  - ollama_native: False, and a ``tools=`` call FAILS LOUD with the
    litellm remediation instead of silently answering without tools
    (feedback_no_silent_defaults).
"""

from __future__ import annotations

from typing import Any

import pytest

from services.llm_providers.ollama_native import OllamaNativeProvider
from services.llm_providers.openai_compat import OpenAICompatProvider


@pytest.mark.unit
class TestSupportsToolsFlags:
    def test_flags_match_reality(self):
        assert OllamaNativeProvider.supports_tools is False
        assert OpenAICompatProvider.supports_tools is True
        from services.llm_providers.litellm_provider import LiteLLMProvider

        assert LiteLLMProvider.supports_tools is True


@pytest.mark.unit
class TestOllamaNativeRefusesTools:
    @pytest.mark.asyncio
    async def test_tools_kwarg_fails_loud_with_remediation(self):
        p = OllamaNativeProvider()
        with pytest.raises(RuntimeError, match="litellm"):
            await p.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="qwen2.5:7b",
                tools=[{"type": "function", "function": {"name": "t"}}],
            )

    @pytest.mark.asyncio
    async def test_empty_tools_list_is_not_a_tools_request(self, monkeypatch):
        """``tools=[]`` (falsy) must not trip the guard — only a real
        tool-schema payload does."""
        p = OllamaNativeProvider()

        class FakeClient:
            async def generate(self, **kwargs: Any):
                return {"response": "ok", "model": "m"}

        monkeypatch.setattr(p, "_get_client", lambda: FakeClient())
        out = await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="qwen2.5:7b", tools=[],
        )
        assert out.text == "ok"


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient; records the POSTed payload."""

    last_payload: dict[str, Any] | None = None
    next_response: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: Any):
        return False

    async def post(self, url: str, *, json: dict[str, Any], headers: Any):
        _FakeAsyncClient.last_payload = json
        return _FakeResponse(_FakeAsyncClient.next_response)


@pytest.mark.unit
class TestOpenAICompatToolCalling:
    @pytest.mark.asyncio
    async def test_tools_forwarded_and_tool_calls_normalized(self, monkeypatch):
        import services.llm_providers.openai_compat as oc

        monkeypatch.setattr(oc.httpx, "AsyncClient", _FakeAsyncClient)
        _FakeAsyncClient.next_response = {
            "model": "m",
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_9",
                        "function": {"name": "get_budget", "arguments": "{}"},
                    }],
                },
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2,
                      "total_tokens": 3},
        }
        p = OpenAICompatProvider()
        tools = [{"type": "function", "function": {"name": "get_budget"}}]
        out = await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="m", tools=tools, tool_choice="auto",
            _provider_config={"base_url": "http://localhost:11434/v1"},
        )
        assert _FakeAsyncClient.last_payload["tools"] == tools
        assert _FakeAsyncClient.last_payload["tool_choice"] == "auto"
        assert out.tool_calls == [
            {"id": "call_9", "name": "get_budget", "arguments": "{}"},
        ]
        # content: null on a tool turn must map to "", not None.
        assert out.text == ""

    @pytest.mark.asyncio
    async def test_prose_turn_has_no_tool_calls(self, monkeypatch):
        import services.llm_providers.openai_compat as oc

        monkeypatch.setattr(oc.httpx, "AsyncClient", _FakeAsyncClient)
        _FakeAsyncClient.next_response = {
            "model": "m",
            "choices": [{"finish_reason": "stop",
                         "message": {"content": "hello"}}],
            "usage": {},
        }
        p = OpenAICompatProvider()
        out = await p.complete(
            messages=[{"role": "user", "content": "hi"}], model="m",
            _provider_config={"base_url": "http://localhost:11434/v1"},
        )
        assert out.tool_calls is None
        assert out.text == "hello"
