"""
Unit tests for natural_language_schemas.py

Tests field validation and model behaviour for natural language schemas.
"""

import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from schemas.natural_language_schemas import (
    NaturalLanguageRequest,
    NaturalLanguageResponse,
    RefineContentRequest,
)


@pytest.mark.unit
class TestNaturalLanguageRequest:
    def test_valid_minimal(self):
        req = NaturalLanguageRequest(  # type: ignore[call-arg]
            prompt="Create a blog post about machine learning applications in healthcare"
        )
        assert req.auto_quality_check is True
        assert req.context is None

    def test_prompt_too_short_raises(self):
        with pytest.raises(ValidationError):
            NaturalLanguageRequest(prompt="short")  # < 10 chars  # type: ignore[call-arg]

    def test_prompt_too_long_raises(self):
        with pytest.raises(ValidationError):
            NaturalLanguageRequest(prompt="x" * 2001)  # type: ignore[call-arg]

    def test_prompt_at_minimum_length(self):
        req = NaturalLanguageRequest(prompt="1234567890")  # exactly 10 chars  # type: ignore[call-arg]
        assert len(req.prompt) == 10

    def test_prompt_at_maximum_length(self):
        req = NaturalLanguageRequest(prompt="x" * 2000)  # type: ignore[call-arg]
        assert len(req.prompt) == 2000

    def test_with_context(self):
        req = NaturalLanguageRequest(  # type: ignore[call-arg]
            prompt="Create a blog post about machine learning",
            context={"audience": "developers", "keywords": ["ML", "AI"]},
        )
        assert req.context == {"audience": "developers", "keywords": ["ML", "AI"]}

    def test_auto_quality_check_false(self):
        req = NaturalLanguageRequest(  # type: ignore[call-arg]
            prompt="Write a blog post about AI trends in 2026",
            auto_quality_check=False,
        )
        assert req.auto_quality_check is False

    def test_missing_prompt_raises(self):
        with pytest.raises(ValidationError):
            NaturalLanguageRequest()  # type: ignore[call-arg]


@pytest.mark.unit
class TestRefineContentRequest:
    def test_valid_minimal(self):
        req = RefineContentRequest(  # type: ignore[call-arg]
            feedback="The introduction needs more context and better flow."
        )
        assert req.focus_area is None

    def test_feedback_too_short_raises(self):
        with pytest.raises(ValidationError):
            RefineContentRequest(feedback="Too short")  # < 10 chars  # type: ignore[call-arg]

    def test_feedback_too_long_raises(self):
        with pytest.raises(ValidationError):
            RefineContentRequest(feedback="x" * 1001)  # type: ignore[call-arg]

    def test_with_focus_area(self):
        req = RefineContentRequest(
            feedback="The conclusion lacks a clear call to action.",
            focus_area="engagement",
        )
        assert req.focus_area == "engagement"

    def test_missing_feedback_raises(self):
        with pytest.raises(ValidationError):
            RefineContentRequest()  # type: ignore[call-arg]


@pytest.mark.unit
class TestNaturalLanguageResponse:
    def test_valid(self):
        resp = NaturalLanguageResponse(
            request_id="req-123",
            status="completed",
            request_type="content_generation",
            task_id="task-456",
            output="Generated content here",
            quality={"score": 92.0},
            message="Content generated successfully",
            created_at=datetime.now(timezone.utc),
        )
        assert resp.status == "completed"
        assert resp.task_id == "task-456"

    def test_pending_status(self):
        resp = NaturalLanguageResponse(
            request_id="req-789",
            status="pending",
            request_type="content_generation",
            task_id=None,
            output=None,
            quality=None,
            message="Request queued",
            created_at=datetime.now(timezone.utc),
        )
        assert resp.status == "pending"
        assert resp.task_id is None
        assert resp.output is None

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            NaturalLanguageResponse(  # type: ignore[call-arg]
                request_id="req-123",
                # missing status
                request_type="content_generation",
                task_id=None,
                output=None,
                quality=None,
                message="Done",
                created_at=datetime.now(timezone.utc),
            )
