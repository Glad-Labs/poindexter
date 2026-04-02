"""
Unit tests for services/permission_service.py

Tests agent permission checking, approval queue, and access control.
"""

import json
from unittest.mock import AsyncMock

import pytest

from services.permission_service import (
    PermissionResult,
    approve_change,
    check_permission,
    deny_change,
    get_pending_approvals,
    require_permission,
)


def _make_pool(permission_row=None, approval_row=None):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=permission_row)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return pool


class TestCheckPermission:
    async def test_allowed_returns_true(self):
        pool = _make_pool({"allowed": True, "requires_approval": False})
        assert await check_permission(pool, "claude_code", "app_settings", "write") is True

    async def test_denied_returns_false(self):
        pool = _make_pool({"allowed": False, "requires_approval": False})
        assert await check_permission(pool, "public_api", "app_settings", "read") is False

    async def test_requires_approval_returns_false(self):
        pool = _make_pool({"allowed": False, "requires_approval": True})
        assert await check_permission(pool, "openclaw", "prompt_templates", "write") is False

    async def test_no_permission_row_returns_false(self):
        pool = _make_pool(None)
        assert await check_permission(pool, "unknown_agent", "anything", "write") is False


class TestRequirePermission:
    async def test_allowed_status(self):
        pool = _make_pool({"allowed": True, "requires_approval": False})
        result = await require_permission(pool, "claude_code", "app_settings", "write")
        assert result.status == "allowed"
        assert result.allowed is True

    async def test_denied_status(self):
        pool = _make_pool({"allowed": False, "requires_approval": False})
        result = await require_permission(pool, "pipeline", "prompt_templates", "write")
        assert result.status == "denied"
        assert result.allowed is False

    async def test_queued_for_approval(self):
        pool = _make_pool({"allowed": False, "requires_approval": True})
        pool.fetchrow = AsyncMock(side_effect=[
            {"allowed": False, "requires_approval": True},  # permission check
            {"id": 42},  # INSERT RETURNING id
        ])
        result = await require_permission(
            pool, "openclaw", "app_settings", "write",
            proposed_change={"key": "qa_threshold", "value": "60"},
            reason="testing threshold adjustment",
        )
        assert result.status == "queued_for_approval"
        assert result.approval_id == 42
        assert result.requires_approval is True

    async def test_no_permission_row(self):
        pool = _make_pool(None)
        result = await require_permission(pool, "unknown", "anything", "delete")
        assert result.status == "denied"


class TestApproveChange:
    async def test_approve_existing(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"id": 1, "status": "pending"})
        pool.execute = AsyncMock()
        assert await approve_change(pool, 1, "matt") is True
        pool.execute.assert_awaited_once()

    async def test_approve_nonexistent(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        assert await approve_change(pool, 999) is False


class TestDenyChange:
    async def test_deny(self):
        pool = AsyncMock()
        pool.execute = AsyncMock()
        assert await deny_change(pool, 1, "matt") is True


class TestGetPendingApprovals:
    async def test_returns_pending(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": 1, "agent_name": "openclaw", "resource": "app_settings",
             "action": "write", "reason": "test", "created_at": "2026-04-02"},
        ])
        pending = await get_pending_approvals(pool)
        assert len(pending) == 1
        assert pending[0]["agent_name"] == "openclaw"

    async def test_empty_queue(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        assert await get_pending_approvals(pool) == []
