"""
Unit tests for WebSocketManager and WebSocketMessage.

All tests are async, using pytest-asyncio (asyncio mode=auto in pyproject.toml).
No real WebSocket connections are opened — mock objects simulate send_text.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from services.websocket_manager import WebSocketManager, WebSocketMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ws(fail: bool = False) -> AsyncMock:
    """Create a mock WebSocket whose send_text succeeds (or raises if fail=True)."""
    ws = AsyncMock()
    if fail:
        ws.send_text.side_effect = RuntimeError("connection closed")
    return ws


# ---------------------------------------------------------------------------
# WebSocketMessage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWebSocketMessage:
    def test_to_json_produces_valid_json(self):
        msg = WebSocketMessage(type="progress", event="task.done", data={"taskId": "t-1"})
        payload = json.loads(msg.to_json())
        assert payload["type"] == "progress"
        assert payload["event"] == "task.done"
        assert payload["data"]["taskId"] == "t-1"

    def test_timestamp_is_set_automatically(self):
        msg = WebSocketMessage(type="x", event="y", data={})
        assert msg.timestamp is not None
        # Should be parseable as an ISO datetime string
        datetime.fromisoformat(msg.timestamp.replace("Z", "+00:00"))

    def test_explicit_timestamp_is_preserved(self):
        msg = WebSocketMessage(type="x", event="y", data={}, timestamp="2026-01-01T00:00:00Z")
        assert msg.timestamp == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestConnectDisconnect:
    async def test_connect_registers_connection_in_namespace(self):
        manager = WebSocketManager()
        ws = _make_ws()
        await manager.connect(ws, namespace="task.abc")
        assert ws in manager.active_connections["task.abc"]

    async def test_connect_increments_connection_count(self):
        manager = WebSocketManager()
        ws = _make_ws()
        await manager.connect(ws, namespace="global")
        assert manager.connection_count == 1

    async def test_connect_multiple_to_same_namespace(self):
        manager = WebSocketManager()
        ws1, ws2 = _make_ws(), _make_ws()
        await manager.connect(ws1, namespace="ns")
        await manager.connect(ws2, namespace="ns")
        assert manager.get_namespace_count("ns") == 2
        assert manager.connection_count == 2

    async def test_disconnect_removes_connection(self):
        manager = WebSocketManager()
        ws = _make_ws()
        await manager.connect(ws, namespace="ns")
        await manager.disconnect(ws, namespace="ns")
        assert ws not in manager.active_connections.get("ns", set())

    async def test_disconnect_decrements_count(self):
        manager = WebSocketManager()
        ws = _make_ws()
        await manager.connect(ws, namespace="ns")
        await manager.disconnect(ws, namespace="ns")
        assert manager.connection_count == 0

    async def test_disconnect_unknown_namespace_does_not_raise(self):
        manager = WebSocketManager()
        ws = _make_ws()
        # Should not raise even if namespace doesn't exist
        await manager.disconnect(ws, namespace="nonexistent")

    async def test_default_namespace_is_global(self):
        manager = WebSocketManager()
        ws = _make_ws()
        await manager.connect(ws)  # no namespace param
        assert ws in manager.active_connections["global"]


# ---------------------------------------------------------------------------
# broadcast_to_namespace
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBroadcastToNamespace:
    async def test_message_sent_to_all_namespace_connections(self):
        manager = WebSocketManager()
        ws1, ws2 = _make_ws(), _make_ws()
        await manager.connect(ws1, namespace="ns")
        await manager.connect(ws2, namespace="ns")
        await manager.broadcast_to_namespace("ns", "progress", "task.done", {"id": "t-1"})
        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()

    async def test_broadcast_payload_is_valid_json(self):
        manager = WebSocketManager()
        ws = _make_ws()
        await manager.connect(ws, namespace="ns")
        await manager.broadcast_to_namespace("ns", "test_type", "test_event", {"key": "value"})
        raw = ws.send_text.call_args[0][0]
        payload = json.loads(raw)
        assert payload["type"] == "test_type"
        assert payload["event"] == "test_event"
        assert payload["data"]["key"] == "value"

    async def test_disconnected_client_does_not_raise(self):
        """A failing send_text should not propagate an exception to the caller."""
        manager = WebSocketManager()
        good_ws = _make_ws()
        bad_ws = _make_ws(fail=True)
        await manager.connect(good_ws, namespace="ns")
        await manager.connect(bad_ws, namespace="ns")
        # Should complete without raising
        await manager.broadcast_to_namespace("ns", "x", "y", {})
        # Good client still received the message
        good_ws.send_text.assert_called_once()

    async def test_failed_client_is_cleaned_up(self):
        """Failed connections should be removed from the registry."""
        manager = WebSocketManager()
        bad_ws = _make_ws(fail=True)
        await manager.connect(bad_ws, namespace="ns")
        await manager.broadcast_to_namespace("ns", "x", "y", {})
        # Should have been removed after the failure
        assert bad_ws not in manager.active_connections.get("ns", set())

    async def test_broadcast_to_empty_namespace_does_not_raise(self):
        manager = WebSocketManager()
        await manager.broadcast_to_namespace("empty", "x", "y", {})  # no connections

    async def test_broadcast_to_nonexistent_namespace_does_not_raise(self):
        manager = WebSocketManager()
        await manager.broadcast_to_namespace("ghost", "x", "y", {})


# ---------------------------------------------------------------------------
# broadcast_to_all
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBroadcastToAll:
    async def test_all_namespaces_receive_message(self):
        manager = WebSocketManager()
        ws1, ws2 = _make_ws(), _make_ws()
        await manager.connect(ws1, namespace="ns1")
        await manager.connect(ws2, namespace="ns2")
        await manager.broadcast_to_all("notif", "alert", {"msg": "hello"})
        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()


# ---------------------------------------------------------------------------
# send_task_progress / send_workflow_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestHighLevelBroadcasters:
    async def test_send_task_progress_reaches_task_namespace(self):
        manager = WebSocketManager()
        ws = _make_ws()
        await manager.connect(ws, namespace="task.task-999")
        await manager.send_task_progress("task-999", {"stage": "drafting", "pct": 50})
        ws.send_text.assert_called_once()
        payload = json.loads(ws.send_text.call_args[0][0])
        assert payload["data"]["taskId"] == "task-999"
        assert payload["data"]["stage"] == "drafting"

    async def test_send_task_progress_does_not_reach_different_task(self):
        manager = WebSocketManager()
        ws = _make_ws()
        await manager.connect(ws, namespace="task.other-task")
        await manager.send_task_progress("task-999", {"pct": 50})
        ws.send_text.assert_not_called()

    async def test_send_workflow_status_reaches_workflow_namespace(self):
        manager = WebSocketManager()
        ws = _make_ws()
        await manager.connect(ws, namespace="workflow.wf-1")
        await manager.send_workflow_status("wf-1", {"status": "completed"})
        ws.send_text.assert_called_once()
        payload = json.loads(ws.send_text.call_args[0][0])
        assert payload["data"]["workflowId"] == "wf-1"


# ---------------------------------------------------------------------------
# get_stats / get_connection_count / get_namespace_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestStats:
    async def test_get_stats_returns_connection_counts(self):
        manager = WebSocketManager()
        ws = _make_ws()
        await manager.connect(ws, namespace="ns")
        stats = await manager.get_stats()
        assert stats["total_connections"] == 1
        assert stats["namespaces"]["ns"] == 1

    async def test_get_stats_excludes_empty_namespaces(self):
        manager = WebSocketManager()
        ws = _make_ws()
        await manager.connect(ws, namespace="ns")
        await manager.disconnect(ws, namespace="ns")
        stats = await manager.get_stats()
        assert "ns" not in stats["namespaces"]

    async def test_get_connection_count_returns_total(self):
        manager = WebSocketManager()
        ws1, ws2 = _make_ws(), _make_ws()
        await manager.connect(ws1, namespace="ns1")
        await manager.connect(ws2, namespace="ns2")
        assert manager.get_connection_count() == 2

    async def test_get_namespace_count_returns_count(self):
        manager = WebSocketManager()
        ws1, ws2 = _make_ws(), _make_ws()
        await manager.connect(ws1, namespace="ns")
        await manager.connect(ws2, namespace="ns")
        assert manager.get_namespace_count("ns") == 2

    async def test_get_namespace_count_returns_zero_for_unknown(self):
        manager = WebSocketManager()
        assert manager.get_namespace_count("ghost") == 0
