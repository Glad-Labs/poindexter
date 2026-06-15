# Topic sourcing: taps ingest, orchestration selects

**Status:** Approved design (2026-06-14)
**Author:** Claude (Opus 4.8) + Matt
**Supersedes:** the "PR2b" framing in `project_topic_discovery_consolidation` (retire `TopicDiscovery`) — that becomes the final step here.

## Problem

Topic sourcing has drifted into **three** places that dispatch the same `plugins.topic_source` plugins, plus a hollow abstraction:

1. `TopicBatchService._discover_external` / `_discover_internal` — dispatches sources **directly, per niche**, inside the orchestrator (`services/topic_batch_service.py`). This is the live path that actually feeds batches. For each enabled `niche_sources` row it loads `PluginConfig`, layers in niche context (`niche_slug` / `niche_id` / `_site_config`), and calls `plugin.extract(pool, cfg)`.
2. `tap.builtin_topic_source` (a handler on the `external_taps` data plane) — **already seeded as 5 enabled global rows** (`hackernews` / `devto` / `knowledge` / `web_search` / `codebase`, each `target_table='content_tasks'`, no niche binding). On each scheduled fire it calls `runner.run_all(pool)` — re-running _every_ source, then filtering to its own `tap_type` and discarding the rest — and **returns only a count; it stores nothing**. Its docstring claims the runner "dedup-and-stores"; the runner does neither (verified: `topic_sources/runner.run_all` aggregates `DiscoveredTopic`s with no persistence). So these 5 taps burn schedule slots and ingest nothing today.
3. `TopicDiscovery.discover` — the legacy pre-niche dispatcher (queue-based), already slated for retirement; dedup fix #1561 left it as the only caller of `get_deduplicator()` on the old path.

Two deeper problems sit underneath: **ingestion is welded into the orchestrator**, and the sources themselves are **niche-blind** — `web_search.extract` reads a global `CATEGORY_SEARCHES` map and never looks at `niche_id`/`niche_slug`, so it returns the same candidates for every niche. The source plugins are otherwise already hot-swappable (entry-points: hackernews, devto, web_search, knowledge, codebase, igdb) — that part is good.

## Goal

Separate **ingestion** from **orchestration** along the seam Matt described: _"taps as how we get the information, then the orchestration decides what to use from where."_ — and make ingestion genuinely **niche-relevant**, not niche-blind.

- **Taps ingest.** Niche-scoped taps run sources on a schedule, dedup, and deposit candidates into a pool.
- **Sources are niche-aware.** A niche-bound tap produces candidates relevant to _its_ niche (e.g. `web_search` searches that niche's topics), so the per-niche rows yield genuinely different results — not N identical fetches.
- **Orchestration selects.** `TopicBatchService` reads the pool, ranks per-niche against goal vectors, and assembles batches — it no longer dispatches sources.

This finishes the half-built tap abstraction, removes the triple-dispatch, and decouples ingestion cadence from batch cadence.

## Decisions (settled during brainstorming)

| Fork                   | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Pool model             | **Niche-scoped** — taps carry niche context; sources get the niche up front (better relevance). A source serving N niches runs N times (fine at 3 niches).                                                                                                                                                                                                                                                                                                                              |
| Pool storage           | **New `topic_pool` table** — niche-tagged candidate pool, the decoupling seam.                                                                                                                                                                                                                                                                                                                                                                                                          |
| Tap definition         | **Niche-bound `external_taps`** — topic taps are `external_taps` rows keyed by `handler_name='builtin_topic_source'`, with a new `niche_id`, `tap_type=<source>`, `target_table='topic_pool'`, + schedule. Reuses the declarative data plane + `tap_runner` + handler registry. `niche_sources` (one row per niche×enabled source) migrates into these rows; the 5 pre-existing global rows are deleted.                                                                                |
| Source niche-awareness | **Sources produce niche-relevant candidates.** `web_search` resolves its queries from niche context (explicit `config.seed_queries` override → seeded `config.categories` → else derived from the niche's `name` + `target_audience_tags`); the silent "search every global category" fallback is **retired**. The migration seeds `config.categories` only where the bank fits (gaming, pc-hardware) and leaves AI/ML to the tag-derived path, so both paths run in prod from day one. |

**Rejected alternative — a nullable `niche_id` where NULL = "global" (fetch once, fan out to all active niches at ingest).** Considered because niche-blind sources like `web_search` get fetched once per niche for near-identical results today. Rejected in favor of uniform per-niche rows: one `niche_id`-per-tap is more robust (independent enable / cadence / tuning per niche, no NULL/fan-out special-casing, no `pooled→batched` status-sharing across niches). Critically, we're **also making sources niche-aware** (§2b), so the per-niche rows now return genuinely _different_ results — the "N identical fetches" objection that motivated the global model goes away. The residual duplicate-fetch cost applies only to truly niche-agnostic _feed_ sources (`hackernews` / `devto` pull one feed regardless of niche); that's collapsed by the existing per-batch dedup, and a source-level fetch cache is the escape hatch if N grows large.

## Architecture (data flow)

```
external_taps  (niche-bound topic tap:
                handler_name=builtin_topic_source · tap_type=<source>
                · niche_id · target_table=topic_pool · config.categories · schedule)
   │  [existing tap_runner — dispatches on handler_name; reads only the
   │   returned {"records": N}; does NOT auto-persist to target_table]
   ▼
tap.builtin_topic_source   (FIXED: dispatch the single tap_type source with
                            niche context (incl. target_audience_tags) →
                            dedup → INSERT into row.target_table)
   │   (internal_rag rides the same path as tap_type=internal_rag)
   ▼
topic_pool                 (NEW: niche-tagged candidate pool — the seam)
   │  [accumulates continuously; retention-pruned]
   ▼
TopicBatchService.run_sweep(niche)   (now a POOL READER, not a dispatcher)
   │  read niche pool → embed/rank vs goals → carry-forward decay → top-N
   ▼
topic_batches + topic_candidates → operator gate → resolve → content_tasks → canonical_blog
```

Note: there is **no `surface` column** on `external_taps`. Each declarative-data-plane _table_ is a surface; a row in `external_taps` is by definition a "tap". Dispatch is keyed on `handler_name`; `target_table` is the destination the handler reads (the framework does not route writes for it — see `tap_runner.run_all`, which only tallies the returned record count).

## Components

### 1. Schema

- **`topic_pool`** (new table): `id`, `niche_id` (FK, ON DELETE CASCADE), `source`, `title`, `summary`, `url`, `category`, `score` (source `relevance_score`), `ingested_at`, `dedup_key`, `status` (`pooled` / `batched` / `expired`), optional `embedding`. Unique constraint on `(niche_id, dedup_key)`.
- **`external_taps.niche_id`** (new nullable column, FK to `niches`, ON DELETE CASCADE): set for niche-bound topic taps, NULL for the existing non-topic taps (`corsair_csv`, future singer/webhook).
- **Migration (two moves, lossless):**
  1. **Delete** the 5 pre-existing global `builtin_topic_source` rows (`target_table='content_tasks'`) — they store nothing today, so nothing is lost.
  2. **Insert** one niche-bound tap per `niche_sources` row: `niche_id`, `tap_type=source_name`, `handler_name='builtin_topic_source'`, `target_table='topic_pool'`, `enabled=<niche_sources.enabled>`, `schedule` from the niche's discovery cadence, `config` carrying `weight_pct` **and** (for category-driven sources like `web_search`) a per-niche `categories` list scoped to that niche. `niche_sources` is the cartesian (niche × source) already, so this is a direct row-for-row promotion.
  - **Per-niche `config.categories` seed** (only where the `CATEGORY_SEARCHES` bank fits the niche): e.g. gaming → `["gaming", "hardware"]`, pc-hardware → `["hardware", "technology"]`. **AI/ML omits `categories`** — the bank has no AI key, so its tap falls through to §2b's tag-derived path. Net: bank-categories for the niches it covers, tag-derived for AI/ML — both §2b paths exercised in prod from day one. Sources that aren't category-driven (feeds, internal_rag) get no `categories`.
  - **`weight_pct` is currently unused.** The live orchestrator (`_discover_external`) reads only `enabled` + `source_name`; the module docstring's "niche_sources weights → pool" is aspirational. Carrying `weight_pct` into the tap's `config` jsonb preserves it for future weighted source-mix selection **without changing today's behavior**.
  - `niche_sources` is then deprecated (left in place, read by nothing).

### 2. Tap handler (`tap.builtin_topic_source`)

Replace the hollow `run_all`-and-discard body with the per-source loop body lifted from `_discover_external` (so b2's deletion is a move, not a rewrite):

- Read `niche_id` from `row`; load the niche (fail-loud if a topic tap has no `niche_id`, per `feedback_no_silent_defaults`).
- Resolve the single `tap_type` source from `get_topic_sources()` — **not** the whole `run_all` fan-out (kills the N-sources-per-tap waste). `internal_rag` branches to `InternalRagSource` (see §4).
- Build `extract_config` like `_discover_external` does — `PluginConfig.load(pool, "topic_source", source_name)` layered with the tap row's `config` and the **full niche context**: `{_site_config, niche_slug, niche_id, niche_name, target_audience_tags}`. The added `niche_name` + `target_audience_tags` are what make §2b possible. Call `source.extract(pool, extract_config)`.
- Dedup the returned topics via `get_deduplicator(pool, site_config=...)` (honors `topic_dedup_engine`) vs (a) the niche's existing `topic_pool` rows and (b) published/in-flight titles.
- `INSERT … ON CONFLICT (niche_id, dedup_key) DO NOTHING` into the table named by `row['target_table']` (`topic_pool`) — read the destination from the row, don't hardcode, matching how `tap_corsair_csv` reads its own target.
- Return `{"records": inserted}` so `tap_runner` tallies correctly.

### 2b. Niche-aware sourcing (`web_search`)

`web_search.extract` today reads `config["categories"]` (or, absent it, **every** key in the global `CATEGORY_SEARCHES` map) and ignores the niche — so all niches get the same candidates. Make its query resolution niche-aware, first match wins:

1. **`config.seed_queries`** (explicit operator override) — a pinned list of search strings. Deterministic, full control, never overridden.
2. **`config.categories`** → seed queries from the `CATEGORY_SEARCHES` bank (kept for back-compat + explicit category selection; this is what the migration seeds per niche).
3. **Niche-derived** — build seed queries from the niche `name` + `target_audience_tags` (now present in `extract_config`). Baseline derivation is **deterministic templating** (per tag, e.g. `"{niche_name} {tag}"`), no LLM. This is the path that actually scales across niches and fits niches the bank doesn't cover (AI/ML).
4. **None available → fail loud.** The old "no config ⇒ search every global category" branch is **retired** — it pulled cross-niche junk and violated `feedback_no_silent_defaults`.

In prod from day one this means gaming / pc-hardware resolve via (2) — their migration-seeded `config.categories` — and AI/ML resolves via (3) — tag-derived, since the bank has no AI key — so both the seeded-category and tag-derived paths are live and tested, not one dormant behind the other.

The pattern generalizes to any query-driven source; **feed sources** (`hackernews`, `devto`) stay feed-based — their niche differentiation happens at ranking, not fetch.

**ML successor (flagged, not built now):** replace the deterministic templating in (3) with **LLM query-generation** from `(niche name + target_audience_tags + goals)`, regenerated per niche on a cadence and **cached** (query ideation is creative/judgment work per `feedback_calculated_vs_generated`; caching keeps it token-cheap per `feedback_token_efficiency`). The deterministic templater is the in-scope baseline and the seam stays identical — the successor swaps the derivation, not the interface (`feedback_always_keep_ml_in_mind`).

### 3. Orchestrator (`TopicBatchService`)

- Replace `_discover_external` + `_discover_internal` with `_read_pool(niche)` → returns `pooled` rows for the niche in the `{"kind": ..., "data": {...}}` shape `_embed_and_pre_rank` already consumes.
- Everything downstream is unchanged: `_embed_and_pre_rank`, `llm_final_score`, carry-forward decay, `_write_batch`, the operator gate, the empty-batch-wedge guard.
- On batch write, flip the chosen pool rows `pooled → batched`.
- The #1561 dedup pass stays as a **thin vs-published safety net** at promotion (the published set drifts between ingest time and batch time).

### 4. Internal RAG as a tap

`internal_rag` is already a `niche_sources` row (`source_name='internal_rag'`), so it promotes to a niche-bound tap (`tap_type=internal_rag`) like any other source. The handler branches on `tap_type=internal_rag` → `InternalRagSource(pool, site_config=...)` (with its per-kind limits) instead of the plugin registry, writing distilled topics to `topic_pool`. `_discover_internal` is deleted alongside `_discover_external` in b2.

### 5. Retention

Pooled rows that never get batched are pruned by a **`retention_policies` row** (reuses the existing retention data plane) — e.g. `table_name='topic_pool'`, `filter_sql="status = 'pooled'"`, `ttl_days=N`. No new retention machinery.

## Non-goals / out of scope

- No change to ranking, goal vectors, carry-forward decay, or the operator gate — the orchestrator's _selection_ logic is untouched; only its _ingestion_ is removed.
- **Source plugin _interface_ is unchanged** (`TopicSource.extract(pool, config)`); only `web_search`'s internal query-resolution gains niche-awareness (§2b). Other source plugins are untouched.
- **No LLM-based query generation in this work** — niche-derived queries use deterministic templating from `target_audience_tags`; LLM query-ideation (cached per niche) is the flagged §2b successor, not built here.
- No wiring of `weight_pct` into selection — it's preserved in config but stays unused (it's unused today too). A future weighted source-mix is its own work.
- `corsair_csv` / future singer / webhook taps unaffected (they keep `niche_id=NULL`).
- The `topic_proposal_service` half-dead sibling is a separate follow-up.

## PR sequencing

Three independently-shippable PRs, each behind validation:

1. **b1 — niche-aware pool + real ingestion (parallel).** Add `topic_pool` + `external_taps.niche_id`; run the tap migration (delete the 5 global rows, insert niche-bound taps → `target_table='topic_pool'`, seeding per-niche `config.categories`); make `web_search` niche-aware (§2b query resolution); fix `tap.builtin_topic_source` to store into the pool with full niche context. The orchestrator **still uses `_discover_external`** for batches, so the tap path and the direct path run side-by-side — the pool accumulates niche-relevant candidates and we validate it fills correctly (right niches, deduped, sane volume) before any cutover. No batch behavior changes yet. _(The `web_search` niche-awareness piece is independent of `topic_pool` and could split into a precursor PR if b1 gets heavy.)_
2. **b2 — cutover.** Switch `run_sweep` to read `topic_pool` (`_read_pool`); delete `_discover_external` / `_discover_internal`; add the `retention_policies` row for stale `pooled` rows. `niche_sources` becomes read-by-nothing.
3. **b3 — retire legacy.** Delete `TopicDiscovery` (+ `queue_topics`, `_scrape_*`, `_BRAND_KEYWORDS`, ~800 LOC tests); re-point `topic="auto"` / `/discover-topics` through the niche gate (async via `topic_auto_resolve`; `scripts/daemon.py` becomes async).

## Testing

Contract tests per layer:

- **Niche-aware `web_search` (§2b):** explicit `config.seed_queries` wins; absent → `config.categories` drives the bank; absent both → queries derived from the niche's `name` + `target_audience_tags`; two different niches → two different query sets; no config **and** no tags → fails loud (no global-bank fallback).
- **Tap handler:** given a niche-bound row, dispatches the named source with full niche context (incl. `target_audience_tags`) and inserts deduped rows into `topic_pool`; `ON CONFLICT` no-ops a re-run; a topic tap with no `niche_id` fails loud; `internal_rag` routes to `InternalRagSource`.
- **Pool:** `(niche_id, dedup_key)` uniqueness holds; status transitions `pooled → batched`.
- **Orchestrator:** `run_sweep` reads the niche pool, ranks, writes a batch, flips status; empty pool → no batch (wedge guard).
- **Retention:** stale `pooled` rows pruned, `batched` rows untouched.
- **Migration:** the 5 global `builtin_topic_source` rows are gone; each `niche_sources` row produces exactly one niche-bound `external_taps` row (`target_table='topic_pool'`, `niche_id` set, `weight_pct` + per-niche `categories` in config).

## Relationship to prior work

- **#1561 (merged):** added the dedup pass to `run_sweep` — becomes the vs-published safety net here.
- **#1569 (merged):** relocated `pick_target_length` / `CATEGORY_SEARCHES` out of `topic_discovery.py` — prerequisite groundwork for the b3 deletion. (`CATEGORY_SEARCHES` now lives in `topic_sources/_filters.py`, where §2b's category path reads it.)
