"""
Blog Quality Evaluation Agent - Bridge agent for workflow system

Wraps quality_service to be callable as a workflow phase.

This agent:
1. Takes workflow inputs (content, topic, evaluation_method)
2. Calls quality_service.evaluate()
3. Returns results compatible with workflow executor
"""

from typing import Any

from services.logger_config import get_logger
from services.quality_service import EvaluationMethod, get_quality_service

logger = get_logger(__name__)


class BlogQualityAgent:
    """
    Agent that evaluates blog post quality using unified quality service.

    Callable as a workflow phase with inputs:
    - content: str (required) - Blog content to evaluate
    - topic: str (optional) - Blog topic for context
    - evaluation_method: str (optional) - "pattern-based", "llm-based", or "hybrid"
    - store_result: bool (optional) - Whether to store in database (default: True)
    """

    def __init__(self, database_service=None):
        """
        Initialize blog quality evaluation agent

        Args:
            database_service: Optional DatabaseService for persistence
        """
        logger.info("Initializing BlogQualityAgent")
        self.quality_service = get_quality_service(database_service=database_service)

    async def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        Evaluate blog post quality.

        Args:
            inputs: Dict with keys:
                - content (required): str, blog content to evaluate
                - topic (optional): str, blog topic for context
                - evaluation_method (optional): str, evaluation method
                - store_result (optional): bool, whether to store result

        Returns:
            Dict with keys:
                - overall_score: float, 0-100 quality score
                - clarity: float, clarity dimension score
                - accuracy: float, accuracy dimension score
                - completeness: float, completeness dimension score
                - relevance: float, relevance dimension score
                - seo_quality: float, SEO quality dimension score
                - readability: float, readability dimension score
                - engagement: float, engagement dimension score
                - passing: bool, True if score >= 70
                - feedback: str, human-readable feedback
                - suggestions: list[str], improvement suggestions
                - evaluation_method: str, method used
                - status: str, "success" or "failed"
                - error: str (if failed)
        """

        try:
            logger.info(
                f"[BlogQualityAgent] Evaluating content: {len(inputs.get('content', ''))} chars"
            )

            content = inputs.get("content")
            if not content or len(content.strip()) < 10:
                raise ValueError("Content must be at least 10 characters")

            # Extract parameters with defaults
            topic = inputs.get("topic", "")
            evaluation_method_str = inputs.get("evaluation_method", "pattern-based")
            store_result = inputs.get("store_result", True)

            # Convert string method to enum
            try:
                evaluation_method = EvaluationMethod(evaluation_method_str)
            except ValueError:
                logger.warning(
                    f"Unknown evaluation method '{evaluation_method_str}', using pattern-based",
                    exc_info=True,
                )
                evaluation_method = EvaluationMethod.PATTERN_BASED

            # Build context for evaluation
            context = {"topic": topic}

            # Call quality service
            assessment = await self.quality_service.evaluate(
                content=content,
                context=context,
                method=evaluation_method,
                store_result=store_result,
            )

            logger.info(
                f"[BlogQualityAgent] Evaluation complete: {assessment.overall_score:.0f}/100 "
                f"({'PASS' if assessment.passing else 'FAIL'})"
            )

            return {
                "overall_score": assessment.overall_score,
                "clarity": assessment.dimensions.clarity,
                "accuracy": assessment.dimensions.accuracy,
                "completeness": assessment.dimensions.completeness,
                "relevance": assessment.dimensions.relevance,
                "seo_quality": assessment.dimensions.seo_quality,
                "readability": assessment.dimensions.readability,
                "engagement": assessment.dimensions.engagement,
                "passing": assessment.passing,
                "feedback": assessment.feedback,
                "suggestions": assessment.suggestions,
                "evaluation_method": assessment.evaluation_method.value,
                "status": "success",
            }

        except Exception as e:
            logger.error(f"[BlogQualityAgent] Error: {e!s}", exc_info=True)
            return {
                "status": "failed",
                "error": str(e),
                "overall_score": 0.0,
            }


def get_blog_quality_agent(database_service=None) -> BlogQualityAgent:
    """Factory function for BlogQualityAgent"""
    return BlogQualityAgent(database_service=database_service)
