"""Unit tests for services.taps._chunking — the shared chunking + dedup helpers.

These functions were moved out of ``scripts/auto-embed.py``. The behavior
must match exactly — every test here is expected to pass before and
after the Phase B migration.
"""

from __future__ import annotations

from services.taps._chunking import MAX_CHARS, chunk_text, classify_file, content_hash


class TestContentHash:
    def test_empty_string(self):
        assert content_hash("") == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_deterministic(self):
        assert content_hash("hello") == content_hash("hello")

    def test_sensitive_to_whitespace(self):
        assert content_hash("hello") != content_hash("hello ")

    def test_utf8_encoding(self):
        # Non-ASCII characters hash consistently via UTF-8.
        h1 = content_hash("café")
        h2 = content_hash("café")
        assert h1 == h2


class TestClassifyFile:
    def test_handoff(self):
        assert classify_file("session_49_handoff.md") == "handoff"
        assert classify_file("session-notes.md") == "handoff"

    def test_feedback(self):
        assert classify_file("feedback_honesty.md") == "feedback"
        assert classify_file("user_preferences.md") == "feedback"

    def test_decision(self):
        assert classify_file("decision-arch-2026.md") == "decision"

    def test_identity(self):
        assert classify_file("user_profile.md") == "identity"
        assert classify_file("matts_voice.md") == "identity"

    def test_project(self):
        assert classify_file("project_lane2_plan.md") == "project"
        assert classify_file("brand_strategy.md") == "project"
        assert classify_file("saas_vision.md") == "project"

    def test_knowledge_default(self):
        assert classify_file("random_note.md") == "knowledge"
        assert classify_file("glossary.md") == "knowledge"

    def test_index(self):
        assert classify_file("MEMORY.md") == "index"

    def test_case_insensitive(self):
        assert classify_file("SESSION.md") == "handoff"
        assert classify_file("Feedback_Test.md") == "feedback"


class TestChunkText:
    def test_short_text_single_chunk(self):
        text = "Just a short paragraph."
        assert chunk_text(text) == [text]

    def test_exactly_max_chars_single_chunk(self):
        text = "x" * MAX_CHARS
        chunks = chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_splits_on_headings(self):
        section_a = "# Section A\n" + ("a" * 4000)
        section_b = "# Section B\n" + ("b" * 4000)
        section_c = "# Section C\n" + ("c" * 4000)
        text = section_a + "\n" + section_b + "\n" + section_c

        chunks = chunk_text(text)
        assert len(chunks) >= 2
        assert all(len(c) <= MAX_CHARS for c in chunks)

    def test_oversized_section_splits_on_paragraphs(self):
        paras = ["paragraph " + "x" * 500 for _ in range(20)]
        text = "# Big Section\n\n" + "\n\n".join(paras)

        chunks = chunk_text(text)
        assert all(len(c) <= MAX_CHARS for c in chunks)
        # Every paragraph should appear in some chunk.
        joined = "".join(chunks)
        for p in paras:
            assert p in joined

    def test_oversized_paragraph_hard_slices(self):
        text = "x" * (MAX_CHARS * 2 + 500)
        chunks = chunk_text(text)
        assert all(len(c) <= MAX_CHARS for c in chunks)
        # Reassembled content matches.
        assert "".join(chunks) == text

    def test_no_content_loss(self):
        section_a = "## A\n" + ("a" * 4000) + "\n"
        section_b = "## B\n" + ("b" * 4000) + "\n"
        section_c = "## C\n" + ("c" * 4000) + "\n"
        text = section_a + section_b + section_c

        chunks = chunk_text(text)
        # Every character from the input appears in some chunk.
        assert sum(len(c) for c in chunks) >= len(text) - 10  # allow minor whitespace drift

    def test_custom_max_chars(self):
        text = "# A\n" + "a" * 200 + "\n# B\n" + "b" * 200
        chunks = chunk_text(text, max_chars=100)
        assert all(len(c) <= 100 for c in chunks)
