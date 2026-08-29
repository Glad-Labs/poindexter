"""Tests for ``services/rag_excerpt.py`` — relevance-windowed excerpting.

The defect this module exists to prevent: a chunk retrieved BECAUSE of a
passage at character 4,000, then head-sliced to 500 chars, hands the consumer
text that argues for nothing. Every test here is a variant of "did the budget
get spent on the matching passage".
"""

from __future__ import annotations

import pytest

from services.rag_excerpt import excerpt_around_query, extract_terms


class TestExtractTerms:
    def test_drops_stopwords_and_short_words(self):
        assert extract_terms("what is the RAG payload in it") == ["rag", "payload"]

    def test_dedupes_preserving_order(self):
        assert extract_terms("vector vector search VECTOR") == ["vector", "search"]

    def test_empty_query_is_empty_list(self):
        assert extract_terms("") == []
        assert extract_terms("a of to") == []


class TestPassthrough:
    def test_text_within_budget_is_untouched(self):
        text = "short chunk about pgvector"
        assert excerpt_around_query(text, "pgvector", 500) == text

    def test_zero_max_chars_means_uncapped(self):
        text = "x" * 9000
        assert excerpt_around_query(text, "anything", 0) == text

    def test_negative_max_chars_means_uncapped(self):
        text = "x" * 9000
        assert excerpt_around_query(text, "anything", -1) == text

    def test_empty_text_is_empty(self):
        assert excerpt_around_query("", "query", 100) == ""


class TestWindowSelection:
    def _chunk(self, needle: str, *, at: int, total: int = 6000) -> str:
        """A chunk of ``total`` filler chars with ``needle`` planted at ``at``."""
        filler = "lorem ipsum dolor sit amet consectetur adipiscing elit "
        body = (filler * ((total // len(filler)) + 2))[:total]
        return body[:at] + f" {needle} " + body[at + len(needle) + 2 :]

    def test_finds_passage_in_the_tail(self):
        """The whole point: a head slice would return none of this."""
        text = self._chunk("cross encoder reranker", at=4000)
        out = excerpt_around_query(text, "cross encoder reranker", 500)
        assert "cross encoder reranker" in out
        assert len(out) <= 500 + 2  # + the two ellipsis marks

    def test_finds_passage_in_the_middle(self):
        text = self._chunk("hnsw index tuning", at=2800)
        assert "hnsw index tuning" in excerpt_around_query(text, "hnsw index", 400)

    def test_finds_passage_near_the_very_end(self):
        text = self._chunk("final boundary marker", at=5900, total=6000)
        out = excerpt_around_query(text, "final boundary marker", 400)
        assert "final boundary marker" in out

    def test_prefers_window_covering_more_distinct_terms(self):
        """Coverage beats repetition — three query terms once each wins over
        one term five times, because the first window is on the QUESTION."""
        filler = "padding text that means nothing at all here " * 40
        repeated = "pgvector pgvector pgvector pgvector pgvector "
        covering = "pgvector reranker latency together in one place "
        text = repeated + filler + covering + filler
        out = excerpt_around_query(text, "pgvector reranker latency", 300)
        assert "reranker latency" in out

    def test_marks_a_windowed_excerpt_with_ellipses(self):
        text = self._chunk("deep in the middle", at=3000)
        out = excerpt_around_query(text, "deep in the middle", 300)
        assert out.startswith("…") and out.endswith("…")

    def test_window_starting_at_zero_has_no_leading_ellipsis(self):
        text = "chunk_text payload " + ("tail filler words here " * 400)
        out = excerpt_around_query(text, "chunk_text payload", 300)
        assert not out.startswith("…")
        assert "chunk_text payload" in out


class TestFallbacks:
    def test_no_content_terms_falls_back_to_head_slice(self):
        text = "alpha beta gamma " * 500
        out = excerpt_around_query(text, "of the a", 200)
        assert out.startswith("alpha beta gamma")
        assert out.endswith("…")

    def test_terms_absent_from_chunk_falls_back_to_head_slice(self):
        """Retrieved on semantic similarity with zero lexical overlap: there
        is no passage to centre on, so the opening is the best guess."""
        text = "alpha beta gamma " * 500
        out = excerpt_around_query(text, "kubernetes helm chart", 200)
        assert out.startswith("alpha beta gamma")

    def test_text_with_no_whitespace_still_returns_a_window(self):
        """Minified JSON / base64 must not be excerpted down to nothing."""
        text = "a" * 3000 + "needle" + "b" * 3000
        out = excerpt_around_query(text, "needle", 500)
        assert "needle" in out
        assert len(out.strip("…")) > 400


class TestBudget:
    @pytest.mark.parametrize("max_chars", [50, 200, 500, 1500, 2000])
    def test_never_exceeds_budget_plus_ellipses(self, max_chars):
        text = "the quick brown fox jumps over the lazy dog " * 500
        out = excerpt_around_query(text, "lazy dog jumps", max_chars)
        assert len(out.strip("…")) <= max_chars

    def test_is_deterministic(self):
        text = "alpha reranker beta " * 400
        query = "reranker"
        assert excerpt_around_query(text, query, 300) == excerpt_around_query(
            text, query, 300
        )
