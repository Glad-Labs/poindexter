"""Unit tests for ``services/topic_sanity.py`` — deterministic topic-sanity gate.

Incident fixture: ``pipeline_tasks`` row 9921678f-9b5b-4d24-9f07-c9d0398cf793
(glad-labs niche, 2026-06-30) carried the literal topic
``". .. . ... . .... . .... . ... ."`` — a dots-only dev.to headline that
sailed through tap discovery, embedding pre-rank, LLM final-score (which
ranked it TOP of its batch at 65), auto-resolve, and a full canonical_blog
GPU run before ``rejected_final`` at the last gate. The evaluator under
test is the deterministic pre-task gate that blocks that class outright —
per ``feedback_calculated_vs_generated``, garbage detection is calculated,
never delegated to an LLM.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.topic_sanity import (
    DEFAULT_MIN_ALPHA_WORDS,
    MIN_ALPHA_WORDS_KEY,
    TopicSanityError,
    count_alpha_words,
    evaluate_topic_sanity,
    resolve_min_alpha_words,
)

# The real topic from the 2026-06-30 incident, verbatim (source:
# https://dev.to/lovestaco/--2kb7 via the dev.to tap).
DOTS_TOPIC = ". .. . ... . .... . .... . ... ."


# ---------------------------------------------------------------------------
# evaluate_topic_sanity
# ---------------------------------------------------------------------------


class TestEvaluateTopicSanity:
    def test_incident_dots_topic_rejected(self):
        verdict = evaluate_topic_sanity(DOTS_TOPIC)
        assert verdict.ok is False
        assert verdict.reason == "no_alphabetic_content"
        assert verdict.alpha_word_count == 0
        assert verdict.detail  # human-readable, non-empty

    @pytest.mark.parametrize("topic", ["", "   ", "\n\t  ", None])
    def test_empty_and_whitespace_rejected(self, topic):
        verdict = evaluate_topic_sanity(topic)
        assert verdict.ok is False
        assert verdict.reason == "empty"

    @pytest.mark.parametrize("topic", ["5090!!!", "---", "12 34 // 56", "..."])
    def test_punctuation_or_digits_only_rejected(self, topic):
        verdict = evaluate_topic_sanity(topic)
        assert verdict.ok is False
        assert verdict.reason == "no_alphabetic_content"

    def test_single_word_topic_rejected_at_default(self):
        # "Cybersecurity" appeared 6x in prod history — every run rejected.
        verdict = evaluate_topic_sanity("Cybersecurity")
        assert verdict.ok is False
        assert verdict.reason == "too_few_alpha_words"
        assert verdict.alpha_word_count == 1

    def test_scattered_single_letters_do_not_count_as_words(self):
        verdict = evaluate_topic_sanity("a b c d e")
        assert verdict.ok is False
        assert verdict.reason == "too_few_alpha_words"
        assert verdict.alpha_word_count == 0

    def test_two_word_topic_passes_default(self):
        verdict = evaluate_topic_sanity("Operator Console")
        assert verdict.ok is True
        assert verdict.reason is None
        assert verdict.alpha_word_count == 2

    def test_real_headline_passes(self):
        verdict = evaluate_topic_sanity(
            "Why RTX 5090 thermals matter for small-form-factor builds"
        )
        assert verdict.ok is True

    def test_unicode_letters_count_as_alphabetic(self):
        verdict = evaluate_topic_sanity("Pokémon Scarlet performance")
        assert verdict.ok is True
        assert verdict.alpha_word_count == 3

    def test_min_zero_disables_word_count_but_not_alpha_check(self):
        # Operators can relax the word-count floor, but a topic with zero
        # alphabetic content is never valid — that check is unconditional.
        assert evaluate_topic_sanity(DOTS_TOPIC, min_alpha_words=0).ok is False
        assert evaluate_topic_sanity("Cybersecurity", min_alpha_words=0).ok is True

    def test_higher_min_rejects_two_word_topic(self):
        verdict = evaluate_topic_sanity("Operator Console", min_alpha_words=3)
        assert verdict.ok is False
        assert verdict.reason == "too_few_alpha_words"


# ---------------------------------------------------------------------------
# count_alpha_words
# ---------------------------------------------------------------------------


class TestCountAlphaWords:
    def test_hyphenated_words_count_per_run(self):
        # Each maximal letter-run of >=2 chars is a word.
        assert count_alpha_words("state-of-the-art AI") == 5

    def test_digit_tokens_do_not_count(self):
        assert count_alpha_words("RTX 5090 Ti") == 2

    def test_empty_and_none(self):
        assert count_alpha_words("") == 0
        assert count_alpha_words(None) == 0

    def test_dots_topic_counts_zero(self):
        assert count_alpha_words(DOTS_TOPIC) == 0


# ---------------------------------------------------------------------------
# TopicSanityError
# ---------------------------------------------------------------------------


class TestTopicSanityError:
    def test_is_value_error_and_carries_context(self):
        # ValueError subclass so existing adapter contracts hold: the
        # resolve/propose HTTP routes map ValueError -> 400, the CLI
        # prints it as a friendly error.
        verdict = evaluate_topic_sanity(DOTS_TOPIC)
        err = TopicSanityError(DOTS_TOPIC, verdict)
        assert isinstance(err, ValueError)
        assert err.result is verdict
        assert err.topic == DOTS_TOPIC
        assert "no_alphabetic_content" in str(err)


# ---------------------------------------------------------------------------
# resolve_min_alpha_words — SiteConfig seam
# ---------------------------------------------------------------------------


class TestResolveMinAlphaWords:
    def test_none_site_config_falls_back_to_default(self):
        assert resolve_min_alpha_words(None) == DEFAULT_MIN_ALPHA_WORDS

    def test_reads_get_int_when_available(self):
        sc = SimpleNamespace(get_int=lambda key, default: 3)
        assert resolve_min_alpha_words(sc) == 3

    def test_get_int_raising_falls_back_to_default(self):
        def _boom(key, default):
            raise TypeError("stub without proper get_int")

        sc = SimpleNamespace(get_int=_boom)
        assert resolve_min_alpha_words(sc) == DEFAULT_MIN_ALPHA_WORDS

    def test_dict_style_get_coerces(self):
        sc = SimpleNamespace(get=lambda key, default=None: "4")
        assert resolve_min_alpha_words(sc) == 4

    def test_dict_style_get_bad_value_falls_back(self):
        sc = SimpleNamespace(get=lambda key, default=None: "not-an-int")
        assert resolve_min_alpha_words(sc) == DEFAULT_MIN_ALPHA_WORDS

    def test_key_name_is_stable(self):
        # The app_settings key is public API (seeded in settings_defaults);
        # renaming it silently orphans operator-tuned values.
        assert MIN_ALPHA_WORDS_KEY == "topic_sanity_min_alpha_words"


# ---------------------------------------------------------------------------
# poindexter#808 — failure sentinels + truncated titles
# ---------------------------------------------------------------------------


class TestFailureSentinels:
    """LLM distillers emit their failure state as the topic string
    ("No topic found" reached awaiting_approval on 2026-07-02, task
    4b470976). A failure sentinel is a bounded, deterministic class —
    catch it at the sanity gate so every task-creation seam is covered."""

    @pytest.mark.parametrize("topic", [
        "No topic found",
        "no topic found",
        "  No Topic Found.  ",
        "Untitled",
        "N/A",
        "None",
        "Unknown",
        "TBD",
        "Not found",
        "No clear topic",
        "Insufficient information",
        "Error",
    ])
    def test_failure_sentinels_rejected(self, topic):
        verdict = evaluate_topic_sanity(topic)
        assert verdict.ok is False
        assert verdict.reason == "failure_sentinel"

    def test_sentinel_embedded_in_real_topic_passes(self):
        # Only the WHOLE topic being a sentinel is junk — a real headline
        # that merely contains the words must pass.
        verdict = evaluate_topic_sanity(
            "Why 'No Topic Found' Errors Plague RAG Pipelines"
        )
        assert verdict.ok is True


class TestTruncatedTitles:
    """Distillation sometimes emits a clause cut mid-phrase — the real
    task 115646d1 (2026-07-01) ran a full pipeline on the topic
    'What to Learn to Be a'. A title ending in an article/preposition/
    conjunction is deterministically incomplete."""

    @pytest.mark.parametrize("topic", [
        "What to Learn to Be a",
        "The Future of AI in",
        "How to Build Your First and",
        "Machine Learning for the",
    ])
    def test_trailing_stopword_rejected(self, topic):
        verdict = evaluate_topic_sanity(topic)
        assert verdict.ok is False
        assert verdict.reason == "truncated_title"

    @pytest.mark.parametrize("topic", [
        "What to Learn to Be a Machine Learning Engineer",
        "The Rise of Local LLMs",
        "Windows on ARM Finally Makes Sense",
        "Automating Content Automation",  # vague but complete — QA's job
        "GPU Undervolting Explained",
    ])
    def test_complete_titles_pass(self, topic):
        verdict = evaluate_topic_sanity(topic)
        assert verdict.ok is True, verdict.detail
