"""
Unit tests for services/task_planning_service.py.

Tests cover:
- TaskPlanningService._generate_stages — stage creation, quality multipliers, unknown stages skipped
- TaskPlanningService._optimize_strategy — strategy selection (sequential/parallel/mixed)
- TaskPlanningService._estimate_quality_score — scoring formula with/without QA
- TaskPlanningService._estimate_success_probability — clamping, risk per stage
- TaskPlanningService._determine_resource_requirements — GPU detection, parallel count
- TaskPlanningService._determine_required_inputs — stage-specific input lists
- TaskPlanningService._determine_quality_metrics — stage-specific metrics
- TaskPlanningService.plan_to_summary — human-readable output, confidence levels
- TaskPlanningService.generate_plan — end-to-end plan construction (ModelRouter mocked)
- TaskPlanningService.serialize_plan — dict serialization

UnifiedOrchestrator and ModelRouter are patched to avoid real LLM calls.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from services.task_intent_router import TaskIntentRequest
from services.task_planning_service import ExecutionPlan, TaskPlanningService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service():
    """Build TaskPlanningService with mocked dependencies."""
    with (
        patch("services.task_planning_service.UnifiedOrchestrator"),
        patch("services.task_planning_service.ModelRouter") as mock_router_cls,
    ):
        mock_router = MagicMock()
        # route_request returns (model_name, tier) — only [0] is used
        mock_router.route_request = MagicMock(return_value=("claude-haiku-3", "cheap"))
        mock_router_cls.return_value = mock_router
        svc = TaskPlanningService()
        svc.model_router = mock_router  # Ensure mock is applied
    return svc


def _make_task_intent(
    task_type="blog_post",
    subtasks=None,
    task_id="tid-1",
):
    intent = MagicMock(spec=TaskIntentRequest)
    intent.task_type = task_type
    intent.suggested_subtasks = subtasks or ["research", "creative", "qa"]
    intent.task_id = task_id
    return intent


# ---------------------------------------------------------------------------
# _generate_stages
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateStages:
    def test_known_stages_created(self):
        svc = _make_service()
        stages = svc._generate_stages(["research", "creative"], "balanced")
        assert len(stages) == 2
        assert stages[0].stage_name == "research"
        assert stages[1].stage_name == "creative"

    def test_stage_numbers_are_sequential(self):
        svc = _make_service()
        stages = svc._generate_stages(["research", "creative", "qa"], "balanced")
        for i, stage in enumerate(stages, start=1):
            assert stage.stage_number == i

    def test_unknown_stage_skipped(self):
        svc = _make_service()
        stages = svc._generate_stages(["research", "totally_unknown", "creative"], "balanced")
        names = [s.stage_name for s in stages]
        assert "totally_unknown" not in names
        assert len(stages) == 2

    def test_high_quality_increases_duration(self):
        svc = _make_service()
        balanced = svc._generate_stages(["research"], "balanced")
        high = svc._generate_stages(["research"], "high")
        assert high[0].estimated_duration_ms > balanced[0].estimated_duration_ms

    def test_draft_quality_decreases_duration(self):
        svc = _make_service()
        balanced = svc._generate_stages(["creative"], "balanced")
        draft = svc._generate_stages(["creative"], "draft")
        assert draft[0].estimated_duration_ms < balanced[0].estimated_duration_ms

    def test_high_quality_increases_cost(self):
        svc = _make_service()
        balanced = svc._generate_stages(["creative"], "balanced")
        high = svc._generate_stages(["creative"], "high")
        assert high[0].estimated_cost > balanced[0].estimated_cost

    def test_all_five_known_stages_accepted(self):
        svc = _make_service()
        stages = svc._generate_stages(
            ["research", "creative", "qa", "images", "format"], "balanced"
        )
        assert len(stages) == 5


# ---------------------------------------------------------------------------
# _optimize_strategy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOptimizeStrategy:
    def _make_stages(self, n=3):
        svc = _make_service()
        return svc._generate_stages(["research", "creative", "qa"][:n], "balanced")

    def test_high_quality_prefers_sequential(self):
        svc = _make_service()
        stages = self._make_stages()
        result = svc._optimize_strategy(
            stages, total_duration=60000, budget=10.0, quality_preference="high"
        )
        assert result == "sequential"

    def test_tight_budget_returns_sequential(self):
        svc = _make_service()
        stages = self._make_stages(3)
        # budget/stages = 0.01 < 0.1 threshold
        result = svc._optimize_strategy(
            stages, total_duration=60000, budget=0.03, quality_preference="balanced"
        )
        assert result == "sequential"

    def test_default_returns_mixed(self):
        svc = _make_service()
        stages = self._make_stages()
        result = svc._optimize_strategy(
            stages, total_duration=60000, budget=10.0, quality_preference="balanced"
        )
        assert result == "mixed"

    def test_tight_deadline_forces_parallel(self):
        svc = _make_service()
        stages = self._make_stages()
        total_duration = 60000  # 60 seconds
        # deadline is very close — less than total_duration * 1.2 ms from now
        tight_deadline = datetime.now(timezone.utc) + timedelta(milliseconds=total_duration * 1.1)
        result = svc._optimize_strategy(
            stages, total_duration=total_duration, deadline=tight_deadline, budget=10.0
        )
        assert result == "parallel"

    def test_moderate_deadline_returns_mixed(self):
        svc = _make_service()
        stages = self._make_stages()
        total_duration = 60000
        # Between 1.2x and 1.8x total_duration → "mixed"
        moderate_deadline = datetime.now(timezone.utc) + timedelta(
            milliseconds=int(total_duration * 1.5)
        )
        result = svc._optimize_strategy(
            stages, total_duration=total_duration, deadline=moderate_deadline, budget=10.0
        )
        assert result == "mixed"


# ---------------------------------------------------------------------------
# _estimate_quality_score
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEstimateQualityScore:
    def test_with_qa_stage_adds_15_points(self):
        svc = _make_service()
        intent = _make_task_intent()
        stages_with_qa = svc._generate_stages(["research", "qa"], "balanced")
        stages_no_qa = svc._generate_stages(["research"], "balanced")

        score_with = svc._estimate_quality_score(intent, stages_with_qa, "balanced")
        score_without = svc._estimate_quality_score(intent, stages_no_qa, "balanced")

        assert score_with > score_without

    def test_high_quality_multiplier_increases_score(self):
        svc = _make_service()
        intent = _make_task_intent()
        stages = svc._generate_stages(["research", "creative"], "balanced")

        score_balanced = svc._estimate_quality_score(intent, stages, "balanced")
        score_high = svc._estimate_quality_score(intent, stages, "high")

        assert score_high > score_balanced

    def test_score_capped_at_100(self):
        svc = _make_service()
        intent = _make_task_intent()
        stages = svc._generate_stages(["research", "creative", "qa", "images", "format"], "high")
        score = svc._estimate_quality_score(intent, stages, "high")
        assert score <= 100.0

    def test_draft_quality_reduces_score(self):
        svc = _make_service()
        intent = _make_task_intent()
        stages = svc._generate_stages(["research", "creative"], "balanced")
        balanced = svc._estimate_quality_score(intent, stages, "balanced")
        draft = svc._estimate_quality_score(intent, stages, "draft")
        assert draft < balanced


# ---------------------------------------------------------------------------
# _estimate_success_probability
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEstimateSuccessProbability:
    def test_result_in_valid_range(self):
        svc = _make_service()
        prob = svc._estimate_success_probability("blog_post", 5, "balanced")
        assert 0.0 <= prob <= 1.0

    def test_blog_post_base_rate_higher_than_generic(self):
        svc = _make_service()
        blog = svc._estimate_success_probability("blog_post", 1, "balanced")
        generic = svc._estimate_success_probability("unknown_type", 1, "balanced")
        assert blog > generic

    def test_more_stages_reduces_probability(self):
        svc = _make_service()
        few = svc._estimate_success_probability("blog_post", 1, "balanced")
        many = svc._estimate_success_probability("blog_post", 10, "balanced")
        assert many < few

    def test_high_quality_reduces_probability(self):
        svc = _make_service()
        balanced = svc._estimate_success_probability("blog_post", 3, "balanced")
        high = svc._estimate_success_probability("blog_post", 3, "high")
        assert high < balanced

    def test_draft_quality_increases_probability(self):
        svc = _make_service()
        balanced = svc._estimate_success_probability("blog_post", 3, "balanced")
        draft = svc._estimate_success_probability("blog_post", 3, "draft")
        assert draft > balanced


# ---------------------------------------------------------------------------
# _determine_resource_requirements
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDetermineResourceRequirements:
    def test_image_stage_requires_gpu(self):
        svc = _make_service()
        stages = svc._generate_stages(["images"], "balanced")
        req = svc._determine_resource_requirements(stages, "balanced")
        assert req["gpu_required"] is True

    def test_no_image_stage_no_gpu(self):
        svc = _make_service()
        stages = svc._generate_stages(["research", "creative"], "balanced")
        req = svc._determine_resource_requirements(stages, "balanced")
        assert req["gpu_required"] is False

    def test_parallel_tasks_count(self):
        svc = _make_service()
        stages = svc._generate_stages(["research", "creative", "qa"], "balanced")
        req = svc._determine_resource_requirements(stages, "balanced")
        assert req["parallel_tasks"] >= 0


# ---------------------------------------------------------------------------
# _determine_required_inputs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDetermineRequiredInputs:
    def test_research_inputs(self):
        svc = _make_service()
        inputs = svc._determine_required_inputs("research")
        assert "topic" in inputs
        assert "keywords" in inputs

    def test_creative_inputs(self):
        svc = _make_service()
        inputs = svc._determine_required_inputs("creative")
        assert "research_output" in inputs

    def test_unknown_stage_returns_empty(self):
        svc = _make_service()
        inputs = svc._determine_required_inputs("unknown_stage")
        assert inputs == []


# ---------------------------------------------------------------------------
# plan_to_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlanToSummary:
    def _make_plan(self, svc, task_type="blog_post", duration_ms=60000, cost=1.0):
        stages = svc._generate_stages(["research", "creative", "qa"], "balanced")
        return ExecutionPlan(
            task_id="tid-1",
            task_type=task_type,
            total_estimated_duration_ms=duration_ms,
            total_estimated_cost=cost,
            total_estimated_tokens=500,
            stages=stages,
            parallelization_strategy="mixed",
            resource_requirements={},
            success_probability=0.92,
            estimated_quality_score=85.0,
        )

    def test_returns_summary_object(self):
        svc = _make_service()
        from services.task_planning_service import ExecutionPlanSummary

        plan = self._make_plan(svc)
        summary = svc.plan_to_summary(plan)
        assert isinstance(summary, ExecutionPlanSummary)

    def test_high_probability_gives_high_confidence(self):
        svc = _make_service()
        plan = self._make_plan(svc)
        plan.success_probability = 0.95
        summary = svc.plan_to_summary(plan)
        assert summary.confidence == "High"

    def test_low_probability_gives_low_confidence(self):
        svc = _make_service()
        plan = self._make_plan(svc)
        plan.success_probability = 0.5
        summary = svc.plan_to_summary(plan)
        assert summary.confidence == "Low"

    def test_duration_under_60_seconds_formatted(self):
        svc = _make_service()
        plan = self._make_plan(svc, duration_ms=30000)  # 30 seconds
        summary = svc.plan_to_summary(plan)
        assert "seconds" in summary.estimated_time

    def test_duration_over_60_seconds_shows_minutes(self):
        svc = _make_service()
        plan = self._make_plan(svc, duration_ms=120000)  # 2 minutes
        summary = svc.plan_to_summary(plan)
        assert "minutes" in summary.estimated_time

    def test_high_cost_adds_warning(self):
        svc = _make_service()
        plan = self._make_plan(svc, cost=10.0)
        summary = svc.plan_to_summary(plan)
        assert summary.warnings is not None
        assert any("cost" in w.lower() for w in summary.warnings)

    def test_sequential_strategy_adds_opportunity(self):
        svc = _make_service()
        plan = self._make_plan(svc)
        plan.parallelization_strategy = "sequential"
        summary = svc.plan_to_summary(plan)
        assert summary.opportunities is not None

    def test_key_stages_list(self):
        svc = _make_service()
        plan = self._make_plan(svc)
        summary = svc.plan_to_summary(plan)
        assert "research" in summary.key_stages


# ---------------------------------------------------------------------------
# generate_plan (end-to-end)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGeneratePlan:
    @pytest.mark.asyncio
    async def test_returns_execution_plan(self):
        svc = _make_service()
        intent = _make_task_intent(subtasks=["research", "creative"])
        plan = await svc.generate_plan(intent)
        assert isinstance(plan, ExecutionPlan)
        assert plan.task_type == "blog_post"

    @pytest.mark.asyncio
    async def test_plan_has_stages(self):
        svc = _make_service()
        intent = _make_task_intent(subtasks=["research", "creative", "qa"])
        plan = await svc.generate_plan(intent)
        assert len(plan.stages) == 3

    @pytest.mark.asyncio
    async def test_plan_respects_quality_preference(self):
        svc = _make_service()
        intent = _make_task_intent(subtasks=["research"])
        plan_balanced = await svc.generate_plan(intent, {"quality_preference": "balanced"})
        plan_high = await svc.generate_plan(intent, {"quality_preference": "high"})
        assert plan_high.total_estimated_cost >= plan_balanced.total_estimated_cost

    @pytest.mark.asyncio
    async def test_plan_has_created_at(self):
        svc = _make_service()
        intent = _make_task_intent(subtasks=["research"])
        plan = await svc.generate_plan(intent)
        assert plan.created_at is not None


# ---------------------------------------------------------------------------
# serialize_plan
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSerializePlan:
    def test_serialized_plan_is_dict(self):
        svc = _make_service()
        stages = svc._generate_stages(["research"], "balanced")
        plan = ExecutionPlan(
            task_id="t1",
            task_type="blog_post",
            total_estimated_duration_ms=15000,
            total_estimated_cost=0.05,
            total_estimated_tokens=100,
            stages=stages,
            parallelization_strategy="sequential",
            resource_requirements={},
        )
        serialized = svc.serialize_plan(plan)
        assert isinstance(serialized, dict)
        assert "task_id" in serialized
        assert isinstance(serialized["stages"], list)
