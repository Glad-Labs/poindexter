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

from typing import Any, Iterable

from services.logger_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Embedding model — Ollama-native via LlamaIndex's adapter
# ---------------------------------------------------------------------------


# Cache key is "<model_name>@<base_url>" so a single process with multiple
# Ollama backends (rare, but supported in tests + future multi-tenant
# fleets) doesn't conflate adapters.
_EMBED_MODEL_CACHE: dict[str, Any] = {}


def _get_embed_model(
    model_name: str = "nomic-embed-text",
    base_url: str = "http://localhost:11434",
) -> Any:
    """Lazy-load + cache the Ollama embedding adapter.

    Mirrors the model used to populate the embeddings table — without
    matching, queries land in a different vector space and get poor
    similarity scores. The cache keys on (model name, base_url) so
    multiple models or backends coexist (e.g. the embedding model used
    by ``embeddings.embedding_model`` is the canonical one; future
    migrations to a different model would write to a new column / new
    index).

    ``base_url`` is the Ollama HTTP endpoint and comes from
    ``app_settings.local_llm_api_url`` via the retriever — see
    ``get_rag_retriever``. Callers should never read ``OLLAMA_BASE_URL``
    directly; that env var bypasses DB-first config (per
    `feedback_no_silent_defaults` + `feedback_no_env_vars`).
    """
    cache_key = f"{model_name}@{base_url}"
    if cache_key in _EMBED_MODEL_CACHE:
        return _EMBED_MODEL_CACHE[cache_key]

    from llama_index.embeddings.ollama import OllamaEmbedding

    embed = OllamaEmbedding(
        model_name=model_name,
        base_url=base_url,
    )
    _EMBED_MODEL_CACHE[cache_key] = embed
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
            base_url: str = "http://localhost:11434",
        ) -> None:
            super().__init__()
            self._pool = pool
            self._top_k = top_k
            self._min_similarity = min_similarity
            self._source_filter = source_filter
            self._model_name = model_name
            self._base_url = base_url

        async def _aretrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
            text = query_bundle.query_str
            if not text or not text.strip():
                return []

            embed = _get_embed_model(self._model_name, self._base_url)
            try:
                vec = await embed.aget_query_embedding(text)
            except Exception as e:
                logger.warning("[rag] embedding query failed: %s", e)
                return []

            vec_str = "[" + ",".join(str(v) for v in vec) + "]"

            # Identical filter clause to EmbeddingsDatabase.search_similar
            # — keep them aligned so the legacy and LlamaIndex paths
            # see the same corpus.
            #
            # writer + origin_path are surfaced into NodeWithScore.metadata
            # so callers like MemoryClient.search can reconstruct a full
            # MemoryHit without a second query (#329 sub-issue 4).
            sql_parts = [
                "SELECT source_table, source_id, text_preview, metadata, "
                "writer, origin_path, "
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
                # asyncpg returns JSONB as either a decoded dict (when
                # the type codec is registered) or as the raw string
                # (default). MemoryClient.search handles both — match
                # that here so the rag_engine path doesn't crash with
                # ``ValueError: dictionary update sequence element #0
                # has length 1; 2 is required`` on string-returned rows.
                raw_meta = row.get("metadata")
                if isinstance(raw_meta, str):
                    try:
                        import json as _json
                        raw_meta = _json.loads(raw_meta)
                    except (ValueError, TypeError):
                        raw_meta = {}
                metadata = dict(raw_meta or {})
                metadata.update({
                    "source_table": row["source_table"],
                    "source_id": row["source_id"],
                    "writer": row.get("writer"),
                    "origin_path": row.get("origin_path"),
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
    hybrid: bool | None = None,
    rerank: bool | None = None,
) -> Any:
    """Return a LlamaIndex retriever wired to our pgvector schema.

    All parameters fall back to ``app_settings`` when omitted. Callers
    pass site_config to enable runtime tuning without code changes.

    Phase C extensions:
    - ``hybrid=True`` (or ``rag_hybrid_enabled`` setting) — wraps the
      vector retriever in BM25 (tsvector) + RRF fusion.
    - ``rerank=True`` (or ``rag_rerank_enabled`` setting) — wraps the
      whole retriever in a cross-encoder re-ranker. Pulls
      ``rag_rerank_candidate_k`` candidates, returns ``top_k`` after
      re-scoring.
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
        # local_llm_api_url is the canonical Ollama base-URL setting
        # (the same key topic_ranking.py / llm_text.py use). Reading
        # OLLAMA_BASE_URL directly was the legacy env-var bypass we're
        # retiring with this sweep — see `feedback_no_silent_defaults`
        # and `feedback_no_env_vars`.
        base_url = (
            site_config.get("local_llm_api_url", "") or "http://localhost:11434"
        )
        if hybrid is None:
            hybrid = bool(site_config.get_bool("rag_hybrid_enabled", False))
        if rerank is None:
            rerank = bool(site_config.get_bool("rag_rerank_enabled", False))
    else:
        # No site_config means tests / minimal bootstrap path — defaults
        # only. Production callers MUST pass site_config (the lifespan
        # in main.py constructs and threads it); failing loud here would
        # break the existing CI suite that exercises the retriever
        # without a SiteConfig fixture, so we keep the safe-default but
        # fall back to a localhost loopback that won't accidentally hit
        # a co-tenant's Ollama on host.docker.internal.
        top_k = top_k if top_k is not None else 5
        min_similarity = min_similarity if min_similarity is not None else 0.3
        model_name = "nomic-embed-text"
        base_url = "http://localhost:11434"
        if hybrid is None:
            hybrid = False
        if rerank is None:
            rerank = False

    cls = _build_retriever_class()
    base = cls(
        pool=pool,
        top_k=top_k if not (hybrid or rerank) else max(top_k * 4, 20),
        min_similarity=min_similarity,
        source_filter=source_filter,
        model_name=model_name,
        base_url=base_url,
    )

    retriever = base
    if hybrid:
        hybrid_cls = _build_hybrid_retriever_class()
        retriever = hybrid_cls(
            vector_retriever=retriever,
            pool=pool,
            top_k=max(top_k * 4, 20) if rerank else top_k,
            min_similarity=min_similarity,
            source_filter=source_filter,
            site_config=site_config,
        )
    if rerank:
        rerank_cls = _build_rerank_retriever_class()
        retriever = rerank_cls(
            inner=retriever,
            top_k=top_k,
            site_config=site_config,
        )

    return retriever


# ---------------------------------------------------------------------------
# Phase C — Hybrid retrieval (BM25 + vector + RRF) + cross-encoder rerank
# ---------------------------------------------------------------------------


def _build_hybrid_retriever_class():
    """Hybrid retriever that fuses vector similarity with tsvector BM25
    via Reciprocal Rank Fusion. Lazy-built (same pattern as the vector
    retriever class) so the module imports without llama-index.

    Background reading: RRF was introduced in
    https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf —
    ``score(doc) = sum_over_lists 1 / (k + rank(doc, list))``.
    The constant ``k`` (default 60 in the literature, configurable
    here via ``rag_rrf_k``) dampens the influence of any single
    high-rank match.
    """
    from llama_index.core.retrievers import BaseRetriever
    from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode

    class HybridRRFRetriever(BaseRetriever):
        def __init__(
            self,
            *,
            vector_retriever: Any,
            pool: Any,
            top_k: int,
            min_similarity: float,
            source_filter: list[str] | None,
            site_config: Any,
        ) -> None:
            super().__init__()
            self._vector = vector_retriever
            self._pool = pool
            self._top_k = top_k
            self._min_similarity = min_similarity
            self._source_filter = source_filter
            self._site_config = site_config

        def _rrf_k(self) -> int:
            if self._site_config is None:
                return 60
            try:
                return int(self._site_config.get_int("rag_rrf_k", 60))
            except Exception:
                return 60

        async def _bm25_search(self, query: str) -> list[tuple[str, float]]:
            """Run tsvector lexical search. Returns ``[(node_id, ts_rank), ...]``
            ordered by ts_rank desc, capped at ``top_k * 4``.
            """
            if not query.strip():
                return []
            sql_parts = [
                "SELECT source_table, source_id, "
                "ts_rank(text_search, websearch_to_tsquery('simple', $1)) AS rank "
                "FROM embeddings",
                "WHERE text_search @@ websearch_to_tsquery('simple', $1)",
            ]
            params: list[Any] = [query]
            if self._source_filter:
                placeholders = ", ".join(
                    f"${i+2}" for i in range(len(self._source_filter))
                )
                sql_parts.append(f"AND source_table IN ({placeholders})")
                params.extend(self._source_filter)
            sql_parts.append(
                f"ORDER BY rank DESC LIMIT {int(self._top_k * 4)}"
            )
            sql = " ".join(sql_parts)

            try:
                rows = await self._pool.fetch(sql, *params)
            except Exception as e:
                logger.warning("[rag/hybrid] BM25 query failed: %s", e)
                return []
            return [
                (f"{r['source_table']}:{r['source_id']}", float(r["rank"]))
                for r in rows
            ]

        async def _aretrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
            # Run both passes — vector retriever gives us full
            # NodeWithScore objects, lexical gives us (id, rank) pairs.
            vector_nodes = await self._vector._aretrieve(query_bundle)
            lexical_pairs = await self._bm25_search(query_bundle.query_str)

            # Index nodes by id so RRF can attach scores back to them.
            node_by_id: dict[str, NodeWithScore] = {
                n.node.node_id: n for n in vector_nodes
            }

            # Fetch any lexical-only results that vector missed, so RRF
            # has full nodes to score. Cheap — they're already
            # bounded to top_k * 4.
            missing_ids = [
                nid for nid, _r in lexical_pairs if nid not in node_by_id
            ]
            if missing_ids:
                # Rehydrate from embeddings table (text_preview + metadata).
                # Use a conservative LIMIT in case of duplicates.
                src_rows = []
                try:
                    src_rows = await self._pool.fetch(
                        "SELECT source_table, source_id, text_preview, metadata "
                        "FROM embeddings "
                        "WHERE (source_table || ':' || source_id) = ANY($1::text[])",
                        missing_ids,
                    )
                except Exception as e:
                    logger.warning("[rag/hybrid] lexical rehydrate failed: %s", e)
                for row in src_rows:
                    nid = f"{row['source_table']}:{row['source_id']}"
                    metadata = dict(row.get("metadata") or {})
                    metadata.update({
                        "source_table": row["source_table"],
                        "source_id": row["source_id"],
                    })
                    node_by_id[nid] = NodeWithScore(
                        node=TextNode(
                            text=row.get("text_preview") or "",
                            metadata=metadata,
                            id_=nid,
                        ),
                        score=0.0,  # placeholder — RRF score replaces it
                    )

            # RRF: rank lists from each retriever; scores are
            # ``1 / (k + rank)`` summed across lists.
            k = self._rrf_k()
            rrf_scores: dict[str, float] = {}
            for rank, n in enumerate(vector_nodes, start=1):
                rrf_scores[n.node.node_id] = (
                    rrf_scores.get(n.node.node_id, 0.0) + 1.0 / (k + rank)
                )
            for rank, (nid, _ts) in enumerate(lexical_pairs, start=1):
                rrf_scores[nid] = rrf_scores.get(nid, 0.0) + 1.0 / (k + rank)

            # Sort by RRF score desc, hand back the corresponding nodes
            # with the RRF score on each.
            ordered = sorted(rrf_scores.items(), key=lambda kv: kv[1], reverse=True)
            results: list[NodeWithScore] = []
            for nid, score in ordered[: self._top_k]:
                if nid in node_by_id:
                    n = node_by_id[nid]
                    results.append(NodeWithScore(node=n.node, score=score))
            return results

        def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
            import asyncio
            return asyncio.run(self._aretrieve(query_bundle))

    return HybridRRFRetriever


_RERANKER_CACHE: dict[str, Any] = {}


def _build_rerank_retriever_class():
    """Cross-encoder re-ranker. Wraps any retriever, takes its top-N
    candidates, and re-scores them using a sentence-transformers
    cross-encoder (``cross-encoder/ms-marco-MiniLM-L-6-v2`` default).

    The cross-encoder is more accurate than dot-product similarity but
    much heavier per call (~50-200ms per (query, doc) pair). Fine on
    20-50 candidates per query; would not scale to whole-corpus
    scoring. Always sits AFTER cheap retrieval (vector or hybrid).
    """
    from llama_index.core.retrievers import BaseRetriever
    from llama_index.core.schema import NodeWithScore, QueryBundle

    class CrossEncoderRerankRetriever(BaseRetriever):
        def __init__(
            self,
            *,
            inner: Any,
            top_k: int,
            site_config: Any,
        ) -> None:
            super().__init__()
            self._inner = inner
            self._top_k = top_k
            self._site_config = site_config

        def _model_name(self) -> str:
            if self._site_config is None:
                return "cross-encoder/ms-marco-MiniLM-L-6-v2"
            return (
                self._site_config.get(
                    "rag_rerank_model", "cross-encoder/ms-marco-MiniLM-L-6-v2",
                ) or "cross-encoder/ms-marco-MiniLM-L-6-v2"
            )

        def _get_model(self) -> Any:
            name = self._model_name()
            if name in _RERANKER_CACHE:
                return _RERANKER_CACHE[name]
            from sentence_transformers import CrossEncoder
            logger.info(
                "[rag/rerank] Loading cross-encoder %s (first call)", name,
            )
            _RERANKER_CACHE[name] = CrossEncoder(name)
            return _RERANKER_CACHE[name]

        async def _aretrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
            candidates = await self._inner._aretrieve(query_bundle)
            if not candidates:
                return []
            try:
                model = self._get_model()
            except Exception as e:
                logger.warning(
                    "[rag/rerank] cross-encoder unavailable, returning "
                    "candidates unchanged: %s", e,
                )
                return candidates[: self._top_k]

            pairs = [(query_bundle.query_str, c.node.text or "") for c in candidates]
            try:
                scores = model.predict(pairs)
            except Exception as e:
                logger.warning(
                    "[rag/rerank] cross-encoder predict failed: %s", e,
                )
                return candidates[: self._top_k]

            scored = list(zip(candidates, [float(s) for s in scores]))
            scored.sort(key=lambda cs: cs[1], reverse=True)
            return [
                NodeWithScore(node=c.node, score=score)
                for c, score in scored[: self._top_k]
            ]

        def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
            import asyncio
            return asyncio.run(self._aretrieve(query_bundle))

    return CrossEncoderRerankRetriever


__all__ = [
    "get_rag_retriever",
]


# Used by the type checker / for documentation.
_ = Iterable  # noqa: F841
