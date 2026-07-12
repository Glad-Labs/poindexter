"""Unit tests for the bake-off sidecar sentence-chunker (stdlib-only).

`chunk_text` lives under `scripts/tts_sidecars/` (it ships into the slim sidecar
images, so it can't import from the app package). We load it by file path and
skip if the scripts tree isn't present (e.g. a stripped in-container checkout).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_CHUNKER = Path(__file__).parents[6] / "scripts" / "tts_sidecars" / "text_chunking.py"


def _load_chunk_text():
    if not _CHUNKER.exists():
        pytest.skip(f"chunker not present at {_CHUNKER}")
    spec = importlib.util.spec_from_file_location("bakeoff_text_chunking", _CHUNKER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.chunk_text


@pytest.mark.unit
class TestChunkText:
    def test_empty_returns_empty_list(self):
        chunk_text = _load_chunk_text()
        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_short_text_is_one_chunk(self):
        chunk_text = _load_chunk_text()
        assert chunk_text("Hello world.") == ["Hello world."]

    def test_groups_multiple_sentences_under_budget(self):
        chunk_text = _load_chunk_text()
        text = "One two three. Four five six. Seven eight nine."
        # each ~15 chars — comfortably under the 240 default — so one chunk.
        assert chunk_text(text) == [text]

    def test_splits_when_over_budget(self):
        chunk_text = _load_chunk_text()
        s1 = "A" * 200 + "."
        s2 = "B" * 200 + "."
        assert chunk_text(f"{s1} {s2}", max_chars=240) == [s1, s2]

    def test_no_sentence_dropped_and_words_preserved(self):
        chunk_text = _load_chunk_text()
        text = ("Here is one. Here is two! And a third? "
                "A fourth sentence follows. Then a fifth one ends it.")
        chunks = chunk_text(text, max_chars=40)
        assert len(chunks) > 1  # actually split
        assert " ".join(chunks).split() == text.split()  # nothing lost

    def test_oversize_single_sentence_kept_not_dropped(self):
        chunk_text = _load_chunk_text()
        big = ("word " * 100).strip()  # no punctuation => one long "sentence"
        chunks = chunk_text(big, max_chars=50)
        assert len(chunks) == 1
        assert chunks[0] == big
