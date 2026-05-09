"""
Unit tests for services/content_db.py.

Tests cover:
- ContentDatabase.create_post — seo_keywords type coercion, tag_ids coercion, DB error
- ContentDatabase.get_post_by_slug — found / not found / DB error
- ContentDatabase.update_post — allowed columns, disallowed columns filtered, no valid columns
- ContentDatabase.get_all_categories — success / empty / DB error
- ContentDatabase.get_all_tags — success / empty / DB error
- ContentDatabase.get_author_by_name — found / not found / DB error
- ContentDatabase.create_quality_evaluation — success / raises on DB error
- ContentDatabase.create_quality_improvement_log — success / raises on DB error
- ContentDatabase.get_metrics — success, rates, DB error fallback
- ContentDatabase.create_orchestrator_training_data — success / raises on DB error

The asyncpg pool is fully mocked; no real database access.
ModelConverter methods are patched to identity functions.
"""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.content_db import ContentDatabase
from services.error_handler import DatabaseError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**kwargs):
    """Build a mock asyncpg Record-like object.

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
    return row


def _make_pool(
    fetchrow_result=None,
    fetch_result=None,
    fetchval_results=None,
    fetchrow_side_effect=None,
    fetch_side_effect=None,
):
    """Build a mock pool with an async context manager for acquire()."""
    conn = MagicMock()

    if fetchrow_side_effect:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_result)

    if fetch_side_effect:
        conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        conn.fetch = AsyncMock(return_value=fetch_result or [])

    # fetchval returns values from a list in sequence
    fetchval_values = list(fetchval_results or [])
    conn.fetchval = AsyncMock(side_effect=fetchval_values + [0] * 20)

    # execute and executemany must be AsyncMock so they can be awaited
    conn.execute = AsyncMock(return_value=None)
    conn.executemany = AsyncMock(return_value=None)

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


def _make_db(pool=None):
    return ContentDatabase(pool=pool or _make_pool())


# Patch ModelConverter so we don't need real schema objects
_CONVERTER_PATCH_BASE = "services.content_db.ModelConverter"


# ---------------------------------------------------------------------------
# create_post
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreatePost:
    @pytest.mark.asyncio
    async def test_success_returns_post_response(self):
        # Opaque row — create_post hands fetchrow's result straight to
        # the patched ModelConverter.to_post_response without reading
        # any column. The row's column shape is not under test (GH#337).
        pool = _make_pool(fetchrow_result=object())
        db = _make_db(pool)

        sentinel = object()
        with patch(f"{_CONVERTER_PATCH_BASE}.to_post_response", return_value=sentinel):
            result = await db.create_post({"title": "My Post", "slug": "my-post"})

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_seo_keywords_list_converted_to_string(self):
        """When seo_keywords is a list, it should be joined into a comma string."""
        # Opaque row — column shape not under test (see GH#337).
        opaque = object()
        pool = _make_pool(fetchrow_result=opaque)
        db = _make_db(pool)

        with patch(f"{_CONVERTER_PATCH_BASE}.to_post_response", return_value=opaque):
            # Should not raise — list coercion happens silently
            await db.create_post({"slug": "x", "seo_keywords": ["AI", "ML", "NLP"]})

        # Verify fetchrow was called (i.e., INSERT ran)
        conn = None
        async with pool.acquire() as c:
            conn = c
        conn.fetchrow.assert_awaited()

    @pytest.mark.asyncio
    async def test_tag_ids_string_converted_to_list(self):
        """When tag_ids is a single string, it should be wrapped in a list."""
        # Opaque row — column shape not under test (see GH#337).
        opaque = object()
        pool = _make_pool(fetchrow_result=opaque)
        db = _make_db(pool)

        with patch(f"{_CONVERTER_PATCH_BASE}.to_post_response", return_value=opaque):
            result = await db.create_post({"slug": "x", "tag_ids": "tag-uuid-1"})
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_row_returned_raises_database_error(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)

        with pytest.raises(DatabaseError):
            await db.create_post({"slug": "x", "title": "T"})

    @pytest.mark.asyncio
    async def test_db_error_raises_database_error(self):
        pool = _make_pool(fetchrow_side_effect=Exception("connection reset"))
        db = _make_db(pool)

        with pytest.raises(DatabaseError):
            await db.create_post({"slug": "x", "title": "T"})


# ---------------------------------------------------------------------------
# get_post_by_slug
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPostBySlug:
    @pytest.mark.asyncio
    async def test_found_returns_post_response(self):
        # Opaque row — get_post_by_slug hands fetchrow's result straight
        # to the patched converter without reading any column (GH#337).
        pool = _make_pool(fetchrow_result=object())
        db = _make_db(pool)

        sentinel = object()
        with patch(f"{_CONVERTER_PATCH_BASE}.to_post_response", return_value=sentinel):
            result = await db.get_post_by_slug("my-post")

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)

        with patch(f"{_CONVERTER_PATCH_BASE}.to_post_response", return_value=None):
            result = await db.get_post_by_slug("no-such-slug")

        assert result is None

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        result = await db.get_post_by_slug("any-slug")
        assert result is None


# ---------------------------------------------------------------------------
# update_post
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdatePost:
    @pytest.mark.asyncio
    async def test_valid_columns_update_succeeds(self):
        row = _make_row(id="p1", title="New Title", slug="new-slug")
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        result = await db.update_post("p1", {"title": "New Title"})  # type: ignore[arg-type]
        assert result is True

    @pytest.mark.asyncio
    async def test_disallowed_columns_filtered_out(self):
        """Only allowlisted columns should reach the SQL query."""
        row = _make_row(id="p1", title="T")
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        # 'injected_column' is not in _ALLOWED_POST_COLUMNS
        result = await db.update_post("p1", {"title": "T", "injected_column": "evil"})  # type: ignore[arg-type]
        assert result is True  # succeeds with valid columns

    @pytest.mark.asyncio
    async def test_no_valid_columns_returns_false(self):
        pool = _make_pool()
        db = _make_db(pool)

        result = await db.update_post("p1", {"invalid_col": "v", "also_bad": "x"})  # type: ignore[arg-type]
        assert result is False

    @pytest.mark.asyncio
    async def test_not_found_returns_false(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)

        result = await db.update_post("p1", {"title": "T"})  # type: ignore[arg-type]
        assert result is False

    @pytest.mark.asyncio
    async def test_db_error_returns_false(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        result = await db.update_post("p1", {"title": "T"})  # type: ignore[arg-type]
        assert result is False


# ---------------------------------------------------------------------------
# get_all_categories / get_all_tags
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAllCategories:
    @pytest.mark.asyncio
    async def test_returns_list_on_success(self):
        # Opaque rows — get_all_categories iterates fetch results and
        # hands each one to the patched converter without reading any
        # column (GH#337). Test asserts only on the row count.
        pool = _make_pool(fetch_result=[object(), object()])
        db = _make_db(pool)

        with patch(f"{_CONVERTER_PATCH_BASE}.to_category_response", side_effect=lambda r: r):
            result = await db.get_all_categories()

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_list(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        result = await db.get_all_categories()
        assert result == []

    @pytest.mark.asyncio
    async def test_no_categories_returns_empty_list(self):
        pool = _make_pool(fetch_result=[])
        db = _make_db(pool)

        with patch(f"{_CONVERTER_PATCH_BASE}.to_category_response", side_effect=lambda r: r):
            result = await db.get_all_categories()
        assert result == []


@pytest.mark.unit
class TestGetAllTags:
    @pytest.mark.asyncio
    async def test_returns_tags_on_success(self):
        # Opaque row — column shape not under test (GH#337).
        pool = _make_pool(fetch_result=[object()])
        db = _make_db(pool)

        with patch(f"{_CONVERTER_PATCH_BASE}.to_tag_response", side_effect=lambda r: r):
            result = await db.get_all_tags()

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_list(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        result = await db.get_all_tags()
        assert result == []


# ---------------------------------------------------------------------------
# get_author_by_name
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAuthorByName:
    @pytest.mark.asyncio
    async def test_found_returns_author(self):
        # Opaque row — get_author_by_name hands fetchrow's result
        # straight to the patched converter (GH#337).
        pool = _make_pool(fetchrow_result=object())
        db = _make_db(pool)

        sentinel = object()
        with patch(f"{_CONVERTER_PATCH_BASE}.to_author_response", return_value=sentinel):
            result = await db.get_author_by_name("Alice")

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)

        with patch(f"{_CONVERTER_PATCH_BASE}.to_author_response", return_value=None):
            result = await db.get_author_by_name("Nobody")

        assert result is None

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        result = await db.get_author_by_name("Alice")
        assert result is None


# ---------------------------------------------------------------------------
# create_quality_evaluation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateQualityEvaluation:
    @pytest.mark.asyncio
    async def test_success_returns_response(self):
        # Opaque row — create_quality_evaluation hands fetchrow's
        # result straight to the patched converter (GH#337).
        pool = _make_pool(fetchrow_result=object())
        db = _make_db(pool)

        sentinel = object()
        eval_data = {
            "content_id": "c-1",
            "overall_score": 80,
            "criteria": {"clarity": 85},
        }
        with patch(
            f"{_CONVERTER_PATCH_BASE}.to_quality_evaluation_response", return_value=sentinel
        ):
            result = await db.create_quality_evaluation(eval_data)

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError):
            await db.create_quality_evaluation(
                {"content_id": "c-1", "overall_score": 80, "criteria": {}}
            )


# ---------------------------------------------------------------------------
# create_quality_improvement_log
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateQualityImprovementLog:
    @pytest.mark.asyncio
    async def test_success_returns_response(self):
        # Opaque row — create_quality_improvement_log hands fetchrow's
        # result straight to the patched converter (GH#337).
        pool = _make_pool(fetchrow_result=object())
        db = _make_db(pool)

        sentinel = object()
        log_data = {"content_id": "c-1", "initial_score": 60.0, "improved_score": 80.0}
        with patch(
            f"{_CONVERTER_PATCH_BASE}.to_quality_improvement_log_response", return_value=sentinel
        ):
            result = await db.create_quality_improvement_log(log_data)

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_score_improvement_calculated(self):
        """Improvement = improved - initial should be stored correctly."""
        # Opaque row — column shape not under test (GH#337).
        pool = _make_pool(fetchrow_result=object())
        db = _make_db(pool)

        with patch(
            f"{_CONVERTER_PATCH_BASE}.to_quality_improvement_log_response",
            side_effect=lambda r: r,
        ):
            await db.create_quality_improvement_log(
                {"content_id": "c-1", "initial_score": 55.0, "improved_score": 78.0}
            )

        # Verify fetchrow was called — improvement calculation ran
        async with pool.acquire() as conn:
            conn.fetchrow.assert_awaited()

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError):
            await db.create_quality_improvement_log(
                {"content_id": "c-1", "initial_score": 55.0, "improved_score": 75.0}
            )


# ---------------------------------------------------------------------------
# get_metrics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMetrics:
    @pytest.mark.asyncio
    async def test_success_returns_metrics_response(self):
        # Data-flow test: production reads ``counts_row[<key>]`` for the
        # task counts + avg_seconds (content_db.py:508-522) and
        # ``cost_result["total"]`` for the cost roll-up (line 543), so
        # the strict row-faker is correct here. Seeding ONLY those keys
        # means a future refactor that adds a new column-read will
        # KeyError loudly instead of silently coasting on the outer
        # try/except — see GH#337.
        counts_row = _make_row(
            total_tasks=10,
            completed_tasks=8,
            failed_tasks=1,
            avg_seconds=45.0,
        )
        cost_row = _make_row(total=2.50)

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetchrow = AsyncMock(side_effect=[counts_row, cost_row])

        db = _make_db(pool)
        result = await db.get_metrics()
        # MetricsResponse should be returned with the seeded values
        assert result.totalTasks == 10
        assert result.completedTasks == 8
        assert result.failedTasks == 1
        assert result.totalCost == 2.50

    @pytest.mark.asyncio
    async def test_db_error_returns_zero_metrics(self):
        """DB errors return a zeroed MetricsResponse, not an exception."""
        pool = _make_pool(fetchval_results=[RuntimeError("DB down")])
        # Make fetchval raise on first call
        async with pool.acquire() as conn:
            conn.fetchval = AsyncMock(side_effect=RuntimeError("DB down"))

        db = _make_db(pool)
        result = await db.get_metrics()
        assert result.totalTasks == 0
        assert result.successRate == 0


# ---------------------------------------------------------------------------
# create_orchestrator_training_data
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateOrchestratorTrainingData:
    @pytest.mark.asyncio
    async def test_success_returns_response(self):
        # Opaque row — create_orchestrator_training_data hands
        # fetchrow's result straight to the patched converter (GH#337).
        pool = _make_pool(fetchrow_result=object())
        db = _make_db(pool)

        sentinel = object()
        train_data = {
            "execution_id": "exec-1",
            "user_request": "Write a blog post about AI",
            "quality_score": 85,
            "success": True,
            "tags": ["blog", "ai"],
        }
        with patch(
            f"{_CONVERTER_PATCH_BASE}.to_orchestrator_training_data_response",
            return_value=sentinel,
        ):
            result = await db.create_orchestrator_training_data(train_data)

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_tags_as_json_string_parsed(self):
        """tags can be a JSON string; should be parsed to list before insertion."""
        # Opaque row — column shape not under test (GH#337).
        pool = _make_pool(fetchrow_result=object())
        db = _make_db(pool)

        with patch(
            f"{_CONVERTER_PATCH_BASE}.to_orchestrator_training_data_response",
            side_effect=lambda r: r,
        ):
            result = await db.create_orchestrator_training_data(
                {
                    "execution_id": "exec-2",
                    "tags": json.dumps(["ai", "content"]),
                }
            )
        assert result is not None

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError):
            await db.create_orchestrator_training_data({"execution_id": "exec-3"})


# ---------------------------------------------------------------------------
# _cache_get / _cache_set — internal TTL cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCacheHelpers:
    def test_cache_miss_returns_none(self):
        db = _make_db()
        assert db._cache_get("missing_key") is None

    def test_cache_set_then_get_returns_value(self):
        db = _make_db()
        db._cache_set("my_key", [{"id": 1, "name": "cat"}])
        assert db._cache_get("my_key") == [{"id": 1, "name": "cat"}]

    def test_cache_set_stores_tuple_with_timestamp(self):
        """Internal representation is (value, monotonic timestamp)."""
        import time
        db = _make_db()
        before = time.monotonic()
        db._cache_set("key", "value")
        after = time.monotonic()

        entry = db._cache["key"]
        assert entry[0] == "value"
        assert before <= entry[1] <= after

    def test_cache_overwrite_updates_timestamp(self):
        """Setting a key twice should replace both the value AND advance the timestamp."""
        from unittest.mock import patch
        db = _make_db()
        # Use controlled timestamps instead of time.sleep which can be flaky
        # under parallel test execution.
        with patch("services.content_db.time") as mock_time:
            mock_time.monotonic = lambda: 1000.0
            db._cache_set("key", "v1")
            first_ts = db._cache["key"][1]

            mock_time.monotonic = lambda: 1005.0
            db._cache_set("key", "v2")
            second_ts = db._cache["key"][1]

            assert second_ts > first_ts
            assert db._cache["key"][0] == "v2"

    def test_cache_expires_after_ttl(self):
        """Entry older than _CACHE_TTL should return None."""
        import time
        from unittest.mock import patch
        db = _make_db()
        db._cache_set("key", "value")
        frozen_now = time.monotonic() + 61
        with patch("services.content_db.time") as mock_time:
            mock_time.monotonic = lambda: frozen_now
            assert db._cache_get("key") is None

    def test_cache_still_valid_within_ttl(self):
        import time
        from unittest.mock import patch
        db = _make_db()
        db._cache_set("key", "value")
        within_ttl = time.monotonic() + 30
        with patch("services.content_db.time") as mock_time:
            mock_time.monotonic = lambda: within_ttl
            assert db._cache_get("key") == "value"

    def test_cache_stores_multiple_keys_independently(self):
        db = _make_db()
        db._cache_set("categories", [{"id": 1}])
        db._cache_set("tags", [{"id": 2}])
        assert db._cache_get("categories") == [{"id": 1}]
        assert db._cache_get("tags") == [{"id": 2}]

    def test_init_creates_empty_cache(self):
        db = _make_db()
        assert db._cache == {}
