"""
Unit tests for services/quality_models.py

Tests data models, enums, and serialization for quality assessment.
"""

import pytest
from datetime import datetime, timezone

from services.quality_models import (
    EvaluationMethod,
    QualityScore,
    RefinementType,
    QualityDimensions,
    QualityAssessment,
)


# ---------------------------------------------------------------------------
# EvaluationMethod enum
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestEvaluationMethod:
    def test_values(self):
        assert EvaluationMethod.PATTERN_BASED.value == "pattern-based"
        assert EvaluationMethod.LLM_BASED.value == "llm-based"
        assert EvaluationMethod.HYBRID.value == "hybrid"

    def test_is_string(self):
        assert isinstance(EvaluationMethod.PATTERN_BASED, str)


# ---------------------------------------------------------------------------
# RefinementType enum
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRefinementType:
    def test_all_dimensions_present(self):
        expected = {"clarity", "accuracy", "completeness", "relevance", "seo", "readability", "engagement"}
        actual = {r.value for r in RefinementType}
        assert actual == expected


# ---------------------------------------------------------------------------
# QualityScore
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestQualityScore:
    def test_to_dict(self):
        score = QualityScore(
            overall_score=85.0,
            clarity=90.0, accuracy=80.0, completeness=85.0,
            relevance=88.0, seo_quality=82.0, readability=84.0,
            engagement=86.0, passing=True, feedback="Good",
            suggestions=["Minor fix"], evaluation_timestamp="2026-04-08T12:00:00Z",
        )
        d = score.to_dict()
        assert d["overall_score"] == 85.0
        assert d["passing"] is True
        assert d["suggestions"] == ["Minor fix"]
        assert "evaluation_timestamp" in d

    def test_default_evaluated_by(self):
        score = QualityScore(
            overall_score=70.0,
            clarity=70.0, accuracy=70.0, completeness=70.0,
            relevance=70.0, seo_quality=70.0, readability=70.0,
            engagement=70.0, passing=True, feedback="OK",
            suggestions=[], evaluation_timestamp="now",
        )
        assert score.evaluated_by == "QualityEvaluator"


# ---------------------------------------------------------------------------
# QualityDimensions
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestQualityDimensions:
    def test_average_all_equal(self):
        dims = QualityDimensions(
            clarity=80, accuracy=80, completeness=80,
            relevance=80, seo_quality=80, readability=80, engagement=80,
        )
        assert dims.average() == pytest.approx(80.0, abs=0.1)

    def test_average_mixed(self):
        dims = QualityDimensions(
            clarity=90, accuracy=70, completeness=80,
            relevance=60, seo_quality=75, readability=85, engagement=70,
        )
        expected = (90 + 70 + 80 + 60 + 75 + 85 + 70) / 7
        result = dims.average()
        # Result might be capped if relevance < CRITICAL_FLOOR
        assert result <= expected + 0.1

    def test_critical_floor_caps_score(self):
        dims = QualityDimensions(
            clarity=90, accuracy=90, completeness=90,
            relevance=30,  # Below CRITICAL_FLOOR (50)
            seo_quality=90, readability=90, engagement=90,
        )
        result = dims.average()
        # Should be capped at relevance value (30)
        assert result <= 30.0

    def test_readability_not_critical(self):
        """Readability was removed from critical dims (#1238)."""
        dims = QualityDimensions(
            clarity=90, accuracy=90, completeness=90,
            relevance=90, seo_quality=90,
            readability=20,  # Very low but NOT critical
            engagement=90,
        )
        result = dims.average()
        # Should NOT be capped at readability value
        assert result > 20.0

    def test_to_dict(self):
        dims = QualityDimensions(
            clarity=80, accuracy=70, completeness=85,
            relevance=75, seo_quality=82, readability=78, engagement=88,
        )
        d = dims.to_dict()
        assert len(d) == 7
        assert d["clarity"] == 80
        assert d["engagement"] == 88

    def test_critical_dimensions_tuple(self):
        dims = QualityDimensions(
            clarity=80, accuracy=80, completeness=80,
            relevance=80, seo_quality=80, readability=80, engagement=80,
        )
        assert "clarity" in dims.CRITICAL_DIMENSIONS
        assert "relevance" in dims.CRITICAL_DIMENSIONS
        assert "readability" not in dims.CRITICAL_DIMENSIONS


# ---------------------------------------------------------------------------
# QualityAssessment
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestQualityAssessment:
    def test_to_dict_includes_all_fields(self):
        dims = QualityDimensions(
            clarity=80, accuracy=80, completeness=80,
            relevance=80, seo_quality=80, readability=80, engagement=80,
        )
        assessment = QualityAssessment(
            dimensions=dims,
            overall_score=80.0,
            passing=True,
            feedback="Good quality",
            suggestions=["Minor improvements"],
            evaluation_method=EvaluationMethod.PATTERN_BASED,
        )
        d = assessment.to_dict()
        assert d["overall_score"] == 80.0
        assert d["passing"] is True
        assert d["evaluation_method"] == "pattern-based"
        assert d["clarity"] == 80  # Flattened from dimensions
        assert "evaluation_timestamp" in d

    def test_defaults(self):
        dims = QualityDimensions(
            clarity=70, accuracy=70, completeness=70,
            relevance=70, seo_quality=70, readability=70, engagement=70,
        )
        assessment = QualityAssessment(
            dimensions=dims, overall_score=70.0, passing=True,
            feedback="OK", suggestions=[],
            evaluation_method=EvaluationMethod.HYBRID,
        )
        assert assessment.evaluated_by == "UnifiedQualityService"
        assert assessment.refinement_attempts == 0
        assert assessment.max_refinements == 3
        assert assessment.truncation_detected is False
        assert assessment.content_length is None


# ---------------------------------------------------------------------------
# QualityDimensions.average() — site_config tunable floor
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCriticalFloorTunable:
    def test_site_config_overrides_default_floor(self):
        from unittest.mock import patch
        dims = QualityDimensions(
            clarity=90, accuracy=90, completeness=90,
            relevance=55,  # Above default 50, but below custom 60
            seo_quality=90, readability=90, engagement=90,
        )
        with patch("services.site_config.site_config") as mock_cfg:
            mock_cfg.get_float.return_value = 60.0
            result = dims.average()
        # relevance (55) < custom floor (60) → cap at 55
        assert result == 55.0

    def test_site_config_exception_falls_back_to_default(self):
        from unittest.mock import patch
        dims = QualityDimensions(
            clarity=90, accuracy=90, completeness=90,
            relevance=30,  # Below default floor 50
            seo_quality=90, readability=90, engagement=90,
        )
        with patch("services.site_config.site_config") as mock_cfg:
            mock_cfg.get_float.side_effect = RuntimeError("config down")
            result = dims.average()
        # Falls back to CRITICAL_FLOOR=50 → 30 < 50 → cap at 30
        assert result == 30.0

    def test_lowest_critical_dim_wins(self):
        """When BOTH clarity and relevance are below floor, the LOWER caps."""
        dims = QualityDimensions(
            clarity=40, accuracy=90, completeness=90,
            relevance=20,  # Lower than clarity
            seo_quality=90, readability=90, engagement=90,
        )
        result = dims.average()
        # Both critical dims below floor, the lower (20) is the cap
        # Implementation iterates in CRITICAL_DIMENSIONS order: clarity first, then relevance
        # min(raw, 40), then min(40, 20) → 20
        assert result == 20.0

    def test_critical_floor_at_exact_boundary_no_cap(self):
        """A critical dimension exactly at the floor should not trigger the cap."""
        dims = QualityDimensions(
            clarity=50, accuracy=90, completeness=90,
            relevance=90, seo_quality=90, readability=90, engagement=90,
        )
        result = dims.average()
        # 50 is NOT < 50 (strict less-than), so no cap
        expected_avg = (50 + 90 * 6) / 7
        assert result == pytest.approx(expected_avg, abs=0.1)

    def test_low_seo_does_not_trigger_cap(self):
        """SEO is not a critical dimension."""
        dims = QualityDimensions(
            clarity=90, accuracy=90, completeness=90,
            relevance=90,
            seo_quality=10,  # Very low but not critical
            readability=90, engagement=90,
        )
        result = dims.average()
        # Should be the raw average, not capped at 10
        expected = (90 * 6 + 10) / 7
        assert result == pytest.approx(expected, abs=0.1)


# ---------------------------------------------------------------------------
# QualityAssessment — optional fields
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQualityAssessmentOptionalFields:
    def _make_dims(self):
        return QualityDimensions(
            clarity=80, accuracy=80, completeness=80,
            relevance=80, seo_quality=80, readability=80, engagement=80,
        )

    def test_content_length_in_dict(self):
        assessment = QualityAssessment(
            dimensions=self._make_dims(),
            overall_score=80.0, passing=True,
            feedback="Good", suggestions=[],
            evaluation_method=EvaluationMethod.LLM_BASED,
            content_length=1500,
            word_count=250,
        )
        d = assessment.to_dict()
        assert d["content_length"] == 1500
        assert d["word_count"] == 250

    def test_flesch_kincaid_in_dict(self):
        assessment = QualityAssessment(
            dimensions=self._make_dims(),
            overall_score=80.0, passing=True,
            feedback="Good", suggestions=[],
            evaluation_method=EvaluationMethod.LLM_BASED,
            flesch_kincaid_grade_level=10.5,
        )
        d = assessment.to_dict()
        assert d["flesch_kincaid_grade_level"] == 10.5

    def test_truncation_detected_in_dict(self):
        assessment = QualityAssessment(
            dimensions=self._make_dims(),
            overall_score=80.0, passing=True,
            feedback="Good", suggestions=[],
            evaluation_method=EvaluationMethod.PATTERN_BASED,
            truncation_detected=True,
        )
        d = assessment.to_dict()
        assert d["truncation_detected"] is True

    def test_refinement_tracking_in_dict(self):
        assessment = QualityAssessment(
            dimensions=self._make_dims(),
            overall_score=80.0, passing=True,
            feedback="Good", suggestions=[],
            evaluation_method=EvaluationMethod.HYBRID,
            refinement_attempts=2,
            max_refinements=3,
            needs_refinement=True,
        )
        d = assessment.to_dict()
        assert d["refinement_attempts"] == 2
        assert d["needs_refinement"] is True

    def test_timestamp_serialized_as_isoformat(self):
        assessment = QualityAssessment(
            dimensions=self._make_dims(),
            overall_score=80.0, passing=True,
            feedback="Good", suggestions=[],
            evaluation_method=EvaluationMethod.PATTERN_BASED,
        )
        d = assessment.to_dict()
        # ISO format string should be parseable
        ts = datetime.fromisoformat(d["evaluation_timestamp"])
        assert ts.tzinfo is not None  # Should be timezone-aware

    def test_default_evaluation_timestamp_is_utc(self):
        assessment = QualityAssessment(
            dimensions=self._make_dims(),
            overall_score=80.0, passing=True,
            feedback="Good", suggestions=[],
            evaluation_method=EvaluationMethod.PATTERN_BASED,
        )
        assert assessment.evaluation_timestamp.tzinfo == timezone.utc

    def test_dimensions_flattened_into_dict(self):
        assessment = QualityAssessment(
            dimensions=self._make_dims(),
            overall_score=80.0, passing=True,
            feedback="Good", suggestions=[],
            evaluation_method=EvaluationMethod.PATTERN_BASED,
        )
        d = assessment.to_dict()
        # All 7 dimensions should be top-level keys
        for dim in ["clarity", "accuracy", "completeness", "relevance",
                    "seo_quality", "readability", "engagement"]:
            assert dim in d


# ---------------------------------------------------------------------------
# QualityScore — additional coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQualityScoreFields:
    def test_failing_score(self):
        score = QualityScore(
            overall_score=55.0,
            clarity=60, accuracy=50, completeness=55,
            relevance=50, seo_quality=60, readability=55, engagement=60,
            passing=False, feedback="Below threshold",
            suggestions=["Improve clarity", "Add citations"],
            evaluation_timestamp="2026-04-10T00:00:00Z",
        )
        assert score.passing is False
        assert score.overall_score == 55.0
        assert len(score.suggestions) == 2

    def test_custom_evaluated_by(self):
        score = QualityScore(
            overall_score=80.0, clarity=80, accuracy=80, completeness=80,
            relevance=80, seo_quality=80, readability=80, engagement=80,
            passing=True, feedback="Good", suggestions=[],
            evaluation_timestamp="t", evaluated_by="CustomEvaluator",
        )
        assert score.evaluated_by == "CustomEvaluator"

    def test_to_dict_round_trip_keys(self):
        score = QualityScore(
            overall_score=80.0, clarity=80, accuracy=80, completeness=80,
            relevance=80, seo_quality=80, readability=80, engagement=80,
            passing=True, feedback="Good", suggestions=[],
            evaluation_timestamp="t",
        )
        d = score.to_dict()
        expected_keys = {
            "overall_score", "clarity", "accuracy", "completeness",
            "relevance", "seo_quality", "readability", "engagement",
            "passing", "feedback", "suggestions",
            "evaluation_timestamp", "evaluated_by",
        }
        assert set(d.keys()) == expected_keys
