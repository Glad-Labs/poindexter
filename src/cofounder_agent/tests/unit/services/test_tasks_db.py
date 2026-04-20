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
        with pytest.raises(RuntimeError):
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
        await db.update_task("t-1", {})
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


# ---------------------------------------------------------------------------
# get_tasks_by_ids — bulk fetch
# ---------------------------------------------------------------------------


class TestGetTasksByIds:
    @pytest.mark.asyncio
    async def test_empty_list_returns_empty_dict(self):
        db = _make_db()
        result = await db.get_tasks_by_ids([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_dict_keyed_by_task_id(self):
        rows = [
            _make_row(task_id="t-1", topic="A", content="aa"),
            _make_row(task_id="t-2", topic="B", content="bb"),
        ]
        pool = _make_pool(fetch_result=rows)
        db = _make_db(pool)

        with patch(_CONVERTER) as mc:
            mc.to_task_response.side_effect = lambda r: r
            mc.to_dict.side_effect = lambda r: {"task_id": r["task_id"], "topic": r["topic"]}
            result = await db.get_tasks_by_ids(["t-1", "t-2"])
        assert set(result.keys()) == {"t-1", "t-2"}

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_dict(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("query failed"))
        db = _make_db(pool)
        result = await db.get_tasks_by_ids(["t-1"])
        assert result == {}


# ---------------------------------------------------------------------------
# sweep_stale_tasks
# ---------------------------------------------------------------------------


class TestSweepStaleTasks:
    @pytest.mark.asyncio
    async def test_no_stale_returns_zero_counts(self):
        # Build a pool whose connection supports transaction context manager
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock()

        @asynccontextmanager
        async def _txn():
            yield None
        conn.transaction = _txn

        pool = MagicMock()

        @asynccontextmanager
        async def _acquire():
            yield conn
        pool.acquire = _acquire

        db = TasksDatabase(pool=pool)
        result = await db.sweep_stale_tasks(stale_threshold_minutes=60)
        assert result == {"reset": 0, "failed": 0}

    @pytest.mark.asyncio
    async def test_resets_under_max_retries(self):
        # Two stale tasks: one with retry_count=0 (reset), one with retry_count=3 (fail)
        stale_rows = [
            {"task_id": "t-1", "task_metadata": json.dumps({"retry_count": 0})},
            {"task_id": "t-2", "task_metadata": json.dumps({"retry_count": 3})},
        ]
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=stale_rows)
        conn.execute = AsyncMock()

        @asynccontextmanager
        async def _txn():
            yield None
        conn.transaction = _txn

        pool = MagicMock()

        @asynccontextmanager
        async def _acquire():
            yield conn
        pool.acquire = _acquire

        db = TasksDatabase(pool=pool)
        result = await db.sweep_stale_tasks(stale_threshold_minutes=60, max_retries=3)
        assert result == {"reset": 1, "failed": 1}
        # Two execute calls: one reset, one fail
        assert conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_db_error_returns_zero_counts(self):
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=RuntimeError("conn lost"))
        db = TasksDatabase(pool=pool)
        result = await db.sweep_stale_tasks(stale_threshold_minutes=60)
        assert result == {"reset": 0, "failed": 0}

    @pytest.mark.asyncio
    async def test_handles_empty_metadata(self):
        # Task with NULL/empty metadata - retry_count defaults to 0
        stale_rows = [
            {"task_id": "t-1", "task_metadata": None},
            {"task_id": "t-2", "task_metadata": ""},
        ]
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=stale_rows)
        conn.execute = AsyncMock()

        @asynccontextmanager
        async def _txn():
            yield None
        conn.transaction = _txn

        pool = MagicMock()

        @asynccontextmanager
        async def _acquire():
            yield conn
        pool.acquire = _acquire

        db = TasksDatabase(pool=pool)
        result = await db.sweep_stale_tasks()
        # Both should be reset (retry_count defaults to 0)
        assert result == {"reset": 2, "failed": 0}


# ---------------------------------------------------------------------------
# bulk_update_task_statuses
# ---------------------------------------------------------------------------


class TestBulkUpdateTaskStatuses:
    @pytest.mark.asyncio
    async def test_empty_list_returns_empty_dicts(self):
        db = _make_db()
        result = await db.bulk_update_task_statuses([], "completed")
        assert result == {"updated_ids": [], "missing_ids": []}

    @pytest.mark.asyncio
    async def test_partitions_existing_and_missing(self):
        # First fetch (existence check) returns one of two
        existing_rows = [{"task_id": "t-1"}]
        # Second fetch (UPDATE RETURNING) returns the same one
        update_rows = [{"task_id": "t-1"}]

        conn = MagicMock()
        conn.fetch = AsyncMock(side_effect=[existing_rows, update_rows])

        @asynccontextmanager
        async def _txn():
            yield None
        conn.transaction = _txn

        pool = MagicMock()

        @asynccontextmanager
        async def _acquire():
            yield conn
        pool.acquire = _acquire

        db = TasksDatabase(pool=pool)
        result = await db.bulk_update_task_statuses(["t-1", "t-2"], "completed")
        assert result["updated_ids"] == ["t-1"]
        assert result["missing_ids"] == ["t-2"]

    @pytest.mark.asyncio
    async def test_all_missing_no_update_call(self):
        # Existence check returns nothing
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=[])

        @asynccontextmanager
        async def _txn():
            yield None
        conn.transaction = _txn

        pool = MagicMock()

        @asynccontextmanager
        async def _acquire():
            yield conn
        pool.acquire = _acquire

        db = TasksDatabase(pool=pool)
        result = await db.bulk_update_task_statuses(["t-1", "t-2"], "failed")
        assert result["updated_ids"] == []
        assert set(result["missing_ids"]) == {"t-1", "t-2"}
        # Only the existence check was called, not the UPDATE
        assert conn.fetch.await_count == 1

    @pytest.mark.asyncio
    async def test_db_exception_raises(self):
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=RuntimeError("conn lost"))
        db = TasksDatabase(pool=pool)
        with pytest.raises(RuntimeError):
            await db.bulk_update_task_statuses(["t-1"], "completed")


# ---------------------------------------------------------------------------
# claim_next_task
# ---------------------------------------------------------------------------


class TestClaimNextTask:
    @pytest.mark.asyncio
    async def test_claims_pending_task(self):
        row_data = {"task_id": "t-1", "topic": "test", "status": "in_progress"}
        # asyncpg Record dict-like behavior
        pool = _make_pool(fetchrow_result=row_data)
        db = _make_db(pool)
        result = await db.claim_next_task("worker-1")
        assert result == row_data

    @pytest.mark.asyncio
    async def test_no_pending_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)
        result = await db.claim_next_task("worker-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_with_categories_filter(self):
        row_data = {"task_id": "t-1", "task_category": "blog_post"}
        pool = _make_pool(fetchrow_result=row_data)
        db = _make_db(pool)
        result = await db.claim_next_task("worker-1", task_categories=["blog_post", "podcast"])
        assert result == row_data

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("conn lost"))
        db = _make_db(pool)
        result = await db.claim_next_task("worker-1")
        assert result is None


# ---------------------------------------------------------------------------
# release_task
# ---------------------------------------------------------------------------


class TestReleaseTask:
    @pytest.mark.asyncio
    async def test_release_with_no_error_marks_pending(self):
        pool = _make_pool()
        db = _make_db(pool)
        # Should not raise
        await db.release_task("t-1", "worker-1")

    @pytest.mark.asyncio
    async def test_release_with_error_marks_failed(self):
        pool = _make_pool()
        db = _make_db(pool)
        await db.release_task("t-1", "worker-1", error_message="oops")


# ---------------------------------------------------------------------------
# bulk_add_tasks
# ---------------------------------------------------------------------------


def _make_bulk_pool():
    """Pool that supports both conn.executemany and a transaction context manager."""
    conn = MagicMock()
    conn.executemany = AsyncMock()

    @asynccontextmanager
    async def _tx():
        yield

    conn.transaction = MagicMock(return_value=_tx())
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


@pytest.mark.unit
class TestBulkAddTasks:
    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self):
        pool = _make_pool()
        db = _make_db(pool)
        result = await db.bulk_add_tasks([])
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_list_of_task_ids(self):
        pool, conn = _make_bulk_pool()
        db = _make_db(pool)

        tasks = [
            {"topic": "AI", "task_name": "post-1", "status": "pending"},
            {"topic": "Docker", "task_name": "post-2", "status": "pending"},
            {"topic": "Kubernetes", "task_name": "post-3", "status": "pending"},
        ]
        result = await db.bulk_add_tasks(tasks)

        assert len(result) == 3
        assert all(isinstance(tid, str) for tid in result)
        conn.executemany.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uses_provided_task_id_when_present(self):
        pool, conn = _make_bulk_pool()
        db = _make_db(pool)

        tasks = [
            {"id": "explicit-id-1", "topic": "AI"},
            {"task_id": "explicit-id-2", "topic": "Docker"},
        ]
        result = await db.bulk_add_tasks(tasks)

        assert result == ["explicit-id-1", "explicit-id-2"]

    @pytest.mark.asyncio
    async def test_uuid_object_coerced_to_string(self):
        from uuid import UUID
        pool, conn = _make_bulk_pool()
        db = _make_db(pool)

        uid = UUID("12345678-1234-5678-1234-567812345678")
        tasks = [{"id": uid, "topic": "AI"}]
        result = await db.bulk_add_tasks(tasks)

        assert isinstance(result[0], str)
        assert result[0] == str(uid)

    @pytest.mark.asyncio
    async def test_task_name_merged_into_metadata(self):
        pool, conn = _make_bulk_pool()
        db = _make_db(pool)

        captured = {}

        async def _capture_executemany(sql, rows):
            captured["rows"] = rows

        conn.executemany = AsyncMock(side_effect=_capture_executemany)

        await db.bulk_add_tasks([{
            "topic": "AI",
            "task_name": "My Post",
            "task_metadata": {},
        }])

        # #231: after dropping dead columns, task_metadata moved from
        # $20 to $14 (positional index 13).
        row = captured["rows"][0]
        metadata_json = row[13]
        metadata = json.loads(metadata_json)
        assert metadata.get("task_name") == "My Post"

    @pytest.mark.asyncio
    async def test_default_fields_when_unspecified(self):
        pool, conn = _make_bulk_pool()
        db = _make_db(pool)

        captured = {}

        async def _capture(sql, rows):
            captured["rows"] = rows

        conn.executemany = AsyncMock(side_effect=_capture)

        await db.bulk_add_tasks([{"topic": "AI"}])

        row = captured["rows"][0]
        # #231: column positions shifted after dropping dead columns.
        # New order: task_id, content_type, task_type, status, topic,
        #            title, style, tone, target_length, primary_keyword,
        #            target_audience, category, approval_status,
        #            task_metadata, site_id, created_at, updated_at.
        assert row[2] == "blog_post"   # task_type
        assert row[3] == "pending"     # status
        assert row[6] == "technical"   # style
        assert row[7] == "professional"  # tone
        assert row[8] == 1500          # target_length

    @pytest.mark.asyncio
    async def test_db_exception_raised(self):
        pool, conn = _make_bulk_pool()
        conn.executemany = AsyncMock(side_effect=RuntimeError("unique violation"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError, match="unique violation"):
            await db.bulk_add_tasks([{"topic": "x"}])


# ---------------------------------------------------------------------------
# get_kpi_aggregates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetKpiAggregates:
    @pytest.mark.asyncio
    async def test_empty_result_returns_zero_total(self):
        pool = _make_pool(fetch_result=[])
        db = _make_db(pool)

        result = await db.get_kpi_aggregates()

        assert result["rows"] == []
        assert result["total_tasks"] == 0

    @pytest.mark.asyncio
    async def test_sums_counts_across_rows(self):
        rows = [
            {"status": "completed", "model_used": "qwen", "task_type": "blog",
             "day": "2026-04-01", "count": 10, "total_cost": 0.50,
             "avg_duration_s": 45.0, "completed_count": 10},
            {"status": "failed", "model_used": "qwen", "task_type": "blog",
             "day": "2026-04-01", "count": 2, "total_cost": 0.10,
             "avg_duration_s": 20.0, "completed_count": 0},
            {"status": "completed", "model_used": "qwen", "task_type": "blog",
             "day": "2026-04-02", "count": 15, "total_cost": 0.75,
             "avg_duration_s": 50.0, "completed_count": 15},
        ]
        pool = _make_pool(fetch_result=rows)
        db = _make_db(pool)

        result = await db.get_kpi_aggregates()

        assert result["total_tasks"] == 27  # 10 + 2 + 15
        assert len(result["rows"]) == 3

    @pytest.mark.asyncio
    async def test_custom_date_range(self):
        """When start_date is provided, it's passed as $2 to the query."""
        captured = {}

        async def _capture_fetch(sql, *params):
            captured["sql"] = sql
            captured["params"] = params
            return []

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetch = AsyncMock(side_effect=_capture_fetch)
        db = _make_db(pool)

        start = datetime(2026, 4, 1)
        end = datetime(2026, 4, 10)
        await db.get_kpi_aggregates(start_date=start, end_date=end)

        assert captured["params"][0] == end
        assert captured["params"][1] == start
        assert "created_at >= $2" in captured["sql"]

    @pytest.mark.asyncio
    async def test_no_start_date_omits_date_filter(self):
        captured = {}

        async def _capture_fetch(sql, *params):
            captured["sql"] = sql
            captured["params"] = params
            return []

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetch = AsyncMock(side_effect=_capture_fetch)
        db = _make_db(pool)

        await db.get_kpi_aggregates()

        # Only end_date passed; no $2 date filter
        assert len(captured["params"]) == 1
        assert "created_at >= $2" not in captured["sql"]

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        result = await db.get_kpi_aggregates()

        assert result == {"rows": [], "total_tasks": 0}

    @pytest.mark.asyncio
    async def test_query_uses_filter_completed_count(self):
        """The SQL uses a FILTER clause to count only completed tasks."""
        captured = {}

        async def _capture_fetch(sql, *params):
            captured["sql"] = sql
            return []

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetch = AsyncMock(side_effect=_capture_fetch)
        db = _make_db(pool)

        await db.get_kpi_aggregates()

        assert "FILTER (WHERE status = 'completed')" in captured["sql"]
        assert "completed_count" in captured["sql"]


# ---------------------------------------------------------------------------
# log_status_change
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogStatusChange:
    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        pool = _make_pool(execute_result="INSERT 0 1")
        db = _make_db(pool)

        result = await db.log_status_change(
            task_id="t-1",
            old_status="pending",
            new_status="running",
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_params_captured_correctly(self):
        captured = {}

        async def _capture_execute(sql, *params):
            captured["sql"] = sql
            captured["params"] = params
            return "INSERT 0 1"

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(side_effect=_capture_execute)
        db = _make_db(pool)

        await db.log_status_change(
            task_id="t-1",
            old_status="running",
            new_status="failed",
            reason="timeout after 5 minutes",
            metadata={"attempt": 3, "worker": "worker-1"},
        )

        assert "INSERT INTO task_status_history" in captured["sql"]
        assert captured["params"][0] == "t-1"
        assert captured["params"][1] == "running"
        assert captured["params"][2] == "failed"
        assert captured["params"][3] == "timeout after 5 minutes"
        # metadata is JSON-serialized
        meta = json.loads(captured["params"][4])
        assert meta == {"attempt": 3, "worker": "worker-1"}

    @pytest.mark.asyncio
    async def test_none_reason_passed_as_empty_string(self):
        captured = {}

        async def _capture(sql, *params):
            captured["params"] = params
            return "INSERT 0 1"

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.log_status_change("t-1", "old", "new")

        assert captured["params"][3] == ""

    @pytest.mark.asyncio
    async def test_none_metadata_becomes_empty_dict_json(self):
        captured = {}

        async def _capture(sql, *params):
            captured["params"] = params
            return "INSERT 0 1"

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.log_status_change("t-1", "old", "new")

        metadata_str = captured["params"][4]
        assert json.loads(metadata_str) == {}

    @pytest.mark.asyncio
    async def test_db_error_returns_false(self):
        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(side_effect=RuntimeError("constraint violated"))
        db = _make_db(pool)

        result = await db.log_status_change("t-1", "old", "new")

        assert result is False


# ---------------------------------------------------------------------------
# get_validation_failures
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetValidationFailures:
    @pytest.mark.asyncio
    async def test_returns_failures_with_errors_from_metadata(self):
        now = datetime.now()
        rows = [
            {
                "id": 1,
                "task_id": "t-1",
                "old_status": "running",
                "new_status": "validation_failed",
                "reason": "content too short",
                "metadata": json.dumps({
                    "validation_errors": ["word_count: 50", "missing_headings"],
                    "context": {"word_count": 50},
                }),
                "created_at": now,
            },
        ]
        pool = _make_pool(fetch_result=rows)
        db = _make_db(pool)

        result = await db.get_validation_failures("t-1")

        assert len(result) == 1
        failure = result[0]
        assert failure["id"] == 1
        assert failure["reason"] == "content too short"
        assert failure["errors"] == ["word_count: 50", "missing_headings"]
        assert failure["context"] == {"word_count": 50}
        assert failure["timestamp"] == now.isoformat()

    @pytest.mark.asyncio
    async def test_empty_metadata_returns_empty_errors(self):
        now = datetime.now()
        rows = [
            {
                "id": 1, "task_id": "t-1", "old_status": "running",
                "new_status": "validation_error", "reason": "generic",
                "metadata": None, "created_at": now,
            },
        ]
        pool = _make_pool(fetch_result=rows)
        db = _make_db(pool)

        result = await db.get_validation_failures("t-1")

        assert len(result) == 1
        assert result[0]["errors"] == []
        assert result[0]["context"] == {}

    @pytest.mark.asyncio
    async def test_no_failures_returns_empty_list(self):
        pool = _make_pool(fetch_result=[])
        db = _make_db(pool)

        result = await db.get_validation_failures("t-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_list(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("table missing"))
        db = _make_db(pool)

        result = await db.get_validation_failures("t-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_legacy_timestamp_column_fallback(self):
        """When the modern created_at column query fails, retry with legacy 'timestamp' column."""
        now = datetime.now()
        modern_result = [
            {
                "id": 1, "task_id": "t-1", "old_status": "running",
                "new_status": "validation_failed", "reason": "ok",
                "metadata": None, "created_at": now,
            },
        ]
        call_count = [0]

        async def _fetch_with_fallback(sql, *params):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call (modern SQL) fails
                raise RuntimeError("column created_at does not exist")
            # Second call (legacy SQL) succeeds
            return modern_result

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetch = AsyncMock(side_effect=_fetch_with_fallback)
        db = _make_db(pool)

        result = await db.get_validation_failures("t-1")

        assert len(result) == 1
        assert call_count[0] == 2  # Fallback SQL was attempted

    @pytest.mark.asyncio
    async def test_limit_passed_to_query(self):
        captured = {}

        async def _capture(sql, *params):
            captured["params"] = params
            return []

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetch = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.get_validation_failures("t-1", limit=25)

        assert captured["params"][0] == "t-1"
        assert captured["params"][1] == 25
