"""Unit tests pinning the first-turn session-collision recovery (poindexter#431).

The voice agent's :class:`ClaudeCodeBridgeLLMService` generates a fresh
``--session-id`` UUID at construction. If something else spawns
``claude`` with that UUID before the user's first audible turn (pipecat
warmup, a healthcheck, a restarted bot inheriting a UUID), claude
refuses the first user turn with ``Error: Session ID <uuid> is already
in use.`` The fix flips to ``--resume`` and retries once.

These tests stub :func:`asyncio.create_subprocess_exec` so they run
without the pipecat / claude binaries actually being available.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any, Iterator

import pytest


# ---------------------------------------------------------------------------
# Stub the tiny slice of pipecat that voice_agent_claude_code imports at
# module load. (The Pipecat install only lives in the voice-agent Docker
# image, not the unit-test venv.)
# ---------------------------------------------------------------------------


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

    class _FrameDirection:
        DOWNSTREAM = "downstream"
        UPSTREAM = "upstream"

    _stub("pipecat.processors.frame_processor", FrameDirection=_FrameDirection)
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
# Fake subprocess so we can drive returncode + stderr deterministically.
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        return None


class _SpawnRecorder:
    """Stand-in for asyncio.create_subprocess_exec that scripts a queue
    of (returncode, stdout, stderr) responses and records every argv it
    was called with so tests can assert on flag transitions
    (--session-id → --resume).
    """

    def __init__(self, responses: list[tuple[int, bytes, bytes]]):
        self._responses = list(responses)
        self.calls: list[list[str]] = []

    async def __call__(self, *argv: str, **_kwargs: Any) -> _FakeProc:
        self.calls.append(list(argv))
        if not self._responses:
            raise AssertionError(
                f"_SpawnRecorder ran out of scripted responses on call "
                f"#{len(self.calls)} argv={argv!r}",
            )
        rc, out, err = self._responses.pop(0)
        return _FakeProc(rc, out, err)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_turn_session_collision_retries_with_resume(monkeypatch):
    """First --session-id call fails with 'already in use' → bridge
    transparently retries with --resume and returns the second call's
    JSON result. Mirrors the 2026-05-08 voice-agent repro."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    success_payload = b'{"type":"result","result":"hello back"}'
    recorder = _SpawnRecorder(
        [
            (1, b"", b"Error: Session ID abc is already in use."),
            (0, success_payload, b""),
        ],
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)

    svc = vac.ClaudeCodeBridgeLLMService(
        cwd="/tmp", session_id=None, claude_binary="claude-fake",
    )
    # Override the bridge's generated UUID so we can assert on it.
    svc._session_id = "abc"
    svc._first_turn = True

    reply = await svc._run_claude("hi")

    assert reply == "hello back"
    assert len(recorder.calls) == 2
    # First call used --session-id (the colliding probe path).
    assert "--session-id" in recorder.calls[0]
    assert "--resume" not in recorder.calls[0]
    # Second call (the retry) used --resume against the same UUID.
    assert "--resume" in recorder.calls[1]
    assert "--session-id" not in recorder.calls[1]
    assert recorder.calls[1][recorder.calls[1].index("--resume") + 1] == "abc"
    # After the retry the bridge has flipped its internal state so every
    # subsequent turn stays on --resume.
    assert svc._first_turn is False
    assert svc._resumed is True


@pytest.mark.asyncio
async def test_non_collision_first_turn_failure_still_raises(monkeypatch):
    """Any non-'already in use' first-turn failure must NOT trigger the
    silent retry — that path is reserved for the documented race."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    recorder = _SpawnRecorder(
        [(2, b"", b"Error: claude binary segfaulted, RIP")],
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)

    svc = vac.ClaudeCodeBridgeLLMService(cwd="/tmp", session_id=None)
    svc._first_turn = True

    with pytest.raises(RuntimeError, match="segfaulted"):
        await svc._run_claude("hi")

    # Failed without a retry attempt.
    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_non_first_turn_collision_does_not_double_retry(monkeypatch):
    """Once past the first turn the bridge is already on --resume; a
    'session already in use' there is a real bug (not the documented
    race) and should surface as an error rather than loop forever."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    recorder = _SpawnRecorder(
        [(1, b"", b"Error: Session ID abc is already in use.")],
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)

    svc = vac.ClaudeCodeBridgeLLMService(cwd="/tmp", session_id=None)
    svc._first_turn = False  # i.e. we've already done at least one turn

    with pytest.raises(RuntimeError, match="already in use"):
        await svc._run_claude("hi")

    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_happy_path_first_turn_no_collision(monkeypatch):
    """Sanity check: when claude accepts --session-id on the first turn
    we don't add a phantom retry call."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    recorder = _SpawnRecorder(
        [(0, b'{"type":"result","result":"first reply"}', b"")],
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)

    svc = vac.ClaudeCodeBridgeLLMService(cwd="/tmp", session_id=None)
    svc._first_turn = True

    reply = await svc._run_claude("hi")

    assert reply == "first reply"
    assert len(recorder.calls) == 1
    assert "--session-id" in recorder.calls[0]
    assert svc._first_turn is False
