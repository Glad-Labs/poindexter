# QA Gate: Self-Heal Before Paging (No Auto-Discard) — Design Spec

**Date:** 2026-06-22
**Status:** Approved for implementation
**PR target:** `Glad-Labs/glad-labs-stack main`

---

## Context

The QA gate currently **auto-discards written drafts**. When `qa.aggregate` can't
approve a draft, it writes `status='rejected'`, halts the graph, and the topic is
gone. The data says this is the wrong default:

- **Reject rate of written drafts:** ~36% over the last 30 days (~78% over 90 days,
  pre-improvement). 98.6% of rejected tasks (1,405 / 1,425 over 90 days) have a
  generated draft — these are real discards, not topic-pool culls.
- **The discarded drafts are good.** Of recently-rejected drafts, **88% scored ≥70**
  (the pass bar) and the **average rejected score is 79/100**. We are throwing away
  79-average posts on a single rail's veto.
- **The vetoes are frequently wrong or trivially fixable.** Sampled rejects:
  - `fa07bfbf` (score **98**): `programmatic_validator` hard-failed on
    `generate_content` — **our own pipeline function** — flagged as a "hallucinated
    API reference" because it isn't in PyPI/stdlib. False positive.
  - `37667d14` (score **92**): `ollama_critic` said _"excellent and
    publication-ready"_, `programmatic_validator` 95 — killed by
    `deepeval_faithfulness` over weak RAG context. False positive.
  - `a25281b3` / `20b8687d`: the critic _passed_ them numerically but flagged a real
    truncation / an empty-parens typo — **fixable defects**, not reasons to discard a
    topic.
- **No operator visibility.** Every rail's verdict is already written to
  `pipeline_versions.qa_feedback`, but nothing surfaces it when a post reaches
  `awaiting_approval`. The operator can't see what any gate flagged.

**Framing — self-heal before paging.** This is the content analogue of the ops
`self_heal_not_suppress` principle: exhaust the automatic fix (bounded regeneration)
before "paging" the operator (route to `awaiting_approval`), and **never silently
suppress** (discard). The pipeline's worst action on a written draft becomes
_regenerate, then hand to the operator with the findings attached._ `rejected_final`
becomes an **operator-only** state.

---

## Goal

A written draft is never auto-terminated. On a QA veto the pipeline:

1. attempts a **bounded regeneration** (the garbage-collector rerun — now covering
   hard vetoes, not just soft critic vetoes), then
2. if regeneration is exhausted or can't help, **flags the draft and lets it flow to
   `awaiting_approval`** as a complete, publishable post with the per-rail findings
   attached, and
3. **never** writes `rejected` / `rejected_final` — that decision is the operator's.

Plus three supporting changes: surface the findings at the review point, demote the
weak-RAG false-positive judge, and stop the validator from flagging our own
vocabulary as fabrication.

## Non-Goals

- **No change to topic-pool culling.** The ~1,250 `stage=pending` rejections are
  upstream topic/dedup decisions made before generation — a different system, out of
  scope.
- **No change to the rail set or the scoring weights.** Same rails, same
  `aggregate_rail_reviews` math.
- **No new operator UI.** Findings surface through the existing MCP/CLI approval
  surfaces.
- **No graph-topology change.** The `qa_rewrite` node and the branch/loop edges
  already exist (2026-06-17 rescue cycle). No `graph_def` reseed.
- **Upstream gates unchanged.** `draft_gate` (seeded disabled) and
  `quality_evaluation` (a scorer, not a gate) are untouched. `qa.aggregate` is the
  only auto-discard point for a written draft.

---

## Chosen Approach: Flag-and-Continue

When regeneration is exhausted or not eligible, `qa.aggregate` stops discarding.
Instead of `persist_qa_reject` + `_halt` + `status='rejected'`, it stamps the draft
`qa_flagged=True`, attaches the findings, and lets it flow down the **existing
default forward edge** (`qa_aggregate → seo_all_metadata → … → persist_task`). The
post lands at `awaiting_approval` as a finished, publishable article. Auto-publish
refuses a flagged post.

**Why this over the alternative ("halt-to-operator"):** halting at `qa.aggregate`
and setting `awaiting_approval` there would leave the post _incomplete_ (no SEO
metadata, no media scripts) and require a resume pass to finish it before publish.
Flag-and-continue reuses `persist_task`'s existing `awaiting_approval` write and
hands the operator a one-click-publishable post. Media render is on _publish_, not
approve, so a flagged post the operator rejects costs only the cheap SEO-metadata +
script-text compute.

The cheap "demote rails + surface, keep discarding hard vetoes" option is rejected: it
still auto-discards fabrication vetoes, violating the no-auto-`rejected_final` rule.

---

## Design

### 1. `qa.aggregate` terminal change — flag instead of reject

Today (`qa_aggregate.py`, lines ~274–306) the `not approved` terminal path calls
`persist_qa_reject`, sets `out["_halt"]=True`, and sets `out["status"]="rejected"`.

New behavior, gated by the master switch `qa_flag_instead_of_reject` (see §9):

- **Switch ON (new):**
  - Do **not** call `persist_qa_reject`, do **not** set `_halt`, do **not** set
    `status`.
  - Emit into state: `qa_flagged=True`, `qa_flag_reasons` (the `vetoed_by` list +
    `"below_threshold"` when the score fell short), `qa_reviews=list(reviews)`,
    `qa_feedback=build_qa_feedback(reviews, kept_score, approved=False)`,
    `quality_score=promoted`, and (keep-best) `content=kept_content` when an earlier
    draft outscored the final revision.
  - `_goto=""`, no `_halt` → the branch router falls through to the default forward
    edge (`seo_all_metadata`). The post finishes the graph and `persist_task` sets
    `awaiting_approval`.
  - Emit a `qa_flagged_surfaced` audit row (replaces the silent reject; powers the
    Grafana flag-rate panel).
- **Switch OFF (today's behavior, unchanged):** `persist_qa_reject` + `_halt` +
  `status='rejected'`, exactly as now.

The `qa_pass_completed` audit row still fires on every pass (approve / flag), so the
QA Rails dashboard denominator is unaffected.

### 2. Broaden regeneration eligibility

`is_rescuable_reject` (in `_qa_rail_common.py`) today only routes **critic-only**
vetoes and **pure below-threshold** rejects to `qa.rewrite`; programmatic / gate
vetoes are excluded (they go straight to the terminal). Under self-heal-before-paging
we want the garbage-collector to rerun _any_ draft a text revision could plausibly
fix, before surfacing.

New eligibility (broadened in place; docstring + tests updated). A reject is
**regen-eligible** iff a text revision could plausibly clear it:

| Veto source                                              | Today   | New                | Rationale                                            |
| -------------------------------------------------------- | ------- | ------------------ | ---------------------------------------------------- |
| below-threshold, no veto                                 | regen   | **regen**          | a better draft lifts the score                       |
| `llm_critic` (ollama/anthropic/google)                   | regen   | **regen**          | catches truncation/typos a revise fixes              |
| `programmatic_validator` (fabrication/structure)         | discard | **regen**          | a revise can drop a fabricated claim / fix structure |
| `deepeval_brand_fabrication`                             | discard | **regen**          | a revise can remove the brand violation              |
| `web_factcheck` / `internal_consistency`                 | discard | **regen**          | text-fixable factual/consistency issues              |
| `missing_required:*` (a required rail emitted no review) | discard | **surface-direct** | infra gap — regen won't make the rail run            |
| `vision_gate` (bad image)                                | discard | **surface-direct** | a text revise can't fix an image                     |
| `url_verifier` (dead link)                               | discard | **surface-direct** | not reliably text-fixable; v1 surfaces               |

"surface-direct" = skip regen, go straight to flag-and-continue. After
`qa_rewrite_attempts` reaches `qa_rewrite_max_attempts`, _every_ reject flag-and-continues
regardless of source (the existing bound; `qa.rewrite` degrades to keep-prior-content
on writer error, so the loop always terminates).

`qa_rewrite_max_attempts` default is raised from `1` to **`2`** (already the code
default in `qa_aggregate._max_attempts`; the seeded setting still says `1` — this
aligns them) so a genuinely-broken draft gets two rerun attempts before surfacing.
Operators tune `0`–`3`.

### 3. `content.evaluate_auto_publish` — refuse flagged posts

The auto-publish decision lives in `modules/content/auto_publish_gate.py::evaluate`
(surfaced by `content.evaluate_auto_publish`, which is **observe-only / `dry_run`
today** — it never actually publishes; it evaluates the gate and re-asserts
`awaiting_approval` on every exit path). Add a `qa_flagged` short-circuit in `evaluate`:
when the post is flagged it returns `would_fire=False` (reason `qa_flagged`), so a
flagged post can **never** fire a real auto-publish even after the edit-distance track
record enables auto-publish. `evaluate_auto_publish` still lands the post at
`awaiting_approval` exactly as today.

The guard is scoped to the **auto**-publish path only. It does **not** touch the
operator's explicit publish (`publish_service` / MCP `approve_and_publish_post`) or the
preview_gate approve-resume — an operator can always publish a flagged post they have
reviewed. See §10.

### 4. Durable `qa_flagged` signal

`qa_flagged` must reach the operator's DB-backed review surface, so it's persisted:

- **`PipelineState`:** add channel `qa_flagged: bool` (default `False`), last-value.
- **Migration:** add `pipeline_tasks.qa_flagged BOOLEAN NOT NULL DEFAULT false`
  (mirrors the preview*gate `regen*\*`column pattern; queryable for Grafana) **and add
the column to`pipeline_tasks_view`\*\* so the read seam in §5 can select it.
- **`content.persist_task`:** write `qa_flagged` to the new column from state, in the
  same write that sets `awaiting_approval` (it already owns the terminal status write
  via `update_task_status_guarded`).
- The per-rail detail is already persisted: `record_pipeline_version` writes
  `qa_feedback` from `qa_reviews` on the forward path, so the flagged post's findings
  land in `pipeline_versions.qa_feedback` with no new write.

### 5. Surface findings at `awaiting_approval` (C1)

The data exists; it's just not shown. The MCP `list_tasks` tool delegates to
`services/tasks_mcp.py::list_tasks`, which selects from `pipeline_tasks_view`. Surface
the findings through that same service seam (adapters stay thin — all SQL lives in the
service layer, per the adapter-purity ratchet):

- Add `qa_flagged` to the `tasks_mcp.list_tasks` SELECT (`task_id, topic, status,
quality_score, created_at, qa_flagged`) — relies on the §4 view column. The listing
  now carries the flag + score per task.
- Add a task-detail read helper in the service layer (`tasks_mcp` / `content_task_store`)
  returning the latest `pipeline_versions.qa_feedback` for a task (the full per-rail
  breakdown). The one-line summary is computed on read from the `qa_feedback` header —
  no stored summary column.
- **CLI-first** (`feedback_cli_first`): `poindexter pipeline list` shows a `⚑` marker
  - score for flagged tasks; `poindexter pipeline qa <task>` prints the full findings.
    MCP `list_tasks` mirrors the marker; the per-task findings are reachable via the
    detail tool.

### 6. Demote `deepeval_faithfulness` → advisory (C2)

Currently `required_to_pass=true`. It is the weak-RAG false-positive driver (it killed
the 92-scoring "publication-ready" `37667d14`), and a text revise can't fix a
RAG-context gap, so under the new flow it would only burn a regen attempt and raise a
spurious flag. Demote to advisory so it **informs** (surfaced in `qa_feedback`)
without vetoing.

- **Migration (prod mutation):** `UPDATE qa_gates SET required_to_pass=false WHERE
name='deepeval_faithfulness'`.
- **Baseline (fresh DBs):** update `0000_baseline.seeds.sql` to seed the row advisory
  (finish-migrations: both the one-shot prod mutation and the fresh-DB seed).

`llm_critic`, `programmatic_validator`, and `deepeval_brand_fabrication` stay required
(now non-discarding regen-triggers).

### 7. Programmatic-validator internal-term allowlist (C3)

`content_validator`'s hallucination detector flags library/API references absent from
stdlib / top-PyPI / known-Ollama-models. It has no knowledge of our own codebase, so
self-referential posts (e.g. "Fixing Video Render-Path Lock Gaps") trip it on names
like `generate_content`. Add a DB-configurable allowlist:

- **Setting:** `content_validator_internal_terms_allowlist` (CSV, default seeds the
  known internal/project terms — pipeline function and atom names that recur in
  dev-facing posts).
- The validator merges the allowlist into its safe-term set; an allowlisted token is
  never flagged as a hallucinated reference.
- Generalizes for customers (allowlist your own product/API names) — consistent with
  DB-first config.

### 8. Telemetry + Grafana

- New audit event `qa_flagged_surfaced` (source `qa.aggregate`) with `final_score`,
  `vetoed_by`, `attempts`. Per `feedback_grafana_everything`, add a **flag-rate**
  panel to the Pipeline (or QA Rails) dashboard: flagged vs clean `awaiting_approval`
  over time, sourced from `audit_log WHERE event_type='qa_flagged_surfaced'` and the
  `pipeline_tasks.qa_flagged` column.

### 9. Master switch + rollout

- **Setting `qa_flag_instead_of_reject`** (default `'false'`). When `false`,
  `qa.aggregate` behaves exactly as today (broadened regen and flag-and-continue are
  both inert — hard vetoes discard). When `true`, the full self-heal flow is live.
- Ship **inert** (default `false`), verify end-to-end in the Docker stack (drive a
  draft to a flagged terminal, confirm it lands at `awaiting_approval` with findings
  and is not auto-published), then flip the default to `'true'` — the preview_gate
  playbook.
- C1 (surface), C2 (faithfulness advisory), and C3 (allowlist) are **independent** of
  the switch — they're improvements under either behavior and can land/flip on their
  own.

### 10. Interaction with preview_gate (operator regen choices)

The preview_gate (`record_pipeline_version → preview_gate → evaluate_auto_publish`,
with branch+loop back-edges to `plan_image_markers` and `generate_draft`) gives the
operator approve / regen_text / regen_images / reject at the review point.
Flag-and-continue is **compatible with and protective of** that flow:

- **Flagged posts reach preview_gate like clean ones.** Flagging means "don't halt;
  keep flowing," so a flagged draft rides the same forward path through `persist_task →
record_pipeline_version → preview_gate`. The operator gets the full regen-choice
  surface for a flagged post — and with C1 the gate now shows _why_ it was flagged, so
  "regen_text" becomes an informed fix.
- **Both regen choices re-run the QA block, and flag-and-continue prevents them from
  silently destroying the post.** `regen_text → generate_draft` and `regen_images →
plan_image_markers` both loop forward through the qa.\* rails + `qa.aggregate`. Under
  **today's discard behavior**, an operator regen whose new draft fails QA would hit
  `qa.aggregate`'s `_halt` + `status='rejected'` — **the operator's post vanishes
  mid-regen.** Under flag-and-continue the regenerated draft flags-and-returns to the
  gate instead. So this change is effectively a **prerequisite for safely enabling
  preview_gate's regen** (relevant to the pending T12 flip of `pipeline_gate_preview_gate`).
- **The `qa_flagged` auto-publish guard does not interfere.** `evaluate_auto_publish`
  is observe-only/`dry_run` today and always lands the post at `awaiting_approval`; the
  guard (§3) only suppresses a _future_ real auto-publish. It never blocks the
  operator's preview_gate approve-resume or their explicit publish. The
  `_FINALIZE_ALLOWED_FROM=("in_progress","awaiting_gate")` guard already finalizes a
  gate-approved post to `awaiting_approval` correctly.
- **`content.persist_task` must accept a flagged (non-approved) draft.** Today it only
  ever sees approved posts (rejects halt before it); under flag-and-continue it also
  persists flagged drafts as `awaiting_approval` + `qa_flagged=true`. The implementation
  must confirm `persist_task` has no "approved-only" assumption that would skip the row.

---

## Bounds and Safety

| Concern                                              | Mitigation                                                                                                                                                                                                                          |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Infinite regen loop                                  | Durable `qa_rewrite_attempts` counter in the LangGraph postgres checkpoint; clamped [0,3]; `qa.rewrite` degrades to keep-prior on writer error. Unchanged from the rescue cycle.                                                    |
| Flagged post auto-published                          | `auto_publish_gate.evaluate` returns `would_fire=False` on `qa_flagged`; auto-publish is `dry_run` today anyway; below-threshold score also fails `score ≥ threshold`; global `auto_publish_threshold` default `0`. Layered guards. |
| Operator regen destroys post mid-flight              | A preview_gate regen that re-fails QA flag-and-continues back to the gate instead of `_halt`+`rejected` (§10) — this change protects the preview_gate regen loop, not just the forward path.                                        |
| Operator flooded with flagged posts                  | Recent volume is a few drafts/day; regen clears the fixable ones first; the `qa_flag_summary` lets the operator triage at a glance.                                                                                                 |
| Genuinely broken draft (empty/garbage) surfaced      | The garbage-collector regen reruns it (now up to 2×) before surfacing; truly unrecoverable output still reaches the operator flagged, never auto-`rejected_final` (operator's call, per spec).                                      |
| Keep-best regression                                 | Existing keep-best guard runs before the terminal branch; the flagged post carries the highest-scoring draft seen across passes.                                                                                                    |
| Silent behavior change in prod                       | Master switch defaults `false` (today's behavior); flipped only after e2e verification.                                                                                                                                             |
| `missing_required` infra gap regenerated pointlessly | `missing_required:*` is surface-direct (no regen) — a rerun won't make an absent rail emit.                                                                                                                                         |

---

## Testing Plan (TDD — failing tests first)

1. **`is_rescuable_reject` broadened** — programmatic veto now regen-eligible, brand
   veto regen-eligible, web_factcheck/consistency regen-eligible, `missing_required:*`
   NOT eligible (surface-direct), vision/url NOT eligible, below-threshold eligible.
2. **`qa.aggregate` flag-and-continue (switch ON)** — terminal fail sets
   `qa_flagged=True` + `qa_flag_reasons` + `qa_feedback`, does **not** set `_halt`,
   does **not** set `status='rejected'`, does **not** call `persist_qa_reject`,
   emits `_goto=""` (→ forward edge).
3. **`qa.aggregate` switch OFF** — terminal fail behaves exactly as today (persist +
   halt + rejected). Regression guard for the existing reject tests.
4. **`evaluate_auto_publish` refuses flagged** — `qa_flagged=True` → no auto-publish,
   stays `awaiting_approval`, even with `auto_publish_threshold` low.
5. **`persist_task` writes `qa_flagged`** — column set from state.
6. **`record_pipeline_version` persists `qa_feedback` on the flagged forward path** —
   findings reach `pipeline_versions.qa_feedback`.
7. **C1 surface** — listing service fn returns `qa_flagged` + `qa_flag_summary`;
   detail service fn returns full `qa_feedback`; CLI renders both; MCP mirrors.
8. **C2** — migration flips `deepeval_faithfulness` advisory; `aggregate_rail_reviews`
   no longer vetoes on a faithfulness fail; it still appears in `qa_feedback`.
9. **C3** — `content_validator` does not flag an allowlisted internal term;
   non-allowlisted unknown reference still flags; allowlist read from the setting.
10. **End-to-end `test_graphdef_pipeline`** — a hard-veto draft (switch ON) regenerates
    (bounded), then flags-and-continues to `persist_task`/`awaiting_approval`;
    `evaluate_auto_publish` does not publish it; `_halt` never fires on the flag path.
11. **preview_gate interaction** — with preview_gate enabled and switch ON, a flagged
    draft reaches `preview_gate` (pauses for the operator); a regen choice loops back
    through the QA block and, if the regen again fails QA, flag-and-continues to the
    gate rather than halting/discarding (the protective property of §10).
12. Full QA atom + `multi_model_qa` regression suite.

---

## Files Touched

| File                                                                                                                                                                                                               | Change                                                                                                                                  |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| `modules/content/atoms/qa_aggregate.py`                                                                                                                                                                            | Flag-and-continue terminal path behind `qa_flag_instead_of_reject`; emit `qa_flagged` / `qa_flag_reasons` / `qa_flagged_surfaced` audit |
| `modules/content/atoms/_qa_rail_common.py`                                                                                                                                                                         | Broaden `is_rescuable_reject` (regen-eligibility truth table)                                                                           |
| `modules/content/auto_publish_gate.py`                                                                                                                                                                             | `would_fire=False` (reason `qa_flagged`) when the post is flagged — auto-path only                                                      |
| `modules/content/atoms/content_persist_task.py`                                                                                                                                                                    | Write `qa_flagged` to `pipeline_tasks` in the `awaiting_approval` write                                                                 |
| `modules/content/content_validator.py`                                                                                                                                                                             | Internal-term allowlist from `content_validator_internal_terms_allowlist`                                                               |
| `services/template_runner.py`                                                                                                                                                                                      | Add `qa_flagged: bool` to `PipelineState`                                                                                               |
| `services/tasks_mcp.py` (+ task-detail read helper)                                                                                                                                                                | Add `qa_flagged` to `list_tasks` SELECT; new `qa_feedback` detail read                                                                  |
| `poindexter/cli/pipeline.py`                                                                                                                                                                                       | Render `⚑` flag marker + `poindexter pipeline qa <task>` findings view                                                                  |
| `mcp-server/...list_tasks` adapter                                                                                                                                                                                 | Mirror `qa_flagged`; detail tool exposes findings                                                                                       |
| `services/settings_defaults.py`                                                                                                                                                                                    | `qa_flag_instead_of_reject='false'`; `content_validator_internal_terms_allowlist=<seed>`; bump `qa_rewrite_max_attempts` to `'2'`       |
| `services/migrations/YYYYMMDD_*_add_pipeline_tasks_qa_flagged.py`                                                                                                                                                  | **New** — add column + extend `pipeline_tasks_view`                                                                                     |
| `services/migrations/YYYYMMDD_*_demote_deepeval_faithfulness_to_advisory.py`                                                                                                                                       | **New** — prod `qa_gates` mutation                                                                                                      |
| `services/migrations/0000_baseline.seeds.sql`                                                                                                                                                                      | Seed `deepeval_faithfulness` advisory for fresh DBs                                                                                     |
| `infrastructure/grafana/...`                                                                                                                                                                                       | Flag-rate panel                                                                                                                         |
| `tests/unit/.../test_qa_aggregate_atom.py`, `test_qa_rail_common.py`, `test_content_evaluate_auto_publish*.py`, `test_content_validator*.py`, CLI/MCP surface tests, `tests/integration/test_graphdef_pipeline.py` | Per the testing plan                                                                                                                    |

---

## Out of Scope

- Topic-pool culling (the `stage=pending` rejections) — different system.
- Per-niche `qa_rewrite_max_attempts` / per-niche flag policy (single global setting;
  per-niche override deferred).
- A bespoke operator review UI — findings surface via existing CLI/MCP.
- Re-pointing `vision_gate` / `url_verifier` vetoes into a media/link regen (they
  surface-direct in v1; a media-regen cycle is the preview_gate workstream).
