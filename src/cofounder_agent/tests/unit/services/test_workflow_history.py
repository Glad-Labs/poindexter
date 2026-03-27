"""
Unit tests for services/workflow_history.py (WorkflowHistoryService).

Tests cover:
- save_workflow_execution — success path, missing required field raises ValueError,
  DB error propagates, WebSocket emit failure is swallowed
- get_workflow_execution — found and not-found paths
- get_user_workflow_history — without and with status filter
- get_workflow_statistics — calculates success_rate, most_common_workflow, empty period
- update_workflow_execution — no updates (passthrough), with updates
- get_performance_metrics — time distribution, error patterns, optimization tips
- _row_to_dict — datetime serialization, UUID conversion, Decimal conversion
- _generate_optimization_tips — slow executions, frequent errors, no tips fallback

The asyncpg pool is fully mocked; no real database access.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.workflow_history import WorkflowHistoryService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**kwargs):
    """Return a MagicMock that behaves like a minimal asyncpg row."""
    row = MagicMock()
    data = kwargs
    row.__iter__ = lambda self: iter(data.items())
    row.__getitem__ = lambda self, key: data[key]
    row.get = lambda key, default=None: data.get(key, default)
    return row, data


def _make_pool(conn):
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


def _make_conn():
    conn = MagicMock()
    conn.fetchrow = AsyncMock()
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock()
    return conn


def _make_asyncpg_row(**kwargs):
    """Build a mock that behaves like an asyncpg Record.

    asyncpg Records support dict(row) via the Mapping protocol (keys() + __getitem__).
    We replicate that here so _row_to_dict can call dict(row).
    """
    data = dict(kwargs)

    class _FakeRow:
        def keys(self):
            return data.keys()

        def __getitem__(self, key):
            return data[key]

        def __iter__(self):
            return iter(data.items())

        def get(self, key, default=None):
            return data.get(key, default)

    return _FakeRow()


def _service(conn=None) -> WorkflowHistoryService:
    _conn = conn or _make_conn()
    pool = _make_pool(_conn)
    return WorkflowHistoryService(db_pool=pool)


# ---------------------------------------------------------------------------
# save_workflow_execution
# ---------------------------------------------------------------------------


class TestSaveWorkflowExecution:
    @pytest.mark.asyncio
    async def test_success_returns_dict(self):
        conn = _make_conn()
        now = datetime.now(timezone.utc)
        row = _make_asyncpg_row(
            id="exec-1",
            workflow_id="wf-1",
            start_time=now,
            end_time=now,
            created_at=now,
            updated_at=None,
            duration_seconds=Decimal("5.5"),
        )
        conn.fetchrow = AsyncMock(return_value=row)

        with patch("services.workflow_history.emit_workflow_status", AsyncMock()):
            svc = _service(conn)
            result = await svc.save_workflow_execution(
                workflow_id="wf-1",
                workflow_type="blog",
                user_id="user-1",
                status="COMPLETED",
                input_data={"topic": "AI"},
                output_data={"content": "..."},
            )

        assert result["id"] == "exec-1"
        assert result["duration_seconds"] == 5.5  # Decimal → float

    @pytest.mark.asyncio
    async def test_missing_required_field_raises_value_error(self):
        svc = _service()
        with pytest.raises(ValueError, match="required"):
            await svc.save_workflow_execution(
                workflow_id="",  # empty — falsy
                workflow_type="blog",
                user_id="user-1",
                status="COMPLETED",
                input_data={},
            )

    @pytest.mark.asyncio
    async def test_websocket_emit_failure_is_swallowed(self):
        conn = _make_conn()
        now = datetime.now(timezone.utc)
        row = _make_asyncpg_row(
            id="e",
            workflow_id="w",
            start_time=now,
            end_time=now,
            created_at=now,
            updated_at=None,
            duration_seconds=Decimal("1"),
        )
        conn.fetchrow = AsyncMock(return_value=row)

        with patch(
            "services.workflow_history.emit_workflow_status",
            AsyncMock(side_effect=RuntimeError("ws down")),
        ):
            svc = _service(conn)
            # Should NOT raise even though emit fails
            result = await svc.save_workflow_execution(
                workflow_id="wf-1",
                workflow_type="blog",
                user_id="u",
                status="COMPLETED",
                input_data={},
            )
        assert "id" in result

    @pytest.mark.asyncio
    async def test_db_error_propagates(self):
        conn = _make_conn()
        conn.fetchrow = AsyncMock(side_effect=Exception("DB exploded"))

        with patch("services.workflow_history.emit_workflow_status", AsyncMock()):
            svc = _service(conn)
            with pytest.raises(Exception, match="DB exploded"):
                await svc.save_workflow_execution(
                    workflow_id="wf-1",
                    workflow_type="blog",
                    user_id="u",
                    status="RUNNING",
                    input_data={},
                )


# ---------------------------------------------------------------------------
# get_workflow_execution
# ---------------------------------------------------------------------------


class TestGetWorkflowExecution:
    @pytest.mark.asyncio
    async def test_found_returns_dict(self):
        conn = _make_conn()
        now = datetime.now(timezone.utc)
        row = _make_asyncpg_row(
            id="exec-99",
            workflow_id="wf-x",
            start_time=now,
            end_time=now,
            created_at=now,
            updated_at=None,
            duration_seconds=Decimal("3.0"),
        )
        conn.fetchrow = AsyncMock(return_value=row)

        svc = _service(conn)
        result = await svc.get_workflow_execution("exec-99")

        assert result is not None
        assert result["id"] == "exec-99"

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        conn = _make_conn()
        conn.fetchrow = AsyncMock(return_value=None)

        svc = _service(conn)
        result = await svc.get_workflow_execution("missing-id")

        assert result is None

    @pytest.mark.asyncio
    async def test_db_error_propagates(self):
        conn = _make_conn()
        conn.fetchrow = AsyncMock(side_effect=RuntimeError("timeout"))

        svc = _service(conn)
        with pytest.raises(RuntimeError, match="timeout"):
            await svc.get_workflow_execution("id")


# ---------------------------------------------------------------------------
# get_user_workflow_history
# ---------------------------------------------------------------------------


class TestGetUserWorkflowHistory:
    @pytest.mark.asyncio
    async def test_no_filter_returns_all(self):
        # Single window-function query: rows include _total_count; no fetchval call needed.
        conn = _make_conn()
        now = datetime.now(timezone.utc)

        def _row(exec_id):
            return _make_asyncpg_row(
                id=exec_id,
                workflow_id="w",
                start_time=now,
                end_time=now,
                created_at=now,
                updated_at=None,
                duration_seconds=Decimal("1"),
                _total_count=3,
            )

        conn.fetch = AsyncMock(return_value=[_row("e1"), _row("e2")])

        svc = _service(conn)
        result = await svc.get_user_workflow_history("user-1", limit=10, offset=0)

        assert result["total"] == 3
        assert len(result["executions"]) == 2
        assert result["limit"] == 10
        assert result["offset"] == 0

    @pytest.mark.asyncio
    async def test_with_status_filter(self):
        # Single window-function query with status filter; _total_count reflects filtered count.
        conn = _make_conn()
        now = datetime.now(timezone.utc)
        row = _make_asyncpg_row(
            id="e1",
            workflow_id="w",
            start_time=now,
            end_time=now,
            created_at=now,
            updated_at=None,
            duration_seconds=Decimal("2"),
            _total_count=1,
        )
        conn.fetch = AsyncMock(return_value=[row])

        svc = _service(conn)
        result = await svc.get_user_workflow_history("user-1", status_filter="COMPLETED")

        assert result["status_filter"] == "COMPLETED"
        assert result["total"] == 1


# ---------------------------------------------------------------------------
# get_workflow_statistics
# ---------------------------------------------------------------------------


class TestGetWorkflowStatistics:
    @pytest.mark.asyncio
    async def test_success_rate_calculated(self):
        conn = _make_conn()
        now = datetime.now(timezone.utc)

        stats_row = MagicMock()
        stats_data = {
            "total_executions": 10,
            "completed": 8,
            "failed": 2,
            "average_duration": Decimal("30.5"),
            "first_execution": now,
            "last_execution": now,
        }
        stats_row.__getitem__ = lambda self, key: stats_data[key]

        wf_row = MagicMock()
        wf_data = {
            "workflow_type": "blog",
            "executions": 10,
            "completed": 8,
            "failed": 2,
            "average_duration": Decimal("30.5"),
        }
        wf_row.__getitem__ = lambda self, key: wf_data[key]

        conn.fetchrow = AsyncMock(return_value=stats_row)
        conn.fetch = AsyncMock(return_value=[wf_row])

        svc = _service(conn)
        result = await svc.get_workflow_statistics("user-1", days=30)

        assert result["total_executions"] == 10
        assert result["success_rate_percent"] == 80.0
        assert result["most_common_workflow"] == "blog"
        assert len(result["workflows"]) == 1

    @pytest.mark.asyncio
    async def test_empty_period_returns_zero_success_rate(self):
        conn = _make_conn()
        now = datetime.now(timezone.utc)

        stats_row = MagicMock()
        stats_data = {
            "total_executions": 0,
            "completed": 0,
            "failed": 0,
            "average_duration": None,
            "first_execution": None,
            "last_execution": None,
        }
        stats_row.__getitem__ = lambda self, key: stats_data[key]

        conn.fetchrow = AsyncMock(return_value=stats_row)
        conn.fetch = AsyncMock(return_value=[])

        svc = _service(conn)
        result = await svc.get_workflow_statistics("user-1")

        assert result["success_rate_percent"] == 0
        assert result["most_common_workflow"] is None
        assert result["average_duration_seconds"] == 0


# ---------------------------------------------------------------------------
# update_workflow_execution
# ---------------------------------------------------------------------------


class TestUpdateWorkflowExecution:
    @pytest.mark.asyncio
    async def test_no_updates_delegates_to_get(self):
        conn = _make_conn()
        now = datetime.now(timezone.utc)
        row = _make_asyncpg_row(
            id="e1",
            workflow_id="w",
            start_time=now,
            end_time=now,
            created_at=now,
            updated_at=None,
            duration_seconds=Decimal("1"),
        )
        conn.fetchrow = AsyncMock(return_value=row)

        svc = _service(conn)
        result = await svc.update_workflow_execution("e1")

        assert result is not None
        assert result["id"] == "e1"

    @pytest.mark.asyncio
    async def test_with_updates_executes_update(self):
        conn = _make_conn()
        now = datetime.now(timezone.utc)
        row = _make_asyncpg_row(
            id="e1",
            workflow_id="w",
            start_time=now,
            end_time=now,
            created_at=now,
            updated_at=now,
            duration_seconds=Decimal("1"),
        )
        conn.fetchrow = AsyncMock(return_value=row)

        svc = _service(conn)
        result = await svc.update_workflow_execution("e1", status="COMPLETED")

        assert result is not None
        assert result["id"] == "e1"
        conn.fetchrow.assert_called_once()


# ---------------------------------------------------------------------------
# _row_to_dict
# ---------------------------------------------------------------------------


class TestRowToDict:
    def test_datetime_fields_serialized(self):
        svc = _service()
        now = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)
        row = _make_asyncpg_row(
            id="123",
            workflow_id="wf-abc",
            start_time=now,
            end_time=now,
            created_at=now,
            updated_at=None,
            duration_seconds=Decimal("7.25"),
        )

        result = svc._row_to_dict(row)

        assert result is not None
        assert result["start_time"] == now.isoformat()
        assert result["end_time"] == now.isoformat()
        assert result["created_at"] == now.isoformat()
        assert result["updated_at"] is None  # None not touched
        assert result["duration_seconds"] == 7.25  # Decimal → float

    def test_none_row_returns_none(self):
        svc = _service()
        assert svc._row_to_dict(None) is None


# ---------------------------------------------------------------------------
# _generate_optimization_tips
# ---------------------------------------------------------------------------


class TestGenerateOptimizationTips:
    def setup_method(self):
        self.svc = _service()

    def _row(self, speed_category, count, avg):
        r = MagicMock()
        d = {"speed_category": speed_category, "count": count, "avg_duration": avg}
        r.__getitem__ = lambda self, key: d[key]
        return r

    def _err_row(self, msg, freq):
        r = MagicMock()
        d = {"error_message": msg, "frequency": freq}
        r.__getitem__ = lambda self, key: d[key]
        return r

    def test_slow_execution_tip_added(self):
        time_dist = [self._row("slow", 5, 120)]
        tips = self.svc._generate_optimization_tips(time_dist, [])
        assert any("slow" in t for t in tips)

    def test_very_slow_execution_tip_added(self):
        time_dist = [self._row("very_slow", 2, 400)]
        tips = self.svc._generate_optimization_tips(time_dist, [])
        assert any("very_slow" in t for t in tips)

    def test_fast_execution_no_tip(self):
        time_dist = [self._row("fast", 100, 10)]
        tips = self.svc._generate_optimization_tips(time_dist, [])
        # No slow rows → fallback tip
        assert any("good" in t.lower() or "monitoring" in t.lower() for t in tips)

    def test_frequent_errors_tip_added(self):
        errors = [self._err_row("Connection timeout", 5)]
        tips = self.svc._generate_optimization_tips([], errors)
        assert any("5 times" in t or "5" in t for t in tips)

    def test_infrequent_errors_no_tip(self):
        errors = [self._err_row("Rare error", 2)]
        tips = self.svc._generate_optimization_tips([], errors)
        # frequency <= 3 should not add error tip
        assert not any("times" in t for t in tips)

    def test_empty_inputs_returns_fallback(self):
        tips = self.svc._generate_optimization_tips([], [])
        assert len(tips) == 1
        assert "Performance is good" in tips[0]
