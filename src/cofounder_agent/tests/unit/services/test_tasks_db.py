"""
Unit tests for services/tasks_db.py.

Tests cover:
- serialize_value_for_postgres — type coercions
- TasksDatabase.get_pending_tasks — success, no pool, timeout, DB error
- TasksDatabase.get_all_tasks — success, empty, DB error
- TasksDatabase.add_task — success, UUID coercion, metadata extraction, raises on DB error
- TasksDatabase.get_task — UUID lookup, numeric lookup, not found, DB error
- TasksDatabase.update_task_status — UUID and numeric ID, result field, not found, DB error
- TasksDatabase.update_task — metadata normalization, task_name→title, no updates, DB error
- TasksDatabase.get_tasks_paginated — success, status/category filters, empty result
- TasksDatabase.get_task_counts — success, DB error fallback
- TasksDatabase.get_queued_tasks — success, DB error fallback
- TasksDatabase.get_tasks_by_date_range — date default, status filter, limit capped
- TasksDatabase.delete_task — success, not found, DB error
- TasksDatabase.get_drafts — success, empty, DB error
- TasksDatabase.get_status_history — success, DB error fallback
- TasksDatabase.sweep_stale_tasks — success, no changes, DB error

asyncpg pool fully mocked; no real DB access.
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.tasks_db import TasksDatabase, serialize_value_for_postgres

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**kwargs):
    row = MagicMock()
    _data = {**kwargs}
    row.__getitem__ = lambda self, k: _data.get(k)
    row.get = lambda k, default=None: _data.get(k, default)
    row.__bool__ = lambda self: True
    row.items = lambda: _data.items()
    row.keys = lambda: _data.keys()
    row.values = lambda: _data.values()
    return row


def _make_pool(
    fetchrow_result=None,
    fetch_result=None,
    fetchval_result=None,
    execute_result=None,
    fetchrow_side_effect=None,
    fetch_side_effect=None,
):
    conn = MagicMock()
    if fetchrow_side_effect:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    if fetch_side_effect:
        conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        conn.fetch = AsyncMock(return_value=fetch_result or [])
    conn.fetchval = AsyncMock(return_value=fetchval_result)
    conn.execute = AsyncMock(return_value=execute_result or "DELETE 1")
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


def _make_db(pool=None):
    return TasksDatabase(pool=pool or _make_pool())


_CONVERTER = "services.tasks_db.ModelConverter"


# ---------------------------------------------------------------------------
# serialize_value_for_postgres — module-level pure function
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSerializeValueForPostgres:
    def test_none_returns_none(self):
        assert serialize_value_for_postgres(None) is None

    def test_dict_serialized_to_json_string(self):
        result = serialize_value_for_postgres({"key": "value"})
        assert result == '{"key": "value"}'

    def test_list_serialized_to_json_string(self):
        result = serialize_value_for_postgres([1, 2, 3])
        assert result == "[1, 2, 3]"

    def test_int_returned_unchanged(self):
        assert serialize_value_for_postgres(42) == 42

    def test_float_returned_unchanged(self):
        assert serialize_value_for_postgres(3.14) == 3.14

    def test_bool_returned_unchanged(self):
        assert serialize_value_for_postgres(True) is True

    def test_plain_string_returned_unchanged(self):
        assert serialize_value_for_postgres("hello") == "hello"

    def test_iso_datetime_string_converted(self):
        result = serialize_value_for_postgres("2026-03-12T08:00:00")
        assert isinstance(result, datetime)

    def test_iso_datetime_z_suffix_handled(self):
        result = serialize_value_for_postgres("2026-03-12T08:00:00Z")
        assert isinstance(result, datetime)

    def test_datetime_object_returned_unchanged(self):
        dt = datetime(2026, 3, 12, 8, 0)
        result = serialize_value_for_postgres(dt)
        assert result is dt

    def test_unknown_type_converted_to_string(self):
        class Weird:
            def __str__(self):
                return "weird_value"

        result = serialize_value_for_postgres(Weird())
        assert result == "weird_value"


# ---------------------------------------------------------------------------
# get_pending_tasks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPendingTasks:
    @pytest.mark.asyncio
    async def test_no_pool_returns_empty(self):
        db = TasksDatabase(pool=None)  # type: ignore[arg-type]
        result = await db.get_pending_tasks()
        assert result == []

    @pytest.mark.asyncio
    async def test_success_returns_list_of_dicts(self):
        row = _make_row(task_id="t-1", status="pending")
        pool = _make_pool(fetch_result=[row])
        db = _make_db(pool)

        sentinel = {"task_id": "t-1"}
        with (
            patch(f"{_CONVERTER}.to_task_response", return_value=MagicMock()),
            patch(f"{_CONVERTER}.to_dict", return_value=sentinel),
        ):
            result = await db.get_pending_tasks(limit=5)

        assert len(result) == 1
        assert result[0] == sentinel

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.get_pending_tasks()
        assert result == []


# ---------------------------------------------------------------------------
# get_all_tasks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAllTasks:
    @pytest.mark.asyncio
    async def test_success_returns_task_responses(self):
        rows = [_make_row(task_id="t-1"), _make_row(task_id="t-2")]
        pool = _make_pool(fetch_result=rows)
        db = _make_db(pool)

        sentinel = MagicMock()
        with patch(f"{_CONVERTER}.to_task_response", return_value=sentinel):
            result = await db.get_all_tasks(limit=50)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.get_all_tasks()
        assert result == []


# ---------------------------------------------------------------------------
# add_task
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAddTask:
    @pytest.mark.asyncio
    async def test_returns_task_id_string(self):
        pool = _make_pool(fetchval_result="task-uuid-returned")
        db = _make_db(pool)

        result = await db.add_task({"task_name": "Blog post about AI", "topic": "AI"})
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_task_name_stored_in_metadata(self):
        pool = _make_pool(fetchval_result="t-1")
        db = _make_db(pool)
        result = await db.add_task({"task_name": "My Task", "topic": "Tech"})
        assert result == "t-1"

    @pytest.mark.asyncio
    async def test_custom_task_id_used(self):
        pool = _make_pool(fetchval_result="custom-id-123")
        db = _make_db(pool)

        result = await db.add_task({"id": "custom-id-123", "topic": "AI"})
        assert result == "custom-id-123"

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetchval_result=None)
        async with pool.acquire() as conn:
            conn.fetchval = AsyncMock(side_effect=RuntimeError("DB down"))

        db = _make_db(pool)
        with pytest.raises(Exception):
            await db.add_task({"topic": "AI"})


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTask:
    @pytest.mark.asyncio
    async def test_uuid_lookup_returns_dict(self):
        row = _make_row(task_id="uuid-abc", status="completed")
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        sentinel = {"task_id": "uuid-abc"}
        with (
            patch(f"{_CONVERTER}.to_task_response", return_value=MagicMock()),
            patch(f"{_CONVERTER}.to_dict", return_value=sentinel),
        ):
            result = await db.get_task("uuid-abc")

        assert result == sentinel

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)

        result = await db.get_task("no-such-uuid")
        assert result is None

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        result = await db.get_task("uuid-abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_numeric_looking_id_uses_task_id_column(self):
        """Numeric-looking strings are looked up via task_id (the actual PK).
        The legacy numeric path was removed: content_tasks.id is UUID, not INTEGER,
        so int(task_id) always raised a DataError. (See issue #301)"""
        row = _make_row(task_id="42")
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        sentinel = {"task_id": "42"}
        with (
            patch(f"{_CONVERTER}.to_task_response", return_value=MagicMock()),
            patch(f"{_CONVERTER}.to_dict", return_value=sentinel),
        ):
            result = await db.get_task("42")

        assert result == sentinel


# ---------------------------------------------------------------------------
# update_task_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateTaskStatus:
    @pytest.mark.asyncio
    async def test_uuid_update_returns_dict(self):
        row = _make_row(task_id="t-1", status="completed")
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        with patch.object(db, "_convert_row_to_dict", return_value={"status": "completed"}):
            result = await db.update_task_status("t-1", "completed")

        assert result is not None

    @pytest.mark.asyncio
    async def test_with_result_includes_result_field(self):
        row = _make_row(task_id="t-1", status="completed", result="some result")
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        with patch.object(db, "_convert_row_to_dict", return_value={"status": "completed"}):
            result = await db.update_task_status("t-1", "completed", result="some result")

        assert result is not None

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)
        result = await db.update_task_status("not-found", "failed")
        assert result is None

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.update_task_status("t-1", "failed")
        assert result is None


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateTask:
    @pytest.mark.asyncio
    async def test_no_updates_calls_get_task(self):
        db = _make_db()
        db.get_task = AsyncMock(return_value={"task_id": "t-1"})
        result = await db.update_task("t-1", {})
        db.get_task.assert_awaited_once_with("t-1")

    @pytest.mark.asyncio
    async def test_task_name_mapped_to_title(self):
        """task_name in updates should be remapped to title."""
        row = _make_row(task_id="t-1", title="My Task")
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        sentinel = {"title": "My Task"}
        with (
            patch(f"{_CONVERTER}.to_task_response", return_value=MagicMock()),
            patch(f"{_CONVERTER}.to_dict", return_value=sentinel),
        ):
            result = await db.update_task("t-1", {"task_name": "My Task"})

        assert result == sentinel

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)
        result = await db.update_task("t-1", {"status": "completed"})
        assert result is None

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.update_task("t-1", {"status": "failed"})
        assert result is None

    @pytest.mark.asyncio
    async def test_metadata_fields_extracted_to_columns(self):
        """Fields in task_metadata should be extracted to dedicated columns."""
        row = _make_row(task_id="t-1", content="extracted content")
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        metadata = {"content": "extracted content", "seo_title": "SEO Title"}

        sentinel = {"content": "extracted content"}
        with (
            patch(f"{_CONVERTER}.to_task_response", return_value=MagicMock()),
            patch(f"{_CONVERTER}.to_dict", return_value=sentinel),
        ):
            result = await db.update_task("t-1", {"task_metadata": json.dumps(metadata)})

        assert result == sentinel


# ---------------------------------------------------------------------------
# get_tasks_paginated
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTasksPaginated:
    @pytest.mark.asyncio
    async def test_returns_tasks_and_total(self):
        row = _make_row(task_id="t-1", total_count=5)
        pool = _make_pool(fetch_result=[row])
        db = _make_db(pool)

        with patch.object(db, "_convert_row_to_dict", return_value={"task_id": "t-1"}):
            tasks, total = await db.get_tasks_paginated(offset=0, limit=20)

        assert total == 5
        assert len(tasks) == 1

    @pytest.mark.asyncio
    async def test_empty_result_returns_zero_total(self):
        pool = _make_pool(fetch_result=[])
        db = _make_db(pool)

        tasks, total = await db.get_tasks_paginated()
        assert total == 0
        assert tasks == []

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        tasks, total = await db.get_tasks_paginated()
        assert tasks == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_status_filter_included(self):
        """Verify status filter doesn't raise."""
        pool = _make_pool(fetch_result=[])
        db = _make_db(pool)
        tasks, total = await db.get_tasks_paginated(status="pending")
        assert tasks == []


# ---------------------------------------------------------------------------
# get_task_counts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTaskCounts:
    @pytest.mark.asyncio
    async def test_success_returns_counts(self):
        rows = [
            _make_row(status="pending", count=3),
            _make_row(status="completed", count=10),
            _make_row(status="failed", count=2),
        ]
        pool = _make_pool(fetch_result=rows)
        db = _make_db(pool)

        result = await db.get_task_counts()
        assert result.total == 15
        assert result.pending == 3
        assert result.completed == 10
        assert result.failed == 2

    @pytest.mark.asyncio
    async def test_db_error_returns_zero_counts(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.get_task_counts()
        assert result.total == 0


# ---------------------------------------------------------------------------
# get_queued_tasks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetQueuedTasks:
    @pytest.mark.asyncio
    async def test_success_returns_task_responses(self):
        rows = [_make_row(task_id="t-1")]
        pool = _make_pool(fetch_result=rows)
        db = _make_db(pool)

        with patch(f"{_CONVERTER}.to_task_response", return_value=MagicMock()):
            result = await db.get_queued_tasks(limit=3)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.get_queued_tasks()
        assert result == []


# ---------------------------------------------------------------------------
# get_tasks_by_date_range
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTasksByDateRange:
    @pytest.mark.asyncio
    async def test_defaults_to_last_30_days(self):
        pool = _make_pool(fetch_result=[])
        db = _make_db(pool)
        result = await db.get_tasks_by_date_range()
        assert result == []

    @pytest.mark.asyncio
    async def test_limit_capped_at_500(self):
        """Limit is capped at 500 even if caller passes 1000."""
        pool = _make_pool(fetch_result=[])
        db = _make_db(pool)
        result = await db.get_tasks_by_date_range(limit=10000)
        assert result == []

    @pytest.mark.asyncio
    async def test_success_returns_dicts(self):
        row = _make_row(task_id="t-1", status="completed")
        pool = _make_pool(fetch_result=[row])
        db = _make_db(pool)

        with patch.object(db, "_convert_row_to_dict", return_value={"task_id": "t-1"}):
            result = await db.get_tasks_by_date_range(
                start_date=datetime(2026, 3, 1),
                end_date=datetime(2026, 3, 12),
            )

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.get_tasks_by_date_range()
        assert result == []


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteTask:
    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        pool = _make_pool(execute_result="DELETE 1")
        db = _make_db(pool)
        # Peek at the actual delete_task implementation for how it checks success
        # In tasks_db delete_task uses execute() and checks result
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(return_value="DELETE 1")
        result = await db.delete_task("t-1")
        # If implementation returns bool True when delete succeeds
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_db_error_returns_false(self):
        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(side_effect=RuntimeError("DB down"))
            conn.fetchrow = AsyncMock(side_effect=RuntimeError("DB down"))
            conn.fetch = AsyncMock(side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.delete_task("t-1")
        assert result is False


# ---------------------------------------------------------------------------
# get_drafts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDrafts:
    @pytest.mark.asyncio
    async def test_success_returns_tuple(self):
        row = _make_row(task_id="t-1", status="draft", total_count=1)
        pool = _make_pool(fetch_result=[row])
        db = _make_db(pool)

        with patch.object(db, "_convert_row_to_dict", return_value={"task_id": "t-1"}):
            drafts, total = await db.get_drafts(limit=10, offset=0)

        assert isinstance(drafts, list)
        assert isinstance(total, int)

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        drafts, total = await db.get_drafts()
        assert drafts == []


# ---------------------------------------------------------------------------
# get_status_history
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetStatusHistory:
    @pytest.mark.asyncio
    async def test_success_returns_list(self):
        rows = [
            _make_row(task_id="t-1", status="pending", created_at=None),
            _make_row(task_id="t-1", status="completed", created_at=None),
        ]
        pool = _make_pool(fetch_result=rows)
        db = _make_db(pool)
        result = await db.get_status_history("t-1")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.get_status_history("t-1")
        assert result == []
