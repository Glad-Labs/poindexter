# SEO metadata as LLM-driven atoms

**Date:** 2026-06-02
**Issue:** Glad-Labs/poindexter#362 (Phase 3 atom granularity refactor, part of umbrella #355)
**Status:** design approved — proceeding to plan

## Problem

`generate_seo_metadata` is one of the four coarse `stage.*` nodes still left in
the `canonical_blog` graph_def after the atom-cutover (only `cross_model_qa` was
decomposed). Decomposing it closes part of #362.

Separately, the current SEO generation **produces low-quality output**. The stage
calls `ContentMetadataGenerator.generate_seo_assets`, which is **entirely
pattern-based — no LLM**, despite a comment claiming an "LLM-backed variant":

- `seo_title` = the raw article title, truncated to 60 chars (no search-intent rewrite)
- `meta_description` = the first paragraph, sliced to 152 chars + "…" (not a written description)
- `meta_keywords` = `extract_keywords_from_text` word-frequency extraction (not semantic search terms)

So this change is both the #362 atomization **and** a quality fix.

## Goal

Replace the single `stage.generate_seo_metadata` graph node with three
LLM-driven, independently-composable atoms that produce genuinely
SEO-optimized title / description / keywords, while preserving the exact
context contract that `finalize_task` consumes.

## Non-goals

- Decomposing the other three coarse stages (`generate_content`,
  `replace_inline_images`, `finalize_task`) — separate #362 work.
- Deleting `GenerateSeoMetadataStage` / `generate_seo_assets` — they stay
  registered and test-backed; the stage simply stops being referenced by
  `canonical_blog`. Deletion is a follow-up cleanup.
- Changing `dev_diary` (its 4-node subset has no SEO stage).

## Architecture

Three new atoms under `src/cofounder_agent/services/atoms/`, each mirroring the
`qa_ragas.py` atom shape (`ATOM_META: AtomMeta` + `async def run(state) -> dict`):

| Atom file                     | Atom name                  | Reads (state)                   | Writes (context_updates)                                                  |
| ----------------------------- | -------------------------- | ------------------------------- | ------------------------------------------------------------------------- |
| `seo_generate_title.py`       | `seo.generate_title`       | `content`, `topic`, `tags`      | `seo_title`                                                               |
| `seo_generate_description.py` | `seo.generate_description` | `content`, `topic`, `seo_title` | `seo_description`                                                         |
| `seo_extract_keywords.py`     | `seo.extract_keywords`     | `content`, `topic`, `seo_title` | `seo_keywords`, `seo_keywords_list`, `stages["4_seo_metadata_generated"]` |

Run **sequentially** so `generate_description` and `extract_keywords` can read the
freshly-generated `seo_title` for coherence (description complements the title,
keywords reflect it).

A shared private helper module `services/atoms/_seo_common.py` holds:

- `run_seo_llm(state, prompt_key, **vars) -> str` — resolves the prompt via
  `UnifiedPromptManager`, calls the LLM through `llm_text` /
  `dispatch_complete` at `capability_tier="cheap_critic"`, returns plain text.
- `fallback_title(...)`, `fallback_description(...)`, `fallback_keywords(...)` —
  the old programmatic derivations, reused for graceful degradation.
- `_degraded(field, exc)` — logs a WARNING and increments the `seo_degraded`
  metric.

### Per-atom logic

**`seo.generate_title`**

1. `primary_keyword = (tags or [topic])[0]`.
2. LLM call with `atoms.seo.generate_title` prompt (vars: `topic`, `primary_keyword`, `content` excerpt).
3. Guard: strip surrounding quotes/markdown, collapse whitespace, then
   `derive_seo_title(raw, max_len=60)` (word-boundary truncation, GH-85).
4. On LLM failure after retries → `fallback_title` (today's `derive_seo_title(canonical_title)`), `_degraded("title", e)`.
5. Write `seo_title`.

**`seo.generate_description`**

1. LLM call with `atoms.seo.generate_description` prompt (vars: `seo_title`, `topic`, `content` excerpt).
2. Guard: strip quotes, collapse whitespace, word-boundary trim to ≤160; if empty → fallback.
3. On failure → `fallback_description` (today's excerpt slice), `_degraded("description", e)`.
4. Write `seo_description`.

**`seo.extract_keywords`**

1. LLM call with `atoms.seo.extract_keywords` prompt (vars: `seo_title`, `topic`, `content` excerpt) → comma/newline list.
2. Guard: split, lowercase-trim, dedupe, **drop any keyword whose tokens don't appear in `content` + `seo_title`** (anti-hallucination), cap at 10.
3. If fewer than 3 survive → backfill from `extract_keywords_from_text(content)` to reach a floor of 3 (deduped).
4. On LLM failure after retries → `fallback_keywords` (pure `extract_keywords_from_text`), `_degraded("keywords", e)`.
5. Write `seo_keywords` (comma-joined), `seo_keywords_list`, and `stages["4_seo_metadata_generated"]=True`.

### ATOM_META

Each atom declares (mirroring `qa.*`):

- `type="atom"`, `version="1.0.0"`
- `inputs` / `outputs` / `requires` / `produces` per the table above
- `capability_tier="cheap_critic"`, `cost_class="compute"`
- `idempotent=False`, `side_effects=("calls ollama",)`
- `retry=RetryPolicy(max_attempts=2, backoff_s=2.0, retry_on=("HTTPError","TimeoutException","ConnectError"))`
- `parallelizable=False` (each depends on the prior atom's `seo_title`)

`requires` for `generate_description`/`extract_keywords` includes `seo_title`, so
the atom-cutover's build-time requires/produces validation (cutover 1/5) enforces
ordering: a graph that runs them before `seo.generate_title` fails to compile.

## Prompts (DB-configurable)

Three new prompt keys, added to the YAML defaults consumed by
`UnifiedPromptManager` (Langfuse-first, YAML fallback). Runtime overrides land in
Langfuse / `prompt_templates`. Each is a system+user pair:

- `atoms.seo.generate_title` — "You are an SEO editor. Write ONE title ≤60 chars
  for the article below. Lead with the primary keyword `{primary_keyword}` when
  natural. Compelling, specific, no clickbait, no quotes, no markdown. Output the
  title only."
- `atoms.seo.generate_description` — "Write ONE meta description, 150–160 chars,
  for an article titled `{seo_title}`. Summarize the value, include the topic
  naturally, active voice, end with a complete sentence. Output the description
  only."
- `atoms.seo.extract_keywords` — "List 5–10 SEO keywords/phrases a searcher would
  use to find this article. Lowercase, comma-separated, most important first.
  Only terms supported by the article text. Output the list only."

Exact wording is finalized in the prompt YAML during implementation; the design
fixes the keys, variables, and output contract (single-line / comma-list, no
prose wrapper).

## Graph wiring

In `services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF`:

- Remove node `{"id": "generate_seo_metadata", "atom": "stage.generate_seo_metadata"}`.
- Add nodes `seo_title` → `seo.generate_title`, `seo_description` →
  `seo.generate_description`, `seo_keywords` → `seo.extract_keywords`.
- Rewire edges: `qa_aggregate → seo_title → seo_description → seo_keywords → generate_media_scripts`.

No other node changes. `pipeline_use_graph_def=true` is already live, so this
takes effect on the next deploy.

## Observability

- `_degraded(field, exc)` emits `logger.warning("[seo.%s] LLM failed, degraded to
programmatic: %s", field, exc)` and increments a Prometheus counter
  `seo_degraded_total{field=...}` (via the existing metrics registry; if no
  in-atom hook exists, the structured WARNING in Loki is the floor and a counter
  is added to the metrics module).
- Existing per-stage metrics (`seo_title_length`, `seo_description_length`,
  `keyword_count`) are preserved by having the atoms emit equivalents where the
  atom return contract allows.

## Testing (TDD)

Unit tests, one file per atom, LLM call mocked (no real Ollama):

- **title:** long LLM output → truncated ≤60 at word boundary; quoted output →
  quotes stripped; LLM raises → fallback used + WARNING + metric.
- **description:** >160 trimmed at word boundary; empty LLM output → fallback;
  reads `seo_title` from state.
- **keywords:** dedupe + cap 10; a keyword absent from content is dropped; <3
  survivors → backfilled to ≥3; LLM raises → pure programmatic fallback.
- **wiring:** `CANONICAL_BLOG_GRAPH_DEF` has the 3 nodes + correct edges, is a
  valid DAG, and `build_graph_from_spec` resolves all three atom callables.
- **registry:** the 3 atoms are discovered with `ATOM_META` and appear in
  `list_atoms()`.

## Acceptance

- `canonical_blog` runs `seo.generate_title → seo.generate_description →
seo.extract_keywords` in place of the old stage; `finalize_task` receives the
  same context keys and a post persists end-to-end.
- Generated titles/descriptions are LLM-written (not truncated source), verified
  on a sample task.
- Forced LLM failure degrades to the programmatic floor with a logged WARNING +
  metric — no silent default, post still completes.
- Full unit suite green.

## Out-of-scope follow-ups (note in #362)

- Delete `GenerateSeoMetadataStage` + the dead title/description branches of
  `generate_seo_assets` once nothing references them.
- Decompose the remaining coarse stages (`generate_content`,
  `replace_inline_images`, `finalize_task`).
