"""
Unit tests for quality_service.py — QualityDimensions scoring logic.

Regression tests for issue #127: unweighted average masked critical dimension
failures (e.g. readability=48 still resulted in overall=71.79/PASS).
"""

import pytest

from services.quality_service import QualityDimensions


class TestQualityDimensionsAverage:
    """Tests for QualityDimensions.average() with minimum-dimension enforcement."""

    def test_basic_average_all_high(self):
        """All high scores produce a high average."""
        dims = QualityDimensions(
            clarity=90,
            accuracy=85,
            completeness=80,
            relevance=88,
            seo_quality=82,
            readability=87,
            engagement=84,
        )
        result = dims.average()
        expected = (90 + 85 + 80 + 88 + 82 + 87 + 84) / 7.0
        assert abs(result - expected) < 0.01

    def test_critical_readability_below_floor_caps_score(self):
        """Regression test for issue #127: clarity=80, readability=48 must FAIL.

        Without the fix, average was ~71.79 (passing). With the fix, score is
        capped at 48 (readability), which is below the 70-point pass threshold.
        """
        dims = QualityDimensions(
            clarity=80,
            accuracy=75,
            completeness=70,
            relevance=78,
            seo_quality=72,
            readability=48,  # Critical failure
            engagement=73,
        )
        result = dims.average()
        # Score must be capped at readability value (48), not raw average (~71.7)
        assert result <= 48.0, f"Expected capped score <= 48, got {result}"
        # Confirm it would FAIL the 70-point threshold
        assert result < 70, f"Content with readability=48 must not pass (score={result})"

    def test_critical_clarity_below_floor_caps_score(self):
        """If clarity is critically low, overall score is capped."""
        dims = QualityDimensions(
            clarity=45,  # Critical failure
            accuracy=80,
            completeness=85,
            relevance=82,
            seo_quality=75,
            readability=78,
            engagement=80,
        )
        result = dims.average()
        assert result <= 45.0, f"Expected capped score <= 45, got {result}"
        assert result < 70

    def test_critical_relevance_below_floor_caps_score(self):
        """If relevance is critically low, overall score is capped."""
        dims = QualityDimensions(
            clarity=82,
            accuracy=78,
            completeness=75,
            relevance=40,  # Critical failure
            seo_quality=80,
            readability=77,
            engagement=79,
        )
        result = dims.average()
        assert result <= 40.0, f"Expected capped score <= 40, got {result}"
        assert result < 70

    def test_non_critical_dimension_below_floor_does_not_cap(self):
        """Low seo_quality or engagement below 50 should NOT cap the score."""
        dims = QualityDimensions(
            clarity=80,
            accuracy=75,
            completeness=72,
            relevance=78,
            seo_quality=40,  # Non-critical — should not cap
            readability=76,
            engagement=45,  # Non-critical — should not cap
        )
        raw = (80 + 75 + 72 + 78 + 40 + 76 + 45) / 7.0
        result = dims.average()
        # Score should equal raw average (not capped), since no critical dim < 50
        assert abs(result - raw) < 0.01, (
            f"Non-critical low dims should not cap score. "
            f"Expected {raw:.2f}, got {result:.2f}"
        )

    def test_critical_dimension_exactly_at_floor_not_capped(self):
        """A critical dimension at exactly 50 should NOT trigger the cap."""
        dims = QualityDimensions(
            clarity=80,
            accuracy=75,
            completeness=70,
            relevance=78,
            seo_quality=72,
            readability=50,  # Exactly at floor — no cap
            engagement=73,
        )
        raw = (80 + 75 + 70 + 78 + 72 + 50 + 73) / 7.0
        result = dims.average()
        assert abs(result - raw) < 0.01

    def test_multiple_critical_dims_below_floor_caps_at_lowest(self):
        """Multiple critical dims below floor: score capped at the lowest."""
        dims = QualityDimensions(
            clarity=45,  # Below floor
            accuracy=80,
            completeness=75,
            relevance=48,  # Below floor — lower than clarity
            seo_quality=72,
            readability=49,  # Below floor
            engagement=73,
        )
        result = dims.average()
        # Should be capped at min of (45, 48, 49) = 45
        assert result <= 45.0, f"Expected capped at 45, got {result}"

    def test_passing_score_above_70(self):
        """Good content with all dimensions well above floor passes normally."""
        dims = QualityDimensions(
            clarity=75,
            accuracy=72,
            completeness=70,
            relevance=74,
            seo_quality=68,
            readability=73,
            engagement=71,
        )
        result = dims.average()
        assert result >= 70, f"Good content should pass: {result}"

    def test_perfect_score(self):
        """100/100 on all dimensions gives 100.0."""
        dims = QualityDimensions(
            clarity=100,
            accuracy=100,
            completeness=100,
            relevance=100,
            seo_quality=100,
            readability=100,
            engagement=100,
        )
        assert dims.average() == 100.0

    def test_zero_readability_gives_zero_overall(self):
        """Zero readability (degenerate case) caps overall at 0."""
        dims = QualityDimensions(
            clarity=90,
            accuracy=90,
            completeness=90,
            relevance=90,
            seo_quality=90,
            readability=0,
            engagement=90,
        )
        result = dims.average()
        assert result == 0.0, f"Zero readability must give zero overall, got {result}"
