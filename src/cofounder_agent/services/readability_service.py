"""
Readability Service

Provides accurate readability metrics including:
- Flesch-Kincaid Reading Ease (improved syllable counting)
- Flesch-Kincaid Grade Level
- Paragraph length validation
- Sentence variety analysis
- Passive voice detection
"""

import re
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ReadabilityMetrics:
    """Complete readability analysis"""
    flesch_reading_ease: float  # 0-100 (higher = easier)
    flesch_grade_level: float  # Grade equivalent
    syllables_per_word: float  # Average
    words_per_sentence: float  # Average
    sentences_per_paragraph: float  # Average

    # Detailed analysis
    total_words: int
    total_sentences: int
    total_paragraphs: int
    avg_sentence_length: float
    avg_paragraph_length: float

    # Issues found
    has_long_paragraphs: bool
    has_orphan_paragraphs: bool
    has_sentence_length_variety: bool
    passive_voice_percentage: float

    # Overall assessment (0-100)
    overall_score: float
    interpretation: str  # Easy, Standard, Difficult, etc.


class ReadabilityService:
    """
    Provides accurate readability scoring using improved metrics
    """

    # CMU Pronouncing Dictionary for accurate syllable counting
    # This is a simplified version - in production, use NLTK
    SYLLABLE_DICTIONARY = {}  # Populated on first use

    # Ideal ranges
    IDEAL_FLESCH_EASE = (60, 70)  # Standard business writing
    IDEAL_PARAGRAPH_WORDS = (100, 200)  # Words per paragraph
    IDEAL_SENTENCE_LENGTH = (15, 20)  # Words per sentence
    IDEAL_SENTENCES_PER_PARA = (4, 7)  # Sentences per paragraph

    def __init__(self):
        self.logger = logger
        self._initialize_syllable_dictionary()

    def _initialize_syllable_dictionary(self):
        """Initialize basic syllable dictionary for English words"""
        # Common words with known syllable counts
        # This is a fallback - better approach is to use NLTK
        self.SYLLABLE_DICTIONARY = {
            # Single syllable
            "the": 1, "a": 1, "is": 1, "and": 1, "or": 1, "to": 1,
            "from": 1, "for": 1, "with": 1, "by": 1, "at": 1, "it": 1,
            # Two syllables
            "about": 2, "also": 2, "after": 2, "before": 2, "between": 2,
            "different": 3, "important": 3, "information": 4, "understand": 3,
            "development": 4, "technology": 4, "documentation": 4,
        }

    def analyze(self, content: str) -> ReadabilityMetrics:
        """
        Analyze content readability

        Args:
            content: Text to analyze

        Returns:
            ReadabilityMetrics with complete analysis
        """
        # Extract sentences and words
        sentences = self._extract_sentences(content)
        paragraphs = self._extract_paragraphs(content)
        words = self._extract_words(content)

        total_words = len(words)
        total_sentences = len(sentences)
        total_paragraphs = len(paragraphs)

        if total_words == 0 or total_sentences == 0:
            return self._create_empty_metrics()

        # Calculate syllables
        syllables = sum(self._count_syllables(word) for word in words)

        # Calculate averages
        syllables_per_word = syllables / total_words if total_words > 0 else 0
        words_per_sentence = total_words / total_sentences if total_sentences > 0 else 0
        sentences_per_paragraph = (
            total_sentences / total_paragraphs if total_paragraphs > 0 else 0
        )
        avg_sentence_length = words_per_sentence
        avg_paragraph_length = total_words / total_paragraphs if total_paragraphs > 0 else 0

        # Calculate Flesch Reading Ease
        flesch_ease = self._calculate_flesch_reading_ease(
            total_words, total_sentences, syllables
        )

        # Calculate Flesch-Kincaid Grade Level
        flesch_grade = self._calculate_flesch_grade_level(
            total_words, total_sentences, syllables
        )

        # Analyze structure
        has_long_paragraphs = any(
            len(p.split()) > 250 for p in paragraphs
        )
        has_orphan_paragraphs = any(
            len(p.split(".")) == 1 for p in paragraphs
        )

        # Check sentence variety (different lengths)
        sentence_lengths = [len(s.split()) for s in sentences]
        has_variety = len(set(sentence_lengths)) > (len(sentences) / 2)

        # Detect passive voice
        passive_percentage = self._analyze_passive_voice(sentences)

        # Calculate overall score (0-100)
        overall = self._calculate_overall_score(
            flesch_ease,
            has_long_paragraphs,
            has_orphan_paragraphs,
            has_variety,
            passive_percentage,
        )

        # Get interpretation
        interpretation = self._interpret_flesch_score(flesch_ease)

        return ReadabilityMetrics(
            flesch_reading_ease=flesch_ease,
            flesch_grade_level=flesch_grade,
            syllables_per_word=syllables_per_word,
            words_per_sentence=words_per_sentence,
            sentences_per_paragraph=sentences_per_paragraph,
            total_words=total_words,
            total_sentences=total_sentences,
            total_paragraphs=total_paragraphs,
            avg_sentence_length=avg_sentence_length,
            avg_paragraph_length=avg_paragraph_length,
            has_long_paragraphs=has_long_paragraphs,
            has_orphan_paragraphs=has_orphan_paragraphs,
            has_sentence_length_variety=has_variety,
            passive_voice_percentage=passive_percentage,
            overall_score=overall,
            interpretation=interpretation,
        )

    def _extract_sentences(self, text: str) -> List[str]:
        """Extract sentences from text"""
        # Simple sentence splitting on ., !, ?
        sentences = re.split(r'[.!?]+', text)
        return [s.strip() for s in sentences if s.strip()]

    def _extract_paragraphs(self, text: str) -> List[str]:
        """Extract paragraphs (separated by blank lines)"""
        paragraphs = text.split('\n\n')
        return [p.strip() for p in paragraphs if p.strip()]

    def _extract_words(self, text: str) -> List[str]:
        """Extract words from text"""
        # Remove punctuation, convert to lowercase
        words = re.findall(r'\b[a-z]+\b', text.lower())
        return words

    def _count_syllables(self, word: str) -> int:
        """
        Count syllables in a word

        Uses dictionary lookup with fallback to heuristic estimation.
        This is more accurate than pure heuristics.
        """
        word = word.lower()

        # Check dictionary first
        if word in self.SYLLABLE_DICTIONARY:
            return self.SYLLABLE_DICTIONARY[word]

        # Fallback: improved heuristic estimation
        return self._estimate_syllables_heuristic(word)

    def _estimate_syllables_heuristic(self, word: str) -> int:
        """
        Estimate syllables using improved heuristics

        Better than simple vowel counting:
        - Considers silent e
        - Accounts for vowel combinations
        - Uses common ending patterns
        """
        word = word.lower()

        # Minimum 1 syllable
        if len(word) <= 3:
            return 1

        # Count vowel groups (more accurate than individual vowels)
        vowel_pattern = r'[aeiouy]+'
        matches = len(re.findall(vowel_pattern, word))
        syllable_count = matches

        # Adjust for silent e at end
        if word.endswith('e'):
            syllable_count -= 1

        # Adjust for common endings
        if word.endswith(('le', 'ey', 'ed')):
            if not word.endswith('eed'):
                syllable_count += 1

        # Some words ending in -tion, -sion
        if word.endswith(('tion', 'sion')):
            syllable_count = max(syllable_count, 2)

        # Ensure at least 1 syllable
        return max(1, syllable_count)

    def _calculate_flesch_reading_ease(
        self, words: int, sentences: int, syllables: int
    ) -> float:
        """
        Calculate Flesch Reading Ease score (0-100)

        Formula: 206.835 - 1.015*(words/sentences) - 84.6*(syllables/words)

        Score interpretation:
        - 90-100: Very Easy (5th grade)
        - 80-90: Easy (6th grade)
        - 70-80: Fairly Easy (7th grade)
        - 60-70: Standard (8th-9th grade)
        - 50-60: Fairly Difficult (10th-12th grade)
        - 30-50: Difficult (College)
        - 0-30: Very Difficult (College graduate)
        """
        if words == 0 or sentences == 0:
            return 0.0

        score = (
            206.835
            - (1.015 * (words / sentences))
            - (84.6 * (syllables / words))
        )

        # Clamp score to 0-100 range
        return max(0.0, min(100.0, score))

    def _calculate_flesch_grade_level(
        self, words: int, sentences: int, syllables: int
    ) -> float:
        """
        Calculate Flesch-Kincaid Grade Level

        Formula: 0.39*(words/sentences) + 11.8*(syllables/words) - 15.59

        Output is grade level equivalent (e.g., 8.5 = middle of 8th grade)
        """
        if words == 0 or sentences == 0:
            return 0.0

        score = (
            (0.39 * (words / sentences))
            + (11.8 * (syllables / words))
            - 15.59
        )

        # Clamp to reasonable range (0-18+)
        return max(0.0, score)

    def _analyze_passive_voice(self, sentences: List[str]) -> float:
        """
        Detect passive voice percentage in sentences

        Looks for patterns like "was + verb" or "is + verb"
        """
        if not sentences:
            return 0.0

        passive_count = 0

        for sentence in sentences:
            # Simple pattern: "is/was/are/been + past participle"
            if re.search(
                r'\b(is|was|are|been|be|being)\s+\w+(?:ed|en)\b',
                sentence.lower()
            ):
                passive_count += 1

        return (passive_count / len(sentences)) * 100

    def _calculate_overall_score(
        self,
        flesch_ease: float,
        has_long_paragraphs: bool,
        has_orphan_paragraphs: bool,
        has_variety: bool,
        passive_percentage: float,
    ) -> float:
        """
        Calculate overall readability score (0-100)

        Factors:
        - Flesch Reading Ease (40% weight)
        - Paragraph structure (25%)
        - Sentence variety (20%)
        - Passive voice (15%)
        """
        score = 0.0

        # Flesch score component (40%)
        # Convert 0-100 to 0-40 contribution
        score += (flesch_ease / 100) * 40

        # Paragraph structure (25%)
        para_score = 25.0
        if has_long_paragraphs:
            para_score -= 10
        if has_orphan_paragraphs:
            para_score -= 10
        score += para_score

        # Sentence variety (20%)
        score += 20.0 if has_variety else 10.0

        # Passive voice (15%)
        # Less is better (max 20% passive)
        passive_component = max(0, (20 - passive_percentage) / 20) * 15
        score += passive_component

        return min(100.0, score)

    def _interpret_flesch_score(self, score: float) -> str:
        """Convert Flesch score to readability interpretation"""
        if score >= 90:
            return "Very Easy (5th grade)"
        elif score >= 80:
            return "Easy (6th grade)"
        elif score >= 70:
            return "Fairly Easy (7th grade)"
        elif score >= 60:
            return "Standard (8th-9th grade)"
        elif score >= 50:
            return "Fairly Difficult (10th-12th grade)"
        elif score >= 30:
            return "Difficult (College)"
        else:
            return "Very Difficult (College graduate)"

    def _create_empty_metrics(self) -> ReadabilityMetrics:
        """Create empty metrics for empty content"""
        return ReadabilityMetrics(
            flesch_reading_ease=0.0,
            flesch_grade_level=0.0,
            syllables_per_word=0.0,
            words_per_sentence=0.0,
            sentences_per_paragraph=0.0,
            total_words=0,
            total_sentences=0,
            total_paragraphs=0,
            avg_sentence_length=0.0,
            avg_paragraph_length=0.0,
            has_long_paragraphs=False,
            has_orphan_paragraphs=False,
            has_sentence_length_variety=False,
            passive_voice_percentage=0.0,
            overall_score=0.0,
            interpretation="No content to analyze",
        )
