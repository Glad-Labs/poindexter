"""Unit tests for host-brain mode of ClaudeCodeBridgeLLMService (#1006).

When ``host_brain_url`` is configured the bridge stops spawning ``claude -p``
locally (the read-only container) and POSTs each turn to a host daemon that
runs claude with full repo/git/write/MCP. These tests pin that path: the
payload + bearer token sent, a non-200 surfacing as a failed turn, and the
create/resume recovery still firing when the host reports "no conversation
found". ``httpx.AsyncClient`` is stubbed — no real network.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Iterator

import pytest


def _ensure_pipecat_stubs() -> None:
    if "pipecat" in sys.modules and getattr(
        sys.modules["pipecat"], "_voice_claude_test_stub", False
    ):
        return

    def _stub(name: str, **attrs: Any) -> types.ModuleType:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
        return mod

    root = _stub("pipecat")
    root._voice_claude_test_stub = True  # type: ignore[attr-defined]
    _stub("pipecat.frames")

    class _Frame:
        pass

    class _LLMTextFrame(_Frame):
        def __init__(self, text: str = ""):
            self.text = text

    class _LLMFullResponseStartFrame(_Frame):
        pass

    class _LLMFullResponseEndFrame(_Frame):
        pass

    class _LLMContextFrame(_Frame):
        def __init__(self, context):
            self.context = context

    _stub(
        "pipecat.frames.frames",
        Frame=_Frame,
        LLMTextFrame=_LLMTextFrame,
        LLMFullResponseStartFrame=_LLMFullResponseStartFrame,
        LLMFullResponseEndFrame=_LLMFullResponseEndFrame,
        LLMContextFrame=_LLMContextFrame,
    )
    _stub("pipecat.processors")
    _stub("pipecat.processors.aggregators")

    class _LLMContext:
        def __init__(self, **_kw):
            pass

        def get_messages(self):
            return []

    _stub("pipecat.processors.aggregators.llm_context", LLMContext=_LLMContext)
    _stub("pipecat.processors.frame_processor", FrameDirection=type("FD", (), {}))
    _stub("pipecat.services")

    class _LLMService:
        def __init__(self, **_kw):
            pass

        async def push_frame(self, *_a, **_kw):
            return None

        async def process_frame(self, *_a, **_kw):
            return None

    _stub("pipecat.services.llm_service", LLMService=_LLMService)


@pytest.fixture(autouse=True)
def _pipecat_stubs() -> Iterator[None]:
    _ensure_pipecat_stubs()
    yield


# ---------------------------------------------------------------------------
# Fake httpx.AsyncClient — scripts responses, records POSTs.
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


def _install_fake_httpx(monkeypatch, responses: list[_Resp]) -> list[dict]:
    import httpx

    calls: list[dict] = []

    class _Client:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None):
            calls.append({"url": url, "json": json, "headers": headers or {}})
            return responses.pop(0)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return calls


_URL = "http://host.docker.internal:8123/turn"
_UUID = "3699ec87-cb49-47b1-af0b-f6f6d83efa55"


def _result(text: str) -> str:
    return f'{{"type":"result","result":"{text}"}}'


@pytest.mark.asyncio
async def test_host_exec_posts_payload_with_token(monkeypatch):
    from cofounder_agent.services import voice_agent_claude_code as vac

    calls = _install_fake_httpx(
        monkeypatch, [_Resp(200, {"returncode": 0, "stdout": _result("hi back"), "stderr": ""})],
    )
    svc = vac.ClaudeCodeBridgeLLMService(
        cwd="/tmp", session_id=_UUID,
        host_brain_url=_URL, host_brain_token="s" * 32,
    )

    reply = await svc._run_claude("hello there")

    assert reply == "hi back"
    assert len(calls) == 1
    assert calls[0]["url"] == _URL
    assert calls[0]["headers"]["Authorization"] == "Bearer " + "s" * 32
    body = calls[0]["json"]
    assert body["text"] == "hello there"
    assert body["session_id"] == _UUID
    assert body["first_turn"] is False  # provided session_id => resume mode
    assert body["permission_mode"] == "dontAsk"


@pytest.mark.asyncio
async def test_host_exec_non_200_raises(monkeypatch):
    from cofounder_agent.services import voice_agent_claude_code as vac

    _install_fake_httpx(monkeypatch, [_Resp(401, {"error": "unauthorized"}, "unauthorized")])
    svc = vac.ClaudeCodeBridgeLLMService(
        cwd="/tmp", session_id=_UUID, host_brain_url=_URL, host_brain_token="bad",
    )

    with pytest.raises(RuntimeError, match="HTTP 401"):
        await svc._run_claude("hello")


@pytest.mark.asyncio
async def test_host_exec_recovers_create_on_no_conversation(monkeypatch):
    """A host 200 reporting rc=1 + 'no conversation found' triggers the same
    create-recovery as the local path: re-POST with first_turn flipped to
    True (create), same pinned id."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    calls = _install_fake_httpx(
        monkeypatch,
        [
            _Resp(200, {"returncode": 1, "stdout": "",
                        "stderr": f"No conversation found with session ID: {_UUID}"}),
            _Resp(200, {"returncode": 0, "stdout": _result("created"), "stderr": ""}),
        ],
    )
    svc = vac.ClaudeCodeBridgeLLMService(
        cwd="/tmp", session_id=_UUID, host_brain_url=_URL, host_brain_token="t" * 32,
    )

    reply = await svc._run_claude("hi")

    assert reply == "created"
    assert len(calls) == 2
    assert calls[0]["json"]["first_turn"] is False   # tried resume
    assert calls[1]["json"]["first_turn"] is True     # recovered -> create
    assert calls[1]["json"]["session_id"] == _UUID    # pin stays stable


@pytest.mark.asyncio
async def test_local_mode_when_no_host_url(monkeypatch):
    """No host_brain_url => host exec is never used (back-compat). We assert
    the dispatcher picks local without mocking a subprocess by stubbing
    _exec_local."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    svc = vac.ClaudeCodeBridgeLLMService(cwd="/tmp", session_id=_UUID)
    assert svc._host_brain_url is None

    called = {}

    async def _fake_local(user_text):
        called["text"] = user_text
        return (0, _result("local").encode(), b"")

    monkeypatch.setattr(svc, "_exec_local", _fake_local)
    # If it tried host mode it'd hit httpx (unstubbed) and fail loudly.
    reply = await svc._run_claude("hey")
    assert reply == "local"
    assert called["text"] == "hey"
