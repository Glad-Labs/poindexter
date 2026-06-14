"""Unit tests for services.taps.runner config seams (config-externalisation audit).

Two behaviours are locked here:

* **D — embedding-model tag.** ``_store_document`` tags the dedup SELECT and
  the stale-chunk DELETE with ``mem.embed_model`` (the model the embedder
  actually used), not a parallel module constant. A mismatch would make the
  dedup query the wrong ``embedding_model`` and silently re-embed every doc.
* **H — chunk size.** ``max_chars`` threads through to ``chunk_text`` so the
  ingest chunk size is DB-tunable (``tap_chunk_max_chars``); ``None`` leaves
  ``chunk_text`` on its own default.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.taps import runner as runner_mod

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _make_pool(existing_hash=None):
    """asyncpg-shape pool whose conn.fetchval returns ``existing_hash`` and
    whose conn.execute is a recordable AsyncMock."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=existing_hash)
    conn.execute = AsyncMock(return_value="DELETE 0")
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


def _make_doc(text: str):
    # No precomputed_embedding attr → the chunking path runs.
    return SimpleNamespace(
        text=text,
        writer="tester",
        source_id="src-1",
        source_table="demo",
        metadata={},
    )


class TestStoreDocumentEmbedModelTag:
    async def test_dedup_uses_mem_embed_model(self):
        mem = MagicMock()
        mem.embed_model = "custom-embed-v2"
        mem.store = AsyncMock()
        pool, conn = _make_pool(existing_hash=None)

        outcome = await runner_mod._store_document(mem, pool, _make_doc("hello world"))

        assert outcome == "embedded"
        # _existing_chunk0_hash(conn, table, id, embedding_model)
        # → conn.fetchval(SQL, table, id, embedding_model)
        assert conn.fetchval.await_args.args[-1] == "custom-embed-v2"

    async def test_stale_delete_uses_mem_embed_model(self):
        mem = MagicMock()
        mem.embed_model = "custom-embed-v2"
        mem.store = AsyncMock()
        pool, conn = _make_pool(existing_hash=None)

        await runner_mod._store_document(mem, pool, _make_doc("hello world"))

        # Final _delete_stale_chunks(conn, table, id, embedding_model, n)
        # → conn.execute(SQL, table, id, embedding_model, n)
        delete_call = conn.execute.await_args_list[-1]
        assert "custom-embed-v2" in delete_call.args

    async def test_falls_back_to_constant_when_mem_lacks_embed_model(self):
        # A mem stand-in without an embed_model attr (e.g. the integration
        # FakeMemoryClient) must keep using the module fallback constant.
        mem = MagicMock(spec=["store"])
        mem.store = AsyncMock()
        pool, conn = _make_pool(existing_hash=None)

        await runner_mod._store_document(mem, pool, _make_doc("hello world"))

        assert conn.fetchval.await_args.args[-1] == runner_mod.EMBED_MODEL


class TestStoreDocumentChunkSize:
    async def test_max_chars_threads_to_chunk_text(self, monkeypatch):
        captured: list = []

        def _spy(text, **kwargs):
            captured.append(kwargs.get("max_chars", "DEFAULT"))
            return [text]

        monkeypatch.setattr(runner_mod, "chunk_text", _spy)
        mem = MagicMock()
        mem.embed_model = "m"
        mem.store = AsyncMock()
        pool, _ = _make_pool(existing_hash=None)

        await runner_mod._store_document(
            mem, pool, _make_doc("x" * 100), max_chars=50
        )
        assert captured == [50]

    async def test_max_chars_none_leaves_chunk_text_default(self, monkeypatch):
        captured: list = []

        def _spy(text, **kwargs):
            captured.append(kwargs.get("max_chars", "DEFAULT"))
            return [text]

        monkeypatch.setattr(runner_mod, "chunk_text", _spy)
        mem = MagicMock()
        mem.embed_model = "m"
        mem.store = AsyncMock()
        pool, _ = _make_pool(existing_hash=None)

        await runner_mod._store_document(mem, pool, _make_doc("x" * 100))
        assert captured == ["DEFAULT"]
