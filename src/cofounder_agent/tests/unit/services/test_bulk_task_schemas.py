"""
Unit tests for bulk_task_schemas.py

Tests field validation and model behaviour for bulk task operation schemas.
"""

import pytest
from pydantic import ValidationError

from schemas.bulk_task_schemas import (
    BulkCreateTaskItem,
    BulkCreateTasksRequest,
    BulkCreateTasksResponse,
    BulkTaskRequest,
    BulkTaskResponse,
)


@pytest.mark.unit
class TestBulkTaskRequest:
    def test_valid(self):
        req = BulkTaskRequest(
            task_ids=["id-1", "id-2"],
            action="cancel",
        )
        assert req.action == "cancel"
        assert len(req.task_ids) == 2

    def test_single_task_id(self):
        req = BulkTaskRequest(task_ids=["id-1"], action="pause")
        assert len(req.task_ids) == 1

    def test_missing_action_raises(self):
        with pytest.raises(ValidationError):
            BulkTaskRequest(task_ids=["id-1"])  # type: ignore[call-arg]

    def test_missing_task_ids_raises(self):
        with pytest.raises(ValidationError):
            BulkTaskRequest(action="cancel")  # type: ignore[call-arg]

    def test_all_valid_actions(self):
        for action in ["pause", "resume", "cancel", "delete"]:
            req = BulkTaskRequest(task_ids=["id-1"], action=action)
            assert req.action == action


@pytest.mark.unit
class TestBulkTaskResponse:
    def test_valid(self):
        resp = BulkTaskResponse(
            message="3 tasks cancelled",
            updated=3,
            failed=0,
            total=3,
        )
        assert resp.updated == 3
        assert resp.errors is None

    def test_with_errors(self):
        resp = BulkTaskResponse(
            message="2 tasks cancelled, 1 failed",
            updated=2,
            failed=1,
            total=3,
            errors=[{"task_id": "id-3", "error": "not found"}],
        )
        assert resp.failed == 1
        assert len(resp.errors) == 1  # type: ignore[arg-type]


@pytest.mark.unit
class TestBulkCreateTaskItem:
    def _valid(self, **kwargs):
        defaults = {
            "task_name": "Blog Post Task",
            "topic": "AI in Healthcare",
            "primary_keyword": "AI healthcare",
            "target_audience": "Healthcare professionals",
            "category": "Technology",
        }
        defaults.update(kwargs)
        return BulkCreateTaskItem(**defaults)

    def test_valid_defaults(self):
        item = self._valid()
        assert item.priority == "medium"
        assert item.description is None

    def test_custom_priority(self):
        item = self._valid(priority="high")
        assert item.priority == "high"

    def test_with_description(self):
        item = self._valid(description="A detailed description")
        assert item.description == "A detailed description"

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            BulkCreateTaskItem(  # type: ignore[call-arg]
                task_name="Task",
                topic="Topic",
                # missing primary_keyword
                target_audience="Audience",
                category="Tech",
            )


@pytest.mark.unit
class TestBulkCreateTasksRequest:
    def _make_item(self, name="Task 1"):
        return BulkCreateTaskItem(
            task_name=name,
            topic="Some topic",
            primary_keyword="keyword",
            target_audience="Audience",
            category="Tech",
        )

    def test_valid(self):
        req = BulkCreateTasksRequest(tasks=[self._make_item()])
        assert len(req.tasks) == 1

    def test_multiple_tasks(self):
        req = BulkCreateTasksRequest(tasks=[self._make_item("Task 1"), self._make_item("Task 2")])
        assert len(req.tasks) == 2

    def test_empty_tasks_allowed(self):
        # The schema does not enforce non-empty list — empty is valid
        req = BulkCreateTasksRequest(tasks=[])
        assert len(req.tasks) == 0

    def test_missing_tasks_raises(self):
        with pytest.raises(ValidationError):
            BulkCreateTasksRequest()  # type: ignore[call-arg]


@pytest.mark.unit
class TestBulkCreateTasksResponse:
    def test_valid(self):
        resp = BulkCreateTasksResponse(created=3, failed=0, total=3)
        assert resp.created == 3
        assert resp.tasks is None
        assert resp.errors is None

    def test_with_task_data(self):
        resp = BulkCreateTasksResponse(
            created=2,
            failed=1,
            total=3,
            tasks=[{"id": "task-1"}, {"id": "task-2"}],
            errors=[{"index": 2, "error": "invalid topic"}],
        )
        assert resp.created == 2
        assert len(resp.tasks) == 2  # type: ignore[arg-type]
        assert len(resp.errors) == 1  # type: ignore[arg-type]
