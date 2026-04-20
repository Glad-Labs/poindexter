"""Unit tests for ``services/taps/openclaw_sqlite.py``.

The Tap reads a real SQLite file (tmp_path-backed in tests) and yields
Documents with ``precomputed_embedding`` attached. Covers the Phase B
follow-up from GitHub #79 — OpenClaw's chunks ship with their own
768-dim vectors, so the Tap must skip the Ollama re-embed path.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from services.taps.openclaw_sqlite import OpenClawSQLiteTap


def _build_sqlite(path: Path, rows: list[tuple]) -> None:
    """Create a fixture SQLite DB matching OpenClaw's chunks schema."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY,
            path TEXT,
            source TEXT,
            start_line INTEGER,
            end_line INTEGER,
            model TEXT,
            text TEXT,
            embedding TEXT
        )
        """,
    )
    conn.executemany(
        "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows,
    )
    conn.commit()
    conn.close()


def _vec(fill: float = 0.0) -> str:
    """Serialize a 768-dim vector (nomic-embed-text shape)."""
    return json.dumps([fill] * 768)


async def _collect(tap, config) -> list:
    """Iterate the async generator into a list."""
    docs = []
    async for doc in tap.extract(pool=None, config=config):
        docs.append(doc)
    return docs


@pytest.mark.unit
class TestOpenClawSQLiteTapMetadata:
    def test_name(self):
        assert OpenClawSQLiteTap.name == "openclaw_sqlite"

    def test_interval(self):
        assert OpenClawSQLiteTap.interval_seconds == 3600


@pytest.mark.unit
@pytest.mark.asyncio
class TestOpenClawSQLiteTapExtract:
    async def test_missing_file_yields_nothing(self, tmp_path):
        tap = OpenClawSQLiteTap()
        docs = await _collect(tap, {"sqlite_path": str(tmp_path / "missing.sqlite")})
        assert docs == []

    async def test_empty_sqlite_yields_nothing(self, tmp_path):
        sqlite_path = tmp_path / "empty.sqlite"
        _build_sqlite(sqlite_path, [])
        tap = OpenClawSQLiteTap()
        docs = await _collect(tap, {"sqlite_path": str(sqlite_path)})
        assert docs == []

    async def test_yields_documents_with_precomputed_embedding(self, tmp_path):
        sqlite_path = tmp_path / "main.sqlite"
        _build_sqlite(sqlite_path, [
            (
                1, "src/foo.py", "code", 1, 20,
                "nomic-embed-text", "def foo():\n    pass",
                _vec(0.1),
            ),
        ])

        tap = OpenClawSQLiteTap()
        docs = await _collect(tap, {"sqlite_path": str(sqlite_path)})

        assert len(docs) == 1
        doc = docs[0]
        assert doc.source_table == "memory"
        assert doc.source_id == "openclaw/src/foo.py#0"
        assert doc.writer == "openclaw"
        # Crucial: the Document carries the embedding so the runner
        # skips Ollama.
        assert doc.precomputed_embedding is not None
        assert len(doc.precomputed_embedding) == 768

    async def test_skips_rows_with_malformed_embedding(self, tmp_path):
        sqlite_path = tmp_path / "main.sqlite"
        _build_sqlite(sqlite_path, [
            # Row 1 has a bad JSON embedding.
            (1, "a.py", "code", 1, 10, "nomic", "text a", "not json"),
            # Row 2 has a wrong-shape vector.
            (2, "b.py", "code", 1, 10, "nomic", "text b", json.dumps([0.0] * 10)),
            # Row 3 is fine.
            (3, "c.py", "code", 1, 10, "nomic", "text c", _vec(0.3)),
        ])

        tap = OpenClawSQLiteTap()
        docs = await _collect(tap, {"sqlite_path": str(sqlite_path)})
        assert len(docs) == 1
        assert docs[0].source_id.startswith("openclaw/c.py")

    async def test_groups_chunks_per_path(self, tmp_path):
        sqlite_path = tmp_path / "main.sqlite"
        _build_sqlite(sqlite_path, [
            # Three chunks of the same file → chunk_index 0, 1, 2.
            (1, "big.py", "code", 1, 50, "nomic", "chunk 0", _vec(0.1)),
            (2, "big.py", "code", 51, 100, "nomic", "chunk 1", _vec(0.2)),
            (3, "big.py", "code", 101, 150, "nomic", "chunk 2", _vec(0.3)),
        ])

        tap = OpenClawSQLiteTap()
        docs = await _collect(tap, {"sqlite_path": str(sqlite_path)})

        assert len(docs) == 3
        ids = [d.source_id for d in docs]
        assert ids == [
            "openclaw/big.py#0",
            "openclaw/big.py#1",
            "openclaw/big.py#2",
        ]
        # All carry their own vector.
        for d in docs:
            assert d.precomputed_embedding is not None
            assert len(d.precomputed_embedding) == 768

    async def test_metadata_carries_line_range(self, tmp_path):
        sqlite_path = tmp_path / "main.sqlite"
        _build_sqlite(sqlite_path, [
            (1, "hello.py", "code", 5, 42, "nomic", "x = 1", _vec()),
        ])

        tap = OpenClawSQLiteTap()
        docs = await _collect(tap, {"sqlite_path": str(sqlite_path)})
        assert docs[0].metadata["start_line"] == 5
        assert docs[0].metadata["end_line"] == 42
        assert docs[0].metadata["path"] == "hello.py"
