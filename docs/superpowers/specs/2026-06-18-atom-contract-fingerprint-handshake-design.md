# Atom contract fingerprinting + graph_def version handshake

- **Issue:** Glad-Labs/poindexter#755 — _arch(pipeline): stored graph_defs don't pin atom versions — I/O contract drift and checkpoint poisoning across graph changes go undetected_
- **Date:** 2026-06-18
- **Status:** Implemented
- **Repo:** `glad-labs-stack` (source of truth); mirrors to `poindexter`.

## Problem

Stored `graph_def` rows in `pipeline_templates` reference atoms **by name only**
(`{"id": ..., "atom": "qa.programmatic", "config": {}}`). Nothing records which
version of the atom's I/O contract the graph was validated against, and nothing
re-checks it at run time:

- `_validate_spec` (`services/pipeline_architect.py:360`) runs **only inside
  `compose()`** (the architect-LLM path, line 300). The run-time path
  (`TemplateRunner.run` → `load_active_graph_def` → `build_graph_from_spec`)
  explicitly **"trusts the spec"** (`pipeline_architect.py:686`) and raises only
  when an atom is _missing_. An atom whose `requires`/`produces` contract changed
  while keeping its name runs **silently against the new contract**.
- `AtomMeta.version` exists (`plugins/atom.py:109`) but nothing pins or checks it
  at load. Empirically it is near-dead as a signal: **47 of 51 atoms sit at
  `1.0.0`; only 2 were ever bumped to `2.0.0`** — a version-only check would
  almost never fire.
- The LangGraph checkpointer (`checkpoints` table, keyed by bare `task_id`)
  stores **no record of which graph produced a checkpoint**. A run killed
  mid-graph leaves a checkpoint that a later run of the same `task_id` resumes —
  even after the graph definition changed underneath it (the checkpoint-poisoning
  class; see `reference_langgraph_checkpoint_poisoning`).

This is the same failure family as the `PipelineState` silent-drop incidents,
one level up: **data (the stored graph) references code (the atoms) with no
version handshake**, and the graph-as-data seam is the centerpiece the architect
vision depends on.

## Decision

Detect drift with a **contract fingerprint** (a hash of each atom's structural
I/O contract), not the hand-maintained `version` field. The fingerprint
auto-detects real I/O drift even when nobody bumps `version`; the `version`
string is recorded alongside for human readability but is **not** gated on.

Rejected alternatives: _version-only + CI bump-enforcement_ (relies on a
discipline the history shows isn't kept); _combined version-OR-hash gate_ (more
to reason about with no extra protection, since the hash already covers the cases
that matter).

## Design

### 1. Fingerprint primitive — `plugins/atom.py`

`AtomMeta.contract_fingerprint() -> str`, pure:

- Serialize **only the structural I/O contract** in a stable form:
  `requires` (sorted), `produces` (sorted), and each `inputs`/`outputs`
  `FieldSpec` reduced to `(name, type, required)`.
- **Deliberately excluded:** `description`, `cost_class`, `capability_tier`,
  `retry`, `side_effects`, `fallback`, `parallelizable`, `idempotent` — none
  affect whether a graph's wiring is still correct, so changing them must NOT
  trip the gate.
- `sha256` of the canonical JSON; keep the first 12 hex chars (short, greppable).

Works uniformly on real atoms and the synthesized stage-atoms (the legacy 12
stages surfaced through `AtomMeta` in `atom_registry.py`), because it operates on
`AtomMeta` regardless of origin.

### 2. Stamping (write time)

`stamp_graph_def(spec: dict) -> dict` writes two keys onto every node:

- `_contract_fp` — the atom's current `contract_fingerprint()` (the gate input).
- `_atom_version` — the atom's `version` string (readability only).

Wired at both graph_def write paths:

- **Architect path:** `cache_template()` (`pipeline_architect.py:1096`) stamps
  before the INSERT.
- **Static-spec path:** a shared `upsert_graph_def(pool, spec)` seeder helper
  stamps before write, for future seeders to use. To establish the baseline for
  the rows already in production, ship **one migration** that re-stamps the
  currently-active graph_defs (`canonical_blog`, `media_pipeline`,
  `podcast_pipeline`, `seo_refresh`) by recomputing fingerprints from the live
  registry. (`dev_diary` has no graph_def row — it runs from the legacy
  `TEMPLATES` factory — so it is unaffected.)

Stamping at seed time freezes "the contract this graph was authored against";
re-deriving at load time and comparing is the handshake.

### 3. Load-time gate — fail loud + notify

`assert_graph_def_current(spec) -> None`, raising a new `GraphContractError`,
called in `TemplateRunner.run` **immediately after `load_active_graph_def`
returns a spec and before `build_graph_from_spec`**. For each node it re-derives
the atom's current `contract_fingerprint()` and compares to the stored
`_contract_fp`. On any mismatch it raises with a `FIX:`-style message naming the
drifted atom(s), old vs current fingerprint, and the re-seed remediation command,
and calls `notify_operator()`.

- **Why at the runner, not inside `load_active_graph_def`:** that loader is
  intentionally best-effort and degrades to `None` on error (the runner then
  falls back to the legacy factory). Letting drift degrade to `None` would
  silently fall back to the **deleted** `canonical_blog` factory → `KeyError`,
  i.e. a silent failure. The gate must fail loud per `feedback_no_silent_defaults`,
  so it lives at the run seam where operator-notify already fires.
- **Unstamped specs:** a node with no `_contract_fp` (e.g. a graph_def seeded
  before this lands and not yet re-stamped) is treated as a drift/`FIX:` with a
  "re-seed to stamp" message rather than silently passing — the baseline
  migration removes this for the active rows on first deploy.
- **Trade-off:** an atom contract change halts that pipeline until a re-seed.
  This is the intended fail-loud behavior; the remediation is one re-seed and is
  named in the error.
- **Reseed un-stamping → boot-time self-heal (follow-up, poindexter#755):** the
  baseline restamp migration only fixes rows extant at its time. graph_def reseed
  migrations write the raw spec via `json.dumps(CANONICAL_BLOG_GRAPH_DEF)`
  with **no** `stamp_graph_def` call — deliberately, so they stay importable in
  the migrations-smoke env (no atom registry). Each reseed therefore re-un-stamps
  the active row, and the next boot halts every run with `GraphContractError`
  (bit twice: the v6 director reseed and the preview_gate reseed; hotfixed by
  one-shot restamp migrations that only move the problem to the next reseed). The
  durable fix is `StartupManager._ensure_active_graph_defs_stamped` →
  `pipeline_architect.ensure_active_graph_defs_stamped`, run on every boot right
  after migrations: it baseline-stamps **only** rows that are fully unstamped
  (`is_graph_def_fully_unstamped`), leaving any row that carries a fingerprint
  untouched so this gate still catches genuine drift. Self-healing regardless of
  how a row got written, so a future reseed can keep emitting raw specs.
- **Missing reseed → seeded-parity CI gate (follow-up, #2250 / #2261):** the boot
  self-heal makes a _reseed_ safe, but the inverse still bit prod on 2026-07-10 —
  a contract change with **no** reseed migration. `content.plan_image_markers`
  gained a `featured_image_subject` output (fp `5f20eda72266` → `1bad0eccc418`);
  PR #2250 regenerated the committed fingerprint snapshot — silencing the PR-time
  gate `assert_specs_match_contract_fingerprints` — but shipped no reseed, so the
  baseline's **frozen** stamp stayed stale (the self-heal only touches
  fully-unstamped rows, so it leaves a stamped-but-stale row alone) and the
  load-time gate halted every `canonical_blog` run. Nothing exercised the
  seeded-then-migrated row in CI. The durable fix is
  `assert_seeded_graph_defs_current`
  (`tests/integration_db/test_seeded_graph_defs_current.py`): apply baseline +
  every migration on a fresh DB, then run the load-time gate against each active
  `pipeline_templates` row. Fully-unstamped rows are skipped (the boot self-heal
  owns them — they can't be stale on a fresh DB); a **stamped** row whose atom
  drifted without a reseed trips the gate at PR time, exactly the #2250 miss.
  Lives in integration_db, not migrations-smoke, because the gate needs the full
  atom registry to derive current fingerprints.

### 4. Checkpoint compatibility — discard on mismatch

Compute a **graph signature** = hash over all node `_contract_fp`s plus the edge
list. Thread it through the checkpointed state as a reserved
`__graph_signature__` key (so it rides in the partitioned `data_state` the
checkpointer serializes). On a resume attempt, fetch the latest checkpoint for
the thread, compare its `__graph_signature__` to the current graph's; on
mismatch, **delete the thread's `checkpoints` rows and start fresh**, logging
loudly.

This is the version-aware complement to the existing stale-sweep checkpoint
clearing (`sweep_stale_tasks`): it kills cross-graph-change poisoning even when
the checkpoint is not stale. Chosen over baking the signature into `thread_id`,
which would orphan checkpoint rows and complicate the `poindexter pipeline
resume` CLI (which keys by bare `task_id`).

### 5. Testing — per docs+tests default

- **Unit (`plugins/atom`):** fingerprint stability — identical contract → equal
  fp; changed `requires`/`produces`/field-type → different fp; **description-only
  change → equal fp** (the key negative case).
- **Unit (stamp/gate):** `stamp_graph_def` adds both keys to every node;
  `assert_graph_def_current` passes on a stamped-and-current spec, raises
  `GraphContractError` (with FIX text + atom name) on a drifted one, and treats a
  missing stamp as a FIX.
- **Unit (checkpoint):** signature equal → resume allowed; signature differs →
  thread checkpoints deleted and run starts fresh.
- **Integration-db:** seed a graph_def → load passes → monkeypatch an atom's
  `requires` → load now refuses with `GraphContractError`.
- **Integration-db (seeded parity, #2250):** apply baseline + every migration on a
  fresh DB → `assert_seeded_graph_defs_current` over every active
  `pipeline_templates` row raises nothing; drift one atom on a frozen-stamped row
  → it raises `GraphContractError` naming the stale template + atom.

## Files touched

| File                                                    | Change                                                                                                           |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `plugins/atom.py`                                       | `AtomMeta.contract_fingerprint()`                                                                                |
| `services/pipeline_architect.py`                        | `stamp_graph_def`, `upsert_graph_def`, `cache_template` stamps; `GraphContractError`, `assert_graph_def_current` |
| `services/template_runner.py`                           | call `assert_graph_def_current` post-load; graph-signature compute + checkpoint discard-on-mismatch              |
| `services/migrations/<ts>_restamp_active_graph_defs.py` | re-stamp the 4 active rows from the live registry                                                                |
| `tests/unit/...`, `tests/integration_db/...`            | the suites above                                                                                                 |

## Out of scope / non-goals

- Forcing or automating `AtomMeta.version` bumps (the fingerprint replaces the
  need; version stays advisory).
- The job-layer/effect-boundary idempotency keys — that is poindexter#757.
- Reworking `_validate_spec`'s reachability checks (orthogonal; unchanged).

## Operator runbook (on `GraphContractError`)

The error names the drifted atom(s) and prints the re-seed command. The operator
re-seeds the affected graph_def (re-running its seeder / the re-stamp path),
which recomputes fingerprints from the current registry; the task then re-runs
cleanly. A mismatched checkpoint is discarded automatically on the next run.
