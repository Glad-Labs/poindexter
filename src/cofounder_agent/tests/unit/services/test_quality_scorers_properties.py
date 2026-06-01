"""Property-based tests for the deterministic quality scorers (GH#337 workstream c).

The original ask in #30 (parent of #337) was a ``hypothesis`` property test for
``quality_service`` scoring. These cover the PURE, DB-free scorers in
``services.quality_scorers`` — the ones that take only plain values and so can
be hammered with arbitrary input without a SiteConfig or a database.

Invariants under test (the things a future refactor must never break):

* **Bounded.** Every dimension score lands in ``[0.0, 10.0]`` — overall quality
  is a weighted blend of these, so an out-of-range dimension would silently
  skew (or clamp away) real signal.
* **Total.** The scorer never raises on *any* string — empty, whitespace,
  punctuation-only, huge, or arbitrary Unicode. A crash here aborts the whole
  ``quality_evaluation`` pipeline stage.
* **Deterministic.** Same input → same score (no hidden state / RNG).

The SiteConfig-coupled scorers (clarity/accuracy/completeness/relevance/seo/
engagement) read tunables via ``qa_cfg`` and validator gates; property-testing
those needs a seeded config + DB and belongs in a separate, slower suite — out
of scope for this DB-free property pass.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from services.quality_scorers import (
    check_keywords,
    count_syllables,
    detect_truncation,
    flesch_kincaid_grade_level,
    generate_feedback,
    generate_suggestions,
    score_readability,
)

# Text strategies that exercise the float math + tokenizer edge cases the
# Flesch formula is sensitive to: empties, whitespace, punctuation-only
# (drives sentence/word counts to 0), and arbitrary Unicode.
_TEXT = st.text(max_size=4000)
_NASTY = st.sampled_from(
    [
        "",
        " ",
        "\n\n\t  \n",
        ".",
        "!?.!?",
        "...",
        "a",
        "I",
        "word " * 1000,
        "supercalifragilisticexpialidocious " * 50,
        "PostgreSQL Kubernetes microservices infrastructure orchestration",
        "中文 文字 测试 句子。",
        "émojis 🚀 and àccénts",
        "no terminators just a very long run on clause that never ends and keeps going",
    ]
)


@given(content=st.one_of(_TEXT, _NASTY))
@settings(max_examples=300)
def test_score_readability_is_bounded_and_total(content: str) -> None:
    """Any string → a finite score in [0, 10], never an exception."""
    score = score_readability(content)
    assert isinstance(score, float)
    assert math.isfinite(score), f"non-finite readability score for {content!r}"
    assert 0.0 <= score <= 10.0, f"readability {score} out of range for {content!r}"


@given(content=st.one_of(_TEXT, _NASTY))
@settings(max_examples=100)
def test_score_readability_is_deterministic(content: str) -> None:
    """Same input scores identically — no hidden state."""
    assert score_readability(content) == score_readability(content)


@given(word=st.text(max_size=80))
@settings(max_examples=300)
def test_count_syllables_is_nonnegative_and_total(word: str) -> None:
    """count_syllables never raises and never returns a negative count for
    arbitrary input (it underpins score_readability's Flesch term)."""
    n = count_syllables(word)
    assert isinstance(n, int)
    assert n >= 0, f"negative syllable count {n} for {word!r}"


# ---------------------------------------------------------------------------
# Example-based edge / error-path coverage for the OTHER pure (DB-free)
# helpers in services.quality_scorers. The property tests above pin
# invariants (bounded / total / deterministic) on the readability path;
# these pin the exact contract of the branchy utilities the pipeline relies
# on — type coercion, empty-input guards, the truncation heuristic regexes,
# and the feedback/suggestion threshold bands. None of these touch SiteConfig
# or the DB, so they stay in this fast, DB-free suite.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "keywords, content, expected",
    [
        # str is wrapped into a single-element list, matched case-insensitively
        ("Python", "I really love PYTHON tooling", True),
        ("Python", "no mention here", False),
        # list path — any-match semantics
        (["Rust", "Go"], "a post about go routines", True),
        (["Rust", "Go"], "neither language appears", False),
        # None / empty list / blank-only entries all coerce to "no keywords"
        (None, "anything at all", False),
        ([], "anything at all", False),
        (["   ", ""], "anything at all", False),
        # non-str / non-list (e.g. an int slipping through) is str()-coerced
        (123, "error code 123 happened", True),
    ],
)
def test_check_keywords_type_coercion_and_matching(keywords, content, expected) -> None:
    """check_keywords coerces None/str/int/list inputs and matches case-insensitively.

    The blank-only case (``["   ", ""]``) is the important guard: whitespace
    keywords are stripped out, so an empty effective keyword list must NOT
    spuriously match every post.
    """
    assert check_keywords(content, {"keywords": keywords}) is expected


def test_check_keywords_tolerates_none_context() -> None:
    """A ``None`` context must not raise — it degrades to 'no keywords'."""
    assert check_keywords("some content", None) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["", "   ", "\n\t  \n", "<p></p>", "#### \n"])
def test_flesch_kincaid_returns_zero_when_no_words(text: str) -> None:
    """Empty/whitespace text, or markup that strips to zero alphabetic words,
    yields 0.0 instead of a ZeroDivisionError on ``/ total_words``."""
    assert flesch_kincaid_grade_level(text) == 0.0


def test_flesch_kincaid_known_value_and_rounding() -> None:
    """Pin the exact formula + 2-decimal rounding on a hand-computed sample.

    "The cat sat." -> 3 words, 1 sentence, 3 syllables:
        0.39*(3/1) + 11.8*(3/3) - 15.59 = -2.62
    """
    assert flesch_kincaid_grade_level("The cat sat.") == -2.62


def test_flesch_kincaid_strips_markup_before_scoring() -> None:
    """Markdown headings and HTML tags are stripped, so the score matches the
    same prose without markup (no '<', '#' chars polluting the word list)."""
    plain = flesch_kincaid_grade_level("The cat sat on the mat today.")
    marked = flesch_kincaid_grade_level("# <b>The</b> cat sat on the mat today.")
    assert plain == marked


@pytest.mark.parametrize(
    "content",
    [
        "",  # empty
        "Too short to bother analysing.",  # < 100 chars -> skipped
        "<div></div>",  # strips to nothing
    ],
)
def test_detect_truncation_false_for_short_or_empty(content: str) -> None:
    """Truncation detection is a no-op below the 100-char floor / on empties."""
    assert detect_truncation(content) is False


def test_detect_truncation_flags_heading_and_fragment_endings() -> None:
    """Content ending on a bare heading or a long unterminated fragment is
    treated as cut off by the LLM token limit."""
    body = "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do. " * 3
    assert detect_truncation(body + "\n## Conclusion") is True
    assert detect_truncation(
        body + "\nand then the orchestrator began to process the next pending"
    ) is True


@pytest.mark.parametrize(
    "ending",
    [
        "and that wraps up the analysis.",  # terminal period
        "see the discussion here)",  # closing paren
        'he simply said "done"',  # closing quote
        "full writeup at https://example.com/article",  # trailing URL = refs
    ],
)
def test_detect_truncation_allows_complete_endings(ending: str) -> None:
    """Properly terminated content (punctuation, paren/quote, or a trailing
    reference URL) is NOT flagged as truncated."""
    body = "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do. " * 3
    assert detect_truncation(body + "\n" + ending) is False


@pytest.mark.parametrize(
    "word, expected",
    [
        ("hello", 2),       # two vowel groups: e, o
        ("queue", 1),       # a single consecutive vowel run counts once
        ("rhythm", 1),      # no a/e/i/o/u -> floored to the 1-syllable minimum
        ("", 1),            # empty word still floors to 1
        ("a", 1),
    ],
)
def test_count_syllables_known_values(word: str, expected: int) -> None:
    """Concrete syllable counts pin the vowel-group heuristic + the floor of 1."""
    assert count_syllables(word) == expected


def test_score_readability_neutral_default_when_wordless() -> None:
    """Whitespace-only content has no words, so the Flesch math is skipped and
    the neutral 7.0 default is returned rather than dividing by zero."""
    assert score_readability("   \n\t ") == 7.0


@pytest.mark.parametrize(
    "overall, expected",
    [
        (90.0, "Excellent content quality - publication ready"),
        (78.0, "Good quality - minor improvements recommended"),
        (72.0, "Acceptable quality - some improvements suggested"),
        (65.0, "Fair quality - significant improvements needed"),
        (40.0, "Poor quality - major revisions required"),
    ],
)
def test_generate_feedback_threshold_bands(overall: float, expected: str) -> None:
    """Each averaged-score band maps to its exact human-readable verdict."""
    dims = SimpleNamespace(average=lambda value=overall: value)
    assert generate_feedback(dims, {}) == expected


def test_generate_suggestions_weak_strong_and_fallback() -> None:
    """Weak dimensions (< 70 on the 0-100 scale) each emit a targeted
    suggestion; an all-strong set falls back to the standards-met message."""
    strong = SimpleNamespace(
        clarity=85, accuracy=85, completeness=85, relevance=85,
        seo_quality=85, readability=85, engagement=85,
    )
    assert generate_suggestions(strong) == ["Content meets quality standards"]

    only_clarity_weak = SimpleNamespace(
        clarity=40, accuracy=85, completeness=85, relevance=85,
        seo_quality=85, readability=85, engagement=85,
    )
    out = generate_suggestions(only_clarity_weak)
    assert out == ["Simplify sentence structure and use shorter sentences"]

    all_weak = SimpleNamespace(
        clarity=0, accuracy=0, completeness=0, relevance=0,
        seo_quality=0, readability=0, engagement=0,
    )
    assert len(generate_suggestions(all_weak)) == 7
