"""
Unit tests for services.quality_score_persistence.QualityScorePersistence

All DB calls are mocked via AsyncMock — zero real I/O.

Tests cover:
- store_evaluation: success path returns stored=True, DB error returns stored=False
- store_improvement: success path, DB error path, score_improvement + passed_after computed
- get_evaluation_history: returns converted rows, empty list, DB error fallback
- get_latest_evaluation: delegates to get_evaluation_history, returns None when empty
- get_quality_metrics_for_date: found row, empty result defaults, DB error empty dict
- get_quality_trend: returns rows, DB error empty list
"""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.quality_score_persistence import QualityScorePersistence
from services.quality_service import QualityScore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_quality_score(
    overall=8.0,
    passing=True,
    evaluated_by="QualityEvaluator",
):
    return QualityScore(
        overall_score=overall,
        clarity=8.0,
        accuracy=8.0,
        completeness=8.0,
        relevance=8.0,
        seo_quality=8.0,
        readability=8.0,
        engagement=8.0,
        passing=passing,
        feedback="Good content",
        suggestions=["Add more examples"],
        evaluated_by=evaluated_by,
        evaluation_timestamp=datetime.now(timezone.utc).isoformat(),
    )


def make_db(execute_result=None, fetch_result=None):
    db = MagicMock()
    db.execute_query = AsyncMock(return_value=execute_result)
    db.fetch_query = AsyncMock(return_value=fetch_result or [])
    return db


# ---------------------------------------------------------------------------
# store_evaluation
# ---------------------------------------------------------------------------


class TestStoreEvaluation:
    @pytest.mark.asyncio
    async def test_success_returns_stored_true(self):
        db = make_db(execute_result=["eval-id-1"])
        svc = QualityScorePersistence(db)
        score = make_quality_score()
        result = await svc.store_evaluation("content-1", score)
        assert result["stored"] is True
        assert result["content_id"] == "content-1"
        assert result["overall_score"] == 8.0
        assert result["passing"] is True

    @pytest.mark.asyncio
    async def test_success_with_task_id(self):
        db = make_db(execute_result=["eval-2"])
        svc = QualityScorePersistence(db)
        score = make_quality_score()
        result = await svc.store_evaluation("content-1", score, task_id="task-42")
        assert result["stored"] is True

    @pytest.mark.asyncio
    async def test_db_error_returns_stored_false(self):
        db = make_db()
        db.execute_query = AsyncMock(side_effect=RuntimeError("db failure"))
        svc = QualityScorePersistence(db)
        score = make_quality_score()
        result = await svc.store_evaluation("content-1", score)
        assert result["stored"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_none_execute_result_sets_evaluation_id_none(self):
        db = make_db(execute_result=None)
        svc = QualityScorePersistence(db)
        score = make_quality_score()
        result = await svc.store_evaluation("content-1", score)
        assert result["stored"] is True
        assert result["evaluation_id"] is None

    @pytest.mark.asyncio
    async def test_evaluation_method_pattern_based_for_quality_evaluator(self):
        db = make_db(execute_result=["e1"])
        svc = QualityScorePersistence(db)
        score = make_quality_score(evaluated_by="QualityEvaluator")
        await svc.store_evaluation("c-1", score)
        # Check the SQL args passed — 15th positional arg is evaluation_method
        call_args = db.execute_query.call_args[0]
        assert "pattern-based" in call_args

    @pytest.mark.asyncio
    async def test_evaluation_method_llm_based_for_other_evaluator(self):
        db = make_db(execute_result=["e1"])
        svc = QualityScorePersistence(db)
        score = make_quality_score(evaluated_by="LLMEvaluator")
        await svc.store_evaluation("c-1", score)
        call_args = db.execute_query.call_args[0]
        assert "llm-based" in call_args


# ---------------------------------------------------------------------------
# store_improvement
# ---------------------------------------------------------------------------


class TestStoreImprovement:
    @pytest.mark.asyncio
    async def test_success_returns_recorded_true(self):
        db = make_db(execute_result=["imp-1"])
        svc = QualityScorePersistence(db)
        result = await svc.store_improvement(
            "content-1", initial_score=6.0, improved_score=8.5
        )
        assert result["recorded"] is True
        assert result["score_improvement"] == pytest.approx(2.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_passed_after_refinement_true_above_7(self):
        db = make_db(execute_result=["imp-1"])
        svc = QualityScorePersistence(db)
        result = await svc.store_improvement("c-1", 5.0, 7.5)
        assert result["passed_after_refinement"] is True

    @pytest.mark.asyncio
    async def test_passed_after_refinement_false_below_7(self):
        db = make_db(execute_result=["imp-1"])
        svc = QualityScorePersistence(db)
        result = await svc.store_improvement("c-1", 4.0, 6.9)
        assert result["passed_after_refinement"] is False

    @pytest.mark.asyncio
    async def test_db_error_returns_recorded_false(self):
        db = make_db()
        db.execute_query = AsyncMock(side_effect=RuntimeError("db crash"))
        svc = QualityScorePersistence(db)
        result = await svc.store_improvement("c-1", 5.0, 8.0)
        assert result["recorded"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_none_result_sets_improvement_log_id_none(self):
        db = make_db(execute_result=None)
        svc = QualityScorePersistence(db)
        result = await svc.store_improvement("c-1", 5.0, 8.0)
        assert result["recorded"] is True
        assert result["improvement_log_id"] is None


# ---------------------------------------------------------------------------
# get_evaluation_history
# ---------------------------------------------------------------------------


class TestGetEvaluationHistory:
    @pytest.mark.asyncio
    async def test_returns_converted_rows(self):
        rows = [
            {"id": "e1", "overall_score": 8.0, "passing": True},
            {"id": "e2", "overall_score": 6.0, "passing": False},
        ]
        db = make_db(fetch_result=rows)
        svc = QualityScorePersistence(db)
        result = await svc.get_evaluation_history("content-1")
        assert len(result) == 2
        assert result[0]["id"] == "e1"

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self):
        db = make_db(fetch_result=None)
        svc = QualityScorePersistence(db)
        result = await svc.get_evaluation_history("content-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_list(self):
        db = make_db()
        db.fetch_query = AsyncMock(side_effect=RuntimeError("db error"))
        svc = QualityScorePersistence(db)
        result = await svc.get_evaluation_history("content-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_limit_passed_to_db(self):
        db = make_db(fetch_result=[])
        svc = QualityScorePersistence(db)
        await svc.get_evaluation_history("c-1", limit=5)
        db.fetch_query.assert_awaited_once()
        call_args = db.fetch_query.call_args[0]
        assert 5 in call_args


# ---------------------------------------------------------------------------
# get_latest_evaluation
# ---------------------------------------------------------------------------


class TestGetLatestEvaluation:
    @pytest.mark.asyncio
    async def test_returns_first_element_when_history_exists(self):
        rows = [{"id": "e1", "overall_score": 8.0}]
        db = make_db(fetch_result=rows)
        svc = QualityScorePersistence(db)
        result = await svc.get_latest_evaluation("content-1")
        assert result == {"id": "e1", "overall_score": 8.0}

    @pytest.mark.asyncio
    async def test_returns_none_when_no_history(self):
        db = make_db(fetch_result=None)
        svc = QualityScorePersistence(db)
        result = await svc.get_latest_evaluation("content-1")
        assert result is None


# ---------------------------------------------------------------------------
# get_quality_metrics_for_date
# ---------------------------------------------------------------------------


class TestGetQualityMetricsForDate:
    @pytest.mark.asyncio
    async def test_returns_row_data_when_found(self):
        row = {"date": date.today(), "total_evaluations": 10, "pass_rate": 0.8}
        db = make_db(fetch_result=[row])
        svc = QualityScorePersistence(db)
        result = await svc.get_quality_metrics_for_date(date.today())
        assert result["total_evaluations"] == 10
        assert result["pass_rate"] == 0.8

    @pytest.mark.asyncio
    async def test_defaults_when_no_row(self):
        db = make_db(fetch_result=[])
        svc = QualityScorePersistence(db)
        result = await svc.get_quality_metrics_for_date(date.today())
        assert result["total_evaluations"] == 0
        assert result["pass_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_uses_today_when_no_date_provided(self):
        db = make_db(fetch_result=[])
        svc = QualityScorePersistence(db)
        result = await svc.get_quality_metrics_for_date()
        assert "total_evaluations" in result

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_dict(self):
        db = make_db()
        db.fetch_query = AsyncMock(side_effect=RuntimeError("db down"))
        svc = QualityScorePersistence(db)
        result = await svc.get_quality_metrics_for_date()
        assert result == {}


# ---------------------------------------------------------------------------
# get_quality_trend
# ---------------------------------------------------------------------------


class TestGetQualityTrend:
    @pytest.mark.asyncio
    async def test_returns_list_of_rows(self):
        rows = [
            {"date": "2025-01-01", "pass_rate": 0.7},
            {"date": "2025-01-02", "pass_rate": 0.8},
        ]
        db = make_db(fetch_result=rows)
        svc = QualityScorePersistence(db)
        result = await svc.get_quality_trend(days=7)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_list(self):
        db = make_db()
        db.fetch_query = AsyncMock(side_effect=RuntimeError("db error"))
        svc = QualityScorePersistence(db)
        result = await svc.get_quality_trend()
        assert result == []
