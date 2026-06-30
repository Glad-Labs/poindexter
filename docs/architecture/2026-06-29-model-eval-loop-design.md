# Champion–Challenger Model Evaluation Loop

**Status:** Plan 1 (reranker vertical slice) **shipped 2026-06-29** — the Evaluate→Promote half of the loop is wired for `rag_rerank_model`. Discovery/gating, the Embedding/STT scorers, and the scheduled jobs are still pending for the full Wave-1 v1 (§14). See the [Plan](2026-06-29-model-eval-loop-wave1-plan.md) and the [operator runbook](../operations/model-eval.md).
**Date:** 2026-06-29
**Related:** [`2026-05-28-phase-1-variant-experiments-design.md`](2026-05-28-phase-1-variant-experiments-design.md) (existing SQL "Lab" A/B framework), `services/langfuse_experiments.py` (existing Langfuse A/B harness), `reference_langfuse_serializer_recursion_bug` (memory)

---

## 1. Context & goal

The pipeline pins a specific open-source model into ~25 distinct **slots** (writer, critic,
vision, embeddings, reranker, image, video, TTS, STT, plus utility LLMs). Open-source models
advance quickly, so any given pin drifts from "best available" over time — silently, because
nothing re-checks it. Several slots were last set when first wired and have never been revisited.

**Goal:** a self-updating system that continuously answers, per slot: _"is there a better
open-source model than the one we run today?"_ — discover candidates, score them against a
golden set with a capability-appropriate metric, and propose the winner for promotion.

This is **cross-cutting kernel infrastructure** (it serves every capability and every business
module), so it lives in substrate (`services/model_eval/` + scheduled jobs), not in a business
module.

## 2. Key decisions (locked in brainstorm)

| #   | Decision                                                                                                                             | Rationale                                                                                                                         |
| --- | ------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| D1  | **Self-updating champion–challenger loop**, not a one-off report                                                                     | Aligns with the SaaS/automation thesis; the system surfaces upgrades forever, not once                                            |
| D2  | **Golden eval set** for v1; **hybrid** (golden gate → live-A/B validation) as the endgame                                            | Deterministic, cheap, repeatable, decoupled from production GPU load; trustworthy before we trust it with autonomy                |
| D3  | **v1 = framework + Wave 1** (deterministic-scored slots: embeddings, reranker, STT)                                                  | Proves the whole loop on unarguable metrics before tackling judgment-based slots                                                  |
| D4  | **Decompose by scorer type**, not business importance                                                                                | Scorer type is the trust boundary — see §3                                                                                        |
| D5  | **Langfuse is the eval harness** (Datasets / Dataset Runs / Scores / Experiments UI), behind a storage seam with a Postgres fallback | Already self-hosted, already a dep, already used by `langfuse_experiments.py`; consolidates instead of forking a third A/B system |
| D6  | **Scorer / discovery / gating / promotion stay bespoke**                                                                             | These are model-slot-evaluation concerns, not generic LLM-eval concerns — no tool provides them                                   |
| D7  | Promotion is **PR-based** (Wave 1); Wave 2 promotes **into the existing experiments framework** as a live A/B variant                | Fits "all changes via PR / CI-green merge"; reuses the dormant live-A/B back-half instead of rebuilding it                        |

## 3. Decomposition by scorer type

| Wave                  | Scorer                      | Slots                                                                                             | Metric                           | Promotion shape                                                                 |
| --------------------- | --------------------------- | ------------------------------------------------------------------------------------------------- | -------------------------------- | ------------------------------------------------------------------------------- |
| **1 — Deterministic** | computable, no judge        | `embed_model`, `niche_embedding_model`, `rag_rerank_model`, `voice_agent_whisper_model`           | recall@k / nDCG / MRR / WER      | reranker = stateless swap; embeddings = **re-embed migration**; STT = stateless |
| **2 — Judge-based**   | LLM / vision judge          | writer (×7 gemma slots), critic, `qa_vision_model`×3, `structured_extraction_model`, utility LLMs | reuse existing QA rails as judge | live A/B via experiments framework                                              |
| **3 — Perceptual**    | human-in-loop / specialized | `image_model`, `generative_video_model`, `podcast_tts_model`                                      | aesthetic/MOS — hardest          | human gate; lowest urgency (image/video just adopted)                           |

**Why this seam:** a system that _promotes_ models is only as credible as its scorer. Wave 1
metrics (recall@k, WER) are ground truth — a regression is unarguable. Wave 2/3 scores are
opinions and need a human/PR gate longer. Building the framework on Wave 1 proves the loop on
unarguable scores first.

### Model slot inventory (source: `services/settings_defaults.py`)

Current pins, grouped by wave:

- **Wave 1:** `embed_model`=`nomic-embed-text`, `niche_embedding_model`=`nomic-embed-text`,
  `rag_rerank_model`=`cross-encoder/ms-marco-MiniLM-L-6-v2`, `voice_agent_whisper_model`=`base`
  — all flagged ripe/stale.
- **Wave 2:** writer + 6 sibling slots = `gemma-4-31B-it-qat` (bakeoff #1692 — likely current),
  `pipeline_critic_model`=`phi4:14b`, `qa_fallback_critic_model`=`qwen2.5:32b`,
  `qa_vision_model`/`qa_preview_vision_model`/`vision_alt_model`=`qwen3-vl:30b`,
  `structured_extraction_model`=`gemma-4-31B-it-qat`, `voice_agent_llm_model`=`glm-4.7-5090`,
  utility (`ops_triage_writer_model`=`llama3.2:3b`, `inline_image_prompt_model`=`llama3:latest`).
- **Wave 3:** `image_model`=`z_image_turbo`, `generative_video_model`=`Wan2.2-TI2V-5B`,
  `podcast_tts_model`=`Kokoro-82M` — image/video recently adopted, defer.

## 4. Architecture — the loop

```
┌──────────┐   ┌────────┐   ┌────────────┐   ┌────────────┐
│ Discover │──▶│  Gate  │──▶│  Evaluate  │──▶│  Promote   │
│ HF scan  │   │license │   │ golden set │   │ PR (W1) /  │
│ + seed   │   │ VRAM   │   │ champion   │   │ live A/B   │
│ list     │   │servable│   │ vs         │   │ variant    │
│          │   │        │   │ challenger │   │ (W2)       │
└──────────┘   └────────┘   └─────┬──────┘   └────────────┘
                                  │
                          ┌───────▼────────┐
                          │ Scorer (ours)  │  computes the metric,
                          │ per capability │  pushes a Langfuse Score
                          └────────────────┘
```

**Stages:**

1. **Discover** — `DiscoverModelCandidatesJob` queries HF (server-side `huggingface_hub`, _not_
   the MCP) per capability tag, plus a curated seed list in `app_settings`. Writes candidate rows.
2. **Gate** — auto-filter by hard rules: **commercial license** (HF metadata), **VRAM ceiling**
   (param-count estimate ≤ slot budget), **servability** (Ollama-pullable / HF-loadable). Rejects
   are logged with a reason.
3. **Evaluate** — `RunModelEvalJob` runs champion + queued challengers over the slot's golden set
   through a pluggable **`Scorer`**, recording each run as a **Langfuse Dataset Run** with the
   metric pushed as a **Score**. Scheduled for idle hours — never competes with production for GPU.
4. **Promote** — if a challenger beats champion by ≥ `model_eval_promotion_margin` and doesn't
   regress latency/VRAM past tolerance → propose the swap (see §8).

## 5. Harness: Langfuse (D5)

Langfuse replaces the data-plumbing and visualization third of the system:

| Concern          | Langfuse primitive                                                                                   | Replaces (vs all-bespoke)                       |
| ---------------- | ---------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| Golden set       | **Dataset** (input / expected_output / metadata; multimodal incl. audio via `LangfuseMedia` for STT) | `golden_eval_sets` + `golden_eval_cases` tables |
| Eval run         | **Dataset Run** per candidate model                                                                  | `model_eval_runs` table                         |
| Metric value     | **Score** (custom, via SDK — numeric + metadata)                                                     | score columns + aggregation                     |
| Comparison / viz | **Experiments UI** (runs side-by-side)                                                               | a new Grafana "Model Eval" board                |

**Storage seam (reversibility).** The `Scorer` _computes_; the harness _stores/compares_. These
are separated by an interface, so the storage backend (Langfuse vs Postgres) is a rentable
implementation. Design the run/store layer backend-agnostic; **start on Langfuse**; if the
self-hosted install proves unreliable, swap to Postgres tables **without touching scorers,
discovery, gating, or promotion**. ("Own the interface, rent the implementation.")

**What Langfuse does _not_ do:** compute deterministic metrics (recall@k/WER/nDCG — that's the
`Scorer`), discovery, gating, or promotion. Those stay bespoke (D6).

## 6. `Scorer` interface + Wave 1 implementations

```
Scorer.score(model, golden_set) -> MetricResult   # the one disposable seam
```

Wave 1 ships three implementations; later waves add `JudgeScorer` (wraps existing QA rails) and
perceptual scorers without touching the loop:

- **`EmbeddingScorer`** — embeds the golden set's queries + corpus with the candidate model,
  measures **recall@k / nDCG@k / MRR** against labeled relevant-doc ids.
- **`RerankerScorer`** — reranks a fixed candidate set per query, measures **nDCG@k / MRR**
  against relevance labels. Can reuse the embedding set's retrievals as input.
- **`STTScorer`** — transcribes audio clips, measures **WER** against reference transcripts.

## 7. Golden sets — bootstrapped from production data (no dummy data)

v1 needs **zero hand-labeling** to start — the production corpus _is_ the test set:

- **Embeddings / Reranker:** (query → relevant-doc) pairs mined from 110 posts + 22K embeddings
  (e.g. title-as-query → body-as-relevant), plus a curated set of harder cases.
- **STT:** (audio → reference transcript) pairs harvested from existing podcast audio + scripts
  (already produced aligned by the podcast pipeline).

Golden sets are **versioned** (Langfuse Dataset versions) so metric deltas are comparable over time.

## 8. Promotion (D7)

- **Wave 1 (deterministic):** **PR-based.** A winner auto-opens a PR against `glad-labs-stack`
  changing the `settings_defaults.py` value + attaching the eval report (metric deltas, latency,
  VRAM). Normal review / CI-green-merge applies. For **embeddings**, the PR bundles/triggers the
  **re-embed migration** of all ~22K vectors. The **reranker** (stateless, no migration) may opt
  into direct auto-swap via a per-slot `auto_promote` flag, since nDCG is unarguable.
  _Business-outcome A/B is meaningless for these slots (an embedding model has no "views_7d"), so
  the golden-set metric is the final word._
- **Wave 2 (judge-based):** offline golden-set/judge **gate**, then promote the winner **into the
  existing experiments framework** (§9) as a live A/B variant for business-outcome validation
  (views / approval-rate / views-per-dollar) before it becomes the default. This is the hybrid
  endgame — _reuse_, not rebuild.

Never silently degrade: keep-best guard; a promoted model that later underperforms in production
(Wave-2 live A/B) is reverted.

## 9. Relationship to existing experiment infrastructure

There are **two dormant A/B subsystems** already in the tree; this design must not add a third.

1. **SQL "Lab" framework** ([`2026-05-28-phase-1-variant-experiments-design.md`](2026-05-28-phase-1-variant-experiments-design.md)):
   `experiments` / `experiment_variants` tables (variant fields include **`writer_model`**,
   `prompt_template_key`, `rag_config`), `capability_outcomes.variant_id`, EWMA weight feedback
   (#361), scorecard views, `experiment_runner.py` / `experiment_admin.py` / CLI. Objective
   functions are **business metrics**. Gated off by `experiment_weighted_selection_enabled=false`.
2. **Langfuse experiment hook** (`pipeline_experiment_hook.py` → `langfuse_experiments.py`, #202):
   content A/B on Langfuse Datasets, understands one knob (`writer_model`). Gated off by
   `active_pipeline_experiment_key=''`.

**Stance:**

- The SQL Lab framework **is the live-A/B back-half** of our hybrid endgame — it was purpose-built
  to A/B _models_ (the `writer_model` column proves it). Wave-2 promotion **reuses** it.
- Our offline golden-set loop is the **missing front-half** — confirmed non-overlapping (nothing
  here does offline technical-metric scoring; all existing infra is online/business-outcome).
- We lean the eval substrate toward **Langfuse** (D5), which also points the way to **collapsing
  the SQL-vs-Langfuse duplication** toward Langfuse. **Open consolidation decision** flagged for
  the implementation plan — out of scope for v1, but v1 must not deepen the duplication.
- To make the experiments framework testable beyond the writer, its variant override
  (currently `writer_model`-only) needs **generalizing to any slot** — a Wave-2 task, anticipated
  by the hook's "one app_settings key at a time" note.

## 10. Why the Langfuse `@observe` bug is orthogonal

The serializer-recursion bug ([langfuse-python#1655](https://github.com/langfuse/langfuse-python/issues/1655))
lives entirely in the **`@observe` auto-capture / logging-hook** path — serializing _arbitrary_
captured objects that may contain cycles (every `@observe` in the tree is on an LLM-call path).

The eval path uses **explicit Dataset / Dataset Run / Score SDK calls with controlled, plain-data
payloads** (query strings, doc-id lists, float metrics) — no cycles, never routes through the
vulnerable auto-capture. `langfuse_experiments.py` already uses exactly these primitives and was
never implicated in the hangs. **Therefore eval on Langfuse is safe by construction, independent
of the `@observe` tracing bug's status.** (Re-enabling `@observe` LLM tracing reliably is a
separate, already-spawned task; not a precondition here.)

## 11. Data model

- **Reused (read-only):** `capability_outcomes` / `atom_runs` (production telemetry — the eventual
  Wave-2 live-A/B signal source); `experiments` / `experiment_variants` (Wave-2 promotion target).
- **New (small):** `model_candidates` (discovery queue: model → per-slot gating status
  discovered→gated→queued→champion/rejected) and `model_eval_slots` (registry: slot_key →
  capability_type, scorer_id, golden_set_id, vram_ceiling, promotion_kind — seeded from the
  `value_type:'model'` annotations already in `settings_defaults.py`).
- **Externalized to Langfuse:** golden sets, eval runs, scores (the 3 tables we'd otherwise build).

New settings → `settings_defaults.py`; new tables → timestamped migration (per migrations rule).

## 12. Error handling

- **Fail loud** on missing golden set / slot config (per `feedback_no_silent_defaults`).
- **Self-heal** on transient HF/Langfuse/infra errors: skip the cycle, log, no crash.
- A challenger that OOMs or won't load → marked `rejected` with reason; **never poisons the
  champion**.
- Promotion: PR-creation failure → alert, never auto-mutate settings; re-embed migration failure →
  rollback, keep champion.
- Routine promotion proposals → Discord (not Telegram), per the notify-tier rule.

## 13. Testing

- **Unit:** each `Scorer` against a tiny fixture golden set (deterministic metric assertions);
  gating filters (license/VRAM/servability); promotion-margin comparison; PR-payload generation;
  Langfuse-adapter calls mocked.
- **Integration:** full loop on a 3-case golden set with stub champion + stub challenger, asserting
  a winner is correctly identified and a promotion proposal emitted.
- Docs + contract tests per `feedback_docs_and_tests_default`.

## 14. v1 deliverable

Framework + Wave 1:

- Slot registry (`model_eval_slots`), `Scorer` interface, `DiscoverModelCandidatesJob`,
  `RunModelEvalJob`, `model_candidates` table, Langfuse harness adapter (behind the storage seam),
  PR-based promotion, CLI (`poindexter model-eval {discover,run,status,promote}` as thin adapters).
- Three Wave-1 scorers (Embedding / Reranker / STT) + bootstrapped golden sets.
- HF discovery + gating for the three Wave-1 capabilities.
- Reranker eligible for opt-in `auto_promote`.

### Shipped — Plan 1 (reranker vertical slice, 2026-06-29)

The first slice proves the loop end-to-end on the single `rag_rerank_model` slot
(deterministic nDCG metric → unarguable). Landed (`services/model_eval/` + `poindexter/cli/model_eval.py`):

- `Scorer` interface + `MetricResult` / `GoldenSet` types; nDCG@k + MRR metrics; `RerankerScorer` (nDCG@10).
- `EvalHarness` storage seam with the `LangfuseEvalHarness` adapter + an `InMemoryEvalHarness` test double.
- Reranker golden set bootstrapped from published posts (versioned by post-id hash, deterministic distractors).
- Runner (`run_slot_eval` → `EvalReport`) + margin comparison; PR/`auto_swap` promotion **proposal** (`propose_promotion`).
- `run_reranker_bakeoff` orchestrator + CLI `poindexter model-eval {run,status}` (thin adapters).
- Settings: `model_eval_promotion_margin`, `model_eval_reranker_golden_size`, `model_eval_reranker_candidates_per_case`.

**Still pending for full Wave-1 v1:** `DiscoverModelCandidatesJob` (HF scan) + gating
(license/VRAM/servability), `model_candidates` / `model_eval_slots` tables, the scheduled
`RunModelEvalJob`, the `EmbeddingScorer` + `STTScorer` (with their golden sets), the
`discover` / `promote` CLI subcommands, and **auto-executing** a promotion (Plan 1 surfaces the
proposal; it does not auto-open the PR or auto-flip the setting).

## 15. Deferred

- **Wave 2** (judge scorers; generalize variant override; promote-into-experiments-framework path).
- **Wave 3** (perceptual scorers; human gate).
- **Hybrid shadow validation** (golden gate → live A/B) — the endgame beyond v1.
- **SQL-vs-Langfuse experiment-infra consolidation** — decide at plan time; do not deepen in v1.

## 16. Open questions / plan-time verifications

1. Read `experiment_runner.py` / `experiment_admin.py` internals to design the exact Wave-2 reuse
   seam (winner computation, variant lifecycle).
2. Confirm Langfuse self-hosted (v4.6) supports Dataset Runs + Experiments comparison UI on the
   OSS tier (custom Scores via SDK are confirmed; managed LLM-judge evaluators may be gated — we
   don't depend on them).
3. Confirm the storage-seam abstraction is thin enough that the Postgres fallback is a genuine
   drop-in.
4. Decide the consolidation direction for the two dormant experiment subsystems.
