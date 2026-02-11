"""
Content Critique Loop Service - WRAPPER/BACKWARD COMPATIBILITY LAYER

⚠️ DEPRECATED: This service is now a thin wrapper around UnifiedQualityService
to maintain backward compatibility with TaskExecutor and other legacy code paths.

The actual quality evaluation logic is centralized in quality_service.py.

For new code, use UnifiedQualityService directly:
    from services.quality_service import UnifiedQualityService
    service = UnifiedQualityService()
    result = await service.evaluate(content, context)
"""

import logging
from typing import Any, Dict, Optional

from .quality_service import UnifiedQualityService

logger = logging.getLogger(__name__)


class ContentCritiqueLoop:
    """
    BACKWARD COMPATIBILITY WRAPPER around UnifiedQualityService.
    
    This class exists solely to maintain compatibility with existing code
    that imports/uses ContentCritiqueLoop (e.g., TaskExecutor).
    
    All actual quality evaluation is delegated to UnifiedQualityService.
    """

    def __init__(self, model_router=None):
        """
        Initialize content critique loop wrapper
        
        Args:
            model_router: Optional ModelRouter (kept for backward compatibility, not used)
        
        Note: model_router parameter is ignored in favor of UnifiedQualityService integration
        """
        # Initialize the actual quality service
        self.quality_service = UnifiedQualityService()
        
        # Keep stats for backward compatibility
        self.critique_count = 0
        self.approval_count = 0
        self.rejection_count = 0
        
        logger.info("ContentCritiqueLoop initialized as wrapper around UnifiedQualityService")

    async def critique(
        self, content: str, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Critique generated content and provide feedback.
        
        DELEGATES to UnifiedQualityService.evaluate() for backward compatibility.

        Args:
            content: Generated content to critique
            context: Optional context (topic, audience, keywords, etc.)

        Returns:
            Dict with:
            - approved: bool (passing from quality service)
            - quality_score: 0-100 (overall_score from quality service)
            - feedback: str (feedback from quality service)
            - suggestions: list (suggestions from quality service)
            - needs_refinement: bool (True if not passing)
        """
        self.critique_count += 1

        # Handle None content
        if not content:
            return {
                "approved": False,
                "quality_score": 0,
                "feedback": "No content provided for critique",
                "suggestions": ["Content is empty or None"],
                "needs_refinement": True,
            }

        logger.debug(f"🔍 Critiquing content ({len(content)} chars) via UnifiedQualityService")

        try:
            # Delegate to UnifiedQualityService
            quality_result = await self.quality_service.evaluate(
                content=content,
                context=context,
                use_llm=True,  # Use LLM-based evaluation
            )

            # Map QualityScore to ContentCritiqueLoop format for backward compatibility
            result = {
                "approved": quality_result.passing,
                "quality_score": int(quality_result.overall_score),
                "feedback": quality_result.feedback,
                "suggestions": quality_result.suggestions,
                "needs_refinement": not quality_result.passing,
                "metrics": {
                    "clarity": quality_result.clarity,
                    "accuracy": quality_result.accuracy,
                    "completeness": quality_result.completeness,
                    "relevance": quality_result.relevance,
                    "seo_quality": quality_result.seo_quality,
                    "readability": quality_result.readability,
                    "engagement": quality_result.engagement,
                    "source": "quality_service",
                },
            }

            # Update stats
            if result["approved"]:
                self.approval_count += 1
                logger.info(f"✅ Content approved (score: {result['quality_score']}/100)")
            else:
                self.rejection_count += 1
                logger.warning(
                    f"⚠️ Content needs improvement (score: {result['quality_score']}/100)"
                )

            return result

        except Exception as e:
            logger.error(f"❌ Critique error: {e}", exc_info=True)
            return {
                "approved": False,
                "quality_score": 0,
                "feedback": f"Critique failed: {str(e)}",
                "suggestions": ["Review the error and try again"],
                "needs_refinement": True,
                "error": str(e),
            }


        if metrics.get("quality_score", 100) < 75:
            suggestions.append("Review for grammar, spelling, and clarity")

        return suggestions if suggestions else ["Content is ready for publication"]

    def get_stats(self) -> Dict[str, Any]:
        """Get critique statistics"""
        total = self.critique_count
        approval_rate = (self.approval_count / total * 100) if total > 0 else 0

        return {
            "total_critiques": total,
            "approved": self.approval_count,
            "rejected": self.rejection_count,
            "approval_rate": f"{approval_rate:.1f}%",
        }
