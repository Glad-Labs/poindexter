"""
Phase 3.4: RAG Service for Style-Aware Sample Retrieval

Implements Retrieval-Augmented Generation (RAG) for writing samples.
Enables semantic similarity search to find relevant samples by:
- Topic similarity
- Style match
- Tone compatibility
- Characteristic alignment

This service enhances content generation by automatically selecting the most
relevant writing samples based on the task topic and user preferences.
"""

import logging
from typing import Optional, Dict, Any, List, Tuple
import json
import re
from collections import Counter
import math

from services.database_service import DatabaseService
from services.writing_style_integration import WritingStyleIntegrationService

logger = logging.getLogger(__name__)


class WritingSampleRAGService:
    """RAG service for semantic similarity-based sample retrieval"""

    def __init__(self, database_service: DatabaseService):
        """
        Initialize RAG service.

        Args:
            database_service: Database service instance
        """
        self.db = database_service
        self.integration_svc = WritingStyleIntegrationService(database_service)

    async def retrieve_relevant_samples(
        self,
        user_id: str,
        query_topic: str,
        preferred_style: Optional[str] = None,
        preferred_tone: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant writing samples using semantic similarity.

        Args:
            user_id: User ID to filter samples
            query_topic: Topic to search for (e.g., "AI in Healthcare")
            preferred_style: Optional preferred style filter
            preferred_tone: Optional preferred tone filter
            limit: Maximum number of samples to return

        Returns:
            List of relevant samples sorted by relevance score
        """
        try:
            # Get all user's samples
            samples = await self.db.writing_style.get_user_writing_samples(user_id)

            if not samples:
                logger.info(f"No writing samples found for user {user_id}")
                return []

            # Score each sample for relevance
            scored_samples = []

            for sample in samples:
                sample_id = sample.get("id")
                sample_text = sample.get("content", "")
                sample_title = sample.get("title", "")

                # Get sample analysis
                sample_data = await self.integration_svc.get_sample_for_content_generation(
                    writing_style_id=sample_id
                )

                if not sample_data:
                    continue

                analysis = sample_data.get("analysis", {})

                # Calculate relevance score
                score = self._calculate_relevance_score(
                    sample_text=sample_text,
                    sample_title=sample_title,
                    sample_analysis=analysis,
                    query_topic=query_topic,
                    preferred_style=preferred_style,
                    preferred_tone=preferred_tone,
                )

                scored_samples.append(
                    {
                        "sample": sample,
                        "sample_data": sample_data,
                        "analysis": analysis,
                        "relevance_score": score,
                    }
                )

            # Sort by relevance score (highest first)
            scored_samples.sort(key=lambda x: x["relevance_score"], reverse=True)

            # Log top results
            if scored_samples:
                top = scored_samples[0]
                logger.info(
                    f"✅ RAG: Top sample: {top['sample'].get('title')} "
                    f"(score: {top['relevance_score']:.2f})"
                )

            # Return top N samples with full data
            return [
                {
                    **s["sample"],
                    "relevance_score": s["relevance_score"],
                    "sample_data": s["sample_data"],
                    "analysis": s["analysis"],
                }
                for s in scored_samples[:limit]
            ]

        except Exception as e:
            logger.error(f"Error retrieving relevant samples: {e}")
            return []

    async def retrieve_by_style_match(
        self, user_id: str, target_style: str, limit: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Retrieve samples matching a specific style.

        Args:
            user_id: User ID
            target_style: Target style (technical, narrative, listicle, etc.)
            limit: Max samples to return

        Returns:
            List of samples matching the style
        """
        try:
            samples = await self.db.writing_style.get_user_writing_samples(user_id)

            matched_samples = []

            for sample in samples:
                sample_id = sample.get("id")
                sample_data = await self.integration_svc.get_sample_for_content_generation(
                    writing_style_id=sample_id
                )

                if not sample_data:
                    continue

                analysis = sample_data.get("analysis", {})
                detected_style = analysis.get("detected_style")

                # Check if style matches
                if detected_style and detected_style.lower() == target_style.lower():
                    matched_samples.append(
                        {**sample, "analysis": analysis, "sample_data": sample_data}
                    )

            logger.info(f"Found {len(matched_samples)} samples matching style: {target_style}")
            return matched_samples[:limit]

        except Exception as e:
            logger.error(f"Error retrieving by style: {e}")
            return []

    async def retrieve_by_tone_match(
        self, user_id: str, target_tone: str, limit: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Retrieve samples matching a specific tone.

        Args:
            user_id: User ID
            target_tone: Target tone (formal, casual, authoritative, conversational)
            limit: Max samples to return

        Returns:
            List of samples matching the tone
        """
        try:
            samples = await self.db.writing_style.get_user_writing_samples(user_id)

            matched_samples = []

            for sample in samples:
                sample_id = sample.get("id")
                sample_data = await self.integration_svc.get_sample_for_content_generation(
                    writing_style_id=sample_id
                )

                if not sample_data:
                    continue

                analysis = sample_data.get("analysis", {})
                detected_tone = analysis.get("detected_tone")

                # Check if tone matches
                if detected_tone and detected_tone.lower() == target_tone.lower():
                    matched_samples.append(
                        {**sample, "analysis": analysis, "sample_data": sample_data}
                    )

            logger.info(f"Found {len(matched_samples)} samples matching tone: {target_tone}")
            return matched_samples[:limit]

        except Exception as e:
            logger.error(f"Error retrieving by tone: {e}")
            return []

    def _calculate_relevance_score(
        self,
        sample_text: str,
        sample_title: str,
        sample_analysis: Dict[str, Any],
        query_topic: str,
        preferred_style: Optional[str] = None,
        preferred_tone: Optional[str] = None,
    ) -> float:
        """
        Calculate relevance score for a sample.

        Scoring factors:
        - Topic similarity (keyword overlap): 40%
        - Style match (if specified): 30%
        - Tone match (if specified): 20%
        - Quality metrics (length, diversity): 10%

        Args:
            sample_text: The sample content
            sample_title: The sample title
            sample_analysis: Analysis results for the sample
            query_topic: The query topic
            preferred_style: Optional preferred style
            preferred_tone: Optional preferred tone

        Returns:
            Relevance score (0-100)
        """
        score = 0.0

        # 1. Topic Similarity (40%)
        topic_similarity = self._calculate_topic_similarity(query_topic, sample_text, sample_title)
        score += topic_similarity * 0.40

        # 2. Style Match (30%)
        if preferred_style:
            detected_style = sample_analysis.get("detected_style", "")
            style_match = 1.0 if detected_style.lower() == preferred_style.lower() else 0.5
            score += style_match * 0.30
        else:
            # No preference, but reward having a detectable style
            if sample_analysis.get("detected_style"):
                score += 0.5 * 0.30

        # 3. Tone Match (20%)
        if preferred_tone:
            detected_tone = sample_analysis.get("detected_tone", "")
            tone_match = 1.0 if detected_tone.lower() == preferred_tone.lower() else 0.5
            score += tone_match * 0.20
        else:
            # No preference, but reward having a detectable tone
            if sample_analysis.get("detected_tone"):
                score += 0.5 * 0.20

        # 4. Quality Metrics (10%)
        quality_score = self._calculate_quality_score(sample_analysis)
        score += quality_score * 0.10

        return min(100.0, max(0.0, score))

    def _calculate_topic_similarity(
        self, query_topic: str, sample_text: str, sample_title: str
    ) -> float:
        """
        Calculate topic similarity using keyword overlap.

        Args:
            query_topic: Query topic (e.g., "AI in Healthcare")
            sample_text: Sample content
            sample_title: Sample title

        Returns:
            Similarity score (0-1)
        """
        # Extract keywords from query
        query_words = set(re.findall(r"\b\w{4,}\b", query_topic.lower()))

        # Extract keywords from sample title and text
        sample_words = set(re.findall(r"\b\w{4,}\b", (sample_title + " " + sample_text).lower()))

        if not query_words or not sample_words:
            return 0.0

        # Calculate Jaccard similarity
        intersection = query_words & sample_words
        union = query_words | sample_words

        jaccard = len(intersection) / len(union) if union else 0.0

        return jaccard

    def _calculate_quality_score(self, analysis: Dict[str, Any]) -> float:
        """
        Calculate quality score based on analysis metrics.

        Factors:
        - Vocabulary diversity (higher is better): 50%
        - Reasonable sentence length (15-25 words): 30%
        - Has structural elements (lists, headings): 20%

        Args:
            analysis: Sample analysis results

        Returns:
            Quality score (0-1)
        """
        score = 0.0

        # Vocabulary diversity (0-1, ideal > 0.6)
        vocab_diversity = analysis.get("vocabulary_diversity", 0.5)
        # Normalize to 0-1 with ideal around 0.7-0.8
        diversity_score = min(1.0, vocab_diversity / 0.8)
        score += diversity_score * 0.50

        # Sentence length (ideal 15-25 words)
        avg_sentence_length = analysis.get("avg_sentence_length", 15)
        # Calculate how close to ideal range
        if 15 <= avg_sentence_length <= 25:
            length_score = 1.0
        else:
            # Penalize sentences that are too short or too long
            distance = abs(avg_sentence_length - 20)
            length_score = max(0.0, 1.0 - (distance / 20.0))
        score += length_score * 0.30

        # Structural elements
        characteristics = analysis.get("style_characteristics", {})
        element_count = sum(1 for v in characteristics.values() if v)
        element_score = min(1.0, element_count / 3)  # 3+ elements = perfect
        score += element_score * 0.20

        return score

    async def get_rag_context(
        self,
        user_id: str,
        query_topic: str,
        preferred_style: Optional[str] = None,
        preferred_tone: Optional[str] = None,
        num_samples: int = 3,
    ) -> Dict[str, Any]:
        """
        Get RAG context for content generation.

        Returns formatted context with top relevant samples that can be
        included in the LLM prompt.

        Args:
            user_id: User ID
            query_topic: Content generation topic
            preferred_style: Optional style preference
            preferred_tone: Optional tone preference
            num_samples: Number of samples to include

        Returns:
            Dict with RAG context ready for LLM prompt injection
        """
        try:
            # Retrieve relevant samples
            relevant_samples = await self.retrieve_relevant_samples(
                user_id=user_id,
                query_topic=query_topic,
                preferred_style=preferred_style,
                preferred_tone=preferred_tone,
                limit=num_samples,
            )

            if not relevant_samples:
                logger.info(f"No relevant samples found for RAG context")
                return {"has_context": False, "samples": []}

            # Format for LLM prompt
            rag_context = {
                "has_context": True,
                "num_samples": len(relevant_samples),
                "samples": [
                    {
                        "title": s.get("title"),
                        "relevance_score": round(s.get("relevance_score", 0), 2),
                        "style": s.get("analysis", {}).get("detected_style"),
                        "tone": s.get("analysis", {}).get("detected_tone"),
                        "excerpt": self._create_sample_excerpt(s.get("content", "")),
                    }
                    for s in relevant_samples
                ],
                "prompt_injection": self._format_rag_prompt(relevant_samples),
            }

            logger.info(f"✅ RAG context created with {len(relevant_samples)} samples")
            return rag_context

        except Exception as e:
            logger.error(f"Error getting RAG context: {e}")
            return {"has_context": False, "samples": [], "error": str(e)}

    @staticmethod
    def _create_sample_excerpt(content: str, length: int = 200) -> str:
        """
        Create excerpt from sample content.

        Args:
            content: Full sample content
            length: Max length of excerpt

        Returns:
            Excerpt text
        """
        if len(content) <= length:
            return content.strip()

        # Take first length characters and trim to sentence boundary
        excerpt = content[:length]
        last_period = excerpt.rfind(".")

        if last_period > length - 50:  # Period within reasonable distance
            return excerpt[: last_period + 1].strip()

        return excerpt.strip() + "..."

    @staticmethod
    def _format_rag_prompt(relevant_samples: List[Dict[str, Any]]) -> str:
        """
        Format relevant samples for inclusion in LLM prompt.

        Args:
            relevant_samples: List of relevant samples with scores

        Returns:
            Formatted prompt text for RAG injection
        """
        if not relevant_samples:
            return ""

        prompt = "\n## Reference Writing Samples (Retrieved via RAG)\n\n"
        prompt += "Below are the most relevant writing samples from your collection. "
        prompt += "Use these as reference for style, tone, and structure:\n\n"

        for i, sample in enumerate(relevant_samples, 1):
            relevance = sample.get("relevance_score", 0)
            title = sample.get("title", "Untitled")
            style = sample.get("analysis", {}).get("detected_style", "general")
            tone = sample.get("analysis", {}).get("detected_tone", "neutral")

            prompt += f"### Sample {i}: {title}\n"
            prompt += f"**Relevance: {relevance:.0f}% | Style: {style} | Tone: {tone}**\n"
            prompt += f"```\n{sample.get('content', '')[:500]}\n```\n\n"

        return prompt.strip()


class RAGRetrievalResult:
    """Result of RAG retrieval operation"""

    def __init__(
        self,
        samples: List[Dict[str, Any]],
        scores: List[float],
        query_topic: str,
        retrieval_time_ms: float,
    ):
        """
        Initialize retrieval result.

        Args:
            samples: Retrieved samples
            scores: Relevance scores
            query_topic: Original query topic
            retrieval_time_ms: Time taken for retrieval (ms)
        """
        self.samples = samples
        self.scores = scores
        self.query_topic = query_topic
        self.retrieval_time_ms = retrieval_time_ms
        self.num_results = len(samples)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "query_topic": self.query_topic,
            "num_results": self.num_results,
            "retrieval_time_ms": self.retrieval_time_ms,
            "samples": [
                {
                    "title": s.get("title"),
                    "score": scores[i] if i < len(self.scores) else 0,
                    "style": s.get("analysis", {}).get("detected_style"),
                }
                for i, s in enumerate(self.samples)
            ],
        }
