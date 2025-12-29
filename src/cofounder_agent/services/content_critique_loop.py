"""
Content Critique Loop Service

Validates generated content and provides feedback for refinement.
Ensures all published content meets quality standards before posting to Strapi.
"""

import asyncio
import logging
from typing import Optional, Dict, Any
import json

logger = logging.getLogger(__name__)


class ContentCritiqueLoop:
    """Validates and critiques generated content"""

    def __init__(self, model_router=None):
        """
        Initialize content critique loop

        Args:
            model_router: Optional ModelRouter for LLM calls (fallback to mock if None)
        """
        self.model_router = model_router
        self.critique_count = 0
        self.approval_count = 0
        self.rejection_count = 0

    async def critique(
        self, content: str, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Critique generated content and provide feedback

        Args:
            content: Generated content to critique
            context: Optional context (topic, audience, keywords, etc.)

        Returns:
            Dict with:
            - approved: bool
            - quality_score: 0-100
            - feedback: str (specific feedback)
            - suggestions: list (improvement suggestions)
            - needs_refinement: bool
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

        logger.debug(f"🔍 Critiquing content ({len(content)} chars)")

        try:
            # Extract key metrics
            metrics = self._calculate_metrics(content, context)

            # Generate critique
            critique_result = {
                "approved": metrics["quality_score"] >= 75,
                "quality_score": metrics["quality_score"],
                "feedback": self._generate_feedback(metrics),
                "suggestions": self._generate_suggestions(metrics),
                "needs_refinement": metrics["quality_score"] < 85,
                "metrics": {
                    "word_count": metrics["word_count"],
                    "readability_score": metrics["readability_score"],
                    "has_structure": metrics["has_structure"],
                    "has_keywords": metrics.get("has_keywords", False),
                    "content_length": len(content),
                },
            }

            if critique_result["approved"]:
                self.approval_count += 1
                logger.info(f"✅ Content approved (score: {critique_result['quality_score']}/100)")
            else:
                self.rejection_count += 1
                logger.warning(
                    f"⚠️ Content needs improvement (score: {critique_result['quality_score']}/100)"
                )

            return critique_result

        except Exception as e:
            logger.error(f"❌ Critique error: {e}")
            return {
                "approved": False,
                "quality_score": 0,
                "feedback": f"Critique failed: {str(e)}",
                "suggestions": ["Review the error and try again"],
                "needs_refinement": True,
                "error": str(e),
            }

    def _calculate_metrics(
        self, content: str, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Calculate content quality metrics"""
        word_count = len(content.split())
        lines = content.split("\n")
        has_structure = len([l for l in lines if l.strip().startswith("#")]) > 0

        # Basic readability: longer paragraphs are harder to read
        paragraphs = [p for p in content.split("\n\n") if p.strip()]
        avg_para_length = word_count / max(len(paragraphs), 1)
        readability_score = max(0, min(100, 100 - (avg_para_length / 150) * 30))

        # Check for keywords if context provided
        has_keywords = False
        if context and "keywords" in context:
            keywords = context.get("keywords", [])
            if isinstance(keywords, str):
                keywords = [keywords]
            content_lower = content.lower()
            has_keywords = any(kw.lower() in content_lower for kw in keywords)

        # Quality score calculation
        quality_score = 50  # Base score

        # Add points for structure
        if has_structure:
            quality_score += 15
        else:
            quality_score += 5  # Some structure without headings

        # Add points for readability
        quality_score += min(20, readability_score / 5)

        # Add points for length
        if word_count >= 300:
            quality_score += 15
        elif word_count >= 150:
            quality_score += 10
        else:
            quality_score += 5

        # Add points for keywords
        if has_keywords:
            quality_score += 10

        # Add points for punctuation/polish
        if content.count(".") > word_count / 50:  # Reasonable sentence count
            quality_score += 10

        # Cap at 100
        quality_score = min(100, quality_score)

        return {
            "word_count": word_count,
            "has_structure": has_structure,
            "readability_score": readability_score,
            "has_keywords": has_keywords,
            "paragraph_count": len(paragraphs),
            "quality_score": int(quality_score),
        }

    def _generate_feedback(self, metrics: Dict[str, Any]) -> str:
        """Generate specific feedback based on metrics"""
        if not metrics:
            return "Unable to generate feedback"

        feedback_parts = []

        if metrics.get("quality_score", 50) >= 90:
            feedback_parts.append("Excellent content quality")
        elif metrics.get("quality_score", 50) >= 80:
            feedback_parts.append("Good content quality")
        elif metrics.get("quality_score", 50) >= 70:
            feedback_parts.append("Acceptable content with room for improvement")
        else:
            feedback_parts.append("Content needs significant improvement")

        if metrics.get("word_count", 0) < 150:
            feedback_parts.append("Consider expanding content for more depth")
        elif metrics.get("word_count", 0) > 3000:
            feedback_parts.append("Consider breaking into multiple posts")
        else:
            feedback_parts.append("Good word count for publication")

        if not metrics.get("has_structure", False):
            feedback_parts.append("Add headings to improve structure and readability")

        if metrics.get("readability_score", 100) < 60:
            feedback_parts.append("Break up long paragraphs for better readability")

        if not metrics.get("has_keywords", False):
            feedback_parts.append("Include target keywords naturally in content")

        return ". ".join(feedback_parts) if feedback_parts else "Content is ready for publication"

    def _generate_suggestions(self, metrics: Dict[str, Any]) -> list:
        """Generate specific improvement suggestions"""
        if not metrics:
            return ["Content is ready for publication"]

        suggestions = []

        if metrics.get("word_count", 0) < 200:
            suggestions.append("Expand content to at least 200-300 words for better SEO")

        if not metrics.get("has_structure", False):
            suggestions.append("Add H2/H3 headings to organize main points")

        if metrics.get("readability_score", 100) < 70:
            suggestions.append("Shorten paragraphs (max 3-4 sentences)")

        if metrics.get("paragraph_count", 0) < 3:
            suggestions.append("Add more paragraphs to improve content flow")

        if not metrics.get("has_keywords", False):
            suggestions.append("Incorporate 2-3 target keywords naturally")

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
