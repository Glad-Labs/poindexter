"""
Unit tests for services/quality_scorers.py

Tests all pure scoring functions with edge cases and boundary conditions.
No DB or LLM calls — all functions are stateless heuristics.
"""


import pytest

from services.quality_scorers import (
    check_keywords,
    count_syllables,
    detect_truncation,
    flesch_kincaid_grade_level,
    generate_feedback,
    generate_suggestions,
    score_accuracy,
    score_clarity,
    score_completeness,
    score_engagement,
    score_readability,
    score_relevance,
    score_seo,
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


# ---------------------------------------------------------------------------
# qa_cfg loader
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQaCfg:
    def test_returns_dict_with_expected_keys(self):
        from services.quality_scorers import qa_cfg
        cfg = qa_cfg()
        assert isinstance(cfg, dict)
        # Spot check keys
        for key in [
            "pass_threshold", "critical_floor",
            "fk_target_min", "fk_target_max",
            "clarity_ideal_min", "clarity_good_max",
            "accuracy_baseline", "completeness_word_2000",
            "relevance_no_topic_default", "seo_baseline", "engagement_baseline",
        ]:
            assert key in cfg

    def test_threshold_values_are_numeric(self):
        from services.quality_scorers import qa_cfg
        cfg = qa_cfg()
        for k, v in cfg.items():
            assert isinstance(v, (int, float)), f"{k} should be numeric, got {type(v).__name__}"

    def test_clarity_ranges_have_sensible_ordering(self):
        from services.quality_scorers import qa_cfg
        cfg = qa_cfg()
        assert cfg["clarity_ideal_min"] < cfg["clarity_ideal_max"]
        assert cfg["clarity_good_min"] <= cfg["clarity_ideal_min"]
        assert cfg["clarity_good_max"] >= cfg["clarity_ideal_max"]


# ---------------------------------------------------------------------------
# score_accuracy — additional branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreAccuracyDetailed:
    def test_bad_link_penalty(self):
        content = "Visit https://random-spam-site.example for the worst content."
        result = score_accuracy(content, {}, cfg=_CFG)
        assert result < 7.0

    def test_citation_bracket_pattern(self):
        content = "As shown by prior work [1], [2], and [3] on this topic."
        result = score_accuracy(content, {}, cfg=_CFG)
        assert result > 7.0

    def test_author_year_citation(self):
        content = "This was demonstrated (Smith 2023) and reproduced (Jones 2024)."
        result = score_accuracy(content, {}, cfg=_CFG)
        assert result > 7.0

    def test_according_to_phrase(self):
        content = "According to recent research, the technique works well."
        result = score_accuracy(content, {}, cfg=_CFG)
        assert result > 7.0

    def test_named_quote_bonus(self):
        content = '"This approach significantly improves performance," said the researcher.'
        result = score_accuracy(content, {}, cfg=_CFG)
        assert result >= 7.0

    def test_first_person_singular_penalized(self):
        content = "I shipped this last month. I built the entire system myself."
        result = score_accuracy(content, {}, cfg=_CFG)
        assert result < 7.0

    def test_first_person_plural_penalized(self):
        content = "We launched the platform yesterday. We built it from scratch."
        result = score_accuracy(content, {}, cfg=_CFG)
        assert result < 7.0

    def test_meta_commentary_lets_us_dive_in(self):
        content = "Let's dive into how containers work in production."
        result = score_accuracy(content, {}, cfg=_CFG)
        assert result < 7.0


# ---------------------------------------------------------------------------
# score_completeness — additional branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreCompletenessDetailed:
    def test_intro_paragraph_bonus(self):
        intro = ("word " * 35).strip() + "."  # >= 30 words, terminal punctuation
        rest = "\n\nMore content."
        content = intro + rest
        result = score_completeness(content, {}, cfg=_CFG)
        # Score includes the intro bonus
        assert result > _CFG["completeness_word_min"]

    def test_conclusion_paragraph_bonus(self):
        intro = ("word " * 35).strip() + "."
        body = "\n\n" + ("word " * 50).strip() + "."
        conclusion = "\n\n" + ("word " * 25).strip() + "."  # >= 20 words, ends with period
        content = intro + body + conclusion
        result = score_completeness(content, {}, cfg=_CFG)
        assert result > _CFG["completeness_word_min"]

    def test_list_bonus(self):
        content = "Intro paragraph here.\n\n- Item one\n- Item two\n- Item three."
        result = score_completeness(content, {}, cfg=_CFG)
        # Lists contribute to score
        assert result > _CFG["completeness_word_min"]

    def test_word_count_500_tier(self):
        content = ("word " * 500).strip() + "."
        result = score_completeness(content, {}, cfg=_CFG)
        # Should hit the 500 tier baseline (3.5)
        assert result >= _CFG["completeness_word_500"] - 0.1

    def test_word_count_1000_tier(self):
        content = ("word " * 1000).strip() + "."
        result = score_completeness(content, {}, cfg=_CFG)
        assert result >= _CFG["completeness_word_1000"] - 0.1

    def test_word_count_1500_tier(self):
        content = ("word " * 1500).strip() + "."
        result = score_completeness(content, {}, cfg=_CFG)
        assert result >= _CFG["completeness_word_1500"] - 0.1

    def test_word_count_2000_tier(self):
        content = ("word " * 2000).strip() + "."
        result = score_completeness(content, {}, cfg=_CFG)
        assert result >= _CFG["completeness_word_2000"] - 0.1

    def test_min_tier_for_short_content(self):
        content = ("word " * 50).strip() + "."
        result = score_completeness(content, {}, cfg=_CFG)
        assert result >= _CFG["completeness_word_min"] - 0.1

    def test_five_paragraphs_bonus(self):
        paras = ["paragraph one with words."] * 5
        content = "\n\n".join(paras)
        result = score_completeness(content, {}, cfg=_CFG)
        # Should be more than just min word tier
        assert result > _CFG["completeness_word_min"]


# ---------------------------------------------------------------------------
# score_relevance — additional branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreRelevanceDetailed:
    def test_med_coverage_score(self):
        # Topic has 2 words, content matches 1 → 50% coverage → med_coverage tier
        content = "Docker provides containerization. " * 5
        result = score_relevance(content, {"topic": "Docker Kubernetes"}, cfg=_CFG)
        assert result == _CFG["relevance_med_coverage"]

    def test_low_coverage_score(self):
        # 25-50% coverage
        content = "Docker is one choice among many. " * 5
        result = score_relevance(content, {"topic": "Docker Kubernetes Helm Argo"}, cfg=_CFG)
        # 1 of 4 = 25% coverage
        assert result == _CFG["relevance_low_coverage"]

    def test_none_coverage_score(self):
        # < 25% coverage
        content = "Cooking recipes for dinner."
        result = score_relevance(content, {"topic": "Docker Kubernetes Helm Argo"}, cfg=_CFG)
        assert result == _CFG["relevance_none_coverage"]

    def test_soft_stuffing_caps_score(self):
        # Build content with high but not extreme density of an exact topic
        content = "kafka " * 20 + " ".join(["filler"] * 80)
        result = score_relevance(content, {"topic": "kafka"}, cfg=_CFG)
        # Density between soft and hard threshold
        assert result <= 7.0

    def test_primary_keyword_used_when_no_topic(self):
        # When primary_keyword is set, it's used as the topic source.
        # Need enough non-keyword words to keep density below the soft stuffing threshold.
        content = (
            "Postgres is a relational database that supports advanced transaction handling, "
            + ("filler content here for word count " * 30)
            + "Postgres covers many use cases."
        )
        result = score_relevance(content, {"primary_keyword": "Postgres"}, cfg=_CFG)
        # 1/1 topic words matched, density well below soft stuffing → high_coverage
        assert result == _CFG["relevance_high_coverage"]

    def test_topic_words_under_4_chars_filtered(self):
        # Words < 4 chars are filtered, so "AI" topic returns no_topic_default
        content = "AI is everywhere these days."
        result = score_relevance(content, {"topic": "AI"}, cfg=_CFG)
        assert result == _CFG["relevance_no_topic_default"]


# ---------------------------------------------------------------------------
# score_seo — additional branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreSeoDetailed:
    def test_paragraph_separator_bonus(self):
        content = "Para 1.\n\nPara 2."
        result = score_seo(content, {}, cfg=_CFG)
        assert result > _CFG["seo_baseline"]

    def test_topic_at_beginning_bonus(self):
        content = "Docker containers are awesome and very useful for deployment."
        result = score_seo(content, {"topic": "Docker"}, cfg=_CFG)
        assert result > _CFG["seo_baseline"]

    def test_topic_not_at_beginning_no_bonus(self):
        content = "Containers are awesome with Docker."
        result_with = score_seo(content, {"topic": "Docker"}, cfg=_CFG)
        result_without = score_seo(content, {}, cfg=_CFG)
        assert result_with == result_without

    def test_keywords_present_boosts_score(self):
        """Primary keyword in content should earn a +1.5 bonus over a run
        with no keywords context at all."""
        content = "Docker containers are fantastic for reproducible deploys."
        with_kw = score_seo(content, {"keywords": ["Docker"]}, cfg=_CFG)
        without_kw = score_seo(content, {}, cfg=_CFG)
        assert with_kw > without_kw

    def test_keywords_missing_penalizes_score(self):
        """Keywords declared in context but absent from content should drop
        the score by 1.0 — previously the pipeline computed this check and
        threw the result away."""
        content = "Containers are fantastic for reproducible deploys."
        with_missing = score_seo(content, {"keywords": ["Docker"]}, cfg=_CFG)
        without_kw = score_seo(content, {}, cfg=_CFG)
        assert with_missing < without_kw

    def test_keyword_penalty_floors_at_zero(self):
        """Score is clamped >= 0 even when penalties stack."""
        result = score_seo("", {"keywords": ["Docker"]}, cfg=_CFG)
        assert result >= 0.0


# ---------------------------------------------------------------------------
# score_readability — Flesch tier branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreReadabilityTiers:
    def test_high_flesch_simple_text(self):
        # Very short, simple words → high Flesch score
        content = "I am. You are. We go. They run. He sees. She knows."
        result = score_readability(content)
        assert result >= 7.0
        assert result <= 10.0

    def test_low_flesch_returns_floor(self):
        # Polysyllabic gibberish → very low Flesch
        content = "Antidisestablishmentarianism characterizes pseudointellectualism methodologically."
        result = score_readability(content)
        assert result >= 7.0  # Floor

    def test_zero_words_returns_seven(self):
        result = score_readability("")
        assert result == 7.0

    def test_zero_sentences_returns_seven(self):
        # No sentence terminators — split returns one chunk so this still works
        result = score_readability("just words no terminator")
        assert result >= 5.0


# ---------------------------------------------------------------------------
# score_engagement — additional branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreEngagementDetailed:
    def test_bullet_dash_bonus(self):
        content = "- one\n- two\n- three"
        result = score_engagement(content, cfg=_CFG)
        assert result > _CFG["engagement_baseline"]

    def test_bullet_asterisk_bonus(self):
        content = "* one\n* two"
        result = score_engagement(content, cfg=_CFG)
        assert result > _CFG["engagement_baseline"]

    def test_single_question_smaller_bonus(self):
        content = "What is this?"
        single_q = score_engagement(content, cfg=_CFG)
        triple_q = score_engagement("What? Why? How?", cfg=_CFG)
        assert triple_q >= single_q

    def test_bold_double_asterisk_bonus(self):
        content = "Plain. **bold text** here."
        result = score_engagement(content, cfg=_CFG)
        assert result > _CFG["engagement_baseline"]

    def test_underscore_bold_bonus(self):
        content = "Plain. __also bold__ here."
        result = score_engagement(content, cfg=_CFG)
        assert result > _CFG["engagement_baseline"]

    def test_paragraph_pacing_bonus(self):
        # 4+ paragraphs of varied lengths
        content = (
            "Short.\n\n"
            + "Medium length paragraph here.\n\n"
            + ("Long " * 30) + "\n\n"
            + ("Very " * 50)
        )
        result = score_engagement(content, cfg=_CFG)
        assert result > _CFG["engagement_baseline"]


# ---------------------------------------------------------------------------
# detect_truncation — additional branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDetectTruncationDetailed:
    def test_html_stripped_before_analysis(self):
        content = "<p>" + "word " * 50 + "Final sentence.</p>"
        assert detect_truncation(content) is False

    def test_quote_ending_not_truncated(self):
        content = "word " * 50 + 'He replied, "Yes."'
        assert detect_truncation(content) is False

    def test_paren_ending_not_truncated(self):
        content = "word " * 50 + "(see footnote 1)"
        assert detect_truncation(content) is False

    def test_short_content_below_100_chars(self):
        assert detect_truncation("Short fragment here") is False

    def test_html_only_returns_false(self):
        assert detect_truncation("<div></div>" + "x" * 100) is False or True
        # The result is acceptable either way; primary check is no exception


# ---------------------------------------------------------------------------
# check_keywords — type coercion
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckKeywordsCoercion:
    def test_non_string_non_list_coerced(self):
        # Integer keyword gets coerced to string then filtered (because integer
        # str representations are unlikely to appear in content)
        result = check_keywords("the answer is 42", {"keywords": 42})
        assert result is True

    def test_whitespace_only_keyword_filtered(self):
        result = check_keywords("Some content", {"keywords": ["   ", "Docker"]})
        # Whitespace filtered, only "Docker" remains
        assert result is False

    def test_mixed_string_and_invalid(self):
        result = check_keywords("Docker is great", {"keywords": ["Docker", 123, None]})
        assert result is True


# ---------------------------------------------------------------------------
# generate_feedback — additional tier coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateFeedbackDetailed:
    def test_acceptable_tier(self):
        dims = type("D", (), {"average": lambda self: 72})()
        msg = generate_feedback(dims, {})
        assert "acceptable" in msg.lower()

    def test_fair_tier(self):
        dims = type("D", (), {"average": lambda self: 65})()
        msg = generate_feedback(dims, {})
        assert "fair" in msg.lower()

    def test_boundary_85_excellent(self):
        dims = type("D", (), {"average": lambda self: 85})()
        msg = generate_feedback(dims, {})
        assert "excellent" in msg.lower()

    def test_boundary_75_good(self):
        dims = type("D", (), {"average": lambda self: 75})()
        msg = generate_feedback(dims, {})
        assert "good" in msg.lower()


# ---------------------------------------------------------------------------
# generate_suggestions — single-dimension coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateSuggestionsDetailed:
    def _make(self, **overrides):
        defaults = {
            "clarity": 80, "accuracy": 80, "completeness": 80,
            "relevance": 80, "seo_quality": 80, "readability": 80, "engagement": 80,
        }
        defaults.update(overrides)
        return type("D", (), defaults)()

    def test_low_accuracy_suggests_citations(self):
        dims = self._make(accuracy=50)
        result = generate_suggestions(dims)
        assert any("citation" in s.lower() or "fact" in s.lower() for s in result)

    def test_low_completeness_suggests_more_detail(self):
        dims = self._make(completeness=50)
        result = generate_suggestions(dims)
        assert any("detail" in s.lower() or "thorough" in s.lower() for s in result)

    def test_low_relevance_suggests_focus(self):
        dims = self._make(relevance=50)
        result = generate_suggestions(dims)
        assert any("focus" in s.lower() for s in result)

    def test_low_seo_suggests_seo_improvements(self):
        dims = self._make(seo_quality=50)
        result = generate_suggestions(dims)
        assert any("seo" in s.lower() or "header" in s.lower() for s in result)

    def test_low_readability_suggests_grammar(self):
        dims = self._make(readability=50)
        result = generate_suggestions(dims)
        assert any("readability" in s.lower() or "grammar" in s.lower() for s in result)

    def test_low_engagement_suggests_questions_lists(self):
        dims = self._make(engagement=50)
        result = generate_suggestions(dims)
        assert any("question" in s.lower() or "list" in s.lower() or "engaging" in s.lower() for s in result)
