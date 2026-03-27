"""
Unit tests for services.task_intent_router

Tests cover:
- TaskIntentRequest and SubtaskPlan dataclasses
- TaskIntentRouter._normalize_parameters — all supported fields
- TaskIntentRouter._determine_subtasks — include/exclude images, multi-agent types
- TaskIntentRouter._should_confirm — low confidence, missing topic, budget present
- TaskIntentRouter._determine_execution_strategy — always sequential
- TaskIntentRouter._get_required_inputs — each stage
- TaskIntentRouter.plan_subtasks — sequential plan, parallel plan, correct IDs
- TaskIntentRouter.generate_execution_plan_summary — step count, estimated time
- TaskIntentRouter._format_duration — ms / seconds / minutes
- TaskIntentRouter.route_user_input — with mocked NLPIntentRecognizer (no network)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.task_intent_router import TaskIntentRequest, TaskIntentRouter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def router() -> TaskIntentRouter:
    return TaskIntentRouter()


# ---------------------------------------------------------------------------
# _normalize_parameters
# ---------------------------------------------------------------------------


class TestNormalizeParameters:
    def test_topic_extracted(self, router):
        result = router._normalize_parameters({"topic": "AI"}, "blog_post")
        assert result["topic"] == "AI"

    def test_style_extracted(self, router):
        result = router._normalize_parameters({"style": "formal"}, "blog_post")
        assert result["style"] == "formal"

    def test_tone_extracted(self, router):
        result = router._normalize_parameters({"tone": "casual"}, "blog_post")
        assert result["tone"] == "casual"

    def test_length_mapped_to_target_length(self, router):
        result = router._normalize_parameters({"length": 1500}, "blog_post")
        assert result["target_length"] == 1500

    def test_budget_converted_to_float(self, router):
        result = router._normalize_parameters({"budget": "50"}, "blog_post")
        assert result["budget"] == 50.0
        assert isinstance(result["budget"], float)

    def test_invalid_budget_stored_as_none(self, router):
        result = router._normalize_parameters({"budget": "not-a-number"}, "blog_post")
        assert result["budget"] is None

    def test_deadline_passthrough(self, router):
        result = router._normalize_parameters({"deadline": "2025-12-01"}, "blog_post")
        assert result["deadline"] == "2025-12-01"

    def test_deadline_days_computed(self, router):
        result = router._normalize_parameters({"deadline_days": "3"}, "blog_post")
        assert "deadline" in result
        # Should be an ISO datetime string
        assert "T" in result["deadline"]

    def test_invalid_deadline_days_no_deadline_key(self, router):
        result = router._normalize_parameters({"deadline_days": "nan"}, "blog_post")
        assert "deadline" not in result

    def test_platforms_extracted(self, router):
        result = router._normalize_parameters(
            {"platforms": ["twitter", "linkedin"]}, "social_media"
        )
        assert result["platforms"] == ["twitter", "linkedin"]

    def test_include_images_extracted(self, router):
        result = router._normalize_parameters({"include_images": True}, "blog_post")
        assert result["include_images"] is True

    def test_quality_preference_extracted(self, router):
        result = router._normalize_parameters({"quality_preference": "premium"}, "blog_post")
        assert result["quality_preference"] == "premium"

    def test_unknown_keys_not_included(self, router):
        result = router._normalize_parameters({"unknown_key": "value"}, "blog_post")
        assert "unknown_key" not in result

    def test_empty_params(self, router):
        result = router._normalize_parameters({}, "blog_post")
        assert result == {}


# ---------------------------------------------------------------------------
# _determine_subtasks
# ---------------------------------------------------------------------------


class TestDetermineSubtasks:
    def test_blog_post_full_subtasks(self, router):
        subtasks = router._determine_subtasks("blog_post", {})
        assert subtasks == ["research", "creative", "qa", "images", "format"]

    def test_blog_post_no_images_when_excluded(self, router):
        subtasks = router._determine_subtasks("blog_post", {"include_images": False})
        assert "images" not in subtasks

    def test_blog_post_images_when_explicitly_included(self, router):
        subtasks = router._determine_subtasks("blog_post", {"include_images": True})
        assert "images" in subtasks

    def test_social_media_subtasks(self, router):
        subtasks = router._determine_subtasks("social_media", {})
        assert subtasks == ["research", "creative", "format"]

    def test_financial_analysis_empty(self, router):
        subtasks = router._determine_subtasks("financial_analysis", {})
        assert subtasks == []

    def test_market_analysis_empty(self, router):
        subtasks = router._determine_subtasks("market_analysis", {})
        assert subtasks == []

    def test_compliance_check_empty(self, router):
        subtasks = router._determine_subtasks("compliance_check", {})
        assert subtasks == []

    def test_unknown_task_type_returns_empty(self, router):
        subtasks = router._determine_subtasks("unknown_type", {})
        assert subtasks == []


# ---------------------------------------------------------------------------
# _should_confirm
# ---------------------------------------------------------------------------


class TestShouldConfirm:
    def test_low_confidence_requires_confirmation(self, router):
        assert router._should_confirm(0.5, "blog_post", {"topic": "AI"}) is True

    def test_exactly_threshold_requires_confirmation(self, router):
        assert router._should_confirm(0.74, "blog_post", {"topic": "AI"}) is True

    def test_above_threshold_no_topic_requires_confirmation(self, router):
        assert router._should_confirm(0.9, "blog_post", {}) is True

    def test_budget_present_requires_confirmation(self, router):
        assert router._should_confirm(0.9, "blog_post", {"topic": "AI", "budget": 50.0}) is True

    def test_high_confidence_topic_no_budget_no_confirm(self, router):
        assert router._should_confirm(0.9, "blog_post", {"topic": "AI"}) is False

    def test_social_media_no_topic_rule_applied(self, router):
        # blog_post topic rule only applies to blog_post
        assert router._should_confirm(0.9, "social_media", {}) is False


# ---------------------------------------------------------------------------
# _determine_execution_strategy
# ---------------------------------------------------------------------------


class TestDetermineExecutionStrategy:
    def test_always_sequential(self, router):
        assert router._determine_execution_strategy("blog_post", {}) == "sequential"
        assert router._determine_execution_strategy("social_media", {"budget": 100}) == "sequential"


# ---------------------------------------------------------------------------
# _get_required_inputs
# ---------------------------------------------------------------------------


class TestGetRequiredInputs:
    def test_research_inputs(self, router):
        assert router._get_required_inputs("research", False) == ["topic", "keywords"]

    def test_creative_not_dependent(self, router):
        assert router._get_required_inputs("creative", False) == ["topic"]

    def test_creative_dependent(self, router):
        assert router._get_required_inputs("creative", True) == ["research_output"]

    def test_qa_inputs(self, router):
        assert router._get_required_inputs("qa", True) == ["creative_output"]

    def test_images_inputs(self, router):
        assert router._get_required_inputs("images", True) == ["creative_output"]

    def test_format_inputs(self, router):
        assert router._get_required_inputs("format", True) == ["creative_output", "images_output"]

    def test_unknown_stage_returns_empty(self, router):
        assert router._get_required_inputs("unknown_stage", False) == []


# ---------------------------------------------------------------------------
# plan_subtasks
# ---------------------------------------------------------------------------


class TestPlanSubtasks:
    def test_returns_correct_count(self, router):
        plans = router.plan_subtasks(
            "task-abcdefgh", "blog_post", ["research", "creative", "qa"], {}, "sequential"
        )
        assert len(plans) == 3

    def test_first_stage_not_requires_parent(self, router):
        plans = router.plan_subtasks("tid-12345678", "blog_post", ["research", "creative"], {})
        assert plans[0].requires_parent is False

    def test_second_stage_requires_parent_sequential(self, router):
        plans = router.plan_subtasks("tid-12345678", "blog_post", ["research", "creative"], {})
        assert plans[1].requires_parent is True

    def test_parallel_strategy_no_parent_requirement(self, router):
        plans = router.plan_subtasks(
            "tid-12345678", "blog_post", ["research", "creative"], {}, "parallel_where_possible"
        )
        assert all(not p.requires_parent for p in plans)

    def test_task_id_prefixed_with_stage(self, router):
        plans = router.plan_subtasks("abcdefgh-1234", "blog_post", ["research"], {})
        assert plans[0].task_id.startswith("research-")

    def test_parent_task_id_set(self, router):
        plans = router.plan_subtasks("abcdefgh-1234", "blog_post", ["research"], {})
        assert plans[0].parent_task_id == "abcdefgh-1234"

    def test_first_two_stages_priority_one(self, router):
        plans = router.plan_subtasks(
            "tid-12345678", "blog_post", ["research", "creative", "qa"], {}
        )
        assert plans[0].priority == 1
        assert plans[1].priority == 1
        assert plans[2].priority == 2

    def test_estimated_duration_from_lookup(self, router):
        plans = router.plan_subtasks("tid-12345678", "blog_post", ["research"], {})
        assert plans[0].estimated_duration_ms == 15000

    def test_unknown_stage_uses_default_duration(self, router):
        plans = router.plan_subtasks("tid-12345678", "blog_post", ["unknown_stage"], {})
        assert plans[0].estimated_duration_ms == 5000

    def test_empty_subtasks_returns_empty_list(self, router):
        plans = router.plan_subtasks("tid-12345678", "financial_analysis", [], {})
        assert plans == []


# ---------------------------------------------------------------------------
# generate_execution_plan_summary
# ---------------------------------------------------------------------------


class TestGenerateExecutionPlanSummary:
    def _make_request(self, task_type="blog_post", confidence=0.9, requires_confirmation=False):
        return TaskIntentRequest(
            raw_input="Write a blog post about AI",
            intent_type="content_generation",
            task_type=task_type,
            confidence=confidence,
            parameters={},
            suggested_subtasks=[],
            requires_confirmation=requires_confirmation,
            execution_strategy="sequential",
        )

    def test_step_count_matches_plan(self, router):
        req = self._make_request()
        plans = router.plan_subtasks("tid-12345678", "blog_post", ["research", "creative"], {})
        summary = router.generate_execution_plan_summary(req, plans)
        assert len(summary["steps"]) == 2

    def test_total_estimated_time_matches_sum(self, router):
        req = self._make_request()
        plans = router.plan_subtasks("tid-12345678", "blog_post", ["research"], {})
        summary = router.generate_execution_plan_summary(req, plans)
        # research = 15000ms = 15s
        assert summary["total_estimated_time"] == "15s"

    def test_ready_to_execute_when_no_confirmation(self, router):
        req = self._make_request(requires_confirmation=False)
        summary = router.generate_execution_plan_summary(req, [])
        assert summary["next_action"] == "Ready to execute"

    def test_awaiting_confirmation_message(self, router):
        req = self._make_request(requires_confirmation=True)
        summary = router.generate_execution_plan_summary(req, [])
        assert "confirmation" in summary["next_action"].lower()

    def test_title_contains_task_type(self, router):
        req = self._make_request(task_type="blog_post")
        summary = router.generate_execution_plan_summary(req, [])
        assert "Blog Post" in summary["title"]

    def test_confidence_formatted_as_percentage(self, router):
        req = self._make_request(confidence=0.85)
        summary = router.generate_execution_plan_summary(req, [])
        assert summary["confidence"] == "85%"


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_under_1000ms_shows_ms(self, router):
        assert router._format_duration(500) == "500ms"

    def test_1000ms_shows_seconds(self, router):
        assert router._format_duration(1000) == "1s"

    def test_15000ms_shows_15s(self, router):
        assert router._format_duration(15000) == "15s"

    def test_60000ms_shows_minutes(self, router):
        assert router._format_duration(60000) == "1.0m"

    def test_90000ms_shows_1_5m(self, router):
        assert router._format_duration(90000) == "1.5m"


# ---------------------------------------------------------------------------
# route_user_input — mocked NLP recognizer
# ---------------------------------------------------------------------------


class TestRouteUserInput:
    @pytest.mark.asyncio
    async def test_unknown_intent_returns_generic(self, router):
        router.nlp_recognizer = MagicMock()
        router.nlp_recognizer.recognize_intent = AsyncMock(return_value=None)

        result = await router.route_user_input("do something weird")
        assert result.intent_type == "unknown"
        assert result.task_type == "generic"
        assert result.requires_confirmation is True
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_recognized_intent_maps_to_task_type(self, router):
        fake_match = MagicMock()
        fake_match.intent_type = "content_generation"
        fake_match.confidence = 0.95
        fake_match.parameters = {"topic": "AI trends"}

        router.nlp_recognizer = MagicMock()
        router.nlp_recognizer.recognize_intent = AsyncMock(return_value=fake_match)

        result = await router.route_user_input("Write a blog post about AI trends")
        assert result.task_type == "blog_post"
        assert result.intent_type == "content_generation"
        assert result.confidence == 0.95
        assert result.parameters["topic"] == "AI trends"

    @pytest.mark.asyncio
    async def test_unknown_intent_type_maps_to_generic_task(self, router):
        fake_match = MagicMock()
        fake_match.intent_type = "totally_new_intent"
        fake_match.confidence = 0.8
        fake_match.parameters = {}

        router.nlp_recognizer = MagicMock()
        router.nlp_recognizer.recognize_intent = AsyncMock(return_value=fake_match)

        result = await router.route_user_input("something new")
        assert result.task_type == "generic"

    @pytest.mark.asyncio
    async def test_returns_task_intent_request(self, router):
        router.nlp_recognizer = MagicMock()
        router.nlp_recognizer.recognize_intent = AsyncMock(return_value=None)

        result = await router.route_user_input("test")
        assert isinstance(result, TaskIntentRequest)
