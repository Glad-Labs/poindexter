"""
Unit tests for models_schemas.py, unified_task_response.py,
and workflow_history_schemas.py

Tests field validation and model behaviour.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from schemas.models_schemas import (
    ModelInfo,
    ModelsListResponse,
    ProvidersStatusResponse,
    ProviderStatus,
)
from schemas.unified_task_response import (
    CostBreakdown,
    ModelSelection,
    ProgressInfo,
    TaskResultContent,
    UnifiedTaskResponse,
)
from schemas.workflow_history_schemas import (
    PerformanceMetrics,
    WorkflowExecutionDetail,
    WorkflowHistoryResponse,
    WorkflowStatistics,
)

NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# models_schemas.py
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModelInfo:
    def test_valid(self):
        info = ModelInfo(
            name="llama2",
            displayName="Llama 2",
            provider="ollama",
            isFree=True,
            size="7B",
            estimatedVramGb=6.5,
            description="Open-source LLM from Meta",
            icon="llama",
            requiresInternet=False,
        )
        assert info.name == "llama2"
        assert info.isFree is True
        assert info.requiresInternet is False

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            ModelInfo(  # type: ignore[call-arg]
                name="llama2",
                displayName="Llama 2",
                # missing provider
                isFree=True,
                size="7B",
                estimatedVramGb=6.5,
                description="desc",
                icon="icon",
                requiresInternet=False,
            )


@pytest.mark.unit
class TestModelsListResponse:
    def test_valid(self):
        model = ModelInfo(
            name="llama2",
            displayName="Llama 2",
            provider="ollama",
            isFree=True,
            size="7B",
            estimatedVramGb=6.5,
            description="desc",
            icon="icon",
            requiresInternet=False,
        )
        resp = ModelsListResponse(
            models=[model],
            total=1,
            timestamp="2026-01-01T00:00:00Z",
        )
        assert resp.total == 1

    def test_empty_models(self):
        resp = ModelsListResponse(models=[], total=0, timestamp="2026-01-01T00:00:00Z")
        assert resp.total == 0


@pytest.mark.unit
class TestProviderStatus:
    def test_valid_defaults(self):
        status = ProviderStatus(available=True)
        assert status.url is None
        assert status.hasToken is False
        assert status.hasKey is False
        assert status.models == 0

    def test_with_url_and_models(self):
        status = ProviderStatus(
            available=True,
            url="http://localhost:11434",
            models=3,
        )
        assert status.models == 3

    def test_unavailable(self):
        status = ProviderStatus(available=False)
        assert status.available is False


@pytest.mark.unit
class TestProvidersStatusResponse:
    def test_valid(self):
        provider = ProviderStatus(available=True, models=2)
        resp = ProvidersStatusResponse(
            ollama=provider,
            huggingface=ProviderStatus(available=False),
            gemini=ProviderStatus(available=True, hasKey=True),
            timestamp="2026-01-01T00:00:00Z",
        )
        assert resp.ollama.available is True
        assert resp.ollama.models == 2
        assert resp.gemini.hasKey is True


# ---------------------------------------------------------------------------
# unified_task_response.py
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProgressInfo:
    def test_valid_minimal(self):
        info = ProgressInfo()  # type: ignore[call-arg]
        assert info.stage is None
        assert info.percentage is None
        assert info.message is None
        assert info.node is None

    def test_with_values(self):
        info = ProgressInfo(stage="draft", percentage=50, message="Drafting...")  # type: ignore[call-arg]
        assert info.stage == "draft"
        assert info.percentage == 50

    def test_percentage_bounds(self):
        info = ProgressInfo(percentage=0)  # type: ignore[call-arg]
        assert info.percentage == 0
        info = ProgressInfo(percentage=100)  # type: ignore[call-arg]
        assert info.percentage == 100

    def test_percentage_too_high_raises(self):
        with pytest.raises(ValidationError):
            ProgressInfo(percentage=101)  # type: ignore[call-arg]

    def test_percentage_negative_raises(self):
        with pytest.raises(ValidationError):
            ProgressInfo(percentage=-1)  # type: ignore[call-arg]


@pytest.mark.unit
class TestCostBreakdown:
    def test_defaults(self):
        breakdown = CostBreakdown()
        assert breakdown.research == 0.0
        assert breakdown.total == 0.0

    def test_with_values(self):
        breakdown = CostBreakdown(
            research=0.001,
            outline=0.0005,
            draft=0.005,
            assess=0.003,
            refine=0.002,
            finalize=0.0005,
            total=0.012,
        )
        assert breakdown.total == 0.012


@pytest.mark.unit
class TestModelSelection:
    def test_defaults_all_none(self):
        selection = ModelSelection()
        assert selection.research is None
        assert selection.draft is None

    def test_with_phase_models(self):
        selection = ModelSelection(
            research="ultra_cheap",
            draft="premium",
            assess="cheap",
        )
        assert selection.research == "ultra_cheap"
        assert selection.draft == "premium"


@pytest.mark.unit
class TestTaskResultContent:
    def test_defaults(self):
        result = TaskResultContent()  # type: ignore[call-arg]
        assert result.content is None
        assert result.quality_score is None

    def test_with_content(self):
        result = TaskResultContent(  # type: ignore[call-arg]
            content="# Blog Post\n\nContent here...",
            excerpt="Short summary",
            quality_score=92.5,
            seo_keywords=["AI", "healthcare"],
        )
        assert result.quality_score == 92.5

    def test_quality_score_bounds(self):
        result = TaskResultContent(quality_score=0.0)  # type: ignore[call-arg]
        assert result.quality_score == 0.0
        result = TaskResultContent(quality_score=100.0)  # type: ignore[call-arg]
        assert result.quality_score == 100.0

    def test_quality_score_too_high_raises(self):
        with pytest.raises(ValidationError):
            TaskResultContent(quality_score=100.1)  # type: ignore[call-arg]


@pytest.mark.unit
class TestUnifiedTaskResponse:
    def _valid(self, **kwargs):
        defaults = {
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        }
        defaults.update(kwargs)
        return UnifiedTaskResponse(**defaults)

    def test_valid_minimal(self):
        task = self._valid()
        assert task.task_type == "blog_post"
        assert task.id is None
        assert task.request_type == "content_generation"

    def test_with_all_identification_fields(self):
        task = self._valid(
            id="task-123",
            task_id="task-123",
            request_id="req-456",
            task_name="Blog Post about AI",
            topic="AI in Healthcare",
            primary_keyword="AI healthcare",
        )
        assert task.id == "task-123"
        assert task.topic == "AI in Healthcare"

    def test_percentage_bounds(self):
        task = self._valid(percentage=0)
        assert task.percentage == 0
        task = self._valid(percentage=100)
        assert task.percentage == 100

    def test_percentage_too_high_raises(self):
        with pytest.raises(ValidationError):
            self._valid(percentage=101)

    def test_estimated_cost_non_negative(self):
        task = self._valid(estimated_cost=0.0)
        assert task.estimated_cost == 0.0

    def test_estimated_cost_negative_raises(self):
        with pytest.raises(ValidationError):
            self._valid(estimated_cost=-0.01)

    def test_quality_score_bounds(self):
        task = self._valid(quality_score=0.0)
        assert task.quality_score == 0.0
        task = self._valid(quality_score=100.0)
        assert task.quality_score == 100.0

    def test_with_progress(self):
        task = self._valid(
            status="generating",
            progress=ProgressInfo(stage="research", percentage=25),  # type: ignore[call-arg]
        )
        assert task.progress is not None
        assert task.progress.stage == "research"  # type: ignore[union-attr]

    def test_with_result(self):
        task = self._valid(
            status="completed",
            result=TaskResultContent(content="Generated content", quality_score=92.0),  # type: ignore[call-arg]
        )
        assert task.result is not None
        assert task.result.quality_score == 92.0  # type: ignore[union-attr]

    def test_with_cost_breakdown(self):
        task = self._valid(
            cost_breakdown=CostBreakdown(draft=0.005, total=0.005),
        )
        assert task.cost_breakdown is not None
        assert task.cost_breakdown.draft == 0.005  # type: ignore[union-attr]

    def test_string_timestamps_accepted(self):
        # Both datetime and ISO string timestamps should work
        task = self._valid(
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:05:00Z",
        )
        assert task.created_at == "2026-01-01T00:00:00Z"

    def test_missing_status_raises_validation_error(self):
        """Status is required — omitting it should raise ValidationError."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            UnifiedTaskResponse(  # type: ignore[call-arg]
                created_at=NOW,
                updated_at=NOW,
                # status omitted — should raise
            )


# ---------------------------------------------------------------------------
# workflow_history_schemas.py
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWorkflowExecutionDetail:
    def test_valid(self):
        detail = WorkflowExecutionDetail(
            id="exec-123",
            workflow_id="wf-456",
            workflow_type="blog_pipeline",
            user_id="user-789",
            status="completed",
            input_data={"topic": "AI trends"},
            task_results=[{"phase": "research", "output": "..."}],
            execution_metadata={"environment": "production"},
            start_time="2026-01-01T10:00:00Z",
            created_at="2026-01-01T10:00:00Z",
            updated_at="2026-01-01T10:10:00Z",
        )
        assert detail.output_data is None
        assert detail.error_message is None
        assert detail.end_time is None

    def test_completed_with_output(self):
        detail = WorkflowExecutionDetail(
            id="exec-456",
            workflow_id="wf-789",
            workflow_type="blog_pipeline",
            user_id="user-001",
            status="completed",
            input_data={},
            output_data={"post_id": "post-123"},
            task_results=[],
            execution_metadata={},
            start_time="2026-01-01T10:00:00Z",
            end_time="2026-01-01T10:05:00Z",
            duration_seconds=300.0,
            created_at="2026-01-01T10:00:00Z",
            updated_at="2026-01-01T10:05:00Z",
        )
        assert detail.duration_seconds == 300.0
        assert detail.output_data == {"post_id": "post-123"}


@pytest.mark.unit
class TestWorkflowHistoryResponse:
    def _make_detail(self, exec_id="exec-1"):
        return WorkflowExecutionDetail(
            id=exec_id,
            workflow_id="wf-1",
            workflow_type="blog_pipeline",
            user_id="user-1",
            status="completed",
            input_data={},
            task_results=[],
            execution_metadata={},
            start_time="2026-01-01T10:00:00Z",
            created_at="2026-01-01T10:00:00Z",
            updated_at="2026-01-01T10:05:00Z",
        )

    def test_valid(self):
        resp = WorkflowHistoryResponse(
            executions=[self._make_detail()],
            total=1,
            limit=20,
            offset=0,
        )
        assert resp.total == 1
        assert resp.status_filter is None

    def test_with_filter(self):
        resp = WorkflowHistoryResponse(
            executions=[],
            total=0,
            limit=20,
            offset=0,
            status_filter="completed",
        )
        assert resp.status_filter == "completed"


@pytest.mark.unit
class TestWorkflowStatistics:
    def test_valid(self):
        stats = WorkflowStatistics(
            user_id="user-123",
            period_days=30,
            total_executions=50,
            completed_executions=45,
            failed_executions=5,
            success_rate_percent=90.0,
            average_duration_seconds=300.0,
            first_execution="2026-01-01T00:00:00Z",
            last_execution="2026-01-30T00:00:00Z",
            workflows=[{"type": "blog_pipeline", "count": 50}],
            most_common_workflow="blog_pipeline",
        )
        assert stats.success_rate_percent == 90.0
        assert stats.most_common_workflow == "blog_pipeline"

    def test_no_executions(self):
        stats = WorkflowStatistics(
            user_id="user-456",
            period_days=30,
            total_executions=0,
            completed_executions=0,
            failed_executions=0,
            success_rate_percent=0.0,
            average_duration_seconds=0.0,
            first_execution=None,
            last_execution=None,
            workflows=[],
            most_common_workflow=None,
        )
        assert stats.total_executions == 0


@pytest.mark.unit
class TestWorkflowPerformanceMetrics:
    def test_valid(self):
        metrics = PerformanceMetrics(
            user_id="user-123",
            workflow_type="blog_pipeline",
            period_days=30,
            execution_time_distribution=[
                {"bucket": "< 5 min", "count": 40},
                {"bucket": "5-15 min", "count": 10},
            ],
            error_patterns=[{"error": "timeout", "count": 3}],
            optimization_tips=["Use ultra_cheap model for research phase"],
        )
        assert len(metrics.optimization_tips) == 1
        assert metrics.workflow_type == "blog_pipeline"

    def test_no_workflow_type(self):
        metrics = PerformanceMetrics(
            user_id="user-789",
            workflow_type=None,
            period_days=7,
            execution_time_distribution=[],
            error_patterns=[],
            optimization_tips=[],
        )
        assert metrics.workflow_type is None
