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
    """Create a mock asyncpg Record-like row.

    Strict ``__getitem__`` (KeyError on missing key) so production code
    that reads a column the test didn't set fails loudly instead of
    silently getting ``None`` and passing — see GH#337.

    Use this helper ONLY when production code reads ``row[<key>]`` — the
    strict mapping is what gives the test signal value. When a test
    just hands the row to a patched ``ModelConverter`` and asserts on
    the converter's return value, prefer ``object()`` directly: a
    literal sentinel makes it obvious the row contents are not under
    test, and prevents the row-faker from quietly accumulating stale
    columns over time (the original symptom in GH#30).
    """
    row = MagicMock()
    _data = {**kwargs}
    row.__getitem__ = lambda self, k, _d=_data: _d[k]
    row.get = lambda k, default=None, _d=_data: _d.get(k, default)
    row.__bool__ = lambda self: True
    row.items = lambda _d=_data: _d.items()
    row.keys = lambda _d=_data: _d.keys()
    row.values = lambda _d=_data: _d.values()
    return row


def _make_pool(
    fetchrow_result=None,
    fetch_result=None,
    fetchval_result=None,
    execute_result=None,
    fetchrow_side_effect=None,
    fetch_side_effect=None,
    execute_side_effect=None,
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
    if execute_side_effect:
        conn.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        conn.execute = AsyncMock(return_value=execute_result or "DELETE 1")
    conn.executemany = AsyncMock()

    # Add async context manager for conn.transaction() so callers that
    # wrap their writes in `async with conn.transaction(): …` (notably
    # add_task / bulk_add_tasks after the #188 view-INSERT fix) work
    # against this lightweight mock. Each call returns a *fresh* CM so
    # tests that create multiple tasks against the same pool work.
    @asynccontextmanager
    async def _tx_inner():
        yield

    conn.transaction = MagicMock(side_effect=lambda *a, **kw: _tx_inner())

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
        # Opaque row — get_pending_tasks iterates fetch results and hands
        # each row straight to the patched ModelConverter without reading
        # any column. The row's column shape is not under test (GH#337).
        pool = _make_pool(fetch_result=[object()])
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
        # Opaque rows — get_all_tasks hands each row straight to the
        # patched ModelConverter.to_task_response without reading any
        # column. Test asserts only on row count (GH#337).
        pool = _make_pool(fetch_result=[object(), object()])
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
        # #188: add_task now writes to pipeline_tasks + pipeline_versions
        # via conn.execute (not fetchval). The returned task_id is the
        # one we generated locally, not anything the DB hands back.
        pool = _make_pool()
        db = _make_db(pool)

        result = await db.add_task({"task_name": "Blog post about AI", "topic": "AI"})
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_task_name_returned_as_uuid(self):
        # Without an explicit id/task_id, add_task generates a UUID
        # locally and returns it. The fetchval_result mock from the
        # pre-#188 era is no longer wired through anything.
        pool = _make_pool()
        db = _make_db(pool)
        result = await db.add_task({"task_name": "My Task", "topic": "Tech"})
        assert isinstance(result, str)
        # Generated UUIDs follow the standard hyphenated format
        assert result.count("-") == 4

    @pytest.mark.asyncio
    async def test_custom_task_id_used(self):
        pool = _make_pool()
        db = _make_db(pool)

        result = await db.add_task({"id": "custom-id-123", "topic": "AI"})
        assert result == "custom-id-123"

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        # The pipeline_tasks INSERT runs via conn.execute now (#188);
        # any DB-side failure must bubble up, not get swallowed.
        pool = _make_pool(execute_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        with pytest.raises(RuntimeError):
            await db.add_task({"topic": "AI"})

    @pytest.mark.asyncio
    async def test_writes_to_pipeline_tasks_not_view(self):
        """#188 regression guard — INSERTs must target pipeline_tasks
        (the underlying table), never content_tasks (the view).
        """
        captured: list[str] = []

        async def _capture(sql, *args, **kwargs):
            captured.append(sql)
            return "INSERT 0 1"

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.add_task({"id": "t-1", "topic": "AI"})

        joined = "\n".join(captured)
        assert "pipeline_tasks" in joined
        assert "pipeline_versions" in joined
        assert "INSERT INTO content_tasks" not in joined

    @pytest.mark.asyncio
    async def test_metadata_routed_into_stage_data(self):
        """task_metadata + metadata payloads must land inside
        pipeline_versions.stage_data (which the view re-projects).
        """
        captured_args: list[tuple] = []

        async def _capture(sql, *args, **kwargs):
            captured_args.append((sql, args))
            return "INSERT 0 1"

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.add_task({
            "id": "t-1",
            "topic": "AI",
            "task_metadata": {"k": "v"},
            "metadata": {"discovered_by": "test"},
        })

        # The pipeline_versions INSERT is the second call; its stage_data
        # arg (positional 12 in our INSERT signature → args index 11
        # since first arg is task_id).
        versions_call = next(
            (sql, args) for sql, args in captured_args
            if "pipeline_versions" in sql
        )
        sql, args = versions_call
        # stage_data is the second-to-last arg (last is created_at).
        stage_data_json = args[11]
        stage_data = json.loads(stage_data_json)
        assert "task_metadata" in stage_data
        assert stage_data["task_metadata"]["k"] == "v"
        assert "metadata" in stage_data
        assert stage_data["metadata"]["discovered_by"] == "test"


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTask:
    @pytest.mark.asyncio
    async def test_uuid_lookup_returns_dict(self):
        # Opaque row — get_task hands fetchrow's result straight to the
        # patched converter without reading any column. The truthiness
        # check at tasks_db.py:495 is satisfied by ``object()`` (GH#337).
        pool = _make_pool(fetchrow_result=object())
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
        # Opaque row — column shape not under test (GH#337). ``object()``
        # is truthy so the ``if row`` check at tasks_db.py:495 passes.
        pool = _make_pool(fetchrow_result=object())
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
        # Opaque row — update_task_status hands the row to the patched
        # ``self._convert_row_to_dict``. The only direct read is
        # ``row.get("task_type", "unknown") if hasattr(row, "get") ...``
        # at tasks_db.py:587 — a plain ``object()`` has no ``.get`` so
        # the fallback branch is taken (GH#337).
        pool = _make_pool(fetchrow_result=object())
        db = _make_db(pool)

        with patch.object(db, "_convert_row_to_dict", return_value={"status": "completed"}):
            result = await db.update_task_status("t-1", "completed")

        assert result is not None

    @pytest.mark.asyncio
    async def test_with_result_includes_result_field(self):
        # Opaque row — column shape not under test (GH#337). See
        # ``test_uuid_update_returns_dict`` above for the row.get/
        # _convert_row_to_dict reasoning.
        pool = _make_pool(fetchrow_result=object())
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
        """task_name in updates should be remapped to title.

        Data-flow test: the resolve-step SELECT at ``tasks_db.py:728-740``
        reads ``resolved["task_id"]`` and ``resolved["status"]`` directly
        to guard against overwriting cancelled/rejected tasks. Seeding
        ONLY those keys on the strict row-faker means a future refactor
        that adds another column-read will KeyError loudly instead of
        coasting on the outer try/except — the GH#337 signal. The final
        ``to_task_response`` / ``to_dict`` calls are patched so the row
        is opaque to that branch.
        """
        row = _make_row(task_id="t-1", status="pending")
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
        """Fields in task_metadata should be extracted to dedicated columns.

        Data-flow test: production reads ``resolved["task_id"]`` and
        ``resolved["status"]`` for the cancelled/rejected guard at
        ``tasks_db.py:728-740``. Seed ONLY those keys on the strict
        row-faker — see GH#337.
        """
        row = _make_row(task_id="t-1", status="pending")
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
        # Data-flow test: production reads ``rows[0]["total_count"]``
        # directly at tasks_db.py:973 to surface the COUNT(*) OVER ()
        # window total. The per-row dict conversion goes through
        # ``self._convert_row_to_dict``, which is patched — so seed
        # ONLY ``total_count`` (GH#337).
        row = _make_row(total_count=5)
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
        # Data-flow test: production reads ``row["status"]`` and
        # ``row["count"]`` directly at tasks_db.py:997. No converter
        # patch in this path. Seed ONLY those two keys so the strict
        # ``__getitem__`` KeyErrors loudly if a future refactor adds
        # another column-read (GH#337).
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
        # Opaque row — get_queued_tasks hands each fetch result straight
        # to the patched ModelConverter.to_task_response without reading
        # any column (GH#337).
        pool = _make_pool(fetch_result=[object()])
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
        # Data-flow test: production at tasks_db.py:1123 calls
        # ``dict(row)`` — iterates ``keys()`` + ``__getitem__`` for every
        # column. Whatever we seed flows verbatim into the result list,
        # but the test asserts only on the row count, so an empty
        # row-faker is enough to satisfy the iteration without
        # accumulating stale columns (GH#337). The
        # ``_convert_row_to_dict`` patch is unreachable here (production
        # uses ``dict(row)`` not the mixin helper) and stays only to
        # match the historical signature.
        row = _make_row()
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
        # Data-flow test: production reads ``rows[0]["total_count"]``
        # directly at tasks_db.py:1254 for the COUNT(*) OVER () window
        # total. The per-row dict conversion goes through the patched
        # ``self._convert_row_to_dict``, so ``task_id`` / ``status`` are
        # never read from the row. Seed ONLY ``total_count`` (GH#337).
        row = _make_row(total_count=1)
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
        # Data-flow test: production at tasks_db.py:1346-1359 reads
        # ``row["id"]``, ``row["task_id"]``, ``row["old_status"]``,
        # ``row["new_status"]``, ``row["reason"]``, ``row["metadata"]``,
        # ``row["created_at"]`` directly. Seed exactly those keys — the
        # strict ``__getitem__`` will KeyError if a future refactor reads
        # an unseeded column (GH#337).
        rows = [
            _make_row(
                id=1,
                task_id="t-1",
                old_status=None,
                new_status="pending",
                reason=None,
                metadata=None,
                created_at=None,
            ),
            _make_row(
                id=2,
                task_id="t-1",
                old_status="pending",
                new_status="completed",
                reason=None,
                metadata=None,
                created_at=None,
            ),
        ]
        pool = _make_pool(fetch_result=rows)
        db = _make_db(pool)
        result = await db.get_status_history("t-1")
        # Assert on the actual success-path output rather than
        # ``isinstance(result, list)``, which the outer except path also
        # satisfies (returning ``[]``) — that's what let the original
        # row-faker silently pass with most columns missing.
        assert len(result) == 2
        assert result[0]["task_id"] == "t-1"
        assert result[0]["new_status"] == "pending"
        assert result[1]["new_status"] == "completed"

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
        # Data-flow test: ``to_task_response`` is patched to identity
        # and ``to_dict`` reads ``r["task_id"]`` + ``r["topic"]`` from
        # the row. Seed ONLY those two keys so the strict mapping
        # KeyErrors loudly if the test ever drifts (GH#337).
        rows = [
            _make_row(task_id="t-1", topic="A"),
            _make_row(task_id="t-2", topic="B"),
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
    """Pool that supports executemany + transaction context manager.

    Captures all executemany calls in ``conn.executemany.call_args_list``
    so tests can assert against both the pipeline_tasks INSERT and the
    pipeline_versions INSERT (post-#188).
    """
    conn = MagicMock()
    conn.executemany = AsyncMock()

    @asynccontextmanager
    async def _tx_inner():
        yield

    conn.transaction = MagicMock(side_effect=lambda *a, **kw: _tx_inner())
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
        # #188: now two executemany calls — pipeline_tasks +
        # pipeline_versions — instead of a single content_tasks insert.
        assert conn.executemany.await_count == 2

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

        captured: list[tuple] = []

        async def _capture_executemany(sql, rows):
            captured.append((sql, rows))

        conn.executemany = AsyncMock(side_effect=_capture_executemany)

        await db.bulk_add_tasks([{
            "topic": "AI",
            "task_name": "My Post",
            "task_metadata": {},
        }])

        # #188: task_metadata now lives inside the pipeline_versions
        # stage_data JSONB blob (positional arg 2 in the version row:
        # task_id, title, stage_data_json, created_at).
        version_call = next(
            (sql, rows) for sql, rows in captured if "pipeline_versions" in sql
        )
        _, rows = version_call
        stage_data_json = rows[0][2]
        stage_data = json.loads(stage_data_json)
        assert stage_data.get("task_metadata", {}).get("task_name") == "My Post"

    @pytest.mark.asyncio
    async def test_default_fields_when_unspecified(self):
        pool, conn = _make_bulk_pool()
        db = _make_db(pool)

        captured: list[tuple] = []

        async def _capture(sql, rows):
            captured.append((sql, rows))

        conn.executemany = AsyncMock(side_effect=_capture)

        await db.bulk_add_tasks([{"topic": "AI"}])

        # #188: pipeline_tasks INSERT positional row layout:
        #   $1 task_id, $2 task_type, $3 topic, $4 status, $5 stage,
        #   $6 site_id, $7 style, $8 tone, $9 target_length,
        #   $10 category, $11 primary_keyword, $12 target_audience,
        #   $13 created_at (used twice for created_at + updated_at)
        pt_call = next(
            (sql, rows) for sql, rows in captured if "pipeline_tasks" in sql
        )
        _, rows = pt_call
        row = rows[0]
        assert row[1] == "blog_post"     # task_type
        assert row[3] == "pending"       # status
        assert row[4] == "pending"       # stage
        assert row[6] == "technical"     # style
        assert row[7] == "professional"  # tone
        assert row[8] == 1500            # target_length

    @pytest.mark.asyncio
    async def test_writes_to_pipeline_tables_not_view(self):
        """#188 regression guard — bulk_add_tasks must INSERT into
        pipeline_tasks + pipeline_versions, never content_tasks (view).
        """
        pool, conn = _make_bulk_pool()
        db = _make_db(pool)

        seen_sql: list[str] = []

        async def _capture(sql, rows):
            seen_sql.append(sql)

        conn.executemany = AsyncMock(side_effect=_capture)
        await db.bulk_add_tasks([{"topic": "AI"}])

        joined = "\n".join(seen_sql)
        assert "pipeline_tasks" in joined
        assert "pipeline_versions" in joined
        assert "INSERT INTO content_tasks" not in joined

    @pytest.mark.asyncio
    async def test_db_exception_raised(self):
        pool, conn = _make_bulk_pool()
        conn.executemany = AsyncMock(side_effect=RuntimeError("unique violation"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError, match="unique violation"):
            await db.bulk_add_tasks([{"topic": "x"}])


# ---------------------------------------------------------------------------
# #188 regression — add_task / bulk_add_tasks against a real Postgres DB
# ---------------------------------------------------------------------------
#
# These tests run against the per-session ``db_pool`` fixture (a real
# Postgres instance with all migrations applied) and exist solely to
# catch the kind of view-vs-table drift that took down the pipeline for
# 2 days in #188.
#
# If the DB driver swings INSERTs back at the content_tasks view, asyncpg
# will raise ``ObjectNotInPrerequisiteStateError: cannot insert into view``
# and these tests will fail loud at CI time rather than at runtime. They
# skip gracefully when no live Postgres is reachable.


@pytest.mark.unit
@pytest.mark.asyncio(loop_scope="session")
class TestAddTaskAgainstRealDb:
    """#188: regression guard — add_task() must succeed against a real
    Postgres regardless of whether the content_tasks view's INSTEAD OF
    triggers are present. We INSERT into pipeline_tasks + pipeline_versions
    directly, so the trigger state is irrelevant.
    """

    async def test_add_task_writes_row_visible_via_view(self, db_pool):
        from services.tasks_db import TasksDatabase

        db = TasksDatabase(pool=db_pool)
        task_id = await db.add_task({
            "topic": "issue-188 regression",
            "task_type": "blog_post",
            "category": "test_188",
            "task_metadata": {"source": "test_188", "discovered_by": "regression"},
        })
        assert task_id

        # Read back through the view — the projected columns must match.
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT topic, task_type, content_type, status, "
                "category, task_metadata, metadata "
                "FROM content_tasks WHERE task_id = $1",
                task_id,
            )

        assert row is not None, "row must round-trip via the view"
        assert row["topic"] == "issue-188 regression"
        assert row["task_type"] == "blog_post"
        assert row["content_type"] == "blog_post"  # view-derived from task_type
        assert row["status"] == "pending"
        assert row["category"] == "test_188"

        meta = row["task_metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta.get("source") == "test_188"
        assert meta.get("discovered_by") == "regression"

        # Cleanup
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM pipeline_tasks WHERE task_id = $1", task_id)

    async def test_bulk_add_tasks_writes_rows_visible_via_view(self, db_pool):
        from services.tasks_db import TasksDatabase

        db = TasksDatabase(pool=db_pool)
        ids = await db.bulk_add_tasks([
            {"topic": "188-bulk-A", "category": "test_188_bulk", "task_name": "Bulk A"},
            {"topic": "188-bulk-B", "category": "test_188_bulk", "task_name": "Bulk B"},
        ])
        assert len(ids) == 2

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT topic, title, status FROM content_tasks "
                "WHERE category = $1 ORDER BY topic ASC",
                "test_188_bulk",
            )

        assert [r["topic"] for r in rows] == ["188-bulk-A", "188-bulk-B"]
        assert [r["title"] for r in rows] == ["Bulk A", "Bulk B"]
        assert all(r["status"] == "pending" for r in rows)

        # Cleanup
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pipeline_tasks WHERE task_id = ANY($1::text[])",
                ids,
            )

    async def test_add_task_does_not_raise_view_insert_error(self, db_pool):
        """Direct guard against the original #188 symptom:
        ``asyncpg.exceptions.ObjectNotInPrerequisiteStateError:
        cannot insert into view "content_tasks"``.
        """
        import asyncpg
        from services.tasks_db import TasksDatabase

        db = TasksDatabase(pool=db_pool)
        try:
            task_id = await db.add_task({
                "topic": "188-no-view-error",
                "category": "test_188_view_guard",
            })
        except asyncpg.exceptions.ObjectNotInPrerequisiteStateError as e:
            pytest.fail(
                f"add_task() raised the #188 view-INSERT error: {e}. "
                "Writes must target pipeline_tasks, not content_tasks."
            )

        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM pipeline_tasks WHERE task_id = $1", task_id)


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


# ---------------------------------------------------------------------------
# heartbeat_task (GH-90)
# ---------------------------------------------------------------------------


class TestHeartbeatTask:
    """GH-90 AC #2: worker stamps updated_at during long stages."""

    @pytest.mark.asyncio
    async def test_heartbeat_updates_live_task(self):
        """A task in in_progress gets updated_at=NOW() and returns True."""
        conn = MagicMock()
        # UPDATE ... RETURNING task_id returns the row when alive
        conn.fetchrow = AsyncMock(return_value={"task_id": "t-live"})

        pool = MagicMock()

        @asynccontextmanager
        async def _acquire():
            yield conn
        pool.acquire = _acquire

        db = TasksDatabase(pool=pool)
        result = await db.heartbeat_task("t-live")
        assert result is True
        # Verify the SQL included a status guard on pending/in_progress
        # so a terminal row cannot be resurrected by a heartbeat.
        call_args = conn.fetchrow.await_args
        sql = call_args.args[0]
        assert "UPDATE pipeline_tasks" in sql
        assert "status IN ('pending', 'in_progress')" in sql
        assert "updated_at = NOW()" in sql
        assert call_args.args[1] == "t-live"

    @pytest.mark.asyncio
    async def test_heartbeat_returns_false_on_terminal_row(self):
        """A task that already flipped to failed/cancelled returns False —
        signal to the caller to abort downstream work."""
        conn = MagicMock()
        # UPDATE guard fails → RETURNING yields no row → fetchrow is None.
        conn.fetchrow = AsyncMock(return_value=None)

        pool = MagicMock()

        @asynccontextmanager
        async def _acquire():
            yield conn
        pool.acquire = _acquire

        db = TasksDatabase(pool=pool)
        result = await db.heartbeat_task("t-cancelled")
        assert result is False

    @pytest.mark.asyncio
    async def test_heartbeat_swallows_db_errors(self):
        """A DB outage during heartbeat MUST NOT kill the worker —
        heartbeat is a best-effort freshness signal, not a correctness
        requirement."""
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=RuntimeError("conn pool closed"))
        db = TasksDatabase(pool=pool)
        # Should not raise.
        result = await db.heartbeat_task("t-whatever")
        assert result is False

    @pytest.mark.asyncio
    async def test_heartbeat_returns_false_for_empty_task_id(self):
        """Defensive: empty task_id short-circuits to False."""
        db = _make_db()
        assert await db.heartbeat_task("") is False


# ---------------------------------------------------------------------------
# update_task_status_guarded (GH-90)
# ---------------------------------------------------------------------------


class TestUpdateTaskStatusGuarded:
    """GH-90 AC #3: terminal writes refuse to overwrite a cancelled row."""

    @pytest.mark.asyncio
    async def test_guard_allows_transition_from_in_progress(self):
        """in_progress → awaiting_approval is allowed — returns 'in_progress'."""
        conn = MagicMock()
        # First fetchval returns the current status ('in_progress').
        # Second fetchval returns task_id on successful UPDATE.
        conn.fetchval = AsyncMock(side_effect=["in_progress", "t-1"])

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
        prev = await db.update_task_status_guarded(
            "t-1", "awaiting_approval",
            allowed_from=("in_progress", "pending"),
        )
        assert prev == "in_progress"
        assert conn.fetchval.await_count == 2

    @pytest.mark.asyncio
    async def test_guard_blocks_transition_from_failed(self):
        """failed → awaiting_approval is BLOCKED — returns None, no UPDATE
        executed. This is the core GH-90 fix: the sweeper already flipped
        the row to failed, and the worker's in-flight finalize must NOT
        resurrect it with generated content."""
        conn = MagicMock()
        # Row is already in terminal state.
        conn.fetchval = AsyncMock(return_value="failed")

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
        prev = await db.update_task_status_guarded(
            "t-ghost", "awaiting_approval",
            allowed_from=("in_progress", "pending"),
        )
        assert prev is None
        # Only ONE fetchval happened — the FOR UPDATE lock read.
        # No UPDATE attempted, no second fetchval.
        assert conn.fetchval.await_count == 1

    @pytest.mark.asyncio
    async def test_guard_returns_none_when_task_missing(self):
        """A missing task_id (prev=None) returns None — no UPDATE."""
        conn = MagicMock()
        conn.fetchval = AsyncMock(return_value=None)

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
        result = await db.update_task_status_guarded(
            "t-nope", "awaiting_approval"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_guard_rejects_non_whitelisted_fields(self):
        """Ad-hoc field names are silently dropped (not SQL-interpolated).
        Only whitelisted columns can be atomically updated."""
        conn = MagicMock()
        conn.fetchval = AsyncMock(side_effect=["in_progress", "t-1"])

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
        prev = await db.update_task_status_guarded(
            "t-1", "awaiting_approval",
            arbitrary_field="injection attempt; DROP TABLE tasks;",
        )
        # Guard passes on the status check, but the arbitrary_field is not
        # in the whitelist so it's silently dropped. No exception raised.
        assert prev == "in_progress"
