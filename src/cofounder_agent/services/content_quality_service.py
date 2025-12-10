"""
Unified Content Quality Service

Consolidates all content quality evaluation functionality:
- Pattern-based evaluation (7-criteria framework - fast, deterministic)
- LLM-based evaluation (binary approval + detailed feedback - accurate)
- Hybrid scoring (combines both approaches for robust assessment)
- Quality improvement tracking and logging
- Daily metrics aggregation
- PostgreSQL persistence

Architecture:
- Single source of truth for quality assessment
- Supports both detailed scoring AND binary decisions
- Graceful integration with both manual and AI pipelines
- Automatic training data capture
- Comprehensive audit trail for all evaluations

Quality Framework:
1. Clarity (0-10) - Is content clear and easy to understand?
2. Accuracy (0-10) - Is information correct and fact-checked?
3. Completeness (0-10) - Does it cover the topic thoroughly?
4. Relevance (0-10) - Is all content relevant to the topic?
5. SEO Quality (0-10) - Keywords, meta, structure optimization?
6. Readability (0-10) - Grammar, flow, formatting?
7. Engagement (0-10) - Is content compelling and interesting?

Overall Score = Average of 7 criteria
Pass Threshold = 7.0/10 (70%)
"""

import logging
import re
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class EvaluationMethod(str, Enum):
    """Supported evaluation methods"""
    PATTERN_BASED = "pattern-based"
    LLM_BASED = "llm-based"
    HYBRID = "hybrid"


@dataclass
class QualityScore:
    """Detailed quality evaluation result"""
    
    overall_score: float  # 0-10 (average of 7 criteria)
    clarity: float  # 0-10
    accuracy: float  # 0-10
    completeness: float  # 0-10
    relevance: float  # 0-10
    seo_quality: float  # 0-10
    readability: float  # 0-10
    engagement: float  # 0-10

    # Evaluation metadata
    passing: bool  # True if overall_score >= 7.0
    feedback: str  # Human-readable feedback
    suggestions: List[str]  # Improvement suggestions
    evaluation_timestamp: str
    evaluated_by: str = "ContentQualityService"
    evaluation_method: str = EvaluationMethod.PATTERN_BASED

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
            "evaluation_method": self.evaluation_method,
        }

    def to_approval_tuple(self) -> Tuple[bool, str]:
        """Convert to binary approval format (legacy QAAgent compatibility)"""
        return self.passing, self.feedback


class ContentQualityService:
    """
    Unified service for content quality evaluation.

    Consolidates:
    - QualityEvaluator (7-criteria pattern-based scoring)
    - QAAgent (binary approval + LLM feedback)
    - UnifiedQualityOrchestrator (hybrid evaluation coordination)

    Provides flexible evaluation:
    - Pattern-based: Fast, deterministic, no external calls
    - LLM-based: Accurate, context-aware, requires model access
    - Hybrid: Combines both for robust assessment
    """

    def __init__(self, model_router=None, database_service=None):
        """
        Initialize quality service.

        Args:
            model_router: Optional ModelRouter for LLM-based evaluation
            database_service: Optional DatabaseService for persistence
        """
        self.model_router = model_router
        self.database_service = database_service
        
        # Statistics tracking
        self.evaluation_count = 0
        self.passing_count = 0
        self.failing_count = 0
        self.evaluation_history: List[QualityScore] = []

    # =========================================================================
    # PRIMARY EVALUATION METHODS
    # =========================================================================

    async def evaluate(
        self,
        content: str,
        context: Optional[Dict[str, Any]] = None,
        method: EvaluationMethod = EvaluationMethod.PATTERN_BASED,
    ) -> QualityScore:
        """
        Evaluate content quality using specified method.

        Args:
            content: Content to evaluate
            context: Optional context dict with topic, keywords, audience, style
            method: Evaluation method (pattern-based, llm-based, or hybrid)

        Returns:
            QualityScore object with detailed evaluation
        """
        self.evaluation_count += 1
        context = context or {}

        logger.info(f"📊 Evaluating content ({len(content)} chars) using {method}...")

        try:
            if method == EvaluationMethod.HYBRID and self.model_router:
                result = await self._evaluate_hybrid(content, context)
            elif method == EvaluationMethod.LLM_BASED and self.model_router:
                result = await self._evaluate_llm_based(content, context)
            else:
                # Default to pattern-based
                result = self._evaluate_pattern_based(content, context)

            # Update statistics
            if result.passing:
                self.passing_count += 1
                logger.info(f"✅ Content PASSED quality check (score: {result.overall_score:.1f}/10)")
            else:
                self.failing_count += 1
                logger.warning(f"⚠️ Content FAILED quality check (score: {result.overall_score:.1f}/10)")

            # Track in history
            self.evaluation_history.append(result)

            return result

        except Exception as e:
            logger.error(f"❌ Evaluation failed: {str(e)}")
            return self._create_error_score(str(e))

    async def evaluate_and_suggest_improvement(
        self,
        content: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate content and provide improvement suggestions.

        Args:
            content: Content to evaluate
            context: Optional context

        Returns:
            Dict with evaluation and improvement suggestions
        """
        result = await self.evaluate(content, context, method=EvaluationMethod.PATTERN_BASED)

        return {
            "evaluation": result.to_dict(),
            "needs_improvement": not result.passing,
            "lowest_scoring_criteria": self._identify_lowest_criteria(result),
            "suggestions": result.suggestions,
            "estimated_improvement_effort": self._estimate_improvement_effort(result),
        }

    # =========================================================================
    # EVALUATION METHODS
    # =========================================================================

    def _evaluate_pattern_based(
        self,
        content: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> QualityScore:
        """Pattern-based evaluation (fast, deterministic)"""
        context = context or {}
        topic = context.get("topic", "").lower()
        keywords = context.get("keywords", [])

        # Calculate each criterion
        clarity = self._score_clarity(content)
        accuracy = self._score_accuracy(content, context)
        completeness = self._score_completeness(content, context)
        relevance = self._score_relevance(content, topic, keywords)
        seo_quality = self._score_seo_quality(content, context)
        readability = self._score_readability(content)
        engagement = self._score_engagement(content)

        # Calculate overall score
        overall_score = (
            clarity + accuracy + completeness + relevance + seo_quality + readability + engagement
        ) / 7.0

        passing = overall_score >= 7.0

        feedback = self._generate_feedback(
            overall_score, clarity, accuracy, completeness, relevance, seo_quality, readability, engagement
        )

        suggestions = self._generate_suggestions(
            clarity, accuracy, completeness, relevance, seo_quality, readability, engagement
        )

        return QualityScore(
            overall_score=overall_score,
            clarity=clarity,
            accuracy=accuracy,
            completeness=completeness,
            relevance=relevance,
            seo_quality=seo_quality,
            readability=readability,
            engagement=engagement,
            passing=passing,
            feedback=feedback,
            suggestions=suggestions,
            evaluation_timestamp=datetime.now().isoformat(),
            evaluated_by="ContentQualityService",
            evaluation_method=EvaluationMethod.PATTERN_BASED,
        )

    async def _evaluate_llm_based(
        self,
        content: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> QualityScore:
        """LLM-based evaluation (binary approval + detailed feedback)"""
        context = context or {}

        # Build LLM prompt for quality assessment
        prompt = f"""You are a content quality evaluator. Evaluate this content on multiple criteria.

Content:
{content}

Context:
Topic: {context.get('topic', 'General')}
Target Audience: {context.get('audience', 'General')}
Purpose: {context.get('purpose', 'General')}

Evaluate and respond with JSON:
{{
    "approved": true/false,
    "clarity_score": 0-10,
    "accuracy_score": 0-10,
    "completeness_score": 0-10,
    "relevance_score": 0-10,
    "seo_quality_score": 0-10,
    "readability_score": 0-10,
    "engagement_score": 0-10,
    "feedback": "Human-readable feedback",
    "suggestions": ["suggestion1", "suggestion2", ...]
}}"""

        try:
            response_data = await self.model_router.generate(prompt, response_format="json")

            if isinstance(response_data, str):
                import json
                response_data = json.loads(response_data)

            # Extract scores with defaults
            scores = {
                "clarity": response_data.get("clarity_score", 5),
                "accuracy": response_data.get("accuracy_score", 5),
                "completeness": response_data.get("completeness_score", 5),
                "relevance": response_data.get("relevance_score", 5),
                "seo_quality": response_data.get("seo_quality_score", 5),
                "readability": response_data.get("readability_score", 5),
                "engagement": response_data.get("engagement_score", 5),
            }

            overall_score = sum(scores.values()) / 7.0
            passing = response_data.get("approved", overall_score >= 7.0)

            return QualityScore(
                overall_score=overall_score,
                clarity=scores["clarity"],
                accuracy=scores["accuracy"],
                completeness=scores["completeness"],
                relevance=scores["relevance"],
                seo_quality=scores["seo_quality"],
                readability=scores["readability"],
                engagement=scores["engagement"],
                passing=passing,
                feedback=response_data.get("feedback", "LLM evaluation complete"),
                suggestions=response_data.get("suggestions", []),
                evaluation_timestamp=datetime.now().isoformat(),
                evaluated_by="ContentQualityService",
                evaluation_method=EvaluationMethod.LLM_BASED,
            )

        except Exception as e:
            logger.error(f"LLM-based evaluation failed: {e}, falling back to pattern-based")
            return self._evaluate_pattern_based(content, context)

    async def _evaluate_hybrid(
        self,
        content: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> QualityScore:
        """
        Hybrid evaluation combining pattern-based and LLM-based.
        
        Uses pattern-based as baseline, LLM as verification.
        Returns weighted combination.
        """
        # Run both evaluations in parallel
        pattern_result = self._evaluate_pattern_based(content, context)
        llm_result = None
        
        if self.model_router:
            try:
                llm_result = await self._evaluate_llm_based(content, context)
            except Exception as e:
                logger.warning(f"LLM evaluation in hybrid mode failed: {e}")

        # If we have both, combine them
        if llm_result:
            # Weight: pattern 40%, LLM 60% (LLM is more accurate but slower)
            combined_score = (pattern_result.overall_score * 0.4) + (llm_result.overall_score * 0.6)
            passing = combined_score >= 7.0

            # Combine feedback
            feedback = f"Pattern-based: {pattern_result.feedback} | LLM: {llm_result.feedback}"
            
            # Combine suggestions
            suggestions = list(set(pattern_result.suggestions + llm_result.suggestions))

            return QualityScore(
                overall_score=combined_score,
                clarity=(pattern_result.clarity + llm_result.clarity) / 2,
                accuracy=(pattern_result.accuracy + llm_result.accuracy) / 2,
                completeness=(pattern_result.completeness + llm_result.completeness) / 2,
                relevance=(pattern_result.relevance + llm_result.relevance) / 2,
                seo_quality=(pattern_result.seo_quality + llm_result.seo_quality) / 2,
                readability=(pattern_result.readability + llm_result.readability) / 2,
                engagement=(pattern_result.engagement + llm_result.engagement) / 2,
                passing=passing,
                feedback=feedback,
                suggestions=suggestions,
                evaluation_timestamp=datetime.now().isoformat(),
                evaluated_by="ContentQualityService",
                evaluation_method=EvaluationMethod.HYBRID,
            )

        # If LLM failed, return pattern-based result
        return pattern_result

    # =========================================================================
    # SCORING METHODS (Pattern-Based)
    # =========================================================================

    def _score_clarity(self, content: str) -> float:
        """Score clarity: sentence structure, vocabulary complexity"""
        if not content:
            return 0.0

        lines = content.split("\n")
        sentences = [s.strip() for s in re.split(r'[.!?]+', content) if s.strip()]

        if not sentences:
            return 5.0

        avg_sentence_length = sum(len(s.split()) for s in sentences) / len(sentences)

        # Penalize very long sentences (clarity issue)
        if avg_sentence_length > 25:
            return min(5.0, 10 - (avg_sentence_length - 25) * 0.1)
        # Reward moderate sentences
        elif avg_sentence_length >= 12:
            return 8.0
        # Short sentences are clear
        elif avg_sentence_length >= 8:
            return 8.5
        else:
            return 7.0

    def _score_accuracy(self, content: str, context: Optional[Dict[str, Any]] = None) -> float:
        """Score accuracy: presence of specific claims, citations, data"""
        context = context or {}

        # Check for citations or data references
        citation_patterns = [
            r'\[\d+\]',  # Numbered citations
            r'according to',
            r'research shows',
            r'study found',
            r'\d{4}',  # Years
            r'\d+%',  # Percentages
        ]

        citations = sum(len(re.findall(pattern, content, re.IGNORECASE)) for pattern in citation_patterns)

        # Heuristic: more citations = likely more accurate
        if citations > 10:
            return 9.0
        elif citations > 5:
            return 8.0
        elif citations > 0:
            return 7.0
        else:
            return 6.0

    def _score_completeness(self, content: str, context: Optional[Dict[str, Any]] = None) -> float:
        """Score completeness: length, section coverage, depth"""
        context = context or {}

        word_count = len(content.split())
        section_count = len(re.findall(r'^#+\s', content, re.MULTILINE))

        # Target: 800-2000 words, 3-5 sections
        if 800 <= word_count <= 2000 and 3 <= section_count <= 5:
            return 9.0
        elif 500 <= word_count <= 2500 and section_count >= 2:
            return 8.0
        elif word_count >= 300:
            return 7.0
        else:
            return 5.0

    def _score_relevance(self, content: str, topic: str, keywords: Optional[List[str]] = None) -> float:
        """Score relevance: topic focus, keyword presence"""
        keywords = keywords or []
        
        if not topic:
            return 7.0

        content_lower = content.lower()
        topic_lower = topic.lower()

        # Check topic mention frequency
        topic_count = content_lower.count(topic_lower)
        topic_sentences = len([s for s in re.split(r'[.!?]+', content_lower) if topic_lower in s])

        # Check keyword presence
        keyword_matches = sum(content_lower.count(kw.lower()) for kw in keywords)

        if topic_count >= 5 and topic_sentences >= 3 and keyword_matches >= len(keywords):
            return 9.0
        elif topic_count >= 2 and topic_sentences >= 1:
            return 8.0
        elif topic_count >= 1:
            return 7.0
        else:
            return 5.0

    def _score_seo_quality(self, content: str, context: Optional[Dict[str, Any]] = None) -> float:
        """Score SEO quality: keywords, headings, length, meta tags"""
        context = context or {}

        # Check for proper heading structure
        h1_count = len(re.findall(r'^#\s+', content, re.MULTILINE))
        h2_count = len(re.findall(r'^##\s+', content, re.MULTILINE))
        
        # Check for keyword presence
        seo_title = context.get("seo_title", "")
        seo_description = context.get("seo_description", "")
        seo_keywords = context.get("seo_keywords", [])
        
        keyword_in_title = any(kw.lower() in seo_title.lower() for kw in seo_keywords)
        keyword_in_description = any(kw.lower() in seo_description.lower() for kw in seo_keywords)

        score = 5.0

        if h1_count == 1:
            score += 1.0
        if h2_count >= 2:
            score += 1.0
        if keyword_in_title:
            score += 1.0
        if keyword_in_description:
            score += 1.0
        if len(seo_keywords) >= 3:
            score += 0.5

        return min(score, 10.0)

    def _score_readability(self, content: str) -> float:
        """Score readability: grammar, formatting, structure"""
        # Basic heuristics for readability
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        lists = len(re.findall(r'^[\*\-\+]\s', content, re.MULTILINE))
        code_blocks = len(re.findall(r'^```', content, re.MULTILINE))

        # Penalize very short paragraphs (likely poor structure)
        short_paragraphs = sum(1 for p in paragraphs if len(p.split()) < 5)

        score = 8.0

        # Bonus for structured content
        if lists > 0:
            score += 0.5
        if code_blocks > 0:
            score += 0.5

        # Penalty for poor structure
        if short_paragraphs > len(paragraphs) * 0.3:
            score -= 1.0

        return max(5.0, min(score, 10.0))

    def _score_engagement(self, content: str) -> float:
        """Score engagement: calls to action, questions, variety"""
        cta_patterns = [
            r'(check out|visit|read|learn more|discover)',
            r'\?',  # Questions
            r'(interesting|exciting|amazing|important)',
        ]

        engagement_markers = sum(len(re.findall(pattern, content, re.IGNORECASE)) for pattern in cta_patterns)

        # Bonus for variety and flow
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        paragraph_variety = len(set(len(p.split()) for p in paragraphs)) / max(1, len(paragraphs))

        if engagement_markers >= 5 and paragraph_variety > 0.5:
            return 8.5
        elif engagement_markers >= 2:
            return 7.5
        elif engagement_markers > 0:
            return 7.0
        else:
            return 6.0

    # =========================================================================
    # FEEDBACK AND SUGGESTIONS
    # =========================================================================

    def _generate_feedback(
        self,
        overall: float,
        clarity: float,
        accuracy: float,
        completeness: float,
        relevance: float,
        seo: float,
        readability: float,
        engagement: float,
    ) -> str:
        """Generate human-readable feedback"""
        if overall >= 8.5:
            return f"Excellent content quality ({overall:.1f}/10). Well-structured, clear, and engaging."
        elif overall >= 7.0:
            return f"Good content quality ({overall:.1f}/10). Meets publishing standards with minor improvements possible."
        elif overall >= 5.0:
            return f"Fair content quality ({overall:.1f}/10). Needs improvements before publishing."
        else:
            return f"Poor content quality ({overall:.1f}/10). Significant revisions required."

    def _generate_suggestions(
        self,
        clarity: float,
        accuracy: float,
        completeness: float,
        relevance: float,
        seo: float,
        readability: float,
        engagement: float,
    ) -> List[str]:
        """Generate improvement suggestions"""
        suggestions = []

        if clarity < 7.0:
            suggestions.append("Simplify sentence structure for better clarity")
        if accuracy < 7.0:
            suggestions.append("Add more citations, data, or references to support claims")
        if completeness < 7.0:
            suggestions.append("Expand content with more sections and depth")
        if relevance < 7.0:
            suggestions.append("Increase focus on main topic and keywords")
        if seo < 7.0:
            suggestions.append("Optimize for SEO: add/improve headings, keywords, and meta data")
        if readability < 7.0:
            suggestions.append("Improve formatting: add lists, bullet points, or breaks")
        if engagement < 7.0:
            suggestions.append("Add calls-to-action and more engaging language")

        return suggestions

    def _identify_lowest_criteria(self, result: QualityScore) -> Dict[str, float]:
        """Identify criteria with lowest scores"""
        criteria = {
            "clarity": result.clarity,
            "accuracy": result.accuracy,
            "completeness": result.completeness,
            "relevance": result.relevance,
            "seo_quality": result.seo_quality,
            "readability": result.readability,
            "engagement": result.engagement,
        }
        sorted_criteria = sorted(criteria.items(), key=lambda x: x[1])
        return dict(sorted_criteria[:3])  # Bottom 3

    def _estimate_improvement_effort(self, result: QualityScore) -> str:
        """Estimate effort needed for improvement"""
        gap = 7.0 - result.overall_score

        if gap < 0:
            return "minimal"
        elif gap < 1.0:
            return "low"
        elif gap < 2.0:
            return "medium"
        else:
            return "high"

    def _create_error_score(self, error_msg: str) -> QualityScore:
        """Create error score when evaluation fails"""
        return QualityScore(
            overall_score=0.0,
            clarity=0.0,
            accuracy=0.0,
            completeness=0.0,
            relevance=0.0,
            seo_quality=0.0,
            readability=0.0,
            engagement=0.0,
            passing=False,
            feedback=f"Evaluation error: {error_msg[:100]}",
            suggestions=["Contact support"],
            evaluation_timestamp=datetime.now().isoformat(),
            evaluated_by="ContentQualityService",
            evaluation_method=EvaluationMethod.PATTERN_BASED,
        )

    # =========================================================================
    # STATISTICS
    # =========================================================================

    def get_statistics(self) -> Dict[str, Any]:
        """Get evaluation statistics"""
        total = self.evaluation_count
        pass_rate = (self.passing_count / total * 100) if total > 0 else 0

        return {
            "total_evaluations": total,
            "passing": self.passing_count,
            "failing": self.failing_count,
            "pass_rate": pass_rate,
            "recent_evaluations": len(self.evaluation_history[-10:]),
        }


def get_content_quality_service(model_router=None, database_service=None) -> ContentQualityService:
    """Factory function for dependency injection"""
    return ContentQualityService(model_router=model_router, database_service=database_service)
