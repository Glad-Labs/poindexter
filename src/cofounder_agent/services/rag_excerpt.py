"""Relevance-windowed excerpting for RAG payloads.

## Why this exists

Retrieval matches a query against a whole chunk (up to
``services/taps/_chunking.py::MAX_CHARS``), but most consumers cannot afford
to *spend* a whole chunk: the writer's snippet block shares one ``num_ctx``
with the draft it is producing, and the cross-encoder reranker truncates at
its own model window. So every consumer has, or needs, a character cap.

The naive cap is ``text[:max_chars]`` — a head slice. That reproduces the
exact defect this module was written alongside: the passage that made the
chunk match can sit anywhere in it, and a head slice need not contain it.
A chunk retrieved *because* of something at character 4,000 and then
truncated at 500 hands the consumer text that argues for nothing.

``excerpt_around_query`` spends the same budget on the part of the chunk the
query actually matched. Same token cost, relevant content.

## What it does not do

It is deliberately lexical — no embeddings, no model call. It runs inline on
every snippet of every retrieval, so it has to be cheap and deterministic.
Term overlap is a coarse proxy for the semantic match that retrieved the
chunk, and it is a much better proxy than "the first N characters".
"""

from __future__ import annotations

import re

__all__ = ["excerpt_around_query", "extract_terms"]

# Short function words carry no retrieval signal but appear in every window,
# so they would flatten the scoring into a tie and hand back the head slice.
_STOPWORDS = frozenset(
    """
    a an and are as at be but by for from had has have how in into is it its
    of on or that the their then there these they this to was were what when
    where which who why will with you your
    """.split()
)

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'_-]*")

# Marks a window that does not start/end at a chunk boundary, so a reader (or
# a model) can tell an excerpt from a complete chunk.
_ELLIPSIS = "…"


def extract_terms(query: str) -> list[str]:
    """Content words from ``query``, lowercased and de-duplicated in order."""
    seen: set[str] = set()
    terms: list[str] = []
    for word in _WORD_RE.findall((query or "").lower()):
        if len(word) < 3 or word in _STOPWORDS or word in seen:
            continue
        seen.add(word)
        terms.append(word)
    return terms


def _score_window(window_lower: str, terms: list[str]) -> float:
    """Distinct-term coverage, with total occurrences as a tiebreak.

    Coverage dominates: a window mentioning three different query terms once
    each beats one that repeats a single term five times, which is the right
    call for grounding — the second window is on topic, the first is on the
    *question*.
    """
    distinct = 0
    occurrences = 0
    for term in terms:
        count = window_lower.count(term)
        if count:
            distinct += 1
            occurrences += count
    return distinct + 0.01 * occurrences


def _snap_to_word_boundaries(text: str, start: int, end: int) -> tuple[int, int]:
    """Nudge ``[start, end)`` outward-then-inward onto whitespace.

    Only ever shrinks the window, so the caller's budget still holds. A
    boundary that would consume more than a fifth of the window is abandoned
    (a chunk with no whitespace for 400 chars — minified JSON, a base64 blob
    — must not be excerpted down to nothing).
    """
    max_shift = max(1, (end - start) // 5)
    if start > 0:
        space = text.find(" ", start, start + max_shift)
        if space != -1:
            start = space + 1
    if end < len(text):
        space = text.rfind(" ", end - max_shift, end)
        if space != -1:
            end = space
    return start, end


def excerpt_around_query(text: str, query: str, max_chars: int) -> str:
    """Return the ``max_chars``-long window of ``text`` most relevant to ``query``.

    ``max_chars <= 0`` means *uncapped* and returns ``text`` unchanged — the
    app_settings convention where 0/'' is "no limit configured". Text already
    within budget is returned untouched, so this is a no-op for the short
    rows (``brain``, most ``audit``) that make up much of the corpus.

    A query with no usable content words falls back to the head slice: with
    nothing to locate, the opening of a chunk is the best guess available.
    """
    if not text:
        return ""
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    terms = extract_terms(query)
    if not terms:
        return text[:max_chars].rstrip() + _ELLIPSIS

    lowered = text.lower()
    # Quarter-window stride: fine enough that a matching passage lands
    # comfortably inside some window, coarse enough to stay ~4 scans per
    # chunk-length of text.
    step = max(1, max_chars // 4)
    last_start = len(text) - max_chars
    starts = list(range(0, last_start, step)) + [last_start]

    best_start = 0
    best_score = -1.0
    for start in starts:
        score = _score_window(lowered[start : start + max_chars], terms)
        # Strict > keeps the EARLIEST best window: on a tie the opening of a
        # document is the more informative excerpt (titles, framing, dates).
        if score > best_score:
            best_score = score
            best_start = start

    if best_score <= 0:
        # No query term appears anywhere in the chunk. It was retrieved on
        # semantic similarity alone, so there is no lexical passage to centre
        # on — head slice, same as the no-terms case.
        return text[:max_chars].rstrip() + _ELLIPSIS

    start, end = _snap_to_word_boundaries(text, best_start, best_start + max_chars)
    window = text[start:end].strip()
    if start > 0:
        window = _ELLIPSIS + window
    if end < len(text):
        window = window + _ELLIPSIS
    return window
