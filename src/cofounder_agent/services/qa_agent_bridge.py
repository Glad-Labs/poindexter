"""
QA Agent Bridge - Integration Layer

Bridges the existing QA Agent with the new Quality Evaluation System.
Enables both LLM-based QA (from qa_agent.py) and pattern-based evaluation
to work together for comprehensive content review.

This module provides:
1. Conversion between QAAgent format and QualityScore format
2. Combined evaluation using both approaches
3. Weighted scoring to balance human and pattern-based evaluation
4. Feedback synthesis from multiple evaluation sources
"""

import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class HybridQualityResult:
    """Combined evaluation result from QA Agent + Pattern-based evaluation"""
    overall_score: float                    # 0-10 final score
    qa_agent_approved: bool                 # QA Agent pass/fail
    qa_agent_feedback: str                  # QA Agent specific feedback
    pattern_based_scores: Dict[str, float]  # 7-criterion pattern scores
    pattern_based_overall: float            # Pattern-based overall (0-10)
    hybrid_overall: float                   # Weighted combination
    passing: bool                           # Final pass/fail (>= 7.0)
    synthesis_feedback: str                 # Combined feedback
    recommendations: list                   # Consolidated suggestions
    evaluation_method: str                  # "hybrid" | "qa_only" | "pattern_only"
    timestamp: datetime
    qa_weight: float = 0.4                  # Weight for QA evaluation (40%)
    pattern_weight: float = 0.6             # Weight for pattern eval (60%)


class QAAgentBridge:
    """
    Bridge between existing QA Agent and new Quality Evaluation System.
    
    Provides:
    - Conversion from QA Agent output to QualityScore format
    - Hybrid evaluation combining both approaches
    - Weighted scoring for balanced assessment
    - Synthesis of feedback from multiple sources
    
    Usage:
        bridge = QAAgentBridge()
        
        # Use existing QA Agent
        qa_approved, qa_feedback = qa_agent.run(post, content)
        
        # Bridge it to new evaluation system
        hybrid_result = await bridge.create_hybrid_evaluation(
            content=content,
            qa_approved=qa_approved,
            qa_feedback=qa_feedback,
            pattern_scores=pattern_evaluation_result
        )
    """
    
    def __init__(self):
        logger.info("Initializing QAAgentBridge (QA Agent ↔ Quality Evaluator)")
        self.qa_weight = 0.4      # 40% weight for QA Agent
        self.pattern_weight = 0.6  # 60% weight for pattern-based
    
    def qa_to_quality_score(
        self,
        qa_approved: bool,
        qa_feedback: str,
        content: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Convert QA Agent output to QualityScore-compatible format.
        
        Args:
            qa_approved: Boolean from QA Agent (True = approved, False = needs revision)
            qa_feedback: Feedback string from QA Agent
            content: The content that was evaluated
            context: Optional context (topic, keywords, etc.)
        
        Returns:
            Dict compatible with QualityScore for storage
        """
        # Map QA Agent approval to overall score
        # QA Agent is often strict, so we give it 0-3 point range
        # True (approved) = 8.0, False (not approved) = 5.0
        qa_score = 8.0 if qa_approved else 5.0
        
        return {
            "source": "qa_agent",
            "qa_approved": qa_approved,
            "qa_feedback": qa_feedback,
            "qa_score": qa_score,
            "context": context or {},
            "timestamp": datetime.now().isoformat()
        }
    
    async def create_hybrid_evaluation(
        self,
        content: str,
        qa_approved: bool,
        qa_feedback: str,
        pattern_scores: Dict[str, float],
        pattern_overall: float,
        context: Optional[Dict[str, Any]] = None,
        qa_weight: Optional[float] = None,
        pattern_weight: Optional[float] = None
    ) -> HybridQualityResult:
        """
        Create a hybrid evaluation combining QA Agent and pattern-based scoring.
        
        This enables:
        - Expert (QA Agent) + Data-driven (patterns) evaluation
        - Balancing human expertise with objective metrics
        - More robust quality assessment
        
        Args:
            content: The content being evaluated
            qa_approved: QA Agent approval (boolean)
            qa_feedback: QA Agent feedback (string)
            pattern_scores: Dict with 7 criterion scores {"clarity": 7.5, ...}
            pattern_overall: Overall pattern-based score (0-10)
            context: Optional context (topic, keywords)
            qa_weight: Override default QA weight (default: 0.4)
            pattern_weight: Override default pattern weight (default: 0.6)
        
        Returns:
            HybridQualityResult with combined evaluation
        """
        # Use provided weights or defaults
        qa_w = qa_weight if qa_weight is not None else self.qa_weight
        p_w = pattern_weight if pattern_weight is not None else self.pattern_weight
        
        # Normalize weights
        total_weight = qa_w + p_w
        qa_w = qa_w / total_weight
        p_w = p_w / total_weight
        
        try:
            # Calculate QA Agent score (0-10 scale)
            qa_score = 8.0 if qa_approved else 5.0
            
            # Calculate hybrid overall score
            hybrid_overall = (qa_score * qa_w) + (pattern_overall * p_w)
            
            # Determine final passing status
            passing = hybrid_overall >= 7.0
            
            # Synthesize feedback
            synthesis_feedback = self._synthesize_feedback(
                qa_approved=qa_approved,
                qa_feedback=qa_feedback,
                pattern_scores=pattern_scores,
                pattern_overall=pattern_overall,
                hybrid_overall=hybrid_overall
            )
            
            # Generate recommendations
            recommendations = self._generate_recommendations(
                qa_approved=qa_approved,
                qa_feedback=qa_feedback,
                pattern_scores=pattern_scores,
                passing=passing
            )
            
            result = HybridQualityResult(
                overall_score=hybrid_overall,
                qa_agent_approved=qa_approved,
                qa_agent_feedback=qa_feedback,
                pattern_based_scores=pattern_scores,
                pattern_based_overall=pattern_overall,
                hybrid_overall=hybrid_overall,
                passing=passing,
                synthesis_feedback=synthesis_feedback,
                recommendations=recommendations,
                evaluation_method="hybrid",
                timestamp=datetime.now(),
                qa_weight=qa_w,
                pattern_weight=p_w
            )
            
            logger.info(
                f"Created hybrid evaluation: QA={qa_score}/10, "
                f"Pattern={pattern_overall}/10, Hybrid={hybrid_overall}/10, "
                f"Passing={passing}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Error creating hybrid evaluation: {e}")
            raise
    
    def _synthesize_feedback(
        self,
        qa_approved: bool,
        qa_feedback: str,
        pattern_scores: Dict[str, float],
        pattern_overall: float,
        hybrid_overall: float
    ) -> str:
        """
        Synthesize feedback from both evaluation sources.
        
        Creates a narrative combining:
        - QA Agent's expert feedback
        - Pattern-based scoring insights
        - Overall assessment
        """
        parts = []
        
        # Overall assessment
        if hybrid_overall >= 8.5:
            parts.append("Excellent quality content with strong performance across all dimensions.")
        elif hybrid_overall >= 7.5:
            parts.append("Good quality content meeting all requirements.")
        elif hybrid_overall >= 7.0:
            parts.append("Acceptable quality content with minor improvements possible.")
        elif hybrid_overall >= 6.0:
            parts.append("Content needs refinement before publication.")
        else:
            parts.append("Content requires significant improvements.")
        
        # QA Agent feedback
        if qa_approved:
            parts.append(f"✅ QA Agent approval: {qa_feedback}")
        else:
            parts.append(f"⚠️ QA Agent feedback: {qa_feedback}")
        
        # Pattern-based insights
        weak_criteria = [k for k, v in pattern_scores.items() if v < 7.0]
        if weak_criteria:
            parts.append(
                f"Pattern analysis shows room for improvement in: "
                f"{', '.join(weak_criteria)}"
            )
        
        strong_criteria = [k for k, v in pattern_scores.items() if v >= 8.5]
        if strong_criteria:
            parts.append(
                f"Excellent performance in: {', '.join(strong_criteria)}"
            )
        
        return " ".join(parts)
    
    def _generate_recommendations(
        self,
        qa_approved: bool,
        qa_feedback: str,
        pattern_scores: Dict[str, float],
        passing: bool
    ) -> list:
        """
        Generate actionable recommendations from both evaluation sources.
        
        Returns:
            List of up to 5 specific recommendations
        """
        recommendations = []
        
        # Extract recommendations from QA feedback if present
        if not qa_approved and qa_feedback:
            # QA feedback often contains specific issues
            if "clarity" in qa_feedback.lower():
                recommendations.append("Improve content clarity and simplify complex ideas")
            if "structure" in qa_feedback.lower():
                recommendations.append("Review and improve content structure and organization")
            if "keyword" in qa_feedback.lower() or "seo" in qa_feedback.lower():
                recommendations.append("Enhance SEO optimization and keyword placement")
            if "length" in qa_feedback.lower():
                recommendations.append("Adjust content length and depth")
        
        # Extract recommendations from pattern-based scores
        for criterion, score in pattern_scores.items():
            if score < 7.0 and criterion not in str(recommendations):
                if criterion == "clarity":
                    recommendations.append("Simplify sentences and improve readability")
                elif criterion == "accuracy":
                    recommendations.append("Add citations and verify all facts")
                elif criterion == "completeness":
                    recommendations.append("Expand content with more examples and details")
                elif criterion == "relevance":
                    recommendations.append("Strengthen topic relevance and keyword focus")
                elif criterion == "seo_quality":
                    recommendations.append("Optimize for search engines (headers, keywords)")
                elif criterion == "readability":
                    recommendations.append("Improve formatting and paragraph structure")
                elif criterion == "engagement":
                    recommendations.append("Add questions, examples, or calls-to-action")
        
        # If passing but room for improvement
        if passing and pattern_scores:
            avg_score = sum(pattern_scores.values()) / len(pattern_scores)
            if avg_score < 8.0:
                recommendations.append(
                    f"Further refinement could elevate from {avg_score:.1f} to 8.5+"
                )
        
        # Limit to 5 recommendations
        return recommendations[:5]
    
    def to_quality_score_format(self, hybrid_result: HybridQualityResult) -> Dict[str, Any]:
        """
        Convert HybridQualityResult to QualityScore-compatible format for storage.
        
        This enables the hybrid evaluation to be stored in the same database
        as pattern-based evaluations while preserving source information.
        
        Args:
            hybrid_result: HybridQualityResult object
        
        Returns:
            Dict compatible with QualityScore persistence layer
        """
        return {
            "overall_score": hybrid_result.hybrid_overall,
            "clarity": hybrid_result.pattern_based_scores.get("clarity", 0),
            "accuracy": hybrid_result.pattern_based_scores.get("accuracy", 0),
            "completeness": hybrid_result.pattern_based_scores.get("completeness", 0),
            "relevance": hybrid_result.pattern_based_scores.get("relevance", 0),
            "seo_quality": hybrid_result.pattern_based_scores.get("seo_quality", 0),
            "readability": hybrid_result.pattern_based_scores.get("readability", 0),
            "engagement": hybrid_result.pattern_based_scores.get("engagement", 0),
            "passing": hybrid_result.passing,
            "feedback": hybrid_result.synthesis_feedback,
            "suggestions": hybrid_result.recommendations,
            "evaluation_method": "hybrid_qa_pattern",
            "evaluation_timestamp": hybrid_result.timestamp,
            "metadata": {
                "qa_agent_approved": hybrid_result.qa_agent_approved,
                "qa_agent_feedback": hybrid_result.qa_agent_feedback,
                "qa_weight": hybrid_result.qa_weight,
                "pattern_weight": hybrid_result.pattern_weight,
                "qa_score": 8.0 if hybrid_result.qa_agent_approved else 5.0,
                "pattern_overall": hybrid_result.pattern_based_overall,
                "hybrid_overall": hybrid_result.hybrid_overall
            }
        }


# Dependency injection function (replaces singleton pattern)
def get_qa_agent_bridge() -> QAAgentBridge:
    """
    Factory function for QAAgentBridge dependency injection.
    
    Replaces singleton pattern with FastAPI Depends() for:
    - Testability: Can inject mocks/test instances
    - Thread safety: No global state
    - Flexibility: Fresh instances for each request
    
    Usage in route:
        @router.post("/endpoint")
        async def handler(bridge = Depends(get_qa_agent_bridge)):
            return await bridge.evaluate(...)
    
    Returns:
        QAAgentBridge instance
    """
    return QAAgentBridge()
