# RAG self-echo hardening — semantic topic dedup + whole-post originality rail

**Date:** 2026-07-12
**Status:** Design — pending approval
**Author:** Claude (with Matt)

## Problem

A draft — _"A Practical Guide to Allocating VRAM Across Local AI Models"_ (task
`b740e4b8-0b23-40a9-826b-f4ea059df302`, `awaiting_approval`, quality 77) —
reached the approval queue as a ~65–70% re-tread of two already-published posts
([`the-vram-currency-problem`](https://www.gladlabs.io/posts/the-vram-currency-problem-bb10de87),
[`single-gpu-vram-budgeting-and-stability`](https://www.gladlabs.io/posts/single-gpu-vram-budgeting-and-stability-9318d724)),
reproducing their exact figures (140 GB FP16, Qwen2.5-32B Q6_K ≈26 GB, `num_ctx`
8192 → 15 GB) near-verbatim.

### Root cause (evidence-backed)

**Cause — RAG self-echo on a saturated cluster:**

1. **No effective topic-time dedup.** `topic_dedup_engine` is set to
   `word_overlap`, which is semantically blind. The topic string _"GPU VRAM
   Budgeting for Local AI Inference"_ shares only ~0.6 content-word overlap with
   _"Single-GPU VRAM Budgeting and Stability"_ — below the 0.7 gate. Eight
   published VRAM posts, none suppressed.
2. **The writer grounds on its own corpus.** `two_pass_writer` defaults its RAG
   snippet source to `("posts",)` and drafts from the top-N nearest neighbors.
   For a dense cluster the neighbors _are_ the sibling posts, so the writer
   paraphrases them. (The existing MMR de-echo `writer_rag_dedup_ceiling: 0.93`
   from #2185 is too loose to drop _relevant_ siblings.)

**Detection gap — the one guard that exists is deliberately declawed:**

3. `qa.opening_originality` (the purpose-built self-echo net, #2182) **ran 3× on
   this task** but is **advisory** (`required_to_pass=false`) → flags, never
   vetoes → routed to `awaiting_approval` (the human backstop caught it).
4. It only inspects the **first 400 chars**, so it can't see the body re-tread
   across the middle sections.
5. It can't be graduated to a hard veto yet: `dev_diary` "what we shipped" posts
   share an opening cadence by template and would false-veto.
6. `qa_gates.opening_originality` shows `total_runs=0` / `last_run_status=null`
   despite the atom running — the atom path never increments the gate counters,
   so the QA Rails dashboard has no data to justify graduating it.

The hard gate that _did_ fire (`llm_critic`) drove 2 rewrite cycles, but it
grades quality/faithfulness, not cross-corpus novelty — the rewrites polished
the prose and left the redundancy intact.

## Goals

- Suppress near-duplicate topics **before** a full pipeline run burns (~17 min).
- Detect **whole-post** corpus re-treads, not just opening echoes.
- Keep the human approval backstop; make the originality gate **advisory now,
  hard-block-ready later** with the series-exclusion that unblocks graduation.

## Non-goals

- Rewriting the writer's grounding retrieval (MMR de-echo #2185 stays; deeper
  changes to `two_pass_writer` are out of scope for this pass).
- Content-embedding topic dedup (title-based semantic is the chosen signal;
  escalate only if calibration proves it insufficient — see §1.4).
- Auto-rewriting duplicates (the rail halts/flags; it does not de-dup prose).

---

## Workstream 1 — Activate semantic topic dedup

The `SemanticDeduplicator` (`services/topic_dedup_semantic.py`,
`all-MiniLM-L6-v2`, cosine) is **already built, unit-tested, and wired** through
`get_deduplicator` on both proposal paths
(`integrations/handlers/tap_builtin_topic_source.py:98`,
`services/topic_batch_service.py:523`). It is inactive only because
`topic_dedup_engine='word_overlap'`.

### 1.1 Flip the engine (all installs)

- `services/settings_defaults.py`: `topic_dedup_engine` default
  `word_overlap` → `semantic`. Ships the improvement to OSS. The model is CPU-
  pinned (§1.2) and ~80 MB; `sentence-transformers` is already a dependency
  (RAG reranker), so no new footprint.
- Prod: seed defaults use `INSERT … ON CONFLICT DO NOTHING`, so the existing
  `app_settings.topic_dedup_engine='word_overlap'` row will **not** move on
  boot. Flip the live value explicitly via `set_setting` as a deploy step.

### 1.2 Pin the embedding model to CPU (pre-existing bug, fixed in scope)

`SemanticDeduplicator._get_model` calls `SentenceTransformer(model_name)` with
no `device` → defaults to **CUDA** when a GPU is present. Activating as-is would
load an 80 MB model onto the 32 GB GPU this effort is protecting.

- Add `device` to `_get_model` / `SentenceTransformer(...)`, resolved from a new
  `topic_dedup_device` setting (default `cpu`). Mirrors the `rag_rerank_device`
  reranker-to-CPU fix.
- Cache key becomes `(model_name, device)`.

### 1.3 Reconcile + calibrate the threshold

Inconsistency: code default `0.65`, docstring says `0.85`,
`topic_dedup_existing_threshold_semantic` is **not seeded** (so the live value
is the code default 0.65).

- One-off calibration script in `scripts/` (NOT `services/` — per repo
  convention): load `all-MiniLM-L6-v2`, embed every published title, compute the
  nearest-neighbor cosine distribution, and pick a threshold that (a) sits near
  the p95 of cross-topic pairs and (b) places the 8 VRAM titles + the "GPU VRAM
  Budgeting for Local AI Inference" topic **above** the line.
- Seed the calibrated `topic_dedup_existing_threshold_semantic` (and
  `_intra_batch_threshold_semantic`) in `settings_defaults.py`. Update the
  docstring so code and prose agree.

### 1.4 Escalation gate (pre-agreed)

If calibration cannot separate the VRAM cluster from unrelated topics at any
sane threshold (title strings too short to carry the signal), escalate to the
**content-embedding** signal: compare the candidate topic+summary against the
pgvector `embeddings` (post content) table via the resident `embed_text`
(nomic) path, reusing the query the originality rail uses. Decided by the
calibration data, not a second round-trip.

### 1.5 Tests

- Positive: VRAM-cluster title pairs mark `is_duplicate=True` at the calibrated
  threshold.
- Negative: distinct on-niche titles (e.g. "Undervolting your GPU" vs "Speculative
  decoding") stay distinct.
- CPU pin: `_get_model` requests `device='cpu'` by default; honored from
  `topic_dedup_device`.

---

## Workstream 2 — Rename `opening_originality` → `content_originality` + whole-post scope

Per decision, the rail is **renamed correctly** (not kept as a legacy misnomer)
and broadened from opening-only to whole-post.

### 2.1 Rename map

| Surface                     | From                                                                | To                                                                  |
| --------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Atom file                   | `modules/content/atoms/qa_opening_originality.py`                   | `qa_content_originality.py`                                         |
| `ATOM_META.name` / reviewer | `opening_originality`                                               | `content_originality`                                               |
| graph_def node id           | `qa_opening_originality`                                            | `qa_content_originality`                                            |
| Settings                    | `opening_originality_enabled`, `opening_originality_max_similarity` | `content_originality_enabled`, `content_originality_max_similarity` |
| qa_gates row                | `name/reviewer='opening_originality'`                               | `content_originality`                                               |

Churn handled:

- **graph_def:** editing the node id in `services/canonical_blog_spec.py`
  changes the graph — regenerate the snapshot + add a re-seed migration, per the
  established procedure (#2261 `plan_image_markers`, CI guard #2263).
- **qa_gates row:** seed the new row in `0000_baseline.seeds.sql`; add a data
  migration to `UPDATE` the live prod row name/reviewer (seeds won't rename an
  existing row).
- **Settings backcompat:** seed the new keys; the atom reads new keys first and
  falls back to the old ones so a prod row lingering under the old name still
  works (per backcompat policy). Old defaults were untuned (0.83 / true), so no
  value carry-over is required.
- `atom_runs` history keeps old `atom`/`node_id` strings — harmless append-only
  log, no migration.
- Docs: `anti-hallucination.md`, `CLAUDE.md` references, regen `services.md`.

### 2.2 Whole-post scoring

Replace the opening-only embed with a chunked whole-draft scan:

1. Strip leading media boilerplate (reuse `_LEADING_IMAGE_RE`), then split the
   draft into **paragraph-based chunks**: merge paragraphs below a min-char
   floor (~200) so a one-line paragraph doesn't produce a noise vector, and
   window paragraphs above a max (~600) so a long section still yields a focused
   vector. (Mirrors the granularity `embeddings` stores post chunks at, which
   the atom already compares against.)
2. Batch-embed the chunks via the existing `embed_text` (nomic, resident).
3. For each chunk, nearest published-post neighbor via the atom's existing
   pgvector query (`embeddings JOIN posts … ORDER BY embedding <=> $1`).
4. Flag on the **max** chunk similarity; report the worst chunk + offending
   post slug in the review feedback.

Cost: N chunks × 3 QA cycles. Batch the embed call; nomic is sub-second and
resident. Contract is unchanged (`requires: content`, `produces:
qa_rail_reviews`) → the atom's `requires/produces` don't move, but the **node
rename** (§2.1) still triggers the snapshot re-seed.

### 2.3 Recalibrate the threshold

Max-over-chunks scores higher than a single opening embed, so the old 0.83
ceiling will over-flag. Recalibrate `content_originality_max_similarity` against
the real distribution (reuse the calibration harness from §1.3, chunk-vs-corpus).

### 2.4 Advisory now, graduation-ready

- Stays `required_to_pass=false` (advisory). No behavior change to the gate
  decision today.
- **Build the series-exclusion now** so a later flip to hard-block is safe:
  exempt templated recurring series (`dev_diary` / `narrate_bundle`) from the
  hard tier — either skip the hard veto for those `template_slug`s or exclude
  same-series nearest-neighbor matches. Exact mechanism confirmed in
  implementation (verify whether `dev_diary` posts run the rail at all; its
  5-node template may not include QA atoms).
- **Fix the telemetry gap:** increment `qa_gates.<reviewer>` counters
  (`total_runs`, `last_run_*`) on the atom path so the QA Rails dashboard
  reflects real runs — the evidence needed to justify graduating to hard-block.

### 2.5 Tests

- Whole-body lift is caught (the VRAM draft body as a regression fixture).
- Opening echo still caught (subsumed by chunk 0).
- Unrelated post passes (nearest neighbor below threshold).
- `dev_diary`/`narrate_bundle` series-exclusion path.
- Advisory status honored (flag, not veto); counter increments recorded.

---

## Rollout

- **PR 1** — Workstream 1 (semantic dedup activation + CPU pin + calibration +
  settings). Deploy step: `set_setting topic_dedup_engine=semantic` on prod.
- **PR 2** — Workstream 2 (rename + whole-post scope + snapshot re-seed +
  qa_gates migration + telemetry + docs). _(May split into 2a rename / 2b
  broaden if review prefers; the node rename's snapshot re-seed lives in the
  first of the two.)_
- One poindexter issue tracks both (public core pipeline). The VRAM draft is the
  worked example / regression fixture.
- Post-merge: rebuild + restart the worker (bind-mounted atom + snapshot re-seed
  take effect on restart).

## Verification

- Re-run the calibration harness; confirm the VRAM topic is suppressed at
  proposal and the VRAM draft body scores above the recalibrated rail threshold.
- Confirm `qa_gates.content_originality` counters increment on the next
  canonical_blog run.
- Confirm no `graph_def` drift (CI snapshot guard green).

## Open questions

None blocking. The one branch point (title-based vs content-embedding dedup) is
resolved by the §1.4 escalation gate at calibration time.
