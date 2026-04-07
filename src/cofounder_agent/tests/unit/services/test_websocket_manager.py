"""
Unit tests for WebSocketManager and WebSocketMessage (stubbed no-op versions).

Verifies the public API surface is importable and methods don't crash.
"""

import json
from datetime import datetime

import pytest

from services.websocket_manager import WebSocketManager, WebSocketMessage


# ---------------------------------------------------------------------------
# WebSocketMessage (dataclass kept for compat)
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
        datetime.fromisoformat(msg.timestamp.replace("Z", "+00:00"))

    def test_explicit_timestamp_is_preserved(self):
        msg = WebSocketMessage(type="x", event="y", data={}, timestamp="2026-01-01T00:00:00Z")
        assert msg.timestamp == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# WebSocketManager (stubbed no-ops)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestWebSocketManagerStub:
    async def test_connect_is_noop(self):
        manager = WebSocketManager()
        await manager.connect(object(), namespace="test")

    async def test_disconnect_is_noop(self):
        manager = WebSocketManager()
        await manager.disconnect(object(), namespace="test")

    async def test_broadcast_to_namespace_is_noop(self):
        manager = WebSocketManager()
        await manager.broadcast_to_namespace("ns", "type", "event", {"key": "val"})

    async def test_broadcast_to_all_is_noop(self):
        manager = WebSocketManager()
        await manager.broadcast_to_all("type", "event", {"key": "val"})

    async def test_send_task_progress_is_noop(self):
        manager = WebSocketManager()
        await manager.send_task_progress("task-1", {"stage": "drafting"})

    async def test_send_workflow_status_is_noop(self):
        manager = WebSocketManager()
        await manager.send_workflow_status("wf-1", {"status": "done"})

    async def test_send_analytics_update_is_noop(self):
        manager = WebSocketManager()
        await manager.send_analytics_update({"metric": 42})

    async def test_send_notification_is_noop(self):
        manager = WebSocketManager()
        await manager.send_notification({"type": "info"})

    def test_get_connection_count_returns_zero(self):
        manager = WebSocketManager()
        assert manager.get_connection_count() == 0

    def test_get_namespace_count_returns_zero(self):
        manager = WebSocketManager()
        assert manager.get_namespace_count("anything") == 0

    async def test_get_stats_returns_empty(self):
        manager = WebSocketManager()
        stats = await manager.get_stats()
        assert stats["total_connections"] == 0
        assert stats["namespaces"] == {}

    def test_active_connections_is_dict(self):
        """metrics_routes reads .active_connections directly."""
        manager = WebSocketManager()
        assert isinstance(manager.active_connections, dict)
