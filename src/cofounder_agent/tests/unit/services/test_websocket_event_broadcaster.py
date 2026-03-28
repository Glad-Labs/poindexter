"""
Unit tests for services.websocket_event_broadcaster

Tests cover:
- WebSocketEventBroadcaster.broadcast_task_progress: calls websocket_manager correctly
- WebSocketEventBroadcaster.broadcast_workflow_status: correct data shape
- WebSocketEventBroadcaster.broadcast_analytics_update: only includes non-None fields
- WebSocketEventBroadcaster.broadcast_notification: correct shape
- Module-level convenience functions delegate to class methods
- emit_task_progress_sync: schedules a task when a loop is running, falls back gracefully
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.websocket_event_broadcaster as broadcaster_mod
from services.websocket_event_broadcaster import (
    WebSocketEventBroadcaster,
    emit_analytics_update,
    emit_notification,
    emit_task_progress,
    emit_task_progress_sync,
    emit_workflow_status,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_websocket_manager(monkeypatch):
    """Replace websocket_manager with an async mock for all tests."""
    mgr = MagicMock()
    mgr.send_task_progress = AsyncMock()
    mgr.send_workflow_status = AsyncMock()
    mgr.send_analytics_update = AsyncMock()
    mgr.send_notification = AsyncMock()
    monkeypatch.setattr(broadcaster_mod, "websocket_manager", mgr)
    return mgr


# ---------------------------------------------------------------------------
# broadcast_task_progress
# ---------------------------------------------------------------------------


class TestBroadcastTaskProgress:
    @pytest.mark.asyncio
    async def test_calls_send_task_progress(self, mock_websocket_manager):
        await WebSocketEventBroadcaster.broadcast_task_progress(
            task_id="task-1",
            status="RUNNING",
            progress=50,
            current_step="Generating content",
            total_steps=5,
            completed_steps=2,
            message="In progress",
        )
        mock_websocket_manager.send_task_progress.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_progress_data_shape(self, mock_websocket_manager):
        await WebSocketEventBroadcaster.broadcast_task_progress(
            task_id="task-1",
            status="RUNNING",
            progress=75,
            current_step="step",
            total_steps=4,
            completed_steps=3,
            message="msg",
            elapsed_time=30.5,
            estimated_time_remaining=10.0,
            error=None,
        )
        call_kwargs = mock_websocket_manager.send_task_progress.call_args[1]
        data = call_kwargs["progress_data"]
        assert data["status"] == "RUNNING"
        assert data["progress"] == 75
        assert data["currentStep"] == "step"
        assert data["elapsedTime"] == 30.5
        assert data["error"] is None

    @pytest.mark.asyncio
    async def test_task_id_forwarded(self, mock_websocket_manager):
        await WebSocketEventBroadcaster.broadcast_task_progress(
            task_id="my-task",
            status="COMPLETED",
            progress=100,
            current_step="done",
            total_steps=1,
            completed_steps=1,
            message="done",
        )
        call_kwargs = mock_websocket_manager.send_task_progress.call_args[1]
        assert call_kwargs["task_id"] == "my-task"


# ---------------------------------------------------------------------------
# broadcast_workflow_status
# ---------------------------------------------------------------------------


class TestBroadcastWorkflowStatus:
    @pytest.mark.asyncio
    async def test_calls_send_workflow_status(self, mock_websocket_manager):
        await WebSocketEventBroadcaster.broadcast_workflow_status(
            workflow_id="wf-1",
            status="COMPLETED",
        )
        mock_websocket_manager.send_workflow_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_data_shape(self, mock_websocket_manager):
        await WebSocketEventBroadcaster.broadcast_workflow_status(
            workflow_id="wf-1",
            status="FAILED",
            duration=123.4,
            task_count=3,
            task_results={"task-1": "ok"},
        )
        call_kwargs = mock_websocket_manager.send_workflow_status.call_args[1]
        data = call_kwargs["status_data"]
        assert data["status"] == "FAILED"
        assert data["duration"] == 123.4
        assert data["taskCount"] == 3
        assert data["taskResults"] == {"task-1": "ok"}

    @pytest.mark.asyncio
    async def test_none_task_results_defaults_to_empty_dict(self, mock_websocket_manager):
        await WebSocketEventBroadcaster.broadcast_workflow_status(
            workflow_id="wf-1", status="RUNNING"
        )
        data = mock_websocket_manager.send_workflow_status.call_args[1]["status_data"]
        assert data["taskResults"] == {}


# ---------------------------------------------------------------------------
# broadcast_analytics_update
# ---------------------------------------------------------------------------


class TestBroadcastAnalyticsUpdate:
    @pytest.mark.asyncio
    async def test_none_values_excluded_from_data(self, mock_websocket_manager):
        await WebSocketEventBroadcaster.broadcast_analytics_update(
            total_tasks=100,
            completed_today=None,  # Should NOT appear
        )
        data = mock_websocket_manager.send_analytics_update.call_args[0][0]
        assert "totalTasks" in data
        assert "completedToday" not in data

    @pytest.mark.asyncio
    async def test_all_fields_included_when_provided(self, mock_websocket_manager):
        await WebSocketEventBroadcaster.broadcast_analytics_update(
            total_tasks=50,
            completed_today=5,
            average_completion_time=120.0,
            cost_today=2.5,
            success_rate=95.0,
            failed_today=2,
            running_now=3,
        )
        data = mock_websocket_manager.send_analytics_update.call_args[0][0]
        assert data["totalTasks"] == 50
        assert data["completedToday"] == 5
        assert data["averageCompletionTime"] == 120.0
        assert data["costToday"] == 2.5
        assert data["successRate"] == 95.0
        assert data["failedToday"] == 2
        assert data["runningNow"] == 3

    @pytest.mark.asyncio
    async def test_empty_call_sends_empty_dict(self, mock_websocket_manager):
        await WebSocketEventBroadcaster.broadcast_analytics_update()
        data = mock_websocket_manager.send_analytics_update.call_args[0][0]
        assert data == {}


# ---------------------------------------------------------------------------
# broadcast_notification
# ---------------------------------------------------------------------------


class TestBroadcastNotification:
    @pytest.mark.asyncio
    async def test_calls_send_notification(self, mock_websocket_manager):
        await WebSocketEventBroadcaster.broadcast_notification(
            type="success",
            title="Done",
            message="Task completed",
        )
        mock_websocket_manager.send_notification.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notification_shape(self, mock_websocket_manager):
        await WebSocketEventBroadcaster.broadcast_notification(
            type="error",
            title="Failed",
            message="Something went wrong",
            duration=8000,
        )
        data = mock_websocket_manager.send_notification.call_args[0][0]
        assert data["type"] == "error"
        assert data["title"] == "Failed"
        assert data["message"] == "Something went wrong"
        assert data["duration"] == 8000

    @pytest.mark.asyncio
    async def test_default_duration_is_5000(self, mock_websocket_manager):
        await WebSocketEventBroadcaster.broadcast_notification(
            type="info", title="Info", message="msg"
        )
        data = mock_websocket_manager.send_notification.call_args[0][0]
        assert data["duration"] == 5000


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:
    @pytest.mark.asyncio
    async def test_emit_task_progress_delegates(self, mock_websocket_manager):
        await emit_task_progress(
            task_id="t-1",
            status="RUNNING",
            progress=10,
            current_step="s",
            total_steps=3,
            completed_steps=1,
            message="m",
        )
        mock_websocket_manager.send_task_progress.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emit_workflow_status_delegates(self, mock_websocket_manager):
        await emit_workflow_status(workflow_id="wf-2", status="COMPLETED")
        mock_websocket_manager.send_workflow_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emit_analytics_update_delegates(self, mock_websocket_manager):
        await emit_analytics_update(total_tasks=10)
        mock_websocket_manager.send_analytics_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emit_notification_delegates(self, mock_websocket_manager):
        await emit_notification(type="info", title="Hi", message="hello")
        mock_websocket_manager.send_notification.assert_awaited_once()


# ---------------------------------------------------------------------------
# emit_task_progress_sync
# ---------------------------------------------------------------------------


class TestEmitTaskProgressSync:
    @pytest.mark.asyncio
    async def test_creates_task_when_loop_running(self, mock_websocket_manager):
        """In an async test, the event loop is already running — should use create_task."""
        with patch("asyncio.create_task") as mock_create_task:
            emit_task_progress_sync(
                task_id="t-1",
                status="RUNNING",
                progress=0,
                current_step="s",
                total_steps=1,
                completed_steps=0,
                message="m",
            )
            mock_create_task.assert_called_once()

    def test_no_loop_does_not_raise(self, mock_websocket_manager):
        """Outside any async context, should not propagate RuntimeError."""
        # This test runs in a plain sync context — emit_task_progress_sync
        # may silently fail to emit but must not raise.
        with patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
            with patch("asyncio.run", side_effect=RuntimeError("no loop")):
                emit_task_progress_sync(
                    task_id="t-1",
                    status="RUNNING",
                    progress=0,
                    current_step="s",
                    total_steps=1,
                    completed_steps=0,
                    message="m",
                )
