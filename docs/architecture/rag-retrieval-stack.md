# RAG Retrieval Stack

The semantic-search backbone for poindexter has two paths today, both
hitting the same `embeddings` pgvector table (16k+ rows, 768-dim
nomic-embed-text vectors, HNSW-indexed). Operators pick which one runs
via `app_settings.rag_engine_enabled`.

## Path A — Legacy inline pgvector (default)

`poindexter.memory.MemoryClient.search` embeds the query once via
Ollama, then issues a single `SELECT ... ORDER BY embedding <=> $1::vector`
against the `embeddings` table. The path is ~50 lines of SQL +
glue, runs in <30ms locally for the typical 5–10 result limit, and
has zero framework cost.

This is what every production query has used since the embedding
table was first populated. It stays as the default because it's the
known-good baseline — the LlamaIndex path is opt-in until the
metric story validates the upgrade.

## Path B — LlamaIndex BaseRetriever (opt-in)

`services/rag_engine.py` exposes `get_rag_retriever(pool, ...)`
which returns a LlamaIndex `BaseRetriever` subclass
(`PoindexterPGVectorRetriever`) over the same `embeddings` table.
The retriever is a thin SQL wrapper — same query shape, different
return type (LlamaIndex `NodeWithScore` instead of asyncpg `Record`).

Routing into Path B happens transparently at
`MemoryClient.search` when:

- `app_settings.rag_engine_enabled = 'true'` (master switch, default `false`)
- The caller did **not** pass a `writer=...` filter (the retriever
  has no writer-filter parameter today; writer-filtered queries
  always run Path A)

If `get_rag_retriever` raises for any reason — llama-index not
installed, embedding model not pulled, query embedding fails —
`MemoryClient.search` catches the exception and silently falls
back to Path A. Operators never lose semantic search because of
a framework bug.

### What Path B unlocks

The reason for the wire-in isn't the base vector query (Path A
already handles that fine). It's the retriever **wrappers** that
LlamaIndex's `BaseRetriever` interface composes naturally:

| Wrapper                      | Setting              | Default | What it does                                                                                                                                                     |
| ---------------------------- | -------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hybrid (BM25 + vector + RRF) | `rag_hybrid_enabled` | `true`  | Wraps the vector retriever with a tsvector BM25 retriever; combines via Reciprocal Rank Fusion (constant `rag_rrf_k`, default 60). Catches lexical-only matches. |
| Cross-encoder rerank         | `rag_rerank_enabled` | `true`  | Pulls `top_k * 4` candidates, re-scores with `rag_rerank_model` (default `cross-encoder/ms-marco-MiniLM-L-6-v2`), returns the top `top_k` after re-ranking.      |
| Source filter                | `rag_source_filter`  | empty   | CSV of `source_table` values; same effect as Path A's `source_table=` arg but applied uniformly across the retriever stack.                                      |

All three wrappers come from `services/rag_engine.py` —
`_build_hybrid_retriever_class` and `_build_rerank_retriever_class`
lazy-build the relevant LlamaIndex `BaseRetriever` subclasses so
the module imports cleanly without llama-index installed.

### Why this is wired through MemoryClient, not the writer stage

#329 sub-issue 4's original framing pointed at
`services/embeddings_db.py` as the wire-in spot. In practice the
right seam is `MemoryClient.search` because it's already the
shared call site for every consumer (research_context,
brain_decisions retrieval, similarity dedup in task_executor,
the operator's `recall_decision` MCP tool). Routing there once
benefits everything; wiring at `embeddings_db.search_similar`
would only catch the small subset that goes through the database
service directly.

## Activation runbook

1. **Verify llama-index is in the venv** — already pinned in
   `src/cofounder_agent/pyproject.toml` (`llama-index-core` +
   `llama-index-embeddings-ollama`). The CI test job
   exercises both paths.
2. **Decide on extras.** `rag_hybrid_enabled` and
   `rag_rerank_enabled` ship as `true` so flipping the master
   switch turns on the full stack. If you want to A/B just the
   base retriever vs Path A, set both to `false` first.
3. **Flip the master switch.**
   ```sql
   UPDATE app_settings SET value = 'true' WHERE key = 'rag_engine_enabled';
   ```
   Or via the operator dashboard / `poindexter set_setting` MCP tool.
4. **Watch the QA-rails dashboard.** The Ragas reviewer
   (`ragas_eval`, default-off) is the canonical signal for "did
   the upgrade improve retrieval quality?" — flip
   `qa_gates.ragas_eval.enabled = true` for a sample run before
   declaring success.

## Ground truth

- Source: `services/rag_engine.py` (524 LOC), `poindexter/memory/client.py:418-690`
- Migration: `services/migrations/20260510_040315_seed_rag_engine_master_switch.py`
- Tests: `tests/unit/services/test_rag_engine.py` (15 cases),
  `tests/unit/poindexter/memory/test_rag_engine_routing.py` (11 cases)
- Issue: `Glad-Labs/glad-labs-stack#329` sub-issue 4 — third sub-issue
  closed in the Lane D push
