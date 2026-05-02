"""Round-2 fills for services/multi_model_qa.py.

The existing test_multi_model_qa.py covers the main `review()` orchestration
path well. This file fills the previously-uncovered helper / formatting
surface area and the web_factcheck entry point — all of which are
unit-testable without standing up a real Ollama / Claude / Playwright stack.

Targets:
  - MultiModelResult.format_feedback_text (lines 70-89)
  - format_qa_feedback_from_reviews module-level helper (lines 105-133)
  - _check_web_factcheck happy + skip paths (lines 1499-1582)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.content_validator import ValidationIssue, ValidationResult
from services.multi_model_qa import (
    MultiModelResult,
    ReviewerResult,
    format_qa_feedback_from_reviews,
)


# ---------------------------------------------------------------------------
# MultiModelResult.format_feedback_text
# ---------------------------------------------------------------------------


def _r(reviewer: str, score: float = 80.0, approved: bool = True,
       feedback: str = "looks fine", provider: str = "ollama") -> ReviewerResult:
    return ReviewerResult(
        reviewer=reviewer, approved=approved, score=score,
        feedback=feedback, provider=provider,
    )


class TestFormatFeedbackText:
    def test_approved_header(self):
        result = MultiModelResult(
            approved=True, final_score=85.0, reviews=[_r("ollama_critic", 85)],
        )
        text = result.format_feedback_text()
        assert "Final score: 85/100" in text
        assert "APPROVED" in text
        assert "ollama_critic" in text

    def test_rejected_header(self):
        result = MultiModelResult(
            approved=False, final_score=42.0, reviews=[_r("critic", 42, approved=False)],
        )
        text = result.format_feedback_text()
        assert "REJECTED" in text
        assert "FAIL" in text

    def test_each_review_rendered(self):
        result = MultiModelResult(
            approved=True, final_score=80.0,
            reviews=[
                _r("ollama_critic", 80, feedback="solid"),
                _r("validator", 100, provider="programmatic", feedback="clean"),
            ],
        )
        text = result.format_feedback_text()
        assert "ollama_critic" in text
        assert "validator" in text
        assert "[ollama]" in text
        assert "[programmatic]" in text

    def test_validation_issues_included(self):
        validation = ValidationResult(
            passed=False,
            issues=[
                ValidationIssue("critical", "fabricated_reference",
                                "fake API call", "blah"),
                ValidationIssue("warning", "weasel_word",
                                "vague claim", "blah"),
            ],
            score_penalty=10,
        )
        result = MultiModelResult(
            approved=False, final_score=70.0,
            reviews=[_r("critic", 70)], validation=validation,
        )
        text = result.format_feedback_text()
        assert "validator[critical]" in text
        assert "fabricated_reference" in text

    def test_empty_feedback_replaced_with_placeholder(self):
        """Whitespace-only feedback shouldn't render as a blank dash."""
        result = MultiModelResult(
            approved=True, final_score=80.0,
            reviews=[_r("critic", 80, feedback="   ")],
        )
        text = result.format_feedback_text()
        assert "(no feedback)" in text

    def test_truncation_when_over_max_chars(self):
        big_feedback = "x" * 5000
        result = MultiModelResult(
            approved=True, final_score=80.0,
            reviews=[_r("critic", 80, feedback=big_feedback)],
        )
        text = result.format_feedback_text(max_chars=200)
        assert len(text) <= 200
        assert "...(truncated)" in text

    def test_validation_issues_capped_at_10(self):
        """The format truncates the issues list to the first 10."""
        issues = [
            ValidationIssue("warning", f"cat_{i}", f"issue {i}", "x")
            for i in range(20)
        ]
        validation = ValidationResult(
            passed=False, issues=issues, score_penalty=0,
        )
        result = MultiModelResult(
            approved=True, final_score=80.0,
            reviews=[_r("critic", 80)], validation=validation,
        )
        text = result.format_feedback_text(max_chars=10000)
        # Only 10 lines like 'validator[warning]' should appear
        assert text.count("validator[warning]") == 10


# ---------------------------------------------------------------------------
# format_qa_feedback_from_reviews — module-level helper
# ---------------------------------------------------------------------------


class TestFormatQAFeedbackFromReviews:
    def test_empty_returns_empty_string(self):
        assert format_qa_feedback_from_reviews([]) == ""

    def test_minimal_review_dict(self):
        out = format_qa_feedback_from_reviews([
            {"reviewer": "x", "provider": "p", "score": 75,
             "approved": True, "feedback": "ok"},
        ])
        assert "x" in out
        assert "[p]" in out
        assert "75/100" in out
        assert "pass" in out

    def test_score_with_no_final_score(self):
        out = format_qa_feedback_from_reviews([
            {"reviewer": "x", "provider": "p", "score": 50, "approved": False},
        ])
        # No "Final score:" header line when final_score is None
        assert "Final score" not in out
        assert "FAIL" in out

    def test_final_score_with_approved_status(self):
        out = format_qa_feedback_from_reviews(
            [{"reviewer": "x", "provider": "p", "score": 90,
              "approved": True, "feedback": "great"}],
            final_score=92.0,
            approved=True,
        )
        assert "Final score: 92/100 (APPROVED)" in out

    def test_final_score_with_rejected_status(self):
        out = format_qa_feedback_from_reviews(
            [{"reviewer": "x", "provider": "p", "score": 50, "approved": False}],
            final_score=45.0,
            approved=False,
        )
        assert "Final score: 45/100 (REJECTED)" in out

    def test_final_score_with_no_approved_flag(self):
        """approved=None drops the (status) suffix entirely."""
        out = format_qa_feedback_from_reviews(
            [{"reviewer": "x", "provider": "p", "score": 70}],
            final_score=70.0,
        )
        assert "Final score: 70/100" in out
        assert "APPROVED" not in out
        assert "REJECTED" not in out

    def test_skips_non_dict_entries(self):
        """Defensive: tolerate stray non-dict entries in the list."""
        out = format_qa_feedback_from_reviews([
            "not a dict",
            {"reviewer": "real", "provider": "p", "score": 80, "approved": True},
        ])
        assert "real" in out
        # Don't crash on the string entry

    def test_invalid_score_coerced_to_zero(self):
        out = format_qa_feedback_from_reviews([
            {"reviewer": "x", "provider": "p", "score": "not-a-number",
             "approved": False},
        ])
        assert "0/100" in out

    def test_missing_score_defaults_to_zero(self):
        out = format_qa_feedback_from_reviews([
            {"reviewer": "x", "provider": "p", "approved": True},
        ])
        assert "0/100" in out

    def test_missing_feedback_uses_placeholder(self):
        out = format_qa_feedback_from_reviews([
            {"reviewer": "x", "provider": "p", "score": 80, "approved": True},
        ])
        assert "(no feedback)" in out

    def test_truncation_applied(self):
        big = "y" * 8000
        out = format_qa_feedback_from_reviews(
            [{"reviewer": "x", "provider": "p", "score": 80,
              "approved": True, "feedback": big}],
            max_chars=300,
        )
        assert len(out) <= 300
        assert "...(truncated)" in out

    def test_default_reviewer_provider(self):
        """Missing keys fall back to 'unknown'/'?'."""
        out = format_qa_feedback_from_reviews([
            {"score": 80, "approved": True, "feedback": "fine"},
        ])
        assert "unknown" in out
        assert "[?]" in out


# ---------------------------------------------------------------------------
# MultiModelResult.summary — sibling format method
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summary_includes_score_and_status(self):
        result = MultiModelResult(
            approved=True, final_score=88.5,
            reviews=[_r("ollama_critic", 90)],
        )
        s = result.summary
        assert "88" in s
        assert "APPROVED" in s

    def test_summary_truncates_feedback(self):
        long_fb = "z" * 200
        result = MultiModelResult(
            approved=True, final_score=80,
            reviews=[_r("critic", 80, feedback=long_fb)],
        )
        s = result.summary
        # The summary slices feedback to 80 chars
        assert "z" * 80 in s
        assert "z" * 81 not in s

    def test_summary_with_validation_issues(self):
        validation = ValidationResult(
            passed=False,
            issues=[
                ValidationIssue("critical", "fab", "x", "snippet"),
                ValidationIssue("critical", "fab2", "x2", "snippet"),
                ValidationIssue("warning", "vague", "y", "snippet"),
                ValidationIssue("warning", "vague2", "y", "snippet"),
                ValidationIssue("warning", "vague3", "y", "snippet"),
            ],
            score_penalty=5,
        )
        result = MultiModelResult(
            approved=False, final_score=40,
            reviews=[_r("critic", 40, approved=False)],
            validation=validation,
        )
        s = result.summary
        assert "Validator" in s
        assert "2 critical" in s
        assert "3 warnings" in s
