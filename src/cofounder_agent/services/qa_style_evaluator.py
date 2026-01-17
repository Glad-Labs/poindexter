"""
QA Style Evaluator - Phase 3.5

Enhances QA system with style consistency verification.

Handles:
1. Style consistency checking (generated content vs. reference sample)
2. Tone consistency verification across content
3. Style-specific scoring metrics
4. Writing characteristic verification
5. Integration with quality evaluation pipeline

This service extends the existing QA system to ensure generated content
matches the user's selected writing style and tone preferences.
"""

import logging
import re
from typing import Optional, Dict, Any, List, Tuple
from collections import Counter
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class StyleConsistencyResult:
    """Result of style consistency evaluation"""

    # Overall style consistency (0-1)
    style_consistency_score: float

    # Component scores
    tone_consistency_score: float  # How well tone is maintained
    vocabulary_score: float  # Vocabulary complexity match
    sentence_structure_score: float  # Sentence length/structure match
    formatting_score: float  # Formatting element match

    # Style-specific metrics
    style_type: str  # technical, narrative, listicle, etc.
    detected_tone: str  # formal, casual, authoritative, conversational
    detected_style: str  # Style detected in generated content

    # Detailed metrics
    avg_sentence_length: float
    avg_word_length: float
    vocabulary_diversity: float

    # Pass/fail determination
    passing: bool  # True if consistency >= 0.75
    issues: List[str]  # List of identified issues
    suggestions: List[str]  # Improvement suggestions

    # Metadata
    reference_style: Optional[str] = None
    reference_tone: Optional[str] = None
    reference_metrics: Optional[Dict[str, Any]] = None


class StyleConsistencyValidator:
    """Validates that generated content matches reference writing style"""

    def __init__(self):
        """Initialize style consistency validator"""
        self.tone_markers = {
            "formal": [
                "therefore",
                "moreover",
                "furthermore",
                "consequently",
                "however",
                "noteworthy",
                "significant",
                "comprehensive",
                "utilize",
                "facilitate",
                "in accordance",
                "hereinafter",
                "pursuant",
            ],
            "casual": [
                "like",
                "really",
                "pretty",
                "super",
                "awesome",
                "cool",
                "actually",
                "basically",
                "literally",
                "totally",
                "gonna",
                "wanna",
                "kinda",
                "sorta",
            ],
            "authoritative": [
                "research shows",
                "studies demonstrate",
                "evidence suggests",
                "proven",
                "based on",
                "according to",
                "documented",
                "established",
                "confirmed",
                "validated",
                "verified",
                "analysis indicates",
            ],
            "conversational": [
                "you",
                "we",
                "let us",
                "consider",
                "imagine",
                "think about",
                "here is",
                "by the way",
                "for instance",
                "in my opinion",
                "let me tell you",
            ],
        }

        self.style_markers = {
            "technical": [
                "algorithm",
                "implementation",
                "framework",
                "architecture",
                "code",
                "function",
            ],
            "narrative": ["story", "journey", "experience", "character", "plot", "describe"],
            "listicle": ["steps", "reasons", "tips", "ways", "secrets", "rules"],
            "educational": ["learn", "understand", "explain", "concept", "principle", "theory"],
            "thought-leadership": [
                "insight",
                "perspective",
                "analysis",
                "opinion",
                "vision",
                "strategy",
            ],
        }

    async def validate_style_consistency(
        self,
        generated_content: str,
        reference_metrics: Optional[Dict[str, Any]] = None,
        reference_style: Optional[str] = None,
        reference_tone: Optional[str] = None,
    ) -> StyleConsistencyResult:
        """
        Validate that generated content matches reference writing style.

        Args:
            generated_content: The generated content to validate
            reference_metrics: Metrics from reference sample (from Phase 3.3)
            reference_style: Expected style (technical, narrative, etc.)
            reference_tone: Expected tone (formal, casual, etc.)

        Returns:
            StyleConsistencyResult with detailed evaluation
        """
        try:
            if not generated_content:
                logger.warning("Empty generated content for style validation")
                return self._create_failed_result(
                    "Generated content is empty", reference_style, reference_tone
                )

            # Analyze generated content
            generated_metrics = self._analyze_content(generated_content)

            # Detect style and tone
            detected_style = self._detect_style(generated_content)
            detected_tone = self._detect_tone(generated_content)

            # Calculate component scores
            tone_score = self._calculate_tone_consistency(
                detected_tone, reference_tone, generated_metrics
            )

            vocab_score = self._calculate_vocabulary_consistency(
                generated_metrics, reference_metrics
            )

            sentence_score = self._calculate_sentence_structure_consistency(
                generated_metrics, reference_metrics
            )

            format_score = self._calculate_formatting_consistency(
                generated_content, reference_metrics
            )

            # Calculate overall consistency
            overall_score = (
                tone_score * 0.35 + vocab_score * 0.25 + sentence_score * 0.25 + format_score * 0.15
            )

            # Identify issues and suggestions
            issues = self._identify_issues(
                generated_metrics,
                reference_metrics,
                detected_style,
                reference_style,
                detected_tone,
                reference_tone,
            )

            suggestions = self._generate_suggestions(issues, generated_metrics, reference_metrics)

            # Create result
            result = StyleConsistencyResult(
                style_consistency_score=overall_score,
                tone_consistency_score=tone_score,
                vocabulary_score=vocab_score,
                sentence_structure_score=sentence_score,
                formatting_score=format_score,
                style_type=reference_style or detected_style,
                detected_tone=detected_tone,
                detected_style=detected_style,
                avg_sentence_length=generated_metrics["avg_sentence_length"],
                avg_word_length=generated_metrics["avg_word_length"],
                vocabulary_diversity=generated_metrics["vocabulary_diversity"],
                passing=overall_score >= 0.75,
                issues=issues,
                suggestions=suggestions,
                reference_style=reference_style,
                reference_tone=reference_tone,
                reference_metrics=reference_metrics,
            )

            logger.info(
                f"Style consistency evaluation: {overall_score:.2f}/1.0 "
                f"({'PASS' if result.passing else 'FAIL'}) - "
                f"Style: {detected_style}, Tone: {detected_tone}"
            )

            return result

        except Exception as e:
            logger.error(f"Error validating style consistency: {e}")
            return self._create_failed_result(
                f"Validation error: {str(e)}", reference_style, reference_tone
            )

    def _analyze_content(self, content: str) -> Dict[str, Any]:
        """Analyze writing metrics of content"""
        words = content.split()
        sentences = re.split(r"[.!?]+", content)
        sentences = [s.strip() for s in sentences if s.strip()]
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]

        word_count = len(words)
        sentence_count = len(sentences)
        paragraph_count = len(paragraphs)

        avg_word_length = sum(len(w) for w in words) / word_count if words else 0
        avg_sentence_length = word_count / sentence_count if sentence_count > 0 else 0
        avg_paragraph_length = word_count / paragraph_count if paragraph_count > 0 else 0

        unique_words = len(set(w.lower() for w in words))
        vocabulary_diversity = unique_words / word_count if word_count > 0 else 0

        # Formatting elements
        has_lists = "- " in content or "* " in content or "1." in content
        has_code_blocks = "```" in content or "`" in content
        has_headings = content.count("#") > 0
        has_quotes = '"' in content or "'" in content

        return {
            "word_count": word_count,
            "sentence_count": sentence_count,
            "paragraph_count": paragraph_count,
            "avg_word_length": avg_word_length,
            "avg_sentence_length": avg_sentence_length,
            "avg_paragraph_length": avg_paragraph_length,
            "vocabulary_diversity": vocabulary_diversity,
            "unique_words": unique_words,
            "has_lists": has_lists,
            "has_code_blocks": has_code_blocks,
            "has_headings": has_headings,
            "has_quotes": has_quotes,
        }

    def _detect_tone(self, content: str) -> str:
        """Detect primary tone of content"""
        text_lower = content.lower()
        tone_scores = {}

        for tone, markers in self.tone_markers.items():
            count = sum(1 for marker in markers if marker in text_lower)
            tone_scores[tone] = count

        return max(tone_scores, key=tone_scores.get) if max(tone_scores.values()) > 0 else "neutral"

    def _detect_style(self, content: str) -> str:
        """Detect primary style of content"""
        text_lower = content.lower()

        # Style-specific checks
        has_lists = "- " in content or "* " in content
        has_code = "```" in content
        has_headings = "#" in content

        style_scores = {}

        for style, markers in self.style_markers.items():
            count = sum(1 for marker in markers if marker in text_lower)
            style_scores[style] = count

        # Boost scores based on formatting
        if has_code:
            style_scores["technical"] += 5
        if has_lists:
            style_scores["listicle"] += 3
        if has_headings:
            style_scores["educational"] += 2

        return (
            max(style_scores, key=style_scores.get) if any(style_scores.values()) > 0 else "general"
        )

    def _calculate_tone_consistency(
        self, detected_tone: str, reference_tone: Optional[str], metrics: Dict[str, Any]
    ) -> float:
        """Calculate tone consistency score (0-1)"""
        if not reference_tone:
            return 0.5  # Neutral if no reference

        # Direct match gives high score
        if detected_tone == reference_tone:
            return 0.95

        # Check tone markers in content
        text_lower = ""  # Would need to pass content here

        # Related tones get partial credit
        related_tones = {
            "formal": ["authoritative"],
            "casual": ["conversational"],
            "authoritative": ["formal"],
            "conversational": ["casual"],
        }

        if reference_tone in related_tones and detected_tone in related_tones[reference_tone]:
            return 0.75

        return 0.4  # Mismatched tone

    def _calculate_vocabulary_consistency(
        self, generated_metrics: Dict[str, Any], reference_metrics: Optional[Dict[str, Any]]
    ) -> float:
        """Calculate vocabulary consistency score (0-1)"""
        if not reference_metrics:
            return 0.7  # Default if no reference

        ref_diversity = reference_metrics.get("vocabulary_diversity", 0.5)
        gen_diversity = generated_metrics.get("vocabulary_diversity", 0.5)

        # Calculate difference
        diff = abs(ref_diversity - gen_diversity)

        # Allow 30% difference
        if diff < 0.15:
            return 0.95
        elif diff < 0.30:
            return 0.80
        else:
            return 0.60

    def _calculate_sentence_structure_consistency(
        self, generated_metrics: Dict[str, Any], reference_metrics: Optional[Dict[str, Any]]
    ) -> float:
        """Calculate sentence structure consistency score (0-1)"""
        if not reference_metrics:
            return 0.7

        ref_sent_len = reference_metrics.get("avg_sentence_length", 15)
        gen_sent_len = generated_metrics.get("avg_sentence_length", 15)

        # Calculate difference
        diff = abs(ref_sent_len - gen_sent_len)

        # Allow 20% difference
        threshold = max(ref_sent_len * 0.2, 3)

        if diff < threshold:
            return 0.95
        elif diff < threshold * 1.5:
            return 0.80
        else:
            return 0.60

    def _calculate_formatting_consistency(
        self, content: str, reference_metrics: Optional[Dict[str, Any]]
    ) -> float:
        """Calculate formatting consistency score (0-1)"""
        if not reference_metrics:
            return 0.7

        metrics = self._analyze_content(content)
        ref_format = reference_metrics.get("style_characteristics", {})
        gen_format = {
            "has_lists": metrics["has_lists"],
            "has_code_blocks": metrics["has_code_blocks"],
            "has_headings": metrics["has_headings"],
            "has_quotes": metrics["has_quotes"],
        }

        # Count matching formatting elements
        matches = sum(1 for key in gen_format if gen_format[key] == ref_format.get(key, False))

        return matches / 4  # 0-1 based on 4 formatting attributes

    def _identify_issues(
        self,
        generated_metrics: Dict[str, Any],
        reference_metrics: Optional[Dict[str, Any]],
        detected_style: str,
        reference_style: Optional[str],
        detected_tone: str,
        reference_tone: Optional[str],
    ) -> List[str]:
        """Identify style consistency issues"""
        issues = []

        # Style mismatch
        if reference_style and detected_style != reference_style:
            issues.append(
                f"Detected style '{detected_style}' doesn't match reference style '{reference_style}'"
            )

        # Tone mismatch
        if reference_tone and detected_tone != reference_tone:
            issues.append(
                f"Detected tone '{detected_tone}' doesn't match reference tone '{reference_tone}'"
            )

        # Vocabulary issues
        if reference_metrics:
            ref_diversity = reference_metrics.get("vocabulary_diversity", 0.5)
            gen_diversity = generated_metrics.get("vocabulary_diversity", 0.5)

            if gen_diversity < ref_diversity * 0.7:
                issues.append("Vocabulary diversity is lower than reference sample")
            elif gen_diversity > ref_diversity * 1.3:
                issues.append("Vocabulary is too diverse compared to reference sample")

        # Sentence structure issues
        if reference_metrics:
            ref_sent = reference_metrics.get("avg_sentence_length", 15)
            gen_sent = generated_metrics.get("avg_sentence_length", 15)

            if abs(ref_sent - gen_sent) > max(ref_sent * 0.3, 5):
                issues.append("Sentence structure differs significantly from reference sample")

        return issues

    def _generate_suggestions(
        self,
        issues: List[str],
        generated_metrics: Dict[str, Any],
        reference_metrics: Optional[Dict[str, Any]],
    ) -> List[str]:
        """Generate improvement suggestions"""
        suggestions = []

        if not issues:
            suggestions.append("Excellent style consistency!")
            return suggestions

        if "vocabulary diversity" in str(issues).lower():
            if generated_metrics.get("vocabulary_diversity", 0) < 0.4:
                suggestions.append("Add more varied vocabulary and avoid repetition")
            else:
                suggestions.append("Simplify vocabulary to match reference sample")

        if "sentence structure" in str(issues).lower():
            gen_sent = generated_metrics.get("avg_sentence_length", 15)
            ref_sent = reference_metrics.get("avg_sentence_length", 15) if reference_metrics else 15

            if gen_sent < ref_sent:
                suggestions.append("Use longer, more complex sentences")
            else:
                suggestions.append("Break up long sentences into shorter ones")

        if "tone" in str(issues).lower():
            suggestions.append("Adjust formality level and language choice to match reference tone")

        if "style" in str(issues).lower():
            suggestions.append(
                "Restructure content to match reference style (format, organization)"
            )

        suggestions.append("Review reference sample for specific examples to emulate")

        return suggestions

    def _create_failed_result(
        self, error_message: str, reference_style: Optional[str], reference_tone: Optional[str]
    ) -> StyleConsistencyResult:
        """Create a failed validation result"""
        return StyleConsistencyResult(
            style_consistency_score=0.0,
            tone_consistency_score=0.0,
            vocabulary_score=0.0,
            sentence_structure_score=0.0,
            formatting_score=0.0,
            style_type=reference_style or "unknown",
            detected_tone=reference_tone or "unknown",
            detected_style="unknown",
            avg_sentence_length=0.0,
            avg_word_length=0.0,
            vocabulary_diversity=0.0,
            passing=False,
            issues=[error_message],
            suggestions=["Unable to evaluate style consistency"],
            reference_style=reference_style,
            reference_tone=reference_tone,
        )


# Singleton instance
_validator_instance: Optional[StyleConsistencyValidator] = None


def get_style_consistency_validator() -> StyleConsistencyValidator:
    """Get or create style consistency validator instance"""
    global _validator_instance
    if _validator_instance is None:
        _validator_instance = StyleConsistencyValidator()
    return _validator_instance
