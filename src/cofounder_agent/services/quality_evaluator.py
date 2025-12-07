"""
Quality Evaluator Service

Comprehensive content quality assessment using 7-criteria evaluation framework.
Provides detailed scoring, feedback, and automatic refinement recommendations.

7-Criteria Evaluation:
1. Clarity (0-10) - Is content clear and easy to understand?
2. Accuracy (0-10) - Is information correct and fact-checked?
3. Completeness (0-10) - Does it cover the topic thoroughly?
4. Relevance (0-10) - Is all content relevant to the topic?
5. SEO Quality (0-10) - Keywords, meta, structure optimization?
6. Readability (0-10) - Grammar, flow, formatting?
7. Engagement (0-10) - Is content compelling and interesting?

Overall Score = Average of 7 criteria (0-10 scale)
Pass Threshold = 7.0 (70%)
"""

import logging
import json
import re
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


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
    
    # Feedback
    passing: bool  # True if overall_score >= 7.0
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


class QualityEvaluator:
    """
    Comprehensive content quality evaluator using 7-criteria framework.
    
    Provides:
    - Multi-criteria scoring (clarity, accuracy, completeness, etc.)
    - Detailed feedback and improvement suggestions
    - Passing/failing determination (threshold: 7.0/10)
    - Automatic refinement recommendations
    """

    def __init__(self, model_router=None):
        """
        Initialize quality evaluator
        
        Args:
            model_router: Optional ModelRouter for LLM-based evaluation
                         If None, uses pattern-based evaluation
        """
        self.model_router = model_router
        self.evaluation_count = 0
        self.passing_count = 0
        self.failing_count = 0
        
        # Evaluation history for trending
        self.evaluation_history: List[QualityScore] = []
    
    async def evaluate(
        self,
        content: str,
        context: Optional[Dict[str, Any]] = None,
        use_llm: bool = False
    ) -> QualityScore:
        """
        Evaluate content quality using 7-criteria framework
        
        Args:
            content: Content to evaluate
            context: Optional context dict with:
                - topic: str (topic of content)
                - target_keywords: list[str] (SEO keywords)
                - audience: str (target audience)
                - style: str (content style)
            use_llm: If True and model_router available, use LLM for evaluation
            
        Returns:
            QualityScore object with detailed evaluation
        """
        self.evaluation_count += 1
        
        logger.info(f"📊 Evaluating content ({len(content)} chars)...")
        
        try:
            # Use LLM-based evaluation if available and requested
            if use_llm and self.model_router:
                result = await self._evaluate_with_llm(content, context)
            else:
                # Use pattern-based evaluation (fast, deterministic)
                result = self._evaluate_with_patterns(content, context)
            
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
            # Return minimal score on error
            return self._create_error_score(str(e))
    
    def _evaluate_with_patterns(
        self,
        content: str,
        context: Optional[Dict[str, Any]] = None
    ) -> QualityScore:
        """Pattern-based evaluation (fast, deterministic)"""
        
        context = context or {}
        topic = context.get("topic", "").lower()
        keywords = context.get("target_keywords", [])
        
        # Calculate each criterion
        clarity = self._score_clarity(content)
        accuracy = self._score_accuracy(content, context)
        completeness = self._score_completeness(content, context)
        relevance = self._score_relevance(content, topic, keywords)
        seo_quality = self._score_seo_quality(content, context)
        readability = self._score_readability(content)
        engagement = self._score_engagement(content)
        
        # Calculate overall
        scores = [clarity, accuracy, completeness, relevance, seo_quality, readability, engagement]
        overall_score = sum(scores) / len(scores)
        
        passing = overall_score >= 7.0
        
        # Generate feedback
        feedback = self._generate_feedback(
            overall_score,
            clarity, accuracy, completeness, relevance, seo_quality, readability, engagement
        )
        
        suggestions = self._generate_suggestions(
            clarity, accuracy, completeness, relevance, seo_quality, readability, engagement
        )
        
        return QualityScore(
            overall_score=round(overall_score, 1),
            clarity=round(clarity, 1),
            accuracy=round(accuracy, 1),
            completeness=round(completeness, 1),
            relevance=round(relevance, 1),
            seo_quality=round(seo_quality, 1),
            readability=round(readability, 1),
            engagement=round(engagement, 1),
            passing=passing,
            feedback=feedback,
            suggestions=suggestions,
            evaluation_timestamp=datetime.utcnow().isoformat(),
        )
    
    async def _evaluate_with_llm(
        self,
        content: str,
        context: Optional[Dict[str, Any]] = None
    ) -> QualityScore:
        """LLM-based evaluation for more nuanced scoring"""
        
        context = context or {}
        
        evaluation_prompt = f"""
Evaluate this content on 7 criteria (0-10 scale):

CONTENT:
{content[:2000]}

CRITERIA:
1. Clarity (0-10): Is it clear and easy to understand?
2. Accuracy (0-10): Is information correct and fact-checked?
3. Completeness (0-10): Does it cover the topic thoroughly?
4. Relevance (0-10): Is all content relevant?
5. SEO Quality (0-10): Keywords, structure, optimization?
6. Readability (0-10): Grammar, flow, formatting?
7. Engagement (0-10): Is it compelling and interesting?

Respond in JSON format:
{{
  "clarity": 8.5,
  "accuracy": 8.0,
  "completeness": 7.5,
  "relevance": 9.0,
  "seo_quality": 8.0,
  "readability": 9.5,
  "engagement": 8.5,
  "feedback": "Strong overall content with...",
  "suggestions": ["Improve X", "Add more about Y"]
}}
"""
        
        try:
            # Call LLM for evaluation
            response = await self.model_router.query(
                evaluation_prompt,
                max_tokens=500,
                temperature=0.2  # Low temperature for objective evaluation
            )
            
            # Parse JSON response
            scores_dict = json.loads(response)
            
            clarity = scores_dict.get("clarity", 5.0)
            accuracy = scores_dict.get("accuracy", 5.0)
            completeness = scores_dict.get("completeness", 5.0)
            relevance = scores_dict.get("relevance", 5.0)
            seo_quality = scores_dict.get("seo_quality", 5.0)
            readability = scores_dict.get("readability", 5.0)
            engagement = scores_dict.get("engagement", 5.0)
            
            overall_score = (clarity + accuracy + completeness + relevance + seo_quality + readability + engagement) / 7
            
            return QualityScore(
                overall_score=round(overall_score, 1),
                clarity=round(clarity, 1),
                accuracy=round(accuracy, 1),
                completeness=round(completeness, 1),
                relevance=round(relevance, 1),
                seo_quality=round(seo_quality, 1),
                readability=round(readability, 1),
                engagement=round(engagement, 1),
                passing=overall_score >= 7.0,
                feedback=scores_dict.get("feedback", "LLM evaluation completed"),
                suggestions=scores_dict.get("suggestions", []),
                evaluation_timestamp=datetime.utcnow().isoformat(),
                evaluated_by="QualityEvaluator-LLM",
            )
            
        except Exception as e:
            logger.warning(f"LLM evaluation failed, falling back to patterns: {str(e)}")
            # Fall back to pattern-based
            return self._evaluate_with_patterns(content, context)
    
    def _score_clarity(self, content: str) -> float:
        """
        Score clarity (0-10)
        - Short, clear sentences (high score)
        - Varied sentence length (high score)
        - Complex jargon without explanation (low score)
        """
        if not content:
            return 0.0
        
        sentences = [s.strip() for s in content.split('.') if s.strip()]
        if not sentences:
            return 0.0
        
        avg_sentence_length = sum(len(s.split()) for s in sentences) / len(sentences)
        
        score = 7.0  # Base score
        
        # Penalize very long sentences
        if avg_sentence_length > 20:
            score -= 1.0
        elif avg_sentence_length > 25:
            score -= 2.0
        
        # Reward appropriately short sentences
        if 8 <= avg_sentence_length <= 15:
            score += 1.0
        
        # Check for clarity indicators
        clarity_words = ["clearly", "obviously", "importantly", "specifically", "for example"]
        if sum(content.lower().count(word) for word in clarity_words) > 3:
            score += 1.0
        
        return min(10.0, max(0.0, score))
    
    def _score_accuracy(self, content: str, context: Optional[Dict[str, Any]] = None) -> float:
        """
        Score accuracy (0-10)
        - Presence of data/citations (high score)
        - Specific numbers and facts (high score)
        - Vague statements (low score)
        """
        if not content:
            return 0.0
        
        score = 6.0  # Base score (assume some accuracy)
        
        # Check for citations/references
        citation_patterns = [
            r'\[.*?\]',  # [reference]
            r'\(source:.*?\)',  # (source: ...)
            r'according to',
            r'research shows',
            r'studies indicate',
            r'data shows'
        ]
        citations = sum(len(re.findall(pattern, content, re.IGNORECASE)) for pattern in citation_patterns)
        if citations > 3:
            score += 2.0
        elif citations > 0:
            score += 1.0
        
        # Check for specific numbers/statistics
        numbers = len(re.findall(r'\b\d+(?:\.\d+)?%?\b', content))
        if numbers > 5:
            score += 1.5
        elif numbers > 2:
            score += 0.5
        
        # Penalize vague language
        vague_words = ["maybe", "possibly", "perhaps", "might", "could be", "somewhat", "kind of"]
        vague_count = sum(content.lower().count(word) for word in vague_words)
        if vague_count > 5:
            score -= 1.0
        
        return min(10.0, max(0.0, score))
    
    def _score_completeness(self, content: str, context: Optional[Dict[str, Any]] = None) -> float:
        """
        Score completeness (0-10)
        - Length: 500+ words = higher score
        - Multiple sections/headers = higher score
        - Covers multiple aspects = higher score
        """
        if not content:
            return 0.0
        
        word_count = len(content.split())
        
        score = 3.0  # Base score
        
        # Length-based scoring
        if word_count >= 1500:
            score += 3.0
        elif word_count >= 1000:
            score += 2.5
        elif word_count >= 700:
            score += 2.0
        elif word_count >= 500:
            score += 1.5
        elif word_count >= 300:
            score += 1.0
        
        # Check for sections (headers, bullet points)
        header_count = len(re.findall(r'#{1,6}\s+', content))
        bullet_count = len(re.findall(r'^\s*[-•*]\s+', content, re.MULTILINE))
        section_count = header_count + (bullet_count // 5)  # Normalize bullets
        
        if section_count >= 3:
            score += 1.5
        elif section_count >= 1:
            score += 0.5
        
        # Check for diverse content (code blocks, quotes, examples)
        has_code = '```' in content or '<code>' in content
        has_quotes = '"' in content or "'" in content
        has_examples = 'example' in content.lower() or 'for instance' in content.lower()
        
        diversity_bonus = sum([has_code, has_quotes, has_examples]) * 0.5
        score += diversity_bonus
        
        return min(10.0, max(0.0, score))
    
    def _score_relevance(self, content: str, topic: str, keywords: Optional[List[str]] = None) -> float:
        """
        Score relevance (0-10)
        - Topic mentions throughout = higher score
        - Target keywords present = higher score
        - Off-topic content = lower score
        """
        if not content:
            return 0.0
        
        score = 5.0  # Base score
        
        # Check for topic mentions
        if topic:
            topic_mentions = content.lower().count(topic.lower())
            if topic_mentions >= 10:
                score += 2.0
            elif topic_mentions >= 5:
                score += 1.5
            elif topic_mentions >= 2:
                score += 1.0
        
        # Check for keyword coverage
        if keywords:
            keyword_matches = sum(1 for kw in keywords if kw.lower() in content.lower())
            keyword_coverage = keyword_matches / len(keywords)
            score += keyword_coverage * 2.5  # Up to 2.5 points
        
        # Check for coherence (no random paragraphs)
        # Simple check: if sentences share common words
        sentences = [s.strip().lower() for s in content.split('.') if s.strip()]
        if len(sentences) > 3:
            words_per_sentence = [set(s.split()) for s in sentences[:5]]
            common_words = set.intersection(*words_per_sentence) if words_per_sentence else set()
            if len(common_words) > 5:
                score += 1.0
        
        return min(10.0, max(0.0, score))
    
    def _score_seo_quality(self, content: str, context: Optional[Dict[str, Any]] = None) -> float:
        """
        Score SEO quality (0-10)
        - Meta title/description quality
        - Keyword optimization
        - Header structure (H1, H2, H3)
        - Link presence
        - Mobile-friendly formatting
        """
        if not content:
            return 0.0
        
        score = 4.0  # Base score
        
        context = context or {}
        
        # Check for proper heading structure
        h1_count = len(re.findall(r'^#\s+', content, re.MULTILINE))
        h2_count = len(re.findall(r'^##\s+', content, re.MULTILINE))
        h3_count = len(re.findall(r'^###\s+', content, re.MULTILINE))
        
        if h1_count >= 1:
            score += 1.0
        if h2_count >= 2:
            score += 1.5
        if h3_count >= 3:
            score += 0.5
        
        # Check for keyword optimization
        keywords = context.get("target_keywords", [])
        if keywords:
            keyword_density = sum(content.lower().count(kw.lower()) for kw in keywords) / max(1, len(content.split()))
            if 0.01 <= keyword_density <= 0.05:  # 1-5% is good
                score += 1.5
            elif 0.005 <= keyword_density <= 0.1:
                score += 1.0
        
        # Check for links (good for SEO)
        link_count = len(re.findall(r'\[.*?\]\(.*?\)', content))
        if link_count >= 3:
            score += 1.0
        
        # Check for image alt text
        alt_text = len(re.findall(r'alt=', content))
        if alt_text >= 2:
            score += 0.5
        
        return min(10.0, max(0.0, score))
    
    def _score_readability(self, content: str) -> float:
        """
        Score readability (0-10)
        - Flesch-Kincaid style (sentence length, syllables)
        - Paragraph structure
        - Grammar quality indicators
        """
        if not content:
            return 0.0
        
        score = 6.0  # Base score
        
        # Check paragraph structure (good formatting)
        paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
        avg_para_length = len(content.split()) / max(1, len(paragraphs))
        
        if 50 <= avg_para_length <= 150:
            score += 1.5
        elif avg_para_length > 200:
            score -= 1.0  # Dense paragraphs are harder to read
        
        # Check for active voice indicators
        passive_indicators = ["was", "were", "been", "being", "by"]
        passive_count = sum(content.lower().count(word) for word in passive_indicators)
        active_ratio = max(0, 1 - (passive_count / max(1, len(content.split()))))
        score += active_ratio * 1.0
        
        # Check for formatting (bullets, lists)
        has_formatting = (
            '\n- ' in content or
            '\n* ' in content or
            '\n• ' in content or
            '**' in content or
            '__' in content
        )
        if has_formatting:
            score += 1.0
        
        # Check for contractions (readability indicator)
        contractions = len(re.findall(r"n't|'re|'ve|'ll|'s|'m", content))
        if contractions > 3:
            score += 0.5
        
        return min(10.0, max(0.0, score))
    
    def _score_engagement(self, content: str) -> float:
        """
        Score engagement (0-10)
        - Use of questions
        - Emotional language
        - Call-to-action
        - Storytelling elements
        """
        if not content:
            return 0.0
        
        score = 4.0  # Base score
        
        # Check for questions
        questions = len(re.findall(r'\?', content))
        if questions >= 3:
            score += 2.0
        elif questions >= 1:
            score += 1.0
        
        # Check for emotional language
        emotional_words = [
            "amazing", "incredible", "fascinating", "wonderful", "exciting",
            "beautiful", "powerful", "stunning", "remarkable", "brilliant",
            "must", "urgent", "critical", "essential"
        ]
        emotional_count = sum(content.lower().count(word) for word in emotional_words)
        if emotional_count >= 5:
            score += 1.5
        elif emotional_count >= 2:
            score += 0.5
        
        # Check for call-to-action
        cta_indicators = ["click here", "learn more", "get started", "try now", "join us", "read more"]
        if any(cta in content.lower() for cta in cta_indicators):
            score += 1.5
        
        # Check for storytelling (quotes, anecdotes, examples)
        has_quotes = content.count('"') >= 2
        has_examples = 'example' in content.lower()
        if has_quotes or has_examples:
            score += 1.0
        
        return min(10.0, max(0.0, score))
    
    def _generate_feedback(
        self,
        overall: float,
        clarity: float,
        accuracy: float,
        completeness: float,
        relevance: float,
        seo: float,
        readability: float,
        engagement: float
    ) -> str:
        """Generate human-readable feedback"""
        
        parts = []
        
        if overall >= 8.5:
            parts.append("Excellent content! Strong performance across all criteria.")
        elif overall >= 7.5:
            parts.append("Good quality content with minor areas for improvement.")
        elif overall >= 7.0:
            parts.append("Acceptable content that meets baseline standards.")
        elif overall >= 6.0:
            parts.append("Content needs improvement in several areas.")
        else:
            parts.append("Content requires significant revision before publishing.")
        
        # Highlight strengths
        strengths = []
        if clarity >= 8.0:
            strengths.append("excellent clarity")
        if accuracy >= 8.0:
            strengths.append("strong factual accuracy")
        if completeness >= 8.0:
            strengths.append("comprehensive coverage")
        if seo >= 8.0:
            strengths.append("good SEO optimization")
        if readability >= 8.0:
            strengths.append("excellent readability")
        
        if strengths:
            parts.append(f"Strengths: {', '.join(strengths)}.")
        
        # Identify weaknesses
        weaknesses = []
        if clarity < 6.0:
            weaknesses.append("clarity (simplify language)")
        if accuracy < 6.0:
            weaknesses.append("accuracy (add citations)")
        if completeness < 6.0:
            weaknesses.append("completeness (expand content)")
        if relevance < 6.0:
            weaknesses.append("relevance (stay on topic)")
        if seo < 6.0:
            weaknesses.append("SEO (optimize keywords)")
        if readability < 6.0:
            weaknesses.append("readability (improve structure)")
        if engagement < 6.0:
            weaknesses.append("engagement (add examples)")
        
        if weaknesses:
            parts.append(f"Focus areas: {', '.join(weaknesses)}.")
        
        return " ".join(parts)
    
    def _generate_suggestions(
        self,
        clarity: float,
        accuracy: float,
        completeness: float,
        relevance: float,
        seo: float,
        readability: float,
        engagement: float
    ) -> List[str]:
        """Generate specific improvement suggestions"""
        
        suggestions = []
        
        if clarity < 7.0:
            suggestions.append("Simplify technical jargon and break up long sentences (avg <18 words)")
        
        if accuracy < 7.0:
            suggestions.append("Add citations, data sources, and fact-checking indicators")
        
        if completeness < 7.0:
            suggestions.append("Expand content to 1000+ words with more sections and examples")
        
        if relevance < 7.0:
            suggestions.append("Ensure all paragraphs relate directly to the main topic")
        
        if seo < 7.0:
            suggestions.append("Optimize with more targeted keywords (1-5% density) and better headers")
        
        if readability < 7.0:
            suggestions.append("Add bullet points, formatting, and shorter paragraphs (3-4 sentences each)")
        
        if engagement < 7.0:
            suggestions.append("Include questions, examples, emotional language, and a clear call-to-action")
        
        return suggestions[:5]  # Return top 5 suggestions
    
    def _create_error_score(self, error_msg: str) -> QualityScore:
        """Create a minimal score when evaluation fails"""
        return QualityScore(
            overall_score=5.0,
            clarity=5.0,
            accuracy=5.0,
            completeness=5.0,
            relevance=5.0,
            seo_quality=5.0,
            readability=5.0,
            engagement=5.0,
            passing=False,
            feedback=f"Evaluation error: {error_msg}. Please review content manually.",
            suggestions=["Manual review recommended"],
            evaluation_timestamp=datetime.utcnow().isoformat(),
        )
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get evaluation statistics"""
        total = self.evaluation_count
        if total == 0:
            return {
                "total_evaluations": 0,
                "pass_rate": 0.0,
                "fail_rate": 0.0,
                "average_score": 0.0,
            }
        
        pass_rate = (self.passing_count / total) * 100
        fail_rate = (self.failing_count / total) * 100
        
        avg_score = (
            sum(e.overall_score for e in self.evaluation_history) / len(self.evaluation_history)
            if self.evaluation_history
            else 0.0
        )
        
        return {
            "total_evaluations": total,
            "passing": self.passing_count,
            "failing": self.failing_count,
            "pass_rate": round(pass_rate, 1),
            "fail_rate": round(fail_rate, 1),
            "average_score": round(avg_score, 1),
            "recent_scores": [e.overall_score for e in self.evaluation_history[-5:]],
        }


# Dependency injection function (replaces singleton pattern)
async def get_quality_evaluator(model_router=None) -> QualityEvaluator:
    """
    Factory function for QualityEvaluator dependency injection.
    
    Replaces singleton pattern with FastAPI Depends() for:
    - Testability: Can inject mocks/test instances
    - Thread safety: No global state
    - Flexibility: Can create multiple instances if needed
    
    Usage in route:
        @router.get("/endpoint")
        async def handler(evaluator: QualityEvaluator = Depends(get_quality_evaluator)):
            return await evaluator.evaluate(...)
    """
    return QualityEvaluator(model_router=model_router)
