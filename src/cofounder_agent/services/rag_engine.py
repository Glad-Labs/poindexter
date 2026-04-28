"""LlamaIndex retrieval layer over our existing pgvector embeddings (#210).

Wraps the homegrown ``EmbeddingsDatabase.search_similar`` query path
behind a ``BaseRetriever`` so stages get a clean industry-standard
query interface (LlamaIndex Document / NodeWithScore conventions, Ragas
eval compatibility, OpenInference tracing hooks etc).

Doesn't migrate the storage layer — our 16k+ existing embeddings stay
in the existing ``embeddings`` table (768-dim, nomic-embed-text,
indexed via HNSW). This is a translation layer over the wire format,
not a re-shape of the data.

Why a custom BaseRetriever (not VectorStoreIndex)
-------------------------------------------------

LlamaIndex ships a ``PGVectorStore`` that expects its own schema
(``data_<index_name>`` tables). We have ~16k embeddings already
indexed across 7 source types in our schema; wholesale migration is
churn for no benefit. The ``BaseRetriever`` subclass below queries
the existing schema directly and yields ``NodeWithScore`` objects
that any LlamaIndex consumer (query engines, Ragas evaluators,
LlamaIndex chat agents, etc) accepts.

Activation
----------

The retriever is dormant until consumed. Simplest call site:

    from services.rag_engine import get_rag_retriever
    retriever = await get_rag_retriever(pool, top_k=5)
    nodes = await retriever.aretrieve("how to bootstrap a SaaS")

Each ``NodeWithScore`` has ``node.text`` (preview / chunk),
``node.metadata`` (source_table, source_id, original metadata), and
``score`` (cosine similarity 0-1).
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from services.logger_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Embedding model — Ollama-native via LlamaIndex's adapter
# ---------------------------------------------------------------------------


_EMBED_MODEL_CACHE: dict[str, Any] = {}


def _get_embed_model(model_name: str = "nomic-embed-text") -> Any:
    """Lazy-load + cache the Ollama embedding adapter.

    Mirrors the model used to populate the embeddings table — without
    matching, queries land in a different vector space and get poor
    similarity scores. The cache keys on model name so multiple models
    coexist (e.g. the embedding model used by ``embeddings.embedding_model``
    is the canonical one; future migrations to a different model would
    write to a new column / new index).
    """
    if model_name in _EMBED_MODEL_CACHE:
        return _EMBED_MODEL_CACHE[model_name]

    from llama_index.embeddings.ollama import OllamaEmbedding

    base_url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    embed = OllamaEmbedding(
        model_name=model_name,
        base_url=base_url,
    )
    _EMBED_MODEL_CACHE[model_name] = embed
    return embed


# ---------------------------------------------------------------------------
# BaseRetriever subclass over our pgvector schema
# ---------------------------------------------------------------------------


class _PoindexterRetriever:
    """LlamaIndex BaseRetriever over the ``embeddings`` table.

    Defined as a forward-declared name; the actual ``BaseRetriever``
    parent is bound at first instantiation so this module can import
    even when llama-index isn't installed (the test path / minimal
    deployment shape).
    """

    pass


def _build_retriever_class():
    """Lazy-build the BaseRetriever subclass.

    Same lazy-binding pattern as ``services/guardrails_rails.py`` —
    keeps top-of-module import cheap when callers don't need it.
    """
    from llama_index.core.retrievers import BaseRetriever
    from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode

    class PoindexterPGVectorRetriever(BaseRetriever):
        """Query our existing ``embeddings`` table via pgvector cosine
        similarity, return LlamaIndex NodeWithScore objects.

        Honors the existing app_settings knobs:
          - ``embedding_model`` (default 'nomic-embed-text')
          - ``rag_min_similarity`` (default 0.3)
          - ``rag_default_top_k`` (default 5)
          - ``rag_source_filter`` (CSV; default empty = all source_tables)
        """

        def __init__(
            self,
            *,
            pool: Any,
            top_k: int = 5,
            min_similarity: float = 0.3,
            source_filter: list[str] | None = None,
            model_name: str = "nomic-embed-text",
        ) -> None:
            super().__init__()
            self._pool = pool
            self._top_k = top_k
            self._min_similarity = min_similarity
            self._source_filter = source_filter
            self._model_name = model_name

        async def _aretrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
            text = query_bundle.query_str
            if not text or not text.strip():
                return []

            embed = _get_embed_model(self._model_name)
            try:
                vec = await embed.aget_query_embedding(text)
            except Exception as e:
                logger.warning("[rag] embedding query failed: %s", e)
                return []

            vec_str = "[" + ",".join(str(v) for v in vec) + "]"

            # Identical filter clause to EmbeddingsDatabase.search_similar
            # — keep them aligned so the legacy and LlamaIndex paths
            # see the same corpus.
            sql_parts = [
                "SELECT source_table, source_id, text_preview, metadata, "
                "1 - (embedding <=> $1::vector) AS similarity "
                "FROM embeddings",
                "WHERE 1 - (embedding <=> $1::vector) >= $2",
            ]
            params: list[Any] = [vec_str, self._min_similarity]
            if self._source_filter:
                placeholders = ", ".join(
                    f"${i+3}" for i in range(len(self._source_filter))
                )
                sql_parts.append(f"AND source_table IN ({placeholders})")
                params.extend(self._source_filter)
            sql_parts.append(
                f"ORDER BY embedding <=> $1::vector LIMIT {int(self._top_k)}"
            )
            sql = " ".join(sql_parts)

            try:
                rows = await self._pool.fetch(sql, *params)
            except Exception as e:
                logger.warning("[rag] pgvector query failed: %s", e)
                return []

            results: list[NodeWithScore] = []
            for row in rows:
                metadata = dict(row.get("metadata") or {})
                metadata.update({
                    "source_table": row["source_table"],
                    "source_id": row["source_id"],
                })
                node = TextNode(
                    text=row.get("text_preview") or "",
                    metadata=metadata,
                    id_=f"{row['source_table']}:{row['source_id']}",
                )
                results.append(NodeWithScore(node=node, score=float(row["similarity"])))
            return results

        # LlamaIndex's BaseRetriever requires a sync ``_retrieve``;
        # delegate to the async path via asyncio for tests + REPL use.
        def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
            import asyncio
            return asyncio.run(self._aretrieve(query_bundle))

    return PoindexterPGVectorRetriever


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


async def get_rag_retriever(
    pool: Any,
    *,
    site_config: Any = None,
    top_k: int | None = None,
    min_similarity: float | None = None,
    source_filter: list[str] | None = None,
) -> Any:
    """Return a LlamaIndex retriever wired to our pgvector schema.

    All parameters fall back to ``app_settings`` when omitted. Callers
    pass site_config to enable runtime tuning without code changes.
    """
    if site_config is not None:
        top_k = top_k if top_k is not None else int(site_config.get_int("rag_default_top_k", 5))
        min_similarity = (
            min_similarity if min_similarity is not None
            else float(site_config.get_float("rag_min_similarity", 0.3))
        )
        if source_filter is None:
            csv = (site_config.get("rag_source_filter", "") or "").strip()
            source_filter = [s.strip() for s in csv.split(",") if s.strip()] or None
        model_name = (
            site_config.get("embedding_model", "") or "nomic-embed-text"
        )
    else:
        top_k = top_k if top_k is not None else 5
        min_similarity = min_similarity if min_similarity is not None else 0.3
        model_name = "nomic-embed-text"

    cls = _build_retriever_class()
    return cls(
        pool=pool,
        top_k=top_k,
        min_similarity=min_similarity,
        source_filter=source_filter,
        model_name=model_name,
    )


__all__ = [
    "get_rag_retriever",
]


# Used by the type checker / for documentation.
_ = Iterable  # noqa: F841
