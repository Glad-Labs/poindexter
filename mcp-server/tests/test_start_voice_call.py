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
from contextlib import asynccontextmanager
from datetime import datetime
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
# ``start_voice_call`` reads + writes settings through
# ``services.admin_db.AdminDatabase``, which acquires a connection
# (``async with pool.acquire() as conn``) and then calls ``conn.fetchrow``
# for per-key reads and ``conn.execute`` for the upsert. So the fake models
# the pool as a ``setting_key -> value`` map: ``fetchrow`` returns a full
# ``app_settings`` row for a known key (so ``AdminDatabase.get_setting`` can
# build a ``SettingResponse``), and every ``execute`` is recorded on
# ``executes`` so tests can assert the canonical key was the write target.
#
# This mirrors the proven fake in
# ``src/cofounder_agent/tests/unit/cli/test_mcp_start_voice_call.py`` — the
# sibling test that was kept current when ``start_voice_call`` was de-SQL'd
# onto ``AdminDatabase`` (commit 8db8e0321) while this twin was left behind.
# ---------------------------------------------------------------------------

_SETTINGS_DT = datetime(2026, 1, 1)


def _settings_row(key: str, value: str) -> dict[str, Any]:
    """Build a minimal ``app_settings`` row for ``AdminDatabase.get_setting``."""
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "key": key,
        "value": value,
        "category": "voice",
        "description": "",
        "is_secret": False,
        "is_active": True,
        "created_at": _SETTINGS_DT,
        "updated_at": _SETTINGS_DT,
    }


class _FakeConn:
    """Fake asyncpg connection — records executes, serves rows by key."""

    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def execute(self, sql: str, *args: Any) -> str:
        self._pool.executes.append((sql, args))
        return "INSERT 0 1"

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        # AdminDatabase.get_setting always binds the setting key as $1.
        key = args[0] if args else None
        if not key or key not in self._pool.settings:
            return None
        return _settings_row(key, self._pool.settings[key])

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return None

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        return []


class _FakePool:
    """Minimal ``asyncpg.Pool``-shaped fake.

    Holds a ``setting_key -> value`` map plus an ``executes`` log. The
    only pool surface the tool touches is ``acquire()`` (an async context
    manager yielding a connection), so that's all we implement. Note
    ``execute`` does NOT mutate ``settings`` — a test that needs the
    post-write readback to reflect a flip pre-seeds ``settings`` itself.
    """

    def __init__(self, settings: dict[str, str]) -> None:
        self.settings: dict[str, str] = dict(settings)
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def acquire(self):
        yield _FakeConn(self)


@pytest.fixture
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> _FakePool:
    """Patch ``server._get_pool`` to yield a recording fake pool.

    Default settings carry the migration-seeded brain mode + join URL so
    happy-path tests don't repeat them.
    """
    pool = _FakePool(
        settings={
            "voice_agent_brain_mode": "ollama",
            "voice_agent_public_join_url": "https://example.test/voice/join",
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
    assert payload["join_url"] == "https://example.test/voice/join"
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
    # The tool reads back AFTER the write. The fake's execute() doesn't
    # mutate settings, so pre-seed the post-flip value to model "the
    # write landed" — AdminDatabase.get_setting_value then returns it.
    fake_pool.settings["voice_agent_brain_mode"] = "claude-code"

    raw = await _start_voice_call(brain="claude-code", note="got a draft to review")
    payload = json.loads(raw)

    assert payload["brain_mode"] == "claude-code"
    assert payload["note"] == "got a draft to review"
    assert payload["join_url"] == "https://example.test/voice/join"

    # Exactly one write, targeting the canonical _mode key with the
    # normalised value. (The legacy voice_agent_brain key is
    # intentionally not touched on the write path — see tool docstring.)
    # AdminDatabase.set_setting binds (key, value, category, description),
    # so assert on the leading two positional args.
    assert len(fake_pool.executes) == 1
    sql, args = fake_pool.executes[0]
    assert "INSERT INTO app_settings" in sql
    assert args[0] == "voice_agent_brain_mode"
    assert args[1] == "claude-code"


@pytest.mark.asyncio
async def test_start_voice_call_normalises_brain_value_before_persisting(fake_pool):
    """A stray space / mixed case in ``brain`` should be normalised
    before the DB write — operators dictating to voice should not need
    to enunciate 'lower hyphen lower-case'.
    """
    # Pre-seed the post-flip readback value (execute() doesn't mutate
    # settings — see the happy-path flip test).
    fake_pool.settings["voice_agent_brain_mode"] = "claude-code"

    raw = await _start_voice_call(brain=" Claude-Code ")
    payload = json.loads(raw)
    assert payload["brain_mode"] == "claude-code"

    # The normalised value should land in the DB — not the user's
    # original whitespace-noisy string. (args = key, value, category, …)
    _sql, args = fake_pool.executes[0]
    assert args[0] == "voice_agent_brain_mode"
    assert args[1] == "claude-code"


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
    # Drop the URL key entirely — AdminDatabase.get_setting_value then
    # returns None, collapsing join_url to "" and tripping the guard.
    del fake_pool.settings["voice_agent_public_join_url"]

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
    # Canonical key absent, legacy voice_agent_brain present: the tool's
    # read chain (voice_agent_brain_mode -> voice_agent_brain -> "ollama")
    # should surface the legacy value. (The old single-fetchrow fake
    # couldn't model the fallback; the per-key fake exercises it for real.)
    del fake_pool.settings["voice_agent_brain_mode"]
    fake_pool.settings["voice_agent_brain"] = "claude-code"

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
