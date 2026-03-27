"""
Unit tests for content_schemas.py

Tests field validation and model behaviour for content creation schemas.
"""

import pytest
from pydantic import ValidationError

from schemas.content_schemas import (
    ApprovalRequest,
    ApprovalResponse,
    BlogDraftResponse,
    ContentStyle,
    ContentTone,
    CreateBlogPostRequest,
    DraftsListResponse,
    GenerateAndPublishRequest,
    PublishDraftRequest,
    PublishMode,
    TaskStatusResponse,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentEnums:
    def test_content_style_values(self):
        assert ContentStyle.TECHNICAL == "technical"
        assert ContentStyle.NARRATIVE == "narrative"
        assert ContentStyle.LISTICLE == "listicle"
        assert ContentStyle.EDUCATIONAL == "educational"
        assert ContentStyle.THOUGHT_LEADERSHIP == "thought-leadership"

    def test_content_tone_values(self):
        assert ContentTone.PROFESSIONAL == "professional"
        assert ContentTone.CASUAL == "casual"
        assert ContentTone.ACADEMIC == "academic"
        assert ContentTone.INSPIRATIONAL == "inspirational"

    def test_publish_mode_values(self):
        assert PublishMode.DRAFT == "draft"
        assert PublishMode.PUBLISH == "publish"


# ---------------------------------------------------------------------------
# CreateBlogPostRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateBlogPostRequest:
    def _valid(self, **kwargs):
        defaults = {"topic": "AI in Healthcare"}
        defaults.update(kwargs)
        return CreateBlogPostRequest(**defaults)  # type: ignore[arg-type]

    def test_minimal_valid_request(self):
        req = self._valid()
        assert req.task_type == "blog_post"
        assert req.style == ContentStyle.TECHNICAL
        assert req.tone == ContentTone.PROFESSIONAL
        assert req.target_length == 1500
        assert req.generate_featured_image is True
        assert req.publish_mode == PublishMode.DRAFT
        assert req.enhanced is False
        assert req.target_environment == "production"

    def test_topic_too_short_raises(self):
        with pytest.raises(ValidationError):
            self._valid(topic="AI")

    def test_topic_too_long_raises(self):
        with pytest.raises(ValidationError):
            self._valid(topic="x" * 201)

    def test_valid_task_types(self):
        for task_type in ["blog_post", "social_media", "email", "newsletter"]:
            req = self._valid(task_type=task_type)
            assert req.task_type == task_type

    def test_invalid_task_type_raises(self):
        with pytest.raises(ValidationError):
            self._valid(task_type="unknown_type")  # type: ignore[arg-type]

    def test_target_length_minimum(self):
        req = self._valid(target_length=200)
        assert req.target_length == 200

    def test_target_length_maximum(self):
        req = self._valid(target_length=5000)
        assert req.target_length == 5000

    def test_target_length_too_short_raises(self):
        with pytest.raises(ValidationError):
            self._valid(target_length=199)

    def test_target_length_too_long_raises(self):
        with pytest.raises(ValidationError):
            self._valid(target_length=5001)

    def test_valid_environments(self):
        for env in ["development", "staging", "production"]:
            req = self._valid(target_environment=env)
            assert req.target_environment == env

    def test_invalid_environment_raises(self):
        with pytest.raises(ValidationError):
            self._valid(target_environment="local")

    def test_valid_quality_preferences(self):
        for pref in ["fast", "balanced", "quality"]:
            req = self._valid(quality_preference=pref)
            assert req.quality_preference == pref

    def test_invalid_quality_preference_raises(self):
        with pytest.raises(ValidationError):
            self._valid(quality_preference="ultra")  # type: ignore[arg-type]

    def test_all_content_styles(self):
        for style in ContentStyle:
            req = self._valid(style=style)
            assert req.style == style

    def test_all_content_tones(self):
        for tone in ContentTone:
            req = self._valid(tone=tone)
            assert req.tone == tone

    def test_models_by_phase(self):
        req = self._valid(
            models_by_phase={
                "research": "ultra_cheap",
                "draft": "premium",
            }
        )
        assert req.models_by_phase == {"research": "ultra_cheap", "draft": "premium"}

    def test_llm_provider_optional(self):
        req = self._valid(llm_provider="anthropic")
        assert req.llm_provider == "anthropic"

    def test_with_tags(self):
        req = self._valid(tags=["AI", "Healthcare"])
        assert req.tags == ["AI", "Healthcare"]

    def test_with_categories(self):
        req = self._valid(categories=["Technology"])
        assert req.categories == ["Technology"]


# ---------------------------------------------------------------------------
# TaskStatusResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskStatusResponse:
    def test_valid(self):
        resp = TaskStatusResponse(
            task_id="task-123",
            status="generating",
            created_at="2026-01-01T00:00:00Z",
        )
        assert resp.task_id == "task-123"
        assert resp.progress is None
        assert resp.result is None
        assert resp.error is None

    def test_with_all_fields(self):
        resp = TaskStatusResponse(
            task_id="task-123",
            status="completed",
            progress={"percentage": 100},
            result={"content": "..."},
            error=None,
            created_at="2026-01-01T00:00:00Z",
        )
        assert resp.result == {"content": "..."}


# ---------------------------------------------------------------------------
# BlogDraftResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBlogDraftResponse:
    def test_valid(self):
        draft = BlogDraftResponse(
            draft_id="draft-456",
            title="My Blog Post",
            created_at="2026-01-01T00:00:00Z",
            status="draft",
            word_count=1500,
        )
        assert draft.summary is None

    def test_with_summary(self):
        draft = BlogDraftResponse(
            draft_id="draft-456",
            title="My Blog Post",
            created_at="2026-01-01T00:00:00Z",
            status="draft",
            word_count=1500,
            summary="A brief summary of the post.",
        )
        assert draft.summary == "A brief summary of the post."


# ---------------------------------------------------------------------------
# DraftsListResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDraftsListResponse:
    def test_valid(self):
        draft = BlogDraftResponse(
            draft_id="draft-1",
            title="Post 1",
            created_at="2026-01-01T00:00:00Z",
            status="draft",
            word_count=1000,
        )
        resp = DraftsListResponse(
            drafts=[draft],
            total=1,
            limit=10,
            offset=0,
        )
        assert resp.total == 1
        assert len(resp.drafts) == 1


# ---------------------------------------------------------------------------
# PublishDraftRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPublishDraftRequest:
    def test_defaults_to_production(self):
        req = PublishDraftRequest()  # type: ignore[call-arg]
        assert req.target_environment == "production"

    def test_valid_environments(self):
        for env in ["development", "staging", "production"]:
            req = PublishDraftRequest(target_environment=env)
            assert req.target_environment == env

    def test_invalid_environment_raises(self):
        with pytest.raises(ValidationError):
            PublishDraftRequest(target_environment="test")


# ---------------------------------------------------------------------------
# ApprovalRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalRequest:
    def _valid(self, **kwargs):
        defaults = {
            "approved": True,
            "human_feedback": "Excellent content! Well-researched and approved.",
            "reviewer_id": "john.doe",
        }
        defaults.update(kwargs)
        return ApprovalRequest(**defaults)

    def test_valid_approval(self):
        req = self._valid()
        assert req.approved is True
        assert req.featured_image_url is None

    def test_rejection(self):
        req = self._valid(approved=False, human_feedback="Needs significant revision here.")
        assert req.approved is False

    def test_feedback_too_short_raises(self):
        with pytest.raises(ValidationError):
            self._valid(human_feedback="Too short")

    def test_feedback_too_long_raises(self):
        with pytest.raises(ValidationError):
            self._valid(human_feedback="x" * 1001)

    def test_invalid_reviewer_id_raises(self):
        with pytest.raises(ValidationError):
            self._valid(reviewer_id="john doe")  # space not allowed

    def test_reviewer_id_with_dot_and_dash(self):
        req = self._valid(reviewer_id="john.doe-123")
        assert req.reviewer_id == "john.doe-123"

    def test_reviewer_id_too_short_raises(self):
        with pytest.raises(ValidationError):
            self._valid(reviewer_id="j")

    def test_reviewer_id_too_long_raises(self):
        with pytest.raises(ValidationError):
            self._valid(reviewer_id="a" * 101)

    def test_with_featured_image_url(self):
        req = self._valid(featured_image_url="https://images.pexels.com/photo.jpg")
        assert req.featured_image_url == "https://images.pexels.com/photo.jpg"


# ---------------------------------------------------------------------------
# ApprovalResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalResponse:
    def test_approved(self):
        resp = ApprovalResponse(  # type: ignore[call-arg]
            task_id="task-123",
            approval_status="approved",
            approval_timestamp="2026-01-01T00:00:00Z",
            reviewer_id="john.doe",
            message="Approved and published",
            published_url="https://example.com/post",
            post_id="42",  # type: ignore[call-arg]
        )
        assert resp.approval_status == "approved"
        assert resp.strapi_post_id is None

    def test_rejected(self):
        resp = ApprovalResponse(
            task_id="task-456",
            approval_status="rejected",
            approval_timestamp="2026-01-01T00:00:00Z",
            reviewer_id="jane.doe",
            message="Content needs improvement",
        )
        assert resp.published_url is None
        assert resp.strapi_post_id is None


# ---------------------------------------------------------------------------
# GenerateAndPublishRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateAndPublishRequest:
    def _valid(self, **kwargs):
        defaults = {"topic": "Machine Learning in Finance"}
        defaults.update(kwargs)
        return GenerateAndPublishRequest(**defaults)  # type: ignore[arg-type]

    def test_valid_minimal(self):
        req = self._valid()
        assert req.audience == "General audience"
        assert req.length == "medium"
        assert req.auto_publish is False

    def test_topic_too_short_raises(self):
        with pytest.raises(ValidationError):
            self._valid(topic="AI")

    def test_topic_too_long_raises(self):
        with pytest.raises(ValidationError):
            self._valid(topic="x" * 201)

    def test_valid_lengths(self):
        for length in ["short", "medium", "long"]:
            req = self._valid(length=length)
            assert req.length == length

    def test_invalid_length_raises(self):
        with pytest.raises(ValidationError):
            self._valid(length="extra-long")

    def test_with_keywords_and_tags(self):
        req = self._valid(
            keywords=["AI", "finance"],
            tags=["Tech", "Finance"],
        )
        assert req.keywords == ["AI", "finance"]
        assert req.tags == ["Tech", "Finance"]

    def test_auto_publish_true(self):
        req = self._valid(auto_publish=True)
        assert req.auto_publish is True
