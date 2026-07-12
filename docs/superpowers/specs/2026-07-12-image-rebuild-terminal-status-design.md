# image_rebuild terminal status — a parameterized status-setter atom

**Date:** 2026-07-12
**Status:** Approved (design) → ready for implementation plan
**Area:** content pipeline (public / poindexter)
**Follows:** #2357 (the `image_rebuild` hydration fix, which let the graph run to
completion for the first time and exposed this)

---

## Problem

Every _successful_ `image_rebuild` run strands its own `pipeline_tasks` row in
`status='in_progress'` forever. The graph does its work correctly — the target
draft's images are rebuilt and persisted — but nothing sets a terminal status on
the **rebuild job row itself**, so:

1. **Infinite re-run loop.** `reclaim_stale_inprogress_tasks`
   (`TasksDatabase.sweep_stale_tasks`, default
   `content_flow_stale_inprogress_minutes=30`) resets the stranded `in_progress`
   row back to `pending`. The worker re-claims it and re-runs the entire rebuild
   (fresh GPU image generation, overwriting the draft again), which strands it
   again, looping until `retry_count` maxes out — at which point the sweep marks
   it `failed` **despite it having succeeded every time**.
2. **Spurious success-path side-effects (latent second bug).** Because the row is
   `in_progress` (not a recognized terminal) when the flow finishes,
   `services/post_pipeline_actions.py::run_post_pipeline_actions` runs its
   success-path steps against the utility job — a `task.completed` webhook, the
   auto-curator gate, the **auto-publish gate**, and an **"awaiting approval"
   operator ping** — none of which make sense for a job row that has no post.

**Confirmed live:** tasks `21e2cfae` and `b3f94260` both completed their graph
(`persist:ok`, draft `f9ed9931` updated with fresh images) yet sat at
`in_progress`; `21e2cfae` had to be manually marked terminal to stop the loop.

### The two-row model (the crux)

The confusing part is that a rebuild involves **two separate `pipeline_tasks`
rows**, and only one of them is broken:

|                       | The **draft** (`target_task_id`)                               | The **rebuild job** (`rebuild_task_id`)                     |
| --------------------- | -------------------------------------------------------------- | ----------------------------------------------------------- |
| What it is            | the post with bad images                                       | a throwaway "go regenerate images" work order               |
| Created by            | the original content pipeline                                  | `enqueue_image_rebuild` on `POST /{task_id}/rebuild-images` |
| Status                | `awaiting_approval`                                            | `in_progress` → **stuck**                                   |
| Has content?          | yes (`pipeline_versions`)                                      | no — pure plumbing                                          |
| Operator approves it? | yes, as normal                                                 | never — invisible utility run                               |
| Touched by the graph  | images swapped into its version row; **status left untouched** | runs the 7 atoms, then orphaned                             |

`content.persist_draft_images` writes the fresh images back to **the draft's**
version row and deliberately leaves the draft at `awaiting_approval` — that part
already works. This design fixes **only the rebuild job row**.

## Root cause

`image_rebuild`'s graph ends at `content.persist_draft_images → END`, and that
atom's contract explicitly does not touch task status. Unlike the two other
graph_def pipelines, it has no terminal node that finalizes its own job row:

- **`canonical_blog`** ends at `content.evaluate_auto_publish`, which re-asserts
  `awaiting_approval` (guarded).
- **`seo_refresh`** ends at `content.republish_post`, which flips its job row to
  `completed` (guarded, `WHERE status='in_progress'`).

### Why `seo_refresh` is safe today but `image_rebuild` is not

Both route through the same `content_generation_flow`, which calls
`run_post_pipeline_actions` after the graph. The difference is topology:

- `seo_refresh` has an **approval gate**. Its run is split across **two** flow
  invocations — the gate pauses (task → `awaiting_gate`), and the terminal
  `completed` write happens later on the operator **resume** path, which bypasses
  `post_pipeline_actions` entirely.
- `image_rebuild` has **no gate**. It runs start-to-finish in **one** flow
  invocation, so its terminal status flows straight into `post_pipeline_actions`
  in the same run.

So `image_rebuild` needs _both_ a terminal status **and** that status recognized
by the post-pipeline guard. `seo_refresh` got away without the guard change by
accident of topology.

## Key finding: `completed` already exists

The task framing assumed `completed` was not a valid status. It is — the DB CHECK
constraint (`pipeline_tasks_status_check`, `0000_baseline.schema.sql`) allows 15
statuses including `completed`, `awaiting_gate`, and `archived`. And
`seo_refresh`'s `content.republish_post` **already** terminates a utility job at
`completed`, with the rationale already written in that atom:

> `'completed'` is recognized-terminal and NOT in the claim set
> (`'pending'`/`'rejected_retry'`) or the stale-sweep set (`'in_progress'`); it
> implies no new publish (unlike `'published'`).

So this is _adopting an existing, under-integrated status_, not minting a new one.
No DB constraint change is required.

## Design decisions

### 1. Terminal status for the job row = `completed`

Not `awaiting_approval`: that is the **draft's** status (already correct and
untouched). The approval queue filters on `status='awaiting_approval'`, so a
_job_ row set to `awaiting_approval` would surface in the review queue as a
phantom item with nothing to approve. `completed` is terminal,
keeps the job out of every claim/sweep/approval query, and is honest ("the
rebuild work finished").

The job's terminal status is deliberately **independent of the target's status**.
Today the target is always `awaiting_approval` (the entry atom hard-requires it);
if `image_rebuild` is ever extended to published posts, the target keeps its own
status (`published` stays `published`, re-exported in place) and the job still
goes `completed`. `completed` is future-proof precisely because it does not
mirror where the target came from.

### 2. Mechanism = a parameterized `atoms.set_task_status` node (not hardcoded)

The status is set by a dedicated, **config-driven, general-purpose**
status-mutation atom rather than baked into a work atom. It is not a
terminal-only "finalizer" — it just transitions a task's status, so it can sit
anywhere in a graph (e.g. mid-graph to move a task into a bespoke state);
`image_rebuild` happens to place it last. The name `set_task_status` reflects
that generality (vs. `finalize_task`, which would wrongly imply terminal-only).
Rationale:

- The pipeline architect composes atoms into ad-hoc graphs, and the governing
  rule is _"the graph must be the whole truth of what runs."_ A status buried in
  a work atom is invisible to the architect and forces one hardcoded terminal on
  every graph that reuses that atom. A status declared in the node's `config` is
  data the architect (or a spec author) reads and chooses per graph — any graph
  can move a task to whatever status it needs, wherever it needs to.
- This mirrors the existing `atoms.approval_gate`, which already takes its
  `gate_name` from node config. The status-setter is its general-purpose sibling.
- It keeps `content.persist_draft_images` single-purpose (write images to the
  draft) — unchanged by this work.

Node `config` values seed into atom state and can satisfy an atom's `requires`
(`pipeline_architect.build_graph_from_spec`), so a graph that omits
`target_status` **fails loud at seed/build time** rather than silently defaulting
— consistent with `feedback_no_silent_defaults`.

## Components

### A. New atom — `atoms.set_task_status`

File: `src/cofounder_agent/modules/content/atoms/set_task_status.py` (physically
in the content atoms dir, like `approval_gate.py`, but registered under the
generic `atoms.*` namespace).

- **`AtomMeta`**
  - `name = "atoms.set_task_status"`, `version = "1.0.0"`
  - `requires = ("task_id", "target_status")` — `target_status` arrives from node
    `config`; missing it fails at seed time.
  - inputs also read (from config, optional): `allowed_from`
    (default `("in_progress",)`), `percentage`.
  - `produces = ()` — the atom's effect is the DB write; it deliberately surfaces
    nothing on pipeline state. A produced output key would both couple downstream
    nodes and imply terminal-only use. Anything that needs the new status reads
    it from the row (as `content.load_draft_for_image_rebuild` already does).
    `atom_runs` capture is unaffected (it records the run regardless of produces).
  - `side_effects = ("db_write",)`, `idempotent = True`,
    `retry = RetryPolicy(max_attempts=1)`.
- **`run(state)`**
  1. Read `task_id`, `target_status`, `allowed_from`, `percentage` from state.
     Fail loud if `task_id` / `target_status` missing.
  2. Validate `target_status` against the known status set for a clear,
     architect-facing error (better than a raw CHECK violation). A unit test
     pins this constant to the DB constraint so it cannot drift.
  3. One guarded write via the existing
     `database_service.update_task_status_guarded(task_id, target_status,
allowed_from, percentage=...)`.
  4. A `None` return (current status not in `allowed_from` — already terminal, or
     an unexpected mid-graph state) is a **benign no-op** by default, logged at
     debug — exactly like `content.evaluate_auto_publish`. Never fails the graph
     on a no-op. (A future mid-graph caller that needs a blocked transition to be
     fatal can opt into a `require_transition` config flag; not built now —
     YAGNI.)
  5. Return `{}` (see `produces = ()` above).
- It only ever writes **the running task's own** row (`state["task_id"]`); it
  never touches the target draft.

### B. Wire it into `image_rebuild`

`src/cofounder_agent/services/image_rebuild_spec.py` — `persist` is unchanged;
append a terminal node:

```
… → inject → persist → finalize → END
```

```python
{"id": "finalize", "atom": "atoms.set_task_status",
 "config": {"target_status": "completed", "percentage": 100}}
```

Edges: replace `persist → END` with `persist → finalize` and `finalize → END`.

### C. `post_pipeline_actions` guard

`services/post_pipeline_actions.py` — add `'completed'` to
`_DECIDED_NON_REJECTED_STATUSES`. This makes `run_post_pipeline_actions` skip the
success-path side-effects when the canonical status is `completed`, fixing the
spurious webhook/auto-publish/awaiting-approval-ping on the job. It is also
retroactively correct for any `seo_refresh` `completed` row and any future
utility graph.

### D. Shipping machinery (the graph_def re-seed trap, ref `#2263`)

Because this changes graph topology, all three surfaces must move together
(snapshot-only would leave prod's stored graph_def stale and halt the pipeline;
seed-only would leave existing prod un-migrated):

1. **CI snapshot** — regenerate
   `tests/unit/services/graph_def_contract_fingerprints.json`
   (`REGEN_GRAPH_DEF_FP=1 … test_graph_def_contract_freshness.py::test__regenerate_snapshot`)
   so it carries `atoms.set_task_status`'s fingerprint.
2. **Baseline seed** — edit the `image_rebuild` row in
   `services/migrations/0000_baseline.seeds.sql` to the new 8-node graph_def
   (fresh installs; the row upserts via `ON CONFLICT (slug) DO UPDATE`).
3. **Re-seed migration** — a new `YYYYMMDD_HHMMSS_reseed_image_rebuild_finalize.py`
   that raw-`UPDATE`s `pipeline_templates.graph_def WHERE slug='image_rebuild'
AND active=true` to the unstamped `json.dumps(IMAGE_REBUILD_GRAPH_DEF)`; the
   boot self-heal (`ensure_active_graph_defs_stamped`) re-stamps it same boot.
   Copy the pattern from `20260710_171718_reseed_canonical_blog_image_markers.py`.

## Data flow (finalize path)

```
worker claims image_rebuild job  → status = in_progress
  graph: load_draft → plan → generate → featured → gate → inject → persist
     persist: writes fresh images to the DRAFT's version row (draft stays awaiting_approval)
  finalize (atoms.set_task_status, config.target_status=completed):
     update_task_status_guarded(job_task_id, 'completed',
                                allowed_from=('in_progress',), percentage=100)
  graph → END
flow → run_post_pipeline_actions: re-reads canonical status = 'completed'
     → 'completed' ∈ _DECIDED_NON_REJECTED_STATUSES → skips success-path side-effects
stale-sweep: only touches status='in_progress' → 'completed' job is left alone (loop broken)
```

## Error handling & edge cases

- **Guard no-op:** if the job is not `in_progress` at finalize (already
  terminal, manually intervened), the guarded write returns `None` and the atom
  passes through without error.
- **Invalid `target_status`:** fails loud with the valid set in the message.
- **Idempotency / retry:** a re-run finds the row already `completed`; the guard
  blocks the second write — safe.
- **Entry-atom guard unchanged:** `content.load_draft_for_image_rebuild` still
  fails loud if the target is no longer `awaiting_approval`. Rebuilding published
  posts remains explicitly unsupported (out of scope).

## Testing (TDD)

1. **`atoms.set_task_status` unit**
   - flips `in_progress → completed` with `percentage=100` (asserts the guarded
     call args).
   - **parameterization:** `target_status='published'` flips to `published`
     (proves the status is config-driven, not hardcoded).
   - no-op when the row is already terminal (guard returns `None`) — does not
     raise.
   - fails loud when `target_status` is missing or invalid.
   - status-constant-vs-DB-constraint drift guard.
2. **`post_pipeline_actions`** — success-path side-effects are skipped when the
   canonical status is `completed`.
3. **graph_def contract test (the requested one)** — `IMAGE_REBUILD_GRAPH_DEF`'s
   terminal node is `atoms.set_task_status → END`, and its declared
   `config.target_status` is a member of
   `post_pipeline_actions._DECIDED_NON_REJECTED_STATUSES`. This is the real
   regression guard: no one can seed a terminal the post-pipeline guard doesn't
   recognize. Plus the existing spec-vs-snapshot freshness assertion covers the
   new atom.
4. **Existing `content.persist_draft_images` tests** remain green unchanged (its
   contract — "the target's status is not touched" — is preserved).

## Out of scope

- Rebuilding **published** posts' images (needs `posts` write + R2 re-export +
  ISR revalidate, and widening the entry-atom guard) — a separate feature.
- Broader `utils/task_status.py::TaskStatus` enum cleanup (it is missing
  `completed` and several other DB-valid statuses; the automated path uses raw
  guarded SQL and does not consult the enum, so this fix does not depend on it).
- Refactoring `seo_refresh`'s `content.republish_post` to reuse
  `atoms.set_task_status` — reasonable once a third utility graph exists; YAGNI
  until then.

## Rollout / verification

- Ship via PR to `main` (linear history); file/attach a poindexter issue for the
  public-repo trail.
- After merge: rebuild + restart the worker image, then run a **real** rebuild
  against an `awaiting_approval` dev-niche draft and confirm, in the DB, that the
  job row ends `completed` (percentage 100), the draft stays `awaiting_approval`
  with fresh images, and no re-run occurs after the stale-sweep window.
