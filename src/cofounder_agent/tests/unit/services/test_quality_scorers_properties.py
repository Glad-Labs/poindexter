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

from hypothesis import given, settings
from hypothesis import strategies as st

from services.quality_scorers import count_syllables, score_readability

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
