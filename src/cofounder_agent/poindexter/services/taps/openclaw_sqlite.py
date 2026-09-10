"""OpenClawSQLiteTap — ingest OpenClaw's pre-embedded chunks into pgvector.

OpenClaw stores its own 768-dim nomic-embed-text vectors in
``~/.openclaw/memory/main.sqlite``. Re-embedding would waste Ollama
cycles *and* produce slightly different vectors (version drift). The Tap
ships those vectors through unchanged via
``Document.precomputed_embedding``.

Replaces the inline ``sync_openclaw_sqlite`` phase in
``scripts/auto-embed.py``. Config (``plugin.tap.openclaw_sqlite``):

- ``enabled`` (default ``true``)
- ``interval_seconds`` (default 3600) — how often the scheduler calls
  extract()
- ``config.sqlite_path`` — override the default
  ``~/.openclaw/memory/main.sqlite``. Primary use is tests.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from plugins.tap import Document

logger = logging.getLogger(__name__)


DEFAULT_SQLITE_PATH = Path.home() / ".openclaw" / "memory" / "main.sqlite"


class OpenClawSQLiteTap:
    """Reads OpenClaw chunks + their pre-computed embeddings."""

    name = "openclaw_sqlite"
    interval_seconds = 3600  # 1h

    async def extract(
        self,
        pool: Any,  # noqa: ARG002 — contract arg, not needed for sqlite read
        config: dict[str, Any],
    ) -> AsyncIterator[Document]:
        sqlite_path = Path(config.get("sqlite_path") or str(DEFAULT_SQLITE_PATH))
        if not sqlite_path.exists():
            logger.info(
                "[openclaw_sqlite] %s not found — nothing to ingest", sqlite_path,
            )
            return

        try:
            import sqlite3
        except ImportError:  # pragma: no cover — stdlib everywhere we target
            logger.warning("[openclaw_sqlite] sqlite3 not importable")
            return

        try:
            conn = sqlite3.connect(str(sqlite_path))
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, path, source, start_line, end_line, model, text, embedding
                  FROM chunks
                 WHERE embedding IS NOT NULL
                 ORDER BY path, start_line
                """,
            )
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            logger.error("[openclaw_sqlite] could not read %s: %s", sqlite_path, e)
            return

        logger.info("[openclaw_sqlite] %d pre-embedded chunks", len(rows))

        # Group chunks by path so chunk_index stays stable within a file.
        chunks_by_path: dict[str, list[tuple]] = {}
        for row in rows:
            chunks_by_path.setdefault(row[1], []).append(row)

        for path, chunks in chunks_by_path.items():
            source_id_base = f"openclaw/{path}"
            for chunk_index, (_cid, _path, _source, start_line, end_line, _model, text, emb_str) in enumerate(chunks):
                try:
                    vec = json.loads(emb_str)
                except Exception as e:
                    logger.warning(
                        "[openclaw_sqlite] %s#%d: unreadable embedding: %s",
                        source_id_base, chunk_index, e,
                    )
                    continue

                if not isinstance(vec, list) or len(vec) != 768:
                    logger.warning(
                        "[openclaw_sqlite] %s#%d: unexpected embedding shape (got %s)",
                        source_id_base, chunk_index, type(vec).__name__,
                    )
                    continue

                # One Document per chunk — the runner's chunking path is
                # skipped because precomputed_embedding != None triggers
                # the single-row store path.
                content = text or ""
                doc = Document(
                    source_id=f"{source_id_base}#{chunk_index}",
                    source_table="memory",
                    text=content,
                    metadata={
                        "path": path,
                        "start_line": start_line,
                        "end_line": end_line,
                        "openclaw_chunk_index": chunk_index,
                        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        "origin_path": path,
                    },
                    writer="openclaw",
                    precomputed_embedding=vec,
                )
                yield doc
