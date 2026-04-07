"""
Unit tests for the progress_broadcaster service (stubbed no-op version).

Verifies functions are importable and handle all input types without error.
"""

from unittest.mock import MagicMock

import pytest

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
