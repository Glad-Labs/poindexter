# Console Task-Trace — design spec

- **Date:** 2026-07-07
- **Status:** Draft (awaiting operator review)
- **Author:** Claude (brainstormed with Matt)
- **Surface:** Operator console (`src/cofounder_agent/console/`) — Pro-tier overlay, stripped from the public mirror. Backend read routes are OSS (generic pipeline observability).
- **Prototype:** `src/cofounder_agent/console/prototype-trace.html` (throwaway, mock data, real console CSS) — delete before merge; real components land in `console/js/trace.jsx`.
- **Related:** `project_console_primary_ui` (A–E reliability program, complete), `console-api-wire-contract-mock-first`, `reference_console_deploy_live`, `project_prefect_concurrency_zombie_stall`, `project_video_pipeline_workstream`.

---

## 1. Problem

The console (now Matt's primary operating UI) shows the pipeline's state scattered across a dozen panels and six-plus DB tables. There is no single place to answer, for one task: **what is it doing right now, what corpus did it use, what did each graph node produce, and what decisions were made.** That story currently lives fragmented across `atom_runs`, `pipeline_versions.stage_data`, the `qa_rail_reviews` channel, `pipeline_gate_history`, `audit_log`, `cost_logs`, and the LangGraph checkpoints — and reconstructing it means cross-querying by hand.

This spec adds a **Task Trace**: a live front-door board plus a full-bleed per-task deep-dive that assembles the whole story in one place, and — critically — captures enough per-node data during the run that a killed/stalled task still leaves a legible trail.

## 2. Goals

1. **Front-door situational awareness** — a live board answering "what is the system doing right now" at a glance, with drill-in.
2. **End-to-end per-task drill-down** — node-by-node execution, the corpus/RAG handed to the writer, each node's output, and every QA/gate decision, in one full-bleed page.
3. **Forensics-first** — "why did this reject/stall?" is a headline path, not a buried mode. Killed runs leave a partial trace.
4. **Actionable** — approve/reject/publish/cancel from inside the trace; deeplink from alerts and posts.
5. **Escape hatch** — per-run and per-node deeplinks to the Langfuse LLM waterfall for anything deeper than the curated view.
6. **Trustworthy** — honest-empty everywhere; never fabricate a spine or a number the capture didn't produce.

## 3. Non-goals

- **No LangGraph checkpoint-blob mining.** Full raw per-node state-diff is explicitly out; Langfuse is the "go deeper" path. (Revisit only if the curated preview proves insufficient.)
- **No new pipeline behavior.** This is observation + existing actions surfaced in a new place. The only pipeline-code change is _when/what_ `atom_runs` captures (§5.2), which is best-effort and must never affect generation.
- **v1 is not multi-run comparison, RAG chunk-level capture, board search/filter, replay/re-run, or export** — see §11 (future).

## 4. User-facing design

Two surfaces, following the existing console grammar: a masonry **section** for the board and a full-bleed **`mode`** for the deep-dive (mirrors `feed`/`map`/`settings`).

### 4.1 Front-door live board — new `Trace` rail section (`sec-trace`)

- **Health strip** (last-24h KPIs): runs, pass-rate, avg quality, avg cost, avg wall, rescued count. Aggregates are **server-computed and cached** (≤60s), not recomputed per board poll (§9).
- **Active runs** — one card per running/pending task: topic, `node X/N · <current atom>`, live progress bar, elapsed, cost-so-far, template chip, status. Cards live-update via `usePolledResource` (30s). Reflects real concurrency (content pool = 1), so typically 1 running + a pending queue — the board shows both.
- **Recently finished** — compact cards: ✓ approved / awaiting_approval (q-score) or ✕ rejected/failed (reason), wall time.
- Click any card → deep-dive.

### 4.2 Deep-dive — full-bleed `mode='trace'`

**Header:** back control · topic · template chip · status · request summary stats (wall / cost / quality / gate, aggregated across the request's runs) · **inline actions** (Approve / Reject / Publish for `awaiting_approval`; Cancel for a running run; reused from the existing task API) · **run/DAG switcher** when the request has >1 run — retries or composed DAGs (§5.4) · whole-run Langfuse deeplink.

**Master–detail body:**

- **Left spine** — every node in execution (`seq`) order, grouped by block (Writer / Image / QA rails / QA rescue / SEO+media / Finalize). Each row: status glyph (✓ ◐ ○ ✕, colorblind-safe) · seq · node id · atom · latency (+cost) · a **timing bar** (width ∝ duration relative to the max node in _this_ run — no history needed, so v1). The row also carries an **anomaly-flag slot** ("2.3× median") that stays dormant until the cross-run baseline read lands (fast-follow, §11 #10) — the flag needs a historical per-atom median, which the timing bar does not. The QA-rescue loop renders as its own group with `↻` markers, so a rewrite-and-re-pass is visible as structure.
- **Right pane, two modes:**
  - **Run overview** (default): **corpus** handed to the writer (name→URL from `research_context`); **decision log** (chronological "what the pipeline decided" — originality, gate skips, defer→rewrite→approve, auto-publish verdict); **QA decision summary** (per-rail score + advisory/gate + the aggregate + rescue outcome); **cost & model rollup** (top cost drivers, cloud vs local, model mix, energy); **draft evolution** (before/after diff of what the rescue rewrite changed — fast-follow, §11); **generated images**; **final post** preview + open-preview link.
  - **Node detail** (on selecting a node): execution facts (status/verdict/latency/model+tier/cost/retries); for QA nodes a score meter + the reviewer's **reasoning**; **output preview** (the bounded snapshot of changed output keys); state I/O keys (in → out); a **per-step Langfuse** deeplink.

**Forensics behavior:** for a halted/rejected run, the deep-dive opens **pre-selected on the halting node** with the veto reason surfaced (§8).

Prototype screenshots (mock data, real CSS) are the visual contract: board+overview, node-detail, and the enriched sections (health strip, actions, timing bars, decision log, cost rollup, draft diff, images).

## 5. Data model & capture

### 5.1 Source mapping (mostly read-only)

| Trace element                                                      | Source                                                                                                                             | New work                        |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| Board: live progress (current node, %, message, last-progress age) | `pipeline_tasks.stage` / `percentage` / `message` / `last_progress_at` / `status` (updated live by the `on_event` progress stream) | read-only                       |
| Board: 24h health KPIs                                             | aggregate over `pipeline_tasks` (+ `atom_runs` for cost/rescue)                                                                    | new cached read                 |
| Spine (order/status/timing/model/cost/retries/io-keys)             | `atom_runs`                                                                                                                        | **incremental persist** (§5.2)  |
| Per-node output preview                                            | `atom_runs.output_preview`                                                                                                         | **new column + capture** (§5.2) |
| Corpus / RAG bullets                                               | `pipeline_versions.stage_data.task_metadata.research_context`                                                                      | read-only                       |
| Decision log                                                       | derived from meaningful nodes: `atom_runs` (gate/skip/halt) + `stage_data` (originality, image style, citation repair) + gate rows | read-only (assembly)            |
| QA verdicts + reasoning                                            | `qa_rail_reviews` channel → persisted `qa_reviews` / `qa_feedback` + `audit_log` (`qa_pass_completed`)                             | read-only                       |
| Gate / HITL decisions                                              | `pipeline_gate_history` + `pipeline_tasks.awaiting_gate`/`gate_artifact`                                                           | read-only                       |
| Cost & model rollup                                                | `cost_logs` + `atom_runs.cost`/`model`/`tier`                                                                                      | read-only                       |
| Generated images                                                   | `stage_data` image markers + `posts.featured_image_*` / `featured_image_data` (seed/prompt)                                        | read-only                       |
| Final post                                                         | `pipeline_versions.content` / `posts`                                                                                              | read-only                       |
| Langfuse (run + step)                                              | existing `GET /api/traces?task_id=` (Langfuse `sessionId`) + `plugins/tracing.py` `{public_url}/trace/{trace_id}`                  | reuse                           |

### 5.2 Capture change — incremental `atom_runs` + `output_preview` (the load-bearing item)

**Today:** `persist_atom_runs` runs **once, batch, at end-of-run** (`template_runner.py:1460`) from an in-memory `record_sink`. So a _running_ task has zero `atom_runs` rows, and a _killed_ task has zero **permanently** — the recent CANCELLING-zombie stalls left no trace of how far they got.

**Change:** persist each `TemplateRunRecord` **as its node completes** (best-effort, swallowed on error — same guarantee as today), instead of one terminal batch. The end-of-run `record_atom_run_outcome` backfill (post_id / decision / edit_distance after the approval gate) stays as-is.

- **Where:** the record is already appended to `record_sink` during node execution in `pipeline_architect.py` (~L1107/L1142). Add a per-record persist hook there (or an `on_record` callback threaded from `template_runner`), keyed on `run_id`+`seq`, upsert-safe so a re-persist at end-of-run is idempotent.
- **New column:** `atom_runs.output_preview text` — a **bounded** (≤ `atom_runs_output_preview_max_bytes`, default 2048) truncated snapshot of the **changed `output_keys`** values, captured at the same per-node persist. Truncation guards against dumping a full draft into every row; capture is observational only.
- **Payoffs:** (1) live deep-dive spine; (2) **partial trace for killed/zombie/crashed runs** (forensics); (3) `output_preview` populated on the same write path.
- **Cost/safety:** ~N small writes/run (N≈40) at content-pool concurrency 1 — negligible; best-effort so a DB hiccup never touches generation; gated by the existing `atom_runs_capture_enabled`.

### 5.3 Forward-only caveat

`output_preview` and incremental capture are **new** — existing runs (the 279 posts, all historical tasks) will not have per-node previews and may lack full spines. Historical deep-dives **degrade honestly**: spine from whatever `atom_runs` exists + `stage_data`/QA that was already persisted; the pane shows a "captured since <ship-date> — this run predates full capture" note rather than a fabricated blank.

### 5.4 One request → 1..N runs (retries and composed DAGs)

`atom_runs` keys on **(task_id, run_id, template_slug)**. One logical request (`task_id`) can therefore own **multiple runs**, in two flavours the schema already distinguishes by `template_slug`:

- **Retries** — more `run_id`s of the _same_ `template_slug` (a reclaimed `canonical_blog` run killed at node 22, then re-run). The later run supersedes.
- **Composed DAGs** — `run_id`s of a _different_ `template_slug` under the same `task_id`. This is how media works today: `dispatch_media_pipeline` runs `media_pipeline` / `podcast_pipeline` under the _source_ task's `task_id` but a distinct `thread_id` (`media-<task_id>`) so the checkpoint never collides with the `canonical_blog` run. They are _complementary_, not superseding.

**Design principle — composition-agnostic.** The trace's unit is the **request**, which holds `1..N` runs of _arbitrary_ templates; it is never "the canonical_blog graph." This is deliberate: the roadmap is autonomous arbitrary composition (Matt — "create an image of a watercooled PC", "make a Q&A-format podcast", "write just a blog post and a short-form video"), where one request may be 1, 2, 3, or more graphs. Because that maps directly onto the existing `(task_id, run_id, template_slug)` shape, the trace supports it with **zero redesign** — it groups runs under the request and renders one spine per run. Design every read + component around "a request has runs", not "a task is a blog graph."

**UI:** the deep-dive header carries a **run/DAG switcher** grouped by template (`canonical_blog` [run 2 · run 1 killed] · `media_pipeline` · `podcast_pipeline`); the summary stats aggregate across all runs of the request; selecting a run swaps the spine + overview. Defaults to the primary content run (latest `canonical_blog`), or the sole run when there's one. `GET /api/trace/{task_id}` returns all runs grouped by template; `?run_id=` picks one.

### 5.5 dev_diary / legacy-path caveat

`atom_runs` capture is **graph_def-only** (the composed `build_graph_from_spec` path). `dev_diary` (5-node legacy `TEMPLATES` factory) writes no `atom_runs`. Its board card shows live progress (from `pipeline_tasks`), and its deep-dive shows a **coarse** progress view + whatever `stage_data`/final-post exists — not a node spine. The board must not imply a spine it can't render for legacy-path tasks.

## 6. Backend

New service `services/trace_read.py` (no inline SQL in routes — adapter-contract; the service holds the queries). New router `routes/trace_routes.py`.

**Endpoints (OAuth-JWT, like the rest):**

- `GET /api/trace/active` → `{ runs: [ {task_id, topic, template_slug, status, stage, current_node, seq, total, percentage, elapsed_s, cost_so_far, quality?, gate?} ], recent: [...] }` — running + pending + recently-finished. Guarded → `{runs:[],recent:[]}` on error.
- `GET /api/trace/summary` → 24h health KPIs, **server-cached ≤60s**.
- `GET /api/trace/{task_id}?run_id=` → the deep-dive assembly: `{ request:{task_id, topic, ...aggregate stats}, runs:[{run_id, template_slug, kind:"content"|"media"|"retry", status, node_count, halted_at?}], selected_run, nodes:[...], corpus, decisions, qa_summary, cost_rollup, images, final_post, halt?, langfuse:{run_url} }`. `runs` is gathered by the `runs_for_request(task_id)` seam (R7) and grouped by template. Guarded; honest-empty per §5.3/§5.5.
- **Reuse** `GET /api/traces?task_id=` for the Langfuse trace list/deeplinks.

**Migration:** `atom_runs.output_preview text` via a new timestamped migration (schema DDL). New setting `atom_runs_output_preview_max_bytes` (default `2048`) → `settings_defaults.py`, **not** the migration (seed discipline).

**Census guards (handle in the same commit — these have bitten sub-projects C/E):**

- New `services/trace_read.py` → run the `regen-services-doc` script + commit `docs/reference/services.md`.
- New worker routes → bump `test_worker_manifest_has_expected_routes` count + add its docstring history line, and add the routes to `_WORKER_ROUTES`.

## 7. Frontend

No-build React (in-browser Babel). Reuse existing seams; one global lexical scope (do **not** re-`const {useState}=React` — primitives own it).

- **`PX.api`** (`js/api.js`): `traceActive()`, `traceSummary()`, `traceDetail(taskId, runId?)`; reuse `traces(task_id)`. All guarded → honest-empty, never throw.
- **`js/trace.jsx`** (new): `TraceBoard` (+ health strip), `TraceDeepDive` (header + actions + run/DAG switcher), `TraceSpine` (grouped rows + timing bars + anomaly flags), `RunOverview` (corpus / decision log / QA summary / cost rollup / draft diff / images / final post), `NodeDetail`. Reuse `Icon`/`StatusText`/`Meter`/`Freshness` and the `PX.ts`/`charts.jsx` primitives where useful. Colorblind-safe: glyph + number carry signal, color reinforces.
- **`app.jsx`**: add `{id:'trace', icon:'pulse', label:'Trace'}` to `RAIL`; render `<TraceBoard>` in a new `sec-trace`; add `mode==='trace'` full-bleed branch driven by a `traceTaskId` state; a board card sets `traceTaskId` + `mode='trace'`; back → `mode='console'`. Deep-dive polls while the selected run is live (via `usePolledResource`), stops when terminal.
- **Contract-net rows** (`js/__tests__/contracts/`): tier-1/2/3 rows for `/api/trace/active`, `/api/trace/summary`, `/api/trace/{id}`; record real fixtures.

## 8. Forensics, alerts, cross-links (v1)

- **Open-on-halt:** deep-dive of a rejected/failed/halted run pre-selects the halting node and surfaces the veto reason (from `qa.aggregate` / `error_message` / gate rows) in the header.
- **Alert → trace deeplink:** brain/firefighter operator notifications about a stuck/failed task include a console deeplink to that task's trace (`/console/#trace/<task_id>` or the console's routing equivalent). Closes alert→diagnosis from the phone. (Requires the console to accept a task_id in its hash/route on load.)
- **Post/task ↔ trace:** a "view trace" affordance from existing task/post surfaces via `posts.metadata->>'pipeline_task_id'`, and back from the trace to the post/preview.

## 9. Edge cases / honest-empty states

- **Running run:** spine fills incrementally; current node marked `◐ run`; unreached nodes `○` pending.
- **Pending/queued:** card shows queued; deep-dive shows "not started."
- **Killed/zombie:** partial spine up to the last persisted node; header shows failed/cancelled + reason; no fabricated remainder.
- **Capture disabled** (`atom_runs_capture_enabled=false`): board still works (pipeline_tasks); deep-dive shows "per-node capture disabled" note.
- **Historical (pre-ship):** §5.3 note.
- **dev_diary/legacy:** §5.5 coarse view.
- **Health strip:** cached; shows "—" honestly if the aggregate read fails (never zeros).

## 10. Testing

- **Console Node tests:** `trace.jsx` render/logic (spine grouping, glyph/status mapping, timing-bar math, run-selector, honest-empty branches); `PX.api` guard tests (unreachable → `{}`/`[]`).
- **Contract net:** rows for the three new endpoints; recorded fixtures; drift-nightly picks up shape changes.
- **Backend (db_pool):** `trace_read` assembly (multi-run selection, halt detection, honest-empty on missing capture); incremental-persist idempotency (per-node persist + end-of-run re-persist = no dup rows); `output_preview` truncation bound.
- **Census guards:** services-doc regen committed; worker-manifest route-count test bumped.
- **Manual/browser:** Playwright verify against the real console (per `reference_console_deploy_live` — ThreadingHTTPServer no-cache; Playwright is the working verifier).

## 11. Scope

**v1 (this spec):**

1. Front-door board (live) + health strip.
2. Deep-dive master–detail: spine (timing bars + rescue loop; anomaly-flag slot wired but dormant until #10), node detail (exec/score/reasoning/output-preview/io-keys/Langfuse), run overview (corpus / decision log / QA summary / cost+model rollup / images / final post).
3. **Incremental `atom_runs` persist + `output_preview` column** (§5.2).
4. Run/DAG switcher — retries + composed DAGs, grouped by template via the `runs_for_request` seam (§5.4, R7); honest-empty states (§9).
5. Inline actions (approve/reject/publish/cancel) + open-on-halt forensics (§8).
6. Alert→trace deeplink + post↔trace cross-links (§8).
7. Per-run and per-node Langfuse deeplinks (reuse).

**Fast-follow:** 8. **Draft-evolution diff** — semantic diff of two stored `pipeline_versions` (the one genuinely non-trivial UI piece; prototype shows the target). 9. **Mobile deep-dive** — the 430px master-detail needs a stacked/collapsible treatment (console isn't phone-first yet; board cards already stack). 10. **Anomaly baselines** — the "2.3× median" flag needs a historical per-atom median (cheap query over `atom_runs`; ship the flag once the baseline read exists).

**Future (explicitly held back so v1 stays focused):**

- Compare two runs side-by-side (champion vs challenger — pairs with the model-eval loop).
- RAG chunk-level retrieval detail (query + top-k + similarity) — needs **new capture** of the retrieval step.
- Board search/filter (by template/niche/status/model) — earns its keep as volume grows.
- Replay / re-run from a node.
- Export trace as markdown/JSON.

## 12. Rollout / flags / mirror posture

- **Flags (DB-first):** reuse `atom_runs_capture_enabled` (master switch for capture); new `atom_runs_output_preview_max_bytes` (default 2048); board poll cadence as a setting. No env vars.
- **Deploy:** console goes live on the next deploy-checkout `git pull` (static, no restart). Backend routes + migration + capture change ship with a worker rebuild/restart (per `feedback_rebuild_authority`).
- **Mirror:** the console overlay is stripped from the public mirror (whole `console/` tree) — new `trace.jsx` rides that. The backend `trace_read`/`trace_routes` + migration are **OSS** (generic pipeline observability — good for the product); confirm they carry no operator-only content. Audit both axes per `project_mirror_safety_guard_coverage`.

## 13. Open questions / risks

- **R1 — incremental-persist mechanism.** Persist-on-append inside `pipeline_architect` vs an `on_record` callback threaded from `template_runner`. Decide during planning; must stay best-effort and idempotent with the end-of-run outcome backfill. _(Highest-risk item; everything live depends on it.)_
- **R2 — decision-log assembly.** The "what the pipeline decided" list is derived, not a single table. Define the exact set of contributing signals (gates, originality, style pick, citation repair, rescue, auto-publish) so it's deterministic, not ad-hoc.
- **R3 — media/podcast/video composition (RESOLVED — see §5.4).** Media DAGs run under the **same `task_id`** as the source content, distinguished by `template_slug` + a `media-<task_id>` `thread_id` (`dispatch_media_pipeline`). They are additional **runs** of the request, surfaced via the run/DAG switcher — first-class in the deep-dive, and on the board as the currently-running DAG for that task. Generalises to the arbitrary-composition roadmap. Media dispatch is currently default-OFF (`media_pipeline_trigger_enabled=false`); the trace supports it for when it flips on. **New open thread → R7.**
- **R4 — console hash/routing** for the alert deeplink — the SPA must accept `#trace/<task_id>` on load and open the deep-dive. Small addition to the app shell.
- **R5 — board live-progress granularity.** The board's "node X/N · current atom" assumes `pipeline_tasks.stage`/`percentage`/`last_progress_at` are updated at per-node (or at least per-block) granularity by the `on_event` stream. If those fields turn out to move only coarsely, "node X/N" becomes block-level, and the live board's current-node precision drops until incremental `atom_runs` (§5.2) backfills it. Confirm the update granularity during planning.
- **R6 — "recently finished" window.** Undefined here (last N vs last few hours). Pick a cheap default during planning (e.g. last 10 terminal runs) and make it a setting.
- **R7 — request grouping key for arbitrary composition.** Today all runs of a request share one `task_id`, so grouping is a `task_id` query. As composition becomes ad-hoc and user-initiated ("blog + short video" from one prompt), the system may add a first-class request/parent id; if so, the trace's grouping key becomes that id, not `task_id`. Isolate "gather the runs of a request" behind one seam (`trace_read.runs_for_request(task_id)`) so a future key swap is a one-place change. Not a v1 blocker — v1 groups by `task_id`.
