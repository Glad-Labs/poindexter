"""
Unit tests for the progress_broadcaster service (stubbed no-op version).

Verifies functions are importable and handle all input types without error.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

import services.progress_broadcaster as broadcaster_module
from services.progress_broadcaster import broadcast_progress, broadcast_workflow_progress

# ---------------------------------------------------------------------------
# broadcast_progress
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBroadcastProgress:
    async def test_dict_progress_is_noop(self):
        await broadcast_progress("task-1", {"step": 3, "total": 10})

    async def test_none_progress_is_noop(self):
        await broadcast_progress("task-2", None)

    async def test_object_progress_is_noop(self):
        obj = MagicMock()
        obj.to_dict.return_value = {"step": 1}
        await broadcast_progress("task-3", obj)

    async def test_returns_none_explicitly(self):
        result = await broadcast_progress("task-r", {"step": 1})
        assert result is None

    async def test_none_progress_skips_logger(self):
        with patch.object(broadcaster_module, "logger") as mock_logger:
            await broadcast_progress("task-skip", None)
        mock_logger.debug.assert_not_called()

    async def test_logs_task_id_when_progress_present(self):
        with patch.object(broadcaster_module, "logger") as mock_logger:
            await broadcast_progress("task-abc", {"any": "payload"})
        mock_logger.debug.assert_called_once()
        args, _ = mock_logger.debug.call_args
        assert "task-abc" in args

    async def test_empty_dict_progress_still_logs(self):
        with patch.object(broadcaster_module, "logger") as mock_logger:
            await broadcast_progress("task-empty", {})
        mock_logger.debug.assert_called_once()

    async def test_empty_string_task_id_does_not_crash(self):
        await broadcast_progress("", {"step": 0})

    async def test_does_not_invoke_methods_on_progress_object(self):
        obj = MagicMock()
        await broadcast_progress("task-no-introspect", obj)
        assert obj.method_calls == []

    async def test_concurrent_calls_all_return_none(self):
        results = await asyncio.gather(
            *(broadcast_progress(f"task-{i}", {"i": i}) for i in range(20))
        )
        assert results == [None] * 20


# ---------------------------------------------------------------------------
# broadcast_workflow_progress
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBroadcastWorkflowProgress:
    async def test_dict_progress_is_noop(self):
        await broadcast_workflow_progress("exec-1", {"phase": "research"})

    async def test_none_progress_is_noop(self):
        await broadcast_workflow_progress("exec-2", None)

    async def test_returns_none_explicitly(self):
        result = await broadcast_workflow_progress("exec-r", {"phase": "draft"})
        assert result is None

    async def test_none_progress_still_logs(self):
        # Asymmetry vs broadcast_progress: this function logs unconditionally,
        # even when progress is None. Pin the current behavior so any future
        # refactor that aligns the two stubs is a deliberate, reviewed change.
        with patch.object(broadcaster_module, "logger") as mock_logger:
            await broadcast_workflow_progress("exec-none", None)
        mock_logger.debug.assert_called_once()

    async def test_logs_execution_id_in_message_args(self):
        with patch.object(broadcaster_module, "logger") as mock_logger:
            await broadcast_workflow_progress("exec-xyz", {"phase": "qa"})
        args, _ = mock_logger.debug.call_args
        assert "exec-xyz" in args

    @pytest.mark.parametrize(
        "exotic_progress",
        [[], [1, 2, 3], 0, "", "string-payload", 3.14, True, False],
    )
    async def test_handles_exotic_progress_types(self, exotic_progress):
        await broadcast_workflow_progress("exec-exotic", exotic_progress)
