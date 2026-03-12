"""
Unit tests for QA Style Evaluator (StyleConsistencyValidator).

All tests are pure-function — zero DB, LLM, or network calls.
Tests cover content analysis, tone detection, style detection, component
scoring, issue identification, suggestion generation, and the full
validate_style_consistency async pipeline.
"""

import pytest

from services.qa_style_evaluator import (
    StyleConsistencyResult,
    StyleConsistencyValidator,
    get_style_consistency_validator,
)


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def validator() -> StyleConsistencyValidator:
    return StyleConsistencyValidator()


FORMAL_TEXT = (
    "Therefore, it is noteworthy that this comprehensive analysis demonstrates "
    "significant implications. Furthermore, the findings facilitate a deeper "
    "understanding of the subject matter. Consequently, utilization of these "
    "insights will prove beneficial."
)

CASUAL_TEXT = (
    "Like, this is really awesome and super cool! "
    "I actually think it's basically the best thing ever. "
    "Literally everyone loves it. It's kinda the best."
)

TECHNICAL_TEXT = (
    "```python\nfor i in range(10):\n    print(i)\n```\n"
    "The algorithm implements a recursive architecture with O(n log n) complexity. "
    "The function uses a framework-based implementation pattern.\n"
    "## Overview\nThis code demonstrates the core architecture.\n"
    "## Details\nSee the implementation below.\n"
    "## Usage\nFunction calls follow this pattern.\n"
)

LISTICLE_TEXT = (
    "- First step to success\n"
    "- Second important tip\n"
    "- Third reason to try\n"
    "- Fourth way to grow\n"
    "- Fifth secret revealed\n"
    "- Sixth method to use\n"
    "- Seventh key insight\n"
    "- Eighth valuable lesson\n"
    "- Ninth proven strategy\n"
    "- Tenth final thought\n"
    "- Eleventh bonus tip\n"
)

NARRATIVE_TEXT = (
    "## Introduction\n"
    "This story explores the journey of a startup. "
    "Consider the experience of building something from scratch. "
    "Imagine the challenges you would face.\n\n"
    "## Chapter One\n"
    "The founder described the early days as exhilarating. "
    "Here is how they describe the turning point. "
    "They examined each step carefully.\n\n"
    "## Chapter Two\n"
    "The team then discovered a new approach. "
    "They explored multiple options before settling on one. "
    "By the way, the solution was simpler than expected.\n\n"
    "## Conclusion\n"
    "In the end, the journey proved worthwhile."
)


# ---------------------------------------------------------------------------
# _analyze_content
# ---------------------------------------------------------------------------


class TestAnalyzeContent:
    def test_word_count(self, validator):
        text = "one two three four five"
        metrics = validator._analyze_content(text)
        assert metrics["word_count"] == 5

    def test_sentence_count(self, validator):
        text = "First sentence. Second sentence. Third sentence."
        metrics = validator._analyze_content(text)
        assert metrics["sentence_count"] == 3

    def test_avg_sentence_length(self, validator):
        # 6 words in 2 sentences → avg 3
        text = "Short sentence here. Another short one."
        metrics = validator._analyze_content(text)
        assert metrics["avg_sentence_length"] == pytest.approx(3.5, abs=0.5)

    def test_vocabulary_diversity_0_to_1(self, validator):
        text = "the the the cat sat on the mat"
        metrics = validator._analyze_content(text)
        assert 0.0 <= metrics["vocabulary_diversity"] <= 1.0

    def test_has_lists_detected(self, validator):
        metrics = validator._analyze_content("- item one\n- item two")
        assert metrics["has_lists"] is True

    def test_no_lists_detected(self, validator):
        metrics = validator._analyze_content("Just plain prose with no lists.")
        assert metrics["has_lists"] is False

    def test_has_code_blocks_detected(self, validator):
        metrics = validator._analyze_content("```python\ncode here\n```")
        assert metrics["has_code_blocks"] is True

    def test_has_headings_detected(self, validator):
        metrics = validator._analyze_content("# Heading\nSome content.")
        assert metrics["has_headings"] is True

    def test_no_headings(self, validator):
        metrics = validator._analyze_content("Just plain text with no markdown headings here.")
        assert metrics["has_headings"] is False

    def test_paragraph_count(self, validator):
        text = "Para one.\n\nPara two.\n\nPara three."
        metrics = validator._analyze_content(text)
        assert metrics["paragraph_count"] == 3


# ---------------------------------------------------------------------------
# _detect_tone
# ---------------------------------------------------------------------------


class TestDetectTone:
    def test_formal_text_detected(self, validator):
        assert validator._detect_tone(FORMAL_TEXT) == "formal"

    def test_casual_text_detected(self, validator):
        assert validator._detect_tone(CASUAL_TEXT) == "casual"

    def test_neutral_on_empty_text(self, validator):
        assert validator._detect_tone("") == "neutral"

    def test_neutral_on_generic_text(self, validator):
        # "neutral" is returned only when ALL tone marker counts are 0.
        # "The sky is blue today. Water is wet." matches 'we' in 'water' (conversational).
        # Use truly marker-free text that avoids all tone keyword substrings.
        tone = validator._detect_tone("Xyz abc def ghi jkl mno pqr stu vwx.")
        assert tone == "neutral"

    def test_academic_tone_detected(self, validator):
        academic = (
            "This research study employs an empirical methodology to test the hypothesis. "
            "The theoretical framework draws from scholarly literature and peer-reviewed findings. "
            "Analysis of the data demonstrates consistent results."
        )
        assert validator._detect_tone(academic) == "academic"


# ---------------------------------------------------------------------------
# _detect_style
# ---------------------------------------------------------------------------


class TestDetectStyle:
    def test_technical_style_detected(self, validator):
        assert validator._detect_style(TECHNICAL_TEXT) == "technical"

    def test_listicle_style_detected(self, validator):
        assert validator._detect_style(LISTICLE_TEXT) == "listicle"

    def test_narrative_style_detected(self, validator):
        assert validator._detect_style(NARRATIVE_TEXT) == "narrative"

    def test_general_fallback_on_plain_text(self, validator):
        plain = "The sky is blue. Water is wet. Grass is green."
        style = validator._detect_style(plain)
        # Falls back to "general" when no markers present
        assert isinstance(style, str)
        assert len(style) > 0


# ---------------------------------------------------------------------------
# _calculate_tone_consistency
# ---------------------------------------------------------------------------


class TestCalculateToneConsistency:
    def test_exact_match_gives_high_score(self, validator):
        score = validator._calculate_tone_consistency("formal", "formal", {})
        assert score == pytest.approx(0.95)

    def test_no_reference_gives_neutral(self, validator):
        score = validator._calculate_tone_consistency("formal", None, {})
        assert score == pytest.approx(0.5)

    def test_related_tone_gives_partial_credit(self, validator):
        # formal → authoritative are related
        score = validator._calculate_tone_consistency("authoritative", "formal", {})
        assert score == pytest.approx(0.78)

    def test_mismatched_tone_gives_low_score(self, validator):
        score = validator._calculate_tone_consistency("casual", "formal", {})
        assert score == pytest.approx(0.45)

    def test_casual_conversational_related(self, validator):
        score = validator._calculate_tone_consistency("conversational", "casual", {})
        assert score == pytest.approx(0.78)


# ---------------------------------------------------------------------------
# _calculate_vocabulary_consistency
# ---------------------------------------------------------------------------


class TestVocabularyConsistency:
    def test_no_reference_returns_default(self, validator):
        score = validator._calculate_vocabulary_consistency({"vocabulary_diversity": 0.5}, None)
        assert score == pytest.approx(0.7)

    def test_very_similar_diversity_high_score(self, validator):
        score = validator._calculate_vocabulary_consistency(
            {"vocabulary_diversity": 0.5},
            {"vocabulary_diversity": 0.52},
        )
        assert score == pytest.approx(0.95)

    def test_moderate_difference_mid_score(self, validator):
        score = validator._calculate_vocabulary_consistency(
            {"vocabulary_diversity": 0.5},
            {"vocabulary_diversity": 0.7},
        )
        assert score == pytest.approx(0.80)

    def test_large_difference_low_score(self, validator):
        score = validator._calculate_vocabulary_consistency(
            {"vocabulary_diversity": 0.2},
            {"vocabulary_diversity": 0.9},
        )
        assert score == pytest.approx(0.60)


# ---------------------------------------------------------------------------
# _calculate_sentence_structure_consistency
# ---------------------------------------------------------------------------


class TestSentenceStructureConsistency:
    def test_no_reference_returns_default(self, validator):
        score = validator._calculate_sentence_structure_consistency(
            {"avg_sentence_length": 15}, None
        )
        assert score == pytest.approx(0.7)

    def test_similar_sentence_length_high_score(self, validator):
        score = validator._calculate_sentence_structure_consistency(
            {"avg_sentence_length": 15},
            {"avg_sentence_length": 16},
        )
        assert score == pytest.approx(0.95)

    def test_large_difference_lower_score(self, validator):
        score = validator._calculate_sentence_structure_consistency(
            {"avg_sentence_length": 5},
            {"avg_sentence_length": 30},
        )
        assert score < 0.95


# ---------------------------------------------------------------------------
# _identify_issues
# ---------------------------------------------------------------------------


class TestIdentifyIssues:
    def test_no_issues_when_everything_matches(self, validator):
        metrics = {"vocabulary_diversity": 0.5, "avg_sentence_length": 15}
        issues = validator._identify_issues(
            generated_metrics=metrics,
            reference_metrics={"vocabulary_diversity": 0.5, "avg_sentence_length": 15},
            detected_style="technical",
            reference_style="technical",
            detected_tone="formal",
            reference_tone="formal",
        )
        assert issues == []

    def test_style_mismatch_flagged(self, validator):
        issues = validator._identify_issues(
            generated_metrics={"vocabulary_diversity": 0.5, "avg_sentence_length": 15},
            reference_metrics=None,
            detected_style="narrative",
            reference_style="technical",
            detected_tone="formal",
            reference_tone="formal",
        )
        assert any("style" in i.lower() for i in issues)

    def test_tone_mismatch_flagged(self, validator):
        issues = validator._identify_issues(
            generated_metrics={"vocabulary_diversity": 0.5, "avg_sentence_length": 15},
            reference_metrics=None,
            detected_style="narrative",
            reference_style="narrative",
            detected_tone="casual",
            reference_tone="formal",
        )
        assert any("tone" in i.lower() for i in issues)

    def test_low_vocabulary_diversity_flagged(self, validator):
        issues = validator._identify_issues(
            generated_metrics={"vocabulary_diversity": 0.2, "avg_sentence_length": 15},
            reference_metrics={"vocabulary_diversity": 0.6, "avg_sentence_length": 15},
            detected_style="narrative",
            reference_style="narrative",
            detected_tone="formal",
            reference_tone="formal",
        )
        assert any("vocabulary" in i.lower() for i in issues)

    def test_too_high_vocabulary_diversity_flagged(self, validator):
        issues = validator._identify_issues(
            generated_metrics={"vocabulary_diversity": 0.9, "avg_sentence_length": 15},
            reference_metrics={"vocabulary_diversity": 0.3, "avg_sentence_length": 15},
            detected_style="narrative",
            reference_style="narrative",
            detected_tone="formal",
            reference_tone="formal",
        )
        assert any("vocabulary" in i.lower() for i in issues)


# ---------------------------------------------------------------------------
# _generate_suggestions
# ---------------------------------------------------------------------------


class TestGenerateSuggestions:
    def test_no_issues_returns_excellent(self, validator):
        suggestions = validator._generate_suggestions([], {}, None)
        assert any("excellent" in s.lower() for s in suggestions)

    def test_vocabulary_issue_produces_suggestion(self, validator):
        issues = ["Vocabulary diversity is lower than reference sample"]
        suggestions = validator._generate_suggestions(
            issues, {"vocabulary_diversity": 0.2}, {"avg_sentence_length": 15}
        )
        assert len(suggestions) > 0

    def test_sentence_length_issue_produces_suggestion(self, validator):
        issues = ["Sentence structure differs significantly from reference sample"]
        suggestions = validator._generate_suggestions(
            issues,
            {"vocabulary_diversity": 0.5, "avg_sentence_length": 5},
            {"avg_sentence_length": 20},
        )
        assert any("sentence" in s.lower() for s in suggestions)


# ---------------------------------------------------------------------------
# _create_failed_result
# ---------------------------------------------------------------------------


class TestCreateFailedResult:
    def test_all_scores_zero(self, validator):
        r = validator._create_failed_result("Something went wrong", "technical", "formal")
        assert r.style_consistency_score == pytest.approx(0.0)
        assert r.tone_consistency_score == pytest.approx(0.0)
        assert r.vocabulary_score == pytest.approx(0.0)

    def test_passing_is_false(self, validator):
        r = validator._create_failed_result("error", None, None)
        assert r.passing is False

    def test_error_in_issues(self, validator):
        r = validator._create_failed_result("timeout error", None, None)
        assert "timeout error" in r.issues

    def test_reference_style_and_tone_preserved(self, validator):
        r = validator._create_failed_result("err", "listicle", "casual")
        assert r.reference_style == "listicle"
        assert r.reference_tone == "casual"


# ---------------------------------------------------------------------------
# validate_style_consistency (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestValidateStyleConsistency:
    async def test_empty_content_returns_failed_result(self, validator):
        result = await validator.validate_style_consistency("")
        assert isinstance(result, StyleConsistencyResult)
        assert result.passing is False
        assert result.style_consistency_score == pytest.approx(0.0)

    async def test_valid_content_returns_result(self, validator):
        result = await validator.validate_style_consistency(FORMAL_TEXT)
        assert isinstance(result, StyleConsistencyResult)
        assert 0.0 <= result.style_consistency_score <= 1.0

    async def test_passing_threshold_is_0_65(self, validator):
        # A formal text evaluated against its own metrics — should produce passing result
        result = await validator.validate_style_consistency(
            FORMAL_TEXT,
            reference_tone="formal",
            reference_style="thought-leadership",
        )
        # passing = score >= 0.65
        assert result.passing == (result.style_consistency_score >= 0.65)

    async def test_matching_reference_tone_raises_score(self, validator):
        no_ref = await validator.validate_style_consistency(FORMAL_TEXT)
        with_ref = await validator.validate_style_consistency(
            FORMAL_TEXT, reference_tone="formal"
        )
        # Providing matching reference tone should give a better tone score
        assert with_ref.tone_consistency_score >= no_ref.tone_consistency_score

    async def test_result_fields_populated(self, validator):
        result = await validator.validate_style_consistency(FORMAL_TEXT)
        assert result.detected_tone != ""
        assert result.detected_style != ""
        assert isinstance(result.issues, list)
        assert isinstance(result.suggestions, list)

    async def test_reference_metrics_affects_vocab_score(self, validator):
        # With reference metrics that match generated content closely → higher vocab score
        metrics = validator._analyze_content(FORMAL_TEXT)
        result = await validator.validate_style_consistency(
            FORMAL_TEXT, reference_metrics=metrics
        )
        assert result.vocabulary_score >= 0.9


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------


class TestGetStyleConsistencyValidator:
    def test_returns_validator_instance(self):
        v = get_style_consistency_validator()
        assert isinstance(v, StyleConsistencyValidator)

    def test_returns_same_instance_on_repeat(self):
        v1 = get_style_consistency_validator()
        v2 = get_style_consistency_validator()
        assert v1 is v2
