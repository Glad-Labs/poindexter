"""
Unit tests for services/metrics_service.py

Tests TaskMetrics and MetricsService:
- Phase recording (start/end) captures duration and status
- LLM call recording accumulates token/cost totals
- Error recording stores entries and is reflected in get_error_count
- Error rate calculation from LLM call statuses
- get_total_duration_ms includes queue wait + phases
- get_phase_breakdown returns dict of phase → duration
- to_dict serialises all data into correct shape
- MetricsService.save_metrics handles no-database gracefully
- MetricsService.update_metrics / get_metric round-trip
- get_metrics_service returns singleton

All tests are pure — no DB or network I/O (database dep is mocked).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.metrics_service import MetricsService, TaskMetrics, get_metrics_service

# ---------------------------------------------------------------------------
# TaskMetrics — phase recording
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskMetricsPhaseRecording:
    def test_record_phase_start_returns_float(self):
        m = TaskMetrics("task-1")
        t = m.record_phase_start("research")
        assert isinstance(t, float)
        assert t > 0

    def test_record_phase_end_stores_entry(self):
        m = TaskMetrics("task-1")
        t = m.record_phase_start("research")
        m.record_phase_end("research", t, status="success")
        assert "research" in m.phases
        assert m.phases["research"]["status"] == "success"

    def test_phase_duration_ms_is_non_negative(self):
        m = TaskMetrics("task-1")
        t = m.record_phase_start("draft")
        m.record_phase_end("draft", t)
        assert m.phases["draft"]["duration_ms"] >= 0

    def test_record_phase_end_with_error_adds_to_errors(self):
        m = TaskMetrics("task-1")
        t = m.record_phase_start("assess")
        m.record_phase_end("assess", t, status="error", error="LLM timeout")
        assert "error" in m.phases["assess"]
        assert m.phases["assess"]["error"] == "LLM timeout"
        assert m.get_error_count() == 1

    def test_multiple_phases_stored_independently(self):
        m = TaskMetrics("task-1")
        for phase in ("research", "draft", "assess"):
            t = m.record_phase_start(phase)
            m.record_phase_end(phase, t)
        assert len(m.phases) == 3


# ---------------------------------------------------------------------------
# TaskMetrics — LLM call recording
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskMetricsLlmCalls:
    def test_record_llm_call_appends_entry(self):
        m = TaskMetrics("task-2")
        m.record_llm_call(
            phase="research",
            model="gpt-4",
            provider="openai",
            tokens_in=100,
            tokens_out=50,
            cost_usd=0.005,
            duration_ms=300.0,
        )
        assert len(m.llm_calls) == 1

    def test_llm_call_accumulates_tokens(self):
        m = TaskMetrics("task-2")
        m.record_llm_call(
            phase="research",
            model="gpt-4",
            provider="openai",
            tokens_in=200,
            tokens_out=100,
            cost_usd=0.01,
            duration_ms=200.0,
        )
        m.record_llm_call(
            phase="draft",
            model="gpt-4",
            provider="openai",
            tokens_in=300,
            tokens_out=150,
            cost_usd=0.015,
            duration_ms=250.0,
        )
        assert m.total_tokens_in == 500
        assert m.total_tokens_out == 250

    def test_llm_call_accumulates_cost(self):
        m = TaskMetrics("task-2")
        m.record_llm_call(
            phase="research",
            model="gpt-4",
            provider="openai",
            tokens_in=100,
            tokens_out=50,
            cost_usd=0.005,
            duration_ms=100.0,
        )
        m.record_llm_call(
            phase="draft",
            model="gpt-4",
            provider="openai",
            tokens_in=100,
            tokens_out=50,
            cost_usd=0.005,
            duration_ms=100.0,
        )
        assert abs(m.total_cost_usd - 0.010) < 1e-9

    def test_llm_call_with_error_stores_error_field(self):
        m = TaskMetrics("task-2")
        m.record_llm_call(
            phase="research",
            model="gpt-4",
            provider="openai",
            tokens_in=0,
            tokens_out=0,
            cost_usd=0,
            duration_ms=100.0,
            status="error",
            error="rate limited",
        )
        assert m.llm_calls[0]["error"] == "rate limited"

    def test_llm_call_total_tokens_field(self):
        m = TaskMetrics("task-2")
        m.record_llm_call(
            phase="research",
            model="gpt-4",
            provider="openai",
            tokens_in=100,
            tokens_out=50,
            cost_usd=0,
            duration_ms=100.0,
        )
        assert m.llm_calls[0]["total_tokens"] == 150


# ---------------------------------------------------------------------------
# TaskMetrics — error recording
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskMetricsErrors:
    def test_record_error_increments_count(self):
        m = TaskMetrics("task-3")
        m.record_error("research", "TimeoutError", "LLM timed out after 30s")
        m.record_error("draft", "APIError", "Provider unavailable")
        assert m.get_error_count() == 2

    def test_record_error_stores_entry(self):
        m = TaskMetrics("task-3")
        m.record_error("assess", "ValueError", "bad response", retry_count=2)
        assert m.errors[0]["phase"] == "assess"
        assert m.errors[0]["error_type"] == "ValueError"
        assert m.errors[0]["retry_count"] == 2

    def test_empty_errors_returns_zero(self):
        m = TaskMetrics("task-3")
        assert m.get_error_count() == 0


# ---------------------------------------------------------------------------
# TaskMetrics — error rate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskMetricsErrorRate:
    def test_zero_calls_gives_zero_rate(self):
        m = TaskMetrics("task-4")
        assert m.get_error_rate() == 0.0

    def test_all_success_gives_zero_rate(self):
        m = TaskMetrics("task-4")
        for i in range(4):
            m.record_llm_call(
                phase="research",
                model="gpt-4",
                provider="openai",
                tokens_in=10,
                tokens_out=5,
                cost_usd=0,
                duration_ms=50.0,
                status="success",
            )
        assert m.get_error_rate() == 0.0

    def test_half_errors_gives_0_5_rate(self):
        m = TaskMetrics("task-4")
        for status in ("success", "error"):
            m.record_llm_call(
                phase="research",
                model="gpt-4",
                provider="openai",
                tokens_in=10,
                tokens_out=5,
                cost_usd=0,
                duration_ms=50.0,
                status=status,
            )
        assert m.get_error_rate() == 0.5


# ---------------------------------------------------------------------------
# TaskMetrics — duration helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskMetricsDuration:
    def test_total_duration_includes_queue_wait(self):
        m = TaskMetrics("task-5")
        m.record_queue_wait(250.0)
        t = m.record_phase_start("research")
        m.record_phase_end("research", t)
        total = m.get_total_duration_ms()
        assert total >= 250.0

    def test_phase_breakdown_returns_dict(self):
        m = TaskMetrics("task-5")
        t = m.record_phase_start("research")
        m.record_phase_end("research", t)
        breakdown = m.get_phase_breakdown()
        assert "research" in breakdown
        assert isinstance(breakdown["research"], (int, float))

    def test_record_queue_wait_stores_value(self):
        m = TaskMetrics("task-5")
        m.record_queue_wait(500.0)
        assert m.queue_wait_ms == 500.0


# ---------------------------------------------------------------------------
# TaskMetrics — to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskMetricsToDict:
    def test_to_dict_contains_required_keys(self):
        m = TaskMetrics("task-6")
        d = m.to_dict()
        for key in (
            "task_id",
            "start_time",
            "end_time",
            "total_duration_ms",
            "phases",
            "llm_calls",
            "llm_stats",
            "errors",
            "error_count",
        ):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_task_id_matches(self):
        m = TaskMetrics("task-6")
        assert m.to_dict()["task_id"] == "task-6"

    def test_to_dict_llm_stats_shape(self):
        m = TaskMetrics("task-6")
        d = m.to_dict()
        stats = d["llm_stats"]
        for key in (
            "total_calls",
            "total_tokens_in",
            "total_tokens_out",
            "total_cost_usd",
            "error_rate",
        ):
            assert key in stats, f"Missing llm_stats key: {key}"


# ---------------------------------------------------------------------------
# MetricsService
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetricsService:
    @pytest.mark.asyncio
    async def test_get_metrics_returns_dict(self):
        svc = MetricsService()
        result = await svc.get_metrics()
        assert isinstance(result, dict)
        assert "total_tasks" in result

    @pytest.mark.asyncio
    async def test_save_metrics_without_db_returns_true(self):
        """save_metrics should succeed gracefully when database_service is None."""
        svc = MetricsService(database_service=None)
        m = TaskMetrics("task-save")
        result = await svc.save_metrics(m)
        assert result is True

    @pytest.mark.asyncio
    async def test_save_metrics_with_db_that_has_log_method(self):
        """When database_service has a .log() async method, it is called."""
        mock_db = MagicMock()
        mock_db.log = AsyncMock(return_value=None)
        svc = MetricsService(database_service=mock_db)

        m = TaskMetrics("task-save2")
        result = await svc.save_metrics(m)
        assert result is True
        mock_db.log.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_metrics_returns_false_on_exception(self):
        """If database_service.log raises, save_metrics should return False."""
        mock_db = MagicMock()
        mock_db.log = AsyncMock(side_effect=RuntimeError("db down"))
        svc = MetricsService(database_service=mock_db)

        m = TaskMetrics("task-fail")
        result = await svc.save_metrics(m)
        assert result is False

    def test_update_and_get_metric(self):
        svc = MetricsService()
        svc.update_metrics(my_key=42, another="value")
        assert svc.get_metric("my_key") == 42
        assert svc.get_metric("another") == "value"

    def test_get_metric_missing_key_returns_none(self):
        svc = MetricsService()
        assert svc.get_metric("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_metrics_with_db_returns_real_values(self):
        """When database_service is wired, get_metrics() delegates to it (#654)."""
        mock_db = AsyncMock()
        mock_db.get_metrics = AsyncMock(
            return_value={
                "totalTasks": 42,
                "completedTasks": 38,
                "failedTasks": 4,
                "pendingTasks": 0,
                "successRate": 90.48,
                "avgExecutionTime": 12.5,
                "totalCost": 1.23,
            }
        )
        svc = MetricsService(database_service=mock_db)
        result = await svc.get_metrics()
        assert result["total_tasks"] == 42
        assert result["completed_tasks"] == 38
        assert result["failed_tasks"] == 4
        assert result["success_rate"] == 90.48
        assert result["total_cost"] == 1.23
        mock_db.get_metrics.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_metrics_maps_camelcase_to_snake_case(self):
        """DB returns camelCase keys — output must be snake_case (#654)."""
        mock_db = AsyncMock()
        mock_db.get_metrics = AsyncMock(
            return_value={
                "totalTasks": 10,
                "completedTasks": 8,
                "failedTasks": 2,
                "pendingTasks": 0,
                "successRate": 80.0,
                "avgExecutionTime": 5.0,
                "totalCost": 0.5,
            }
        )
        svc = MetricsService(database_service=mock_db)
        result = await svc.get_metrics()
        assert "total_tasks" in result
        assert "completed_tasks" in result
        assert "failed_tasks" in result
        assert "totalTasks" not in result  # camelCase must not leak into output

    @pytest.mark.asyncio
    async def test_get_metrics_with_db_error_returns_zeros(self):
        """If DB raises, get_metrics() falls back to zero defaults (#654)."""
        mock_db = AsyncMock()
        mock_db.get_metrics = AsyncMock(side_effect=RuntimeError("db down"))
        svc = MetricsService(database_service=mock_db)
        result = await svc.get_metrics()
        assert result["total_tasks"] == 0
        assert result["success_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_get_metrics_without_db_returns_zeros(self):
        """Without a database_service, returns zero defaults without error (#654)."""
        svc = MetricsService(database_service=None)
        result = await svc.get_metrics()
        assert result["total_tasks"] == 0
        assert isinstance(result["success_rate"], float)


# ---------------------------------------------------------------------------
# get_metrics_service singleton
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMetricsServiceSingleton:
    def test_returns_metrics_service_instance(self):
        import services.metrics_service as ms_module

        ms_module.metrics_service = None  # Reset singleton
        svc = get_metrics_service()
        assert isinstance(svc, MetricsService)

    def test_returns_same_instance(self):
        import services.metrics_service as ms_module

        ms_module.metrics_service = None  # Reset singleton
        svc1 = get_metrics_service()
        svc2 = get_metrics_service()
        assert svc1 is svc2
