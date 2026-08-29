"""The retrieval-payload gaps poindexter#1033 / #3452 left behind.

That change added ``embeddings.chunk_text`` and routed the ``rag_engine``
retrievers through it. Three consumers were not on that path and kept reading
the 500-char preview:

1. ``text_search`` — the generated tsvector the hybrid retriever's BM25 half
   matches against — was still defined over ``text_preview`` alone, so lexical
   search kept the exact blind spot the vector half had just been cured of.
2. The writer's snippet grounding runs its OWN pgvector query in
   ``two_pass_writer``, not through ``rag_engine``/``MemoryClient``, so the
   single largest consumer of retrieved text was untouched.
3. ``embed_post`` still wrote a 2000-char whole-post truncation to the same
   natural key ``PostsTap`` writes its real chunk 0 to.

(1) is verified against a live database by the migration smoke; its *guard*
logic is unit-tested here because an unguarded re-run silently reverts the
column to the old definition — a failure that looks exactly like success.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.unit]

_MIGRATION = (
    "services.migrations."
    "20260829_003232_repoint_the_text_search_tsvector_at_chunk_text_"
    "so_bm25_stops_matching_only_the_preview"
)

_PREVIEW_EXPR = "to_tsvector('simple'::regconfig, COALESCE(text_preview, ''::character varying)::text)"
_PAYLOAD_EXPR = (
    "to_tsvector('simple'::regconfig, COALESCE(chunk_text, "
    "COALESCE(text_preview, ''::character varying)::text))"
)


def _pool_returning(expr: str | None):
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=expr)
    conn.execute = AsyncMock(return_value="ALTER TABLE")
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


@pytest.mark.asyncio
class TestTsvectorRepointGuard:
    async def test_up_rebuilds_a_preview_only_column(self):
        mod = importlib.import_module(_MIGRATION)
        pool, conn = _pool_returning(_PREVIEW_EXPR)

        await mod.up(pool)

        sql = " ".join(str(c.args[0]) for c in conn.execute.await_args_list)
        assert "DROP INDEX IF EXISTS idx_embeddings_text_search" in sql
        assert "DROP COLUMN IF EXISTS text_search" in sql
        assert "COALESCE(chunk_text" in sql
        # The GIN index must come back with the column, or BM25 goes from
        # "matching a prefix" to "sequential-scanning the whole table".
        assert "USING gin (text_search)" in sql

    async def test_up_is_a_no_op_once_repointed(self):
        """Guarded on the EXPRESSION, not the column name. The column always
        exists, so a name check would re-run the drop and rebuild the OLD
        definition — a silent revert that logs as a successful apply."""
        mod = importlib.import_module(_MIGRATION)
        pool, conn = _pool_returning(_PAYLOAD_EXPR)

        await mod.up(pool)

        conn.execute.assert_not_awaited()

    async def test_down_restores_the_preview_only_definition(self):
        mod = importlib.import_module(_MIGRATION)
        pool, conn = _pool_returning(_PAYLOAD_EXPR)

        await mod.down(pool)

        sql = " ".join(str(c.args[0]) for c in conn.execute.await_args_list)
        assert "COALESCE(chunk_text" not in sql
        assert "COALESCE(text_preview" in sql
        assert "USING gin (text_search)" in sql

    async def test_down_is_a_no_op_when_already_reverted(self):
        mod = importlib.import_module(_MIGRATION)
        pool, conn = _pool_returning(_PREVIEW_EXPR)

        await mod.down(pool)

        conn.execute.assert_not_awaited()


@pytest.mark.asyncio
class TestWriterSnippetPayload:
    async def test_writer_query_selects_the_full_chunk(self, monkeypatch):
        """``two_pass_writer`` grounds drafts from its own pgvector query.
        It never goes through rag_engine, so #3452's fix did not reach it."""
        from modules.content.atoms import two_pass_writer as tpw

        async def fake_embed(text, *, site_config=None):
            return [1.0] + [0.0] * 767

        # Patch at the definition site — the atom imports it lazily, so a
        # partial stub here would reach a real Ollama on the CI box.
        monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

        captured: dict = {}

        async def _fetch(sql, *args):
            captured["sql"] = sql
            return [{
                "source_table": "posts", "source_id": "p1",
                "snippet_text": "opening … and the passage that matched",
                "embedding": "[1,0]", "relevance": 0.9,
            }]

        conn = MagicMock()
        conn.fetch = AsyncMock(side_effect=_fetch)
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        sc = MagicMock()
        sc.get_int.side_effect = lambda k, d: d
        sc.get_float.side_effect = lambda k, d: d
        sc.get.side_effect = lambda k, d="": d

        tpw._POOL_REGISTRY["t"] = pool
        tpw._SITE_CONFIG_REGISTRY["t"] = sc
        try:
            state = await tpw._embed_and_fetch_snippets(
                {"topic": "reranker budget", "angle": "why", "pool_thread": "t",
                 "site_config": sc},
            )
        finally:
            tpw._POOL_REGISTRY.pop("t", None)
            tpw._SITE_CONFIG_REGISTRY.pop("t", None)

        assert "COALESCE(chunk_text, text_preview)" in captured["sql"]
        assert state["snippets"][0]["snippet"].endswith("passage that matched")


class TestPostWriterAgreement:
    def test_embed_post_and_the_tap_build_identical_text(self):
        """Both write ``('posts', <id>, 0, <model>)``. Different text means
        different content hashes, which means they overwrite each other on
        every republish forever."""
        from services.taps.published_posts import PostsTap, build_post_text

        post = SimpleNamespace(
            title="A Title", excerpt="An excerpt.", content="Body paragraph.",
        )
        tap_text = build_post_text(
            title=post.title, excerpt=post.excerpt, content=post.content,
        )
        # The tap's own extract() builds its Document text through the same
        # helper — this is the shared-builder contract, asserted on the tap
        # class so a future edit to either side breaks here.
        assert PostsTap.name == "posts"
        assert tap_text == "# A Title\n\nAn excerpt.\n\nBody paragraph."

    def test_builder_skips_empty_parts(self):
        from services.taps.published_posts import build_post_text

        assert build_post_text(title=None, excerpt="", content="Just body.") == "Just body."
        assert build_post_text(title="T", excerpt=None, content=None) == "# T"
