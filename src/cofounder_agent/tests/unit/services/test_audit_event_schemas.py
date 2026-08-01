"""Unit tests for services/audit_event_schemas.py (poindexter#758).

Covers:
- QaPassCompletedDetails / FindingDetails — the two load-bearing shapes
- validate_event_details — registry lookup, pass-through for unregistered
  event_types, schema_version stamping, loud-log-not-raise on mismatch
"""

import logging

import pytest

from services.audit_event_schemas import (
    EVENT_SCHEMAS,
    FindingDetails,
    QaPassCompletedDetails,
    validate_event_details,
)

pytestmark = pytest.mark.unit


class TestQaPassCompletedDetails:
    def test_accepts_canonical_blog_shape(self):
        """qa_aggregate.py's shape — includes the rescue-loop fields."""
        model = QaPassCompletedDetails(
            approved=True,
            final_score=8.5,
            approval_threshold=7.0,
            reviewer_count=2,
            terminal=True,
            qa_rewrite_attempts=1,
            rescued=True,
            reviews=[
                {
                    "reviewer": "critic",
                    "provider": "ollama",
                    "approved": True,
                    "score": 8.5,
                    "advisory": False,
                    "feedback_preview": "looks good",
                }
            ],
        )
        assert model.terminal is True
        assert model.rescued is True

    def test_accepts_dev_diary_shape_without_rescue_fields(self):
        """multi_model_qa.py's shape — omits terminal/qa_rewrite_attempts/rescued."""
        model = QaPassCompletedDetails(
            approved=True,
            final_score=8.5,
            approval_threshold=7.0,
            reviewer_count=1,
            reviews=[],
        )
        assert model.terminal is None
        assert model.qa_rewrite_attempts is None
        assert model.rescued is None

    def test_missing_required_field_raises(self):
        with pytest.raises(Exception):
            QaPassCompletedDetails(final_score=8.5, approval_threshold=7.0, reviewer_count=1, reviews=[])


class TestFindingDetails:
    def test_accepts_minimal_shape(self):
        model = FindingDetails(kind="broken_link", title="t", body="b")
        assert model.dedup_key is None
        assert model.extra is None

    def test_accepts_dedup_key_and_extra(self):
        model = FindingDetails(
            kind="broken_link",
            title="t",
            body="b",
            dedup_key="broken-link:https://example.com",
            extra={"urls": ["https://example.com"]},
        )
        assert model.dedup_key == "broken-link:https://example.com"
        assert model.extra == {"urls": ["https://example.com"]}


class TestEventSchemasRegistry:
    def test_registers_exactly_the_load_bearing_types(self):
        # Opportunistic additions only (poindexter#758): each entry here is a
        # dashboard-load-bearing shape. The chat_* types power the Cofounder
        # panels (poindexter#947 tool calls; #949 turns + approval denies).
        assert set(EVENT_SCHEMAS.keys()) == {
            "qa_pass_completed",
            "finding",
            "chat_tool_call",
            "chat_turn_completed",
            "chat_approval_resolved",
            "chat_plan_run",
        }


class TestValidateEventDetails:
    def test_unregistered_event_type_passes_through_unchanged(self):
        details = {"anything": "goes", "here": 1}
        result = validate_event_details("job_run", details)
        assert result == details

    def test_none_details_passes_through(self):
        assert validate_event_details("finding", None) is None

    def test_valid_finding_details_gets_schema_version_stamped(self):
        details = {"kind": "broken_link", "title": "t", "body": "b"}
        result = validate_event_details("finding", details)
        assert result["schema_version"] == 1
        assert result["kind"] == "broken_link"

    def test_valid_qa_pass_completed_details_gets_schema_version_stamped(self):
        details = {
            "approved": True,
            "final_score": 8.5,
            "approval_threshold": 7.0,
            "reviewer_count": 0,
            "reviews": [],
        }
        result = validate_event_details("qa_pass_completed", details)
        assert result["schema_version"] == 1

    def test_mismatched_details_logs_loud_and_returns_original_unchanged(self, caplog):
        """A producer-side shape bug must surface in logs (fail loud) but
        must NEVER drop the row or raise into the caller — audit writes
        must never block/corrupt the pipeline (established pattern across
        every write_bg call site: 'best-effort, never raises')."""
        details = {"title": "t", "body": "b"}  # missing required `kind`
        with caplog.at_level(logging.ERROR):
            result = validate_event_details("finding", details)
        assert result == details
        assert "poindexter#758" in caplog.text or "758" in caplog.text

    def test_extra_unknown_fields_do_not_fail_validation(self):
        """The registry is deliberately permissive on extra keys — a new
        field a producer adds ahead of a schema update shouldn't itself
        trip the mismatch log."""
        details = {
            "kind": "broken_link",
            "title": "t",
            "body": "b",
            "some_new_field_not_yet_in_schema": "value",
        }
        result = validate_event_details("finding", details)
        assert result["some_new_field_not_yet_in_schema"] == "value"
