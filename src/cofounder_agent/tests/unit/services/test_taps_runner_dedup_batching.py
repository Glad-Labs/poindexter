"""Dedup-batching perf tests for services.taps.runner (poindexter#735 item 1).

The chunk-0 dedup lookup used to run one ``SELECT`` per document, per source,
per hourly pass (51k calls in the prod pg_stat_statements window). ``run_tap``
now buffers documents and pre-fetches their existing chunk-0 hashes one query
per ``source_table`` (``source_id = ANY($1)``), threading the result into
``_store_document`` via ``existing_hash`` — so the per-document SELECT is gone
on the hot path. ``_store_document`` keeps the per-doc query as a fallback when
no ``existing_hash`` is supplied (direct callers / the config-seam tests).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.taps import runner as runner_mod

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _make_doc(text: str, source_id: str = "src-1", source_table: str = "demo"):
    return SimpleNamespace(
        text=text,
        writer="tester",
        source_id=source_id,
        source_table=source_table,
        metadata={},
    )


def _make_pool():
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value="DELETE 0")
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


class TestStoreDocumentExistingHashParam:
    async def test_provided_matching_hash_skips_without_querying(self):
        mem = MagicMock()
        mem.embed_model = "m"
        mem.store = AsyncMock()
        pool, conn = _make_pool()
        text = "hello world"
        match = runner_mod.content_hash(text)

        outcome = await runner_mod._store_document(
            mem, pool, _make_doc(text), existing_hash=match
        )

        assert outcome == "skipped"
        # Used the supplied hash → no per-document dedup SELECT, no store.
        conn.fetchval.assert_not_awaited()
        mem.store.assert_not_awaited()

    async def test_provided_mismatch_hash_embeds_without_querying(self):
        mem = MagicMock()
        mem.embed_model = "m"
        mem.store = AsyncMock()
        pool, conn = _make_pool()

        outcome = await runner_mod._store_document(
            mem, pool, _make_doc("fresh text"), existing_hash="STALE-HASH"
        )

        assert outcome == "embedded"
        conn.fetchval.assert_not_awaited()  # supplied hash short-circuits the query
        assert mem.store.await_count >= 1

    async def test_omitted_hash_falls_back_to_per_doc_query(self):
        # Backcompat: direct callers that don't supply existing_hash still get
        # the per-document dedup query (the config-seam tests rely on this).
        mem = MagicMock()
        mem.embed_model = "m"
        mem.store = AsyncMock()
        pool, conn = _make_pool()

        await runner_mod._store_document(mem, pool, _make_doc("text"))

        conn.fetchval.assert_awaited()  # queried, since no hash was supplied


class TestBatchExistingChunk0Hashes:
    async def test_one_fetch_per_source_table(self):
        pool, conn = _make_pool()

        async def _fetch(query, source_table, ids, model):
            if source_table == "demo":
                return [
                    {"source_id": "a", "content_hash": "ha"},
                    {"source_id": "b", "content_hash": "hb"},
                ]
            return []

        conn.fetch = AsyncMock(side_effect=_fetch)
        docs = [
            _make_doc("x", "a", "demo"),
            _make_doc("y", "b", "demo"),
            _make_doc("z", "c", "other"),
        ]

        result = await runner_mod._batch_existing_chunk0_hashes_for(pool, docs, "m")

        # One round-trip per distinct source_table (demo, other), not per doc.
        assert conn.fetch.await_count == 2
        assert result[("demo", "a")] == "ha"
        assert result[("demo", "b")] == "hb"
        assert ("other", "c") not in result  # absent source → treated as new


def _enable_plugin_config(monkeypatch):
    monkeypatch.setattr(
        runner_mod,
        "PluginConfig",
        MagicMock(load=AsyncMock(return_value=SimpleNamespace(enabled=True, config={}))),
    )


class TestRunTapBatchesDedup:
    async def test_dedup_uses_one_batch_fetch_not_per_doc_query(self, monkeypatch):
        _enable_plugin_config(monkeypatch)
        docs = [_make_doc(f"doc-{i}", f"id-{i}", "demo") for i in range(3)]

        async def _extract(pool, cfg):
            for d in docs:
                yield d

        tap = SimpleNamespace(name="faketap", extract=_extract)
        mem = MagicMock()
        mem.embed_model = "m"
        mem.store = AsyncMock()
        pool, conn = _make_pool()  # conn.fetch → [] (nothing stored yet)

        stats = await runner_mod.run_tap(tap, pool, mem)

        assert stats.embedded == 3
        # One batched dedup query for the source_table; ZERO per-doc fetchval.
        conn.fetchval.assert_not_awaited()
        assert conn.fetch.await_count == 1

    async def test_respects_dedup_batch_size_boundary(self, monkeypatch):
        _enable_plugin_config(monkeypatch)
        docs = [_make_doc(f"doc-{i}", f"id-{i}", "demo") for i in range(5)]

        async def _extract(pool, cfg):
            for d in docs:
                yield d

        tap = SimpleNamespace(name="faketap", extract=_extract)
        mem = MagicMock()
        mem.embed_model = "m"
        mem.store = AsyncMock()
        pool, conn = _make_pool()

        # batch size 2 over 5 docs → 3 flushes → 3 batched dedup fetches.
        stats = await runner_mod.run_tap(tap, pool, mem, dedup_batch_size=2)

        assert stats.embedded == 5
        conn.fetchval.assert_not_awaited()
        assert conn.fetch.await_count == 3
