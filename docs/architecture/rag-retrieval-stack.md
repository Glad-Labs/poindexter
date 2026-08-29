# RAG Retrieval Stack

The semantic-search backbone for poindexter has two paths today, both
hitting the same `embeddings` pgvector table (16k+ rows, 768-dim
nomic-embed-text vectors, HNSW-indexed). Operators pick which one runs
via `app_settings.rag_engine_enabled`.

## The retrieval payload — `chunk_text` vs `text_preview` (poindexter#1033)

Two columns on `embeddings` hold text, and conflating them cost us most of the
corpus for months.

| Column         | Type           | Holds                                           | Read by                                                                                  |
| -------------- | -------------- | ----------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `chunk_text`   | `text`         | The **full chunk the vector was computed over** | Retrieval payload, cross-encoder reranker, any consumer feeding retrieved text to an LLM |
| `text_preview` | `varchar(500)` | A short **display** snippet                     | Memory dashboard, `poindexter memory` CLI, `chat_tools`, voice agent                     |

The tap runner chunks at `app_settings.tap_chunk_max_chars` (default 6000) and
embeds each chunk **in full**. Before #1033 only `text_preview` was persisted,
and both `rag_engine` retriever paths built their `TextNode` from it. Measured
on the live 196-post corpus:

- 313 chunks, median **4,284** chars each (p90 5,751)
- **1,079,141 of 1,234,626 chars discarded — 87.4%**
- only 4/313 chunks (1.3%) fit inside the preview
- across 40 sampled queries the best-matching 500-char window was **not** the
  first one 40% of the time (median **+0.073 cosine** left on the table)

The reranker consequence was unconditional: `rag_engine._rerank` scores
`node.text`, so the cross-encoder — the most expensive stage in the stack — saw
~12% of every candidate regardless of where the match sat.

**Invariants to preserve:**

- `_retrieval_text(row)` in `services/rag_engine.py` is the single seam that
  decides what a consumer sees. It returns `chunk_text` and falls back to
  `text_preview`. **The fallback is load-bearing, not defensive** — `chunk_text`
  is NULL on every pre-migration row, so the column ships ahead of the backfill
  without blanking retrieval.
- `MemoryHit.text_preview` is **always** a preview and `MemoryHit.chunk_text`
  **always** the full payload. The two search paths used to disagree (the
  direct-pgvector path stored a preview, the rag_engine path stored
  `node.text`); `_node_preview` re-clips on the rag path so they agree.
- Don't add whole-chunk text to a display surface. Dashboards render 240 chars,
  the CLI 80, voice 10 words — they must not pull 6 KB rows to do it.

**Backfill.** Taps dedup on chunk-0 `content_hash`, so an unchanged document is
skipped forever and would keep `chunk_text` NULL indefinitely. Use
`scripts/backfill_embeddings_chunk_text.py`, which re-derives text from each
Tap's own `extract()` and writes it only when two interlocks both hold: chunk 0's
stored hash equals `content_hash(text)` (proving the source is unchanged since
the vector was made) **and** the recomputed chunk count matches the stored row
count. Anything failing either check is skipped and reported — writing the wrong
text against a real vector is worse than NULL, because the fallback at least
degrades honestly. No embedding calls; vectors are never touched. Verified
196/196 posts (313 rows) recoverable at time of writing.

```bash
docker exec poindexter-worker python /app/scripts/backfill_embeddings_chunk_text.py --dry-run
```

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

**Embed endpoint (standalone caller).** `MemoryClient` has no `SiteConfig`,
so `_search_via_rag_engine` reads `local_llm_api_url` from the pool
(`_rag_embed_base_url`) and passes it to `get_rag_retriever` as
`embed_base_url`. Without it the retriever's no-`site_config` branch defaults
the query-embedder to `http://localhost:11434`, which inside the worker
container has no Ollama (it runs on `host.docker.internal:11434`) — so every
embed fails. That mis-pointed endpoint once stayed hidden (≈1 RAG injection in
27 runs) because the retriever **swallowed** the embed `ConnectionError` and
returned an _empty_ result — indistinguishable from "no similar posts", so it
never tripped the loud Path-A fallback below.

**As of 2026-07-11 the retriever RAISES on an embed failure instead** (after
exhausting the retry budget). A failed query-embedding is a rail _failure_, not
an empty corpus, so it propagates out of `_aretrieve` to `MemoryClient.search`,
trips all three surfaces below, **and** degrades to Path A — whose embed runs
through the independently-configured dispatcher and often still succeeds,
recovering real hits even while Path B's endpoint is mis-pointed (exactly the
#2303 shape). A genuinely empty corpus stays quiet: the embed succeeds, the
pgvector query runs, and zero matching rows return an empty result without
raising. (The retriever's _pgvector-query_ failure is a deliberately separate
path that still returns empty rather than raising: Path A shares the same DB
pool, so a raise there couldn't unlock any recovery the way a diverging embed
endpoint can.)

If `get_rag_retriever` raises for any reason — llama-index not
installed, embedding model not pulled, query embedding fails —
`MemoryClient.search` catches the exception and falls back to
Path A so semantic search keeps working. The fallback is **loud**
per `feedback_no_silent_defaults`: every fallback fires three
independent observability surfaces before continuing to the legacy
path:

1. **Exception log** — `WARNING` with the full traceback in
   stdout/Loki, plus a one-line repair instruction ("either fix
   the retriever or set `rag_engine_enabled=false` until repaired").
2. **`audit_log` row** — `event_type='rag_engine_fallback'`,
   `severity='warning'`. Lights up the Observability dashboard's
   "rag_engine fallbacks (24h)" panel; alert rules can trip on
   threshold (e.g. > 5 in 10 min).
3. **`notify_operator`** — Discord ops webhook (escalates to
   Telegram if the dedup logic flags it as critical).

The three surfaces are independent: a Discord webhook outage doesn't
suppress the audit_log row, and an unbound audit logger doesn't
swallow the operator notification. You will know if the rail breaks.

The fallback to Path A still runs after the surfaces fire, so a
bad framework upgrade can't take down the site. But silent
degradation — operator-enabled rail goes back to legacy without
anyone noticing — is explicitly forbidden.

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

#### Rerank scores are logits — normalized only for display

The cross-encoder rerank wrapper re-scores candidates with raw relevance
**logits** (`cross-encoder/ms-marco-MiniLM-L-6-v2`), not cosines. Live samples
span roughly **+6 … −10**, so `NodeWithScore.score` — and therefore
`MemoryHit.similarity` on the rerank path — is routinely negative. That is
correct for _ordering_ (higher = more relevant) but wrong to render verbatim: a
writer-prompt line reading `[similarity: -7.86]` is nonsense to a human or LLM.

`MemoryClient._search_via_rag_engine` therefore attaches a **display-only**
`MemoryHit.display_similarity` — the logistic sigmoid of the logit (0-1,
monotonic, so it preserves rerank order) — but _only on the rerank path_. It
stays `None` on the cosine + legacy paths, where `.similarity` is already a 0-1
cosine. Surfaces that render a number to a human/LLM (today:
`services/research_context.py::build_rag_context`'s internal-linking block) show
`display_similarity` when present, else fall back to `.similarity`. The
transform is `poindexter/memory/client.py::_rerank_logit_to_similarity`.

Crucially, `.similarity` itself is **left as the raw logit**. Normalizing it at
the source would silently change behavior for _threshold_ consumers, because a
fixed cutoff selects a different set in logit-space vs probability-space — e.g.
`services/topic_dedup_guard.py` re-checks `best.similarity >= 0.75`. (That
re-check compares a rerank logit to a cosine threshold and is a separate
pre-existing bug; the display normalization deliberately does not touch
`.similarity`, so it neither fixes nor worsens that consumer.)

### Why this is wired through MemoryClient, not the writer stage

#329 sub-issue 4's original framing pointed at
`services/embeddings_db.py` as the wire-in spot. In practice the
right seam is `MemoryClient.search` because it's already the
shared call site for every consumer (research_context,
brain_decisions retrieval, similarity dedup in the Prefect
content-generation flow, the operator's `recall_decision` MCP
tool). Routing there once benefits everything; wiring at
`embeddings_db.search_similar` would only catch the small subset
that goes through the database service directly.

### The writer's snippet retrieval is a separate, source-scoped path

The `canonical_blog` writer atom
(`modules/content/atoms/two_pass_writer._embed_and_fetch_snippets`)
does **not** go through Path B / `MemoryClient` — it runs its own
direct pgvector nearest-neighbour query for the "internal snippets"
it grounds the draft in. That query is **independently source-scoped**
via `_resolve_snippet_source_filter`, which reads the same
`rag_source_filter` setting but with a stricter empty-value rule: the
writer **never queries the embeddings table unfiltered**. An empty/unset
value falls back to the `posts` content allowlist rather than "all
tables", because the corpus is ~⅔ `claude_sessions` / `brain` / `audit`
ops-logs and grounding a published draft in operational telemetry
reproduces session transcripts / agent instructions into the post (the
2026-06 contamination incident; memory: `project_rag_corpus_pollution`).
A complementary deterministic **prompt-echo guard** in the same atom
strips any prompt preamble the writer model regurgitates as content —
see `docs/architecture/anti-hallucination.md` (Layer 1).

#### Retrieval de-echo — MMR + near-duplicate ceiling

Source-scoping fixed _ops-log_ pollution, but `posts` itself becomes the
contaminant when topics cluster: the raw top-N nearest `posts` for a dense
topic are the same _sibling_ posts restating the same opening, and the writer
(instructed to draw ONLY from the snippets) parrots it. This is the **inverse
self-echo** failure — the 2026-06 "VRAM is the only currency" cluster where
four published posts opened near-identically, each echoing the last. Every
per-post QA rail scores a draft in isolation, so nothing catches it;
`qa.content_originality` flags it advisorily post-hoc (whole-post chunked scan,
so a self-echo anywhere in the body — not just the opening — is caught), and
this is the retrieval-side root fix.

`_embed_and_fetch_snippets` now selects a **diverse** grounding set from an
oversampled candidate pool rather than taking the raw top-N:

1. **Oversample** — fetch `writer_rag_two_pass_snippet_limit ×
writer_rag_candidate_multiplier` candidates (with their `embedding` +
   query-similarity) so the selector has room to work.
2. **Near-duplicate ceiling** — drop any candidate whose query cosine is
   `>= writer_rag_dedup_ceiling` (default `0.93`, above the corpus p95
   nearest-neighbour cosine of ~0.89 — so only the pathological near-republish
   tail is struck). **Fail-open**: if that would empty the pool, the originals
   are kept, so the ceiling can never zero grounding.
3. **MMR** — greedily maximise `λ·relevance − (1−λ)·max_sim_to_selected`
   (`writer_rag_mmr_lambda`, default `0.5`), so an echo cluster collapses to a
   single representative snippet and the remaining slots go to diverse posts.
   `writer_rag_mmr_lambda = 1.0` disables the diversity term (pure relevance —
   the MMR escape hatch).

All three knobs are DB-tunable app_settings. memory:
`project_rag_corpus_pollution` (the INVERSE self-echo failure).

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
- Migration: `services/migrations/0000_baseline.py` (originally `20260510_040315_seed_rag_engine_master_switch.py`, folded in by the 2026-06-22 squash)
- Tests: `tests/unit/services/test_rag_engine.py` (15 cases),
  `tests/unit/poindexter/memory/test_rag_engine_routing.py` (11 cases)
- Issue: `Glad-Labs/glad-labs-stack#329` sub-issue 4 — third sub-issue
  closed in the Lane D push

## Writer prompt-size observability (poindexter#868)

Every `content.generate_draft` run on the two-pass (niche) path records how
large the assembled writer prompt actually was, broken down by which part of
this RAG/research stack contributed how many characters. Fields live on
`atom_runs.metrics` (JSONB) for rows where `atom = 'content.generate_draft'`:

| Field                                    | What it measures                                                                                                                         |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `writer_prompt_draft_chars`              | Total size of the fully-rendered draft prompt                                                                                            |
| `writer_prompt_snippet_chars`            | Portion from this atom's own internal RAG snippet block                                                                                  |
| `writer_prompt_research_chars`           | Portion from the `research_context`/SOURCES block (caller-attached + `ResearchService.build_context()` + `build_rag_context()`, layered) |
| `writer_prompt_context_bundle_chars`     | Portion from the dev_diary GROUND TRUTH bundle (0 outside dev_diary)                                                                     |
| `writer_prompt_override_chars`           | Portion from the niche `writer_prompt_override` + operator `writing_style_reference`                                                     |
| `writer_prompt_internal_grounding_chars` | Portion from the #822 prior-work-anchor section                                                                                          |
| `writer_prompt_revise_chars`             | Sum of every QA-rescue revise-pass prompt for this task                                                                                  |
| `writer_prompt_revise_calls`             | How many revise passes ran (same value as `revision_loops`)                                                                              |

Visible on the **Pipeline** dashboard's "Writer Context Size" row. See
the design doc (`docs/superpowers/specs/2026-07-16-writer-prompt-size-metrics-design.md`)
for the full rationale and the forks considered.

> **Always filter `atom_runs.metrics` queries by `atom`.** Generic keys
> (`content_length`, `model_used`) are emitted by many nodes, so a query without
> an `atom = '<name>'` predicate blends unrelated nodes into a meaningless
> average. The `writer_prompt_*` fields come from `content.generate_draft`
> alone — one row per task.
>
> These fields only reach `atom_runs.metrics` as of
> [poindexter#873](https://github.com/Glad-Labs/poindexter/issues/873), which
> fixed the two points where `StageResult.metrics` was discarded on the
> graph_def path. Before that fix the panels read "No data".
