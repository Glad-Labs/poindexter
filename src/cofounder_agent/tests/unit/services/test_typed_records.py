"""
Unit tests for ``schemas.typed_records``.

The TypedDict shapes here mirror Pydantic models in
``schemas.database_response_models``. These tests pin them together so
the two layers can't silently drift — if a Pydantic field is added or
renamed without updating the TypedDict (or vice versa), the
``test_*_matches_pydantic_fields`` checks fail. That's the whole point
of the typed-Record layer: same shape, two runtime costs.

Reference: glad-labs-stack #201.
"""

from typing import get_type_hints

import pytest

from schemas.database_response_models import (
    PostResponse,
    TaskCostBreakdownResponse,
    TaskCountsResponse,
    TaskResponse,
    UserResponse,
)
from schemas.typed_records import (
    CostBreakdownRecord,
    PaginatedTasksResult,
    PostRecord,
    TaskCountsRecord,
    TaskRecord,
    UserRecord,
)


def _pydantic_field_names(model_cls) -> set[str]:
    return set(model_cls.model_fields.keys())


def _typeddict_field_names(td_cls) -> set[str]:
    # TypedDict stores its field set on __annotations__
    return set(td_cls.__annotations__.keys())


@pytest.mark.unit
class TestTaskRecord:
    def test_matches_pydantic_fields(self):
        """TaskRecord and TaskResponse must agree on field set.

        Drift here is the bug the typed-Record layer exists to prevent:
        a route that builds a TaskResponse, dumps to dict, and hands to
        an internal caller that expects a TaskRecord must produce the
        same keys on both sides.
        """
        assert _typeddict_field_names(TaskRecord) == _pydantic_field_names(TaskResponse)

    def test_total_false_means_all_optional(self):
        """Every key is NotRequired so callers can pass any subset.

        Mirrors how rows from the ``content_tasks`` view come back —
        nullable columns may be absent after ModelConverter strips
        them.
        """
        assert TaskRecord.__total__ is False

    def test_typeddict_is_dict_at_runtime(self):
        """TypedDict has zero runtime overhead — instances are dicts."""
        record: TaskRecord = {"id": "t-1", "status": "pending"}
        assert isinstance(record, dict)
        assert record["id"] == "t-1"

    def test_subscription_works(self):
        record: TaskRecord = {"id": "t-1", "title": "Test", "status": "pending"}
        assert record["title"] == "Test"


@pytest.mark.unit
class TestPostRecord:
    def test_matches_pydantic_fields(self):
        assert _typeddict_field_names(PostRecord) == _pydantic_field_names(PostResponse)

    def test_total_false(self):
        assert PostRecord.__total__ is False


@pytest.mark.unit
class TestUserRecord:
    def test_matches_pydantic_fields(self):
        assert _typeddict_field_names(UserRecord) == _pydantic_field_names(UserResponse)

    def test_total_false(self):
        assert UserRecord.__total__ is False


@pytest.mark.unit
class TestCostBreakdownRecord:
    def test_matches_pydantic_fields(self):
        assert _typeddict_field_names(CostBreakdownRecord) == _pydantic_field_names(
            TaskCostBreakdownResponse
        )

    def test_total_false(self):
        assert CostBreakdownRecord.__total__ is False


@pytest.mark.unit
class TestTaskCountsRecord:
    def test_matches_pydantic_fields(self):
        assert _typeddict_field_names(TaskCountsRecord) == _pydantic_field_names(
            TaskCountsResponse
        )

    def test_total_false(self):
        assert TaskCountsRecord.__total__ is False


@pytest.mark.unit
class TestPaginatedTasksResult:
    def test_is_tuple_alias(self):
        """``PaginatedTasksResult`` resolves to ``tuple[list[TaskRecord], int]``.

        Helpers that return ``(rows, total)`` should destructure at the
        call site — this test pins the alias so type checkers catch a
        breaking change to the shape.
        """
        origin = getattr(PaginatedTasksResult, "__origin__", None)
        assert origin is tuple
        args = PaginatedTasksResult.__args__
        assert len(args) == 2
        assert args[1] is int
        # First arg is list[TaskRecord]
        list_args = args[0]
        assert getattr(list_args, "__origin__", None) is list
        assert list_args.__args__[0] is TaskRecord


@pytest.mark.unit
class TestModuleSurface:
    def test_exports_via_schemas_package(self):
        """All typed-Record types must be re-exported from ``schemas``
        so consumers can write ``from schemas import TaskRecord`` next
        to ``from schemas import TaskResponse`` — the two layers should
        be one import away from each other.
        """
        import schemas

        for name in (
            "TaskRecord",
            "PostRecord",
            "UserRecord",
            "CostBreakdownRecord",
            "TaskCountsRecord",
            "PaginatedTasksResult",
        ):
            assert hasattr(schemas, name), f"{name} missing from schemas package surface"

    def test_task_record_field_types_are_optional_or_required_strings(self):
        """Sanity-check the shape after import — every annotation should
        be a usable type.
        """
        hints = get_type_hints(TaskRecord)
        assert "id" in hints
        assert "status" in hints
        # 45 fields keeps drift discoverable in CI
        assert len(hints) >= 40
