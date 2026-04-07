"""
Content Constraint Utilities - Word Count & Writing Style Management

Provides utilities for enforcing content constraints throughout the
generation pipeline, including word count validation, tolerance checking,
phase target allocation, and strict mode enforcement.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from services.logger_config import get_logger

logger = get_logger(__name__)
# ============================================================================
# DATA STRUCTURES
# ============================================================================


@dataclass
class ContentConstraints:
    """Represents content generation constraints"""

    word_count: int = 1800  # Target word count (300-5000)
    writing_style: str = (
        "educational"  # Style: technical, narrative, listicle, educational, thought-leadership
    )
    word_count_tolerance: int = 10  # Tolerance percentage (5-20%)
    per_phase_overrides: Optional[Dict[str, int]] = None  # Override targets for specific phases
    strict_mode: bool = False  # If True, fail task if constraints violated


@dataclass
class ConstraintCompliance:
    """Metrics about constraint compliance"""

    word_count_actual: int
    word_count_target: int
    word_count_within_tolerance: bool
    word_count_percentage: float  # (actual - target) / target * 100
    writing_style_applied: str
    strict_mode_enforced: bool
    violation_message: Optional[str] = None


@dataclass
class PhaseWordCountTarget:
    """Target word count for a specific phase"""

    phase_name: str
    target_words: int
    actual_words: int = 0
    completed: bool = False


# ============================================================================
# WORD COUNT & VALIDATION
# ============================================================================


def count_words_in_content(content: str) -> int:
    """
    Count words in content string.

    Splits on whitespace and filters out empty strings, providing accurate word count.
    Handles markdown, HTML, and plain text equally.

    Args:
        content: Text content to count

    Returns:
        Number of words in content
    """
    if not content:
        return 0

    # Split on whitespace and filter empty strings
    words = [w for w in content.split() if w.strip()]
    return len(words)


def validate_constraints(
    content: str,
    constraints: ContentConstraints,
    phase_name: str = "general",
    word_count_target: Optional[int] = None,
) -> ConstraintCompliance:
    """
    Validate that generated content meets the constraints.

    Checks word count against target and tolerance, verifies style is appropriate.
    Returns detailed compliance report.

    Args:
        content: Generated content to validate
        constraints: ContentConstraints to validate against
        phase_name: Name of phase that generated this content
        word_count_target: Override word count target

    Returns:
        ConstraintCompliance object with detailed metrics
    """
    actual_words = count_words_in_content(content)
    target_words = word_count_target or constraints.word_count
    tolerance_range = int(target_words * constraints.word_count_tolerance / 100)
    min_words = target_words - tolerance_range
    max_words = target_words + tolerance_range

    within_tolerance = min_words <= actual_words <= max_words
    percentage_diff = (
        ((actual_words - target_words) / target_words * 100) if target_words > 0 else 0
    )

    violation_message = None
    if not within_tolerance:
        if actual_words < min_words:
            violation_message = f"Content too short: {actual_words} words (target: {target_words} ±{constraints.word_count_tolerance}%)"
        else:
            violation_message = f"Content too long: {actual_words} words (target: {target_words} ±{constraints.word_count_tolerance}%)"

    return ConstraintCompliance(
        word_count_actual=actual_words,
        word_count_target=target_words,
        word_count_within_tolerance=within_tolerance,
        word_count_percentage=percentage_diff,
        writing_style_applied=constraints.writing_style,
        strict_mode_enforced=constraints.strict_mode,
        violation_message=violation_message,
    )


# ============================================================================
# PHASE TARGET ALLOCATION
# ============================================================================


def calculate_phase_targets(
    total_word_count: int, constraints: ContentConstraints, num_phases: int = 5
) -> Dict[str, int]:
    """
    Calculate target word counts for each phase in the pipeline.

    Allocates word count to phases based on their role:
    - research: 0 (data gathering, not output)
    - creative: 100% (main blog content generation)
    - qa: 15% (refinement buffer - can expand/improve beyond initial draft)
    - format: 0 (formatting, not word count change)
    - finalize: 0 (final touches, not word count change)

    Args:
        total_word_count: Total target word count for entire content
        constraints: ContentConstraints with per_phase_overrides if specified
        num_phases: Number of phases (research, creative, etc.)

    Returns:
        Dict mapping phase names to target word counts
    """
    phase_names = ["research", "creative", "qa", "format", "finalize"][:num_phases]

    # If overrides specified, use them
    if constraints.per_phase_overrides:
        targets = {
            phase: constraints.per_phase_overrides.get(
                phase, _get_default_phase_target(phase, total_word_count)
            )
            for phase in phase_names
        }
    else:
        # Default allocation by phase role
        targets = {
            phase: _get_default_phase_target(phase, total_word_count) for phase in phase_names
        }

    return targets


def _get_default_phase_target(phase: str, total_word_count: int) -> int:
    """
    Get default word count target for a specific phase.

    - creative: 100% (main generation)
    - qa: 15% (refinement/expansion buffer)
    - others: 0% (supporting phases)
    """
    if phase == "creative":
        return total_word_count
    elif phase == "qa":
        # QA can expand by up to 15% for quality improvements
        return int(total_word_count * 0.15)
    else:
        # research, format, finalize don't produce measurable output
        return 0


# ============================================================================
# TOLERANCE & STRICT MODE
# ============================================================================


def check_tolerance(
    actual_value: int, target_value: int, tolerance_percent: int
) -> Tuple[bool, float]:
    """
    Check if actual value is within tolerance of target.

    Args:
        actual_value: Actual measured value
        target_value: Target value
        tolerance_percent: Tolerance percentage (e.g., 10 for ±10%)

    Returns:
        Tuple of (is_within_tolerance, percentage_difference)
    """
    if target_value == 0:
        return False, 0.0

    tolerance_range = int(target_value * tolerance_percent / 100)
    is_within = target_value - tolerance_range <= actual_value <= target_value + tolerance_range
    percentage = ((actual_value - target_value) / target_value * 100) if target_value > 0 else 0

    return is_within, percentage


def apply_strict_mode(compliance: ConstraintCompliance) -> Tuple[bool, str]:
    """
    Apply strict mode validation.

    In strict mode, any constraint violation causes task to fail.
    In non-strict mode, violations are warnings.

    Args:
        compliance: ConstraintCompliance report to check

    Returns:
        Tuple of (is_valid_in_strict_mode, error_message_if_invalid)
    """
    if not compliance.strict_mode_enforced:
        # Non-strict mode: always valid
        return True, ""

    # Strict mode: must meet all constraints
    if not compliance.word_count_within_tolerance:
        return (
            False,
            compliance.violation_message or "Word count constraint violated in strict mode",
        )

    return True, ""


def merge_compliance_reports(reports: List[ConstraintCompliance]) -> ConstraintCompliance:
    """
    Merge multiple constraint compliance reports into one summary.

    Used to aggregate phase-level compliance into task-level report.

    Args:
        reports: List of ConstraintCompliance reports from each phase

    Returns:
        Merged ConstraintCompliance with aggregated metrics
    """
    if not reports:
        return ConstraintCompliance(
            word_count_actual=0,
            word_count_target=0,
            word_count_within_tolerance=False,
            word_count_percentage=0.0,
            writing_style_applied="unknown",
            strict_mode_enforced=False,
        )

    total_actual = sum(r.word_count_actual for r in reports)
    total_target = sum(r.word_count_target for r in reports)

    # Check if all phases are within tolerance
    all_within_tolerance = all(r.word_count_within_tolerance for r in reports)

    # Aggregate word count percentage
    if total_target > 0:
        aggregate_percentage = (total_actual - total_target) / total_target * 100
    else:
        aggregate_percentage = 0.0

    # Find first violation message if any
    violation_messages = [r.violation_message for r in reports if r.violation_message]

    return ConstraintCompliance(
        word_count_actual=total_actual,
        word_count_target=total_target,
        word_count_within_tolerance=all_within_tolerance,
        word_count_percentage=aggregate_percentage,
        writing_style_applied=reports[0].writing_style_applied if reports else "unknown",
        strict_mode_enforced=reports[0].strict_mode_enforced if reports else False,
        violation_message=violation_messages[0] if violation_messages else None,
    )
