"""Tests for the ``start_voice_call`` MCP tool (Half B runtime brain-mode toggle).

The tool is the assistant's voice-call summon surface — when Claude
decides "let's hop on a call" / "I want to talk through this draft
live", it calls ``start_voice_call`` and gets back a JSON payload
with a tap-to-join URL and the effective brain mode.

These tests exercise the tool function directly (no FastMCP transport
roundtrip) with a fake asyncpg pool. We don't spin up Postgres because
the SQL is tiny and the value here is asserting the wiring — flip
mutates the canonical ``voice_agent_brain_mode`` key, response carries
the just-flipped value, invalid input is rejected loudly, missing URL
fails loud.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Make ``import server`` resolve regardless of where pytest is invoked.
HERE = Path(__file__).resolve().parent
MCP_SERVER_DIR = HERE.parent
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))

import server  # noqa: E402 — sys.path adjustment above


# ---------------------------------------------------------------------------
# Fake asyncpg pool
#
# server.start_voice_call calls ``await pool.execute(...)`` to write the
# brain mode and ``await pool.fetchrow(...)`` to read back the join URL +
# effective brain. The fake records writes so we can assert the canonical
# key was the target, and returns a configurable row for the read.
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal ``asyncpg.Pool``-shaped fake.

    Records every (sql, args) pair sent to ``execute``; ``fetchrow``
    returns whichever dict the test's fixture set. The real pool's
    transaction / connection-acquire surface is unused by the tool.
    """

    def __init__(self, fetchrow_result: dict[str, Any] | None) -> None:
        self.fetchrow_result = fetchrow_result
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append((sql, args))
        return "INSERT 0 1"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        if self.fetchrow_result is None:
            return None
        # Return an object with __getitem__ since the tool indexes
        # row["brain_mode"] / row["join_url"]. dict already supports that.
        return self.fetchrow_result


@pytest.fixture
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> _FakePool:
    """Patch ``server._get_pool`` to yield a recording fake pool.

    Default fetchrow returns the migration's seeded values so happy-
    path tests don't have to repeat the URL in every parametrise
    block.
    """
    pool = _FakePool(
        fetchrow_result={
            "brain_mode": "ollama",
            "join_url": "https://nightrider.taild4f626.ts.net/voice/join",
        },
    )

    async def _get_pool() -> _FakePool:
        return pool

    monkeypatch.setattr(server, "_get_pool", _get_pool)
    return pool


# ---------------------------------------------------------------------------
# FastMCP decorates tools — pull the underlying coroutine out so the
# test calls the function directly, not through the transport. The
# ``fn`` attribute is the FastMCP convention; we fall back to ``func``
# (older MCP SDK) and finally to looking up by name on the registered
# tool manager so the test stays robust to library upgrades.
# ---------------------------------------------------------------------------


def _resolve_tool_callable(tool_name: str):
    tool_obj = getattr(server, tool_name, None)
    for attr in ("fn", "func", "callable"):
        impl = getattr(tool_obj, attr, None)
        if callable(impl):
            return impl
    if callable(tool_obj):
        return tool_obj
    raise AssertionError(
        f"Could not resolve underlying callable for tool {tool_name!r}; "
        "MCP SDK shape may have changed.",
    )


_start_voice_call = _resolve_tool_callable("start_voice_call")


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_start_voice_call_happy_path_no_brain_flip(fake_pool):
    """Default invocation: no brain arg, no note. Returns the seeded URL
    and the existing brain mode without writing to app_settings.
    """
    raw = await _start_voice_call()
    payload = json.loads(raw)

    # Response shape — exactly the four fields the assistant relies on.
    assert payload["join_url"] == "https://nightrider.taild4f626.ts.net/voice/join"
    assert payload["brain_mode"] == "ollama"
    assert payload["note"] is None
    assert "Tap the join_url" in payload["instructions"]
    assert "ollama" in payload["instructions"]  # echoes effective mode

    # No brain flip means no UPSERT to app_settings — the ONLY DB
    # interaction should be the readback.
    assert fake_pool.executes == [], (
        "start_voice_call wrote to app_settings even though brain=None; "
        "the runtime-toggle contract is 'flip only when asked'."
    )


@pytest.mark.asyncio
async def test_start_voice_call_flips_brain_to_claude_code(fake_pool):
    """brain='claude-code' + note: persists the canonical setting and
    echoes the note in the response.
    """
    # The tool reads back AFTER the write, so the readback must reflect
    # the flip.
    fake_pool.fetchrow_result = {
        "brain_mode": "claude-code",
        "join_url": "https://nightrider.taild4f626.ts.net/voice/join",
    }

    raw = await _start_voice_call(brain="claude-code", note="got a draft to review")
    payload = json.loads(raw)

    assert payload["brain_mode"] == "claude-code"
    assert payload["note"] == "got a draft to review"
    assert payload["join_url"] == "https://nightrider.taild4f626.ts.net/voice/join"

    # Exactly one write, targeting the canonical _mode key with the
    # normalised value. (The legacy voice_agent_brain key is
    # intentionally not touched on the write path — see tool docstring.)
    assert len(fake_pool.executes) == 1
    sql, args = fake_pool.executes[0]
    assert "INSERT INTO app_settings" in sql
    assert args == ("voice_agent_brain_mode", "claude-code")


@pytest.mark.asyncio
async def test_start_voice_call_normalises_brain_value_before_persisting(fake_pool):
    """A stray space / mixed case in ``brain`` should be normalised
    before the DB write — operators dictating to voice should not need
    to enunciate 'lower hyphen lower-case'.
    """
    fake_pool.fetchrow_result = {
        "brain_mode": "claude-code",
        "join_url": "https://nightrider.taild4f626.ts.net/voice/join",
    }

    raw = await _start_voice_call(brain=" Claude-Code ")
    payload = json.loads(raw)
    assert payload["brain_mode"] == "claude-code"

    # The normalised value should land in the DB — not the user's
    # original whitespace-noisy string.
    assert fake_pool.executes[0][1] == ("voice_agent_brain_mode", "claude-code")


@pytest.mark.asyncio
async def test_start_voice_call_rejects_invalid_brain(fake_pool):
    """An unrecognised brain value yields a structured error and DOES
    NOT write to app_settings (per feedback_no_silent_defaults — a
    half-applied state would mask the bug).
    """
    raw = await _start_voice_call(brain="totally-not-a-brain")
    payload = json.loads(raw)

    # Structured error, not a quiet success.
    assert "error" in payload
    assert "totally-not-a-brain" in payload["error"]
    assert "ollama" in payload["error"]
    assert "claude-code" in payload["error"]
    assert payload["valid_brains"] == ["ollama", "claude-code"]

    # The DB MUST be untouched on a validation failure — no half-write.
    assert fake_pool.executes == [], (
        "Invalid brain still wrote to app_settings — that would leave "
        "the system in a half-applied state contrary to "
        "feedback_no_silent_defaults."
    )


@pytest.mark.asyncio
async def test_start_voice_call_fails_loud_when_join_url_missing(fake_pool):
    """If ``voice_agent_public_join_url`` is unset we must NOT silently
    fall back to a hardcoded URL — operators on a custom deployment
    would get a link that doesn't reach their bot.
    """
    fake_pool.fetchrow_result = {
        "brain_mode": "ollama",
        "join_url": "",
    }

    raw = await _start_voice_call()
    payload = json.loads(raw)

    assert "error" in payload
    assert "voice_agent_public_join_url" in payload["error"]
    assert payload["missing_setting"] == "voice_agent_public_join_url"


@pytest.mark.asyncio
async def test_start_voice_call_falls_back_to_legacy_brain_for_read(fake_pool):
    """When voice_agent_brain_mode isn't set but the legacy
    voice_agent_brain row exists, the readback's COALESCE picks the
    legacy value — operators upgrading from 20260505_135518 don't see
    a phantom default until they touch the new key.

    The COALESCE chain lives in SQL, so we model it by returning the
    legacy value as the readback's ``brain_mode`` field. The fake
    pool's job is to simulate what the real DB would return; this test
    asserts the tool surfaces it verbatim.
    """
    fake_pool.fetchrow_result = {
        "brain_mode": "claude-code",  # legacy row would land here via COALESCE
        "join_url": "https://nightrider.taild4f626.ts.net/voice/join",
    }

    raw = await _start_voice_call()
    payload = json.loads(raw)
    assert payload["brain_mode"] == "claude-code"


@pytest.mark.asyncio
async def test_start_voice_call_note_is_echoed_verbatim(fake_pool):
    """The note arg is round-tripped untouched so the operator's client
    can render it as-is — no "ai-improved" rewrite, no truncation.
    """
    note = "Found a regression in the publish flow — let's pair on it."
    raw = await _start_voice_call(note=note)
    payload = json.loads(raw)
    assert payload["note"] == note
