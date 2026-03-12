"""
Unit tests for orchestrator_schemas.py

Tests field validation and model behaviour for orchestrator schemas.
"""

import pytest
from pydantic import ValidationError

from schemas.orchestrator_schemas import (
    ApprovalAction,
    ProcessRequestBody,
    TrainingDataExportRequest,
    TrainingModelUploadRequest,
)


@pytest.mark.unit
class TestProcessRequestBody:
    def test_valid_minimal(self):
        req = ProcessRequestBody(  # type: ignore[call-arg]
            prompt="Create a blog post about the future of AI technology in enterprise"
        )
        assert req.auto_approve is False
        assert req.context is None

    def test_prompt_too_short_raises(self):
        with pytest.raises(ValidationError):
            ProcessRequestBody(prompt="too short")  # < 10 chars  # type: ignore[call-arg]

    def test_prompt_too_long_raises(self):
        with pytest.raises(ValidationError):
            ProcessRequestBody(prompt="x" * 2001)  # type: ignore[call-arg]

    def test_prompt_at_limits(self):
        req = ProcessRequestBody(prompt="1234567890")  # exactly 10 chars  # type: ignore[call-arg]
        assert len(req.prompt) == 10

    def test_with_context(self):
        req = ProcessRequestBody(  # type: ignore[call-arg]
            prompt="Write about machine learning in healthcare industry",
            context={"audience": "doctors", "tone": "professional"},
        )
        assert req.context == {"audience": "doctors", "tone": "professional"}

    def test_auto_approve(self):
        req = ProcessRequestBody(  # type: ignore[call-arg]
            prompt="Generate blog content about cloud computing trends",
            auto_approve=True,
        )
        assert req.auto_approve is True

    def test_missing_prompt_raises(self):
        with pytest.raises(ValidationError):
            ProcessRequestBody()  # type: ignore[call-arg]


@pytest.mark.unit
class TestApprovalAction:
    def test_valid_approved(self):
        action = ApprovalAction(approved=True)  # type: ignore[call-arg]
        assert action.publish_to_channels == ["blog"]
        assert action.modifications is None

    def test_multiple_channels(self):
        action = ApprovalAction(  # type: ignore[call-arg]
            approved=True,
            publish_to_channels=["blog", "linkedin", "twitter"],
        )
        assert len(action.publish_to_channels) == 3

    def test_rejected(self):
        action = ApprovalAction(approved=False)  # type: ignore[call-arg]
        assert action.approved is False

    def test_with_modifications(self):
        action = ApprovalAction(  # type: ignore[call-arg]
            approved=True,
            modifications={"title": "Updated Title", "add_disclaimer": True},
        )
        assert action.modifications == {"title": "Updated Title", "add_disclaimer": True}

    def test_missing_approved_raises(self):
        with pytest.raises(ValidationError):
            ApprovalAction()  # type: ignore[call-arg]


@pytest.mark.unit
class TestTrainingDataExportRequest:
    def test_valid_defaults(self):
        req = TrainingDataExportRequest()  # type: ignore[call-arg]
        assert req.format == "jsonl"
        assert req.min_quality_score is None
        assert req.limit == 1000

    def test_csv_format(self):
        req = TrainingDataExportRequest(format="csv")  # type: ignore[call-arg]
        assert req.format == "csv"

    def test_with_quality_filter(self):
        req = TrainingDataExportRequest(min_quality_score=0.85)  # type: ignore[call-arg]
        assert req.min_quality_score == 0.85

    def test_custom_limit(self):
        req = TrainingDataExportRequest(limit=500)  # type: ignore[call-arg]
        assert req.limit == 500


@pytest.mark.unit
class TestTrainingModelUploadRequest:
    def test_valid(self):
        req = TrainingModelUploadRequest(  # type: ignore[call-arg]
            model_name="my-fine-tuned-model",
            model_type="content-generator",
        )
        assert req.description is None

    def test_with_description(self):
        req = TrainingModelUploadRequest(
            model_name="task-router-v2",
            model_type="task-router",
            description="Fine-tuned on 10k examples",
        )
        assert req.description == "Fine-tuned on 10k examples"

    def test_all_model_types(self):
        for model_type in ["task-router", "content-generator", "quality-evaluator"]:
            req = TrainingModelUploadRequest(  # type: ignore[call-arg]
                model_name="model-v1",
                model_type=model_type,
            )
            assert req.model_type == model_type

    def test_missing_model_name_raises(self):
        with pytest.raises(ValidationError):
            TrainingModelUploadRequest(model_type="task-router")  # type: ignore[call-arg]

    def test_missing_model_type_raises(self):
        with pytest.raises(ValidationError):
            TrainingModelUploadRequest(model_name="my-model")  # type: ignore[call-arg]
