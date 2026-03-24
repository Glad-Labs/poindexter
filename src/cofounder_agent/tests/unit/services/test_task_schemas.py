"""
Unit tests for Pydantic task schemas.

Tests field validation, defaults, and model behaviour without any DB or LLM calls.
"""

import pytest
from pydantic import ValidationError

from schemas.task_schemas import (
    ApproveTaskRequest,
    ContentConstraints,
    TaskCreateRequest,
    UnifiedTaskRequest,
)
from schemas.task_status_schemas import TaskStatusUpdateRequest


# ---------------------------------------------------------------------------
# UnifiedTaskRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnifiedTaskRequest:
    def test_minimal_valid_request(self):
        req = UnifiedTaskRequest(topic="AI in Healthcare")  # type: ignore[call-arg]
        assert req.topic == "AI in Healthcare"
        assert req.task_type == "blog_post"  # default

    def test_topic_too_short_raises(self):
        with pytest.raises(ValidationError):
            UnifiedTaskRequest(topic="AI")  # type: ignore[call-arg]  # < 3 chars

    def test_topic_too_long_raises(self):
        with pytest.raises(ValidationError):
            UnifiedTaskRequest(topic="x" * 201)  # type: ignore[call-arg]

    def test_invalid_task_type_raises(self):
        with pytest.raises(ValidationError):
            UnifiedTaskRequest(topic="Valid topic", task_type="invalid_type")  # type: ignore[call-arg]

    def test_all_valid_task_types_accepted(self):
        valid_types = [
            "blog_post",
            "social_media",
            "email",
            "newsletter",
            "business_analytics",
            "data_retrieval",
            "market_research",
            "financial_analysis",
        ]
        for task_type in valid_types:
            req = UnifiedTaskRequest(topic="Some topic", task_type=task_type)  # type: ignore[call-arg]
            assert req.task_type == task_type

    def test_invalid_style_raises(self):
        with pytest.raises(ValidationError):
            UnifiedTaskRequest(topic="Valid topic", style="fancy")  # type: ignore[call-arg]

    def test_valid_tone_accepted(self):
        req = UnifiedTaskRequest(topic="Valid topic", tone="casual")  # type: ignore[call-arg]
        assert req.tone == "casual"

    def test_invalid_tone_raises(self):
        with pytest.raises(ValidationError):
            UnifiedTaskRequest(topic="Valid topic", tone="angry")  # type: ignore[call-arg]

    def test_target_length_below_min_raises(self):
        with pytest.raises(ValidationError):
            UnifiedTaskRequest(topic="Valid topic", target_length=199)  # type: ignore[call-arg]

    def test_target_length_above_max_raises(self):
        with pytest.raises(ValidationError):
            UnifiedTaskRequest(topic="Valid topic", target_length=5001)  # type: ignore[call-arg]

    def test_target_length_at_boundaries_accepted(self):
        low = UnifiedTaskRequest(topic="Valid topic", target_length=200)  # type: ignore[call-arg]
        high = UnifiedTaskRequest(topic="Valid topic", target_length=5000)  # type: ignore[call-arg]
        assert low.target_length == 200
        assert high.target_length == 5000

    def test_tags_over_limit_raises(self):
        with pytest.raises(ValidationError):
            UnifiedTaskRequest(topic="Valid topic", tags=["t"] * 11)  # type: ignore[call-arg]

    def test_quality_preference_invalid_raises(self):
        with pytest.raises(ValidationError):
            UnifiedTaskRequest(topic="Valid topic", quality_preference="ultra")  # type: ignore[call-arg]

    def test_quality_preference_valid_values(self):
        for pref in ("fast", "balanced", "quality"):
            req = UnifiedTaskRequest(topic="Valid topic", quality_preference=pref)  # type: ignore[call-arg]
            assert req.quality_preference == pref

    def test_optional_fields_default_to_none_or_defaults(self):
        req = UnifiedTaskRequest(topic="Valid topic")  # type: ignore[call-arg]
        assert req.tags is None
        assert req.platforms is None
        assert req.primary_keyword is None
        assert req.models_by_phase is None


# ---------------------------------------------------------------------------
# ContentConstraints
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentConstraints:
    def test_defaults_are_sensible(self):
        cc = ContentConstraints()
        assert cc.word_count == 1500
        assert cc.writing_style == "technical"
        assert cc.word_count_tolerance == 10
        assert cc.strict_mode is False

    def test_word_count_below_min_raises(self):
        with pytest.raises(ValidationError):
            ContentConstraints(word_count=299)

    def test_word_count_above_max_raises(self):
        with pytest.raises(ValidationError):
            ContentConstraints(word_count=5001)

    def test_word_count_at_boundaries_accepted(self):
        low = ContentConstraints(word_count=300)
        high = ContentConstraints(word_count=5000)
        assert low.word_count == 300
        assert high.word_count == 5000

    def test_invalid_writing_style_raises(self):
        with pytest.raises(ValidationError):
            ContentConstraints(writing_style="haiku")  # type: ignore[arg-type]

    def test_valid_writing_styles(self):
        valid = ["technical", "narrative", "listicle", "educational", "thought-leadership"]
        for style in valid:
            cc = ContentConstraints(writing_style=style)  # type: ignore[arg-type]
            assert cc.writing_style == style

    def test_tolerance_below_min_raises(self):
        with pytest.raises(ValidationError):
            ContentConstraints(word_count_tolerance=4)

    def test_tolerance_above_max_raises(self):
        with pytest.raises(ValidationError):
            ContentConstraints(word_count_tolerance=21)

    def test_per_phase_overrides_optional(self):
        cc = ContentConstraints(per_phase_overrides={"draft": 1200})
        assert cc.per_phase_overrides == {"draft": 1200}


# ---------------------------------------------------------------------------
# TaskCreateRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskCreateRequest:
    def test_minimal_valid_request(self):
        req = TaskCreateRequest(task_name="My Task", topic="My Topic")
        assert req.task_name == "My Task"
        assert req.topic == "My Topic"
        assert req.category == "general"
        assert req.quality_preference == "balanced"

    def test_task_name_too_short_raises(self):
        with pytest.raises(ValidationError):
            TaskCreateRequest(task_name="AB", topic="Valid topic")

    def test_task_name_too_long_raises(self):
        with pytest.raises(ValidationError):
            TaskCreateRequest(task_name="x" * 201, topic="Valid topic")

    def test_topic_too_short_raises(self):
        with pytest.raises(ValidationError):
            TaskCreateRequest(task_name="Valid name", topic="AB")

    def test_quality_preference_invalid_raises(self):
        with pytest.raises(ValidationError):
            TaskCreateRequest(
                task_name="Valid name",
                topic="Valid topic",
                quality_preference="unknown",
            )

    def test_quality_preference_valid_values(self):
        for pref in ("fast", "balanced", "quality"):
            req = TaskCreateRequest(
                task_name="Valid name", topic="Valid topic", quality_preference=pref
            )
            assert req.quality_preference == pref

    def test_estimated_cost_negative_raises(self):
        with pytest.raises(ValidationError):
            TaskCreateRequest(
                task_name="Valid name", topic="Valid topic", estimated_cost=-0.01
            )

    def test_category_too_long_raises(self):
        with pytest.raises(ValidationError):
            TaskCreateRequest(
                task_name="Valid name", topic="Valid topic", category="x" * 51
            )

    def test_primary_keyword_too_long_raises(self):
        with pytest.raises(ValidationError):
            TaskCreateRequest(
                task_name="Valid name", topic="Valid topic", primary_keyword="k" * 101
            )

    def test_target_audience_too_long_raises(self):
        with pytest.raises(ValidationError):
            TaskCreateRequest(
                task_name="Valid name", topic="Valid topic", target_audience="a" * 101
            )

    def test_content_constraints_embedded(self):
        req = TaskCreateRequest(
            task_name="Valid name",
            topic="Valid topic",
            content_constraints=ContentConstraints(word_count=2000, strict_mode=True),
        )
        assert req.content_constraints.word_count == 2000  # type: ignore[union-attr]
        assert req.content_constraints.strict_mode is True  # type: ignore[union-attr]

    def test_writing_style_id_optional(self):
        req = TaskCreateRequest(task_name="Valid name", topic="Valid topic")
        assert req.writing_style_id is None


# ---------------------------------------------------------------------------
# TaskStatusUpdateRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskStatusUpdateRequest:
    def test_minimal_valid_request(self):
        req = TaskStatusUpdateRequest(status="approved")  # type: ignore[call-arg]
        assert req.status == "approved"
        assert req.updated_by is None
        assert req.reason is None

    def test_status_is_required(self):
        with pytest.raises(ValidationError):
            TaskStatusUpdateRequest()  # type: ignore[call-arg]

    def test_all_optional_fields_accepted(self):
        req = TaskStatusUpdateRequest(
            status="published",
            updated_by="editor@example.com",
            reason="Content approved after review",
            result="Final post text",
            metadata={"score": 9.1},
        )
        assert req.updated_by == "editor@example.com"
        assert req.result == "Final post text"
        assert req.metadata["score"] == 9.1  # type: ignore[index]


# ---------------------------------------------------------------------------
# ApproveTaskRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApproveTaskRequest:
    def test_defaults(self):
        req = ApproveTaskRequest()  # type: ignore[call-arg]
        assert req.approved is True
        assert req.auto_publish is False
        assert req.human_feedback is None
        assert req.reviewer_id is None
        assert req.featured_image_url is None
        assert req.image_source is None

    def test_reject_with_feedback(self):
        req = ApproveTaskRequest(  # type: ignore[call-arg]
            approved=False,
            human_feedback="Needs more citations.",
            reviewer_id="editor@example.com",
        )
        assert req.approved is False
        assert req.human_feedback == "Needs more citations."

    def test_auto_publish_true_accepted(self):
        req = ApproveTaskRequest(approved=True, auto_publish=True)  # type: ignore[call-arg]
        assert req.auto_publish is True

    def test_image_fields_accepted(self):
        req = ApproveTaskRequest(  # type: ignore[call-arg]
            featured_image_url="https://example.com/img.jpg",
            image_source="pexels",
        )
        assert req.featured_image_url == "https://example.com/img.jpg"
        assert req.image_source == "pexels"
