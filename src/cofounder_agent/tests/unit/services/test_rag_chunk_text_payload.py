"""Retrieval must return the full embedded chunk, not the display preview.

Regression guard for poindexter#1033. The tap runner embeds chunks up to
``MAX_CHARS = 6000`` in full, but ``text_preview`` is a ``varchar(500)``
display column. Building the retrieval payload from it discarded 87.4% of
the live post corpus and fed the cross-encoder reranker ~12% of each
candidate.
"""

from __future__ import annotations

from services.rag_engine import _retrieval_text


class TestRetrievalText:
    """``_retrieval_text`` is the seam that decides what a consumer sees."""

    def test_prefers_full_chunk_over_preview(self):
        row = {"chunk_text": "F" * 6000, "text_preview": "F" * 500}
        assert len(_retrieval_text(row)) == 6000

    def test_falls_back_to_preview_when_chunk_text_null(self):
        """Load-bearing: chunk_text is NULL on every pre-migration row, so the
        column must be deployable ahead of the backfill without blanking
        retrieval in the interval."""
        row = {"chunk_text": None, "text_preview": "legacy preview"}
        assert _retrieval_text(row) == "legacy preview"

    def test_falls_back_when_chunk_text_absent_entirely(self):
        assert _retrieval_text({"text_preview": "only preview"}) == "only preview"

    def test_empty_string_chunk_text_falls_back(self):
        """'' is not a valid payload — treat it like NULL rather than
        returning a blank node and silently losing the candidate."""
        row = {"chunk_text": "", "text_preview": "preview"}
        assert _retrieval_text(row) == "preview"

    def test_returns_empty_string_when_nothing_available(self):
        assert _retrieval_text({}) == ""

    def test_does_not_truncate(self):
        """The whole point: no 500-char clip anywhere on this path."""
        body = "".join(f"paragraph {i} " for i in range(600))
        assert _retrieval_text({"chunk_text": body}) == body


class TestNodePreviewStaysShort:
    """The display field must not inherit whole chunks now that node.text is
    the full payload — dashboards, CLI and voice render it directly."""

    def test_preview_clipped_to_500(self):
        from poindexter.memory.client import _node_preview

        class _Node:
            text = "x" * 6000

        assert len(_node_preview(_Node())) == 500

    def test_preview_flattens_newlines_like_store(self):
        from poindexter.memory.client import _node_preview

        class _Node:
            text = "line one\nline two"

        assert _node_preview(_Node()) == "line one line two"

    def test_preview_handles_missing_text(self):
        from poindexter.memory.client import _node_preview

        assert _node_preview(object()) == ""


class TestMemoryHitContract:
    """Both search paths must agree on what each field means."""

    def test_chunk_text_defaults_empty_for_legacy_constructions(self):
        from poindexter.memory.client import MemoryHit

        hit = MemoryHit(
            source_table="posts",
            source_id="p1",
            similarity=0.9,
            text_preview="short",
            writer="worker",
            origin_path=None,
        )
        assert hit.chunk_text == ""

    def test_chunk_text_carries_full_payload(self):
        from poindexter.memory.client import MemoryHit

        hit = MemoryHit(
            source_table="posts",
            source_id="p1",
            similarity=0.9,
            text_preview="short",
            writer="worker",
            origin_path=None,
            chunk_text="F" * 6000,
        )
        assert len(hit.chunk_text) == 6000
        assert len(hit.text_preview) == 5
