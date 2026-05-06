"""Tests for the HTTP tool allowlist (Glad-Labs/poindexter#239).

These tests poke ``http_server`` directly without spinning up the full
worker / FastAPI app — the goal is to verify:

* ``POINDEXTER_MCP_HTTP_TOOL_ALLOWLIST`` parsing is whitespace-tolerant
  and correctly distinguishes "unset" from "explicitly empty".
* ``_apply_tool_allowlist`` mutates the FastMCP tool manager so
  ``list_tools`` and ``call_tool`` (the underlying primitives that
  back MCP's ``tools/list`` and ``tools/call`` RPCs) both reflect the
  filter.
* Unlisted tool calls raise ``ToolError("Unknown tool: ...")`` — the
  error MCP surfaces to clients via ``-32602`` invalid-params /
  ``isError: true``.
* Unknown allowlist entries are silently ignored.
* Stdio entry point is unaffected — only ``build_app`` (HTTP) runs the
  filter, so importing ``server`` for a stdio launch keeps the full
  registry.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

# Make ``import http_server`` resolve regardless of where pytest is invoked.
HERE = Path(__file__).resolve().parent
MCP_SERVER_DIR = HERE.parent
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))

import http_server  # noqa: E402 — sys.path adjustment above


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_fake_mcp() -> FastMCP:
    """Create a fresh FastMCP instance with three tools mimicking the
    read/write split (``read_a``, ``read_b``, ``write_c``).

    Using a fresh instance keeps the tests hermetic — we don't touch
    the real ``server.mcp`` (which has 25+ tools and side effects on
    import via the wider repo).
    """
    mcp = FastMCP("AllowlistTest")

    @mcp.tool()
    def read_a() -> str:
        """Read tool A."""
        return "a"

    @mcp.tool()
    def read_b() -> str:
        """Read tool B."""
        return "b"

    @mcp.tool()
    def write_c() -> str:
        """Write tool C — would be excluded by the voice/mobile allowlist."""
        return "c"

    return mcp


# ---------------------------------------------------------------------------
# _parse_tool_allowlist — env var parsing semantics
# ---------------------------------------------------------------------------


def test_parse_returns_none_when_env_var_unset() -> None:
    """Unset env var ⇒ ``None`` ⇒ caller skips filtering ⇒ pre-#239 behaviour."""
    assert http_server._parse_tool_allowlist(None) is None


def test_parse_empty_string_is_explicit_empty_set() -> None:
    """``""`` is the explicit "expose no tools" choice, distinct from unset."""
    result = http_server._parse_tool_allowlist("")
    assert result == frozenset()
    assert result is not None  # MUST be distinguishable from "unset"


def test_parse_is_whitespace_tolerant() -> None:
    """Operators paste from docs; tolerate ``a, b ,c`` style spacing."""
    assert http_server._parse_tool_allowlist("a, b ,c") == frozenset({"a", "b", "c"})


def test_parse_drops_empty_entries() -> None:
    """Trailing commas / double commas shouldn't introduce a ``""`` tool."""
    assert http_server._parse_tool_allowlist("a,,b,") == frozenset({"a", "b"})
    # And whitespace-only entries (``a, ,b``) should also drop.
    assert http_server._parse_tool_allowlist("a, ,b") == frozenset({"a", "b"})


# ---------------------------------------------------------------------------
# _apply_tool_allowlist — registry mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unfiltered_registry_exposes_all_tools() -> None:
    """Sanity: without applying the allowlist the registry has all tools."""
    mcp = _build_fake_mcp()
    names = {t.name for t in await mcp.list_tools()}
    assert names == {"read_a", "read_b", "write_c"}


@pytest.mark.asyncio
async def test_apply_allowlist_filters_list_tools_to_subset() -> None:
    """``tools/list`` (backed by ``list_tools``) reflects the filter."""
    mcp = _build_fake_mcp()
    removed = http_server._apply_tool_allowlist(mcp, frozenset({"read_a", "read_b"}))
    names = {t.name for t in await mcp.list_tools()}
    assert names == {"read_a", "read_b"}
    assert removed == ["write_c"]


@pytest.mark.asyncio
async def test_apply_allowlist_blocks_call_tool_for_unlisted() -> None:
    """``tools/call`` (backed by ``call_tool``) raises for filtered tools.

    FastMCP's tool manager raises ``ToolError("Unknown tool: ...")`` when
    a name isn't in the registry — that's the same error path an
    unregistered name would hit, which the MCP layer surfaces as the
    standard ``isError: true`` tool-call response.
    """
    mcp = _build_fake_mcp()
    http_server._apply_tool_allowlist(mcp, frozenset({"read_a"}))

    # Allowed call works.
    result = await mcp.call_tool("read_a", {})
    # FastMCP normalises the return into structured content blocks; the
    # only thing we care about here is that no error was raised.
    assert result is not None

    # Filtered call raises.
    with pytest.raises(ToolError, match="Unknown tool: write_c"):
        await mcp.call_tool("write_c", {})
    with pytest.raises(ToolError, match="Unknown tool: read_b"):
        await mcp.call_tool("read_b", {})


@pytest.mark.asyncio
async def test_apply_empty_allowlist_removes_every_tool() -> None:
    """``""`` env var → frozenset() → every tool removed (explicit hard stop)."""
    mcp = _build_fake_mcp()
    removed = http_server._apply_tool_allowlist(mcp, frozenset())
    assert sorted(removed) == ["read_a", "read_b", "write_c"]
    assert list(await mcp.list_tools()) == []
    with pytest.raises(ToolError):
        await mcp.call_tool("read_a", {})


@pytest.mark.asyncio
async def test_apply_allowlist_silently_ignores_unknown_names() -> None:
    """Typos / forward-compat names in the allowlist must NOT crash.

    ``DEFAULT_VOICE_MOBILE_ALLOWLIST`` is documentation that operators
    copy verbatim; if a tool gets renamed the allowlist must remain
    valid (just with the renamed entry no-op'd) until the operator
    notices and updates their config.
    """
    mcp = _build_fake_mcp()
    removed = http_server._apply_tool_allowlist(
        mcp,
        frozenset({"read_a", "read_b", "write_c", "totally_made_up_tool"}),
    )
    # All real tools allowlisted ⇒ none removed; the bogus name is dropped silently.
    assert removed == []
    names = {t.name for t in await mcp.list_tools()}
    assert names == {"read_a", "read_b", "write_c"}


# ---------------------------------------------------------------------------
# Stdio path is untouched — only build_app applies the filter
# ---------------------------------------------------------------------------


def test_stdio_path_does_not_call_apply_tool_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stdio entry point is ``server.py``'s ``mcp.run()``, which doesn't
    import ``http_server``. We assert that fact structurally:

    * ``server.py``'s text never references ``_apply_tool_allowlist`` or
      ``POINDEXTER_MCP_HTTP_TOOL_ALLOWLIST``.
    * ``http_server.main()``'s flow goes through ``build_app()`` (HTTP),
      so the filter is bound to the HTTP transport build, not to import
      of the underlying ``server`` module.

    If a future refactor moves the filter into ``server.py`` itself, this
    test trips and the author has to consciously revisit the stdio
    contract (which the issue explicitly says must stay full-fat).
    """
    server_src = (MCP_SERVER_DIR / "server.py").read_text(encoding="utf-8")
    assert "_apply_tool_allowlist" not in server_src, (
        "server.py must not call the HTTP-only allowlist filter — "
        "stdio transport stays full-fat per Glad-Labs/poindexter#239."
    )
    assert "POINDEXTER_MCP_HTTP_TOOL_ALLOWLIST" not in server_src, (
        "server.py must not read the HTTP-only allowlist env var."
    )


# ---------------------------------------------------------------------------
# Default voice/mobile allowlist constant — documentation contract
# ---------------------------------------------------------------------------


def test_default_voice_mobile_allowlist_matches_spec() -> None:
    """The constant documented in the docstring is the same 13 read-only
    tools the issue spec calls out — this guards against accidental
    drift between the docstring and the constant."""
    expected = {
        "search_memory",
        "recall_decision",
        "find_similar_posts",
        "list_tasks",
        "get_post_count",
        "get_setting",
        "list_settings",
        "get_audit_log",
        "get_audit_summary",
        "get_brain_knowledge",
        "check_health",
        "get_budget",
        "memory_stats",
    }
    assert set(http_server.DEFAULT_VOICE_MOBILE_ALLOWLIST) == expected
    assert len(http_server.DEFAULT_VOICE_MOBILE_ALLOWLIST) == 13
