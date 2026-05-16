"""Tests for services/rag_engine.py — LlamaIndex retrieval layer over
the existing pgvector embeddings table (#210).

The ``rag_engine`` module lazy-imports LlamaIndex inside its factory
helpers so the module is importable without the SDK present, but the
helpers themselves (which every test below exercises) require
``llama_index``. The whole file is skipped when LlamaIndex isn't
installed (CI default — LlamaIndex is opt-in via the rerank/RAG
settings and not pinned in pyproject).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip(
    "llama_index",
    reason="LlamaIndex is an opt-in dep; install via "
    "`pip install llama-index llama-index-embeddings-ollama` to run.",
)

from services.rag_engine import get_rag_retriever  # noqa: E402


def _site_config(values: dict | None = None) -> MagicMock:
    sc = MagicMock()
    values = values or {}
    sc.get.side_effect = lambda key, default="": values.get(key, default)
    sc.get_int.side_effect = lambda key, default: values.get(key, default)
    sc.get_float.side_effect = lambda key, default: values.get(key, default)
    sc.get_bool.side_effect = lambda key, default=False: values.get(key, default)
    return sc


def _stub_embed_model() -> MagicMock:
    embed = MagicMock()
    embed.aget_query_embedding = AsyncMock(return_value=[0.1] * 768)
    return embed


def _row(source_table: str, source_id: str, text: str, similarity: float) -> dict:
    return {
        "source_table": source_table,
        "source_id": source_id,
        "text_preview": text,
        "metadata": {"title": text},
        "similarity": similarity,
    }


# ---------------------------------------------------------------------------
# Factory wires app_settings into the retriever
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFactoryDefaults:
    @pytest.mark.asyncio
    async def test_no_site_config_uses_module_defaults(self):
        retriever = await get_rag_retriever(pool=MagicMock())
        assert retriever._top_k == 5
        assert retriever._min_similarity == 0.3
        assert retriever._model_name == "nomic-embed-text"

    @pytest.mark.asyncio
    async def test_site_config_overrides_top_k(self):
        sc = _site_config({"rag_default_top_k": 10})
        retriever = await get_rag_retriever(pool=MagicMock(), site_config=sc)
        assert retriever._top_k == 10

    @pytest.mark.asyncio
    async def test_site_config_overrides_min_similarity(self):
        sc = _site_config({"rag_min_similarity": 0.5})
        retriever = await get_rag_retriever(pool=MagicMock(), site_config=sc)
        assert retriever._min_similarity == 0.5

    @pytest.mark.asyncio
    async def test_source_filter_csv_parsed(self):
        sc = _site_config({"rag_source_filter": "posts, brain , issues"})
        retriever = await get_rag_retriever(pool=MagicMock(), site_config=sc)
        assert retriever._source_filter == ["posts", "brain", "issues"]

    @pytest.mark.asyncio
    async def test_explicit_kwargs_win_over_site_config(self):
        sc = _site_config({"rag_default_top_k": 10})
        retriever = await get_rag_retriever(
            pool=MagicMock(), site_config=sc, top_k=3,
        )
        assert retriever._top_k == 3

    @pytest.mark.asyncio
    async def test_base_url_pulled_from_site_config(self):
        """`local_llm_api_url` is the canonical Ollama-base-URL setting
        (same key topic_ranking / llm_text use). The retriever stores
        it so `_get_embed_model` no longer reads OLLAMA_BASE_URL from
        env. Regression guard for the settings-discipline cleanup."""
        sc = _site_config({"local_llm_api_url": "http://my-ollama:11434"})
        retriever = await get_rag_retriever(pool=MagicMock(), site_config=sc)
        assert retriever._base_url == "http://my-ollama:11434"

    @pytest.mark.asyncio
    async def test_base_url_defaults_to_localhost_when_unset(self):
        """No site_config + no value = localhost loopback. Critically
        the retriever must NOT read OLLAMA_BASE_URL — that env-var path
        is what we just retired."""
        retriever = await get_rag_retriever(pool=MagicMock())
        assert retriever._base_url == "http://localhost:11434"

    @pytest.mark.asyncio
    async def test_base_url_passed_into_embed_model(self, monkeypatch):
        """Retriever's stored base_url flows through to `_get_embed_model`
        with no env-var read involved. Smoke-test the wiring."""
        from llama_index.core.schema import QueryBundle

        # Trip the test if anything reads OLLAMA_BASE_URL from env —
        # that's the legacy path the cleanup retired.
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://should-never-be-read:9999")

        captured: dict = {}

        def _capture_embed(model_name, base_url):
            captured["model_name"] = model_name
            captured["base_url"] = base_url
            return _stub_embed_model()

        sc = _site_config({"local_llm_api_url": "http://from-db:11434"})
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])

        retriever = await get_rag_retriever(pool=pool, site_config=sc)
        with patch(
            "services.rag_engine._get_embed_model", side_effect=_capture_embed,
        ):
            await retriever._aretrieve(QueryBundle(query_str="x"))

        assert captured["base_url"] == "http://from-db:11434"
        assert "should-never-be-read" not in captured["base_url"]


# ---------------------------------------------------------------------------
# Retrieval behavior
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRetrieverQuery:
    @pytest.mark.asyncio
    async def test_returns_nodes_with_scores(self):
        from llama_index.core.schema import QueryBundle

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[
            _row("posts", "abc-123", "FastAPI is a great framework.", 0.91),
            _row("brain", "decision-1", "We use Postgres.", 0.74),
        ])

        retriever = await get_rag_retriever(pool=pool, top_k=2)
        with patch(
            "services.rag_engine._get_embed_model",
            return_value=_stub_embed_model(),
        ):
            results = await retriever._aretrieve(QueryBundle(query_str="backend stack"))

        assert len(results) == 2
        scores = [r.score for r in results]
        assert scores == [0.91, 0.74]
        assert results[0].node.text.startswith("FastAPI")
        assert results[0].node.metadata["source_table"] == "posts"
        assert results[0].node.metadata["source_id"] == "abc-123"

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        from llama_index.core.schema import QueryBundle

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        retriever = await get_rag_retriever(pool=pool)
        results = await retriever._aretrieve(QueryBundle(query_str=""))
        assert results == []

    @pytest.mark.asyncio
    async def test_embedding_failure_returns_empty(self):
        from llama_index.core.schema import QueryBundle

        embed = MagicMock()
        embed.aget_query_embedding = AsyncMock(side_effect=Exception("ollama down"))

        pool = MagicMock()
        retriever = await get_rag_retriever(pool=pool)
        with patch("services.rag_engine._get_embed_model", return_value=embed):
            results = await retriever._aretrieve(QueryBundle(query_str="x"))
        assert results == []

    @pytest.mark.asyncio
    async def test_db_failure_returns_empty(self):
        from llama_index.core.schema import QueryBundle

        pool = MagicMock()
        pool.fetch = AsyncMock(side_effect=Exception("pg connection lost"))
        retriever = await get_rag_retriever(pool=pool)
        with patch(
            "services.rag_engine._get_embed_model",
            return_value=_stub_embed_model(),
        ):
            results = await retriever._aretrieve(QueryBundle(query_str="x"))
        assert results == []

    @pytest.mark.asyncio
    async def test_source_filter_appears_in_query(self):
        from llama_index.core.schema import QueryBundle

        captured: dict[str, Any] = {}

        async def _fake_fetch(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return []

        pool = MagicMock()
        pool.fetch = _fake_fetch
        retriever = await get_rag_retriever(
            pool=pool, source_filter=["posts", "brain"],
        )
        with patch(
            "services.rag_engine._get_embed_model",
            return_value=_stub_embed_model(),
        ):
            await retriever._aretrieve(QueryBundle(query_str="x"))

        assert "source_table IN" in captured["sql"]
        # Args after [vec_str, min_sim] are the source_filter values.
        assert captured["args"][2:] == ("posts", "brain")


# ---------------------------------------------------------------------------
# Phase C — Hybrid + rerank wiring
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHybridFactory:
    @pytest.mark.asyncio
    async def test_hybrid_flag_wraps_in_hybrid_retriever(self):
        retriever = await get_rag_retriever(pool=MagicMock(), hybrid=True)
        # Outermost class should be the hybrid wrapper
        assert "Hybrid" in type(retriever).__name__

    @pytest.mark.asyncio
    async def test_rerank_flag_wraps_in_rerank_retriever(self):
        retriever = await get_rag_retriever(pool=MagicMock(), rerank=True)
        assert "Rerank" in type(retriever).__name__

    @pytest.mark.asyncio
    async def test_both_flags_wrap_rerank_outside(self):
        # Order: vector → hybrid → rerank, so rerank wraps the hybrid.
        retriever = await get_rag_retriever(
            pool=MagicMock(), hybrid=True, rerank=True,
        )
        assert "Rerank" in type(retriever).__name__
        # Inner should be the hybrid
        assert "Hybrid" in type(retriever._inner).__name__

    @pytest.mark.asyncio
    async def test_site_config_enables_hybrid(self):
        sc = _site_config({"rag_hybrid_enabled": True})
        retriever = await get_rag_retriever(pool=MagicMock(), site_config=sc)
        assert "Hybrid" in type(retriever).__name__


@pytest.mark.unit
class TestHybridRRF:
    @pytest.mark.asyncio
    async def test_rrf_fusion_orders_results(self):
        from llama_index.core.schema import (
            NodeWithScore,
            QueryBundle,
            TextNode,
        )

        # Inner vector retriever returns 2 nodes (ranks 1, 2 in vector
        # space). BM25 returns 2 different ones in different order.
        vector_nodes = [
            NodeWithScore(
                node=TextNode(text="vec hit 1", id_="posts:V1",
                              metadata={"source_table": "posts", "source_id": "V1"}),
                score=0.9,
            ),
            NodeWithScore(
                node=TextNode(text="vec hit 2", id_="posts:V2",
                              metadata={"source_table": "posts", "source_id": "V2"}),
                score=0.7,
            ),
        ]
        bm25_pairs = [("posts:V2", 0.5), ("posts:V3", 0.4)]
        # V2 appears in both lists — should rank highest after RRF.

        inner = MagicMock()
        inner._aretrieve = AsyncMock(return_value=vector_nodes)

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[
            {
                "source_table": "posts", "source_id": "V3",
                "text_preview": "lex-only hit", "metadata": {},
            },
        ])

        from services.rag_engine import _build_hybrid_retriever_class
        cls = _build_hybrid_retriever_class()
        h = cls(
            vector_retriever=inner, pool=pool, top_k=3,
            min_similarity=0.3, source_filter=None, site_config=None,
        )
        # Stub the BM25 query to return our scripted pairs
        h._bm25_search = AsyncMock(return_value=bm25_pairs)

        results = await h._aretrieve(QueryBundle(query_str="test"))

        # V2 was rank-2 in vector, rank-1 in BM25 → highest RRF.
        # V1 was rank-1 in vector only.
        # V3 was rank-2 in BM25 only.
        ids = [r.node.node_id for r in results]
        assert ids[0] == "posts:V2"  # appears in both lists
        assert "posts:V1" in ids
        assert "posts:V3" in ids


# ---------------------------------------------------------------------------
# Phase C — Cross-encoder rerank
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCrossEncoderRerank:
    """Stub the cross-encoder model so the tests don't touch
    sentence-transformers (which downloads ~80MB on first call and is
    too heavy for unit tests). Verifies:

    - the rerank wrapper reorders the inner retriever's candidates
      according to the cross-encoder's pair scores
    - missing sentence-transformers (the prod hot-path warning we just
      fixed in pyproject) degrades gracefully to passthrough rather
      than raising
    - top_k slicing is applied after rerank
    """

    @pytest.mark.asyncio
    async def test_rerank_reorders_candidates_by_pair_scores(self):
        from llama_index.core.schema import (
            NodeWithScore,
            QueryBundle,
            TextNode,
        )

        # Inner retriever returns 3 nodes in initial order A, B, C.
        candidates = [
            NodeWithScore(
                node=TextNode(text="alpha doc", id_="posts:A"),
                score=0.9,
            ),
            NodeWithScore(
                node=TextNode(text="beta doc", id_="posts:B"),
                score=0.8,
            ),
            NodeWithScore(
                node=TextNode(text="gamma doc", id_="posts:C"),
                score=0.7,
            ),
        ]
        inner = MagicMock()
        inner._aretrieve = AsyncMock(return_value=candidates)

        # Cross-encoder will score (query, A)=0.1, (query, B)=0.9,
        # (query, C)=0.5 — so expected post-rerank order is B, C, A.
        fake_model = MagicMock()
        fake_model.predict = MagicMock(return_value=[0.1, 0.9, 0.5])

        from services.rag_engine import (
            _RERANKER_CACHE,
            _build_rerank_retriever_class,
        )
        _RERANKER_CACHE.clear()  # don't bleed across tests
        cls = _build_rerank_retriever_class()
        r = cls(inner=inner, top_k=3, site_config=None)
        # Pre-populate cache so _get_model returns our stub without
        # importing sentence-transformers.
        _RERANKER_CACHE["cross-encoder/ms-marco-MiniLM-L-6-v2"] = fake_model

        results = await r._aretrieve(QueryBundle(query_str="query text"))

        ids = [n.node.node_id for n in results]
        assert ids == ["posts:B", "posts:C", "posts:A"]
        # Scores are the cross-encoder's raw outputs, not the original
        # vector scores — caller can correlate trends if needed.
        assert results[0].score == pytest.approx(0.9)
        assert results[2].score == pytest.approx(0.1)

    @pytest.mark.asyncio
    async def test_rerank_top_k_truncates_after_reorder(self):
        from llama_index.core.schema import (
            NodeWithScore,
            QueryBundle,
            TextNode,
        )

        candidates = [
            NodeWithScore(node=TextNode(text="a", id_="X:1"), score=0.9),
            NodeWithScore(node=TextNode(text="b", id_="X:2"), score=0.8),
            NodeWithScore(node=TextNode(text="c", id_="X:3"), score=0.7),
        ]
        inner = MagicMock()
        inner._aretrieve = AsyncMock(return_value=candidates)

        fake_model = MagicMock()
        # Last candidate scores highest after rerank.
        fake_model.predict = MagicMock(return_value=[0.1, 0.2, 0.99])

        from services.rag_engine import (
            _RERANKER_CACHE,
            _build_rerank_retriever_class,
        )
        _RERANKER_CACHE.clear()
        cls = _build_rerank_retriever_class()
        r = cls(inner=inner, top_k=1, site_config=None)
        _RERANKER_CACHE["cross-encoder/ms-marco-MiniLM-L-6-v2"] = fake_model

        results = await r._aretrieve(QueryBundle(query_str="q"))
        assert len(results) == 1
        assert results[0].node.node_id == "X:3"

    @pytest.mark.asyncio
    async def test_rerank_passthrough_on_missing_sentence_transformers(self):
        """When the operator has flipped rag_rerank_enabled=true but
        the worker image lacks sentence-transformers, the rerank
        wrapper must degrade to passthrough (return the inner
        candidates truncated to top_k) without raising. This is the
        prod regression we fixed by pinning sentence-transformers in
        pyproject — the test pins the contract.
        """
        from llama_index.core.schema import (
            NodeWithScore,
            QueryBundle,
            TextNode,
        )

        candidates = [
            NodeWithScore(node=TextNode(text="a", id_="P:1"), score=0.9),
            NodeWithScore(node=TextNode(text="b", id_="P:2"), score=0.7),
        ]
        inner = MagicMock()
        inner._aretrieve = AsyncMock(return_value=candidates)

        from services.rag_engine import (
            _RERANKER_CACHE,
            _build_rerank_retriever_class,
        )
        _RERANKER_CACHE.clear()
        cls = _build_rerank_retriever_class()
        r = cls(inner=inner, top_k=2, site_config=None)

        # Force _get_model to raise ImportError exactly like the
        # missing-dep prod failure.
        def _raise_import(*_a, **_kw):
            raise ImportError("No module named 'sentence_transformers'")

        with patch.object(r, "_get_model", side_effect=_raise_import):
            results = await r._aretrieve(QueryBundle(query_str="q"))

        # Passthrough preserves inner ordering.
        ids = [n.node.node_id for n in results]
        assert ids == ["P:1", "P:2"]


# ---------------------------------------------------------------------------
# Gate-setting wiring — explicit/site_config/None
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRerankGate:
    @pytest.mark.asyncio
    async def test_rerank_site_config_default_false_no_wrap(self):
        """A bare site_config (no rag_rerank_enabled key) must NOT
        wrap the retriever in rerank. Otherwise the cross-encoder
        would warmup on every operator who hasn't opted in."""
        sc = _site_config({})  # no rag_rerank_enabled value
        retriever = await get_rag_retriever(pool=MagicMock(), site_config=sc)
        assert "Rerank" not in type(retriever).__name__

    @pytest.mark.asyncio
    async def test_rerank_explicit_kwarg_wins_over_site_config(self):
        sc = _site_config({"rag_rerank_enabled": True})
        retriever = await get_rag_retriever(
            pool=MagicMock(), site_config=sc, rerank=False,
        )
        assert "Rerank" not in type(retriever).__name__

    @pytest.mark.asyncio
    async def test_hybrid_explicit_kwarg_wins_over_site_config(self):
        sc = _site_config({"rag_hybrid_enabled": True})
        retriever = await get_rag_retriever(
            pool=MagicMock(), site_config=sc, hybrid=False,
        )
        assert "Hybrid" not in type(retriever).__name__
