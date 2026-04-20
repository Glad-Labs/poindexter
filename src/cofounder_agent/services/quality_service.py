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
from typing import Any

from services.logger_config import get_logger
from services.quality_models import (  # noqa: F401 — re-exported below
    EvaluationMethod,
    QualityAssessment,
    QualityDimensions,
    QualityScore,
    RefinementType,
)
from services.quality_scorers import (
    check_keywords as _check_keywords_fn,
)
from services.quality_scorers import (
    count_syllables as _count_syllables_fn,
)
from services.quality_scorers import (
    detect_truncation as _detect_truncation_fn,
)
from services.quality_scorers import (
    flesch_kincaid_grade_level as _fk_grade_level_fn,
)
from services.quality_scorers import (
    generate_feedback as _generate_feedback_fn,
)
from services.quality_scorers import (
    generate_suggestions as _generate_suggestions_fn,
)
from services.quality_scorers import (
    qa_cfg as _qa_cfg_fn,
)
from services.quality_scorers import (
    score_accuracy as _score_accuracy_fn,
)
from services.quality_scorers import (
    score_clarity as _score_clarity_fn,
)
from services.quality_scorers import (
    score_completeness as _score_completeness_fn,
)
from services.quality_scorers import (
    score_engagement as _score_engagement_fn,
)
from services.quality_scorers import (
    score_readability as _score_readability_fn,
)
from services.quality_scorers import (
    score_relevance as _score_relevance_fn,
)
from services.quality_scorers import (
    score_seo as _score_seo_fn,
)

logger = get_logger(__name__)


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

        logger.info("UnifiedQualityService initialized")

    @staticmethod
    def _qa_cfg() -> dict:
        """Load all QA pipeline thresholds from DB via site_config.

        Delegates to :func:`quality_scorers.qa_cfg`.
        """
        return _qa_cfg_fn()

    async def evaluate(
        self,
        content: str,
        context: dict[str, Any] | None = None,
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
        logger.info("Evaluating content (%s): %d chars", method.value, len(content))

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
                "Evaluation complete: %.0f/100 (%s)",
                assessment.overall_score, "PASS" if assessment.passing else "FAIL",
            )

            return assessment

        except Exception as e:
            logger.error("[_evaluate] Evaluation failed: %s", e, exc_info=True)
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
        self, content: str, context: dict[str, Any]
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

        # Extract patterns. Keyword presence is factored into _score_seo()
        # directly now (it used to be computed here and silently discarded).
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

    async def _evaluate_llm_based(self, content: str, context: dict[str, Any]) -> QualityAssessment:
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
                "LLM evaluation parsing failed (%s), falling back to pattern-based", e, exc_info=True
            )
            return await self._evaluate_pattern_based(content, context)
        except Exception as e:
            logger.error("[_evaluate_llm_based] LLM call failed: %s", e, exc_info=True)
            return await self._evaluate_pattern_based(content, context)

    async def _evaluate_hybrid(self, content: str, context: dict[str, Any]) -> QualityAssessment:
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
        """Compute the Flesch-Kincaid Grade Level. Delegates to quality_scorers."""
        return _fk_grade_level_fn(text)

    # ========================================================================
    # SCORING METHODS (Pattern-Based Heuristics)
    # ========================================================================

    def _score_clarity(self, content: str, sentence_count: int, word_count: int) -> float:
        """Score clarity. Delegates to quality_scorers."""
        return _score_clarity_fn(content, sentence_count, word_count)

    def _score_accuracy(self, content: str, context: dict[str, Any]) -> float:
        """Score accuracy. Delegates to quality_scorers."""
        return _score_accuracy_fn(content, context)

    @staticmethod
    def detect_truncation(content: str) -> bool:
        """Detect if content was truncated. Delegates to quality_scorers."""
        return _detect_truncation_fn(content)

    def _score_completeness(self, content: str, context: dict[str, Any]) -> float:
        """Score completeness. Delegates to quality_scorers."""
        return _score_completeness_fn(content, context)

    def _score_relevance(self, content: str, context: dict[str, Any]) -> float:
        """Score relevance. Delegates to quality_scorers."""
        return _score_relevance_fn(content, context)

    def _score_seo(self, content: str, context: dict[str, Any]) -> float:
        """Score SEO quality. Delegates to quality_scorers."""
        return _score_seo_fn(content, context)

    def _score_readability(self, content: str) -> float:
        """Score readability. Delegates to quality_scorers."""
        return _score_readability_fn(content)

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
                    issues.append("Generic listicle/guide title pattern")
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
        """Score engagement. Delegates to quality_scorers."""
        return _score_engagement_fn(content)

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    def _check_keywords(self, content: str, context: dict[str, Any]) -> bool:
        """Check if keywords are present in content. Delegates to quality_scorers."""
        return _check_keywords_fn(content, context)

    def _count_syllables(self, word: str) -> int:
        """Estimate syllable count. Delegates to quality_scorers."""
        return _count_syllables_fn(word)

    def _generate_feedback(self, dimensions: QualityDimensions, context: dict[str, Any]) -> str:
        """Generate human-readable feedback. Delegates to quality_scorers."""
        return _generate_feedback_fn(dimensions, context)

    def _generate_suggestions(self, dimensions: QualityDimensions) -> list[str]:
        """Generate improvement suggestions. Delegates to quality_scorers."""
        return _generate_suggestions_fn(dimensions)

    async def _store_evaluation(
        self, assessment: QualityAssessment, context: dict[str, Any]
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
            logger.error("[_store_evaluation] Failed to store evaluation: %s", e, exc_info=True)

    # ========================================================================
    # STATISTICS & REPORTING
    # ========================================================================

    def get_statistics(self) -> dict[str, Any]:
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

# Re-export data models so existing callers don't need to update imports.
__all__ = [
    "EvaluationMethod",
    "QualityAssessment",
    "QualityDimensions",
    "QualityScore",
    "RefinementType",
    "UnifiedQualityService",
    "ContentQualityService",
    "get_quality_service",
    "get_content_quality_service",
]
