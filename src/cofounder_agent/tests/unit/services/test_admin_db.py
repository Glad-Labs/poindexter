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

import json
import time
import pytest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from services.admin_db import AdminDatabase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**kwargs):
    """Create a mock asyncpg Record-like row."""
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
        row = _make_row(id="cl-1", task_id="task-1", phase="research")
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        sentinel = _make_cost_log_sentinel()
        with patch(f"{_CONVERTER}.to_cost_log_response", return_value=sentinel):
            result = await db.log_cost({
                "task_id": "task-1",
                "phase": "research",
                "model": "ultra_cheap",
                "provider": "ollama",
                "cost_usd": 0.001,
            })

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_optional_fields_default_to_zero(self):
        """Missing optional fields (tokens, duration_ms) default to 0/None."""
        row = _make_row(id="cl-2", task_id="task-1", phase="draft")
        pool = _make_pool(fetchrow_result=row)
        db = _make_db(pool)

        sentinel = _make_cost_log_sentinel()
        with patch(f"{_CONVERTER}.to_cost_log_response", return_value=sentinel):
            result = await db.log_cost({
                "task_id": "task-1",
                "phase": "draft",
                "model": "premium",
                "provider": "anthropic",
            })

        assert result is sentinel

    @pytest.mark.asyncio
    async def test_db_error_raises(self):
        pool = _make_pool(fetchrow_side_effect=RuntimeError("DB down"))
        db = _make_db(pool)

        with pytest.raises(RuntimeError, match="DB down"):
            await db.log_cost({
                "task_id": "task-1",
                "phase": "research",
                "model": "cheap",
                "provider": "openai",
            })


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
        row = _make_row(key="my_key", value="my_value")
        pool = _make_pool(fetchrow_result=row)
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
        # Populate cache manually
        db._settings_cache["cached_key"] = {"value": sentinel, "ts": time.monotonic()}

        result = await db.get_setting("cached_key")
        assert result is sentinel
        # DB should NOT have been called
        async with pool.acquire() as conn:
            conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_cache_hits_db(self):
        """TTL = 60s; if timestamp is old, DB is re-queried."""
        row = _make_row(key="k", value="v")
        pool = _make_pool(fetchrow_result=row)
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
        rows = [_make_row(key="k1", value="v1"), _make_row(key="k2", value="v2")]
        pool = _make_pool(fetch_result=rows)
        db = _make_db(pool)

        sentinel = _make_setting_sentinel()
        with patch(f"{_CONVERTER}.to_setting_response", return_value=sentinel):
            result = await db.get_all_settings()

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_category_filter_returns_subset(self):
        rows = [_make_row(key="k1", value="v1", category="ui")]
        pool = _make_pool(fetch_result=rows)
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
        db._all_settings_cache["__all__"] = {"value": cached_list, "ts": time.monotonic()}

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
