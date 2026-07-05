"""Unit tests for the DB-driven pinned + auto-resetting claude-code voice
session (Glad-Labs/poindexter#1006).

The always-on voice room pins ONE ``claude -p`` session so context survives
container restarts, and rotates that session when it ages out, burns through
its token budget, or the operator says a manual-reset phrase. These tests pin
the rotation contract on :class:`ClaudeCodeBridgeLLMService`:

  * token-budget trip → ``_session_id`` changes + persist callback awaited
  * age trip (via an injected fake ``monotonic``) → id changes
  * manual phrase → id changes AND rotation happens before the (mocked) send
  * under-threshold turn → id unchanged AND persist NOT called
  * cumulative token accounting adds up across turns

``asyncio.create_subprocess_exec`` is stubbed so no real ``claude`` binary is
invoked. Pipecat is stubbed the same way as the sibling collision test so the
module imports on the unit-test venv.
"""

from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Stub the tiny slice of pipecat that voice_agent_claude_code imports at
# module load. (Pipecat only lives in the voice-agent Docker image, not the
# unit-test venv — same gate the collision test uses.)
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
        def __init__(self, messages=None, **_kw):
            self._messages = messages or []

        def get_messages(self):
            return self._messages

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
# Fakes: deterministic subprocess + a settable monotonic clock.
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
    """Stand-in for asyncio.create_subprocess_exec that scripts a queue of
    (returncode, stdout, stderr) responses and records every argv."""

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


class _FakeClock:
    """Settable monotonic clock injected into the service."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _result_payload(text: str, in_tokens: int = 0, out_tokens: int = 0) -> bytes:
    """Build a `--output-format json` result array element with usage."""
    import json

    return json.dumps(
        [
            {
                "type": "result",
                "result": text,
                "usage": {
                    "input_tokens": in_tokens,
                    "output_tokens": out_tokens,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "total_cost_usd": 0.0,
            }
        ]
    ).encode("utf-8")


class _PersistSpy:
    """Records each persisted session id; usable as the async callback."""

    def __init__(self) -> None:
        self.ids: list[str] = []

    async def __call__(self, new_id: str) -> None:
        self.ids.append(new_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_budget_trip_rotates_and_persists(monkeypatch):
    """Once cumulative tokens exceed the budget, the next turn rotates the
    session (new uuid, --session-id create) and awaits the persist callback."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    recorder = _SpawnRecorder([(0, _result_payload("ok"), b"")])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)

    persist = _PersistSpy()
    svc = vac.ClaudeCodeBridgeLLMService(
        cwd="/tmp",
        session_id="pinned-1",
        token_budget=1000,
        persist_session_id=persist,
    )
    original = svc._session_id
    # Simulate a prior turn having blown the budget.
    svc._cumulative_tokens = 1500

    await svc._maybe_reset("just a normal message")

    assert svc._session_id != original
    assert svc._first_turn is True
    assert svc._resumed is False
    assert svc._cumulative_tokens == 0
    assert persist.ids == [svc._session_id]


@pytest.mark.asyncio
async def test_age_trip_rotates_via_fake_clock(monkeypatch):
    """When the session is older than max_age_seconds (measured by the
    injected clock) the next reset check rotates it."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    clock = _FakeClock(now=1000.0)
    persist = _PersistSpy()
    svc = vac.ClaudeCodeBridgeLLMService(
        cwd="/tmp",
        session_id="pinned-1",
        max_age_seconds=300,
        monotonic=clock,
        persist_session_id=persist,
    )
    original = svc._session_id

    # Not yet aged out — no rotation.
    clock.now = 1000.0 + 299
    await svc._maybe_reset("hello")
    assert svc._session_id == original
    assert persist.ids == []

    # Cross the age threshold — rotation fires.
    clock.now = 1000.0 + 300
    await svc._maybe_reset("hello again")
    assert svc._session_id != original
    assert persist.ids == [svc._session_id]


@pytest.mark.asyncio
async def test_manual_phrase_rotates_before_send(monkeypatch):
    """A manual-reset phrase rotates the session, and the rotation happens
    BEFORE the (mocked) claude send so turn 1 of the fresh session is a
    create, not a resume."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    recorder = _SpawnRecorder([(0, _result_payload("fresh start"), b"")])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)
    # Keep the turn hermetic — the transcript mirror hits the DB/webhook path
    # which isn't available in the unit venv (it's caught + logged in prod;
    # we stub it so the test doesn't depend on DB creds).
    async def _no_transcript(*_a, **_kw):
        return None

    monkeypatch.setattr(vac, "_push_transcript_to_discord", _no_transcript)

    # Feed the user turn deterministically rather than constructing a real
    # pipecat ``LLMContext``. The stubbed-vs-real-pipecat context extraction
    # diverged in CI's forked full-suite run (the hand-built context yielded
    # empty text → _process_context early-returned → no rotation → flaky
    # failure), but production extraction works (Emma answers live voice).
    # This test only cares that a manual phrase rotates BEFORE the send, so
    # bypass the context layer entirely.
    monkeypatch.setattr(
        vac, "_latest_user_text_impl", lambda _ctx: "start fresh please",
    )

    persist = _PersistSpy()
    svc = vac.ClaudeCodeBridgeLLMService(
        cwd="/tmp",
        session_id="pinned-1",
        persist_session_id=persist,
    )
    # push_frame is a stub no-op in the unit venv but real in a real-pipecat
    # env; neutralise it so the test exercises only the reset→send ordering.
    async def _noop_push(*_a, **_kw):
        return None

    monkeypatch.setattr(svc, "push_frame", _noop_push)
    original = svc._session_id

    # Drive a full turn through the context handler so we exercise the
    # ordering: _maybe_reset must run before _run_claude. The context object
    # is unused now that extraction is patched.
    await svc._process_context(object())

    assert svc._session_id != original
    assert persist.ids == [svc._session_id]
    # The single claude send used --session-id (a create on the new id),
    # not --resume — proving rotation preceded the send.
    assert len(recorder.calls) == 1
    assert "--session-id" in recorder.calls[0]
    assert "--resume" not in recorder.calls[0]
    assert recorder.calls[0][recorder.calls[0].index("--session-id") + 1] == svc._session_id


@pytest.mark.parametrize(
    "phrase",
    [
        "start fresh",
        "Start Fresh please",
        "new session",
        "reset the session",
        "reset session",
        "reset the conversation",
        "reset conversation",
    ],
)
@pytest.mark.asyncio
async def test_manual_phrases_match(monkeypatch, phrase):
    """All documented manual-reset phrases trigger a rotation."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    svc = vac.ClaudeCodeBridgeLLMService(cwd="/tmp", session_id="pinned-1")
    original = svc._session_id
    await svc._maybe_reset(phrase)
    assert svc._session_id != original, f"phrase {phrase!r} should rotate"


@pytest.mark.asyncio
async def test_under_threshold_turn_does_not_rotate(monkeypatch):
    """A normal under-threshold turn leaves the session id intact and does
    NOT call the persist callback."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    clock = _FakeClock(now=1000.0)
    persist = _PersistSpy()
    svc = vac.ClaudeCodeBridgeLLMService(
        cwd="/tmp",
        session_id="pinned-1",
        token_budget=1000,
        max_age_seconds=300,
        monotonic=clock,
        persist_session_id=persist,
    )
    original = svc._session_id
    svc._cumulative_tokens = 500  # under budget
    clock.now = 1000.0 + 100  # under age

    await svc._maybe_reset("what's the post count")

    assert svc._session_id == original
    assert persist.ids == []


@pytest.mark.asyncio
async def test_cumulative_token_accounting_adds_up(monkeypatch):
    """Each successful turn adds input+output tokens to the running total."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    recorder = _SpawnRecorder(
        [
            (0, _result_payload("r1", in_tokens=100, out_tokens=50), b""),
            (0, _result_payload("r2", in_tokens=30, out_tokens=20), b""),
        ],
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)

    svc = vac.ClaudeCodeBridgeLLMService(cwd="/tmp", session_id="pinned-1")
    assert svc._cumulative_tokens == 0

    await svc._run_claude("first")
    assert svc._cumulative_tokens == 150

    await svc._run_claude("second")
    assert svc._cumulative_tokens == 200


@pytest.mark.asyncio
async def test_extract_usage_handles_missing_usage(monkeypatch):
    """_extract_usage returns 0 when the payload has no usage block, and
    _extract_text still returns the result text (no regression)."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    payload = b'{"type":"result","result":"no usage here"}'
    assert vac.ClaudeCodeBridgeLLMService._extract_text(payload) == "no usage here"
    assert vac.ClaudeCodeBridgeLLMService._extract_usage(payload) == 0


@pytest.mark.asyncio
async def test_no_persist_callback_is_safe(monkeypatch):
    """Rotation with no persist callback configured must not raise."""
    from cofounder_agent.services import voice_agent_claude_code as vac

    svc = vac.ClaudeCodeBridgeLLMService(
        cwd="/tmp",
        session_id="pinned-1",
        token_budget=10,
    )
    original = svc._session_id
    svc._cumulative_tokens = 100
    await svc._maybe_reset("hi")  # should not raise
    assert svc._session_id != original


def test_run_bot_lazy_import_symbols_resolve():
    """``run_bot``'s ``claude-code`` block lazily imports these symbols and
    constructs ``AdminDatabase(pool)`` to persist the rotated session id. A
    wrong name (e.g. ``AdminDB`` vs ``AdminDatabase``) is an ImportError only
    when that block actually runs on a live container — invisible to every
    service-class test above. Pin the wiring contract here so the next rename
    fails in CI, not at 2 a.m. on the voice line (#1006)."""
    # The settings-write class the persist callback constructs.
    from cofounder_agent.services.admin_db import AdminDatabase

    assert hasattr(AdminDatabase, "set_setting"), (
        "run_bot persists the pinned session via AdminDatabase.set_setting; "
        "the method moved or was renamed."
    )
    # The brain service the block constructs with the resolved session.
    # The auto-reset ctor params run_bot threads in must still exist.
    import inspect

    from cofounder_agent.services.voice_agent_claude_code import (
        ClaudeCodeBridgeLLMService,
    )

    params = inspect.signature(ClaudeCodeBridgeLLMService.__init__).parameters
    for required in ("session_id", "token_budget", "max_age_seconds", "persist_session_id"):
        assert required in params, f"run_bot passes {required!r}; ctor dropped it."
