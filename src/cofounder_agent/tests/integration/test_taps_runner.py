"""Integration tests for services.taps.runner.

Exercises the full runner path end-to-end against real services:
registered Tap discovery → PluginConfig → extract() → store + dedup
behavior. Uses the Phase A0 harness + a tiny in-memory demo Tap
instead of the real auto-embed flow so each test stays focused.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from plugins import Document, PluginConfig
from services.taps.runner import run_tap
from tests.integration.conftest import requires_real_services

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, requires_real_services]


# ---------------------------------------------------------------------------
# MemoryClient stand-in — records stored documents, doesn't actually call Ollama.
# The runner's job under test is iteration + dedup + chunking, not embedding.
# ---------------------------------------------------------------------------


class _FakeMemoryClient:
    def __init__(self):
        self.stored: list[dict[str, Any]] = []

    async def store(
        self,
        text,
        writer,
        source_id,
        source_table,
        chunk_index,
        metadata,
        content_hash,
        origin_path,
    ):
        self.stored.append(
            {
                "text": text,
                "writer": writer,
                "source_id": source_id,
                "source_table": source_table,
                "chunk_index": chunk_index,
                "metadata": metadata,
                "content_hash": content_hash,
            }
        )


class _EnsureEmbeddingsTable:
    """Context manager that creates a minimal embeddings table in the
    test DB so the runner's dedup SELECT + stale-chunk DELETE work.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def __aenter__(self):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    id SERIAL PRIMARY KEY,
                    source_table VARCHAR(50) NOT NULL,
                    source_id VARCHAR(255) NOT NULL,
                    content_hash VARCHAR(64) NOT NULL,
                    chunk_index INTEGER DEFAULT 0,
                    text_preview VARCHAR(500),
                    embedding_model VARCHAR(100) NOT NULL,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (source_table, source_id, chunk_index, embedding_model)
                )
                """
            )
        return self._pool

    async def __aexit__(self, *exc):
        return None


class _OneDocTap:
    """Tap that yields one Document per extract() call."""

    name = "one_doc_tap"
    interval_seconds = 0

    def __init__(self, doc: Document):
        self._doc = doc

    async def extract(self, pool: Any, config: dict[str, Any]) -> AsyncIterator[Document]:
        yield self._doc


class _MultiDocTap:
    """Tap that yields N Documents."""

    name = "multi_doc_tap"
    interval_seconds = 0

    def __init__(self, docs: list[Document]):
        self._docs = docs

    async def extract(self, pool: Any, config: dict[str, Any]) -> AsyncIterator[Document]:
        for d in self._docs:
            yield d


class TestRunTapBasic:
    async def test_embeds_document_on_first_run(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with _EnsureEmbeddingsTable(clean_test_tables):
            mem = _FakeMemoryClient()
            tap = _OneDocTap(Document(
                source_id="demo/1",
                source_table="demo",
                text="hello world",
                writer="test",
            ))
            stats = await run_tap(tap, clean_test_tables, mem)

        assert stats.embedded == 1
        assert stats.skipped == 0
        assert stats.failed == 0
        assert len(mem.stored) == 1

    async def test_skips_on_second_run_with_unchanged_content(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with _EnsureEmbeddingsTable(clean_test_tables):
            mem = _FakeMemoryClient()
            tap = _OneDocTap(Document(
                source_id="demo/2",
                source_table="demo",
                text="static content",
                writer="test",
            ))

            first = await run_tap(tap, clean_test_tables, mem)
            # Simulate the chunk-0 row now existing — runner's dedup query reads it.
            # Our fake mem doesn't write to the real table, so we insert the hash manually.
            from services.taps._chunking import content_hash
            async with clean_test_tables.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO embeddings (source_table, source_id, content_hash,
                                             chunk_index, text_preview, embedding_model)
                    VALUES ('demo', 'demo/2', $1, 0, 'static content', 'nomic-embed-text')
                    ON CONFLICT DO NOTHING
                    """,
                    content_hash("static content"),
                )

            second = await run_tap(tap, clean_test_tables, mem)

        assert first.embedded == 1
        assert second.embedded == 0
        assert second.skipped == 1

    async def test_empty_document_text_skipped(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with _EnsureEmbeddingsTable(clean_test_tables):
            mem = _FakeMemoryClient()
            tap = _OneDocTap(Document(
                source_id="demo/empty",
                source_table="demo",
                text="   ",
                writer="test",
            ))
            stats = await run_tap(tap, clean_test_tables, mem)

        assert stats.skipped == 1
        assert stats.embedded == 0
        assert len(mem.stored) == 0


class TestDisabledTap:
    async def test_disabled_tap_reports_enabled_false(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with clean_test_tables.acquire() as conn:
            await PluginConfig(
                plugin_type="tap",
                name="one_doc_tap",
                enabled=False,
            ).save(conn)

        async with _EnsureEmbeddingsTable(clean_test_tables):
            mem = _FakeMemoryClient()
            tap = _OneDocTap(Document(
                source_id="demo/disabled",
                source_table="demo",
                text="should not be stored",
                writer="test",
            ))
            stats = await run_tap(tap, clean_test_tables, mem)

        assert stats.enabled is False
        assert stats.embedded == 0
        assert len(mem.stored) == 0


class TestChunking:
    async def test_oversize_doc_splits_into_multiple_stored_chunks(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with _EnsureEmbeddingsTable(clean_test_tables):
            mem = _FakeMemoryClient()
            big = "# A\n" + ("a" * 4000) + "\n# B\n" + ("b" * 4000)
            tap = _OneDocTap(Document(
                source_id="demo/big",
                source_table="demo",
                text=big,
                writer="test",
            ))
            stats = await run_tap(tap, clean_test_tables, mem)

        assert stats.embedded == 1  # one document from the Tap's perspective
        assert len(mem.stored) >= 2  # multiple chunks stored
        indices = sorted(s["chunk_index"] for s in mem.stored)
        assert indices == list(range(len(mem.stored)))

    async def test_every_chunk_records_total_chunks_metadata(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with _EnsureEmbeddingsTable(clean_test_tables):
            mem = _FakeMemoryClient()
            big = "# A\n" + ("a" * 4000) + "\n# B\n" + ("b" * 4000)
            tap = _OneDocTap(Document(
                source_id="demo/big2",
                source_table="demo",
                text=big,
                writer="test",
            ))
            await run_tap(tap, clean_test_tables, mem)

        total = len(mem.stored)
        assert all(s["metadata"]["total_chunks"] == total for s in mem.stored)


class TestErrorIsolation:
    async def test_store_failure_counts_as_failed_without_killing_run(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        class FlakyMem(_FakeMemoryClient):
            async def store(self, *args, **kwargs):
                if len(self.stored) == 0:
                    self.stored.append(kwargs)  # sentinel
                    raise RuntimeError("flaky")
                await super().store(*args, **kwargs)

        async with _EnsureEmbeddingsTable(clean_test_tables):
            mem = FlakyMem()
            tap = _MultiDocTap([
                Document(source_id=f"demo/{i}", source_table="demo",
                         text=f"item {i}", writer="test")
                for i in range(3)
            ])
            stats = await run_tap(tap, clean_test_tables, mem)

        assert stats.failed == 1  # first doc's store raised
        # Remaining docs still attempted.
        assert stats.embedded + stats.skipped >= 1
