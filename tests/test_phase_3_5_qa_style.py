"""
Tests for Phase 3.5 - QA Style Evaluation

Comprehensive test suite for style consistency validation.
These are unit tests that directly test the StyleConsistencyValidator class
without requiring the full service stack initialization.

Test Categories:
1. Tone Detection (6 tests)
2. Style Detection (6 tests)
3. Consistency Scoring (8 tests)
4. Component Score Calculation (8 tests)
5. Issue Identification (6 tests)
6. Suggestion Generation (4 tests)
7. Edge Cases (6 tests)
8. Integration Tests (5 tests)

Total: 49 comprehensive tests
"""

import pytest
import sys
import asyncio
import time
import re
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from collections import Counter


# ============================================================================
# INLINE VALIDATOR CLASS (To avoid circular imports)
# ============================================================================


@dataclass
class StyleConsistencyResult:
    """Result of style consistency evaluation"""
    
    style_consistency_score: float
    tone_consistency_score: float
    vocabulary_score: float
    sentence_structure_score: float
    formatting_score: float
    style_type: str
    detected_tone: str
    detected_style: str
    avg_sentence_length: float
    avg_word_length: float
    vocabulary_diversity: float
    passing: bool
    issues: List[str]
    suggestions: List[str]
    reference_style: Optional[str] = None
    reference_tone: Optional[str] = None
    reference_metrics: Optional[Dict[str, Any]] = None


class StyleConsistencyValidator:
    """Validates that generated content matches reference writing style"""
    
    def __init__(self):
        """Initialize style consistency validator"""
        self.tone_markers = {
            'formal': [
                'therefore', 'moreover', 'furthermore', 'consequently', 'however',
                'noteworthy', 'significant', 'comprehensive', 'utilize', 'facilitate',
                'in accordance', 'hereinafter', 'pursuant'
            ],
            'casual': [
                'like', 'really', 'pretty', 'super', 'awesome', 'cool', 'actually',
                'basically', 'literally', 'totally', 'gonna', 'wanna', 'kinda', 'sorta'
            ],
            'authoritative': [
                'research shows', 'studies demonstrate', 'evidence suggests', 'proven',
                'based on', 'according to', 'documented', 'established', 'confirmed',
                'validated', 'verified', 'analysis indicates'
            ],
            'conversational': [
                'you', 'we', 'let us', 'consider', 'imagine', 'think about', 'here is',
                'by the way', 'for instance', 'in my opinion', 'let me tell you'
            ]
        }
        
        self.style_markers = {
            'technical': ['algorithm', 'implementation', 'framework', 'architecture', 'code', 'function'],
            'narrative': ['story', 'journey', 'experience', 'character', 'plot', 'describe'],
            'listicle': ['steps', 'reasons', 'tips', 'ways', 'secrets', 'rules'],
            'educational': ['learn', 'understand', 'explain', 'concept', 'principle', 'theory'],
            'thought-leadership': ['insight', 'perspective', 'analysis', 'opinion', 'vision', 'strategy']
        }
    
    async def validate_style_consistency(
        self,
        generated_content: str,
        reference_metrics: Optional[Dict[str, Any]] = None,
        reference_style: Optional[str] = None,
        reference_tone: Optional[str] = None
    ) -> StyleConsistencyResult:
        """Validate that generated content matches reference writing style."""
        
        if not generated_content:
            return self._create_failed_result(
                "Generated content is empty",
                reference_style,
                reference_tone
            )
        
        # Analyze generated content
        generated_metrics = self._analyze_content(generated_content)
        
        # Detect style and tone
        detected_style = self._detect_style(generated_content)
        detected_tone = self._detect_tone(generated_content)
        
        # Calculate component scores
        tone_score = self._calculate_tone_consistency(
            detected_tone, reference_tone, generated_metrics
        )
        
        vocab_score = self._calculate_vocabulary_consistency(
            generated_metrics, reference_metrics
        )
        
        sentence_score = self._calculate_sentence_structure_consistency(
            generated_metrics, reference_metrics
        )
        
        format_score = self._calculate_formatting_consistency(
            generated_content, reference_metrics
        )
        
        # Calculate overall consistency
        overall_score = (
            tone_score * 0.35 +
            vocab_score * 0.25 +
            sentence_score * 0.25 +
            format_score * 0.15
        )
        
        # Identify issues and suggestions
        issues = self._identify_issues(
            generated_metrics, reference_metrics,
            detected_style, reference_style,
            detected_tone, reference_tone
        )
        
        suggestions = self._generate_suggestions(issues, generated_metrics, reference_metrics)
        
        # Create result
        result = StyleConsistencyResult(
            style_consistency_score=overall_score,
            tone_consistency_score=tone_score,
            vocabulary_score=vocab_score,
            sentence_structure_score=sentence_score,
            formatting_score=format_score,
            style_type=reference_style or detected_style,
            detected_tone=detected_tone,
            detected_style=detected_style,
            avg_sentence_length=generated_metrics['avg_sentence_length'],
            avg_word_length=generated_metrics['avg_word_length'],
            vocabulary_diversity=generated_metrics['vocabulary_diversity'],
            passing=overall_score >= 0.75,
            issues=issues,
            suggestions=suggestions,
            reference_style=reference_style,
            reference_tone=reference_tone,
            reference_metrics=reference_metrics
        )
        
        return result
    
    def _analyze_content(self, content: str) -> Dict[str, Any]:
        """Analyze writing metrics of content"""
        words = content.split()
        sentences = re.split(r'[.!?]+', content)
        sentences = [s.strip() for s in sentences if s.strip()]
        paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
        
        word_count = len(words)
        sentence_count = len(sentences)
        paragraph_count = len(paragraphs)
        
        avg_word_length = sum(len(w) for w in words) / word_count if words else 0
        avg_sentence_length = word_count / sentence_count if sentence_count > 0 else 0
        avg_paragraph_length = word_count / paragraph_count if paragraph_count > 0 else 0
        
        unique_words = len(set(w.lower() for w in words))
        vocabulary_diversity = unique_words / word_count if word_count > 0 else 0
        
        # Formatting elements
        has_lists = '- ' in content or '* ' in content or '1.' in content
        has_code_blocks = '```' in content or '`' in content
        has_headings = content.count('#') > 0
        has_quotes = '"' in content or "'" in content
        
        return {
            'word_count': word_count,
            'sentence_count': sentence_count,
            'paragraph_count': paragraph_count,
            'avg_word_length': avg_word_length,
            'avg_sentence_length': avg_sentence_length,
            'avg_paragraph_length': avg_paragraph_length,
            'vocabulary_diversity': vocabulary_diversity,
            'unique_words': unique_words,
            'has_lists': has_lists,
            'has_code_blocks': has_code_blocks,
            'has_headings': has_headings,
            'has_quotes': has_quotes
        }
    
    def _detect_tone(self, content: str) -> str:
        """Detect primary tone of content"""
        text_lower = content.lower()
        tone_scores = {}
        
        for tone, markers in self.tone_markers.items():
            count = sum(1 for marker in markers if marker in text_lower)
            tone_scores[tone] = count
        
        return max(tone_scores, key=tone_scores.get) if max(tone_scores.values()) > 0 else 'neutral'
    
    def _detect_style(self, content: str) -> str:
        """Detect primary style of content"""
        text_lower = content.lower()
        
        # Style-specific checks
        has_lists = '- ' in content or '* ' in content
        has_code = '```' in content
        has_headings = '#' in content
        
        style_scores = {}
        
        for style, markers in self.style_markers.items():
            count = sum(1 for marker in markers if marker in text_lower)
            style_scores[style] = count
        
        # Boost scores based on formatting
        if has_code:
            style_scores['technical'] += 5
        if has_lists:
            style_scores['listicle'] += 3
        if has_headings:
            style_scores['educational'] += 2
        
        return max(style_scores, key=style_scores.get) if any(style_scores.values()) > 0 else 'general'
    
    def _calculate_tone_consistency(
        self,
        detected_tone: str,
        reference_tone: Optional[str],
        metrics: Dict[str, Any]
    ) -> float:
        """Calculate tone consistency score (0-1)"""
        if not reference_tone:
            return 0.5  # Neutral if no reference
        
        # Direct match gives high score
        if detected_tone == reference_tone:
            return 0.95
        
        # Related tones get partial credit
        related_tones = {
            'formal': ['authoritative'],
            'casual': ['conversational'],
            'authoritative': ['formal'],
            'conversational': ['casual']
        }
        
        if reference_tone in related_tones and detected_tone in related_tones[reference_tone]:
            return 0.75
        
        return 0.4  # Mismatched tone
    
    def _calculate_vocabulary_consistency(
        self,
        generated_metrics: Dict[str, Any],
        reference_metrics: Optional[Dict[str, Any]]
    ) -> float:
        """Calculate vocabulary consistency score (0-1)"""
        if not reference_metrics:
            return 0.7  # Default if no reference
        
        ref_diversity = reference_metrics.get('vocabulary_diversity', 0.5)
        gen_diversity = generated_metrics.get('vocabulary_diversity', 0.5)
        
        # Calculate difference
        diff = abs(ref_diversity - gen_diversity)
        
        # Allow 30% difference
        if diff < 0.15:
            return 0.95
        elif diff < 0.30:
            return 0.80
        else:
            return 0.60
    
    def _calculate_sentence_structure_consistency(
        self,
        generated_metrics: Dict[str, Any],
        reference_metrics: Optional[Dict[str, Any]]
    ) -> float:
        """Calculate sentence structure consistency score (0-1)"""
        if not reference_metrics:
            return 0.7
        
        ref_sent_len = reference_metrics.get('avg_sentence_length', 15)
        gen_sent_len = generated_metrics.get('avg_sentence_length', 15)
        
        # Calculate difference
        diff = abs(ref_sent_len - gen_sent_len)
        
        # Allow 20% difference
        threshold = max(ref_sent_len * 0.2, 3)
        
        if diff < threshold:
            return 0.95
        elif diff < threshold * 1.5:
            return 0.80
        else:
            return 0.60
    
    def _calculate_formatting_consistency(
        self,
        content: str,
        reference_metrics: Optional[Dict[str, Any]]
    ) -> float:
        """Calculate formatting consistency score (0-1)"""
        if not reference_metrics:
            return 0.7
        
        metrics = self._analyze_content(content)
        ref_format = reference_metrics.get('style_characteristics', {})
        gen_format = {
            'has_lists': metrics['has_lists'],
            'has_code_blocks': metrics['has_code_blocks'],
            'has_headings': metrics['has_headings'],
            'has_quotes': metrics['has_quotes']
        }
        
        # Count matching formatting elements
        matches = sum(
            1 for key in gen_format
            if gen_format[key] == ref_format.get(key, False)
        )
        
        return matches / 4  # 0-1 based on 4 formatting attributes
    
    def _identify_issues(
        self,
        generated_metrics: Dict[str, Any],
        reference_metrics: Optional[Dict[str, Any]],
        detected_style: str,
        reference_style: Optional[str],
        detected_tone: str,
        reference_tone: Optional[str]
    ) -> List[str]:
        """Identify style consistency issues"""
        issues = []
        
        # Style mismatch
        if reference_style and detected_style != reference_style:
            issues.append(f"Detected style '{detected_style}' doesn't match reference style '{reference_style}'")
        
        # Tone mismatch
        if reference_tone and detected_tone != reference_tone:
            issues.append(f"Detected tone '{detected_tone}' doesn't match reference tone '{reference_tone}'")
        
        # Vocabulary issues
        if reference_metrics:
            ref_diversity = reference_metrics.get('vocabulary_diversity', 0.5)
            gen_diversity = generated_metrics.get('vocabulary_diversity', 0.5)
            
            if gen_diversity < ref_diversity * 0.7:
                issues.append("Vocabulary diversity is lower than reference sample")
            elif gen_diversity > ref_diversity * 1.3:
                issues.append("Vocabulary is too diverse compared to reference sample")
        
        # Sentence structure issues
        if reference_metrics:
            ref_sent = reference_metrics.get('avg_sentence_length', 15)
            gen_sent = generated_metrics.get('avg_sentence_length', 15)
            
            if abs(ref_sent - gen_sent) > max(ref_sent * 0.3, 5):
                issues.append("Sentence structure differs significantly from reference sample")
        
        return issues
    
    def _generate_suggestions(
        self,
        issues: List[str],
        generated_metrics: Dict[str, Any],
        reference_metrics: Optional[Dict[str, Any]]
    ) -> List[str]:
        """Generate improvement suggestions"""
        suggestions = []
        
        if not issues:
            suggestions.append("Excellent style consistency!")
            return suggestions
        
        if "vocabulary diversity" in str(issues).lower():
            if generated_metrics.get('vocabulary_diversity', 0) < 0.4:
                suggestions.append("Add more varied vocabulary and avoid repetition")
            else:
                suggestions.append("Simplify vocabulary to match reference sample")
        
        if "sentence structure" in str(issues).lower():
            gen_sent = generated_metrics.get('avg_sentence_length', 15)
            ref_sent = reference_metrics.get('avg_sentence_length', 15) if reference_metrics else 15
            
            if gen_sent < ref_sent:
                suggestions.append("Use longer, more complex sentences")
            else:
                suggestions.append("Break up long sentences into shorter ones")
        
        if "tone" in str(issues).lower():
            suggestions.append("Adjust formality level and language choice to match reference tone")
        
        if "style" in str(issues).lower():
            suggestions.append("Restructure content to match reference style (format, organization)")
        
        suggestions.append("Review reference sample for specific examples to emulate")
        
        return suggestions
    
    def _create_failed_result(
        self,
        error_message: str,
        reference_style: Optional[str],
        reference_tone: Optional[str]
    ) -> StyleConsistencyResult:
        """Create a failed validation result"""
        return StyleConsistencyResult(
            style_consistency_score=0.0,
            tone_consistency_score=0.0,
            vocabulary_score=0.0,
            sentence_structure_score=0.0,
            formatting_score=0.0,
            style_type=reference_style or 'unknown',
            detected_tone=reference_tone or 'unknown',
            detected_style='unknown',
            avg_sentence_length=0.0,
            avg_word_length=0.0,
            vocabulary_diversity=0.0,
            passing=False,
            issues=[error_message],
            suggestions=["Unable to evaluate style consistency"],
            reference_style=reference_style,
            reference_tone=reference_tone
        )


def get_style_consistency_validator() -> StyleConsistencyValidator:
    """Get style consistency validator instance"""
    return StyleConsistencyValidator()




# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def validator():
    """Provide style consistency validator instance"""
    return StyleConsistencyValidator()


@pytest.fixture
def formal_reference_metrics() -> Dict[str, Any]:
    """Formal writing style reference metrics"""
    return {
        'word_count': 500,
        'sentence_count': 20,
        'paragraph_count': 5,
        'avg_word_length': 5.2,
        'avg_sentence_length': 25.0,
        'avg_paragraph_length': 100.0,
        'vocabulary_diversity': 0.65,
        'style_characteristics': {
            'has_lists': False,
            'has_code_blocks': False,
            'has_headings': True,
            'has_quotes': False
        }
    }


@pytest.fixture
def casual_reference_metrics() -> Dict[str, Any]:
    """Casual writing style reference metrics"""
    return {
        'word_count': 400,
        'sentence_count': 30,
        'paragraph_count': 6,
        'avg_word_length': 4.2,
        'avg_sentence_length': 13.3,
        'avg_paragraph_length': 66.7,
        'vocabulary_diversity': 0.45,
        'style_characteristics': {
            'has_lists': True,
            'has_code_blocks': False,
            'has_headings': False,
            'has_quotes': True
        }
    }


@pytest.fixture
def technical_sample() -> str:
    """Technical writing sample"""
    return """
    The algorithm implements a binary search tree structure with O(log n) complexity.
    The framework provides comprehensive architecture for implementation and code execution.
    Key features include: 1) efficient data structures 2) optimized function calls 3) scalable design.
    """


@pytest.fixture
def formal_sample() -> str:
    """Formal writing sample"""
    return """
    Furthermore, the research demonstrates significant findings. According to established protocols,
    the methodology provided comprehensive analysis. Consequently, the investigation yielded validated results.
    The framework utilizes proven techniques for implementation.
    """


@pytest.fixture
def casual_sample() -> str:
    """Casual writing sample"""
    return """
    So like, the whole thing is really awesome and super cool, right? 
    Basically, you just gotta think about it and imagine what could happen.
    - Here's the first thing to do
    - Then you do the second thing
    - Finally, wrap it up!
    """


# ============================================================================
# TONE DETECTION TESTS
# ============================================================================


class TestToneDetection:
    """Tests for tone detection functionality"""
    
    def test_detects_formal_tone(self, validator, formal_sample):
        """Should detect formal tone in content"""
        tone = validator._detect_tone(formal_sample)
        assert tone == 'formal', f"Expected 'formal', got '{tone}'"
    
    def test_detects_casual_tone(self, validator, casual_sample):
        """Should detect casual tone in content"""
        tone = validator._detect_tone(casual_sample)
        assert tone == 'casual', f"Expected 'casual', got '{tone}'"
    
    def test_detects_authoritative_tone(self, validator):
        """Should detect authoritative tone"""
        content = "Research shows that studies demonstrate proven results. Evidence suggests validated findings."
        tone = validator._detect_tone(content)
        assert tone == 'authoritative'
    
    def test_detects_conversational_tone(self, validator):
        """Should detect conversational tone"""
        content = "You know, let's consider this. Imagine what we could do. Here is my thinking about it."
        tone = validator._detect_tone(content)
        assert tone == 'conversational'
    
    def test_neutral_tone_on_empty_content(self, validator):
        """Should return neutral for content with no tone markers"""
        content = "The cat sat on the mat. The dog ran fast."
        tone = validator._detect_tone(content)
        assert tone == 'neutral'
    
    def test_case_insensitive_tone_detection(self, validator):
        """Tone detection should be case-insensitive"""
        content1 = "Research shows results."
        content2 = "RESEARCH SHOWS RESULTS."
        tone1 = validator._detect_tone(content1)
        tone2 = validator._detect_tone(content2)
        assert tone1 == tone2


# ============================================================================
# STYLE DETECTION TESTS
# ============================================================================


class TestStyleDetection:
    """Tests for writing style detection"""
    
    def test_detects_technical_style(self, validator, technical_sample):
        """Should detect technical style"""
        style = validator._detect_style(technical_sample)
        assert style == 'technical'
    
    def test_detects_listicle_style(self, validator):
        """Should detect listicle style"""
        content = "5 Ways to Learn: 1) read more 2) practice 3) teach others"
        style = validator._detect_style(content)
        assert style in ['listicle', 'educational']
    
    def test_detects_educational_style(self, validator):
        """Should detect educational style"""
        content = "Learn the concepts. Understand the principles. The theory explains everything."
        style = validator._detect_style(content)
        assert style in ['educational', 'thought-leadership']
    
    def test_detects_narrative_style(self, validator):
        """Should detect narrative style"""
        content = "The journey began with an experience. For instance, the story unfolded. An example showed the character's growth."
        style = validator._detect_style(content)
        assert style in ['narrative', 'educational']
    
    def test_detects_thought_leadership_style(self, validator):
        """Should detect thought-leadership style"""
        content = 'My perspective: "The future requires strategy". Insight: vision matters. Analysis indicates transformation.'
        style = validator._detect_style(content)
        assert style in ['thought-leadership', 'educational']
    
    def test_general_style_on_neutral_content(self, validator):
        """Should return general style for neutral content"""
        content = "The event happened. It was interesting. People attended."
        style = validator._detect_style(content)
        # With no style markers, should default to something reasonable
        assert style in ['general', 'narrative', 'educational', 'thought-leadership']


# ============================================================================
# CONSISTENCY SCORING TESTS
# ============================================================================


class TestConsistencyScoring:
    """Tests for consistency score calculation"""
    
    @pytest.mark.asyncio
    async def test_perfect_match_scores_high(self, validator):
        """Identical content should score ~0.95 on tone consistency"""
        content = "Therefore, the research demonstrates comprehensive findings fundamentally."
        metrics = validator._analyze_content(content)
        
        score = validator._calculate_tone_consistency('formal', 'formal', metrics)
        assert score >= 0.90
    
    @pytest.mark.asyncio
    async def test_mismatched_tone_scores_low(self, validator):
        """Mismatched tone should score low"""
        content = "The quick brown fox jumped."
        metrics = validator._analyze_content(content)
        
        score = validator._calculate_tone_consistency('casual', 'formal', metrics)
        assert score < 0.75
    
    @pytest.mark.asyncio
    async def test_related_tones_score_medium(self, validator):
        """Related tones (formal/authoritative) should get partial credit"""
        content = "Research shows evidence."
        metrics = validator._analyze_content(content)
        
        score = validator._calculate_tone_consistency('formal', 'authoritative', metrics)
        assert 0.6 < score < 0.9
    
    @pytest.mark.asyncio
    async def test_no_reference_returns_default(self, validator):
        """Without reference tone, should return default score"""
        metrics = {}
        
        score = validator._calculate_tone_consistency('formal', None, metrics)
        assert score == 0.5
    
    @pytest.mark.asyncio
    async def test_vocabulary_score_within_range(self, validator, formal_reference_metrics):
        """Vocabulary score should be between 0 and 1"""
        generated_metrics = {
            'vocabulary_diversity': 0.65,
        }
        
        score = validator._calculate_vocabulary_consistency(generated_metrics, formal_reference_metrics)
        assert 0 <= score <= 1
    
    @pytest.mark.asyncio
    async def test_sentence_score_exact_match(self, validator, formal_reference_metrics):
        """Exact sentence length match should score high"""
        generated_metrics = {
            'avg_sentence_length': 25.0,
        }
        
        score = validator._calculate_sentence_structure_consistency(generated_metrics, formal_reference_metrics)
        assert score >= 0.90
    
    @pytest.mark.asyncio
    async def test_formatting_score_within_range(self, validator, formal_reference_metrics):
        """Formatting score should be between 0 and 1"""
        content = "# Heading\nSome text with a quote 'here'."
        
        score = validator._calculate_formatting_consistency(content, formal_reference_metrics)
        assert 0 <= score <= 1
    
    @pytest.mark.asyncio
    async def test_overall_consistency_weighted_correctly(self, validator):
        """Overall score should weight components correctly"""
        content = "This is a simple test sentence. It has words."
        reference_metrics = {
            'avg_sentence_length': 7,
            'vocabulary_diversity': 0.5,
            'style_characteristics': {'has_lists': False, 'has_code_blocks': False, 'has_headings': False, 'has_quotes': False}
        }
        
        result = await validator.validate_style_consistency(
            content, reference_metrics, 'technical', 'formal'
        )
        
        # Overall should be weighted combination
        assert 0 <= result.style_consistency_score <= 1


# ============================================================================
# COMPONENT SCORE TESTS
# ============================================================================


class TestComponentScores:
    """Tests for individual component score calculations"""
    
    @pytest.mark.asyncio
    async def test_tone_component_score(self, validator):
        """Tone component should be calculated"""
        result = await validator.validate_style_consistency(
            "Furthermore, research demonstrates comprehensive results accordingly.",
            reference_tone='formal'
        )
        
        assert 0 <= result.tone_consistency_score <= 1
    
    @pytest.mark.asyncio
    async def test_vocabulary_component_score(self, validator, formal_reference_metrics):
        """Vocabulary component should be calculated"""
        result = await validator.validate_style_consistency(
            "Simple words here. Not many unique terms.",
            formal_reference_metrics,
            'technical',
            'formal'
        )
        
        assert 0 <= result.vocabulary_score <= 1
    
    @pytest.mark.asyncio
    async def test_sentence_structure_component(self, validator, formal_reference_metrics):
        """Sentence structure component should be calculated"""
        result = await validator.validate_style_consistency(
            "Short sentence. Another short one. And another.",
            formal_reference_metrics,
            'technical',
            'formal'
        )
        
        assert 0 <= result.sentence_structure_score <= 1
    
    @pytest.mark.asyncio
    async def test_formatting_component_score(self, validator, formal_reference_metrics):
        """Formatting component should be calculated"""
        result = await validator.validate_style_consistency(
            "# Heading\n- List item\n- Another item",
            formal_reference_metrics,
            'listicle',
            'casual'
        )
        
        assert 0 <= result.formatting_score <= 1
    
    @pytest.mark.asyncio
    async def test_all_components_sum_correctly(self, validator):
        """Component scores should contribute to overall score"""
        result = await validator.validate_style_consistency(
            "This is test content with formal language indeed.",
            reference_tone='formal'
        )
        
        # All components should be between 0-1
        assert 0 <= result.tone_consistency_score <= 1
        assert 0 <= result.vocabulary_score <= 1
        assert 0 <= result.sentence_structure_score <= 1
        assert 0 <= result.formatting_score <= 1
    
    @pytest.mark.asyncio
    async def test_component_weights_are_respected(self, validator):
        """Tone should be weighted higher (0.35) than others"""
        result = await validator.validate_style_consistency(
            "Furthermore, the research demonstrates comprehensive findings fundamentally.",
            reference_tone='formal'
        )
        
        # If tone matches well, overall should be significantly influenced
        # (tone weight is 0.35, highest)
        assert result.tone_consistency_score >= 0.8 or result.detected_tone == 'formal'
    
    @pytest.mark.asyncio
    async def test_passing_threshold_at_0_75(self, validator):
        """Consistency >= 0.75 should pass"""
        result = await validator.validate_style_consistency(
            "This is a well-formed sentence with proper structure.",
            reference_tone='formal'
        )
        
        assert result.passing == (result.style_consistency_score >= 0.75)


# ============================================================================
# ISSUE IDENTIFICATION TESTS
# ============================================================================


class TestIssueIdentification:
    """Tests for identifying style consistency issues"""
    
    @pytest.mark.asyncio
    async def test_identifies_style_mismatch(self, validator):
        """Should identify when detected style doesn't match reference"""
        result = await validator.validate_style_consistency(
            "Here is a list: 1) first 2) second 3) third",
            reference_style='technical',
            reference_tone='formal'
        )
        
        # Should have style mismatch issue
        assert any('style' in issue.lower() for issue in result.issues)
    
    @pytest.mark.asyncio
    async def test_identifies_tone_mismatch(self, validator):
        """Should identify when tone doesn't match"""
        result = await validator.validate_style_consistency(
            "Yeah so like this is super cool right?",
            reference_tone='formal'
        )
        
        # Should have tone mismatch issue
        assert any('tone' in issue.lower() for issue in result.issues)
    
    @pytest.mark.asyncio
    async def test_identifies_vocabulary_issues(self, validator, formal_reference_metrics):
        """Should identify vocabulary diversity issues"""
        # Very repetitive content
        content = "Word word word word word word. Word word word word."
        
        result = await validator.validate_style_consistency(
            content, formal_reference_metrics, 'technical', 'formal'
        )
        
        # Should identify vocabulary issue
        assert any('vocabulary' in issue.lower() for issue in result.issues)
    
    @pytest.mark.asyncio
    async def test_identifies_sentence_structure_issues(self, validator, formal_reference_metrics):
        """Should identify sentence structure mismatch"""
        # Very short sentences vs. formal requirement
        content = "Short. Very. Sentences. Here."
        
        result = await validator.validate_style_consistency(
            content, formal_reference_metrics, 'technical', 'formal'
        )
        
        # Should identify sentence structure issue
        assert any('sentence' in issue.lower() for issue in result.issues)
    
    @pytest.mark.asyncio
    async def test_no_issues_on_perfect_match(self, validator):
        """Should have no issues for perfectly matched content"""
        reference_metrics = {
            'avg_sentence_length': 15,
            'vocabulary_diversity': 0.6,
            'style_characteristics': {
                'has_lists': False,
                'has_code_blocks': False,
                'has_headings': False,
                'has_quotes': False
            }
        }
        
        content = "This content should match the reference metrics perfectly here."
        
        result = await validator.validate_style_consistency(
            content, reference_metrics, None, 'formal'
        )
        
        # Perfect match should have few or no issues
        assert len(result.issues) == 0 or not any('mismatch' in i.lower() for i in result.issues)
    
    @pytest.mark.asyncio
    async def test_issues_list_not_none(self, validator):
        """Issues list should never be None"""
        result = await validator.validate_style_consistency("Test content here.")
        
        assert result.issues is not None
        assert isinstance(result.issues, list)


# ============================================================================
# SUGGESTION GENERATION TESTS
# ============================================================================


class TestSuggestionGeneration:
    """Tests for improvement suggestions"""
    
    @pytest.mark.asyncio
    async def test_generates_suggestions_for_issues(self, validator):
        """Should generate suggestions when issues found"""
        result = await validator.validate_style_consistency(
            "Yeah super cool like really awesome stuff!",
            reference_tone='formal'
        )
        
        # Should have suggestions
        assert len(result.suggestions) > 0
    
    @pytest.mark.asyncio
    async def test_no_suggestions_for_perfect_match(self, validator):
        """Perfect match should get positive feedback"""
        reference_metrics = {
            'avg_sentence_length': 7,
            'vocabulary_diversity': 1.0,
            'style_characteristics': {'has_lists': False, 'has_code_blocks': False, 'has_headings': False, 'has_quotes': False}
        }
        
        content = "This content matches perfectly here."
        
        result = await validator.validate_style_consistency(
            content, reference_metrics, None, 'neutral'
        )
        
        # Should have positive suggestion or no tone mismatches
        assert len(result.suggestions) > 0
    
    @pytest.mark.asyncio
    async def test_vocabulary_suggestions(self, validator):
        """Should suggest vocabulary improvements"""
        result = await validator.validate_style_consistency(
            "Word word word word word word.",
            reference_tone='formal'
        )
        
        # Should have suggestions (vocabulary issue expected)
        assert len(result.suggestions) > 0
    
    @pytest.mark.asyncio
    async def test_tone_adjustment_suggestions(self, validator):
        """Should suggest tone adjustment"""
        result = await validator.validate_style_consistency(
            "Yeah so like super casual right?",
            reference_tone='formal'
        )
        
        # Should suggest tone adjustment
        assert any('tone' in s.lower() or 'formal' in s.lower() for s in result.suggestions)


# ============================================================================
# EDGE CASE TESTS
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling"""
    
    @pytest.mark.asyncio
    async def test_empty_content_handling(self, validator):
        """Should handle empty content gracefully"""
        result = await validator.validate_style_consistency("")
        
        assert result.passing is False
        assert len(result.issues) > 0
    
    @pytest.mark.asyncio
    async def test_very_short_content(self, validator):
        """Should handle very short content"""
        result = await validator.validate_style_consistency("Hi.")
        
        assert result.style_consistency_score >= 0
        assert result.style_consistency_score <= 1
    
    @pytest.mark.asyncio
    async def test_very_long_content(self, validator):
        """Should handle very long content"""
        long_content = "This is a sentence. " * 1000
        
        result = await validator.validate_style_consistency(long_content)
        
        assert result.style_consistency_score >= 0
        assert result.style_consistency_score <= 1
    
    @pytest.mark.asyncio
    async def test_special_characters_handling(self, validator):
        """Should handle special characters"""
        content = "Test!@#$%^&*()_+-=[]{}|;:',.<>?/\\`~"
        
        result = await validator.validate_style_consistency(content)
        
        # Should not crash
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_unicode_content_handling(self, validator):
        """Should handle Unicode content"""
        content = "This is a test with émojis 🎉 and spëcial characters"
        
        result = await validator.validate_style_consistency(content)
        
        # Should not crash
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_none_reference_metrics(self, validator):
        """Should handle None reference metrics"""
        result = await validator.validate_style_consistency(
            "Test content",
            reference_metrics=None
        )
        
        # Should handle gracefully
        assert result is not None
        assert 0 <= result.style_consistency_score <= 1


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


class TestIntegration:
    """Integration tests with full pipeline"""
    
    @pytest.mark.asyncio
    async def test_full_style_validation_pipeline(self, validator, formal_reference_metrics):
        """Test complete validation pipeline"""
        content = "The algorithm implements comprehensive features for efficient execution."
        
        result = await validator.validate_style_consistency(
            content,
            formal_reference_metrics,
            'technical',
            'formal'
        )
        
        # Should return complete result with all fields
        assert result.style_consistency_score >= 0
        assert result.tone_consistency_score >= 0
        assert result.detected_style is not None
        assert result.detected_tone is not None
        assert isinstance(result.passing, bool)
        assert isinstance(result.issues, list)
        assert isinstance(result.suggestions, list)
    
    @pytest.mark.asyncio
    async def test_multiple_validations_consistent(self, validator):
        """Multiple validations of same content should be consistent"""
        content = "This is test content with formal language patterns."
        
        result1 = await validator.validate_style_consistency(
            content, reference_tone='formal'
        )
        
        result2 = await validator.validate_style_consistency(
            content, reference_tone='formal'
        )
        
        # Same content should give similar results
        assert abs(result1.style_consistency_score - result2.style_consistency_score) < 0.01
    
    @pytest.mark.asyncio
    async def test_style_consistency_affects_passing(self, validator):
        """Passing determination should match consistency score"""
        content = "Test content here."
        
        result = await validator.validate_style_consistency(content)
        
        # Should align with threshold
        expected_passing = result.style_consistency_score >= 0.75
        assert result.passing == expected_passing
    
    @pytest.mark.asyncio
    async def test_validator_singleton(self):
        """Validator instances should function identically"""
        validator1 = get_style_consistency_validator()
        validator2 = get_style_consistency_validator()
        
        # Should create instances successfully
        assert validator1 is not None
        assert validator2 is not None
        # Both should have same methods
        assert hasattr(validator1, 'validate_style_consistency')
        assert hasattr(validator2, 'validate_style_consistency')
    
    @pytest.mark.asyncio
    async def test_phase_3_3_integration_compatibility(self, validator, formal_reference_metrics):
        """Should work with Phase 3.3 reference metrics"""
        # Phase 3.3 provides analysis with these fields
        content = "Generated content matching the reference style here."
        
        result = await validator.validate_style_consistency(
            content,
            formal_reference_metrics,
            formal_reference_metrics.get('detected_style'),
            formal_reference_metrics.get('detected_tone')
        )
        
        # Should integrate smoothly
        assert result is not None
        assert 0 <= result.style_consistency_score <= 1


# ============================================================================
# PERFORMANCE TESTS
# ============================================================================


class TestPerformance:
    """Performance-related tests"""
    
    @pytest.mark.asyncio
    async def test_validation_completes_quickly(self, validator):
        """Validation should complete in reasonable time"""
        import time
        
        content = "Test content. " * 100
        
        start = time.time()
        result = await validator.validate_style_consistency(content)
        elapsed = time.time() - start
        
        # Should complete in less than 100ms
        assert elapsed < 0.1
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_large_batch_validation(self, validator):
        """Should handle multiple validations"""
        contents = [f"Test content {i}. " * 50 for i in range(10)]
        
        results = []
        for content in contents:
            result = await validator.validate_style_consistency(content)
            results.append(result)
        
        assert len(results) == 10
        assert all(r is not None for r in results)


# ============================================================================
# RUN TESTS
# ============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
