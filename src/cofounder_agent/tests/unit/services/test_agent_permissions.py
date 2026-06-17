"""Unit tests for ``services.agent_permissions``."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from services import agent_permissions


def _make_pool(fetchrow_return=None, execute_return=None):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.execute = AsyncMock(return_value=execute_return)
    return pool


class TestCheckWritePermission:
    async def test_no_row_means_allowed(self):
        pool = _make_pool(fetchrow_return=None)
        allowed, requires_approval = await agent_permissions.check_write_permission(
            pool, "mcp_server", "app_settings", "write"
        )
        assert allowed is True
        assert requires_approval is False

    async def test_allowed_row_returns_true(self):
        pool = _make_pool(fetchrow_return={"allowed": True, "requires_approval": False})
        allowed, requires_approval = await agent_permissions.check_write_permission(
            pool, "mcp_server", "app_settings", "write"
        )
        assert allowed is True
        assert requires_approval is False

    async def test_denied_row_returns_false(self):
        pool = _make_pool(fetchrow_return={"allowed": False, "requires_approval": False})
        allowed, requires_approval = await agent_permissions.check_write_permission(
            pool, "mcp_server", "app_settings", "write"
        )
        assert allowed is False

    async def test_denied_with_approval_returns_requires_true(self):
        pool = _make_pool(fetchrow_return={"allowed": False, "requires_approval": True})
        allowed, requires_approval = await agent_permissions.check_write_permission(
            pool, "mcp_server", "app_settings", "write"
        )
        assert allowed is False
        assert requires_approval is True

    async def test_queries_correct_columns(self):
        pool = _make_pool()
        await agent_permissions.check_write_permission(
            pool, "mcp_server", "app_settings", "write"
        )
        sql = pool.fetchrow.await_args.args[0]
        assert "agent_permissions" in sql
        assert "allowed" in sql
        assert "requires_approval" in sql


class TestQueueForApproval:
    async def test_inserts_into_approval_queue(self):
        pool = _make_pool()
        await agent_permissions.queue_for_approval(
            pool,
            agent_name="mcp_server",
            resource="app_settings",
            action="write",
            proposed_change={"key": "foo", "value": "bar"},
            reason="MCP set_setting tool",
        )
        pool.execute.assert_awaited_once()
        sql = pool.execute.await_args.args[0]
        assert "approval_queue" in sql

    async def test_proposed_change_serialized_as_json(self):
        pool = _make_pool()
        change = {"key": "foo", "value": "bar"}
        await agent_permissions.queue_for_approval(
            pool,
            agent_name="mcp_server",
            resource="app_settings",
            action="write",
            proposed_change=change,
            reason="reason",
        )
        args = pool.execute.await_args.args
        # proposed_change is passed as JSON string param
        json_param = next(a for a in args[1:] if isinstance(a, str) and "foo" in a)
        parsed = json.loads(json_param)
        assert parsed["key"] == "foo"
