"""
Unit tests for ReadabilityService.

All tests are pure-function — zero DB, LLM, or network calls.
Tests verify the Flesch-Kincaid formulas, syllable estimation, and overall scoring.
"""

import pytest

from services.readability_service import ReadabilityMetrics, ReadabilityService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service() -> ReadabilityService:
    return ReadabilityService()


# A simple, readable paragraph — short sentences, common words
SIMPLE_TEXT = (
    "The cat sat on the mat. "
    "It was a sunny day. "
    "The bird sang in the tree. "
    "She smiled and waved at him. "
    "They walked home together."
)

# Dense academic text — long sentences, polysyllabic words
COMPLEX_TEXT = (
    "The proliferation of multidisciplinary methodologies in contemporary "
    "epistemological frameworks necessitates a comprehensive reevaluation of "
    "foundational philosophical assumptions underpinning modern scientific discourse. "
    "Extant paradigmatic structures inadequately accommodate the increasingly "
    "sophisticated computational architectures characterizing twenty-first-century "
    "technological implementation strategies."
)


# ---------------------------------------------------------------------------
# Basic analysis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBasicAnalysis:
    def test_returns_readability_metrics_type(self, service):
        result = service.analyze(SIMPLE_TEXT)
        assert isinstance(result, ReadabilityMetrics)

    def test_empty_string_returns_empty_metrics(self, service):
        result = service.analyze("")
        assert result.total_words == 0
        assert result.flesch_reading_ease == 0.0

    def test_whitespace_only_returns_empty_metrics(self, service):
        result = service.analyze("   \n\n  ")
        assert result.total_words == 0

    def test_word_count_is_positive(self, service):
        result = service.analyze(SIMPLE_TEXT)
        # SIMPLE_TEXT has ~25-27 words depending on tokenizer
        assert result.total_words > 20

    def test_sentence_count_is_correct(self, service):
        result = service.analyze(SIMPLE_TEXT)
        assert result.total_sentences == 5

    def test_overall_score_is_between_0_and_100(self, service):
        result = service.analyze(SIMPLE_TEXT)
        assert 0.0 <= result.overall_score <= 100.0


# ---------------------------------------------------------------------------
# Flesch Reading Ease
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFleschReadingEase:
    def test_simple_text_has_higher_score_than_complex(self, service):
        simple = service.analyze(SIMPLE_TEXT)
        complex_ = service.analyze(COMPLEX_TEXT)
        # Simple text should be easier (higher score)
        assert simple.flesch_reading_ease > complex_.flesch_reading_ease

    def test_flesch_score_is_clamped_to_0_to_100(self, service):
        result = service.analyze(SIMPLE_TEXT)
        assert 0.0 <= result.flesch_reading_ease <= 100.0

    def test_complex_text_score_is_lower_than_60(self, service):
        # Complex academic text should score below 60 (Fairly Difficult or worse)
        result = service.analyze(COMPLEX_TEXT)
        assert result.flesch_reading_ease < 60.0

    def test_known_flesch_calculation(self, service):
        # Direct calculation test for the internal formula:
        # 5 words, 1 sentence, 5 syllables => 206.835 - 1.015*(5/1) - 84.6*(5/5)
        # = 206.835 - 5.075 - 84.6 = 117.16 → clamped to 100
        score = service._calculate_flesch_reading_ease(5, 1, 5)
        assert score == pytest.approx(100.0)

    def test_zero_words_returns_zero(self, service):
        score = service._calculate_flesch_reading_ease(0, 0, 0)
        assert score == 0.0


# ---------------------------------------------------------------------------
# Flesch-Kincaid Grade Level
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFleschGradeLevel:
    def test_simple_text_has_lower_grade_than_complex(self, service):
        simple = service.analyze(SIMPLE_TEXT)
        complex_ = service.analyze(COMPLEX_TEXT)
        assert simple.flesch_grade_level < complex_.flesch_grade_level

    def test_grade_level_is_non_negative(self, service):
        result = service.analyze(SIMPLE_TEXT)
        assert result.flesch_grade_level >= 0.0

    def test_zero_inputs_returns_zero(self, service):
        grade = service._calculate_flesch_grade_level(0, 0, 0)
        assert grade == 0.0


# ---------------------------------------------------------------------------
# Passive voice detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPassiveVoiceDetection:
    def test_active_sentences_return_low_passive_percentage(self, service):
        active = ["The dog bit the man.", "She runs every morning.", "They built the house."]
        percentage = service._analyze_passive_voice(active)
        assert percentage == 0.0

    def test_passive_sentences_detected(self, service):
        passive = [
            "The report was written by the team.",
            "Errors were found in the code.",
        ]
        percentage = service._analyze_passive_voice(passive)
        assert percentage > 0.0

    def test_empty_sentences_returns_zero(self, service):
        assert service._analyze_passive_voice([]) == 0.0


# ---------------------------------------------------------------------------
# Interpretation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInterpretation:
    def test_high_score_gives_easy_interpretation(self, service):
        # 95 score → contains "easy" in the interpretation string
        interp = service._interpret_flesch_score(95.0)
        assert "easy" in interp.lower()

    def test_low_score_gives_difficult_interpretation(self, service):
        # 15 score → contains "difficult" in the interpretation string
        interp = service._interpret_flesch_score(15.0)
        assert "difficult" in interp.lower()

    def test_medium_score_gives_standard_interpretation(self, service):
        # 65 score → contains "standard" or "easy" in the interpretation string
        interp = service._interpret_flesch_score(65.0)
        assert "standard" in interp.lower() or "easy" in interp.lower()
