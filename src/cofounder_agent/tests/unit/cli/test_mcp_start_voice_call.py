"""Unit tests for the ``start_voice_call`` MCP tool (Half B).

The tool lives in ``mcp-server/server.py`` — the public Poindexter MCP
server. From the cofounder_agent test suite we import it the same way
``test_mcp_oauth.py`` imports the OAuth helper: prepend ``mcp-server``
to ``sys.path`` and import ``server`` as a fresh module under a private
alias so it doesn't collide with the gladlabs operator MCP mirror.

Coverage:
- Happy path: brain flip + note returns the just-flipped mode + URL.
- Invalid brain: structured error, no DB write (no half-applied state).
- Note echoed verbatim (no AI rewrite, no truncation).
- Missing ``voice_agent_public_join_url`` -> loud error, not a silent
  hardcoded-URL fallback.

These run inside ``cd src/cofounder_agent && poetry run pytest tests/unit
-k "voice or mcp_start_voice"`` so the spec's gate command picks them
up alongside the voice-agent runtime-toggle tests.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Resolve the mcp-server directory the same way test_mcp_oauth does.
# Layout: <repo>/mcp-server/server.py and
#         <repo>/src/cofounder_agent/tests/unit/cli/test_mcp_start_voice_call.py
# Five parents up from THIS file -> <repo>/src/cofounder_agent
# Six parents up                  -> <repo>
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MCP_DIR = _REPO_ROOT / "mcp-server"


def _import_mcp_server():
    """Import ``mcp-server/server.py`` once, reuse on subsequent calls.

    The module registers tool callables on a FastMCP instance at import
    time. Importing once + reusing keeps the registry stable across
    tests. The server's heavy side effects (DB pool init, OAuth init)
    are LAZY — they fire only when the tool actually runs — so the
    import itself is cheap.

    Robust against test-ordering pollution: ``test_mcp_oauth.py`` (which
    runs earlier in the same process) prepends ``mcp-server-gladlabs``
    to sys.path, and that directory ALSO has an ``oauth_client.py``.
    Without forcing the correct one into ``sys.modules`` first, server's
    ``from oauth_client import MCP_CLIENT_ID_KEY`` resolves to the
    gladlabs mirror — which doesn't export that name — and the import
    fails. We pre-load the correct module under the bare ``oauth_client``
    name before exec'ing server.py.
    """
    mod_name = "mcp_server_under_test_for_start_voice_call"
    if mod_name in sys.modules:
        cached = sys.modules[mod_name]
        # If a previous attempt left a half-loaded module in sys.modules
        # (e.g. the import raised and pytest still cached the partial),
        # discard it and re-import. ``_get_pool`` is defined near the
        # top of server.py — its absence is a reliable "partial load"
        # marker.
        if hasattr(cached, "_get_pool"):
            return cached
        del sys.modules[mod_name]

    if str(_MCP_DIR) not in sys.path:
        sys.path.insert(0, str(_MCP_DIR))

    # Pre-load the right ``oauth_client`` (the public mcp-server one)
    # under the bare module name so server.py's
    # ``from oauth_client import ...`` finds the correct exports even
    # when sys.path has the gladlabs dir on it from a prior test.
    oauth_mod_name = "oauth_client"
    oauth_path = _MCP_DIR / "oauth_client.py"
    saved_oauth = sys.modules.get(oauth_mod_name)
    needs_restore = False
    if saved_oauth is None or getattr(saved_oauth, "MCP_CLIENT_ID_KEY", None) is None:
        # Either not loaded, or loaded as the gladlabs flavour. Force
        # the public one in. We restore ``saved_oauth`` after exec'ing
        # server.py so we don't break later tests that wanted the
        # gladlabs mirror.
        oauth_spec = importlib.util.spec_from_file_location(
            oauth_mod_name, oauth_path,
        )
        assert oauth_spec and oauth_spec.loader
        oauth_module = importlib.util.module_from_spec(oauth_spec)
        sys.modules[oauth_mod_name] = oauth_module
        oauth_spec.loader.exec_module(oauth_module)
        needs_restore = saved_oauth is not None

    try:
        spec = importlib.util.spec_from_file_location(
            mod_name, _MCP_DIR / "server.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        # Drop the partial module on failure so the next call retries
        # cleanly instead of seeing a half-initialised module.
        sys.modules.pop(mod_name, None)
        raise
    finally:
        if needs_restore:
            sys.modules[oauth_mod_name] = saved_oauth


def _resolve_tool_callable(mod, tool_name: str):
    """Pull the underlying coroutine out of FastMCP's tool wrapper.

    FastMCP wraps ``@mcp.tool()`` decorated functions; the underlying
    coroutine is exposed as ``.fn`` (current SDK) or ``.func`` (older
    SDK). Falling back to the raw attribute keeps this robust to MCP
    SDK upgrades.
    """
    tool_obj = getattr(mod, tool_name, None)
    for attr in ("fn", "func", "callable"):
        impl = getattr(tool_obj, attr, None)
        if callable(impl):
            return impl
    if callable(tool_obj):
        return tool_obj
    raise AssertionError(
        f"Could not resolve underlying callable for tool {tool_name!r}",
    )


# ---------------------------------------------------------------------------
# Fake asyncpg pool — records writes, returns configurable readback row
# ---------------------------------------------------------------------------


class _FakePool:
    def __init__(self, fetchrow_result: dict[str, Any] | None) -> None:
        self.fetchrow_result = fetchrow_result
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append((sql, args))
        return "INSERT 0 1"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        return self.fetchrow_result


@pytest.fixture
def mcp_server_with_fake_pool(monkeypatch):
    """Import the MCP server once, swap ``_get_pool`` for a fake.

    Returns a ``(server_module, fake_pool, start_voice_call_callable)``
    triple so tests can reach into both halves without re-resolving the
    tool wrapper each time.
    """
    server_mod = _import_mcp_server()
    pool = _FakePool(
        fetchrow_result={
            "brain_mode": "ollama",
            "join_url": "https://example.test/voice/join",
        },
    )

    async def _get_pool() -> _FakePool:
        return pool

    monkeypatch.setattr(server_mod, "_get_pool", _get_pool)

    return server_mod, pool, _resolve_tool_callable(server_mod, "start_voice_call")


# ===========================================================================
# Tests — names start with ``test_mcp_start_voice_`` so the spec's
# ``-k "voice or mcp_start_voice"`` filter picks them up unambiguously.
# ===========================================================================


@pytest.mark.asyncio
async def test_mcp_start_voice_call_happy_path_with_brain_flip(
    mcp_server_with_fake_pool,
):
    """brain='claude-code' + note: response carries the flipped brain
    and the echoed note; DB sees exactly one write to the canonical key.
    """
    _server, pool, start_voice_call = mcp_server_with_fake_pool

    # Readback reflects the post-write state.
    pool.fetchrow_result = {
        "brain_mode": "claude-code",
        "join_url": "https://example.test/voice/join",
    }

    raw = await start_voice_call(brain="claude-code", note="got a draft to review")
    payload = json.loads(raw)

    assert payload["join_url"] == "https://example.test/voice/join"
    assert payload["brain_mode"] == "claude-code"
    assert payload["note"] == "got a draft to review"
    assert "Tap the join_url" in payload["instructions"]

    # One write, canonical key, normalised value.
    assert len(pool.executes) == 1
    sql, args = pool.executes[0]
    assert "INSERT INTO app_settings" in sql
    assert args == ("voice_agent_brain_mode", "claude-code")


@pytest.mark.asyncio
async def test_mcp_start_voice_call_rejects_invalid_brain_without_db_write(
    mcp_server_with_fake_pool,
):
    """Unknown brain value -> structured error, NO write to app_settings.

    Per ``feedback_no_silent_defaults``: a half-applied state (write
    succeeded, validation later) is worse than failing loud. Validation
    runs BEFORE the DB write, so the row is untouched on rejection.
    """
    _server, pool, start_voice_call = mcp_server_with_fake_pool

    raw = await start_voice_call(brain="totally-not-a-brain")
    payload = json.loads(raw)

    assert "error" in payload
    assert "totally-not-a-brain" in payload["error"]
    assert "ollama" in payload["error"]
    assert "claude-code" in payload["error"]
    assert payload["valid_brains"] == ["ollama", "claude-code"]

    # NO write happened. (The DB is the source of truth for runtime
    # config — leaving it untouched on failure is the contract.)
    assert pool.executes == []


@pytest.mark.asyncio
async def test_mcp_start_voice_call_default_invocation_does_not_flip(
    mcp_server_with_fake_pool,
):
    """No brain arg + no note: the tool is read-only and just hands
    back the existing URL + brain mode.
    """
    _server, pool, start_voice_call = mcp_server_with_fake_pool

    raw = await start_voice_call()
    payload = json.loads(raw)

    assert payload["brain_mode"] == "ollama"
    assert payload["note"] is None
    assert payload["join_url"]  # non-empty
    assert pool.executes == []  # no writes on the read-only path


@pytest.mark.asyncio
async def test_mcp_start_voice_call_fails_loud_on_missing_join_url(
    mcp_server_with_fake_pool,
):
    """No ``voice_agent_public_join_url`` -> loud error, no fake URL.

    Without this guard, an operator on a custom deployment would get a
    Tailscale Funnel URL that doesn't reach their box.
    """
    _server, pool, start_voice_call = mcp_server_with_fake_pool
    pool.fetchrow_result = {"brain_mode": "ollama", "join_url": ""}

    raw = await start_voice_call()
    payload = json.loads(raw)

    assert "error" in payload
    assert payload["missing_setting"] == "voice_agent_public_join_url"
