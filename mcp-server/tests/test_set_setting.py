"""Tests for the set_setting MCP tool — poindexter#750.

Two fixes under test:

1. Permission check fails CLOSED: any exception on the fetchrow blocks the
   write instead of passing through (the old ``except Exception: pass`` bug).
2. Mutation is delegated to PUT /api/settings/{key} instead of a raw asyncpg
   upsert, so read-only protection, secret handling, and cache invalidation
   are enforced by the service layer, not re-implemented here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

HERE = Path(__file__).resolve().parent
MCP_SERVER_DIR = HERE.parent
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))

import server  # noqa: E402

# ---------------------------------------------------------------------------
# Fake asyncpg pool
# ---------------------------------------------------------------------------


class _FakePool:
    def __init__(
        self,
        perm_row=None,
        fetchrow_exc: Exception | None = None,
    ):
        self._perm_row = perm_row
        self._fetchrow_exc = fetchrow_exc
        self.executed: list[tuple] = []

    async def fetchrow(self, _query: str, *_args):
        if self._fetchrow_exc is not None:
            raise self._fetchrow_exc
        return self._perm_row

    async def execute(self, _query: str, *args):
        self.executed.append(args)


# ---------------------------------------------------------------------------
# Permission check — fail CLOSED (#750 fix 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_permission_check_fail_closed_blocks_write() -> None:
    """A DB error on the agent_permissions query must BLOCK the write, not
    pass through.  The old code had `except Exception: pass` which silently
    allowed the upsert — this test verifies it is now fail-closed."""
    pool = _FakePool(fetchrow_exc=Exception("pg: connection refused"))
    with (
        patch.object(server, "_get_pool", AsyncMock(return_value=pool)),
        patch.object(server, "_api", AsyncMock()) as mock_api,
    ):
        result = await server.set_setting("some_key", "new_val")

    # Result must signal an error, not "Updated"
    assert "Updated" not in result
    assert "permission_check" in result or "failed" in result.lower()
    # The REST API must NOT have been reached
    mock_api.assert_not_called()


@pytest.mark.asyncio
async def test_missing_permissions_table_allows_write() -> None:
    """A missing ``agent_permissions`` TABLE (``UndefinedTableError``) means the
    gate is unconfigured, so the write must PROCEED via REST — not fail closed.

    Regression for the production outage where #687's dead-Gitea-table sweep
    dropped ``agent_permissions`` while the gate code stayed live, so every MCP
    ``set_setting`` write blocked with ``UndefinedTableError``. The boundary is
    deliberate: a *missing table* (this test) is allowed, but an indeterminate
    error like connection-refused (the test above) still fails closed (#750)."""
    pool = _FakePool(
        fetchrow_exc=asyncpg.exceptions.UndefinedTableError(
            'relation "agent_permissions" does not exist'
        )
    )
    with (
        patch.object(server, "_get_pool", AsyncMock(return_value=pool)),
        patch.object(
            server, "_api",
            AsyncMock(return_value={"key": "mykey", "value": "v"}),
        ) as mock_api,
    ):
        result = await server.set_setting("mykey", "v")

    mock_api.assert_called_once_with("PUT", "/api/settings/mykey", {"value": "v"})
    assert "Updated" in result


@pytest.mark.asyncio
async def test_permission_denied_no_approval_blocks_write() -> None:
    """allowed=False, requires_approval=False → plain deny; REST not called."""
    perm = {"allowed": False, "requires_approval": False}
    pool = _FakePool(perm_row=perm)
    with (
        patch.object(server, "_get_pool", AsyncMock(return_value=pool)),
        patch.object(server, "_api", AsyncMock()) as mock_api,
    ):
        result = await server.set_setting("some_key", "new_val")

    assert "Permission denied" in result
    assert "cannot write" in result
    mock_api.assert_not_called()


@pytest.mark.asyncio
async def test_permission_denied_with_approval_queues_change() -> None:
    """allowed=False, requires_approval=True → change queued; REST not called."""
    perm = {"allowed": False, "requires_approval": True}
    pool = _FakePool(perm_row=perm)
    with (
        patch.object(server, "_get_pool", AsyncMock(return_value=pool)),
        patch.object(server, "_api", AsyncMock()) as mock_api,
    ):
        result = await server.set_setting("some_key", "new_val")

    assert "queued for approval" in result
    mock_api.assert_not_called()


# ---------------------------------------------------------------------------
# REST delegation (#750 fix 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegates_to_rest_put_not_raw_asyncpg() -> None:
    """No permission row (None) → allowed; write goes through REST API,
    not a direct asyncpg upsert."""
    pool = _FakePool(perm_row=None)
    with (
        patch.object(server, "_get_pool", AsyncMock(return_value=pool)),
        patch.object(
            server, "_api",
            AsyncMock(return_value={"key": "mykey", "value": "v"}),
        ) as mock_api,
    ):
        result = await server.set_setting("mykey", "v")

    mock_api.assert_called_once_with("PUT", "/api/settings/mykey", {"value": "v"})
    assert "Updated" in result


@pytest.mark.asyncio
async def test_category_prefix_stripped_before_rest_path() -> None:
    """``category/key`` form → bare key used as REST path parameter."""
    pool = _FakePool(perm_row=None)
    with (
        patch.object(server, "_get_pool", AsyncMock(return_value=pool)),
        patch.object(
            server, "_api",
            AsyncMock(return_value={"key": "mykey", "value": "v"}),
        ) as mock_api,
    ):
        await server.set_setting("pipeline/mykey", "v")

    # Path must use the bare key, not the category-prefixed form
    mock_api.assert_called_once_with("PUT", "/api/settings/mykey", {"value": "v"})


@pytest.mark.asyncio
async def test_rest_api_error_is_surfaced_not_swallowed() -> None:
    """An error from the REST API (e.g. 403 read-only) must surface in the
    tool return value, not be silently dropped."""
    pool = _FakePool(perm_row=None)
    with (
        patch.object(server, "_get_pool", AsyncMock(return_value=pool)),
        patch.object(
            server, "_api",
            AsyncMock(return_value={"error": "HTTP 403: read-only setting"}),
        ),
    ):
        result = await server.set_setting("protected_key", "v")

    assert "Failed to update" in result
    assert "read-only" in result
