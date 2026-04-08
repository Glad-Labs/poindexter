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
