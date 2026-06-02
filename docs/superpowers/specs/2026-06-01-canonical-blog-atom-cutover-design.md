# Canonical_blog atom cutover + granularity refactor

**Status:** Design locked · **Date:** 2026-06-01 · **Refs:** Glad-Labs/poindexter#355 (umbrella), #362 (atom granularity), #364 (architect activation — follow-up), #363/#361/#365 (follow-ups)

## Problem

The dynamic-pipeline-composition engine is **built but dormant**. `plugins/atom.py` (`AtomMeta`), `services/atom_registry.py` (discovery + `sync_to_db`, wired in `main.py` lifespan), `services/pipeline_architect.py` (`build_graph_from_spec`, `compose`), and the `pipeline_templates` DB table (with a `graph_def` JSONB column) all exist — but `compose()` / `build_graph_from_spec()` have **zero production callers**. The live `canonical_blog` pipeline is still a hand-coded `StateGraph` factory in the Python `TEMPLATES` registry (`services/pipeline_templates/__init__.py`). Two pipeline systems coexist; the new one runs nothing.

This spec **kills the coexistence** by making the composed path the real one for `canonical_blog`, and refactors the coarse stages into finer atoms in the same pass.

## Goal / End-state (success criteria)

1. `canonical_blog` runs as a **static `graph_def` spec** stored in `pipeline_templates.graph_def`, compiled by `build_graph_from_spec` — not the hand-coded factory.
2. The coarse stages (primarily `cross_model_qa`; `generate_content` if sub-steps warrant) are **split into finer atoms** with declared `AtomMeta` `requires`/`produces`.
3. **Spec-build validation** enforces `requires`/`produces` compatibility (fail loud at build/seed, not a runtime `KeyError`).
4. **Per-atom run + outcome data is persisted** (training / feedback substrate).
5. The hand-coded `canonical_blog` factory + its `TEMPLATES` entry (and any stages fully superseded by finer atoms) are **deleted**.
6. **Everything DB-configurable**: the `graph_def` spec, the cutover selection flag, gate enable flags, and the capture toggle all live in the DB (`pipeline_templates` / `app_settings`), flippable without a deploy.
7. The **static-spec catalog pattern** is established (dev_diary + future pipelines follow); **gates are parameterized atoms**.

## Architecture

- A **static pipeline** is a `graph_def` JSON object — `{"name", "entry", "nodes":[{"id","atom","config"}], "edges":[{"from","to"}]}` — stored in `pipeline_templates.graph_def`. This is the _same_ format `pipeline_architect` emits, so static and architect-composed pipelines share one format and one builder.
- **Runner selection (DB-driven):** for a given slug the runner loads the active `pipeline_templates` row; if it has a `graph_def`, build via `build_graph_from_spec(graph_def, pool=pool, record_sink=records)` → `ainvoke`. If not (transition / fallback), use the Python `TEMPLATES` factory. The cutover is gated by a DB setting (`app_settings.pipeline_use_graph_def`, default `false` until validated) plus the per-slug `active` graph_def row.
- **Atoms** resolve through the boot-wired registry; the `stage.<name>` shorthand bridges any stage not yet split into a finer atom (so the cutover is incremental within the big-bang).
- **Capability routing:** atoms declare a `capability_tier` (never a hardcoded model); the resolver is the existing cost-tier API (`services/llm_providers/dispatcher.resolve_tier_model`). `model_router.py` was deleted long ago — no rename needed (#360's rename scope is moot).

## Granularity refactor (#362 content)

Split coarse stages into finer atoms, each declaring `AtomMeta` (`requires`, `produces`, `capability_tier`, `idempotent`, `side_effects`, `retry`):

- **`cross_model_qa` → independent rail atoms** — `qa.deepeval`, `qa.guardrails`, `qa.ragas`, `qa.critic`, plus `qa.aggregate` (combine the per-rail verdicts into the gate decision). Each rail declares `requires=("draft_content", …)` and `produces=(<its score>)`; they are `parallelizable=True`. This makes each rail individually swappable, A/B-able, and skippable via the spec — directly retiring the per-mode bypasses that motivated #355.
- **`generate_content`** — evaluate during the plan whether to split into `writer.draft` (+ a RAG-retrieve atom) given `writer_self_review` is already a distinct stage. Split only if there are genuine independent sub-actions; do not over-fragment.
- Already-fine stages (`verify_task`, `url_validation`, `source_featured_image`, `finalize_task`, etc.) get thin atom wrappers or stay as `stage.*` nodes.

The **exact final atom list and per-atom I/O schemas are enumerated in the implementation plan** (after reading each coarse stage's internals); this spec fixes the _decomposition targets, the contract requirement, and the principle_ — one action per atom, declared I/O, recomposable by the architect.

### Granularity principle

The atom unit is **one meaningful, independently-composable action** — a step where a real compositional choice exists (swap / skip / reorder / gate it) or that is independently testable/reusable — **not** the smallest possible code unit. The `cross_model_qa` → per-rail split meets this bar (each rail is a genuine enable/disable/swap/A-B choice); cohesive steps that always co-occur in the same order are **not** split.

Finer granularity is **deferred, not foregone**. Once the framework + registry + `requires`/`produces` validation are in place, splitting a coarse atom into finer ones is a **local, validated** operation — write the finer atoms, repoint the `graph_def`, re-validate (the validator catches mis-wiring). It is not a one-way door. So we take the high-value splits now and refine opportunistically when a concrete compositional need appears (the architect or a new pipeline actually wanting to vary a sub-step), rather than front-loading speculative fragmentation that adds `AtomMeta`/schema/node/edge overhead for choices nothing yet makes.

## requires/produces validation

Extend the spec validator (`pipeline_architect._validate_spec`, shared by the architect's `compose` and a new static-spec seed/load path) so that, in addition to its existing atom-existence + DAG checks, it verifies **every node's `requires` keys are satisfied** by (a) an upstream node's `produces`, (b) the node's own `config` seed, or (c) the declared initial-state contract. On failure it raises with a `FIX:`-prefixed message. This is what makes "a new pipeline is a JSON file" _safe_, not merely easy — a mis-wired composition fails at build/seed time, not mid-run.

## Cutover mechanism (flag-gated, DB-config)

1. Author `canonical_blog`'s `graph_def` at the finer granularity; seed it into `pipeline_templates` (new `version`, `active=true`).
2. Runner prefers the `graph_def` path when `app_settings.pipeline_use_graph_def=true` and an active `graph_def` exists for the slug; otherwise the legacy Python factory runs (fallback during validation).
3. After validation, flip the default, then **delete** the hand-coded `canonical_blog` factory, its `TEMPLATES` entry, and stages fully superseded by finer atoms.

## Validation / rollout

No parity check is possible (the granularity refactor changes behavior), so validation is **quality-based**:

- **Quality canary:** enable the `graph_def` path for `canonical_blog` on a low-stakes niche (or N tasks). Human-review the output and compare `quality_score` to the legacy baseline.
- The **human-approval gate is the backstop** — posts do not auto-publish, so a regression is caught at review, never on the live site (consistent with the no-auto-publish rule).
- When quality holds across N runs, flip the default and delete the legacy path.

## Data capture (training / learning substrate)

The atom boundary is the cheapest-ever point to capture this and it cannot be retrofitted cheaply, so it ships in this spec (capture only — no models/learning):

- Persist `build_graph_from_spec`'s `record_sink` per run to a new DB table (`atom_runs`): `run_id`, `task_id`, `atom`, `node_id`, resolved `tier`/`model`, `latency_ms`, `cost`, `retries`, `status`, and input/output state-key digests.
- **Join to outcome:** `post_id`, approval decision (`approved`/`rejected`/`revised`), `quality_score`, and edit-distance (from the per-niche auto-publish edit-distance gate).

This `(composition → outcome)` dataset is the substrate for #361 (outcome→router feedback), cost projection, and a future architect that learns which compositions/atoms produce approved content. Langfuse already covers the _LLM-call_ level; this adds the _atom / composition / outcome_ level. DB-configurable enable flag.

## Gates

Gates are a **single parameterized atom**, `atoms.approval_gate` (already built), placed as nodes wherever a checkpoint belongs: `{"id":"preview_gate","atom":"atoms.approval_gate","config":{"gate_name":"preview"}}`. It `requires=("task_id","gate_name")`, `produces=("_halt","awaiting_gate","gate_artifact")`, `capability_tier=None`; on an open gate it pauses + notifies + returns `_halt=True`, and `build_graph_from_spec`'s halt-aware router short-circuits the graph. Per-gate enable is a DB flag (`pipeline_gate_<name>`).

- **Two distinct layers:** _in-pipeline_ gates (the `approval_gate` atom — topic/preview/final content review, inside the graph) vs. _post-level_ gates (`post_approval_gates` — media review before publish, after the post exists; wired 2026-06-01). Same conceptual role, different layers; they may converge under #363 but stay separate here.
- **Autonomous-safety principle:** a side-effecting / publish-terminal pipeline must be preceded by an approval gate (a convention enforced later via #531 earned-autonomy + #365 permission/sandbox). `canonical_blog` is already covered by `finalize_task → awaiting_approval` + the post-creation media gates.

## Self-authoring readiness (design-for, NOT built now)

The endgame (architect-LLM / self-sufficient program) is the system creating atoms and pipelines as needed. We don't build the generator now, but lock principles so it's a later drop-in, not a rewrite:

- A system-authored **pipeline** = an inserted, validated `graph_def` row — the _same_ path a human or the architect uses.
- A system-authored **atom** = a conforming, self-describing (`AtomMeta`), directory-discovered, I/O-validated, tier-routed file — registered with zero wiring.
- The generator itself, plus trust-gating of which self-authored capabilities may _run_ (especially side-effecting ones), are future work (#531, #365). The design must not fight them.

## Testing

- **Unit:** each new finer atom — I/O contract, `requires`/`produces`, retry, `capability_tier`.
- **Validation:** the extended validator rejects a spec with unmet `requires` (fail-loud test) and accepts a valid one.
- **Build:** `build_graph_from_spec(canonical_blog graph_def)` compiles; entry/edges/atoms all resolve.
- **Integration (`db_pool`):** seed the `canonical_blog` `graph_def`, run a seed task → reaches `awaiting_approval`; `record_sink` shows every atom fired; `atom_runs` rows persist and join to the outcome.
- **Regression:** the legacy Python-factory path still runs while `pipeline_use_graph_def=false`.

## Out of scope (follow-ups)

- **Architect-for-ad-hoc** (LLM composition for novel intents) — the second spec (#364 activation).
- **True LangGraph interrupts** (#363; needs the Postgres checkpointer with custom serializers) — the gate atom keeps its v1 re-queue + `_gate_already_cleared` workaround.
- **Outcome→router feedback _learning_** (#361) — this spec captures the data; the learner is later.
- **Ops atoms + permission/sandbox** (#365) — deliberately deferred. Ops atoms (Stripe/Resend/Discord/Sentry) are `side_effects=True` and unsafe to compose without governance, so #365 bundles them with a permission/sandbox layer (+ #531 earned-autonomy dials). The atom framework is proven here on the _safe_ content pipeline first; side-effecting atoms come later behind that layer. The design already accommodates them (`AtomMeta.side_effects` + the gate-before-side-effects principle), so deferring costs nothing structurally.
- **Earned-autonomy dials** (#531) — the trust mechanism gating which self-authored / side-effecting capabilities may actually run.

## Decisions locked

- **D1** — Cut over `canonical_blog` to atoms (not additive activation).
- **D2** — Static `graph_def` specs in the DB; architect reserved for ad-hoc (later spec).
- **D3** — Big-bang: granularity refactor _during_ the cutover (no parity check).
- **D4** — Quality-canary validation with the human-approval gate as backstop.
- **D5** — Per-atom run + outcome capture ships in this spec (capture only).
- **D6** — Everything DB-configurable (spec, cutover flag, gate flags, capture toggle).
- **D7** — Gates are a single parameterized atom, placed declaratively.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
