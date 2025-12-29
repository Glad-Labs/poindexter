"""
Ollama Content Quality Assessment Tool

Comprehensive evaluation of generated content across multiple dimensions:

Quality Dimensions:
1. COHERENCE - Does the text flow logically?
2. RELEVANCE - Does it address the prompt/topic?
3. COMPLETENESS - Is the content thorough?
4. CLARITY - Is the writing clear and easy to understand?
5. ACCURACY - Are facts presented correctly?
6. STRUCTURE - Is there good organization?
7. ENGAGEMENT - Does it hold attention?
8. GRAMMAR - Are there grammatical errors?

Scoring System: 0-100 for each dimension
Overall Score: Average of all dimensions

Usage:
    assessor = QualityAssessor()
    results = await assessor.assess_content(
        content="Generated text...",
        context={
            "topic": "AI in healthcare",
            "target_audience": "medical professionals",
            "expected_length": "500-1000 words"
        }
    )
"""

import re
import asyncio
import logging
from typing import Dict, List, Any, Optional
from collections import Counter
import json
from datetime import datetime

logger = logging.getLogger(__name__)


class QualityAssessor:
    """Comprehensive content quality assessment"""

    def __init__(self):
        """Initialize quality assessor"""
        self.assessment_history = []

        # Quality thresholds
        self.min_length = 100  # Minimum characters
        self.max_length = 50000  # Maximum characters
        self.min_sentences = 3
        self.max_readability = 18  # Flesch-Kincaid grade level
        self.min_word_variety = 0.5  # Unique words / total words

    async def assess_content(
        self, content: str, context: Optional[Dict[str, Any]] = None, detailed: bool = True
    ) -> Dict[str, Any]:
        """
        Comprehensive content quality assessment

        Args:
            content: Generated content to assess
            context: Optional context (topic, audience, expected_length, etc.)
            detailed: Include detailed analysis

        Returns:
            Dict with:
            - overall_score: 0-100
            - dimension_scores: Dict of individual dimension scores
            - metrics: Raw metrics used for assessment
            - recommendations: List of improvement suggestions
            - assessment_timestamp: When assessment was performed
            - detailed_analysis: Detailed breakdown (if detailed=True)
        """
        logger.info(f"🔍 Assessing content quality ({len(content)} chars)...")

        try:
            # Calculate all dimensions
            scores = {
                "coherence": self._assess_coherence(content),
                "relevance": self._assess_relevance(content, context),
                "completeness": self._assess_completeness(content, context),
                "clarity": self._assess_clarity(content),
                "accuracy": self._assess_accuracy(content),
                "structure": self._assess_structure(content),
                "engagement": self._assess_engagement(content),
                "grammar": self._assess_grammar(content),
            }

            # Calculate overall score
            overall_score = sum(scores.values()) / len(scores)

            # Extract metrics
            metrics = self._extract_metrics(content)

            # Generate recommendations
            recommendations = self._generate_recommendations(scores, metrics, context)

            result = {
                "overall_score": round(overall_score, 1),
                "dimension_scores": {k: round(v, 1) for k, v in scores.items()},
                "metrics": metrics,
                "recommendations": recommendations,
                "assessment_timestamp": datetime.now().isoformat(),
                "pass_quality_check": overall_score >= 70,
                "quality_level": self._score_to_level(overall_score),
            }

            if detailed:
                result["detailed_analysis"] = {
                    "coherence_details": self._detailed_coherence(content),
                    "readability": self._calculate_readability(content),
                    "keyword_analysis": self._analyze_keywords(content, context),
                    "structure_analysis": self._analyze_structure(content),
                    "engagement_analysis": self._analyze_engagement(content),
                }

            # Log summary
            logger.info(
                f"✅ Assessment complete: {result['overall_score']}/100 "
                f"({result['quality_level']}) - "
                f"Pass quality check: {result['pass_quality_check']}"
            )

            # Store in history
            self.assessment_history.append(result)

            return result

        except Exception as e:
            logger.error(f"❌ Assessment error: {e}")
            return {
                "overall_score": 0,
                "error": str(e),
                "assessment_timestamp": datetime.now().isoformat(),
            }

    def _assess_coherence(self, content: str) -> float:
        """
        Assess logical flow and coherence

        Checks:
        - Sentence transitions
        - Logical progression
        - Paragraph connections
        - Topic consistency
        """
        score = 50  # Base score

        sentences = self._get_sentences(content)
        if len(sentences) < 2:
            return 20

        # Check for transition words (good sign of coherence)
        transition_words = [
            "furthermore",
            "however",
            "therefore",
            "moreover",
            "thus",
            "meanwhile",
            "consequently",
            "nevertheless",
            "additionally",
            "similarly",
            "in contrast",
            "on the other hand",
            "as a result",
            "finally",
            "in summary",
            "in conclusion",
            "specifically",
            "generally",
            "for example",
            "such as",
            "notably",
        ]

        text_lower = content.lower()
        transition_count = sum(1 for word in transition_words if word in text_lower)
        score += min(20, transition_count * 2)

        # Check paragraph structure
        paragraphs = [p for p in content.split("\n\n") if p.strip()]
        if len(paragraphs) >= 2:
            score += 10

        # Check for repetitive sentences (negative)
        sentence_lengths = [len(s.split()) for s in sentences]
        if len(set(sentence_lengths)) > len(sentence_lengths) * 0.5:
            score += 10

        return min(100, max(0, score))

    def _assess_relevance(self, content: str, context: Optional[Dict] = None) -> float:
        """
        Assess relevance to topic/prompt

        Checks:
        - Topic keywords present
        - Addresses main points
        - Stays on topic
        """
        score = 60  # Base score

        if not context:
            return score

        topic = context.get("topic", "").lower()
        if topic:
            topic_keywords = set(topic.split())
            content_words = set(content.lower().split())
            keyword_match = len(topic_keywords & content_words)

            # Higher score if many keywords present
            keyword_ratio = keyword_match / max(len(topic_keywords), 1)
            score += int(keyword_ratio * 30)

        # Check if content addresses expected points
        if "expected_points" in context:
            points = context["expected_points"]
            if isinstance(points, list):
                points_addressed = sum(1 for point in points if point.lower() in content.lower())
                score += int((points_addressed / len(points)) * 20) if points else 0

        return min(100, max(0, score))

    def _assess_completeness(self, content: str, context: Optional[Dict] = None) -> float:
        """
        Assess if content is thorough and complete

        Checks:
        - Length appropriate for topic
        - Covers multiple aspects
        - Has introduction and conclusion
        """
        score = 50

        # Check length
        content_length = len(content)
        word_count = len(content.split())

        if context and "expected_length" in context:
            min_len, max_len = self._parse_length_range(context["expected_length"])
            if min_len <= word_count <= max_len:
                score += 25
            elif word_count >= min_len * 0.8:
                score += 15
        else:
            # Default: 300+ words is good
            if word_count >= 300:
                score += 25
            elif word_count >= 100:
                score += 15

        # Check for introduction/conclusion markers
        intro_markers = ["introduction", "overview", "background", "about", "this"]
        conclusion_markers = ["conclusion", "summary", "finally", "in conclusion", "in summary"]

        has_intro = any(marker in content.lower() for marker in intro_markers)
        has_conclusion = any(marker in content.lower() for marker in conclusion_markers)

        if has_intro:
            score += 10
        if has_conclusion:
            score += 15

        # Check for multiple sections/paragraphs
        paragraphs = [p for p in content.split("\n\n") if p.strip()]
        if len(paragraphs) >= 3:
            score += 10

        return min(100, max(0, score))

    def _assess_clarity(self, content: str) -> float:
        """
        Assess clarity and readability

        Checks:
        - Sentence complexity
        - Vocabulary level
        - Jargon usage
        - Passive voice percentage
        """
        score = 60

        sentences = self._get_sentences(content)
        if not sentences:
            return 0

        # Average sentence length (shorter is clearer, but not too short)
        avg_sentence_length = sum(len(s.split()) for s in sentences) / len(sentences)
        if 10 <= avg_sentence_length <= 20:
            score += 20
        elif 5 <= avg_sentence_length <= 30:
            score += 10

        # Check for passive voice (higher is worse for clarity)
        passive_patterns = [r"\b(is|are|was|were|been|being)\s+\w+ed\b"]
        passive_count = sum(
            len(re.findall(pattern, content, re.IGNORECASE)) for pattern in passive_patterns
        )
        passive_ratio = passive_count / max(len(sentences), 1)
        if passive_ratio < 0.2:
            score += 10

        # Check for clear language (avoiding overly complex vocabulary)
        complex_words = self._find_complex_words(content)
        if len(complex_words) / max(len(content.split()), 1) < 0.1:
            score += 10

        return min(100, max(0, score))

    def _assess_accuracy(self, content: str) -> float:
        """
        Assess factual accuracy and confidence

        Checks:
        - Extreme claims
        - Contradictions
        - Unsupported assertions
        """
        score = 75  # Default (hard to verify without external sources)

        # Deduct for extreme language
        extreme_words = [
            "always",
            "never",
            "definitely",
            "absolutely",
            "certainly",
            "undoubtedly",
            "obviously",
            "unquestionably",
        ]
        extreme_count = sum(1 for word in extreme_words if f" {word} " in f" {content.lower()} ")

        if extreme_count > 5:
            score -= 15
        elif extreme_count > 3:
            score -= 10

        # Check for hedging language (good for accuracy)
        hedging_words = [
            "may",
            "might",
            "could",
            "possibly",
            "perhaps",
            "likely",
            "suggests",
            "indicates",
            "appears",
            "seems",
            "often",
            "sometimes",
        ]
        hedging_count = sum(1 for word in hedging_words if f" {word} " in f" {content.lower()} ")

        if hedging_count > 5:
            score += 10

        return min(100, max(0, score))

    def _assess_structure(self, content: str) -> float:
        """
        Assess organizational structure

        Checks:
        - Headings/sections
        - Logical organization
        - Paragraph structure
        - Formatting
        """
        score = 50

        lines = content.split("\n")

        # Check for headings
        heading_count = sum(1 for line in lines if line.strip().startswith("#"))
        if heading_count >= 2:
            score += 20
        elif heading_count >= 1:
            score += 10

        # Check for lists
        list_count = sum(1 for line in lines if line.strip().startswith(("-", "*", "•")))
        if list_count >= 3:
            score += 15

        # Check paragraph structure
        paragraphs = [p for p in content.split("\n\n") if p.strip()]
        if len(paragraphs) >= 3:
            score += 15

        # Check for good paragraph length (not too long, not too short)
        para_lengths = [len(p.split()) for p in paragraphs]
        if all(20 <= len <= 150 for len in para_lengths):
            score += 10

        return min(100, max(0, score))

    def _assess_engagement(self, content: str) -> float:
        """
        Assess reader engagement

        Checks:
        - Interesting language
        - Variety in sentence structure
        - Use of examples
        - Call to action
        """
        score = 50

        # Check for varied sentence starters
        sentences = self._get_sentences(content)
        if sentences:
            starters = set(s.split()[0].lower() for s in sentences if s.split())
            if len(starters) > len(sentences) * 0.6:
                score += 15

        # Check for examples/specific details
        if "example" in content.lower() or "such as" in content.lower():
            score += 10
        if any(marker in content.lower() for marker in ["specifically", "particularly", "notably"]):
            score += 10

        # Check for questions
        question_count = content.count("?")
        if question_count >= 1:
            score += 10

        # Check for call to action
        cta_words = ["explore", "discover", "learn", "try", "experiment", "consider"]
        if any(cta in content.lower() for cta in cta_words):
            score += 10

        return min(100, max(0, score))

    def _assess_grammar(self, content: str) -> float:
        """
        Assess grammatical correctness

        Checks:
        - Common errors
        - Punctuation
        - Subject-verb agreement
        """
        score = 80  # Assume good grammar by default

        # Check for common errors
        common_errors = [
            (r"\btheir\s+is\b", "there/their confusion"),
            (r"\byour\s+(going|coming)\b", "you're confusion"),
            (r"\bits\s+is\b", "it's confusion"),
            (r"\ba\s+\w+\s+by\b", "potential a/an error"),
        ]

        error_count = sum(
            len(re.findall(pattern, content, re.IGNORECASE)) for pattern, _ in common_errors
        )

        score -= error_count * 5

        # Check punctuation balance
        open_parens = content.count("(")
        close_parens = content.count(")")
        if open_parens != close_parens:
            score -= 5

        open_quotes = content.count('"')
        if open_quotes % 2 != 0:
            score -= 3

        return min(100, max(0, score))

    def _extract_metrics(self, content: str) -> Dict[str, Any]:
        """Extract raw metrics from content"""
        words = content.split()
        sentences = self._get_sentences(content)
        paragraphs = [p for p in content.split("\n\n") if p.strip()]

        return {
            "character_count": len(content),
            "word_count": len(words),
            "sentence_count": len(sentences),
            "paragraph_count": len(paragraphs),
            "average_word_length": sum(len(w) for w in words) / max(len(words), 1),
            "average_sentence_length": sum(len(s.split()) for s in sentences)
            / max(len(sentences), 1),
            "average_paragraph_length": sum(len(p.split()) for p in paragraphs)
            / max(len(paragraphs), 1),
            "unique_word_count": len(set(w.lower() for w in words)),
            "word_variety_ratio": len(set(w.lower() for w in words)) / max(len(words), 1),
        }

    def _generate_recommendations(
        self, scores: Dict[str, float], metrics: Dict[str, Any], context: Optional[Dict] = None
    ) -> List[str]:
        """Generate specific improvement recommendations"""
        recommendations = []

        # Coherence recommendations
        if scores["coherence"] < 70:
            recommendations.append(
                "🔗 Improve coherence: Add transition words between sentences "
                "(e.g., 'Furthermore', 'However', 'As a result')"
            )

        # Relevance recommendations
        if scores["relevance"] < 70:
            recommendations.append(
                "🎯 Increase relevance: Ensure all content directly addresses the main topic. "
                "Consider focusing on the key points."
            )

        # Completeness recommendations
        if scores["completeness"] < 70:
            if metrics["word_count"] < 300:
                recommendations.append(
                    "📝 Add more detail: Expand the content to be more thorough. "
                    f"Current: {metrics['word_count']} words, Target: 300+ words"
                )
            else:
                recommendations.append(
                    "✅ Structure: Add clear introduction, body, and conclusion sections"
                )

        # Clarity recommendations
        if scores["clarity"] < 70:
            recommendations.append(
                "📖 Improve clarity: Use shorter sentences and simpler vocabulary. "
                f"Current average sentence length: {metrics['average_sentence_length']:.0f} words"
            )

        # Structure recommendations
        if scores["structure"] < 70:
            recommendations.append(
                "🏗️ Improve structure: Add headings and organize content into clear sections"
            )

        # Engagement recommendations
        if scores["engagement"] < 70:
            recommendations.append(
                "✨ Increase engagement: Add examples, specific details, or questions to "
                "capture reader attention"
            )

        # Grammar recommendations
        if scores["grammar"] < 85:
            recommendations.append(
                "✏️ Proofread: Review for grammatical errors and punctuation mistakes"
            )

        return recommendations

    def _score_to_level(self, score: float) -> str:
        """Convert numeric score to quality level"""
        if score >= 90:
            return "Excellent"
        elif score >= 80:
            return "Very Good"
        elif score >= 70:
            return "Good"
        elif score >= 60:
            return "Fair"
        elif score >= 50:
            return "Needs Improvement"
        else:
            return "Poor"

    def _get_sentences(self, content: str) -> List[str]:
        """Extract sentences from content"""
        sentences = re.split(r"[.!?]+", content)
        return [s.strip() for s in sentences if s.strip()]

    def _calculate_readability(self, content: str) -> Dict[str, float]:
        """Calculate readability scores"""
        sentences = self._get_sentences(content)
        words = content.split()

        if not sentences or not words:
            return {}

        avg_sentence_length = len(words) / len(sentences)
        avg_word_length = sum(len(w) for w in words) / len(words)

        # Flesch-Kincaid grade level
        flesch_kincaid = (
            0.39 * avg_sentence_length
            + 11.8 * (sum(1 for w in words if len(w) > 2) / len(words))
            - 15.59
        )

        return {
            "flesch_kincaid_grade": round(max(0, flesch_kincaid), 1),
            "avg_sentence_length": round(avg_sentence_length, 1),
            "avg_word_length": round(avg_word_length, 1),
        }

    def _find_complex_words(self, content: str) -> List[str]:
        """Find complex words (3+ syllables)"""
        words = content.split()
        return [w for w in words if self._count_syllables(w) >= 3]

    def _count_syllables(self, word: str) -> int:
        """Estimate syllable count"""
        word = word.lower()
        count = 0
        vowels = "aeiou"
        prev_was_vowel = False

        for char in word:
            is_vowel = char in vowels
            if is_vowel and not prev_was_vowel:
                count += 1
            prev_was_vowel = is_vowel

        if word.endswith("e"):
            count -= 1
        if word.endswith("le") and len(word) > 2 and word[-3] not in vowels:
            count += 1

        return max(1, count)

    def _detailed_coherence(self, content: str) -> Dict[str, Any]:
        """Detailed coherence analysis"""
        sentences = self._get_sentences(content)
        return {
            "sentence_count": len(sentences),
            "transitions_present": any(
                word in content.lower()
                for word in ["furthermore", "however", "therefore", "moreover"]
            ),
            "avg_sentence_length": (
                round(sum(len(s.split()) for s in sentences) / max(len(sentences), 1), 1)
                if sentences
                else 0
            ),
        }

    def _analyze_keywords(self, content: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Analyze keyword usage and density"""
        words = content.lower().split()
        word_freq = Counter(words)

        # Remove common words
        common = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of"}
        filtered_freq = {w: count for w, count in word_freq.items() if w not in common}
        top_keywords = dict(sorted(filtered_freq.items(), key=lambda x: x[1], reverse=True)[:10])

        return {
            "top_keywords": top_keywords,
            "unique_words": len(set(words)),
            "total_words": len(words),
            "keyword_diversity": len(set(words)) / max(len(words), 1),
        }

    def _analyze_structure(self, content: str) -> Dict[str, Any]:
        """Analyze content structure"""
        lines = content.split("\n")
        paragraphs = [p for p in content.split("\n\n") if p.strip()]

        heading_count = sum(1 for line in lines if line.strip().startswith("#"))
        list_count = sum(1 for line in lines if line.strip().startswith(("-", "*", "•")))

        return {
            "headings": heading_count,
            "lists": list_count,
            "paragraphs": len(paragraphs),
            "avg_para_length": (
                round(sum(len(p.split()) for p in paragraphs) / max(len(paragraphs), 1), 1)
                if paragraphs
                else 0
            ),
        }

    def _analyze_engagement(self, content: str) -> Dict[str, Any]:
        """Analyze engagement elements"""
        return {
            "questions": content.count("?"),
            "exclamations": content.count("!"),
            "examples_mentioned": "example" in content.lower() or "such as" in content.lower(),
            "has_cta": any(
                cta in content.lower() for cta in ["explore", "discover", "learn", "try"]
            ),
        }

    def _parse_length_range(self, length_spec: str) -> tuple:
        """Parse expected length specification"""
        # Handle formats like "500-1000 words", "500 words", "1000+"
        import re

        numbers = re.findall(r"\d+", length_spec)
        if len(numbers) >= 2:
            return int(numbers[0]), int(numbers[1])
        elif len(numbers) == 1:
            return int(numbers[0]), int(numbers[0]) * 2
        else:
            return 500, 1000  # Default


def generate_quality_report(assessment: Dict[str, Any]) -> str:
    """Generate a readable quality report"""
    report = "\n" + "=" * 80 + "\n"
    report += "📊 CONTENT QUALITY ASSESSMENT REPORT\n"
    report += "=" * 80 + "\n\n"

    # Overall score
    report += "🎯 OVERALL ASSESSMENT\n"
    report += f"{'─' * 80}\n"
    report += f"Score: {assessment['overall_score']}/100\n"
    report += f"Level: {assessment.get('quality_level', 'Unknown')}\n"
    report += (
        f"Pass Quality Check: {'✅ Yes' if assessment.get('pass_quality_check') else '❌ No'}\n"
    )
    report += "\n"

    # Dimension scores
    if "dimension_scores" in assessment:
        report += "📈 DIMENSION SCORES\n"
        report += f"{'─' * 80}\n"
        scores = assessment["dimension_scores"]
        for dimension, score in scores.items():
            bar_length = int(score / 5)
            bar = "█" * bar_length + "░" * (20 - bar_length)
            report += f"{dimension:<15} {bar} {score:>6.1f}/100\n"
        report += "\n"

    # Metrics
    if "metrics" in assessment:
        report += "📋 CONTENT METRICS\n"
        report += f"{'─' * 80}\n"
        metrics = assessment["metrics"]
        report += f"Word Count: {metrics.get('word_count', 'N/A')}\n"
        report += f"Sentence Count: {metrics.get('sentence_count', 'N/A')}\n"
        report += f"Paragraph Count: {metrics.get('paragraph_count', 'N/A')}\n"
        report += f"Avg Sentence Length: {metrics.get('average_sentence_length', 'N/A')} words\n"
        report += f"Word Variety: {metrics.get('word_variety_ratio', 'N/A'):.1%}\n"
        report += "\n"

    # Recommendations
    if "recommendations" in assessment and assessment["recommendations"]:
        report += "💡 RECOMMENDATIONS\n"
        report += f"{'─' * 80}\n"
        for i, rec in enumerate(assessment["recommendations"], 1):
            report += f"{i}. {rec}\n"
        report += "\n"

    report += "=" * 80 + "\n"
    return report


async def main():
    """Example of using the quality assessor"""
    assessor = QualityAssessor()

    sample_content = """
    Artificial Intelligence (AI) is transforming how we work and live. 
    Machine learning, a subset of AI, enables computers to learn from data 
    without explicit programming.
    
    There are several key applications of AI today. In healthcare, AI helps 
    diagnose diseases more accurately. In finance, algorithms detect fraud 
    automatically. In transportation, autonomous vehicles promise safer roads.
    
    The future of AI looks promising. However, we must address ethical concerns 
    about bias and privacy. By working together, we can ensure AI benefits 
    everyone equally.
    """

    context = {
        "topic": "artificial intelligence applications",
        "target_audience": "business professionals",
        "expected_length": "400-600 words",
    }

    result = await assessor.assess_content(sample_content, context=context)
    print(generate_quality_report(result))
