"""Tests for _seo_common.resolve_primary_keyword + is_topic_echo
(2026-07-24 topic-echo fix).

resolve_primary_keyword: tags[0] → content-derived keyword → topic — so a
directive-shaped topic stops being injected as the "lead with primary
keyword '...'" instruction when the article can supply a real keyword.

is_topic_echo: the programmatic guard predicate — a seo_title that is the
topic string parroted back (optionally clipped at the 60-char SEO cap).
"""

from __future__ import annotations

from modules.content.atoms import _seo_common as sc

_DIRECTIVE_TOPIC = "Expand coverage of the Insights category — only Insights (3)"


# ---------------------------------------------------------------------------
# resolve_primary_keyword
# ---------------------------------------------------------------------------

def test_tags_win():
    state = {"tags": ["pgvector", "postgres"], "content": "words words", "topic": "T"}
    assert sc.resolve_primary_keyword(state) == "pgvector"


def test_blank_first_tag_skipped_to_content_keyword():
    state = {
        "tags": ["  "],
        "content": "dashboard rebuild dashboard rebuild dashboard console",
        "topic": _DIRECTIVE_TOPIC,
    }
    assert sc.resolve_primary_keyword(state) == "dashboard"


def test_no_tags_uses_content_keyword_not_topic():
    state = {
        "tags": [],
        "content": "console console console migration migration essay",
        "topic": _DIRECTIVE_TOPIC,
    }
    # extract_keywords_from_text needs a word appearing at least twice.
    assert sc.resolve_primary_keyword(state) == "console"


def test_no_tags_no_content_keywords_falls_back_to_topic():
    # Every word appears once and/or is under the 4-char floor — no
    # frequency keyword exists, so the legacy topic fallback applies.
    state = {"tags": [], "content": "one two big cat", "topic": "The Console Transition"}
    assert sc.resolve_primary_keyword(state) == "The Console Transition"


def test_everything_empty_returns_empty():
    assert sc.resolve_primary_keyword({}) == ""


# ---------------------------------------------------------------------------
# is_topic_echo
# ---------------------------------------------------------------------------

def test_exact_echo():
    assert sc.is_topic_echo(_DIRECTIVE_TOPIC, _DIRECTIVE_TOPIC) is True


def test_echo_survives_case_and_punctuation_differences():
    assert sc.is_topic_echo(
        "expand coverage of the insights category only insights 3",
        _DIRECTIVE_TOPIC,
    ) is True


def test_clipped_echo_at_seo_cap():
    # A long topic clipped by derive_seo_title's 60-char word-boundary
    # truncation is still an echo of it.
    long_topic = (
        "Expand coverage of the Insights category and make sure every post "
        "lands in exactly that category going forward"
    )
    from utils.title_utils import derive_seo_title

    clipped = derive_seo_title(long_topic, max_len=60)
    assert sc.is_topic_echo(clipped, long_topic) is True


def test_short_prefix_is_not_an_echo():
    # A short generic title that happens to prefix a long topic must not
    # false-positive (normalized prefix under the minimum length).
    assert sc.is_topic_echo("Insights", _DIRECTIVE_TOPIC) is False


def test_real_title_about_the_topic_is_not_an_echo():
    assert sc.is_topic_echo(
        "The Three-Post Problem: Why Our Insights Category Stayed Empty",
        _DIRECTIVE_TOPIC,
    ) is False


def test_clean_topic_verbatim_title_is_an_echo():
    # Matt's spec is literal: seo_title == topic string ⇒ guard fires, even
    # for a clean title-shaped topic (the guard then prefers the canonical
    # title, which adds an actual angle).
    assert sc.is_topic_echo("The Console Transition", "The Console Transition") is True


def test_empty_inputs_never_echo():
    assert sc.is_topic_echo("", "topic") is False
    assert sc.is_topic_echo("title", "") is False
    assert sc.is_topic_echo("", "") is False
