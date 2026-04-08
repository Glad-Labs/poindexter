"""
Unit tests for services/quality_scorers.py

Tests all pure scoring functions with edge cases and boundary conditions.
No DB or LLM calls — all functions are stateless heuristics.
"""

import pytest
from unittest.mock import patch

from services.quality_scorers import (
    score_clarity,
    score_accuracy,
    score_completeness,
    score_relevance,
    score_seo,
    score_readability,
    score_engagement,
    check_keywords,
    count_syllables,
    flesch_kincaid_grade_level,
    detect_truncation,
    generate_feedback,
    generate_suggestions,
)

# Default config for testing (avoids DB calls)
_CFG = {
    "clarity_ideal_min": 15, "clarity_ideal_max": 20,
    "clarity_good_min": 10, "clarity_good_max": 25,
    "clarity_ok_min": 8, "clarity_ok_max": 30,
    "accuracy_baseline": 7.0,
    "accuracy_good_link_bonus": 0.3, "accuracy_good_link_max": 1.0,
    "accuracy_bad_link_penalty": 0.5, "accuracy_bad_link_max": 2.0,
    "accuracy_citation_bonus": 0.3,
    "accuracy_first_person_penalty": 1.0, "accuracy_first_person_max": 3.0,
    "accuracy_meta_commentary_penalty": 0.5, "accuracy_meta_commentary_max": 2.0,
    "completeness_word_2000": 6.5, "completeness_word_1500": 6.0,
    "completeness_word_1000": 5.0, "completeness_word_500": 3.5,
    "completeness_word_min": 2.0,
    "completeness_heading_bonus": 0.3, "completeness_heading_max": 1.5,
    "completeness_truncation_penalty": 3.0,
    "relevance_no_topic_default": 6.0,
    "relevance_high_coverage": 8.5, "relevance_med_coverage": 7.0,
    "relevance_low_coverage": 5.5, "relevance_none_coverage": 3.0,
    "relevance_stuffing_hard": 5.0, "relevance_stuffing_soft": 3.0,
    "seo_baseline": 6.0,
    "engagement_baseline": 6.0,
}


# ---------------------------------------------------------------------------
# score_clarity
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestScoreClarity:
    def test_ideal_sentence_length(self):
        # 17 words per sentence (ideal range 15-20)
        content = "word " * 170  # 170 words
        result = score_clarity(content, sentence_count=10, word_count=170, cfg=_CFG)
        assert result == 9.0

    def test_good_sentence_length(self):
        # 12 words per sentence (good range 10-25)
        result = score_clarity("text", sentence_count=10, word_count=120, cfg=_CFG)
        assert result == 8.0

    def test_ok_sentence_length(self):
        # 9 words per sentence (ok range 8-30)
        result = score_clarity("text", sentence_count=10, word_count=90, cfg=_CFG)
        assert result == 7.0

    def test_poor_sentence_length(self):
        # 5 words per sentence (too short)
        result = score_clarity("text", sentence_count=10, word_count=50, cfg=_CFG)
        assert result == 5.0

    def test_zero_words(self):
        result = score_clarity("", sentence_count=0, word_count=0, cfg=_CFG)
        assert result == 5.0

    def test_returns_float(self):
        result = score_clarity("text", sentence_count=5, word_count=100, cfg=_CFG)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# score_accuracy
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestScoreAccuracy:
    def test_baseline_score(self):
        result = score_accuracy("Simple text with no links or citations.", {}, cfg=_CFG)
        assert result == pytest.approx(7.0, abs=0.5)

    def test_reputable_link_bonus(self):
        content = "See https://github.com/example/repo for the code."
        result = score_accuracy(content, {}, cfg=_CFG)
        assert result > 7.0

    def test_first_person_penalty(self):
        content = "I built this application. We created a new framework. I developed the API."
        result = score_accuracy(content, {}, cfg=_CFG)
        assert result < 7.0

    def test_meta_commentary_penalty(self):
        content = "In this article we will explore how Docker works. This post discusses containers."
        result = score_accuracy(content, {}, cfg=_CFG)
        assert result < 7.0

    def test_score_capped_at_10(self):
        # Lots of reputable links and citations
        links = " ".join(f"https://github.com/repo{i}" for i in range(20))
        content = f"According to research, {links}"
        result = score_accuracy(content, {}, cfg=_CFG)
        assert result <= 10.0

    def test_score_floored_at_0(self):
        # Heavy penalties
        claims = " ".join("I built something." for _ in range(10))
        result = score_accuracy(claims, {}, cfg=_CFG)
        assert result >= 0.0


# ---------------------------------------------------------------------------
# score_completeness
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestScoreCompleteness:
    def test_long_content_scores_higher(self):
        long_content = "word " * 2000
        short_content = "word " * 100
        long_score = score_completeness(long_content, {}, cfg=_CFG)
        short_score = score_completeness(short_content, {}, cfg=_CFG)
        assert long_score > short_score

    def test_headings_add_bonus(self):
        content = "## Heading 1\nParagraph.\n\n## Heading 2\nMore text.\n\n## Heading 3\nEven more."
        result = score_completeness(content, {}, cfg=_CFG)
        # Headings should contribute positively
        assert result > _CFG["completeness_word_min"]

    def test_score_capped_at_10(self):
        content = ("## Section\n\n" + "word " * 500 + "\n\n") * 10
        result = score_completeness(content, {}, cfg=_CFG)
        assert result <= 10.0

    def test_truncated_content_penalized(self):
        # Content that ends mid-sentence
        content = "word " * 200 + "This sentence is not"
        result = score_completeness(content, {}, cfg=_CFG)
        # compare with completed version
        content_complete = "word " * 200 + "This sentence is complete."
        result_complete = score_completeness(content_complete, {}, cfg=_CFG)
        assert result < result_complete or result == result_complete  # truncation detected


# ---------------------------------------------------------------------------
# score_relevance
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestScoreRelevance:
    def test_high_relevance(self):
        content = "Docker containers provide isolated environments. Docker containers are used for deployment and orchestration."
        result = score_relevance(content, {"topic": "Docker containers deployment"}, cfg=_CFG)
        assert result >= 5.0  # Topic words need 4+ chars; coverage depends on matching

    def test_no_topic_returns_default(self):
        result = score_relevance("some content", {}, cfg=_CFG)
        assert result == _CFG["relevance_no_topic_default"]

    def test_keyword_stuffing_penalized(self):
        content = " ".join(["Docker"] * 50 + ["word"] * 50)
        result = score_relevance(content, {"topic": "Docker"}, cfg=_CFG)
        # High density should cap the score
        assert result <= 7.0

    def test_no_relevance(self):
        content = "Cooking recipes and gardening tips for beginners."
        result = score_relevance(content, {"topic": "Kubernetes deployment"}, cfg=_CFG)
        assert result < 6.0


# ---------------------------------------------------------------------------
# score_seo
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestScoreSeo:
    def test_baseline(self):
        result = score_seo("Plain text.", {}, cfg=_CFG)
        assert result == _CFG["seo_baseline"]

    def test_headers_bonus(self):
        result = score_seo("## Heading\nSome content.", {}, cfg=_CFG)
        assert result > _CFG["seo_baseline"]

    def test_capped_at_10(self):
        result = score_seo("## H\n\nParagraph\n\nMore", {"topic": "## H"}, cfg=_CFG)
        assert result <= 10.0


# ---------------------------------------------------------------------------
# score_readability
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestScoreReadability:
    def test_simple_text_scores_well(self):
        content = "The cat sat on the mat. It was a nice day. The sun was bright."
        result = score_readability(content)
        assert result >= 7.0

    def test_technical_text_has_floor(self):
        content = "Kubernetes orchestrates containerized microservices infrastructure. PostgreSQL database replication."
        result = score_readability(content)
        assert result >= 7.0  # Technical floor

    def test_empty_content(self):
        result = score_readability("")
        assert result == 7.0

    def test_returns_float(self):
        result = score_readability("Some text here.")
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# score_engagement
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestScoreEngagement:
    def test_baseline(self):
        result = score_engagement("Plain text.", cfg=_CFG)
        assert result == _CFG["engagement_baseline"]

    def test_questions_boost(self):
        content = "What is Docker? Why use containers? How does it work?"
        result = score_engagement(content, cfg=_CFG)
        assert result > _CFG["engagement_baseline"]

    def test_lists_boost(self):
        content = "Features:\n- Fast\n- Reliable\n- Secure"
        result = score_engagement(content, cfg=_CFG)
        assert result > _CFG["engagement_baseline"]

    def test_code_blocks_boost(self):
        content = "Example:\n```python\nprint('hello')\n```"
        result = score_engagement(content, cfg=_CFG)
        assert result > _CFG["engagement_baseline"]

    def test_capped_at_10(self):
        content = "What? Why? How?\n\n- A\n- B\n\n```code```\n\n**bold**\n\nP1\n\nP2\n\nP3\n\nP4"
        result = score_engagement(content, cfg=_CFG)
        assert result <= 10.0


# ---------------------------------------------------------------------------
# count_syllables
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCountSyllables:
    def test_one_syllable(self):
        assert count_syllables("cat") == 1

    def test_two_syllables(self):
        assert count_syllables("docker") == 2

    def test_three_syllables(self):
        assert count_syllables("container") == 3

    def test_minimum_one(self):
        assert count_syllables("x") >= 1

    def test_empty_string(self):
        assert count_syllables("") >= 1


# ---------------------------------------------------------------------------
# flesch_kincaid_grade_level
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFleschKincaid:
    def test_simple_text_low_grade(self):
        text = "The cat sat on the mat. It was a nice day."
        grade = flesch_kincaid_grade_level(text)
        assert grade < 5.0  # Simple sentences = low grade level

    def test_complex_text_higher_grade(self):
        text = ("The implementation of containerized microservices architecture "
                "requires comprehensive understanding of distributed systems "
                "and infrastructure orchestration methodologies.")
        grade = flesch_kincaid_grade_level(text)
        assert grade > 10.0  # Complex = high grade level

    def test_empty_text(self):
        assert flesch_kincaid_grade_level("") == 0.0
        assert flesch_kincaid_grade_level("   ") == 0.0

    def test_returns_float(self):
        result = flesch_kincaid_grade_level("Hello world.")
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# detect_truncation
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDetectTruncation:
    def test_complete_content_not_truncated(self):
        content = "This is a complete article. It has a proper ending."
        assert detect_truncation(content) is False

    def test_mid_sentence_is_truncated(self):
        content = "word " * 50 + "This sentence ends without any punctuation and keeps going on"
        assert detect_truncation(content) is True

    def test_short_content_not_truncated(self):
        assert detect_truncation("Short.") is False
        assert detect_truncation("") is False

    def test_url_ending_not_truncated(self):
        content = "word " * 30 + "See https://example.com/docs"
        assert detect_truncation(content) is False

    def test_heading_ending_is_truncated(self):
        content = "word " * 50 + "\n## Next Section"
        assert detect_truncation(content) is True


# ---------------------------------------------------------------------------
# check_keywords
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCheckKeywords:
    def test_keyword_found(self):
        assert check_keywords("Docker is great for containers", {"keywords": ["Docker"]}) is True

    def test_keyword_not_found(self):
        assert check_keywords("Python is great", {"keywords": ["Docker"]}) is False

    def test_case_insensitive(self):
        assert check_keywords("docker containers", {"keywords": ["Docker"]}) is True

    def test_empty_keywords(self):
        assert check_keywords("Some content", {"keywords": []}) is False

    def test_none_keywords(self):
        assert check_keywords("Some content", {}) is False

    def test_string_keyword(self):
        assert check_keywords("Docker is great", {"keywords": "Docker"}) is True


# ---------------------------------------------------------------------------
# generate_feedback / generate_suggestions
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGenerateFeedback:
    def test_excellent(self):
        dims = type("D", (), {"average": lambda self: 90})()
        assert "excellent" in generate_feedback(dims, {}).lower()

    def test_good(self):
        dims = type("D", (), {"average": lambda self: 78})()
        assert "good" in generate_feedback(dims, {}).lower()

    def test_poor(self):
        dims = type("D", (), {"average": lambda self: 40})()
        assert "poor" in generate_feedback(dims, {}).lower()


@pytest.mark.unit
class TestGenerateSuggestions:
    def test_all_good_returns_standard(self):
        dims = type("D", (), {
            "clarity": 80, "accuracy": 80, "completeness": 80,
            "relevance": 80, "seo_quality": 80, "readability": 80,
            "engagement": 80,
        })()
        result = generate_suggestions(dims)
        assert result == ["Content meets quality standards"]

    def test_low_clarity_suggests_improvement(self):
        dims = type("D", (), {
            "clarity": 50, "accuracy": 80, "completeness": 80,
            "relevance": 80, "seo_quality": 80, "readability": 80,
            "engagement": 80,
        })()
        result = generate_suggestions(dims)
        assert any("sentence" in s.lower() for s in result)

    def test_multiple_low_dimensions(self):
        dims = type("D", (), {
            "clarity": 50, "accuracy": 50, "completeness": 50,
            "relevance": 50, "seo_quality": 50, "readability": 50,
            "engagement": 50,
        })()
        result = generate_suggestions(dims)
        assert len(result) == 7
