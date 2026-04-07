"""
Unit tests for utils.constraint_utils module.

All tests are pure — zero DB, LLM, or network calls.
Covers:
- count_words_in_content
- validate_constraints
- calculate_phase_targets
- check_tolerance
- apply_strict_mode
- merge_compliance_reports
"""

import pytest

from utils.constraint_utils import (
    ConstraintCompliance,
    ContentConstraints,
    apply_strict_mode,
    calculate_phase_targets,
    check_tolerance,
    count_words_in_content,
    merge_compliance_reports,
    validate_constraints,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_compliance(
    actual: int = 1000,
    target: int = 1000,
    within: bool = True,
    pct: float = 0.0,
    style: str = "educational",
    strict: bool = False,
    violation: str | None = None,
) -> ConstraintCompliance:
    return ConstraintCompliance(
        word_count_actual=actual,
        word_count_target=target,
        word_count_within_tolerance=within,
        word_count_percentage=pct,
        writing_style_applied=style,
        strict_mode_enforced=strict,
        violation_message=violation,
    )


# ---------------------------------------------------------------------------
# count_words_in_content
# ---------------------------------------------------------------------------


class TestCountWordsInContent:
    """Tests for count_words_in_content."""

    def test_counts_simple_sentence(self):
        assert count_words_in_content("hello world foo bar") == 4

    def test_returns_zero_for_empty_string(self):
        assert count_words_in_content("") == 0

    def test_returns_zero_for_none(self):
        assert count_words_in_content(None) == 0  # type: ignore[arg-type]

    def test_handles_multiple_spaces(self):
        assert count_words_in_content("  one   two   three  ") == 3

    def test_handles_newlines(self):
        assert count_words_in_content("line one\nline two") == 4

    def test_single_word(self):
        assert count_words_in_content("word") == 1


# ---------------------------------------------------------------------------
# validate_constraints
# ---------------------------------------------------------------------------


class TestValidateConstraints:
    """Tests for validate_constraints."""

    def _content_of_words(self, n: int) -> str:
        return " ".join(["word"] * n)

    def test_content_within_tolerance_is_passing(self):
        constraints = ContentConstraints(word_count=1000, word_count_tolerance=10)
        content = self._content_of_words(1000)
        result = validate_constraints(content, constraints)
        assert result.word_count_within_tolerance is True
        assert result.violation_message is None

    def test_content_too_short_generates_violation(self):
        constraints = ContentConstraints(word_count=1000, word_count_tolerance=10)
        content = self._content_of_words(800)  # 900 is min
        result = validate_constraints(content, constraints)
        assert result.word_count_within_tolerance is False
        assert result.violation_message is not None
        assert "too short" in result.violation_message

    def test_content_too_long_generates_violation(self):
        constraints = ContentConstraints(word_count=1000, word_count_tolerance=10)
        content = self._content_of_words(1200)  # 1100 is max
        result = validate_constraints(content, constraints)
        assert result.word_count_within_tolerance is False
        assert result.violation_message is not None
        assert "too long" in result.violation_message

    def test_word_count_override(self):
        constraints = ContentConstraints(word_count=1000)
        content = self._content_of_words(500)
        result = validate_constraints(content, constraints, word_count_target=500)
        assert result.word_count_within_tolerance is True

    def test_returns_correct_actual_word_count(self):
        constraints = ContentConstraints(word_count=1000)
        content = self._content_of_words(750)
        result = validate_constraints(content, constraints)
        assert result.word_count_actual == 750

    def test_returns_correct_target_word_count(self):
        constraints = ContentConstraints(word_count=1500)
        content = self._content_of_words(1500)
        result = validate_constraints(content, constraints)
        assert result.word_count_target == 1500

    def test_percentage_diff_positive_when_over(self):
        constraints = ContentConstraints(word_count=1000, word_count_tolerance=5)
        content = self._content_of_words(1200)
        result = validate_constraints(content, constraints)
        assert result.word_count_percentage == pytest.approx(20.0)

    def test_percentage_diff_negative_when_under(self):
        constraints = ContentConstraints(word_count=1000, word_count_tolerance=5)
        content = self._content_of_words(800)
        result = validate_constraints(content, constraints)
        assert result.word_count_percentage == pytest.approx(-20.0)

    def test_writing_style_recorded(self):
        constraints = ContentConstraints(writing_style="narrative")
        content = self._content_of_words(1000)
        result = validate_constraints(content, constraints)
        assert result.writing_style_applied == "narrative"

    def test_strict_mode_recorded(self):
        constraints = ContentConstraints(strict_mode=True)
        content = self._content_of_words(1000)
        result = validate_constraints(content, constraints)
        assert result.strict_mode_enforced is True


# ---------------------------------------------------------------------------
# calculate_phase_targets
# ---------------------------------------------------------------------------


class TestCalculatePhaseTargets:
    """Tests for calculate_phase_targets."""

    def test_creative_phase_gets_full_word_count(self):
        constraints = ContentConstraints()
        targets = calculate_phase_targets(1000, constraints)
        assert targets["creative"] == 1000

    def test_qa_phase_gets_fifteen_percent(self):
        constraints = ContentConstraints()
        targets = calculate_phase_targets(1000, constraints)
        assert targets["qa"] == 150

    def test_research_phase_gets_zero(self):
        constraints = ContentConstraints()
        targets = calculate_phase_targets(1000, constraints)
        assert targets["research"] == 0

    def test_format_phase_gets_zero(self):
        constraints = ContentConstraints()
        targets = calculate_phase_targets(1000, constraints)
        assert targets["format"] == 0

    def test_finalize_phase_gets_zero(self):
        constraints = ContentConstraints()
        targets = calculate_phase_targets(1000, constraints)
        assert targets["finalize"] == 0

    def test_per_phase_overrides_applied(self):
        overrides = {"creative": 800, "qa": 50}
        constraints = ContentConstraints(per_phase_overrides=overrides)
        targets = calculate_phase_targets(1000, constraints)
        assert targets["creative"] == 800
        assert targets["qa"] == 50

    def test_partial_overrides_use_defaults_for_missing(self):
        overrides = {"creative": 900}
        constraints = ContentConstraints(per_phase_overrides=overrides)
        targets = calculate_phase_targets(1000, constraints)
        assert targets["creative"] == 900
        assert targets["qa"] == 150  # default

    def test_num_phases_limits_returned_phases(self):
        constraints = ContentConstraints()
        targets = calculate_phase_targets(1000, constraints, num_phases=2)
        assert len(targets) == 2
        assert "research" in targets
        assert "creative" in targets


# ---------------------------------------------------------------------------
# check_tolerance
# ---------------------------------------------------------------------------


class TestCheckTolerance:
    """Tests for check_tolerance."""

    def test_within_tolerance_returns_true(self):
        within, pct = check_tolerance(1000, 1000, 10)
        assert within is True
        assert pct == pytest.approx(0.0)

    def test_above_tolerance_returns_false(self):
        within, pct = check_tolerance(1200, 1000, 10)
        assert within is False
        assert pct == pytest.approx(20.0)

    def test_below_tolerance_returns_false(self):
        within, pct = check_tolerance(800, 1000, 10)
        assert within is False
        assert pct == pytest.approx(-20.0)

    def test_zero_target_returns_false(self):
        within, pct = check_tolerance(100, 0, 10)
        assert within is False


# ---------------------------------------------------------------------------
# apply_strict_mode
# ---------------------------------------------------------------------------


class TestApplyStrictMode:
    """Tests for apply_strict_mode."""

    def test_non_strict_always_valid(self):
        compliance = _make_compliance(within=False, strict=False)
        valid, msg = apply_strict_mode(compliance)
        assert valid is True
        assert msg == ""

    def test_strict_mode_passes_when_within_tolerance(self):
        compliance = _make_compliance(within=True, strict=True)
        valid, msg = apply_strict_mode(compliance)
        assert valid is True

    def test_strict_mode_fails_when_outside_tolerance(self):
        compliance = _make_compliance(within=False, strict=True, violation="Too short")
        valid, msg = apply_strict_mode(compliance)
        assert valid is False
        assert "Too short" in msg


# ---------------------------------------------------------------------------
# merge_compliance_reports
# ---------------------------------------------------------------------------


class TestMergeComplianceReports:
    """Tests for merge_compliance_reports."""

    def test_empty_list_returns_zero_report(self):
        result = merge_compliance_reports([])
        assert result.word_count_actual == 0
        assert result.word_count_target == 0
        assert result.word_count_within_tolerance is False

    def test_single_report_passes_through(self):
        report = _make_compliance(actual=1000, target=1000, within=True)
        result = merge_compliance_reports([report])
        assert result.word_count_actual == 1000
        assert result.word_count_within_tolerance is True

    def test_multiple_reports_totals_sum(self):
        r1 = _make_compliance(actual=500, target=500, within=True)
        r2 = _make_compliance(actual=600, target=600, within=True)
        result = merge_compliance_reports([r1, r2])
        assert result.word_count_actual == 1100
        assert result.word_count_target == 1100

    def test_all_within_tolerance_is_true(self):
        r1 = _make_compliance(within=True)
        r2 = _make_compliance(within=True)
        result = merge_compliance_reports([r1, r2])
        assert result.word_count_within_tolerance is True

    def test_one_violation_makes_overall_false(self):
        r1 = _make_compliance(within=True)
        r2 = _make_compliance(within=False, violation="Content too short: 500 words")
        result = merge_compliance_reports([r1, r2])
        assert result.word_count_within_tolerance is False

    def test_first_violation_message_included(self):
        r1 = _make_compliance(within=False, violation="First violation")
        r2 = _make_compliance(within=False, violation="Second violation")
        result = merge_compliance_reports([r1, r2])
        assert result.violation_message == "First violation"

    def test_no_violation_message_when_all_pass(self):
        r1 = _make_compliance(within=True)
        r2 = _make_compliance(within=True)
        result = merge_compliance_reports([r1, r2])
        assert result.violation_message is None

    def test_writing_style_taken_from_first_report(self):
        r1 = _make_compliance(style="technical")
        r2 = _make_compliance(style="narrative")
        result = merge_compliance_reports([r1, r2])
        assert result.writing_style_applied == "technical"

    def test_aggregate_percentage_computed(self):
        # total actual=2000, total target=1000 → 100%
        r1 = _make_compliance(actual=1500, target=1000, within=False)
        r2 = _make_compliance(actual=500, target=0, within=False)
        result = merge_compliance_reports([r1, r2])
        # target=1000 → percentage = (2000-1000)/1000 * 100 = 100
        assert result.word_count_percentage == pytest.approx(100.0)
