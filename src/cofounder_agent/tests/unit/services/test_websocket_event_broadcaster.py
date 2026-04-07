"""
Unit tests for services.websocket_event_broadcaster (stubbed no-op version).

Verifies all public functions are importable and execute without error.
"""

import pytest

from services.websocket_event_broadcaster import (
    WebSocketEventBroadcaster,
    emit_analytics_update,
    emit_notification,
    emit_task_progress,
    emit_task_progress_sync,
    emit_workflow_status,
)


# ---------------------------------------------------------------------------
# Class methods
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBroadcasterStubs:
    async def test_broadcast_task_progress_is_noop(self):
        await WebSocketEventBroadcaster.broadcast_task_progress(task_id="t-1", status="RUNNING")

    async def test_broadcast_workflow_status_is_noop(self):
        await WebSocketEventBroadcaster.broadcast_workflow_status(
            workflow_id="wf-1", status="COMPLETED"
        )

    async def test_broadcast_analytics_update_is_noop(self):
        await WebSocketEventBroadcaster.broadcast_analytics_update(total_tasks=100)

    async def test_broadcast_notification_is_noop(self):
        await WebSocketEventBroadcaster.broadcast_notification(
            type="info", title="Hi", message="hello"
        )


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestConvenienceFunctions:
    async def test_emit_task_progress_is_noop(self):
        await emit_task_progress(task_id="t-1", status="RUNNING", progress=50)

    async def test_emit_workflow_status_is_noop(self):
        await emit_workflow_status(workflow_id="wf-1", status="COMPLETED")

    async def test_emit_analytics_update_is_noop(self):
        await emit_analytics_update(total_tasks=10)

    async def test_emit_notification_is_noop(self):
        await emit_notification(type="info", title="Hi", message="hello")


# ---------------------------------------------------------------------------
# Sync wrapper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmitTaskProgressSync:
    def test_sync_wrapper_does_not_raise(self):
        emit_task_progress_sync(task_id="t-1", status="RUNNING", progress=0)
