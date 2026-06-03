"""Tests for the ``rebuild_static_export`` MCP tool timeout contract.

Regression guard for Glad-Labs/poindexter#657: a full static export runs
~35s at current post volume, but ``_api`` defaulted to a 15s read timeout.
httpx raised ``ReadTimeout`` *before* the worker's 200 came back, and the
tool flattened that into ``Export failed:`` — a false negative on a
successful export. Dangerous for operators: a takedown/rebuild looks failed
and may be retried or assumed not to have worked.

These tests exercise the tool function directly (no FastMCP transport
roundtrip) with ``server._api`` monkeypatched, so they assert the wiring:
the export tool must hand ``_api`` a timeout comfortably above the export's
worst-case duration, and a slow-but-successful 200 must render as success.
"""

from __future__ import annotations

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


def _resolve_tool_callable(tool_name: str):
    """Pull the underlying coroutine out from behind the FastMCP decorator.

    Mirrors the helper in ``test_start_voice_call.py`` — the ``fn`` attr is
    the FastMCP convention, with ``func`` / direct-callable fallbacks so the
    test survives an SDK shape change.
    """
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


_rebuild_static_export = _resolve_tool_callable("rebuild_static_export")


@pytest.fixture
def captured_api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch ``server._api`` to record its call and return a happy export.

    Captures method / path / kwargs so a test can assert the timeout the
    tool requested, and returns a representative successful export body.
    """
    captured: dict[str, Any] = {}

    async def _fake_api(method: str, path: str, data: dict | None = None, **kwargs: Any) -> dict:
        captured["method"] = method
        captured["path"] = path
        captured["data"] = data
        captured["kwargs"] = kwargs
        return {
            "posts_exported": 92,
            "categories_exported": 6,
            "authors_exported": 2,
            "total_files": 100,
            "errors": [],
        }

    monkeypatch.setattr(server, "_api", _fake_api)
    return captured


@pytest.mark.asyncio
async def test_rebuild_static_export_uses_generous_timeout(captured_api):
    """The export tool must request a timeout above the ~35s worst case.

    This is the core #657 regression guard: with the old hardcoded 15s the
    call timed out before the 200 and reported a false failure. We assert
    the requested timeout clears the observed ~35s worst case with headroom,
    rather than pinning an exact constant (so tuning the value later doesn't
    break the test as long as it stays safe).
    """
    await _rebuild_static_export()

    assert captured_api["method"] == "POST"
    assert captured_api["path"] == "/api/export/rebuild"

    timeout = captured_api["kwargs"].get("timeout")
    assert timeout is not None, (
        "rebuild_static_export must pass an explicit timeout to _api; relying "
        "on the 15s default reintroduces the #657 false-failure on slow exports."
    )
    assert timeout >= 60.0, (
        f"export timeout {timeout}s is too tight for the ~35s worst-case full "
        "rebuild (Glad-Labs/poindexter#657) — give it real headroom."
    )


@pytest.mark.asyncio
async def test_rebuild_static_export_reports_success_on_slow_200(captured_api):
    """A successful export body renders as success, not a failure string.

    Complements the timeout assertion: even when the worker takes its time,
    once the 200 arrives the tool must summarise it as completed — never the
    ``Export failed:`` path.
    """
    result = await _rebuild_static_export()

    assert "Export failed" not in result
    assert "completed successfully" in result
    assert "Posts: 92" in result


@pytest.mark.asyncio
async def test_rebuild_static_export_still_surfaces_real_errors(monkeypatch):
    """A genuine ``{"error": ...}`` from _api still reports failure.

    The fix widens the timeout; it must NOT swallow real failures — a non-2xx
    or transport error must still reach the operator as ``Export failed:``.
    """
    async def _failing_api(method: str, path: str, data: dict | None = None, **kwargs: Any) -> dict:
        return {"error": "HTTP 500: boom"}

    monkeypatch.setattr(server, "_api", _failing_api)

    result = await _rebuild_static_export()
    assert result.startswith("Export failed:")
    assert "HTTP 500: boom" in result
