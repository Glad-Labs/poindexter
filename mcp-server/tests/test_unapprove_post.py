"""Tests for the ``unapprove_post`` MCP tool — undo an accidental approve on
a task that hasn't published yet.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

HERE = Path(__file__).resolve().parent
MCP_SERVER_DIR = HERE.parent
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))

import server  # noqa: E402


@pytest.mark.asyncio
async def test_unapprove_post_default_target():
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server, "_api", AsyncMock(return_value={"status": "awaiting_approval"}),
        ) as api,
    ):
        out = await server.unapprove_post("abc1")
    api.assert_awaited_once_with(
        "POST", "/api/tasks/full/unapprove",
        data={"to": "awaiting_approval", "feedback": None},
    )
    assert "awaiting_approval" in out


@pytest.mark.asyncio
async def test_unapprove_post_to_rejected_final_with_feedback():
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server, "_api", AsyncMock(return_value={"status": "rejected_final"}),
        ) as api,
    ):
        out = await server.unapprove_post("abc1", to="rejected_final", feedback="Off-topic")
    api.assert_awaited_once_with(
        "POST", "/api/tasks/full/unapprove",
        data={"to": "rejected_final", "feedback": "Off-topic"},
    )
    assert "rejected_final" in out
