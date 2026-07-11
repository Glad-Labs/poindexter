"""enqueue_image_rebuild — the service-layer enqueue behind rebuild-images.

The synchronous ImageRebuildService (in-process atom execution inside a
worker HTTP request) was replaced by the image_rebuild graph template; this
module now only validates the target draft and inserts the pipeline_tasks
row. The graph atoms themselves are covered by
tests/unit/modules/content/atoms/test_image_rebuild_atoms.py.
"""
from __future__ import annotations

import pytest

from modules.content.image_rebuild_service import enqueue_image_rebuild


class FakePool:
    def __init__(self, *, status: str | None = "awaiting_approval", topic="RAG echo chamber"):
        self._status = status
        self._topic = topic

    async def fetchval(self, sql, *args):
        if "SELECT status" in sql:
            return self._status
        if "SELECT topic" in sql:
            return self._topic
        raise AssertionError(f"unexpected fetchval: {sql}")


@pytest.mark.asyncio
async def test_enqueue_inserts_image_rebuild_task(monkeypatch):
    added: dict = {}

    class FakeTasksDb:
        def __init__(self, pool):
            added["pool"] = pool

        async def add_task(self, task_data):
            added["task_data"] = task_data
            return "rebuild-task-123"

    monkeypatch.setattr("services.tasks_db.TasksDatabase", FakeTasksDb)

    pool = FakePool()
    new_id = await enqueue_image_rebuild(pool, "draft-1", allow_stock=True)

    assert new_id == "rebuild-task-123"
    td = added["task_data"]
    assert td["template_slug"] == "image_rebuild"
    assert td["task_type"] == "image_rebuild"
    assert td["status"] == "pending"
    assert td["topic"] == "RAG echo chamber"
    assert td["task_metadata"] == {"target_task_id": "draft-1", "allow_stock": True}


@pytest.mark.asyncio
async def test_enqueue_defaults_allow_stock_false(monkeypatch):
    added: dict = {}

    class FakeTasksDb:
        def __init__(self, pool):
            pass

        async def add_task(self, task_data):
            added["task_data"] = task_data
            return "rebuild-task-456"

    monkeypatch.setattr("services.tasks_db.TasksDatabase", FakeTasksDb)

    await enqueue_image_rebuild(FakePool(), "draft-2")
    assert added["task_data"]["task_metadata"]["allow_stock"] is False


@pytest.mark.asyncio
async def test_enqueue_rejects_non_awaiting_draft():
    with pytest.raises(ValueError, match="awaiting_approval"):
        await enqueue_image_rebuild(FakePool(status="approved"), "draft-3")


@pytest.mark.asyncio
async def test_enqueue_rejects_unknown_task():
    with pytest.raises(ValueError, match="no pipeline_tasks row"):
        await enqueue_image_rebuild(FakePool(status=None), "nope")
