"""
Unit tests for routes/websocket_routes.py.

Tests cover:
- GET /api/ws/stats — websocket_stats (the only REST endpoint)
- ConnectionManager — connect, disconnect, broadcast, get_active_connections_count
- Helper functions — broadcast_progress, broadcast_approval_status,
  broadcast_workflow_progress, get_connection_manager
- _validate_ws_token — dev bypass and invalid-token paths
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from routes.websocket_routes import (
    ConnectionManager,
    broadcast_approval_status,
    broadcast_progress,
    broadcast_workflow_progress,
    connection_manager,
    get_connection_manager,
    websocket_router,
)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(websocket_router)
    return app


# ---------------------------------------------------------------------------
# GET /api/ws/stats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWebSocketStats:
    def test_returns_200(self):
        with patch(
            "routes.websocket_routes.websocket_manager"
        ) as mock_mgr:
            mock_mgr.get_stats = AsyncMock(
                return_value={"total_connections": 0, "namespaces": {}}
            )
            client = TestClient(_build_app())
            resp = client.get("/api/ws/stats")
        assert resp.status_code == 200

    def test_response_has_total_connections(self):
        with patch(
            "routes.websocket_routes.websocket_manager"
        ) as mock_mgr:
            mock_mgr.get_stats = AsyncMock(
                return_value={"total_connections": 3, "namespaces": {"global": 3}}
            )
            client = TestClient(_build_app())
            data = client.get("/api/ws/stats").json()
        assert "total_connections" in data

    def test_response_has_namespaces(self):
        with patch(
            "routes.websocket_routes.websocket_manager"
        ) as mock_mgr:
            mock_mgr.get_stats = AsyncMock(
                return_value={"total_connections": 5, "namespaces": {"global": 5}}
            )
            client = TestClient(_build_app())
            data = client.get("/api/ws/stats").json()
        assert "namespaces" in data

    def test_no_auth_required(self):
        """Stats endpoint has no auth guard."""
        with patch(
            "routes.websocket_routes.websocket_manager"
        ) as mock_mgr:
            mock_mgr.get_stats = AsyncMock(
                return_value={"total_connections": 0, "namespaces": {}}
            )
            client = TestClient(_build_app())
            resp = client.get("/api/ws/stats")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# ConnectionManager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConnectionManagerInit:
    def test_active_connections_starts_empty(self):
        mgr = ConnectionManager()
        assert mgr.active_connections == {}

    def test_two_instances_are_independent(self):
        """ConnectionManager is NOT a singleton — each instance owns its own dict."""
        a = ConnectionManager()
        b = ConnectionManager()
        assert a.active_connections is not b.active_connections


@pytest.mark.unit
class TestConnectionManagerConnect:
    @pytest.mark.asyncio
    async def test_accept_called_on_websocket(self):
        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect("task-1", ws)
        ws.accept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connection_registered_under_task_id(self):
        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect("task-1", ws)
        assert ws in mgr.active_connections["task-1"]

    @pytest.mark.asyncio
    async def test_multiple_connections_same_task(self):
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        await mgr.connect("task-2", ws1)
        await mgr.connect("task-2", ws2)
        assert len(mgr.active_connections["task-2"]) == 2

    @pytest.mark.asyncio
    async def test_multiple_tasks_isolated(self):
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        await mgr.connect("task-a", ws1)
        await mgr.connect("task-b", ws2)
        assert "task-a" in mgr.active_connections
        assert "task-b" in mgr.active_connections


@pytest.mark.unit
class TestConnectionManagerDisconnect:
    @pytest.mark.asyncio
    async def test_connection_removed_on_disconnect(self):
        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect("task-1", ws)
        await mgr.disconnect("task-1", ws)
        assert "task-1" not in mgr.active_connections

    @pytest.mark.asyncio
    async def test_task_key_removed_when_last_connection_leaves(self):
        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect("task-1", ws)
        await mgr.disconnect("task-1", ws)
        assert "task-1" not in mgr.active_connections

    @pytest.mark.asyncio
    async def test_other_connections_stay_when_one_disconnects(self):
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        await mgr.connect("task-1", ws1)
        await mgr.connect("task-1", ws2)
        await mgr.disconnect("task-1", ws1)
        assert ws2 in mgr.active_connections["task-1"]
        assert ws1 not in mgr.active_connections["task-1"]

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_task_is_noop(self):
        mgr = ConnectionManager()
        ws = AsyncMock()
        # Should not raise even when task_id is unknown
        await mgr.disconnect("unknown-task", ws)

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_ws_is_noop(self):
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        await mgr.connect("task-1", ws1)
        # ws2 was never connected — discard should be a no-op
        await mgr.disconnect("task-1", ws2)
        assert ws1 in mgr.active_connections["task-1"]


@pytest.mark.unit
class TestConnectionManagerBroadcast:
    @pytest.mark.asyncio
    async def test_message_sent_to_all_connections(self):
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        await mgr.connect("task-1", ws1)
        await mgr.connect("task-1", ws2)
        msg = {"type": "progress", "pct": 50}
        await mgr.broadcast("task-1", msg)
        ws1.send_json.assert_awaited_once_with(msg)
        ws2.send_json.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_broadcast_unknown_task_is_noop(self):
        mgr = ConnectionManager()
        # No connections for "missing-task" — should not raise
        await mgr.broadcast("missing-task", {"type": "ping"})

    @pytest.mark.asyncio
    async def test_failed_send_removes_connection(self):
        mgr = ConnectionManager()
        ws_good = AsyncMock()
        ws_bad = AsyncMock()
        ws_bad.send_json.side_effect = RuntimeError("socket closed")
        await mgr.connect("task-1", ws_good)
        await mgr.connect("task-1", ws_bad)
        await mgr.broadcast("task-1", {"type": "x"})
        # ws_good still connected; ws_bad cleaned up
        assert ws_good in mgr.active_connections.get("task-1", set())
        assert ws_bad not in mgr.active_connections.get("task-1", set())

    @pytest.mark.asyncio
    async def test_broadcasts_to_correct_task_only(self):
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        await mgr.connect("task-A", ws1)
        await mgr.connect("task-B", ws2)
        await mgr.broadcast("task-A", {"type": "x"})
        ws1.send_json.assert_awaited_once()
        ws2.send_json.assert_not_awaited()


@pytest.mark.unit
class TestConnectionManagerGetActiveConnectionsCount:
    @pytest.mark.asyncio
    async def test_count_zero_when_no_connections(self):
        mgr = ConnectionManager()
        assert mgr.get_active_connections_count("task-1") == 0

    @pytest.mark.asyncio
    async def test_count_reflects_connected_clients(self):
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        await mgr.connect("task-1", ws1)
        await mgr.connect("task-1", ws2)
        assert mgr.get_active_connections_count("task-1") == 2

    @pytest.mark.asyncio
    async def test_count_decrements_on_disconnect(self):
        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect("task-1", ws)
        assert mgr.get_active_connections_count("task-1") == 1
        await mgr.disconnect("task-1", ws)
        assert mgr.get_active_connections_count("task-1") == 0


# ---------------------------------------------------------------------------
# get_connection_manager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetConnectionManager:
    def test_returns_global_instance(self):
        assert get_connection_manager() is connection_manager

    def test_returns_connection_manager_type(self):
        assert isinstance(get_connection_manager(), ConnectionManager)


# ---------------------------------------------------------------------------
# broadcast_progress
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBroadcastProgress:
    @pytest.mark.asyncio
    async def test_delegates_to_connection_manager_broadcast(self):
        progress = MagicMock()
        progress.to_dict.return_value = {"pct": 42}
        with patch.object(connection_manager, "broadcast", new=AsyncMock()) as mock_bcast:
            await broadcast_progress("task-x", progress)
        mock_bcast.assert_awaited_once_with("task-x", {"type": "progress", "pct": 42})

    @pytest.mark.asyncio
    async def test_to_dict_is_called(self):
        progress = MagicMock()
        progress.to_dict.return_value = {}
        with patch.object(connection_manager, "broadcast", new=AsyncMock()):
            await broadcast_progress("task-x", progress)
        progress.to_dict.assert_called_once()


# ---------------------------------------------------------------------------
# broadcast_approval_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBroadcastApprovalStatus:
    @pytest.mark.asyncio
    async def test_message_type_is_approval_status(self):
        with patch.object(connection_manager, "broadcast", new=AsyncMock()) as mock_bcast:
            await broadcast_approval_status("task-1", "approved")
        sent = mock_bcast.call_args[0][1]
        assert sent["type"] == "approval_status"

    @pytest.mark.asyncio
    async def test_status_is_included(self):
        with patch.object(connection_manager, "broadcast", new=AsyncMock()) as mock_bcast:
            await broadcast_approval_status("task-1", "rejected")
        sent = mock_bcast.call_args[0][1]
        assert sent["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_task_id_is_included(self):
        with patch.object(connection_manager, "broadcast", new=AsyncMock()) as mock_bcast:
            await broadcast_approval_status("task-99", "pending_revision")
        sent = mock_bcast.call_args[0][1]
        assert sent["task_id"] == "task-99"

    @pytest.mark.asyncio
    async def test_details_merged_into_message(self):
        with patch.object(connection_manager, "broadcast", new=AsyncMock()) as mock_bcast:
            await broadcast_approval_status("task-1", "approved", details={"feedback": "LGTM"})
        sent = mock_bcast.call_args[0][1]
        assert sent["feedback"] == "LGTM"

    @pytest.mark.asyncio
    async def test_no_details_does_not_raise(self):
        with patch.object(connection_manager, "broadcast", new=AsyncMock()):
            await broadcast_approval_status("task-1", "approved")


# ---------------------------------------------------------------------------
# broadcast_workflow_progress
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBroadcastWorkflowProgress:
    @pytest.mark.asyncio
    async def test_dict_progress_sent_directly(self):
        with patch.object(connection_manager, "broadcast", new=AsyncMock()) as mock_bcast:
            await broadcast_workflow_progress("exec-1", {"pct": 10})
        sent = mock_bcast.call_args[0][1]
        assert sent["type"] == "progress"
        assert sent["pct"] == 10

    @pytest.mark.asyncio
    async def test_object_progress_calls_to_dict(self):
        progress = MagicMock()
        progress.to_dict.return_value = {"pct": 75}
        with patch.object(connection_manager, "broadcast", new=AsyncMock()) as mock_bcast:
            await broadcast_workflow_progress("exec-2", progress)
        sent = mock_bcast.call_args[0][1]
        assert sent["type"] == "progress"
        assert sent["pct"] == 75

    @pytest.mark.asyncio
    async def test_execution_id_used_as_broadcast_key(self):
        with patch.object(connection_manager, "broadcast", new=AsyncMock()) as mock_bcast:
            await broadcast_workflow_progress("exec-abc", {"pct": 0})
        assert mock_bcast.call_args[0][0] == "exec-abc"


# ---------------------------------------------------------------------------
# _validate_ws_token (tested via logic — not FastAPI endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateWsToken:
    @pytest.mark.asyncio
    async def test_dev_token_accepted_in_development_mode(self):
        from routes.websocket_routes import _validate_ws_token

        ws = AsyncMock()
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "true"}):
            result = await _validate_ws_token(ws, "dev-token")
        assert result is True
        ws.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dev_token_rejected_in_production_mode(self):
        from routes.websocket_routes import _validate_ws_token

        ws = AsyncMock()
        mock_validator = MagicMock()
        mock_validator.verify_token.return_value = None  # invalid
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "false"}), \
             patch("routes.websocket_routes.JWTTokenValidator", mock_validator, create=True):
            # Import patches the local import inside the function
            with patch("services.token_validator.JWTTokenValidator") as patched_validator:
                patched_validator.verify_token.return_value = None
                result = await _validate_ws_token(ws, "dev-token")
        # Should close — returns False
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_during_validation_closes_connection(self):
        from routes.websocket_routes import _validate_ws_token

        ws = AsyncMock()
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "false"}):
            with patch("services.token_validator.JWTTokenValidator") as mock_cls:
                mock_cls.verify_token.side_effect = RuntimeError("DB down")
                result = await _validate_ws_token(ws, "some-token")
        assert result is False
        ws.close.assert_awaited_once_with(code=1008, reason="Invalid token")

    @pytest.mark.asyncio
    async def test_valid_claims_returns_true(self):
        from routes.websocket_routes import _validate_ws_token

        ws = AsyncMock()
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "false"}):
            with patch("services.token_validator.JWTTokenValidator") as mock_cls:
                mock_cls.verify_token.return_value = {"sub": "user-123"}
                result = await _validate_ws_token(ws, "valid.jwt.token")
        assert result is True
        ws.close.assert_not_awaited()
