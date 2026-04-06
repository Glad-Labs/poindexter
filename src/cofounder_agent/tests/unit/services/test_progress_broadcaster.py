"""
Unit tests for the progress_broadcaster service.

Tests broadcast_progress and broadcast_workflow_progress functions.
All tests are async, using pytest-asyncio (asyncio mode=auto in pyproject.toml).
The websocket_manager singleton is patched so no real connections are needed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MODULE = "services.progress_broadcaster"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_progress_dict() -> dict:
    """Return a plain dict that looks like progress data."""
    return {"step": 3, "total": 10, "message": "Generating content"}


def _make_progress_obj(data: dict | None = None) -> MagicMock:
    """Return a mock object with a to_dict() method."""
    obj = MagicMock()
    obj.to_dict.return_value = data or _make_progress_dict()
    return obj


# ---------------------------------------------------------------------------
# broadcast_progress
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBroadcastProgress:
    async def test_sends_dict_progress(self):
        """When progress is already a dict, it is forwarded directly."""
        mock_manager = AsyncMock()
        with patch(f"{MODULE}.websocket_manager", mock_manager):
            from services.progress_broadcaster import broadcast_progress

            data = _make_progress_dict()
            await broadcast_progress("task-1", data)

            mock_manager.send_task_progress.assert_awaited_once_with("task-1", data)

    async def test_sends_object_progress_via_to_dict(self):
        """When progress has a to_dict method, it is called before sending."""
        mock_manager = AsyncMock()
        with patch(f"{MODULE}.websocket_manager", mock_manager):
            from services.progress_broadcaster import broadcast_progress

            obj = _make_progress_obj()
            await broadcast_progress("task-2", obj)

            obj.to_dict.assert_called_once()
            mock_manager.send_task_progress.assert_awaited_once_with(
                "task-2", obj.to_dict.return_value
            )

    async def test_none_progress_is_noop(self):
        """Passing None as progress should not call the manager at all."""
        mock_manager = AsyncMock()
        with patch(f"{MODULE}.websocket_manager", mock_manager):
            from services.progress_broadcaster import broadcast_progress

            await broadcast_progress("task-3", None)

            mock_manager.send_task_progress.assert_not_awaited()

    async def test_manager_exception_is_swallowed(self):
        """If send_task_progress raises, broadcast_progress logs but does not propagate."""
        mock_manager = AsyncMock()
        mock_manager.send_task_progress.side_effect = RuntimeError("ws broken")
        with patch(f"{MODULE}.websocket_manager", mock_manager):
            from services.progress_broadcaster import broadcast_progress

            # Should not raise
            await broadcast_progress("task-4", {"step": 1})

    async def test_to_dict_exception_is_swallowed(self):
        """If the progress object's to_dict raises, the error is caught."""
        mock_manager = AsyncMock()
        bad_obj = MagicMock()
        bad_obj.to_dict.side_effect = ValueError("bad serialization")
        # isinstance(bad_obj, dict) is False, so to_dict will be called
        with patch(f"{MODULE}.websocket_manager", mock_manager):
            from services.progress_broadcaster import broadcast_progress

            await broadcast_progress("task-5", bad_obj)

            mock_manager.send_task_progress.assert_not_awaited()


# ---------------------------------------------------------------------------
# broadcast_workflow_progress
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBroadcastWorkflowProgress:
    async def test_sends_dict_progress(self):
        """When progress is already a dict, it is forwarded directly."""
        mock_manager = AsyncMock()
        with patch(f"{MODULE}.websocket_manager", mock_manager):
            from services.progress_broadcaster import broadcast_workflow_progress

            data = {"phase": "research", "pct": 50}
            await broadcast_workflow_progress("exec-1", data)

            mock_manager.send_workflow_status.assert_awaited_once_with("exec-1", data)

    async def test_sends_object_progress_via_to_dict(self):
        """When progress has a to_dict method, it is called before sending."""
        mock_manager = AsyncMock()
        with patch(f"{MODULE}.websocket_manager", mock_manager):
            from services.progress_broadcaster import broadcast_workflow_progress

            obj = _make_progress_obj({"phase": "writing", "pct": 80})
            await broadcast_workflow_progress("exec-2", obj)

            obj.to_dict.assert_called_once()
            mock_manager.send_workflow_status.assert_awaited_once_with(
                "exec-2", obj.to_dict.return_value
            )

    async def test_manager_exception_is_swallowed(self):
        """If send_workflow_status raises, the error is logged but not propagated."""
        mock_manager = AsyncMock()
        mock_manager.send_workflow_status.side_effect = ConnectionError("gone")
        with patch(f"{MODULE}.websocket_manager", mock_manager):
            from services.progress_broadcaster import broadcast_workflow_progress

            await broadcast_workflow_progress("exec-3", {"phase": "done"})

    async def test_to_dict_exception_is_swallowed(self):
        """If the progress object's to_dict raises, the error is caught."""
        mock_manager = AsyncMock()
        bad_obj = MagicMock()
        bad_obj.to_dict.side_effect = TypeError("not serializable")
        with patch(f"{MODULE}.websocket_manager", mock_manager):
            from services.progress_broadcaster import broadcast_workflow_progress

            await broadcast_workflow_progress("exec-4", bad_obj)

            mock_manager.send_workflow_status.assert_not_awaited()

    async def test_none_progress_calls_manager(self):
        """Unlike broadcast_progress, workflow variant does NOT short-circuit on None.

        None is not a dict and has no to_dict, so it will raise in the try block
        and be caught — the manager is never called.
        """
        mock_manager = AsyncMock()
        with patch(f"{MODULE}.websocket_manager", mock_manager):
            from services.progress_broadcaster import broadcast_workflow_progress

            await broadcast_workflow_progress("exec-5", None)

            mock_manager.send_workflow_status.assert_not_awaited()
