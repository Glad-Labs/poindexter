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
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from services.logger_config import get_logger

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
            from services.site_config import site_config
            effective_floor = site_config.get_float("qa_critical_floor", self.CRITICAL_FLOOR)
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

    # Flesch-Kincaid Grade Level (complementary readability metric)
    flesch_kincaid_grade_level: Optional[float] = None

    # Truncation detection
    truncation_detected: bool = False

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
            "flesch_kincaid_grade_level": self.flesch_kincaid_grade_level,
            "truncation_detected": self.truncation_detected,
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

    @staticmethod
    def _qa_cfg() -> dict:
        """Load all QA pipeline thresholds from DB via site_config.

        Every threshold in the QA pipeline is tunable via app_settings
        (key prefix: qa_). Returns a dict of all values with sensible defaults.
        Change any value with a simple SQL UPDATE on app_settings.
        """
        from services.site_config import site_config

        return {
            # --- Overall pipeline ---
            "pass_threshold": site_config.get_float("qa_pass_threshold", 70.0),
            "critical_floor": site_config.get_float("qa_critical_floor", 50.0),
            "artifact_penalty_per": site_config.get_float("qa_artifact_penalty_per", 5.0),
            "artifact_penalty_max": site_config.get_float("qa_artifact_penalty_max", 20.0),
            # --- Flesch-Kincaid target ---
            "fk_target_min": site_config.get_float("qa_fk_target_min", 8.0),
            "fk_target_max": site_config.get_float("qa_fk_target_max", 12.0),
            # --- Clarity ---
            "clarity_ideal_min": site_config.get_int("qa_clarity_ideal_min_wps", 15),
            "clarity_ideal_max": site_config.get_int("qa_clarity_ideal_max_wps", 20),
            "clarity_good_min": site_config.get_int("qa_clarity_good_min_wps", 10),
            "clarity_good_max": site_config.get_int("qa_clarity_good_max_wps", 25),
            "clarity_ok_min": site_config.get_int("qa_clarity_ok_min_wps", 8),
            "clarity_ok_max": site_config.get_int("qa_clarity_ok_max_wps", 30),
            # --- Accuracy ---
            "accuracy_baseline": site_config.get_float("qa_accuracy_baseline", 7.0),
            "accuracy_good_link_bonus": site_config.get_float("qa_accuracy_good_link_bonus", 0.3),
            "accuracy_good_link_max": site_config.get_float("qa_accuracy_good_link_max_bonus", 1.0),
            "accuracy_bad_link_penalty": site_config.get_float("qa_accuracy_bad_link_penalty", 0.5),
            "accuracy_bad_link_max": site_config.get_float("qa_accuracy_bad_link_max_penalty", 2.0),
            "accuracy_citation_bonus": site_config.get_float("qa_accuracy_citation_bonus", 0.3),
            "accuracy_first_person_penalty": site_config.get_float("qa_accuracy_first_person_penalty", 1.0),
            "accuracy_first_person_max": site_config.get_float("qa_accuracy_first_person_max_penalty", 3.0),
            "accuracy_meta_commentary_penalty": site_config.get_float("qa_accuracy_meta_commentary_penalty", 0.5),
            "accuracy_meta_commentary_max": site_config.get_float("qa_accuracy_meta_commentary_max_penalty", 2.0),
            # --- Completeness ---
            "completeness_word_2000": site_config.get_float("qa_completeness_word_2000_score", 6.5),
            "completeness_word_1500": site_config.get_float("qa_completeness_word_1500_score", 6.0),
            "completeness_word_1000": site_config.get_float("qa_completeness_word_1000_score", 5.0),
            "completeness_word_500": site_config.get_float("qa_completeness_word_500_score", 3.5),
            "completeness_word_min": site_config.get_float("qa_completeness_word_min_score", 2.0),
            "completeness_heading_bonus": site_config.get_float("qa_completeness_heading_bonus", 0.3),
            "completeness_heading_max": site_config.get_float("qa_completeness_heading_max_bonus", 1.5),
            "completeness_truncation_penalty": site_config.get_float("qa_completeness_truncation_penalty", 3.0),
            # --- Relevance ---
            "relevance_no_topic_default": site_config.get_float("qa_relevance_no_topic_default", 6.0),
            "relevance_high_coverage": site_config.get_float("qa_relevance_high_coverage_score", 8.5),
            "relevance_med_coverage": site_config.get_float("qa_relevance_med_coverage_score", 7.0),
            "relevance_low_coverage": site_config.get_float("qa_relevance_low_coverage_score", 5.5),
            "relevance_none_coverage": site_config.get_float("qa_relevance_none_coverage_score", 3.0),
            "relevance_stuffing_hard": site_config.get_float("qa_relevance_stuffing_hard_density", 5.0),
            "relevance_stuffing_soft": site_config.get_float("qa_relevance_stuffing_soft_density", 3.0),
            # --- SEO ---
            "seo_baseline": site_config.get_float("qa_seo_baseline", 6.0),
            # --- Engagement ---
            "engagement_baseline": site_config.get_float("qa_engagement_baseline", 6.0),
        }

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

        truncated = self.detect_truncation(content)

        # Artifact detection: photo metadata, leaked prompts, placeholders, etc.
        cfg = self._qa_cfg()
        artifacts = self._detect_artifacts(content)
        if artifacts:
            artifact_penalty = min(
                len(artifacts) * cfg["artifact_penalty_per"],
                cfg["artifact_penalty_max"],
            )
            overall_score = max(0, overall_score - artifact_penalty)
            logger.warning(
                "[QA] Artifacts detected (-%d pts): %s",
                artifact_penalty, "; ".join(artifacts),
            )

        # LLM pattern detection: buzzwords, filler, cliché openers, etc.
        llm_penalty, llm_issues = self._score_llm_patterns(content)
        if llm_issues:
            overall_score = max(0, overall_score + llm_penalty)  # penalty is negative
            logger.warning(
                "[QA] LLM patterns detected (%+.0f pts): %s",
                llm_penalty, "; ".join(llm_issues),
            )

        # Flesch-Kincaid Grade Level (complementary readability metric)
        fk_grade = self.flesch_kincaid_grade_level(content)

        # Truncated content cannot pass quality — it's incomplete by definition
        passing = overall_score >= cfg["pass_threshold"] and not truncated

        suggestions = self._generate_suggestions(dimensions)

        # Add artifact-specific suggestions
        for artifact in artifacts:
            suggestions.insert(0, f"Content contains {artifact} — must be cleaned before publishing.")

        # Add LLM pattern suggestions
        for issue in llm_issues:
            suggestions.insert(0, f"AI writing pattern: {issue} — rewrite to sound more natural.")

        # Add FK-based suggestion when outside target range
        if fk_grade > cfg["fk_target_max"]:
            suggestions.append(
                f"Flesch-Kincaid grade level is {fk_grade:.1f} "
                f"(target: {cfg['fk_target_min']:.0f}-{cfg['fk_target_max']:.0f}). "
                "Simplify vocabulary and shorten sentences for broader readability."
            )
        elif fk_grade < cfg["fk_target_min"] and word_count > 100:
            suggestions.append(
                f"Flesch-Kincaid grade level is {fk_grade:.1f} "
                f"(target: {cfg['fk_target_min']:.0f}-{cfg['fk_target_max']:.0f}). "
                "Content may be too simplistic; consider adding more depth."
            )

        if truncated:
            suggestions.insert(
                0,
                "Content appears truncated (cut off mid-sentence). The LLM may have hit its output token limit. Try regenerating with a shorter target length or a model with a larger context window.",
            )

        return QualityAssessment(
            dimensions=dimensions,
            overall_score=overall_score,
            passing=passing,
            feedback=self._generate_feedback(dimensions, context),
            suggestions=suggestions,
            evaluation_method=EvaluationMethod.PATTERN_BASED,
            content_length=len(content),
            word_count=word_count,
            flesch_kincaid_grade_level=fk_grade,
            truncation_detected=truncated,
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
                logger.warning(
                    "LLM evaluation returned no valid JSON, falling back to pattern-based"
                )
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
                passing=overall_score >= self._qa_cfg()["pass_threshold"],
                feedback=feedback,
                suggestions=suggestions,
                evaluation_method=EvaluationMethod.LLM_BASED,
                content_length=len(content),
                word_count=len(content.split()),
                flesch_kincaid_grade_level=self.flesch_kincaid_grade_level(content),
            )

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning(
                f"LLM evaluation parsing failed ({e}), falling back to pattern-based", exc_info=True
            )
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
            passing=overall >= self._qa_cfg()["pass_threshold"],
            feedback=llm_assessment.feedback,
            suggestions=llm_assessment.suggestions,
            evaluation_method=EvaluationMethod.HYBRID,
            content_length=len(content),
            word_count=len(content.split()),
            flesch_kincaid_grade_level=self.flesch_kincaid_grade_level(content),
        )

    # ========================================================================
    # FLESCH-KINCAID GRADE LEVEL
    # ========================================================================

    @staticmethod
    def flesch_kincaid_grade_level(text: str) -> float:
        """Compute the Flesch-Kincaid Grade Level for *text*.

        Formula:
            0.39 * (total_words / total_sentences)
            + 11.8 * (total_syllables / total_words)
            - 15.59

        Syllable counting uses a simple vowel-group heuristic: each
        consecutive run of vowels (a, e, i, o, u) in a word counts as
        one syllable, with a minimum of 1 syllable per word.

        Returns the grade level as a float.  Lower values indicate easier
        readability (target: 8-12 for general audience content).
        """
        if not text or not text.strip():
            return 0.0

        # Strip HTML/markdown for cleaner analysis
        clean = re.sub(r"<[^>]+>", "", text)
        clean = re.sub(r"#{1,6}\s", "", clean)

        words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", clean)
        total_words = len(words)
        if total_words == 0:
            return 0.0

        # Sentence splitting: split on . ! ? (ignore abbreviations as noise)
        sentences = [s for s in re.split(r"[.!?]+", clean) if s.strip()]
        total_sentences = max(len(sentences), 1)

        # Count syllables using vowel-group heuristic
        def _count_syllables(word: str) -> int:
            word = word.lower()
            count = 0
            prev_vowel = False
            for ch in word:
                is_vowel = ch in "aeiou"
                if is_vowel and not prev_vowel:
                    count += 1
                prev_vowel = is_vowel
            return max(count, 1)

        total_syllables = sum(_count_syllables(w) for w in words)

        grade = (
            0.39 * (total_words / total_sentences)
            + 11.8 * (total_syllables / total_words)
            - 15.59
        )
        return round(grade, 2)

    # ========================================================================
    # SCORING METHODS (Pattern-Based Heuristics)
    # ========================================================================

    def _score_clarity(self, content: str, sentence_count: int, word_count: int) -> float:
        """Score clarity based on sentence structure and word count.
        Thresholds tunable via qa_clarity_* app_settings keys."""
        cfg = self._qa_cfg()
        if word_count == 0 or sentence_count == 0:
            return 5.0

        avg_words_per_sentence = word_count / sentence_count

        if cfg["clarity_ideal_min"] <= avg_words_per_sentence <= cfg["clarity_ideal_max"]:
            return 9.0
        if cfg["clarity_good_min"] <= avg_words_per_sentence <= cfg["clarity_good_max"]:
            return 8.0
        if cfg["clarity_ok_min"] <= avg_words_per_sentence <= cfg["clarity_ok_max"]:
            return 7.0
        return 5.0

    def _score_accuracy(self, content: str, context: Dict[str, Any]) -> float:
        """Score accuracy based on citation patterns and factual anchors.
        Thresholds tunable via qa_accuracy_* app_settings keys."""
        cfg = self._qa_cfg()
        score = cfg["accuracy_baseline"]
        content_lower = content.lower()

        # External links: only count links to known reputable domains.
        all_links = re.findall(r"https?://([^\s\)\]\"'>]+)", content)
        reputable_domains = {
            "github.com", "arxiv.org", "docs.python.org", "docs.rs",
            "developer.mozilla.org", "stackoverflow.com", "wikipedia.org",
            "news.ycombinator.com", "devto.dev", "dev.to", "blog.rust-lang.org",
            "go.dev", "kubernetes.io", "docker.com", "vercel.com", "nextjs.org",
            "react.dev", "pytorch.org", "huggingface.co", "openai.com",
            "gladlabs.io", "www.gladlabs.io",
        }
        good_links = sum(1 for link in all_links if any(d in link for d in reputable_domains))
        bad_links = len(all_links) - good_links
        score += min(good_links * cfg["accuracy_good_link_bonus"], cfg["accuracy_good_link_max"])
        score -= min(bad_links * cfg["accuracy_bad_link_penalty"], cfg["accuracy_bad_link_max"])

        # Citation/reference patterns: [1], (Smith 2023), Source:, References:
        citation_patterns = [
            r"\[\d+\]",  # [1], [12]
            r"\(\w[^)]{1,40}\d{4}\)",  # (Author 2023)
            r"(?:source|reference|cited|per|via):",
            r"according to\b",
            r"research (?:shows?|suggests?|finds?|indicates?)\b",
            r"studies? (?:show|suggest|find|indicate)\b",
            r"published (?:in|by)\b",
        ]
        for pat in citation_patterns:
            if re.search(pat, content_lower):
                score += cfg["accuracy_citation_bonus"]

        # Named quotes in proper context (not decorative use of quotation marks)
        named_quote = re.search(r'"[^"]{10,}"[,\s]+(?:said|wrote|noted|according)', content)
        if named_quote:
            score += 0.5

        # Voice violation: penalize first-person claims about building/creating things
        # (Glad Labs is a publication, not a builder — "I built" is almost always wrong)
        first_person_claims = len(re.findall(
            r"\b(?:I|we)\s+(?:built|created|developed|designed|made|launched|shipped|released|wrote)\b",
            content, re.IGNORECASE,
        ))
        if first_person_claims > 0:
            score -= min(
                first_person_claims * cfg["accuracy_first_person_penalty"],
                cfg["accuracy_first_person_max"],
            )

        # Meta-commentary penalty: "this post explores", "in this article we will", etc.
        meta_commentary = len(re.findall(
            r"\b(?:this\s+(?:post|article|blog|piece)\s+(?:explores?|examines?|discusses?|looks\s+at|covers?|delves?))"
            r"|\b(?:in\s+this\s+(?:post|article|blog|piece))"
            r"|\b(?:(?:we.ll|let.s|we\s+will)\s+(?:explore|discuss|examine|look\s+at|dive\s+into))",
            content, re.IGNORECASE,
        ))
        if meta_commentary > 0:
            score -= min(
                meta_commentary * cfg["accuracy_meta_commentary_penalty"],
                cfg["accuracy_meta_commentary_max"],
            )

        return min(max(score, 0.0), 10.0)

    @staticmethod
    def detect_truncation(content: str) -> bool:
        """Detect if content was truncated by LLM token limits.

        Checks whether the content ends mid-sentence, which is a strong signal
        that the LLM hit its output token limit before completing the article.
        """
        if not content or len(content.strip()) < 100:
            return False

        # Strip HTML tags for analysis
        text = re.sub(r"<[^>]+>", "", content).strip()
        if not text:
            return False

        # Get the last non-empty line
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if not lines:
            return False

        last_line = lines[-1]

        # Content ending with terminal punctuation is complete
        if last_line[-1] in ".!?)\"'":
            return False

        # Content ending with a URL or link is likely a references section (OK)
        if re.search(r"https?://\S+$", last_line):
            return False

        # Content ending with a markdown/HTML heading is truncated
        if re.match(r"^#{1,6}\s", last_line):
            return True

        # If the last line is very short and looks like a fragment, it's truncated
        # (e.g., "The Ingress controller uses labels and selectors to")
        if not last_line[-1] in ".!?)\"':*" and len(last_line) > 20:
            logger.warning(
                f"[TRUNCATION] Content appears truncated — last line: ...{last_line[-80:]}"
            )
            return True

        return False

    def _score_completeness(self, content: str, context: Dict[str, Any]) -> float:
        """Score completeness based on depth signals beyond raw word count.
        Thresholds tunable via qa_completeness_* app_settings keys."""
        cfg = self._qa_cfg()
        word_count = len(content.split())
        score = 0.0

        # Word-count baseline
        if word_count >= 2000:
            score += cfg["completeness_word_2000"]
        elif word_count >= 1500:
            score += cfg["completeness_word_1500"]
        elif word_count >= 1000:
            score += cfg["completeness_word_1000"]
        elif word_count >= 500:
            score += cfg["completeness_word_500"]
        else:
            score += cfg["completeness_word_min"]

        # Structural depth signals
        heading_count = len(re.findall(r"^#{1,3}\s", content, re.MULTILINE))
        score += min(heading_count * cfg["completeness_heading_bonus"], cfg["completeness_heading_max"])

        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        if len(paragraphs) >= 5:
            score += 0.5

        # Intro/conclusion present (first and last paragraphs are non-trivial)
        if paragraphs and len(paragraphs[0].split()) >= 30:
            score += 0.5
        if len(paragraphs) > 1 and len(paragraphs[-1].split()) >= 20:
            score += 0.5

        # Contains lists (signals structured coverage)
        if re.search(r"^[-*]\s", content, re.MULTILINE):
            score += 0.5

        # Truncation penalty — content cut off mid-sentence by LLM token limit
        if self.detect_truncation(content):
            score = max(score - cfg["completeness_truncation_penalty"], 0.0)

        return min(score, 10.0)

    def _score_relevance(self, content: str, context: Dict[str, Any]) -> float:
        """Score relevance using topic-word family matching to resist keyword stuffing.
        Thresholds tunable via qa_relevance_* app_settings keys."""
        cfg = self._qa_cfg()
        topic = context.get("topic", "") or context.get("primary_keyword", "")
        if not topic:
            return cfg["relevance_no_topic_default"]

        content_lower = content.lower()
        topic_words = [w.lower() for w in re.findall(r"\b\w{4,}\b", topic)]
        word_count = len(content.split())

        if not topic_words or word_count == 0:
            return cfg["relevance_no_topic_default"]

        matched_words = sum(1 for w in topic_words if w in content_lower)
        coverage = matched_words / len(topic_words)

        exact_count = content_lower.count(topic.lower())
        density = exact_count / (word_count / 100)

        if coverage >= 0.8:
            base = cfg["relevance_high_coverage"]
        elif coverage >= 0.5:
            base = cfg["relevance_med_coverage"]
        elif coverage >= 0.25:
            base = cfg["relevance_low_coverage"]
        else:
            base = cfg["relevance_none_coverage"]

        if density > cfg["relevance_stuffing_hard"]:
            base = min(base, 5.5)
        elif density > cfg["relevance_stuffing_soft"]:
            base = min(base, 7.0)

        return min(base, 10.0)

    def _score_seo(self, content: str, context: Dict[str, Any]) -> float:
        """Score SEO quality. Baseline tunable via qa_seo_baseline."""
        score = self._qa_cfg()["seo_baseline"]

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
        """Score readability using Flesch Reading Ease with technical content adjustment.

        The raw Flesch formula heavily penalizes polysyllabic technical terms
        (PostgreSQL, Kubernetes, infrastructure) causing valid technical writing
        to score 20-40. We apply a floor of 5.5/10 for technical content and
        compress the scale so technical writing lands in 5.5-8.5 range.
        """
        words = content.split()
        sentences = len(re.split(r"[.!?]+", content))
        syllables = sum(self._count_syllables(word) for word in words)

        if len(words) == 0 or sentences == 0:
            return 7.0

        # Flesch Reading Ease approximation (0-100 scale)
        flesch = 206.835 - 1.015 * (len(words) / sentences) - 84.6 * (syllables / len(words))

        # Technical content floor: Flesch scores below 30 are normal for
        # technical writing (PostgreSQL, Kubernetes, microservices). Don't let
        # readability tank the overall score for valid technical prose.
        # Scale: Flesch 0→7.0, 30→7.5, 60→8.0, 100→9.0
        if flesch >= 60:
            return min(10.0, 8.0 + (flesch - 60) * 0.025)  # 60→8.0, 100→9.0
        elif flesch >= 30:
            return 7.5 + (flesch - 30) * 0.017  # 30→7.5, 60→8.0
        else:
            return max(7.0, 7.0 + flesch * 0.017)  # 0→7.0, 30→7.5

    @staticmethod
    def _detect_artifacts(content: str) -> list[str]:
        """Detect junk artifacts that should never appear in published content.

        Returns list of artifact descriptions found. Each one should penalize the score.
        """
        artifacts = []

        # Photo metadata / attribution junk (may be wrapped in *italic* markdown)
        photo_meta = re.findall(
            r"(?i)(?:\*?\s*photo\s+by\s+[\w\s]+\s+on\s+(?:pexels|unsplash|pixabay|shutterstock)\s*\*?)"
            r"|(?:image\s+(?:credit|source|courtesy|by)\s*:)"
            r"|(?:shutterstock\s+(?:id|#))"
            r"|(?:getty\s+images)"
            r"|(?:EXIF|IPTC|XMP)\b"
            r"|(?:alt\s*=\s*[\"'])"
            r"|(?:photographer:\s)",
            content,
        )
        if photo_meta:
            artifacts.append(f"Photo metadata/attribution ({len(photo_meta)} instances)")

        # Leaked SDXL/image generation prompts
        sdxl_leaks = re.findall(
            r"(?i)(?:stable\s+diffusion|SDXL|negative\s+prompt|guidance.scale|cinematic\s+lighting,\s+no\s+(?:people|text|faces))"
            r"|(?::\s+A\s+(?:diagram|flowchart|illustration|visualization)\s+(?:showing|comparing|depicting))",
            content,
        )
        if sdxl_leaks:
            artifacts.append(f"Leaked image generation prompts ({len(sdxl_leaks)} instances)")

        # Unresolved placeholders
        placeholders = re.findall(
            r"\[IMAGE-\d+[^\]]*\]|\[TODO[^\]]*\]|\[PLACEHOLDER[^\]]*\]|\[INSERT[^\]]*\]|\[TBD\]",
            content, re.IGNORECASE,
        )
        if placeholders:
            artifacts.append(f"Unresolved placeholders ({len(placeholders)} instances)")

        # Raw markdown/rendering artifacts that shouldn't be visible
        raw_artifacts = re.findall(
            r"\\n\\n|\\\\n|&amp;|&lt;|&gt;|<br\s*/?>|</?(?:div|span|p)\b",
            content,
        )
        if raw_artifacts:
            artifacts.append(f"Raw HTML/markdown artifacts ({len(raw_artifacts)} instances)")

        # Empty sections (heading followed immediately by another heading or end)
        empty_sections = re.findall(r"^#{1,4}\s+.+\n\s*#{1,4}\s+", content, re.MULTILINE)
        if empty_sections:
            artifacts.append(f"Empty sections ({len(empty_sections)} instances)")

        # Empty reference/resource sections (section with bullet labels but no URLs)
        ref_sections = re.findall(
            r"(?i)(?:#{1,4}\s+(?:Suggested\s+)?(?:External\s+)?(?:Resources?|References?|Further\s+Reading|Links?))"
            r"[^\n]*\n(?:\s*[\*\-]\s+\*?\*?[^\n]+:\*?\*?\s*(?:$|\n))+",
            content, re.MULTILINE,
        )
        for ref in ref_sections:
            if not re.search(r"https?://", ref):
                artifacts.append("Empty reference section (labels without URLs)")

        # Repeated consecutive sentences (copy-paste or LLM loop)
        sentences = [s.strip() for s in re.split(r'[.!?]+', content) if len(s.strip()) > 30]
        seen = set()
        dupes = 0
        for s in sentences:
            normalized = s.lower().strip()
            if normalized in seen:
                dupes += 1
            seen.add(normalized)
        if dupes > 0:
            artifacts.append(f"Duplicate sentences ({dupes} repeated)")

        return artifacts

    @staticmethod
    def _score_llm_patterns(content: str) -> tuple[float, list[str]]:
        """Detect and penalize common LLM-generated content patterns.

        Returns (penalty, list_of_issues) where penalty is a NEGATIVE number
        to subtract from the overall score (0 to -25 range).

        All thresholds are tunable via app_settings (key prefix: qa_llm_).
        """
        from services.site_config import site_config

        # Load tunable thresholds from DB (with sensible defaults)
        _t = {
            "buzzword_warn": site_config.get_int("qa_llm_buzzword_warn_threshold", 3),
            "buzzword_fail": site_config.get_int("qa_llm_buzzword_fail_threshold", 5),
            "buzzword_penalty": site_config.get_float("qa_llm_buzzword_penalty_per", 0.5),
            "buzzword_max": site_config.get_float("qa_llm_buzzword_max_penalty", 5.0),
            "buzzword_warn_penalty": site_config.get_float("qa_llm_buzzword_warn_penalty_per", 0.3),
            "buzzword_warn_max": site_config.get_float("qa_llm_buzzword_warn_max_penalty", 2.0),
            "filler_warn": site_config.get_int("qa_llm_filler_warn_threshold", 2),
            "filler_fail": site_config.get_int("qa_llm_filler_fail_threshold", 4),
            "filler_penalty": site_config.get_float("qa_llm_filler_penalty_per", 0.5),
            "filler_max": site_config.get_float("qa_llm_filler_max_penalty", 4.0),
            "filler_warn_penalty": site_config.get_float("qa_llm_filler_warn_penalty_per", 0.3),
            "opener_penalty": site_config.get_float("qa_llm_opener_penalty", 5.0),
            "transition_penalty": site_config.get_float("qa_llm_transition_penalty_per", 1.0),
            "listicle_penalty": site_config.get_float("qa_llm_listicle_title_penalty", 2.0),
            "hedge_ratio": site_config.get_float("qa_llm_hedge_ratio_threshold", 0.02),
            "hedge_penalty": site_config.get_float("qa_llm_hedge_penalty", 2.0),
            "repetitive_penalty": site_config.get_float("qa_llm_repetitive_starter_penalty_per", 1.0),
            "repetitive_max": site_config.get_float("qa_llm_repetitive_starter_max_penalty", 4.0),
            "formulaic_penalty": site_config.get_float("qa_llm_formulaic_structure_penalty", 2.0),
            "formulaic_min_avg": site_config.get_int("qa_llm_formulaic_min_avg_words", 50),
            "formulaic_variance": site_config.get_float("qa_llm_formulaic_variance", 0.2),
            "exclamation_threshold": site_config.get_int("qa_llm_exclamation_threshold", 5),
            "exclamation_penalty": site_config.get_float("qa_llm_exclamation_penalty_per", 0.3),
            "exclamation_max": site_config.get_float("qa_llm_exclamation_max_penalty", 2.0),
            "repetitive_min_count": site_config.get_int("qa_llm_repetitive_min_count", 3),
            "transition_min_count": site_config.get_int("qa_llm_transition_min_count", 2),
            "enabled": site_config.get_bool("qa_llm_patterns_enabled", True),
        }

        issues = []
        penalty = 0.0

        if not _t["enabled"]:
            return penalty, issues

        content_lower = content.lower()

        # --- 1. CLICHÉ OPENERS (the biggest AI slop tell) ---
        slop_openers = [
            r"^#[^\n]*\n+\s*in today.s (?:digital|fast-paced|ever-changing|rapidly evolving)",
            r"^#[^\n]*\n+\s*in the (?:world|realm|landscape|arena) of",
            r"^#[^\n]*\n+\s*(?:as|with) (?:artificial intelligence|AI|technology) continues to",
            r"^#[^\n]*\n+\s*in an era (?:of|where)",
            r"^#[^\n]*\n+\s*the (?:world|landscape|field) of .{5,30} is (?:evolving|changing|transforming)",
            r"^#[^\n]*\n+\s*imagine a world where",
        ]
        for pat in slop_openers:
            if re.search(pat, content, re.IGNORECASE | re.MULTILINE):
                issues.append("Cliché AI opener detected")
                penalty -= _t["opener_penalty"]
                break

        # --- 2. CORPORATE BUZZWORDS (density check) ---
        buzzwords = [
            "leverage", "synergy", "paradigm", "game-changer", "game changer",
            "cutting-edge", "cutting edge", "innovative", "robust", "seamless",
            "harness", "unlock the power", "unleash", "revolutionize",
            "transformative", "disruptive", "scalable solution", "empower",
            "drive innovation", "holistic approach", "best-in-class",
            "next-generation", "next generation", "world-class",
        ]
        buzz_count = sum(1 for b in buzzwords if b in content_lower)
        if buzz_count >= _t["buzzword_fail"]:
            issues.append(f"Heavy buzzword usage ({buzz_count} instances)")
            penalty -= min(buzz_count * _t["buzzword_penalty"], _t["buzzword_max"])
        elif buzz_count >= _t["buzzword_warn"]:
            issues.append(f"Moderate buzzword usage ({buzz_count} instances)")
            penalty -= min(buzz_count * _t["buzzword_warn_penalty"], _t["buzzword_warn_max"])

        # --- 3. FILLER PHRASES (padding that adds no information) ---
        fillers = [
            r"it.s (?:important|worth|crucial|essential) to (?:note|mention|understand|remember) that",
            r"it should be (?:noted|mentioned) that",
            r"it.s no secret that",
            r"needless to say",
            r"it goes without saying",
            r"at the end of the day",
            r"when it comes to",
            r"in order to",  # just use "to"
            r"the fact (?:that|of the matter)",
            r"as (?:we all know|everyone knows)",
            r"in today.s (?:world|age|landscape|environment)",
            r"the bottom line is",
            r"all things considered",
            r"at its core",
            r"when all is said and done",
        ]
        filler_count = sum(len(re.findall(pat, content_lower)) for pat in fillers)
        if filler_count >= _t["filler_fail"]:
            issues.append(f"Excessive filler phrases ({filler_count} instances)")
            penalty -= min(filler_count * _t["filler_penalty"], _t["filler_max"])
        elif filler_count >= _t["filler_warn"]:
            issues.append(f"Filler phrases detected ({filler_count} instances)")
            penalty -= filler_count * _t["filler_warn_penalty"]

        # --- 4. GENERIC TRANSITIONS (LLMs love these) ---
        generic_transitions = [
            r"(?:^|\n)\s*in conclusion[,.]",
            r"(?:^|\n)\s*to (?:summarize|sum up)[,.]",
            r"(?:^|\n)\s*in summary[,.]",
            r"(?:^|\n)\s*(?:all in all|overall)[,.]",
            r"(?:^|\n)\s*(?:wrapping up|to wrap up)[,.]",
            r"(?:^|\n)\s*(?:final thoughts|closing thoughts)[,.]",
            r"(?:^|\n)\s*(?:moving forward|going forward)[,.]",
        ]
        transition_count = sum(1 for pat in generic_transitions if re.search(pat, content_lower))
        if transition_count >= _t["transition_min_count"]:
            issues.append(f"Generic transitions ({transition_count} instances)")
            penalty -= transition_count * _t["transition_penalty"]

        # --- 5. REPETITIVE SENTENCE STARTERS ---
        sentences = [s.strip() for s in re.split(r'[.!?]\s+', content) if len(s.strip()) > 10]
        if len(sentences) >= 5:
            # Get first 3 words of each sentence
            starters = []
            for s in sentences:
                words = s.split()[:3]
                if words:
                    starters.append(" ".join(words).lower())

            # Check for repeated starters
            from collections import Counter
            starter_counts = Counter(starters)
            repeated = {k: v for k, v in starter_counts.items() if v >= _t["repetitive_min_count"]}
            if repeated:
                worst = max(repeated.values())
                issues.append(f"Repetitive sentence starters ({worst}x same opening)")
                penalty -= min(worst * _t["repetitive_penalty"], _t["repetitive_max"])

        # --- 6. LISTICLE TITLE PATTERNS (overused AI format) ---
        title_match = re.match(r'^#\s*(.+)$', content, re.MULTILINE)
        if title_match:
            title = title_match.group(1)
            listicle_patterns = [
                r"^\d+\s+(?:ways?|things?|tips?|tricks?|reasons?|secrets?|mistakes?|hacks?)\s",
                r"(?:ultimate|definitive|complete|comprehensive)\s+guide",
                r"everything you need to know",
                r"you need to know about",
                r"a deep dive into",
            ]
            for pat in listicle_patterns:
                if re.search(pat, title, re.IGNORECASE):
                    issues.append(f"Generic listicle/guide title pattern")
                    penalty -= _t["listicle_penalty"]
                    break

        # --- 7. OVER-HEDGING (non-committal language density) ---
        hedges = re.findall(
            r"\b(?:arguably|somewhat|potentially|perhaps|possibly|might|may|could)\b",
            content_lower,
        )
        hedge_ratio = len(hedges) / max(len(content_lower.split()), 1)
        if hedge_ratio > _t["hedge_ratio"]:
            issues.append(f"Over-hedging ({len(hedges)} non-committal words)")
            penalty -= _t["hedge_penalty"]

        # --- 8. EMOJI/EXCLAMATION SPAM ---
        exclamation_count = content.count("!")
        if exclamation_count > _t["exclamation_threshold"]:
            issues.append(f"Exclamation spam ({exclamation_count} instances)")
            penalty -= min(
                (exclamation_count - _t["exclamation_threshold"]) * _t["exclamation_penalty"],
                _t["exclamation_max"],
            )

        # --- 9. FORMULAIC STRUCTURE ---
        # Check if every section follows the same pattern (intro sentence + 3 paras + summary)
        sections = re.split(r'\n#{2,4}\s+', content)
        if len(sections) >= 4:
            section_lengths = [len(s.split()) for s in sections[1:]]  # skip pre-first-heading
            if section_lengths:
                avg = sum(section_lengths) / len(section_lengths)
                # If all sections are within 20% of the same length, it's formulaic
                if avg > _t["formulaic_min_avg"] and all(
                    abs(l - avg) / avg < _t["formulaic_variance"] for l in section_lengths
                ):
                    issues.append("Formulaic structure (all sections same length)")
                    penalty -= _t["formulaic_penalty"]

        return penalty, issues

    def _score_engagement(self, content: str) -> float:
        """Score engagement based on structure and style. Baseline tunable via qa_engagement_baseline."""
        score = self._qa_cfg()["engagement_baseline"]

        # Bullet points / lists
        if "- " in content or "* " in content:
            score += 1.0

        # Questions (hooks the reader)
        question_count = content.count("?")
        if question_count >= 3:
            score += 1.0
        elif question_count >= 1:
            score += 0.5

        # Varied paragraph length (good pacing)
        paragraphs = [p for p in content.split("\n\n") if p.strip()]
        if len(paragraphs) >= 4:
            score += 0.5
        if len(set(len(p.split()) // 10 for p in paragraphs)) > 2:
            score += 0.5

        # Code blocks (technical engagement signal)
        if "```" in content:
            score += 0.5

        # Bold/emphasis (highlights key points)
        if "**" in content or "__" in content:
            score += 0.5

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
        """Store evaluation result in database for audit trail and learning loop."""
        try:
            if not self.database_service:
                return

            task_id = context.get("task_id") or context.get("content_id")
            if not task_id:
                logger.debug("[_store_evaluation] No task_id in context — skipping persistence")
                return

            await self.database_service.create_quality_evaluation(
                {
                    "content_id": task_id,
                    "task_id": task_id,
                    "overall_score": assessment.overall_score,
                    "criteria": {
                        "clarity": assessment.dimensions.clarity,
                        "accuracy": assessment.dimensions.accuracy,
                        "completeness": assessment.dimensions.completeness,
                        "relevance": assessment.dimensions.relevance,
                        "seo_quality": assessment.dimensions.seo_quality,
                        "readability": assessment.dimensions.readability,
                        "engagement": assessment.dimensions.engagement,
                        "flesch_kincaid_grade_level": assessment.flesch_kincaid_grade_level,
                    },
                    "passing": assessment.passing,
                    "feedback": assessment.feedback,
                    "suggestions": assessment.suggestions,
                    "evaluated_by": assessment.evaluated_by,
                    "evaluation_method": (
                        assessment.evaluation_method.value
                        if hasattr(assessment.evaluation_method, "value")
                        else str(assessment.evaluation_method)
                    ),
                    "content_length": assessment.content_length,
                    "context_data": {
                        k: v
                        for k, v in context.items()
                        if k not in ("content",)  # exclude large content blob
                    },
                }
            )
            logger.debug(
                "[_store_evaluation] Quality evaluation persisted: task_id=%s score=%.0f passing=%s",
                task_id,
                assessment.overall_score,
                assessment.passing,
            )
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
