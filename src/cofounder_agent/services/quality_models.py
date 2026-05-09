"""
Quality data models for the Unified Quality Assessment Service.

Contains all dataclasses, enums, and type definitions used by quality_service.py
and its callers.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from services.logger_config import get_logger
from services.site_config import SiteConfig

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Defaults to a fresh env-fallback instance until the lifespan setter
# fires. Tests can either patch this attribute directly or call
# ``set_site_config()`` for explicit wiring.
site_config: SiteConfig = SiteConfig()


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global site_config
    site_config = sc


logger = get_logger(__name__)


class EvaluationMethod(str, Enum):
    """Supported evaluation methods"""

    PATTERN_BASED = "pattern-based"  # Fast, deterministic
    LLM_BASED = "llm-based"  # Accurate, uses language model
    HYBRID = "hybrid"  # Combines both


@dataclass
class QualityScore:
    """Detailed quality evaluation result (backward compatibility with QualityEvaluator)"""

    overall_score: float  # 0-100 (average of 7 criteria)
    clarity: float  # 0-100
    accuracy: float  # 0-100
    completeness: float  # 0-100
    relevance: float  # 0-100
    seo_quality: float  # 0-100
    readability: float  # 0-100
    engagement: float  # 0-100

    # Feedback
    passing: bool  # True if overall_score >= 70.0 (70/100 = passing for 0-100 scale)
    feedback: str  # Human-readable feedback
    suggestions: list[str]  # Improvement suggestions

    # Metadata
    evaluation_timestamp: str
    evaluated_by: str = "QualityEvaluator"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database storage"""
        return {
            "overall_score": self.overall_score,
            "clarity": self.clarity,
            "accuracy": self.accuracy,
            "completeness": self.completeness,
            "relevance": self.relevance,
            "seo_quality": self.seo_quality,
            "readability": self.readability,
            "engagement": self.engagement,
            "passing": self.passing,
            "feedback": self.feedback,
            "suggestions": self.suggestions,
            "evaluation_timestamp": self.evaluation_timestamp,
            "evaluated_by": self.evaluated_by,
        }


class RefinementType(str, Enum):
    """Types of refinements that can be applied"""

    CLARITY = "clarity"
    ACCURACY = "accuracy"
    COMPLETENESS = "completeness"
    RELEVANCE = "relevance"
    SEO = "seo"
    READABILITY = "readability"
    ENGAGEMENT = "engagement"


@dataclass
class QualityDimensions:
    """7-criteria quality assessment"""

    clarity: float  # 0-100
    accuracy: float  # 0-100
    completeness: float  # 0-100
    relevance: float  # 0-100
    seo_quality: float  # 0-100
    readability: float  # 0-100
    engagement: float  # 0-100

    # Critical dimensions that, if severely low, cap the overall score.
    # Any critical dimension below CRITICAL_FLOOR causes overall score to be
    # capped at that dimension's value, preventing high scores in other
    # dimensions from masking critical weaknesses (issue #127).
    # NOTE: readability removed from critical dims (#1238) — Flesch formula
    # penalizes technical vocabulary, causing valid technical content to score
    # 10-30 and cap the entire score. Readability still contributes to the
    # weighted average but no longer triggers the hard cap.
    CRITICAL_FLOOR: float = 50.0
    CRITICAL_DIMENSIONS: tuple = ("clarity", "relevance")

    def average(self) -> float:
        """Calculate overall score with minimum-dimension enforcement.

        Returns the arithmetic mean of all 7 dimensions, but caps the result
        at the lowest critical dimension score if that score is below
        CRITICAL_FLOOR. This prevents content with critically weak clarity
        or relevance from receiving a passing overall score solely because
        other dimensions scored well.

        Note: readability is excluded from critical caps (#1238) because the
        Flesch formula penalizes technical vocabulary unfairly.

        Examples:
            clarity=80, relevance=48 → overall capped at 48 → FAIL
            clarity=80, relevance=70 → normal average → may PASS
        """
        raw_average = (
            self.clarity
            + self.accuracy
            + self.completeness
            + self.relevance
            + self.seo_quality
            + self.readability
            + self.engagement
        ) / 7.0

        # Enforce minimum-dimension constraint on critical dimensions only.
        # readability excluded (#1238) — Flesch penalizes technical vocabulary.
        # CRITICAL_FLOOR is tunable via app_settings (qa_critical_floor).
        try:
            effective_floor = site_config.get_float(
                "qa_critical_floor", self.CRITICAL_FLOOR,
            )
        except Exception:
            effective_floor = self.CRITICAL_FLOOR
        critical_values = {dim: getattr(self, dim) for dim in self.CRITICAL_DIMENSIONS}
        for dim_name, dim_value in critical_values.items():
            if dim_value < effective_floor:
                logger.debug(
                    f"Quality cap applied: {dim_name}={dim_value:.1f} < "
                    f"CRITICAL_FLOOR={self.CRITICAL_FLOOR} — "
                    f"overall capped from {raw_average:.1f} to {dim_value:.1f}"
                )
                raw_average = min(raw_average, dim_value)

        return raw_average

    def to_dict(self) -> dict[str, float]:
        """Convert to dictionary"""
        return {
            "clarity": self.clarity,
            "accuracy": self.accuracy,
            "completeness": self.completeness,
            "relevance": self.relevance,
            "seo_quality": self.seo_quality,
            "readability": self.readability,
            "engagement": self.engagement,
        }


@dataclass
class QualityAssessment:
    """Complete quality assessment result"""

    # Dimensions
    dimensions: QualityDimensions

    # Overall score
    overall_score: float  # Average of dimensions (0-100)
    passing: bool  # True if >= 70

    # Feedback
    feedback: str  # Summary feedback
    suggestions: list[str]  # Improvement suggestions

    # Evaluation details
    evaluation_method: EvaluationMethod
    evaluation_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    evaluated_by: str = "UnifiedQualityService"

    # Content metadata
    content_length: int | None = None
    word_count: int | None = None

    # Flesch-Kincaid Grade Level (complementary readability metric)
    flesch_kincaid_grade_level: float | None = None

    # Truncation detection
    truncation_detected: bool = False

    # Refinement tracking
    refinement_attempts: int = 0
    max_refinements: int = 3
    needs_refinement: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database storage"""
        return {
            **self.dimensions.to_dict(),
            "overall_score": self.overall_score,
            "passing": self.passing,
            "feedback": self.feedback,
            "suggestions": self.suggestions,
            "evaluation_method": self.evaluation_method.value,
            "evaluation_timestamp": self.evaluation_timestamp.isoformat(),
            "evaluated_by": self.evaluated_by,
            "content_length": self.content_length,
            "word_count": self.word_count,
            "flesch_kincaid_grade_level": self.flesch_kincaid_grade_level,
            "truncation_detected": self.truncation_detected,
            "refinement_attempts": self.refinement_attempts,
            "needs_refinement": self.needs_refinement,
        }
