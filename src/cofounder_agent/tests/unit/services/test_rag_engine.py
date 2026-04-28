"""Tests for services/rag_engine.py — LlamaIndex retrieval layer over
the existing pgvector embeddings table (#210)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.rag_engine import get_rag_retriever


def _site_config(values: dict | None = None) -> MagicMock:
    sc = MagicMock()
    values = values or {}
    sc.get.side_effect = lambda key, default="": values.get(key, default)
    sc.get_int.side_effect = lambda key, default: values.get(key, default)
    sc.get_float.side_effect = lambda key, default: values.get(key, default)
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
