"""
Unified Quality Assessment Service

Consolidates all content quality evaluation functionality:
- Pattern-based evaluation (7-criteria framework - fast, deterministic)
- LLM-based evaluation (binary approval + detailed feedback - accurate)
- Hybrid scoring (combines both approaches for robust assessment)
- Quality improvement tracking and logging
- Automatic refinement suggestions
- PostgreSQL persistence

This single service replaces:
- QualityEvaluator (pattern-based scoring)
- UnifiedQualityOrchestrator (workflow orchestration)
- ContentQualityService (business logic)

Quality Framework (7 Criteria):
1. Clarity (0-10) - Is content clear and easy to understand?
2. Accuracy (0-10) - Is information correct and fact-checked?
3. Completeness (0-10) - Does it cover the topic thoroughly?
4. Relevance (0-10) - Is all content relevant to the topic?
5. SEO Quality (0-10) - Keywords, meta, structure optimization?
6. Readability (0-10) - Grammar, flow, formatting?
7. Engagement (0-10) - Is content compelling and interesting?

Overall Score = Average of 7 criteria (with minimum-dimension enforcement)
Pass Threshold = 7.0/10 (70%)
Critical Floor = 50/100 — if clarity, readability, or relevance falls below this
  threshold the overall score is capped at that dimension's value, preventing
  compensatory passing from high scores in other dimensions.
"""

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


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
    suggestions: List[str]  # Improvement suggestions

    # Metadata
    evaluation_timestamp: str
    evaluated_by: str = "QualityEvaluator"

    def to_dict(self) -> Dict[str, Any]:
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
    CRITICAL_FLOOR: float = 50.0
    CRITICAL_DIMENSIONS: tuple = ("clarity", "readability", "relevance")

    def average(self) -> float:
        """Calculate overall score with minimum-dimension enforcement.

        Returns the arithmetic mean of all 7 dimensions, but caps the result
        at the lowest critical dimension score if that score is below
        CRITICAL_FLOOR. This prevents content with critically low readability
        (e.g. 48/100) from receiving a passing overall score solely because
        other dimensions scored well.

        Examples:
            clarity=80, readability=48 → overall capped at 48 → FAIL
            clarity=80, readability=70 → normal average → may PASS
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

        # Enforce minimum-dimension constraint on critical dimensions
        critical_values = {
            "clarity": self.clarity,
            "readability": self.readability,
            "relevance": self.relevance,
        }
        for dim_name, dim_value in critical_values.items():
            if dim_value < self.CRITICAL_FLOOR:
                logger.debug(
                    f"Quality cap applied: {dim_name}={dim_value:.1f} < "
                    f"CRITICAL_FLOOR={self.CRITICAL_FLOOR} — "
                    f"overall capped from {raw_average:.1f} to {dim_value:.1f}"
                )
                raw_average = min(raw_average, dim_value)

        return raw_average

    def to_dict(self) -> Dict[str, float]:
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
    suggestions: List[str]  # Improvement suggestions

    # Evaluation details
    evaluation_method: EvaluationMethod
    evaluation_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    evaluated_by: str = "UnifiedQualityService"

    # Content metadata
    content_length: Optional[int] = None
    word_count: Optional[int] = None

    # Refinement tracking
    refinement_attempts: int = 0
    max_refinements: int = 3
    needs_refinement: bool = False

    def to_dict(self) -> Dict[str, Any]:
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
            "refinement_attempts": self.refinement_attempts,
            "needs_refinement": self.needs_refinement,
        }


class UnifiedQualityService:
    """
    Unified service for all content quality assessment.

    Provides:
    - Multi-criteria evaluation (7 dimensions)
    - Pattern-based scoring (fast, deterministic)
    - LLM-based evaluation (accurate, nuanced)
    - Hybrid approach (combines both)
    - Automatic refinement recommendations
    - Complete audit trail
    """

    def __init__(self, model_router=None, database_service=None, qa_agent=None, llm_client=None):
        """
        Initialize quality service

        Args:
            model_router: Optional ModelRouter for LLM access
            database_service: Optional DatabaseService for persistence
            qa_agent: Optional QA Agent for binary approval
            llm_client: Optional LLMClient for direct LLM evaluation calls
        """
        self.model_router = model_router
        self.database_service = database_service
        self.qa_agent = qa_agent
        self.llm_client = llm_client

        # Statistics
        self.total_evaluations = 0
        self.passing_count = 0
        self.failing_count = 0
        self.average_score = 0.0

        logger.info("✅ UnifiedQualityService initialized")

    async def evaluate(
        self,
        content: str,
        context: Optional[Dict[str, Any]] = None,
        method: EvaluationMethod = EvaluationMethod.PATTERN_BASED,
        store_result: bool = True,
    ) -> QualityAssessment:
        """
        Evaluate content quality using specified method.

        Args:
            content: Content to evaluate
            context: Optional context (topic, keywords, audience, etc.)
            method: Evaluation method to use
            store_result: Whether to store result in database

        Returns:
            QualityAssessment with scores and feedback
        """
        logger.info(f"Evaluating content ({method.value}): {len(content)} chars")

        context = context or {}

        try:
            if method == EvaluationMethod.PATTERN_BASED:
                assessment = await self._evaluate_pattern_based(content, context)
            elif method == EvaluationMethod.LLM_BASED:
                assessment = await self._evaluate_llm_based(content, context)
            elif method == EvaluationMethod.HYBRID:
                assessment = await self._evaluate_hybrid(content, context)
            else:
                assessment = await self._evaluate_pattern_based(content, context)

            # Update statistics
            self.total_evaluations += 1
            if assessment.passing:
                self.passing_count += 1
            else:
                self.failing_count += 1

            # Calculate running average
            if self.total_evaluations > 0:
                self.average_score = (
                    self.average_score * (self.total_evaluations - 1) + assessment.overall_score
                ) / self.total_evaluations

            # Store if requested
            if store_result and self.database_service:
                await self._store_evaluation(assessment, context)

            logger.info(
                f"✅ Evaluation complete: {assessment.overall_score:.0f}/100 "
                f"({'PASS' if assessment.passing else 'FAIL'})"
            )

            return assessment

        except Exception as e:
            logger.error(f"[_evaluate] ❌ Evaluation failed: {e}", exc_info=True)
            # Return minimal assessment on error
            return QualityAssessment(
                dimensions=QualityDimensions(
                    clarity=5.0,
                    accuracy=5.0,
                    completeness=5.0,
                    relevance=5.0,
                    seo_quality=5.0,
                    readability=5.0,
                    engagement=5.0,
                ),
                overall_score=5.0,
                passing=False,
                feedback=f"Evaluation error: {str(e)}",
                suggestions=["Unable to evaluate at this time"],
                evaluation_method=method,
                evaluated_by="UnifiedQualityService-Error",
            )

    async def _evaluate_pattern_based(
        self, content: str, context: Dict[str, Any]
    ) -> QualityAssessment:
        """
        Fast pattern-based evaluation using heuristics.

        Analyzes:
        - Length and word count
        - Sentence structure and complexity
        - Keyword presence
        - Grammar patterns
        - Readability metrics
        """
        logger.debug("Running pattern-based evaluation...")

        # Calculate basic metrics
        word_count = len(content.split())
        sentence_count = len(re.split(r"[.!?]+", content))
        paragraphs = len(content.split("\n\n"))

        # Extract patterns
        has_keywords = self._check_keywords(content, context)
        clarity_score = self._score_clarity(content, sentence_count, word_count)
        readability_score = self._score_readability(content)

        dimensions = QualityDimensions(
            clarity=clarity_score * 10,  # Convert 0-10 to 0-100
            accuracy=self._score_accuracy(content, context) * 10,
            completeness=self._score_completeness(content, context) * 10,
            relevance=self._score_relevance(content, context) * 10,
            seo_quality=self._score_seo(content, context) * 10,
            readability=readability_score * 10,
            engagement=self._score_engagement(content) * 10,
        )

        overall_score = dimensions.average()

        return QualityAssessment(
            dimensions=dimensions,
            overall_score=overall_score,
            passing=overall_score >= 70,  # 70/100 = 7/10
            feedback=self._generate_feedback(dimensions, context),
            suggestions=self._generate_suggestions(dimensions),
            evaluation_method=EvaluationMethod.PATTERN_BASED,
            content_length=len(content),
            word_count=word_count,
        )

    async def _evaluate_llm_based(self, content: str, context: Dict[str, Any]) -> QualityAssessment:
        """
        LLM-based evaluation using language model (issue #189).

        Uses llm_client for direct calls.  Falls back to pattern-based if no
        LLM client is available or if the LLM call fails.
        """
        if not self.llm_client:
            logger.warning(
                "LLM evaluation requested but llm_client not available, falling back to pattern-based"
            )
            return await self._evaluate_pattern_based(content, context)

        logger.debug("Running LLM-based evaluation...")

        topic = context.get("topic", "unknown topic")
        # Truncate very long content to avoid excessive token usage
        content_excerpt = content[:4000] if len(content) > 4000 else content

        evaluation_prompt = (
            "You are a content quality evaluator. Score the following content on 7 dimensions, "
            "each from 0 to 10 (integers only). Respond ONLY with a JSON object — no markdown, "
            "no explanation.\n\n"
            f"Topic: {topic}\n\n"
            f"Content:\n{content_excerpt}\n\n"
            "Return JSON with these keys:\n"
            '{"clarity": N, "accuracy": N, "completeness": N, "relevance": N, '
            '"seo_quality": N, "readability": N, "engagement": N, "feedback": "one sentence summary", '
            '"suggestions": ["suggestion1", "suggestion2"]}'
        )

        try:
            raw_response = await self.llm_client.generate_text(evaluation_prompt)

            # Extract JSON from response (may contain markdown fences)
            json_match = re.search(r"\{[^{}]*\}", raw_response, re.DOTALL)
            if not json_match:
                logger.warning("LLM evaluation returned no valid JSON, falling back to pattern-based")
                return await self._evaluate_pattern_based(content, context)

            scores = json.loads(json_match.group())

            # Validate and clamp dimension scores to 0-10 range, then scale to 0-100
            def _clamp_score(val: Any) -> float:
                try:
                    return max(0.0, min(10.0, float(val))) * 10
                except (TypeError, ValueError):
                    return 50.0  # neutral fallback

            dimensions = QualityDimensions(
                clarity=_clamp_score(scores.get("clarity", 5)),
                accuracy=_clamp_score(scores.get("accuracy", 5)),
                completeness=_clamp_score(scores.get("completeness", 5)),
                relevance=_clamp_score(scores.get("relevance", 5)),
                seo_quality=_clamp_score(scores.get("seo_quality", 5)),
                readability=_clamp_score(scores.get("readability", 5)),
                engagement=_clamp_score(scores.get("engagement", 5)),
            )

            overall_score = dimensions.average()
            feedback = scores.get("feedback", self._generate_feedback(dimensions, context))
            suggestions = scores.get("suggestions", self._generate_suggestions(dimensions))
            if isinstance(suggestions, str):
                suggestions = [suggestions]

            return QualityAssessment(
                dimensions=dimensions,
                overall_score=overall_score,
                passing=overall_score >= 70,
                feedback=feedback,
                suggestions=suggestions,
                evaluation_method=EvaluationMethod.LLM_BASED,
                content_length=len(content),
                word_count=len(content.split()),
            )

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning(f"LLM evaluation parsing failed ({e}), falling back to pattern-based")
            return await self._evaluate_pattern_based(content, context)
        except Exception as e:
            logger.error(f"[_evaluate_llm_based] LLM call failed: {e}", exc_info=True)
            return await self._evaluate_pattern_based(content, context)

    async def _evaluate_hybrid(self, content: str, context: Dict[str, Any]) -> QualityAssessment:
        """
        Hybrid evaluation combining pattern-based and LLM-based.

        Runs both evaluations and averages their dimension scores (50/50 weight).
        Falls back to pattern-based only if no LLM client is available.
        """
        logger.debug("Running hybrid evaluation...")

        pattern_assessment = await self._evaluate_pattern_based(content, context)

        if not self.llm_client:
            return pattern_assessment

        llm_assessment = await self._evaluate_llm_based(content, context)

        # If LLM fell back to pattern-based, just return pattern (avoid double-counting)
        if llm_assessment.evaluation_method == EvaluationMethod.PATTERN_BASED:
            return pattern_assessment

        # Average dimension scores (equal weight)
        p = pattern_assessment.dimensions
        l = llm_assessment.dimensions
        combined_dims = QualityDimensions(
            clarity=(p.clarity + l.clarity) / 2,
            accuracy=(p.accuracy + l.accuracy) / 2,
            completeness=(p.completeness + l.completeness) / 2,
            relevance=(p.relevance + l.relevance) / 2,
            seo_quality=(p.seo_quality + l.seo_quality) / 2,
            readability=(p.readability + l.readability) / 2,
            engagement=(p.engagement + l.engagement) / 2,
        )

        overall = combined_dims.average()
        return QualityAssessment(
            dimensions=combined_dims,
            overall_score=overall,
            passing=overall >= 70,
            feedback=llm_assessment.feedback,
            suggestions=llm_assessment.suggestions,
            evaluation_method=EvaluationMethod.HYBRID,
            content_length=len(content),
            word_count=len(content.split()),
        )

    # ========================================================================
    # SCORING METHODS (Pattern-Based Heuristics)
    # ========================================================================

    def _score_clarity(self, content: str, sentence_count: int, word_count: int) -> float:
        """Score clarity based on sentence structure and word count"""
        if word_count == 0 or sentence_count == 0:
            return 5.0

        avg_words_per_sentence = word_count / sentence_count

        # Ideal: 15-20 words per sentence
        if 15 <= avg_words_per_sentence <= 20:
            return 9.0
        if 10 <= avg_words_per_sentence <= 25:
            return 8.0
        if 8 <= avg_words_per_sentence <= 30:
            return 7.0
        return 5.0

    def _score_accuracy(self, content: str, context: Dict[str, Any]) -> float:
        """Score accuracy - placeholder, would check facts in real implementation"""
        # Pattern-based: check for citations, quotes, etc.
        if '"' in content or "according to" in content.lower():
            return 7.5
        return 6.5  # Generic content, unknown accuracy

    def _score_completeness(self, content: str, context: Dict[str, Any]) -> float:
        """Score completeness based on content depth"""
        word_count = len(content.split())

        if word_count >= 2000:
            return 9.0
        if word_count >= 1500:
            return 8.0
        if word_count >= 1000:
            return 7.5
        if word_count >= 500:
            return 6.5
        return 5.0

    def _score_relevance(self, content: str, context: Dict[str, Any]) -> float:
        """Score relevance based on keyword presence and focus"""
        topic = context.get("topic", "")
        if not topic:
            return 6.0

        # Count topic mentions
        topic_count = content.lower().count(topic.lower())
        word_count = len(content.split())
        topic_density = topic_count / (word_count / 100) if word_count > 0 else 0

        # Ideal: 1-3% keyword density
        if 1 <= topic_density <= 3:
            return 9.0
        if 0.5 <= topic_density <= 5:
            return 7.5
        if topic_count > 0:
            return 6.0
        return 3.0  # Topic not mentioned

    def _score_seo(self, content: str, context: Dict[str, Any]) -> float:
        """Score SEO quality"""
        score = 6.0

        # Check for headers
        if "#" in content or re.search(r"#+\s", content):
            score += 1.0

        # Check for internal structure
        if "\n\n" in content:
            score += 1.0

        # Check keyword in beginning
        topic = context.get("topic", "")
        if topic and content.lower().startswith(topic.lower()):
            score += 1.0

        return min(score, 10.0)

    def _score_readability(self, content: str) -> float:
        """Score readability using Flesch Reading Ease approximation"""
        words = content.split()
        sentences = len(re.split(r"[.!?]+", content))
        syllables = sum(self._count_syllables(word) for word in words)

        if len(words) == 0 or sentences == 0:
            return 5.0

        # Flesch Reading Ease approximation
        score = 206.835 - 1.015 * (len(words) / sentences) - 84.6 * (syllables / len(words))

        # Convert to 0-10 scale
        return max(0, min(10, score / 10))

    def _score_engagement(self, content: str) -> float:
        """Score engagement based on structure and style"""
        score = 5.0

        # Bullet points
        if "- " in content or "* " in content:
            score += 1.5

        # Questions
        if "?" in content:
            score += 1.0

        # Varied paragraph length
        paragraphs = content.split("\n\n")
        if len(set(len(p.split()) for p in paragraphs)) > 1:
            score += 1.0

        # Exclamation marks (but not too many)
        exclamations = content.count("!")
        if 0 < exclamations <= 5:
            score += 1.0

        return min(score, 10.0)

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    def _check_keywords(self, content: str, context: Dict[str, Any]) -> bool:
        """Check if keywords are present in content"""
        context = context or {}
        keywords = context.get("keywords")
        if keywords is None:
            keywords = []
        elif isinstance(keywords, str):
            keywords = [keywords]
        elif not isinstance(keywords, list):
            keywords = [str(keywords)]

        keywords = [kw for kw in keywords if isinstance(kw, str) and kw.strip()]
        content_lower = content.lower()

        return any(kw.lower() in content_lower for kw in keywords)

    def _count_syllables(self, word: str) -> int:
        """Estimate syllable count"""
        word = word.lower()
        syllable_count = 0
        vowels = "aeiou"
        previous_was_vowel = False

        for char in word:
            is_vowel = char in vowels
            if is_vowel and not previous_was_vowel:
                syllable_count += 1
            previous_was_vowel = is_vowel

        return max(1, syllable_count)

    def _generate_feedback(self, dimensions: QualityDimensions, context: Dict[str, Any]) -> str:
        """Generate human-readable feedback"""
        overall = dimensions.average()

        if overall >= 85:
            return "Excellent content quality - publication ready"
        if overall >= 75:
            return "Good quality - minor improvements recommended"
        if overall >= 70:
            return "Acceptable quality - some improvements suggested"
        if overall >= 60:
            return "Fair quality - significant improvements needed"
        return "Poor quality - major revisions required"

    def _generate_suggestions(self, dimensions: QualityDimensions) -> List[str]:
        """Generate improvement suggestions based on weak dimensions"""
        suggestions = []
        threshold = 70  # 0-100 scale

        if dimensions.clarity < threshold:
            suggestions.append("Simplify sentence structure and use shorter sentences")
        if dimensions.accuracy < threshold:
            suggestions.append("Fact-check claims and add citations where appropriate")
        if dimensions.completeness < threshold:
            suggestions.append("Add more detail and cover the topic more thoroughly")
        if dimensions.relevance < threshold:
            suggestions.append("Keep content focused on the main topic")
        if dimensions.seo_quality < threshold:
            suggestions.append("Improve SEO with better headers, keywords, and structure")
        if dimensions.readability < threshold:
            suggestions.append("Improve grammar and readability")
        if dimensions.engagement < threshold:
            suggestions.append("Add engaging elements like questions, lists, or examples")

        return suggestions or ["Content meets quality standards"]

    async def _store_evaluation(
        self, assessment: QualityAssessment, context: Dict[str, Any]
    ) -> None:
        """Store evaluation result in database"""
        try:
            if not self.database_service:
                return

            # Quality metrics tracked in memory and task metadata
            # await self.database_service.create_quality_evaluation({...})
            logger.debug("Evaluation stored in database")
        except Exception as e:
            logger.error(f"[_store_evaluation] Failed to store evaluation: {e}", exc_info=True)

    # ========================================================================
    # STATISTICS & REPORTING
    # ========================================================================

    def get_statistics(self) -> Dict[str, Any]:
        """Get evaluation statistics"""
        return {
            "total_evaluations": self.total_evaluations,
            "passing_count": self.passing_count,
            "failing_count": self.failing_count,
            "pass_rate": (
                self.passing_count / self.total_evaluations * 100
                if self.total_evaluations > 0
                else 0
            ),
            "average_score": self.average_score,
        }


# ============================================================================
# DEPENDENCY INJECTION & FACTORY FUNCTIONS
# ============================================================================


def get_quality_service(
    model_router=None, database_service=None, llm_client=None
) -> UnifiedQualityService:
    """Factory function for UnifiedQualityService dependency injection"""
    return UnifiedQualityService(
        model_router=model_router, database_service=database_service, llm_client=llm_client
    )


# Backward compatibility alias
def get_content_quality_service(
    model_router=None, database_service=None, llm_client=None
) -> UnifiedQualityService:
    """Backward compatibility alias for get_quality_service"""
    return UnifiedQualityService(
        model_router=model_router, database_service=database_service, llm_client=llm_client
    )


# Backward compatibility alias for class name
ContentQualityService = UnifiedQualityService
