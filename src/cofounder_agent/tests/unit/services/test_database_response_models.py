"""
Unit tests for database_response_models.py and model_converter.py

Tests field validation, model construction, and converter logic.
"""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from schemas.database_response_models import (
    CategoryResponse,
    CostLogResponse,
    ErrorResponse,
    MetricsResponse,
    PaginatedResponse,
    PostResponse,
    QualityEvaluationResponse,
    TagResponse,
    TaskCountsResponse,
    TaskResponse,
    UserResponse,
)
from schemas.model_converter import ModelConverter

NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# UserResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUserResponse:
    def test_valid(self):
        user = UserResponse(
            id="user-123",
            email="test@example.com",
            username="testuser",
            created_at=NOW,
            updated_at=NOW,
        )
        assert user.is_active is True

    def test_inactive_user(self):
        user = UserResponse(
            id="user-456",
            email="inactive@example.com",
            username="inactiveuser",
            is_active=False,
            created_at=NOW,
            updated_at=NOW,
        )
        assert user.is_active is False


# ---------------------------------------------------------------------------
# TaskResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskResponse:
    def test_valid_minimal(self):
        task = TaskResponse(  # type: ignore[call-arg]
            id="task-123",
            created_at=NOW,
            updated_at=NOW,
        )
        assert task.priority == 0
        assert task.tags == []
        assert task.status is None

    def test_priority_bounds(self):
        task = TaskResponse(id="t1", created_at=NOW, updated_at=NOW, priority=5)  # type: ignore[call-arg]
        assert task.priority == 5

    def test_priority_too_high_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TaskResponse(id="t1", created_at=NOW, updated_at=NOW, priority=6)  # type: ignore[call-arg]

    def test_priority_negative_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TaskResponse(id="t1", created_at=NOW, updated_at=NOW, priority=-1)  # type: ignore[call-arg]

    def test_with_content_fields(self):
        task = TaskResponse(  # type: ignore[call-arg]
            id="task-456",
            task_name="My Blog Post",
            topic="AI in Healthcare",
            status="completed",
            quality_score=92.5,
            created_at=NOW,
            updated_at=NOW,
        )
        assert task.task_name == "My Blog Post"
        assert task.quality_score == 92.5


# ---------------------------------------------------------------------------
# TaskCountsResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskCountsResponse:
    def test_defaults(self):
        counts = TaskCountsResponse()
        assert counts.total == 0
        assert counts.pending == 0
        assert counts.completed == 0

    def test_with_values(self):
        counts = TaskCountsResponse(
            total=100,
            pending=20,
            in_progress=10,
            completed=60,
            failed=5,
            awaiting_approval=3,
            approved=2,
        )
        assert counts.total == 100
        assert counts.completed == 60


# ---------------------------------------------------------------------------
# PostResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPostResponse:
    def test_valid(self):
        post = PostResponse(  # type: ignore[call-arg]
            id="post-123",
            title="AI in Healthcare",
            slug="ai-in-healthcare",
            content="# AI in Healthcare\n\nContent...",
            created_at=NOW,
            updated_at=NOW,
        )
        assert post.status == "draft"
        assert post.excerpt is None

    def test_published_status(self):
        post = PostResponse(  # type: ignore[call-arg]
            id="post-456",
            title="Post Title",
            slug="post-title",
            content="Content here",
            status="published",
            created_at=NOW,
            updated_at=NOW,
        )
        assert post.status == "published"

    def test_invalid_status_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PostResponse(
                id="post-789",
                title="Title",
                slug="title",
                content="Content",
                status="unknown",  # type: ignore[arg-type]
                created_at=NOW,
                updated_at=NOW,
            )


# ---------------------------------------------------------------------------
# CategoryResponse / TagResponse / AuthorResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCategoryResponse:
    def test_valid(self):
        cat = CategoryResponse(id="cat-1", name="Technology", slug="technology")  # type: ignore[call-arg]
        assert cat.description is None


@pytest.mark.unit
class TestTagResponse:
    def test_valid(self):
        tag = TagResponse(id="tag-1", name="AI", slug="ai")  # type: ignore[call-arg]
        assert tag.description is None


# ---------------------------------------------------------------------------
# MetricsResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetricsResponse:
    def test_defaults(self):
        metrics = MetricsResponse()
        assert metrics.totalTasks == 0
        assert metrics.successRate == 0.0
        assert metrics.totalCost == 0.0

    def test_success_rate_bounds(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MetricsResponse(successRate=100.1)
        with pytest.raises(ValidationError):
            MetricsResponse(successRate=-0.1)

    def test_with_values(self):
        metrics = MetricsResponse(
            totalTasks=100,
            completedTasks=95,
            failedTasks=5,
            successRate=95.0,
            avgExecutionTime=45.2,
            totalCost=1.23,
        )
        assert metrics.successRate == 95.0


# ---------------------------------------------------------------------------
# CostLogResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCostLogResponse:
    def _valid(self, **kwargs):
        defaults = {
            "id": "cost-123",
            "task_id": "task-456",
            "phase": "draft",
            "model": "gpt-4",
            "provider": "openai",
            "created_at": NOW,
            "updated_at": NOW,
        }
        defaults.update(kwargs)
        return CostLogResponse(**defaults)

    def test_valid_defaults(self):
        log = self._valid()
        assert log.input_tokens == 0
        assert log.cost_usd == 0.0
        assert log.success is True

    def test_all_valid_phases(self):
        valid_phases = [
            "research",
            "outline",
            "draft",
            "assess",
            "refine",
            "finalize",
            "content_generation",
        ]
        for phase in valid_phases:
            log = self._valid(phase=phase)
            assert log.phase == phase

    def test_invalid_phase_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._valid(phase="unknown_phase")

    def test_all_valid_providers(self):
        valid_providers = ["ollama", "openai", "anthropic", "google", "gemini", "unknown"]
        for provider in valid_providers:
            log = self._valid(provider=provider)
            assert log.provider == provider

    def test_quality_score_bounds(self):
        from pydantic import ValidationError

        log = self._valid(quality_score=0.0)
        assert log.quality_score == 0.0
        log = self._valid(quality_score=5.0)
        assert log.quality_score == 5.0
        with pytest.raises(ValidationError):
            self._valid(quality_score=5.1)


# ---------------------------------------------------------------------------
# QualityEvaluationResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQualityEvaluationResponse:
    def test_valid(self):
        eval_resp = QualityEvaluationResponse(  # type: ignore[call-arg]
            id="eval-123",
            content_id="content-456",
            overall_score=85.0,
            evaluation_timestamp=NOW,
        )
        assert eval_resp.passing is False
        assert eval_resp.suggestions is None

    def test_score_bounds(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            QualityEvaluationResponse(  # type: ignore[call-arg]
                id="e1",
                content_id="c1",
                overall_score=100.1,  # over 100
                evaluation_timestamp=NOW,
            )

    def test_passing_evaluation(self):
        eval_resp = QualityEvaluationResponse(  # type: ignore[call-arg]
            id="eval-456",
            content_id="content-789",
            overall_score=95.0,
            passing=True,
            feedback="Excellent content",
            suggestions=["Add more examples"],
            evaluation_timestamp=NOW,
        )
        assert eval_resp.passing is True
        assert len(eval_resp.suggestions) == 1  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ErrorResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestErrorResponse:
    def test_valid(self):
        err = ErrorResponse(  # type: ignore[call-arg]
            status=404,
            error="Not Found",
            message="Task not found",
        )
        assert err.details is None
        assert err.timestamp is not None  # auto-generated

    def test_with_details(self):
        err = ErrorResponse(
            status=422,
            error="Validation Error",
            message="Invalid input",
            details={"field": "topic", "issue": "too short"},
        )
        assert err.details == {"field": "topic", "issue": "too short"}


# ---------------------------------------------------------------------------
# PaginatedResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPaginatedResponse:
    def test_valid(self):
        page: PaginatedResponse[str] = PaginatedResponse(
            total=100,
            page=1,
            limit=20,
            items=["item1", "item2"],
        )
        assert page.total == 100
        assert len(page.items) == 2

    def test_page_minimum(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PaginatedResponse(total=10, page=0, limit=20, items=[])

    def test_limit_maximum(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PaginatedResponse(total=10, page=1, limit=101, items=[])


# ---------------------------------------------------------------------------
# ModelConverter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModelConverter:
    def test_normalize_row_data_with_dict(self):
        data = {"id": "abc", "value": "test"}
        result = ModelConverter._normalize_row_data(data)
        assert result["id"] == "abc"

    def test_normalize_row_data_with_uuid(self):
        uuid_val = UUID("550e8400-e29b-41d4-a716-446655440000")
        data = {"id": uuid_val, "name": "test"}
        result = ModelConverter._normalize_row_data(data)
        assert result["id"] == "550e8400-e29b-41d4-a716-446655440000"

    def test_normalize_row_data_parses_json_fields(self):
        data = {
            "metadata": '{"key": "value"}',
            "suggestions": '["fix 1", "fix 2"]',
        }
        result = ModelConverter._normalize_row_data(data)
        assert result["metadata"] == {"key": "value"}
        assert result["suggestions"] == ["fix 1", "fix 2"]

    def test_normalize_row_data_invalid_json_stays_as_string(self):
        data = {"metadata": "not-valid-json"}
        result = ModelConverter._normalize_row_data(data)
        assert result["metadata"] == "not-valid-json"

    def test_normalize_row_data_parses_array_fields(self):
        data = {"tags": '["AI", "Tech"]'}
        result = ModelConverter._normalize_row_data(data)
        assert result["tags"] == ["AI", "Tech"]

    def test_normalize_row_data_uuid_in_array(self):
        uuid_val = UUID("550e8400-e29b-41d4-a716-446655440000")
        data = {"tag_ids": [uuid_val]}
        result = ModelConverter._normalize_row_data(data)
        assert result["tag_ids"] == ["550e8400-e29b-41d4-a716-446655440000"]

    def test_to_task_counts_response(self):
        counts = ModelConverter.to_task_counts_response({"total": 10, "pending": 2, "completed": 8})
        assert counts.total == 10

    def test_to_metrics_response(self):
        metrics = ModelConverter.to_metrics_response(
            {"totalTasks": 50, "completedTasks": 45, "failedTasks": 5}
        )
        assert metrics.totalTasks == 50

    def test_to_financial_summary_response(self):
        summary = ModelConverter.to_financial_summary_response(
            {"total_entries": 10, "total_amount": 150.00}
        )
        assert summary.total_entries == 10

    def test_to_dict_pydantic_v2(self):
        metrics = MetricsResponse(totalTasks=5)
        result = ModelConverter.to_dict(metrics)
        assert result["totalTasks"] == 5

    def test_to_list_unsupported_model_raises(self):
        with pytest.raises(ValueError, match="No converter found"):
            ModelConverter.to_list([{"id": "1"}], MetricsResponse)

    def test_to_list_empty_returns_empty(self):
        result = ModelConverter.to_list([], UserResponse)
        assert result == []

    def test_task_response_to_unified_converts_seo_keywords(self):
        task = TaskResponse(  # type: ignore[call-arg]
            id="task-1",
            seo_keywords='["AI", "healthcare"]',
            created_at=NOW,
            updated_at=NOW,
        )
        result = ModelConverter.task_response_to_unified(task)
        assert result["seo_keywords"] == ["AI", "healthcare"]

    def test_task_response_to_unified_preserves_cost_breakdown_dict(self):
        # TaskResponse.cost_breakdown is Optional[Dict], so pass a dict directly
        task = TaskResponse(  # type: ignore[call-arg]
            id="task-2",
            cost_breakdown={"research": 0.001, "draft": 0.005},
            created_at=NOW,
            updated_at=NOW,
        )
        result = ModelConverter.task_response_to_unified(task)
        # Cost breakdown is already a dict; task_response_to_unified passes it through
        assert result["cost_breakdown"] == {"research": 0.001, "draft": 0.005}

    def test_task_response_to_unified_none_cost_breakdown(self):
        # Cost breakdown is None by default
        task = TaskResponse(  # type: ignore[call-arg]
            id="task-3",
            created_at=NOW,
            updated_at=NOW,
        )
        result = ModelConverter.task_response_to_unified(task)
        assert result["cost_breakdown"] is None

    def test_to_cost_log_response_maps_phase_aliases(self):
        data = {
            "id": "cost-1",
            "task_id": "task-1",
            "phase": "content_generation",  # should map to "draft"
            "model": "gpt-4",
            "provider": "openai",
            "created_at": NOW,
            "updated_at": NOW,
        }
        log = ModelConverter.to_cost_log_response(data)
        assert log.phase == "draft"

    def test_to_cost_log_response_maps_provider_aliases(self):
        data = {
            "id": "cost-2",
            "task_id": "task-2",
            "phase": "draft",
            "model": "gemini-pro",
            "provider": "gemini",  # should map to "google"
            "created_at": NOW,
            "updated_at": NOW,
        }
        log = ModelConverter.to_cost_log_response(data)
        assert log.provider == "google"
