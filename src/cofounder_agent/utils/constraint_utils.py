"""
Content Constraint Utilities - Word Count & Writing Style Management

Tier 1: Basic enforcement - word count and writing style injection, output validation
Tier 2: Compliance tracking - tolerance levels, per-phase targets, strict mode
Tier 3: Advanced features - auto-correction, style consistency, cost optimization

This module provides utilities for enforcing content constraints throughout the
generation pipeline, from prompt injection to output validation.
"""

import logging
import re
from typing import Dict, Any, Optional, Tuple, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================


@dataclass
class ContentConstraints:
    """Represents content generation constraints"""

    word_count: int = 1500  # Target word count (300-5000)
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
# TIER 1: BASIC CONSTRAINT UTILITIES
# ============================================================================


def extract_constraints_from_request(request_data: Dict[str, Any]) -> ContentConstraints:
    """
    Extract content constraints from a task request.

    Looks for content_constraints field in the request and converts to ContentConstraints
    object. Provides sensible defaults if not present.

    Args:
        request_data: Full request dictionary from TaskCreateRequest

    Returns:
        ContentConstraints object with extracted values or defaults

    Tier: 1 (Basic)
    """
    constraints_data = request_data.get("content_constraints", {})

    if isinstance(constraints_data, dict):
        return ContentConstraints(
            word_count=constraints_data.get("word_count", 1500),
            writing_style=constraints_data.get("writing_style", "educational"),
            word_count_tolerance=constraints_data.get("word_count_tolerance", 10),
            per_phase_overrides=constraints_data.get("per_phase_overrides"),
            strict_mode=constraints_data.get("strict_mode", False),
        )
    elif isinstance(constraints_data, ContentConstraints):
        return constraints_data
    else:
        # Return defaults if no constraints provided
        return ContentConstraints()


def count_words_in_content(content: str) -> int:
    """
    Count words in content string.

    Splits on whitespace and filters out empty strings, providing accurate word count.
    Handles markdown, HTML, and plain text equally.

    Args:
        content: Text content to count

    Returns:
        Number of words in content

    Tier: 1 (Basic)
    """
    if not content:
        return 0

    # Split on whitespace and filter empty strings
    words = [w for w in content.split() if w.strip()]
    return len(words)


def inject_constraints_into_prompt(
    base_prompt: str,
    constraints: Optional[ContentConstraints],
    phase_name: str = "general",
    word_count_target: Optional[int] = None,
) -> str:
    """
    Inject content constraints into a generation prompt.

    Adds instructions to the prompt about target word count and writing style.
    Called before passing prompt to LLM for generation.

    Args:
        base_prompt: The original generation prompt
        constraints: ContentConstraints with target values
        phase_name: Name of current phase (research, creative, etc.)
        word_count_target: Override word count target (if different from default)

    Returns:
        Modified prompt with constraint instructions injected

    Tier: 1 (Basic)
    """
    if not constraints:
        return base_prompt

    target_words = word_count_target or constraints.word_count
    tolerance = constraints.word_count_tolerance

    # Build constraint instruction
    constraint_instruction = f"""
[CONTENT CONSTRAINTS]
- Target word count: {target_words} words (±{tolerance}% tolerance = {int(target_words * tolerance / 100)} words)
- Acceptable range: {int(target_words * (1 - tolerance / 100))} to {int(target_words * (1 + tolerance / 100))} words
- Writing style: {constraints.writing_style}
- Phase: {phase_name}

Please generate content that meets these constraints. The word count is important - aim for the target or within tolerance.
"""

    # Add style-specific guidance
    style_guidance = _get_style_guidance(constraints.writing_style)
    if style_guidance:
        constraint_instruction += f"\nSTYLE GUIDANCE:\n{style_guidance}\n"

    # Return original prompt + constraints at the beginning
    return constraint_instruction + "\n" + base_prompt


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

    Tier: 1 (Basic)
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
# TIER 2: COMPLIANCE TRACKING & PHASE MANAGEMENT
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
    
    This allows QA phase to expand content slightly for quality improvements
    while keeping creative as the primary content generation phase.
    
    Per-phase overrides can customize this allocation if needed.
    Used in Tier 2 for detailed compliance tracking.

    Args:
        total_word_count: Total target word count for entire content
        constraints: ContentConstraints with per_phase_overrides if specified
        num_phases: Number of phases (research, creative, etc.)

    Returns:
        Dict mapping phase names to target word counts

    Tier: 2 (Control)
    """
    phase_names = ["research", "creative", "qa", "format", "finalize"][:num_phases]

    # If overrides specified, use them
    if constraints.per_phase_overrides:
        targets = {
            phase: constraints.per_phase_overrides.get(phase, _get_default_phase_target(phase, total_word_count))
            for phase in phase_names
        }
    else:
        # Default allocation by phase role
        targets = {phase: _get_default_phase_target(phase, total_word_count) for phase in phase_names}

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
        return 0{phase: _get_default_phase_target(phase, total_word_count) for phase in phase_names}

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


def check_tolerance(
    actual_value: int, target_value: int, tolerance_percent: int
) -> Tuple[bool, float]:
    """
    Check if actual value is within tolerance of target.

    Tier 2 helper for compliance checking.

    Args:
        actual_value: Actual measured value
        target_value: Target value
        tolerance_percent: Tolerance percentage (e.g., 10 for ±10%)

    Returns:
        Tuple of (is_within_tolerance, percentage_difference)

    Tier: 2 (Control)
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

    Tier: 2 (Control)
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

    Used in Tier 2 to aggregate phase-level compliance into task-level report.

    Args:
        reports: List of ConstraintCompliance reports from each phase

    Returns:
        Merged ConstraintCompliance with aggregated metrics

    Tier: 2 (Control)
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


# ============================================================================
# TIER 3: ADVANCED FEATURES (Auto-correction, Style Consistency, Optimization)
# ============================================================================


def auto_trim_content(
    content: str, target_words: int, tolerance_percent: int = 10, preserve_structure: bool = True
) -> str:
    """
    Automatically trim content to fit word count constraints.

    Removes least important content to meet word count target.
    Tier 3 advanced feature.

    Args:
        content: Content to trim
        target_words: Target word count
        tolerance_percent: Tolerance percentage
        preserve_structure: Keep paragraph breaks if True

    Returns:
        Trimmed content

    Tier: 3 (Advanced)
    """
    current_words = count_words_in_content(content)
    tolerance_words = int(target_words * tolerance_percent / 100)
    max_words = target_words + tolerance_words

    if current_words <= max_words:
        return content  # Already within tolerance

    # Simple approach: truncate to max words, then find sentence boundary
    words = content.split()
    trimmed_words = words[:max_words]
    trimmed_content = " ".join(trimmed_words)

    # Try to end at sentence boundary
    last_period = trimmed_content.rfind(".")
    if last_period > len(trimmed_content) * 0.8:  # If period is in last 20% of trimmed content
        trimmed_content = trimmed_content[: last_period + 1]

    logger.info(
        f"Auto-trimmed content from {current_words} to {count_words_in_content(trimmed_content)} words"
    )
    return trimmed_content


def auto_expand_content(
    content: str,
    target_words: int,
    tolerance_percent: int = 10,
    expansion_prompt_template: Optional[str] = None,
) -> str:
    """
    Expand content to meet word count target.

    NOTE: This is a placeholder. Real implementation would call LLM to expand.
    Tier 3 advanced feature.

    Args:
        content: Content to expand
        target_words: Target word count
        tolerance_percent: Tolerance percentage
        expansion_prompt_template: Template for expansion prompt

    Returns:
        Expanded content

    Tier: 3 (Advanced)
    """
    current_words = count_words_in_content(content)
    tolerance_words = int(target_words * tolerance_percent / 100)
    min_words = target_words - tolerance_words

    if current_words >= min_words:
        return content  # Already within tolerance

    # Placeholder: In real implementation, would call LLM with expansion_prompt_template
    logger.warning(f"Content expansion needed: {current_words} words -> {target_words} words")
    logger.warning("NOTE: Actual expansion requires LLM call - implement with model_router")

    return content


def analyze_style_consistency(
    content: str, target_style: str, min_score: float = 0.7
) -> Tuple[float, str]:
    """
    Analyze if content matches the target writing style.

    NOTE: This is a placeholder. Real implementation would use LLM or NLP.
    Tier 3 advanced feature.

    Args:
        content: Content to analyze
        target_style: Target writing style
        min_score: Minimum score for acceptance (0-1)

    Returns:
        Tuple of (style_score, feedback)

    Tier: 3 (Advanced)
    """
    # Placeholder scoring based on simple heuristics
    score = 0.8  # Default placeholder score

    if target_style == "technical":
        # Look for technical language
        technical_words = ["algorithm", "implementation", "architecture", "framework", "component"]
        technical_count = sum(1 for word in technical_words if word in content.lower())
        score = min(1.0, 0.5 + (technical_count * 0.1))

    elif target_style == "narrative":
        # Look for storytelling elements
        story_words = ["story", "journey", "experience", "discovered", "realized", "learned"]
        story_count = sum(1 for word in story_words if word in content.lower())
        score = min(1.0, 0.5 + (story_count * 0.1))

    elif target_style == "listicle":
        # Look for list structure
        list_count = content.count("-") + content.count("•") + content.count("\n")
        score = min(1.0, 0.3 + (list_count / len(content) * 10) if content else 0.3)

    # Add more style checks as needed

    feedback = f"Style '{target_style}' consistency score: {score:.1%}"
    if score < min_score:
        feedback += " - Below acceptable threshold"

    return score, feedback


def calculate_cost_impact(
    content: str,
    original_word_count: int,
    constraint_word_count: int,
    cost_per_1k_tokens: float = 0.01,
) -> Dict[str, Any]:
    """
    Calculate cost impact of applying constraints.

    Estimates token usage and API cost for constraint adjustments.
    Tier 3 advanced feature.

    Args:
        content: Final content
        original_word_count: Original unconstrained word count
        constraint_word_count: Target constraint word count
        cost_per_1k_tokens: Cost per 1000 tokens

    Returns:
        Dict with cost metrics

    Tier: 3 (Advanced)
    """
    # Rough estimate: 1 word ≈ 1.3 tokens
    tokens_estimate = count_words_in_content(content) * 1.3
    cost = (tokens_estimate / 1000) * cost_per_1k_tokens

    word_reduction = original_word_count - constraint_word_count
    cost_savings = (word_reduction / 1000) * cost_per_1k_tokens if word_reduction > 0 else 0

    return {
        "tokens_estimated": int(tokens_estimate),
        "cost_estimated": round(cost, 4),
        "cost_savings_from_reduction": round(cost_savings, 4),
        "word_reduction": word_reduction,
        "efficiency_ratio": (
            round(constraint_word_count / original_word_count, 2)
            if original_word_count > 0
            else 1.0
        ),
    }


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _get_style_guidance(writing_style: str) -> str:
    """Get writing style guidance prompt."""
    guidance_map = {
        "technical": """
- Use precise, technical terminology
- Include concepts, implementation details, architecture
- Assume reader has technical background
- Use active voice and direct statements
- Include specific examples and code concepts where relevant""",
        "narrative": """
- Tell a compelling story with beginning, middle, end
- Include personal experiences or real-world examples
- Use vivid descriptions and sensory details
- Build emotional connection with reader
- Use varied sentence structure for flow""",
        "listicle": """
- Organize content as numbered or bulleted list
- Each list item should be self-contained
- Use clear, scannable formatting
- Add brief intro and conclusion
- Make each point punchy and memorable""",
        "educational": """
- Explain concepts clearly for learners
- Build from basic to advanced
- Include examples and analogies
- Summarize key takeaways
- Use question-answer format where helpful""",
        "thought-leadership": """
- Present original insights and perspective
- Back up claims with data and research
- Include calls to action
- Position as expert in field
- Address industry challenges and solutions""",
    }

    return guidance_map.get(writing_style, "")


def format_compliance_report(compliance: ConstraintCompliance) -> str:
    """
    Format a constraint compliance report as readable text.

    Args:
        compliance: ConstraintCompliance object to format

    Returns:
        Formatted text report
    """
    status = "✅ PASS" if compliance.word_count_within_tolerance else "❌ FAIL"

    report = f"""
{status} CONSTRAINT COMPLIANCE REPORT
{'=' * 50}
Word Count:
  - Target: {compliance.word_count_target} words
  - Actual: {compliance.word_count_actual} words
  - Difference: {compliance.word_count_percentage:+.1f}%
  - Within Tolerance: {compliance.word_count_within_tolerance}

Writing Style: {compliance.writing_style_applied}
Strict Mode: {'ENABLED' if compliance.strict_mode_enforced else 'disabled'}
"""

    if compliance.violation_message:
        report += f"\nViolation: {compliance.violation_message}\n"

    return report
