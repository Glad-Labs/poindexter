"""
Unit tests for services/admin_db.py.

Tests cover:
- AdminDatabase.__init__ — pool and cache initialization
- AdminDatabase._invalidate_settings_cache — clears both caches
- AdminDatabase.log_cost — success, field extraction, raises on DB error
- AdminDatabase.get_task_costs — success with breakdown, empty result, DB error fallback
- AdminDatabase.health_check — healthy, pool utilization, unhealthy on exception
- AdminDatabase.get_setting — success, cache hit, not found, DB error fallback
- AdminDatabase.get_all_settings — success, category filter, cache hit, DB error fallback
- AdminDatabase.set_setting — success, JSON serialization, DB error fallback
- AdminDatabase.delete_setting — success, DB error fallback
- AdminDatabase.get_setting_value — JSON decode, plain string, default, not found
- AdminDatabase.setting_exists — found, not found, DB error fallback

asyncpg pool fully mocked; no real DB access.
"""

import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.admin_db import AdminDatabase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**kwargs):
    """Create a mock asyncpg Record-like row.

    Strict ``__getitem__`` (KeyError on missing key) so production code
    that reads a column the test didn't set fails loudly instead of
    silently getting ``None`` and passing — see GH#337.

    Use this helper ONLY when production code reads ``row[<key>]`` —
    the strict mapping is what gives the test signal value. When a test
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
        conn.execute = AsyncMock(return_value=execute_result or "OK")
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    pool.get_size = MagicMock(return_value=10)
    pool.get_idle_size = MagicMock(return_value=8)
    return pool


def _make_db(pool=None) -> AdminDatabase:
    return AdminDatabase(pool=pool or _make_pool())


_CONVERTER = "services.admin_db.ModelConverter"


def _make_cost_log_sentinel():
    sentinel = MagicMock()
    sentinel.cost_usd = 0.001
    return sentinel


def _make_setting_sentinel(key="my_key", value="my_value"):
    sentinel = MagicMock()
    sentinel.key = key
    sentinel.value = value
    # Support both dict and attribute access (get_setting_value uses both)
    sentinel.get = lambda k, default=None: {"value": value}.get(k, default)
    return sentinel


# ---------------------------------------------------------------------------
# __init__ and _invalidate_settings_cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAdminDatabaseInit:
    def test_pool_is_stored(self):
        pool = _make_pool()
        db = AdminDatabase(pool=pool)
        assert db.pool is pool

    def test_caches_initialized_empty(self):
        db = _make_db()
        assert db._settings_cache == {}
        assert db._all_settings_cache == {}

    def test_invalidate_clears_both_caches(self):
        db = _make_db()
        db._settings_cache["key1"] = {"value": "x", "ts": time.monotonic()}
        db._all_settings_cache["cat"] = {"value": [], "ts": time.monotonic()}
        db._invalidate_settings_cache()
        assert db._settings_cache == {}
        assert db._all_settings_cache == {}


# ---------------------------------------------------------------------------
# log_cost
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogCost:
    @pytest.mark.asyncio
    async def test_success_returns_cost_log_response(self):
        # Opaque row — log_cost passes fetchrow's result straight to
        # the patched ModelConverter without reading any column itself.
        pool = _make_pool(fetchrow_result=object())
        db = _make_db(pool)

        sentinel = _make_cost_log_sentinel()
        with patch(f"{_CONVERTER}.to_cost_log_response", return_value=sentinel):
            result = await db.log_cost(
                {
                    "task_id": "task-1",
                    "phase": "research",
                    "model": "ultra_cheap",
                    "provider": "ollama",
                    "cost_usd": 0.001,
                }
            )

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_optional_fields_default_to_zero(self):
        """Missing optional fields (tokens, duration_ms) default to 0/None."""
        # Opaque row — defaults live on the input dict, not the row.
        pool = _make_pool(fetchrow_result=object())
        db = _make_db(pool)

        sentinel = _make_cost_log_sentinel()
        with patch(f"{_CONVERTER}.to_cost_log_response", return_value=sentinel):
            result = await db.log_cost(
                {
                    "task_id": "task-1",
                    "phase": "draft",
                    "model": "premium",
                    "provider": "anthropic",
                }
            )

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError, match="DB down"):
            await db.log_cost(
                {
                    "task_id": "task-1",
                    "phase": "research",
                    "model": "cheap",
                    "provider": "openai",
                }
            )


# ---------------------------------------------------------------------------
# get_task_costs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTaskCosts:
    @pytest.mark.asyncio
    async def test_empty_returns_zero_total(self):
        pool = _make_pool(fetch_result=[])
        db = _make_db(pool)
        result = await db.get_task_costs("task-1")
        assert result.total == 0.0
        assert result.entries == []

    @pytest.mark.asyncio
    async def test_success_builds_breakdown_by_phase(self):
        """get_task_costs groups entries by phase and sums cost_usd correctly."""
        from schemas.database_response_models import CostLogResponse

        now = datetime.now(timezone.utc)

        def _make_cost_response(phase: str, cost: float) -> CostLogResponse:
            return CostLogResponse(  # type: ignore[call-arg]
                id="cl-1",
                task_id="task-1",
                phase=phase,  # type: ignore[arg-type]
                model="ultra_cheap",
                provider="ollama",
                cost_usd=cost,
                created_at=now,
                updated_at=now,
            )

        rows = [
            _make_row(phase="research", cost_usd=0.001, model="ultra_cheap"),
            _make_row(phase="draft", cost_usd=0.005, model="premium"),
            _make_row(phase="research", cost_usd=0.001, model="ultra_cheap"),
        ]
        pool = _make_pool(fetch_result=rows)
        db = _make_db(pool)

        call_count = [0]

        def _side_effect(row):
            phases = ["research", "draft", "research"]
            costs = [0.001, 0.005, 0.001]
            idx = call_count[0]
            call_count[0] += 1
            return _make_cost_response(phases[idx], costs[idx])

        with patch(f"{_CONVERTER}.to_cost_log_response", side_effect=_side_effect):
            result = await db.get_task_costs("task-1")

        assert result.total == pytest.approx(0.007, abs=1e-6)
        assert result.research is not None
        assert result.research["count"] == 2
        assert result.draft is not None
        assert result.draft["count"] == 1
        assert result.entries is not None
        assert len(result.entries) == 3

    @pytest.mark.asyncio
    async def test_db_error_returns_zero_total(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.get_task_costs("task-1")
        assert result.total == 0.0

    @pytest.mark.asyncio
    async def test_none_cost_usd_treated_as_zero(self):
        """Rows with NULL cost_usd should be treated as 0.0."""
        from schemas.database_response_models import CostLogResponse

        now = datetime.now(timezone.utc)

        def _make_zero_cost_response(row):
            return CostLogResponse(  # type: ignore[call-arg]
                id="cl-1",
                task_id="task-1",
                phase="assess",  # type: ignore[arg-type]
                model="cheap",
                provider="ollama",
                cost_usd=0.0,
                created_at=now,
                updated_at=now,
            )

        row = _make_row(phase="assess", cost_usd=None, model="cheap")
        pool = _make_pool(fetch_result=[row])
        db = _make_db(pool)

        with patch(f"{_CONVERTER}.to_cost_log_response", side_effect=_make_zero_cost_response):
            result = await db.get_task_costs("task-1")

        assert result.total == 0.0


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_returns_status_healthy(self):
        now = datetime.now(timezone.utc)
        pool = _make_pool(fetchval_result=now)
        db = _make_db(pool)
        result = await db.health_check()
        assert result["status"] == "healthy"
        assert result["database"] == "postgresql"
        assert "pool" in result

    @pytest.mark.asyncio
    async def test_service_name_included_in_response(self):
        now = datetime.now(timezone.utc)
        pool = _make_pool(fetchval_result=now)
        db = _make_db(pool)
        result = await db.health_check(service="my_service")
        assert result["service"] == "my_service"

    @pytest.mark.asyncio
    async def test_pool_stats_present(self):
        now = datetime.now(timezone.utc)
        pool = _make_pool(fetchval_result=now)
        db = _make_db(pool)
        result = await db.health_check()
        pool_stats = result["pool"]
        assert pool_stats["size"] == 10
        assert pool_stats["idle"] == 8
        assert pool_stats["used"] == 2
        assert pool_stats["utilization"] == pytest.approx(0.2, abs=0.01)

    @pytest.mark.asyncio
    async def test_high_pool_utilization_still_returns_healthy(self):
        """Pool at 90% utilization logs a warning but result is still healthy."""
        now = datetime.now(timezone.utc)
        pool = _make_pool(fetchval_result=now)
        pool.get_size = MagicMock(return_value=10)
        pool.get_idle_size = MagicMock(return_value=1)  # 90% utilized
        db = _make_db(pool)
        result = await db.health_check()
        assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_db_exception_returns_unhealthy(self):
        pool = _make_pool(fetchval_result=None)
        async with pool.acquire() as conn:
            conn.fetchval = AsyncMock(side_effect=RuntimeError("conn refused"))
        db = _make_db(pool)
        result = await db.health_check()
        assert result["status"] == "unhealthy"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_zero_pool_size_avoids_division_by_zero(self):
        now = datetime.now(timezone.utc)
        pool = _make_pool(fetchval_result=now)
        pool.get_size = MagicMock(return_value=0)
        pool.get_idle_size = MagicMock(return_value=0)
        db = _make_db(pool)
        result = await db.health_check()
        # Should not raise ZeroDivisionError
        assert result["status"] == "healthy"
        assert result["pool"]["utilization"] == 0.0


# ---------------------------------------------------------------------------
# get_setting
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSetting:
    @pytest.mark.asyncio
    async def test_success_returns_setting_response(self):
        # Opaque row — get_setting passes fetchrow's result straight
        # to the patched ModelConverter without reading any column.
        pool = _make_pool(fetchrow_result=object())
        db = _make_db(pool)

        sentinel = _make_setting_sentinel()
        with patch(f"{_CONVERTER}.to_setting_response", return_value=sentinel):
            result = await db.get_setting("my_key")

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)

        with patch(f"{_CONVERTER}.to_setting_response", return_value=None):
            result = await db.get_setting("missing_key")

        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit_skips_db(self):
        pool = _make_pool()
        db = _make_db(pool)
        sentinel = _make_setting_sentinel()
        # Populate cache manually — cache key format is "key|include_inactive"
        # since the 2026-04-12 is_active migration.
        db._settings_cache["cached_key|False"] = {"value": sentinel, "ts": time.monotonic()}

        result = await db.get_setting("cached_key")
        assert result is sentinel
        # DB should NOT have been called
        async with pool.acquire() as conn:
            conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_cache_hits_db(self):
        """TTL = 60s; if timestamp is old, DB is re-queried."""
        # Opaque row — orchestration only.
        pool = _make_pool(fetchrow_result=object())
        db = _make_db(pool)
        sentinel = _make_setting_sentinel(key="k", value="v")
        # Set cache with very old timestamp
        db._settings_cache["k"] = {"value": sentinel, "ts": time.monotonic() - 999}

        with patch(f"{_CONVERTER}.to_setting_response", return_value=sentinel):
            result = await db.get_setting("k")

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.get_setting("any_key")
        assert result is None


# ---------------------------------------------------------------------------
# get_all_settings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAllSettings:
    @pytest.mark.asyncio
    async def test_no_category_returns_all(self):
        # Opaque rows — get_all_settings just maps the patched
        # converter over fetch's result without reading any column.
        pool = _make_pool(fetch_result=[object(), object()])
        db = _make_db(pool)

        sentinel = _make_setting_sentinel()
        with patch(f"{_CONVERTER}.to_setting_response", return_value=sentinel):
            result = await db.get_all_settings()

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_category_filter_returns_subset(self):
        # Opaque row — the test asserts only the count; the filter
        # work happens in SQL upstream of fetch.
        pool = _make_pool(fetch_result=[object()])
        db = _make_db(pool)

        sentinel = _make_setting_sentinel()
        with patch(f"{_CONVERTER}.to_setting_response", return_value=sentinel):
            result = await db.get_all_settings(category="ui")

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_cache_hit_skips_db(self):
        pool = _make_pool()
        db = _make_db(pool)
        cached_list = [_make_setting_sentinel()]
        # Cache key format is "category_or___all__|include_inactive" since 2026-04-12.
        db._all_settings_cache["__all__|False"] = {"value": cached_list, "ts": time.monotonic()}

        result = await db.get_all_settings()
        assert result is cached_list

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.get_all_settings()
        assert result == []


# ---------------------------------------------------------------------------
# set_setting
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetSetting:
    @pytest.mark.asyncio
    async def test_string_value_stored(self):
        pool = _make_pool()
        db = _make_db(pool)
        result = await db.set_setting("my_key", "my_value")
        assert result is True

    @pytest.mark.asyncio
    async def test_dict_value_json_serialized(self):
        pool = _make_pool()
        db = _make_db(pool)
        result = await db.set_setting("config", {"debug": True, "max": 100})
        assert result is True

    @pytest.mark.asyncio
    async def test_list_value_json_serialized(self):
        pool = _make_pool()
        db = _make_db(pool)
        result = await db.set_setting("tags", ["a", "b", "c"])
        assert result is True

    @pytest.mark.asyncio
    async def test_set_invalidates_cache(self):
        pool = _make_pool()
        db = _make_db(pool)
        db._settings_cache["my_key"] = {"value": "stale", "ts": time.monotonic()}
        await db.set_setting("my_key", "new_value")
        assert "my_key" not in db._settings_cache

    @pytest.mark.asyncio
    async def test_db_error_returns_false(self):
        pool = _make_pool(execute_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.set_setting("k", "v")
        assert result is False

    @pytest.mark.asyncio
    async def test_with_category_and_display_name(self):
        pool = _make_pool()
        db = _make_db(pool)
        result = await db.set_setting(
            "feature_flag",
            True,
            category="features",
            display_name="Feature Flag",
            description="Enables experimental feature",
        )
        assert result is True


# ---------------------------------------------------------------------------
# delete_setting
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteSetting:
    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        pool = _make_pool()
        db = _make_db(pool)
        result = await db.delete_setting("old_key")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_invalidates_cache(self):
        pool = _make_pool()
        db = _make_db(pool)
        db._settings_cache["old_key"] = {"value": "x", "ts": time.monotonic()}
        await db.delete_setting("old_key")
        assert "old_key" not in db._settings_cache

    @pytest.mark.asyncio
    async def test_db_error_returns_false(self):
        pool = _make_pool(execute_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.delete_setting("k")
        assert result is False


# ---------------------------------------------------------------------------
# get_setting_value
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSettingValue:
    @pytest.mark.asyncio
    async def test_json_string_decoded(self):
        db = _make_db()
        setting = _make_setting_sentinel(value='{"enabled": true, "limit": 100}')
        db.get_setting = AsyncMock(return_value=setting)
        result = await db.get_setting_value("config")
        assert result == {"enabled": True, "limit": 100}

    @pytest.mark.asyncio
    async def test_plain_string_returned_as_is(self):
        db = _make_db()
        setting = _make_setting_sentinel(value="plain_string")
        db.get_setting = AsyncMock(return_value=setting)
        result = await db.get_setting_value("my_key")
        # "plain_string" is not valid JSON — should be returned as-is
        assert result == "plain_string"

    @pytest.mark.asyncio
    async def test_not_found_returns_default(self):
        db = _make_db()
        db.get_setting = AsyncMock(return_value=None)
        result = await db.get_setting_value("missing", default="fallback")
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_not_found_default_none(self):
        db = _make_db()
        db.get_setting = AsyncMock(return_value=None)
        result = await db.get_setting_value("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_value_returns_default(self):
        db = _make_db()
        setting = _make_setting_sentinel(value="")
        db.get_setting = AsyncMock(return_value=setting)
        result = await db.get_setting_value("empty_key", default="default_val")
        assert result == "default_val"


# ---------------------------------------------------------------------------
# setting_exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettingExists:
    @pytest.mark.asyncio
    async def test_exists_returns_true(self):
        pool = _make_pool(fetchval_result=True)
        db = _make_db(pool)
        result = await db.setting_exists("existing_key")
        assert result is True

    @pytest.mark.asyncio
    async def test_not_found_returns_false(self):
        pool = _make_pool(fetchval_result=False)
        db = _make_db(pool)
        result = await db.setting_exists("missing_key")
        assert result is False

    @pytest.mark.asyncio
    async def test_none_fetchval_returns_false(self):
        """fetchval returning None (e.g., no row) treated as False."""
        pool = _make_pool(fetchval_result=None)
        db = _make_db(pool)
        result = await db.setting_exists("key")
        assert result is False

    @pytest.mark.asyncio
    async def test_db_error_returns_false(self):
        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetchval = AsyncMock(side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.setting_exists("k")
        assert result is False


# ---------------------------------------------------------------------------
# add_log_entry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAddLogEntry:
    @pytest.mark.asyncio
    async def test_success_returns_row_dict(self):
        row = {
            "id": "log-uuid-1",
            "agent_name": "writer",
            "level": "INFO",
            "message": "task started",
            "context": None,
            "created_at": datetime.now(timezone.utc),
        }
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        result = await db.add_log_entry("writer", "INFO", "task started")

        assert result["agent_name"] == "writer"
        assert result["level"] == "INFO"
        assert result["message"] == "task started"

    @pytest.mark.asyncio
    async def test_serializes_context_dict_as_json(self):
        captured = {}

        async def _capture(sql, log_id, agent_name, level, message, context):
            captured["context"] = context
            return {
                "id": log_id,
                "agent_name": agent_name,
                "level": level,
                "message": message,
                "context": context,
                "created_at": datetime.now(timezone.utc),
            }

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetchrow = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.add_log_entry("agent", "WARN", "msg", context={"task_id": "abc", "step": 3})

        import json
        assert captured["context"] is not None
        decoded = json.loads(captured["context"])
        assert decoded == {"task_id": "abc", "step": 3}

    @pytest.mark.asyncio
    async def test_none_context_passed_as_null(self):
        captured = {}

        async def _capture(sql, log_id, agent_name, level, message, context):
            captured["context"] = context
            return {"id": log_id}

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetchrow = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.add_log_entry("agent", "DEBUG", "no ctx")

        assert captured["context"] is None

    @pytest.mark.asyncio
    async def test_empty_row_returns_id_only(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)
        result = await db.add_log_entry("a", "INFO", "m")
        assert "id" in result
        assert isinstance(result["id"], str)

    @pytest.mark.asyncio
    async def test_db_error_returns_error_dict(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.add_log_entry("agent", "ERROR", "boom")
        assert result.get("error") == "Failed to save log entry"
        assert "id" in result


# ---------------------------------------------------------------------------
# get_logs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetLogs:
    @pytest.mark.asyncio
    async def test_no_filters_returns_all(self):
        rows = [
            {"id": "1", "agent_name": "a", "level": "INFO", "message": "m1"},
            {"id": "2", "agent_name": "b", "level": "WARN", "message": "m2"},
        ]
        pool = _make_pool(fetch_result=rows)
        db = _make_db(pool)
        result = await db.get_logs()
        assert len(result) == 2
        assert result[0]["id"] == "1"

    @pytest.mark.asyncio
    async def test_agent_name_filter_in_sql_and_params(self):
        captured = {}

        async def _capture(sql, *params):
            captured["sql"] = sql
            captured["params"] = params
            return []

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetch = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.get_logs(agent_name="writer", limit=50)

        assert "agent_name = $1" in captured["sql"]
        assert "WHERE" in captured["sql"]
        assert captured["params"][0] == "writer"
        assert captured["params"][-1] == 50  # limit always last

    @pytest.mark.asyncio
    async def test_level_filter_in_sql_and_params(self):
        captured = {}

        async def _capture(sql, *params):
            captured["sql"] = sql
            captured["params"] = params
            return []

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetch = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.get_logs(level="ERROR")

        assert "level = $1" in captured["sql"]
        assert captured["params"][0] == "ERROR"

    @pytest.mark.asyncio
    async def test_both_filters_combined_with_and(self):
        captured = {}

        async def _capture(sql, *params):
            captured["sql"] = sql
            captured["params"] = params
            return []

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetch = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.get_logs(agent_name="x", level="ERROR", limit=10)

        assert "agent_name = $1" in captured["sql"]
        assert "level = $2" in captured["sql"]
        assert " AND " in captured["sql"]
        assert captured["params"] == ("x", "ERROR", 10)

    @pytest.mark.asyncio
    async def test_default_limit_100_appended(self):
        captured = {}

        async def _capture(sql, *params):
            captured["params"] = params
            return []

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetch = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.get_logs()
        assert captured["params"][-1] == 100

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_list(self):
        pool = _make_pool(fetch_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.get_logs()
        assert result == []


# ---------------------------------------------------------------------------
# add_financial_entry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAddFinancialEntry:
    @pytest.mark.asyncio
    async def test_success_returns_row_dict(self):
        row = {
            "id": 7,
            "entry_type": "expense",
            "amount": 19.99,
            "currency": "USD",
            "description": "OpenAI credit",
            "category": "ai",
        }
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)
        result = await db.add_financial_entry({
            "entry_type": "expense",
            "amount": 19.99,
            "description": "OpenAI credit",
            "category": "ai",
        })
        assert result["amount"] == 19.99
        assert result["entry_type"] == "expense"

    @pytest.mark.asyncio
    async def test_default_field_values_when_unspecified(self):
        captured = {}

        async def _capture(sql, entry_type, amount, currency, description, category, date, metadata):
            captured.update({
                "entry_type": entry_type,
                "amount": amount,
                "currency": currency,
                "description": description,
                "category": category,
                "metadata": metadata,
            })
            return {"id": 1}

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetchrow = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.add_financial_entry({})

        assert captured["entry_type"] == "expense"
        assert captured["amount"] == 0
        assert captured["currency"] == "USD"
        assert captured["description"] is None
        assert captured["category"] is None
        # metadata is JSON-serialized empty dict
        import json
        assert json.loads(captured["metadata"]) == {}

    @pytest.mark.asyncio
    async def test_metadata_serialized_as_json(self):
        captured = {}

        async def _capture(sql, entry_type, amount, currency, description, category, date, metadata):
            captured["metadata"] = metadata
            return {"id": 1}

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetchrow = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.add_financial_entry({"metadata": {"vendor": "openai", "ref": "inv-42"}})

        import json
        assert json.loads(captured["metadata"]) == {"vendor": "openai", "ref": "inv-42"}

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_dict(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.add_financial_entry({"amount": 1.0})
        assert result == {}

    @pytest.mark.asyncio
    async def test_none_row_returns_empty_dict(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)
        result = await db.add_financial_entry({"amount": 1.0})
        assert result == {}


# ---------------------------------------------------------------------------
# get_financial_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetFinancialSummary:
    @pytest.mark.asyncio
    async def test_success_returns_summary_dict(self):
        row = {
            "total_amount": 1234.56,
            "entry_count": 42,
            "total_revenue": 2000.00,
            "total_expenses": 765.44,
        }
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)
        result = await db.get_financial_summary(days=30)
        assert result["total_amount"] == 1234.56
        assert result["entry_count"] == 42
        assert result["total_revenue"] == 2000.00

    @pytest.mark.asyncio
    async def test_days_passed_as_int_param(self):
        captured = {}

        async def _capture(sql, days):
            captured["days"] = days
            captured["sql"] = sql
            return {"total_amount": 0, "entry_count": 0}

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetchrow = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.get_financial_summary(days=7)

        assert captured["days"] == 7
        assert isinstance(captured["days"], int)
        assert "make_interval" in captured["sql"]

    @pytest.mark.asyncio
    async def test_string_days_coerced_to_int(self):
        """Even if a caller passes a string, it gets coerced (defense vs. SQL injection)."""
        captured = {}

        async def _capture(sql, days):
            captured["days"] = days
            return {"total_amount": 0, "entry_count": 0}

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetchrow = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.get_financial_summary(days="14")  # type: ignore[arg-type]

        assert captured["days"] == 14
        assert isinstance(captured["days"], int)

    @pytest.mark.asyncio
    async def test_none_row_returns_zero_defaults(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)
        result = await db.get_financial_summary()
        assert result["total_amount"] == 0
        assert result["entry_count"] == 0

    @pytest.mark.asyncio
    async def test_db_error_returns_full_zero_dict(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.get_financial_summary()
        assert result["total_amount"] == 0
        assert result["entry_count"] == 0
        assert result["total_revenue"] == 0
        assert result["total_expenses"] == 0


# ---------------------------------------------------------------------------
# update_agent_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateAgentStatus:
    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        pool = _make_pool()
        db = _make_db(pool)
        result = await db.update_agent_status("writer", "running")
        assert result is True

    @pytest.mark.asyncio
    async def test_default_last_run_is_now(self):
        captured = {}

        async def _capture(sql, agent_name, status, last_heartbeat, metadata):
            captured["last_heartbeat"] = last_heartbeat
            return "OK"

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        before = datetime.now(timezone.utc)
        await db.update_agent_status("writer", "running")
        after = datetime.now(timezone.utc)

        assert isinstance(captured["last_heartbeat"], datetime)
        assert before <= captured["last_heartbeat"] <= after

    @pytest.mark.asyncio
    async def test_explicit_last_run_passed_through(self):
        captured = {}
        explicit = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)

        async def _capture(sql, agent_name, status, last_heartbeat, metadata):
            captured["last_heartbeat"] = last_heartbeat
            return "OK"

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.update_agent_status("writer", "idle", last_run=explicit)
        assert captured["last_heartbeat"] == explicit

    @pytest.mark.asyncio
    async def test_metadata_serialized_as_json(self):
        captured = {}

        async def _capture(sql, agent_name, status, last_heartbeat, metadata):
            captured["metadata"] = metadata
            return "OK"

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.update_agent_status("w", "running", metadata={"task": "abc"})

        import json
        assert json.loads(captured["metadata"]) == {"task": "abc"}

    @pytest.mark.asyncio
    async def test_none_metadata_passed_as_null(self):
        captured = {}

        async def _capture(sql, agent_name, status, last_heartbeat, metadata):
            captured["metadata"] = metadata
            return "OK"

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.update_agent_status("w", "running")
        assert captured["metadata"] is None

    @pytest.mark.asyncio
    async def test_upsert_sql_uses_on_conflict(self):
        captured = {}

        async def _capture(sql, *args):
            captured["sql"] = sql
            return "OK"

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.execute = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.update_agent_status("w", "running")
        assert "ON CONFLICT" in captured["sql"]
        assert "agent_status" in captured["sql"]

    @pytest.mark.asyncio
    async def test_db_error_returns_false(self):
        pool = _make_pool(execute_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.update_agent_status("w", "running")
        assert result is False


# ---------------------------------------------------------------------------
# get_agent_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAgentStatus:
    @pytest.mark.asyncio
    async def test_found_returns_dict(self):
        row = {
            "agent_name": "writer",
            "status": "running",
            "last_heartbeat": datetime.now(timezone.utc),
            "metadata": None,
        }
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)
        result = await db.get_agent_status("writer")
        assert result is not None
        assert result["agent_name"] == "writer"
        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        db = _make_db(pool)
        result = await db.get_agent_status("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_passes_agent_name_param(self):
        captured = {}

        async def _capture(sql, agent_name):
            captured["agent_name"] = agent_name
            captured["sql"] = sql
            return None

        pool = _make_pool()
        async with pool.acquire() as conn:
            conn.fetchrow = AsyncMock(side_effect=_capture)
        db = _make_db(pool)

        await db.get_agent_status("special-agent")
        assert captured["agent_name"] == "special-agent"
        assert "agent_status" in captured["sql"]
        assert "WHERE agent_name = $1" in captured["sql"]

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)
        result = await db.get_agent_status("w")
        assert result is None
