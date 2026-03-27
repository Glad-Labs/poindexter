"""
Unit tests for utils.common_schemas module.

All tests are pure — zero DB, LLM, or network calls.
Covers Pydantic schema validation for pagination, task, subtask, content,
settings, search/filter, and bulk operation models.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from utils.common_schemas import (
    BulkCreateRequest,
    BulkDeleteRequest,
    BulkOperationResponse,
    BulkUpdateRequest,
    ContentCreateRequest,
    ContentUpdateRequest,
    FilterParams,
    IdPathParam,
    IdsQuery,
    PaginatedResponse,
    PaginationMeta,
    PaginationParams,
    SearchParams,
    SettingsBaseRequest,
    SettingsUpdateRequest,
    SubtaskCreateRequest,
    SubtaskUpdateRequest,
    TaskCreateRequest,
    TaskUpdateRequest,
)

# ---------------------------------------------------------------------------
# PaginationParams
# ---------------------------------------------------------------------------


class TestPaginationParams:
    def test_defaults(self):
        params = PaginationParams()  # type: ignore[call-arg]
        assert params.skip == 0
        assert params.limit == 10

    def test_custom_values(self):
        params = PaginationParams(skip=20, limit=50)
        assert params.skip == 20
        assert params.limit == 50

    def test_skip_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            PaginationParams(skip=-1)  # type: ignore[call-arg]

    def test_limit_must_be_at_least_1(self):
        with pytest.raises(ValidationError):
            PaginationParams(limit=0)  # type: ignore[call-arg]

    def test_limit_cannot_exceed_100(self):
        with pytest.raises(ValidationError):
            PaginationParams(limit=101)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# PaginationMeta
# ---------------------------------------------------------------------------


class TestPaginationMeta:
    def test_basic_construction(self):
        meta = PaginationMeta(total=100, skip=0, limit=10, has_more=True)
        assert meta.total == 100
        assert meta.has_more is True

    def test_has_more_false(self):
        meta = PaginationMeta(total=5, skip=0, limit=10, has_more=False)
        assert meta.has_more is False


# ---------------------------------------------------------------------------
# PaginatedResponse
# ---------------------------------------------------------------------------


class TestPaginatedResponse:
    def test_basic_construction(self):
        meta = PaginationMeta(total=1, skip=0, limit=10, has_more=False)
        resp = PaginatedResponse(data=["item"], pagination=meta)  # type: ignore[call-arg]
        assert resp.status == "success"
        assert resp.data == ["item"]
        assert resp.pagination == meta

    def test_optional_request_id(self):
        meta = PaginationMeta(total=0, skip=0, limit=10, has_more=False)
        resp = PaginatedResponse(data=[], pagination=meta, request_id="req-123")  # type: ignore[call-arg]
        assert resp.request_id == "req-123"


# ---------------------------------------------------------------------------
# TaskCreateRequest
# ---------------------------------------------------------------------------


class TestTaskCreateRequest:
    def test_minimum_required_fields(self):
        req = TaskCreateRequest(task_name="My task")  # type: ignore[call-arg]
        assert req.task_name == "My task"

    def test_default_priority_is_medium(self):
        req = TaskCreateRequest(task_name="Task")  # type: ignore[call-arg]
        assert req.priority == "medium"

    def test_valid_priorities(self):
        for priority in ["low", "medium", "high", "critical"]:
            req = TaskCreateRequest(task_name="Task", priority=priority)  # type: ignore[call-arg]
            assert req.priority == priority

    def test_invalid_priority_raises(self):
        with pytest.raises(ValidationError):
            TaskCreateRequest(task_name="Task", priority="extreme")  # type: ignore[call-arg]

    def test_task_name_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            TaskCreateRequest(task_name="")  # type: ignore[call-arg]

    def test_task_name_cannot_exceed_255_chars(self):
        with pytest.raises(ValidationError):
            TaskCreateRequest(task_name="x" * 256)  # type: ignore[call-arg]

    def test_description_max_2000_chars(self):
        with pytest.raises(ValidationError):
            TaskCreateRequest(task_name="Task", description="x" * 2001)  # type: ignore[call-arg]

    def test_optional_project_id(self):
        req = TaskCreateRequest(task_name="Task", project_id="proj-123")  # type: ignore[call-arg]
        assert req.project_id == "proj-123"

    def test_optional_tags(self):
        req = TaskCreateRequest(task_name="Task", tags=["ai", "content"])  # type: ignore[call-arg]
        assert req.tags == ["ai", "content"]

    def test_strips_whitespace_from_task_name(self):
        req = TaskCreateRequest(task_name="  Task Name  ")  # type: ignore[call-arg]
        assert req.task_name == "Task Name"


# ---------------------------------------------------------------------------
# TaskUpdateRequest
# ---------------------------------------------------------------------------


class TestTaskUpdateRequest:
    def test_all_fields_optional(self):
        req = TaskUpdateRequest()  # type: ignore[call-arg]
        assert req.task_name is None
        assert req.status is None

    def test_update_single_field(self):
        req = TaskUpdateRequest(status="completed")  # type: ignore[call-arg]
        assert req.status == "completed"

    def test_invalid_priority_in_update_raises(self):
        with pytest.raises(ValidationError):
            TaskUpdateRequest(priority="galaxy")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# SubtaskCreateRequest
# ---------------------------------------------------------------------------


class TestSubtaskCreateRequest:
    def test_minimum_required_fields(self):
        req = SubtaskCreateRequest(subtask_name="Sub", task_id="task-1")  # type: ignore[call-arg]
        assert req.subtask_name == "Sub"
        assert req.task_id == "task-1"

    def test_estimated_hours_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            SubtaskCreateRequest(subtask_name="Sub", task_id="t1", estimated_hours=-1)  # type: ignore[call-arg]

    def test_estimated_hours_zero_is_allowed(self):
        req = SubtaskCreateRequest(subtask_name="Sub", task_id="t1", estimated_hours=0)  # type: ignore[call-arg]
        assert req.estimated_hours == 0


# ---------------------------------------------------------------------------
# SubtaskUpdateRequest
# ---------------------------------------------------------------------------


class TestSubtaskUpdateRequest:
    def test_all_fields_optional(self):
        req = SubtaskUpdateRequest()  # type: ignore[call-arg]
        assert req.subtask_name is None

    def test_update_with_value(self):
        req = SubtaskUpdateRequest(subtask_name="Updated")  # type: ignore[call-arg]
        assert req.subtask_name == "Updated"


# ---------------------------------------------------------------------------
# ContentCreateRequest
# ---------------------------------------------------------------------------


class TestContentCreateRequest:
    def test_requires_title_and_topic(self):
        req = ContentCreateRequest(title="My Blog", topic="AI")  # type: ignore[call-arg]
        assert req.title == "My Blog"
        assert req.topic == "AI"

    def test_title_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            ContentCreateRequest(title="", topic="AI")  # type: ignore[call-arg]

    def test_title_cannot_exceed_500_chars(self):
        with pytest.raises(ValidationError):
            ContentCreateRequest(title="x" * 501, topic="AI")  # type: ignore[call-arg]

    def test_body_max_50000_chars(self):
        with pytest.raises(ValidationError):
            ContentCreateRequest(title="Title", topic="AI", body="x" * 50001)  # type: ignore[call-arg]

    def test_optional_body(self):
        req = ContentCreateRequest(title="Title", topic="AI")  # type: ignore[call-arg]
        assert req.body is None


# ---------------------------------------------------------------------------
# ContentUpdateRequest
# ---------------------------------------------------------------------------


class TestContentUpdateRequest:
    def test_all_fields_optional(self):
        req = ContentUpdateRequest()  # type: ignore[call-arg]
        assert req.title is None
        assert req.body is None

    def test_is_published_field(self):
        req = ContentUpdateRequest(is_published=True)  # type: ignore[call-arg]
        assert req.is_published is True


# ---------------------------------------------------------------------------
# SettingsBaseRequest
# ---------------------------------------------------------------------------


class TestSettingsBaseRequest:
    def test_requires_key(self):
        req = SettingsBaseRequest(key="my_key")  # type: ignore[call-arg]
        assert req.key == "my_key"

    def test_key_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            SettingsBaseRequest(key="")  # type: ignore[call-arg]

    def test_optional_value(self):
        req = SettingsBaseRequest(key="key", value={"nested": True})  # type: ignore[call-arg]
        assert req.value == {"nested": True}

    def test_description_max_500_chars(self):
        with pytest.raises(ValidationError):
            SettingsBaseRequest(key="key", description="x" * 501)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# SettingsUpdateRequest
# ---------------------------------------------------------------------------


class TestSettingsUpdateRequest:
    def test_all_optional(self):
        req = SettingsUpdateRequest()  # type: ignore[call-arg]
        assert req.value is None

    def test_set_value(self):
        req = SettingsUpdateRequest(value=42)  # type: ignore[call-arg]
        assert req.value == 42


# ---------------------------------------------------------------------------
# IdPathParam / IdsQuery
# ---------------------------------------------------------------------------


class TestIdModels:
    def test_id_path_param(self):
        param = IdPathParam(id="abc-123")
        assert param.id == "abc-123"

    def test_ids_query(self):
        query = IdsQuery(ids=["a", "b", "c"])
        assert len(query.ids) == 3


# ---------------------------------------------------------------------------
# Bulk operation schemas
# ---------------------------------------------------------------------------


class TestBulkSchemas:
    def test_bulk_create_request(self):
        req = BulkCreateRequest(items=[{"name": "item1"}])
        assert len(req.items) == 1

    def test_bulk_update_request(self):
        req = BulkUpdateRequest(updates=[{"id": "1", "status": "done"}])
        assert len(req.updates) == 1

    def test_bulk_delete_request(self):
        req = BulkDeleteRequest(ids=["id1", "id2"])
        assert len(req.ids) == 2

    def test_bulk_operation_response(self):
        resp = BulkOperationResponse(total_processed=10, successful=8, failed=2)  # type: ignore[call-arg]
        assert resp.status == "success"
        assert resp.total_processed == 10
        assert resp.successful == 8
        assert resp.failed == 2


# ---------------------------------------------------------------------------
# SearchParams / FilterParams
# ---------------------------------------------------------------------------


class TestSearchAndFilterParams:
    def test_search_params_all_optional(self):
        params = SearchParams()  # type: ignore[call-arg]
        assert params.query is None

    def test_search_params_sort_order_default(self):
        params = SearchParams()  # type: ignore[call-arg]
        assert params.sort_order == "asc"

    def test_search_params_invalid_sort_order(self):
        with pytest.raises(ValidationError):
            SearchParams(sort_order="sideways")  # type: ignore[call-arg]

    def test_search_params_query_max_500_chars(self):
        with pytest.raises(ValidationError):
            SearchParams(query="x" * 501)  # type: ignore[call-arg]

    def test_filter_params_all_optional(self):
        params = FilterParams()  # type: ignore[call-arg]
        assert params.status is None
        assert params.created_after is None

    def test_filter_params_with_datetime(self):
        now = datetime.now(timezone.utc)
        params = FilterParams(created_after=now)  # type: ignore[call-arg]
        assert params.created_after == now
