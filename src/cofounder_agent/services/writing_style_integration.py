"""
Writing Style Integration Service - Phase 3.3

Provides enhanced integration of writing samples into content generation pipeline.
Handles:
1. Sample retrieval by ID
2. Sample analysis (tone, style, characteristics)
3. Prompt injection for creative agent
4. Style matching verification

This service bridges the gap between the sample upload system (Phase 3.1/3.2)
and content generation (Phase 3.3).
"""

import logging
from typing import Optional, Dict, Any, List
import re
from collections import Counter

from services.database_service import DatabaseService
from services.writing_style_service import WritingStyleService

logger = logging.getLogger(__name__)


class WritingStyleIntegrationService:
    """Service for integrating writing samples into content generation"""

    def __init__(self, database_service: DatabaseService):
        """
        Initialize writing style integration service.
        
        Args:
            database_service: Database service instance
        """
        self.db = database_service
        self.writing_style_service = WritingStyleService(database_service)

    async def get_sample_for_content_generation(
        self, 
        writing_style_id: str,
        user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get writing sample for use in content generation with full analysis.
        
        Args:
            writing_style_id: UUID of writing sample
            user_id: Optional user ID for fallback to active sample
            
        Returns:
            Dict with sample data and analysis, or None if not found
        """
        try:
            # Try to get specific sample
            sample_data = None
            
            if writing_style_id:
                sample_data = await self.writing_style_service.get_style_prompt_for_specific_sample(
                    writing_style_id
                )
            elif user_id:
                # Fallback to active sample for user
                sample_data = await self.writing_style_service.get_style_prompt_for_generation(user_id)
            
            if not sample_data:
                logger.warning(f"No writing sample found for style_id={writing_style_id}, user_id={user_id}")
                return None
            
            # Enhance with detailed analysis
            sample_text = sample_data.get("sample_text", "")
            if sample_text:
                analysis = self._analyze_sample(sample_text)
                sample_data["analysis"] = analysis
                logger.info(f"✅ Sample analysis: tone={analysis.get('detected_tone')}, "
                          f"style={analysis.get('detected_style')}, "
                          f"avg_sentence_len={analysis.get('avg_sentence_length')}")
            
            return sample_data
            
        except Exception as e:
            logger.error(f"Error getting sample for content generation: {e}")
            return None

    def _analyze_sample(self, sample_text: str) -> Dict[str, Any]:
        """
        Analyze writing sample characteristics.
        
        Args:
            sample_text: The writing sample text
            
        Returns:
            Dict with analysis results including tone, style, characteristics
        """
        if not sample_text:
            return {}
        
        # Calculate basic metrics
        words = sample_text.split()
        sentences = re.split(r'[.!?]+', sample_text)
        sentences = [s.strip() for s in sentences if s.strip()]
        paragraphs = [p.strip() for p in sample_text.split('\n\n') if p.strip()]
        
        word_count = len(words)
        sentence_count = len(sentences)
        paragraph_count = len(paragraphs)
        
        avg_word_length = sum(len(w) for w in words) / word_count if words else 0
        avg_sentence_length = word_count / sentence_count if sentence_count > 0 else 0
        avg_paragraph_length = word_count / paragraph_count if paragraph_count > 0 else 0
        
        # Detect tone markers
        formal_markers = [
            'therefore', 'moreover', 'furthermore', 'consequently', 'however',
            'noteworthy', 'significant', 'comprehensive', 'utilize', 'facilitate'
        ]
        casual_markers = [
            'like', 'really', 'pretty', 'super', 'awesome', 'cool', 'actually',
            'basically', 'literally', 'totally', 'gonna', 'wanna'
        ]
        authoritative_markers = [
            'research shows', 'studies demonstrate', 'evidence suggests', 'proven',
            'based on', 'according to', 'documented', 'established'
        ]
        conversational_markers = [
            'you', 'we', 'let us', 'consider', 'imagine', 'think about', 'here is'
        ]
        
        text_lower = sample_text.lower()
        formal_count = sum(1 for marker in formal_markers if marker in text_lower)
        casual_count = sum(1 for marker in casual_markers if marker in text_lower)
        authoritative_count = sum(1 for marker in authoritative_markers if marker in text_lower)
        conversational_count = sum(1 for marker in conversational_markers if marker in text_lower)
        
        # Determine dominant tone
        tone_scores = {
            'formal': formal_count,
            'casual': casual_count,
            'authoritative': authoritative_count,
            'conversational': conversational_count
        }
        detected_tone = max(tone_scores, key=tone_scores.get) if max(tone_scores.values()) > 0 else 'neutral'
        
        # Detect style characteristics
        has_lists = '- ' in sample_text or '* ' in sample_text or '1.' in sample_text
        has_code_blocks = '```' in sample_text or '`' in sample_text
        has_headings = sample_text.count('#') > 0
        has_quotes = '"' in sample_text or "'" in sample_text
        has_examples = 'example' in text_lower or 'for instance' in text_lower or 'such as' in text_lower
        
        # Determine style
        style_markers = {
            'technical': has_code_blocks or has_headings,
            'narrative': has_examples and not has_code_blocks,
            'listicle': has_lists,
            'educational': has_headings and has_examples,
            'thought-leadership': has_quotes or authoritative_count > casual_count
        }
        detected_style = max(style_markers, key=style_markers.get) if any(style_markers.values()) else 'general'
        
        # Calculate vocabulary complexity
        unique_words = len(set(words))
        vocabulary_diversity = unique_words / word_count if word_count > 0 else 0
        
        # Get most common word types for pattern matching
        punctuation_marks = Counter([c for c in sample_text if c in '.,!?;:'])
        
        return {
            'detected_tone': detected_tone,
            'detected_style': detected_style,
            'tone_scores': tone_scores,
            'word_count': word_count,
            'sentence_count': sentence_count,
            'paragraph_count': paragraph_count,
            'avg_word_length': round(avg_word_length, 2),
            'avg_sentence_length': round(avg_sentence_length, 2),
            'avg_paragraph_length': round(avg_paragraph_length, 2),
            'vocabulary_diversity': round(vocabulary_diversity, 2),
            'style_characteristics': {
                'has_lists': has_lists,
                'has_code_blocks': has_code_blocks,
                'has_headings': has_headings,
                'has_quotes': has_quotes,
                'has_examples': has_examples
            }
        }

    async def generate_creative_agent_prompt_injection(
        self,
        writing_style_id: Optional[str],
        user_id: Optional[str],
        base_prompt: str
    ) -> str:
        """
        Generate enhanced prompt for creative agent that includes writing sample guidance.
        
        Args:
            writing_style_id: UUID of writing sample to use
            user_id: User ID for fallback to active sample
            base_prompt: Base prompt from creative agent
            
        Returns:
            Enhanced prompt with writing sample guidance injected
        """
        try:
            sample_data = await self.get_sample_for_content_generation(
                writing_style_id, user_id
            )
            
            if not sample_data:
                logger.info("No writing sample found, returning base prompt")
                return base_prompt
            
            # Get the writing sample guidance and analysis
            writing_sample_guidance = sample_data.get("writing_style_guidance", "")
            analysis = sample_data.get("analysis", {})
            
            # Build enhanced prompt with guidance
            enhanced_prompt = base_prompt
            
            if writing_sample_guidance:
                enhanced_prompt += f"\n\n{writing_sample_guidance}"
            
            # Add analysis-specific guidance
            if analysis:
                analysis_guidance = self._build_analysis_guidance(analysis)
                if analysis_guidance:
                    enhanced_prompt += f"\n\n## Style Analysis Results\n{analysis_guidance}"
            
            logger.info(f"✅ Creative agent prompt enhanced with writing sample guidance")
            return enhanced_prompt
            
        except Exception as e:
            logger.error(f"Error generating creative agent prompt injection: {e}")
            return base_prompt

    @staticmethod
    def _build_analysis_guidance(analysis: Dict[str, Any]) -> str:
        """
        Build guidance text based on sample analysis.
        
        Args:
            analysis: Sample analysis results
            
        Returns:
            Formatted guidance text
        """
        if not analysis:
            return ""
        
        guidance = "**Detected Characteristics:**\n"
        guidance += f"- Tone: {analysis.get('detected_tone', 'neutral')}\n"
        guidance += f"- Style: {analysis.get('detected_style', 'general')}\n"
        guidance += f"- Average sentence length: {analysis.get('avg_sentence_length', 0):.1f} words\n"
        guidance += f"- Vocabulary diversity: {analysis.get('vocabulary_diversity', 0):.1%}\n"
        
        # Add style characteristics
        chars = analysis.get('style_characteristics', {})
        if chars:
            guidance += "\n**Structural Elements:**\n"
            if chars.get('has_headings'):
                guidance += "- Uses clear headings and structure\n"
            if chars.get('has_lists'):
                guidance += "- Organizes content with lists\n"
            if chars.get('has_examples'):
                guidance += "- Includes concrete examples\n"
            if chars.get('has_quotes'):
                guidance += "- Uses quotations for emphasis\n"
            if chars.get('has_code_blocks'):
                guidance += "- Includes code examples\n"
        
        return guidance.strip()

    async def verify_style_match(
        self,
        generated_content: str,
        writing_style_id: str,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Verify if generated content matches the requested writing style.
        
        Args:
            generated_content: The generated content to verify
            writing_style_id: UUID of the requested writing sample
            user_id: User ID for fallback
            
        Returns:
            Dict with match results and confidence scores
        """
        try:
            sample_data = await self.get_sample_for_content_generation(
                writing_style_id, user_id
            )
            
            if not sample_data:
                return {"matched": False, "reason": "Sample not found"}
            
            sample_text = sample_data.get("sample_text", "")
            sample_analysis = sample_data.get("analysis", {})
            generated_analysis = self._analyze_sample(generated_content)
            
            # Compare characteristics
            results = {
                "matched": True,
                "sample_analysis": sample_analysis,
                "generated_analysis": generated_analysis,
                "comparison": self._compare_analyses(sample_analysis, generated_analysis)
            }
            
            return results
            
        except Exception as e:
            logger.error(f"Error verifying style match: {e}")
            return {"matched": False, "reason": f"Verification error: {str(e)}"}

    @staticmethod
    def _compare_analyses(sample_analysis: Dict[str, Any], generated_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compare sample and generated content analyses.
        
        Args:
            sample_analysis: Analysis of the original sample
            generated_analysis: Analysis of the generated content
            
        Returns:
            Dict with comparison results
        """
        comparison = {
            "tone_match": sample_analysis.get('detected_tone') == generated_analysis.get('detected_tone'),
            "tone_sample": sample_analysis.get('detected_tone'),
            "tone_generated": generated_analysis.get('detected_tone'),
            "style_match": sample_analysis.get('detected_style') == generated_analysis.get('detected_style'),
            "style_sample": sample_analysis.get('detected_style'),
            "style_generated": generated_analysis.get('detected_style'),
            "sentence_length_similarity": abs(
                sample_analysis.get('avg_sentence_length', 0) - 
                generated_analysis.get('avg_sentence_length', 0)
            ) < 5,  # Within 5 words is considered similar
            "sample_sentence_length": sample_analysis.get('avg_sentence_length'),
            "generated_sentence_length": generated_analysis.get('avg_sentence_length')
        }
        
        return comparison
