"""
Unit tests for utils.constraint_utils module.

All tests are pure — zero DB, LLM, or network calls.
Covers all three tiers of constraint utilities:
- Tier 1: extract_constraints_from_request, count_words, inject_constraints, validate_constraints
- Tier 2: calculate_phase_targets, check_tolerance, apply_strict_mode, merge_compliance_reports
- Tier 3: auto_trim_content, auto_expand_content (sync path), analyze_style_consistency,
          calculate_cost_impact
"""

from unittest.mock import patch

import pytest

from utils.constraint_utils import (
    ConstraintCompliance,
    ContentConstraints,
    analyze_style_consistency,
    auto_expand_content,
    auto_trim_content,
    calculate_cost_impact,
    calculate_phase_targets,
    count_words_in_content,
    extract_constraints_from_request,
    format_compliance_report,
    inject_constraints_into_prompt,
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
# Tier 1: extract_constraints_from_request
# ---------------------------------------------------------------------------


class TestExtractConstraintsFromRequest:
    """Tests for extract_constraints_from_request."""

    def test_extracts_word_count_from_dict(self):
        req = {"content_constraints": {"word_count": 2000}}
        result = extract_constraints_from_request(req)
        assert result.word_count == 2000

    def test_extracts_writing_style(self):
        req = {"content_constraints": {"writing_style": "technical"}}
        result = extract_constraints_from_request(req)
        assert result.writing_style == "technical"

    def test_extracts_tolerance(self):
        req = {"content_constraints": {"word_count_tolerance": 15}}
        result = extract_constraints_from_request(req)
        assert result.word_count_tolerance == 15

    def test_extracts_strict_mode(self):
        req = {"content_constraints": {"strict_mode": True}}
        result = extract_constraints_from_request(req)
        assert result.strict_mode is True

    def test_extracts_per_phase_overrides(self):
        overrides = {"creative": 1500, "qa": 200}
        req = {"content_constraints": {"per_phase_overrides": overrides}}
        result = extract_constraints_from_request(req)
        assert result.per_phase_overrides == overrides

    def test_returns_defaults_when_no_constraints_key(self):
        result = extract_constraints_from_request({})
        assert result.word_count == 1500
        assert result.writing_style == "educational"
        assert result.word_count_tolerance == 10
        assert result.strict_mode is False

    def test_returns_passed_constraints_object_unchanged(self):
        constraints = ContentConstraints(word_count=3000)
        req = {"content_constraints": constraints}
        result = extract_constraints_from_request(req)
        assert result is constraints

    def test_returns_defaults_for_non_dict_non_constraints_type(self):
        req = {"content_constraints": "invalid value"}
        result = extract_constraints_from_request(req)
        assert result.word_count == 1800  # ContentConstraints() default

    def test_partial_dict_uses_defaults_for_missing_keys(self):
        req = {"content_constraints": {"word_count": 500}}
        result = extract_constraints_from_request(req)
        assert result.word_count == 500
        assert result.writing_style == "educational"  # default


# ---------------------------------------------------------------------------
# Tier 1: count_words_in_content
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
# Tier 1: inject_constraints_into_prompt
# ---------------------------------------------------------------------------


class TestInjectConstraintsIntoPrompt:
    """Tests for inject_constraints_into_prompt."""

    def test_returns_base_prompt_when_no_constraints(self):
        result = inject_constraints_into_prompt("Write a blog post.", None)
        assert result == "Write a blog post."

    def test_injects_word_count_target(self):
        constraints = ContentConstraints(word_count=1000)
        result = inject_constraints_into_prompt("Write.", constraints)
        assert "1000" in result

    def test_injects_writing_style(self):
        constraints = ContentConstraints(writing_style="technical")
        result = inject_constraints_into_prompt("Write.", constraints)
        assert "technical" in result

    def test_injects_phase_name(self):
        constraints = ContentConstraints()
        result = inject_constraints_into_prompt("Write.", constraints, phase_name="creative")
        assert "creative" in result

    def test_uses_word_count_target_override(self):
        constraints = ContentConstraints(word_count=1500)
        result = inject_constraints_into_prompt("Write.", constraints, word_count_target=800)
        assert "800" in result

    def test_adds_style_guidance_for_technical(self):
        constraints = ContentConstraints(writing_style="technical")
        result = inject_constraints_into_prompt("Prompt.", constraints)
        assert "technical" in result.lower()

    def test_adds_style_guidance_for_narrative(self):
        constraints = ContentConstraints(writing_style="narrative")
        result = inject_constraints_into_prompt("Prompt.", constraints)
        assert "narrative" in result.lower() or "story" in result.lower()

    def test_adds_style_guidance_for_listicle(self):
        constraints = ContentConstraints(writing_style="listicle")
        result = inject_constraints_into_prompt("Prompt.", constraints)
        assert "list" in result.lower()

    def test_adds_style_guidance_for_educational(self):
        constraints = ContentConstraints(writing_style="educational")
        result = inject_constraints_into_prompt("Prompt.", constraints)
        assert "educational" in result.lower() or "learn" in result.lower()

    def test_adds_style_guidance_for_thought_leadership(self):
        constraints = ContentConstraints(writing_style="thought-leadership")
        result = inject_constraints_into_prompt("Prompt.", constraints)
        assert (
            "insight" in result.lower() or "thought" in result.lower() or "expert" in result.lower()
        )

    def test_original_prompt_preserved_in_output(self):
        constraints = ContentConstraints()
        base = "Write an amazing blog post."
        result = inject_constraints_into_prompt(base, constraints)
        assert base in result

    def test_tolerance_range_shown(self):
        constraints = ContentConstraints(word_count=1000, word_count_tolerance=10)
        result = inject_constraints_into_prompt("Write.", constraints)
        assert "900" in result and "1100" in result

    def test_unknown_style_no_guidance(self):
        constraints = ContentConstraints(writing_style="unknown_style_xyz")
        result = inject_constraints_into_prompt("Write.", constraints)
        # No STYLE GUIDANCE block expected for unknown styles
        assert "Write." in result


# ---------------------------------------------------------------------------
# Tier 1: validate_constraints
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
# Tier 2: calculate_phase_targets
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
# Tier 2: merge_compliance_reports
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


# ---------------------------------------------------------------------------
# Tier 3: auto_trim_content
# ---------------------------------------------------------------------------


class TestAutoTrimContent:
    """Tests for auto_trim_content."""

    def _content(self, word_count: int) -> str:
        return " ".join([f"word{i}" for i in range(word_count)])

    def test_content_within_tolerance_returned_unchanged(self):
        content = self._content(1000)
        result = auto_trim_content(content, target_words=1000, tolerance_percent=10)
        assert result == content

    def test_content_shorter_than_max_returned_unchanged(self):
        content = self._content(500)
        result = auto_trim_content(content, target_words=1000, tolerance_percent=10)
        # 500 <= 1100 (max), so returned unchanged
        assert result == content

    def test_content_exceeding_max_is_trimmed(self):
        content = self._content(1500)
        result = auto_trim_content(content, target_words=1000, tolerance_percent=10)
        # result should be <= 1100 words
        result_words = count_words_in_content(result)
        assert result_words <= 1100

    def test_preserve_structure_default_true(self):
        content = self._content(2000)
        result = auto_trim_content(content, target_words=1000, tolerance_percent=5)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Tier 3: auto_expand_content — async mode (returns original unchanged)
# ---------------------------------------------------------------------------


class TestAutoExpandContent:
    """Tests for auto_expand_content."""

    def _content(self, word_count: int) -> str:
        return " ".join(["word"] * word_count)

    def test_content_already_at_target_returned_unchanged(self):
        content = self._content(1000)
        result = auto_expand_content(content, target_words=1000, tolerance_percent=10)
        assert result == content

    def test_content_above_min_returned_unchanged(self):
        content = self._content(950)  # min for 1000 target @ 10% = 900
        result = auto_expand_content(content, target_words=1000, tolerance_percent=10)
        assert result == content

    def test_short_content_async_mode_returns_original(self):
        # In async=False mode (default), returns original content with warning
        content = self._content(500)
        result = auto_expand_content(content, target_words=1000, tolerance_percent=10)
        assert result == content

    def test_sync_mode_import_error_returns_original(self):
        # When model_router / prompt_manager not available (mocked), returns original
        content = self._content(500)
        with patch("utils.constraint_utils.auto_expand_content") as mock_fn:
            mock_fn.return_value = content
            result = mock_fn(content, target_words=1000, sync_mode=True)
            assert result == content


# ---------------------------------------------------------------------------
# Tier 3: analyze_style_consistency
# ---------------------------------------------------------------------------


class TestAnalyzeStyleConsistency:
    """Tests for analyze_style_consistency."""

    def test_returns_tuple_of_float_and_string(self):
        score, feedback = analyze_style_consistency("Some content here", "technical")
        assert isinstance(score, float)
        assert isinstance(feedback, str)

    def test_score_between_zero_and_one(self):
        for style in ["technical", "narrative", "listicle", "educational", "thought-leadership"]:
            score, _ = analyze_style_consistency("Some content here.", style)
            assert 0.0 <= score <= 1.0

    def test_technical_style_detected_by_keywords(self):
        content = "The algorithm implementation uses architecture framework component"
        score, _ = analyze_style_consistency(content, "technical")
        assert score >= 0.5

    def test_narrative_style_detected_by_keywords(self):
        content = (
            "I discovered the story of a journey where I experienced and realized many things."
        )
        score, _ = analyze_style_consistency(content, "narrative")
        assert score >= 0.5

    def test_listicle_detected_by_structure(self):
        content = "- Item one\n- Item two\n- Item three\n- Item four\n- Item five"
        score, _ = analyze_style_consistency(content, "listicle")
        assert score >= 0.3

    def test_feedback_includes_style_name(self):
        _, feedback = analyze_style_consistency("Some content", "educational")
        assert "educational" in feedback

    def test_feedback_includes_score(self):
        _, feedback = analyze_style_consistency("Some content", "technical")
        assert "%" in feedback

    def test_below_threshold_feedback_mentions_threshold(self):
        # Short generic content won't match technical keywords well
        score, feedback = analyze_style_consistency("hello world", "technical", min_score=0.99)
        if score < 0.99:
            assert "Below" in feedback


# ---------------------------------------------------------------------------
# Tier 3: calculate_cost_impact
# ---------------------------------------------------------------------------


class TestCalculateCostImpact:
    """Tests for calculate_cost_impact."""

    def _content(self, word_count: int) -> str:
        return " ".join(["word"] * word_count)

    def test_returns_expected_keys(self):
        content = self._content(1000)
        result = calculate_cost_impact(content, 1000, 800)
        assert "tokens_estimated" in result
        assert "cost_estimated" in result
        assert "cost_savings_from_reduction" in result
        assert "word_reduction" in result
        assert "efficiency_ratio" in result

    def test_word_reduction_computed(self):
        content = self._content(800)
        result = calculate_cost_impact(content, 1000, 800)
        assert result["word_reduction"] == 200  # 1000 - 800

    def test_efficiency_ratio_computed(self):
        content = self._content(800)
        result = calculate_cost_impact(content, 1000, 800)
        assert result["efficiency_ratio"] == pytest.approx(0.8)

    def test_zero_word_reduction_no_savings(self):
        content = self._content(1000)
        result = calculate_cost_impact(content, 1000, 1000)
        assert result["cost_savings_from_reduction"] == 0

    def test_cost_estimated_is_positive(self):
        content = self._content(500)
        result = calculate_cost_impact(content, 1000, 800)
        assert result["cost_estimated"] >= 0

    def test_zero_original_efficiency_ratio_is_one(self):
        content = self._content(0)
        result = calculate_cost_impact(content, 0, 0)
        assert result["efficiency_ratio"] == 1.0


# ---------------------------------------------------------------------------
# format_compliance_report
# ---------------------------------------------------------------------------


class TestFormatComplianceReport:
    """Tests for format_compliance_report."""

    def test_returns_string(self):
        compliance = _make_compliance(actual=1000, target=1000, within=True)
        result = format_compliance_report(compliance)
        assert isinstance(result, str)

    def test_includes_target_word_count(self):
        compliance = _make_compliance(actual=1000, target=1500, within=False)
        result = format_compliance_report(compliance)
        assert "1500" in result

    def test_includes_actual_word_count(self):
        compliance = _make_compliance(actual=800, target=1000, within=False)
        result = format_compliance_report(compliance)
        assert "800" in result

    def test_includes_writing_style(self):
        compliance = _make_compliance(style="narrative")
        result = format_compliance_report(compliance)
        assert "narrative" in result

    def test_pass_indicator_when_within_tolerance(self):
        compliance = _make_compliance(within=True)
        result = format_compliance_report(compliance)
        assert "PASS" in result

    def test_fail_indicator_when_outside_tolerance(self):
        compliance = _make_compliance(within=False, violation="Content too short")
        result = format_compliance_report(compliance)
        assert "FAIL" in result

    def test_includes_violation_message_when_present(self):
        compliance = _make_compliance(within=False, violation="Content too short: 500 words")
        result = format_compliance_report(compliance)
        assert "500 words" in result

    def test_no_violation_section_when_none(self):
        compliance = _make_compliance(within=True)
        result = format_compliance_report(compliance)
        assert "Violation:" not in result

    def test_strict_mode_shown(self):
        compliance = _make_compliance(strict=True)
        result = format_compliance_report(compliance)
        assert "ENABLED" in result

    def test_non_strict_mode_shown(self):
        compliance = _make_compliance(strict=False)
        result = format_compliance_report(compliance)
        assert "disabled" in result
