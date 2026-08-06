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

from modules.content.content_validator import ValidationIssue, ValidationResult
from modules.content.multi_model_qa import (
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
        """A single reviewer's oversized feedback is capped individually
        (per_reviewer_max_chars), not by the old whole-blob max_chars —
        the whole-blob cap is a backstop for many reviewers, not the
        primary truncation mechanism for one verbose reviewer."""
        big = "y" * 8000
        out = format_qa_feedback_from_reviews(
            [{"reviewer": "x", "provider": "p", "score": 80,
              "approved": True, "feedback": big}],
            per_reviewer_max_chars=50,
        )
        assert "…" in out
        assert "y" * 51 not in out
        assert len(out) < 200

    def test_default_reviewer_provider(self):
        """Missing keys fall back to 'unknown'/'?'."""
        out = format_qa_feedback_from_reviews([
            {"score": 80, "approved": True, "feedback": "fine"},
        ])
        assert "unknown" in out
        assert "[?]" in out

    def test_low_score_reviewer_not_dropped_behind_verbose_ones(self):
        """Regression test for the 2026-07-13 incident: a low-score
        reviewer's line must survive even when several verbose reviewers
        ran before it in the pipeline's execution order."""
        verbose = "v" * 500
        reviews = [
            {"reviewer": "ollama_critic", "provider": "ollama", "score": 25,
             "approved": False, "feedback": verbose},
            {"reviewer": "deepeval_faithfulness", "provider": "deepeval",
             "score": 67, "approved": True, "feedback": verbose},
            {"reviewer": "ragas_eval", "provider": "ragas", "score": 50,
             "approved": True, "feedback": verbose},
            {"reviewer": "content_originality",
             "provider": "content_originality_gate", "score": 13.6,
             "approved": True,
             "feedback": (
                 "content near-duplicate of published post 'x' "
                 "(worst-chunk cosine 0.86 > 0.83)"
             )},
        ]
        out = format_qa_feedback_from_reviews(reviews, max_chars=1000)
        assert "content_originality" in out
        assert "near-duplicate" in out

    def test_reviewers_sorted_by_score_ascending(self):
        reviews = [
            {"reviewer": "high", "provider": "p", "score": 90,
             "approved": True, "feedback": "fine"},
            {"reviewer": "low", "provider": "p", "score": 10,
             "approved": False, "feedback": "bad"},
            {"reviewer": "mid", "provider": "p", "score": 50,
             "approved": True, "feedback": "meh"},
        ]
        out = format_qa_feedback_from_reviews(reviews)
        assert out.index("low") < out.index("mid") < out.index("high")

    def test_per_reviewer_cap_applies_individually(self):
        reviews = [
            {"reviewer": "verbose", "provider": "p", "score": 80,
             "approved": True, "feedback": "a" * 2000},
            {"reviewer": "terse", "provider": "p", "score": 85,
             "approved": True, "feedback": "short"},
        ]
        out = format_qa_feedback_from_reviews(reviews, per_reviewer_max_chars=50)
        assert "a" * 51 not in out
        assert "…" in out
        assert "short" in out
        assert "terse" in out

    def test_advisory_low_score_sorts_by_score_not_approved_flag(self):
        """Advisory rails report approved=True even when their own verdict
        failed (the real verdict is embedded in the feedback text) — the
        sort must use score, not the approved flag, or a failing advisory
        rail sorts as if it were fine."""
        reviews = [
            {"reviewer": "fine_rail", "provider": "p", "score": 95,
             "approved": True, "feedback": "all good"},
            {"reviewer": "content_originality",
             "provider": "content_originality_gate", "score": 13.6,
             "approved": True,
             "feedback": "[advisory] (failed, not required_to_pass) near-duplicate"},
        ]
        out = format_qa_feedback_from_reviews(reviews)
        assert out.index("content_originality") < out.index("fine_rail")

    def test_truncation_backstop_still_fires_with_many_reviewers(self):
        """The whole-blob max_chars backstop still applies when enough
        reviewers, even after per-reviewer capping, exceed it."""
        reviews = [
            {"reviewer": f"r{i}", "provider": "p", "score": 80,
             "approved": True, "feedback": "z" * 100}
            for i in range(10)
        ]
        out = format_qa_feedback_from_reviews(
            reviews, max_chars=300, per_reviewer_max_chars=100,
        )
        assert len(out) <= 300
        assert "...(truncated)" in out


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


# ---------------------------------------------------------------------------
# Critic review window (Glad-Labs/poindexter#985)
# ---------------------------------------------------------------------------


class TestBuildReviewExcerpt:
    """The critic must never see a bare mid-word slice of a complete
    article — that shape trips the rubric's unfinished-content auto-reject
    (the 2026-06-29 approval collapse: a complete 13K-char draft presented
    as prose cut mid-word at ``content[:8000]``)."""

    def test_short_content_passes_through_untouched(self):
        from modules.content.multi_model_qa import build_review_excerpt

        text, excerpted = build_review_excerpt("Short article. Done.", 24000)
        assert text == "Short article. Done."
        assert excerpted is False

    def test_long_content_cut_at_paragraph_boundary_with_marker(self):
        from modules.content.multi_model_qa import (
            REVIEW_EXCERPT_MARKER,
            build_review_excerpt,
        )

        paragraphs = [f"Paragraph {i} with several words in it." for i in range(200)]
        content = "\n\n".join(paragraphs)
        text, excerpted = build_review_excerpt(content, 2000)
        assert excerpted is True
        assert text.endswith(REVIEW_EXCERPT_MARKER)
        body = text[: -len(REVIEW_EXCERPT_MARKER)].rstrip()
        # Cut lands on a paragraph boundary — the body ends with a COMPLETE
        # paragraph, never mid-word.
        assert body.endswith("words in it.")
        assert len(body) <= 2000

    def test_no_boundary_falls_back_to_hard_cut_with_marker(self):
        from modules.content.multi_model_qa import (
            REVIEW_EXCERPT_MARKER,
            build_review_excerpt,
        )

        content = "x" * 5000  # no paragraph boundaries at all
        text, excerpted = build_review_excerpt(content, 2000)
        assert excerpted is True
        assert text.endswith(REVIEW_EXCERPT_MARKER)

    def test_excerpted_content_not_flagged_as_truncated(self):
        """Cross-gate invariant: an excerpt produced for the critic must not
        read as truncated to the #984 detector — the marker line ends
        terminally by construction."""
        from modules.content.content_validator import detect_truncated_content
        from modules.content.multi_model_qa import build_review_excerpt

        paragraphs = [f"Paragraph {i} with several words in it." for i in range(200)]
        text, _ = build_review_excerpt("\n\n".join(paragraphs), 2000)
        assert detect_truncated_content(text) == []
