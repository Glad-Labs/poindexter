"""
Unit tests for services.workflow_event_emitter

Tests cover:
- WorkflowEventEmitter construction and handler registration
- emit_* methods delegate to progress_service and broadcast_function
- _broadcast_progress handles async and sync broadcast functions
- _broadcast_progress swallows broadcast exceptions
- No-op behaviour when progress_service / broadcast_function not set
- Global singleton get_workflow_event_emitter
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.workflow_event_emitter import (
    WorkflowEventEmitter,
    get_workflow_event_emitter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def emitter() -> WorkflowEventEmitter:
    return WorkflowEventEmitter()


@pytest.fixture()
def mock_progress():
    """Fake WorkflowProgressService — all methods return a sentinel object."""
    svc = MagicMock()
    sentinel = MagicMock()
    svc.start_execution.return_value = sentinel
    svc.start_phase.return_value = sentinel
    svc.complete_phase.return_value = sentinel
    svc.fail_phase.return_value = sentinel
    svc.mark_complete.return_value = sentinel
    svc.mark_failed.return_value = sentinel
    return svc


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_initial_state(self, emitter):
        assert emitter.handlers == {}
        assert emitter._progress_service is None
        assert emitter._broadcast_function is None

    def test_set_progress_service(self, emitter, mock_progress):
        emitter.set_progress_service(mock_progress)
        assert emitter._progress_service is mock_progress

    def test_set_broadcast_function(self, emitter):
        fn = lambda eid, p: None
        emitter.set_broadcast_function(fn)
        assert emitter._broadcast_function is fn

    def test_register_handler(self, emitter):
        handler = MagicMock()
        emitter.register_handler("phase_started", handler)
        assert emitter.handlers["phase_started"] is handler


# ---------------------------------------------------------------------------
# emit_execution_started
# ---------------------------------------------------------------------------


class TestEmitExecutionStarted:
    @pytest.mark.asyncio
    async def test_calls_start_execution_and_broadcast(self, emitter, mock_progress):
        broadcast = AsyncMock()
        emitter.set_progress_service(mock_progress)
        emitter.set_broadcast_function(broadcast)

        await emitter.emit_execution_started("ex-1", total_phases=3)

        mock_progress.start_execution.assert_called_once_with(
            "ex-1", message="Starting workflow execution with 3 phases..."
        )
        broadcast.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_op_without_progress_service(self, emitter):
        broadcast = AsyncMock()
        emitter.set_broadcast_function(broadcast)
        await emitter.emit_execution_started("ex-1", total_phases=2)
        broadcast.assert_not_awaited()


# ---------------------------------------------------------------------------
# emit_phase_started
# ---------------------------------------------------------------------------


class TestEmitPhaseStarted:
    @pytest.mark.asyncio
    async def test_calls_start_phase_and_broadcast(self, emitter, mock_progress):
        broadcast = AsyncMock()
        emitter.set_progress_service(mock_progress)
        emitter.set_broadcast_function(broadcast)

        await emitter.emit_phase_started("ex-1", 0, "research")

        mock_progress.start_phase.assert_called_once_with("ex-1", 0, "research")
        broadcast.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_op_without_progress_service(self, emitter):
        broadcast = AsyncMock()
        emitter.set_broadcast_function(broadcast)
        await emitter.emit_phase_started("ex-1", 0, "research")
        broadcast.assert_not_awaited()


# ---------------------------------------------------------------------------
# emit_phase_completed
# ---------------------------------------------------------------------------


class TestEmitPhaseCompleted:
    @pytest.mark.asyncio
    async def test_calls_complete_phase(self, emitter, mock_progress):
        broadcast = AsyncMock()
        emitter.set_progress_service(mock_progress)
        emitter.set_broadcast_function(broadcast)

        await emitter.emit_phase_completed("ex-1", "creative", {"key": "val"}, 300.0)

        mock_progress.complete_phase.assert_called_once_with(
            "ex-1", "creative", {"key": "val"}, 300.0
        )

    @pytest.mark.asyncio
    async def test_no_op_without_progress_service(self, emitter):
        broadcast = AsyncMock()
        emitter.set_broadcast_function(broadcast)
        await emitter.emit_phase_completed("ex-1", "creative")
        broadcast.assert_not_awaited()


# ---------------------------------------------------------------------------
# emit_phase_failed
# ---------------------------------------------------------------------------


class TestEmitPhaseFailed:
    @pytest.mark.asyncio
    async def test_calls_fail_phase(self, emitter, mock_progress):
        broadcast = AsyncMock()
        emitter.set_progress_service(mock_progress)
        emitter.set_broadcast_function(broadcast)

        await emitter.emit_phase_failed("ex-1", "research", "timeout")

        mock_progress.fail_phase.assert_called_once_with("ex-1", "research", "timeout")

    @pytest.mark.asyncio
    async def test_no_op_without_progress_service(self, emitter):
        broadcast = AsyncMock()
        emitter.set_broadcast_function(broadcast)
        await emitter.emit_phase_failed("ex-1", "research", "err")
        broadcast.assert_not_awaited()


# ---------------------------------------------------------------------------
# emit_execution_completed
# ---------------------------------------------------------------------------


class TestEmitExecutionCompleted:
    @pytest.mark.asyncio
    async def test_calls_mark_complete(self, emitter, mock_progress):
        broadcast = AsyncMock()
        emitter.set_progress_service(mock_progress)
        emitter.set_broadcast_function(broadcast)

        await emitter.emit_execution_completed("ex-1", {"result": "ok"}, 5000.0)

        mock_progress.mark_complete.assert_called_once_with(
            "ex-1",
            {"result": "ok"},
            5000.0,
            message="Workflow execution completed successfully",
        )


# ---------------------------------------------------------------------------
# emit_execution_failed
# ---------------------------------------------------------------------------


class TestEmitExecutionFailed:
    @pytest.mark.asyncio
    async def test_calls_mark_failed(self, emitter, mock_progress):
        broadcast = AsyncMock()
        emitter.set_progress_service(mock_progress)
        emitter.set_broadcast_function(broadcast)

        await emitter.emit_execution_failed("ex-1", "crash", failed_phase="qa")

        mock_progress.mark_failed.assert_called_once_with("ex-1", "crash", "qa")


# ---------------------------------------------------------------------------
# _broadcast_progress — sync vs async, exception swallowing
# ---------------------------------------------------------------------------


class TestBroadcastProgress:
    @pytest.mark.asyncio
    async def test_async_broadcast_is_awaited(self, emitter, mock_progress):
        calls = []

        async def async_broadcast(eid, p):
            calls.append(eid)

        emitter.set_progress_service(mock_progress)
        emitter.set_broadcast_function(async_broadcast)
        await emitter.emit_execution_started("ex-1", 2)
        assert "ex-1" in calls

    @pytest.mark.asyncio
    async def test_sync_broadcast_is_called(self, emitter, mock_progress):
        calls = []

        def sync_broadcast(eid, p):
            calls.append(eid)

        emitter.set_progress_service(mock_progress)
        emitter.set_broadcast_function(sync_broadcast)
        await emitter.emit_execution_started("ex-1", 2)
        assert "ex-1" in calls

    @pytest.mark.asyncio
    async def test_broadcast_exception_swallowed(self, emitter, mock_progress):
        async def bad_broadcast(eid, p):
            raise RuntimeError("ws down")

        emitter.set_progress_service(mock_progress)
        emitter.set_broadcast_function(bad_broadcast)
        # Should not raise
        await emitter.emit_execution_started("ex-1", 2)

    @pytest.mark.asyncio
    async def test_no_op_when_no_broadcast_function(self, emitter, mock_progress):
        emitter.set_progress_service(mock_progress)
        # No broadcast function set — should not raise
        await emitter._broadcast_progress("ex-1", MagicMock())


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------


class TestGlobalSingleton:
    def test_returns_workflow_event_emitter(self):
        em = get_workflow_event_emitter()
        assert isinstance(em, WorkflowEventEmitter)

    def test_same_instance_returned_twice(self):
        a = get_workflow_event_emitter()
        b = get_workflow_event_emitter()
        assert a is b
