# QA Rescue Cycle — Design Spec

**Date:** 2026-06-17
**Status:** Approved for implementation
**PR target:** `Glad-Labs/glad-labs-stack main`

---

## Context

PR Glad-Labs/glad-labs-stack#1668 (QA scoring recalibration) deliberately deferred a bounded rewrite/rescue loop because it touches the shared pipeline engine. This spec implements that deferred feature: when a draft that the LLM critic vetoes arrives at `qa.aggregate`, it gets one targeted revision pass before being hard-rejected, rather than being dropped immediately.

The legacy `cross_model_qa` rewrite loop was intentionally NOT ported at atom-cutover #355. This feature reintroduces the idea with a cleaner, bounded, durable architecture built around LangGraph's native conditional-edge mechanism.

---

## Chosen Approach: Graph Cycle (`qa.rewrite` node)

Rejected **Option 2 (in-aggregate rescue)** — calling the writer inline from `qa.aggregate` would make the atom stateful, opaque, and hard to test. Chosen **Option 1 (graph cycle)**: a `qa.rewrite` node with a conditional back-edge to the QA block entrance (`qa_programmatic`), bounded by a durable counter in `PipelineState`.

---

## Design

### What is a Rescuable Reject?

`is_rescuable_reject(reviews, vetoed_by, *, final_score, threshold)` returns `True` iff:

- **(a) Critic-only veto:** `vetoed_by` is non-empty, **and** every vetoing reviewer has `provider ∈ ("anthropic", "google", "ollama")` — no `programmatic_validator`, no `missing_required:*` synthetic vetoes.
- **(b) Score-threshold reject:** `vetoed_by == []` (critic approved) **and** `final_score < threshold` (content was below the weighted score floor).

Returns `False` — never rescuable — if `vetoed_by` contains any `"programmatic"` or `"missing_required:*"` provider (fabrication, structural violations, JSON-envelope leaks).

### `qa.aggregate` — Rescue Dispatch

When a reject arrives at `qa.aggregate` and `is_rescuable_reject()` is `True`:

1. Check `qa_rewrite_attempts < qa_rewrite_max_attempts` (setting, default `1`, clamped [0,3]).
2. If within budget: emit `_goto="qa_rewrite"` (no `_halt`, no DB persist, no `qa_pass_completed` event). Emit `qa_rescue_scheduled` audit entry. No other state mutation.
3. If budget exhausted or not rescuable: write DB reject, set `_halt=True`, emit `qa_pass_completed` as before. Set `_goto=""`.

All approve paths and non-rescuable rejects are unchanged except they now also explicitly set `_goto=""` (clearing any stale value from a prior rescue pass).

### `qa.rewrite` Atom (new: `modules/content/atoms/qa_rewrite.py`)

Receives the failing draft + critic feedback from `qa_rail_reviews`. Calls the writer model via `llm_text.ollama_chat_text` with `timeout_setting="content_router_qa_rewrite_timeout_seconds"` (default `240`). Note: `ollama_chat_text` takes no `max_tokens` param (output length is governed by the dispatcher per-model), so the orphaned `content_router_qa_rewrite_max_tokens` setting stays orphaned — it is documented, not plumbed.

Prompt is DB-configurable via the UnifiedPromptManager key `atoms.qa_rewrite.revise_prompt`, resolved by a `_resolve_revise_prompt()` helper with an inline `_REVISE_PROMPT_FALLBACK` constant (the codebase convention — no `prompts/*.yaml` is git-tracked; Langfuse is the live override surface). Mirrors `review_with_critic.py`.

Returns:

- `content`: revised body
- `qa_rewrite_attempts`: incremented by 1
- `qa_rail_reviews`: reset sentinel `[{"__reset__": True}]` (clears stale first-pass reviews)
- `qa_known_wrong_fact_only`: `False` (reset; second pass starts fresh)

**Degrade-to-reject** if the writer errors or returns empty: keep prior content unchanged, increment counter (so the loop terminates on the next `qa.aggregate` pass), emit a finding. The original reject stands.

ATOM_META:

- `requires`: `("content", "qa_rail_reviews", "qa_rewrite_attempts")`
- `produces`: `("content", "qa_rewrite_attempts", "qa_rail_reviews")`
- `cost_class`: `"compute"`, `idempotent`: `False`, `side_effects`: `("llm_call",)`

### Graph Topology

Node added to `CANONICAL_BLOG_GRAPH_DEF` (36 → 37 nodes):

```
... → qa_aggregate → seo_all_metadata → ...    (approve / exhausted-reject path, default forward)
          │
          └─[branch: _goto=="qa_rewrite"]──→ qa_rewrite ──[loop]──→ qa_programmatic → ...
```

Edge annotations:

- `{"from": "qa_aggregate", "to": "qa_rewrite", "branch": true}` — fires when `_goto == "qa_rewrite"`
- `{"from": "qa_rewrite", "to": "qa_programmatic", "loop": true}` — the back-edge; exempt from cycle detection
- `{"from": "qa_aggregate", "to": "seo_all_metadata"}` — unchanged default forward edge

### `_validate_spec` Changes

The DAG validator (`pipeline_architect.py::_validate_spec`) currently blocks ANY cycle with an `_has_cycle` DFS check (line 487) and a Kahn topo-sort reachability check (lines 505-519). Both must be exempted for flagged `loop` edges:

1. `_has_cycle` DFS: skip any edge with `"loop": true` when building the adjacency list.
2. Kahn topo-sort (`adj2`/`indeg`): skip `"loop"` edges when computing in-degrees, so the loopback target (`qa_programmatic`) doesn't get inflated indegree that would prevent it and its entire downstream chain from clearing zero-indegree validation.

Unflagged accidental back-edges still fail validation loudly.

### `build_graph_from_spec` Changes

Nodes with a `"branch": true` out-edge get a `_branch_router(branch_target, default_target)` instead of the default halt-router:

```python
def _branch_router(branch_target, default_target, halt_target="__end__"):
    def router(state):
        if state.get("_halt"):
            return halt_target
        if state.get("_goto") == branch_target:
            return branch_target
        return default_target
    return router
```

This keeps `_halt` taking priority over `_goto`, and `_goto` taking priority over the default forward edge.

### `_merge_rail_reviews` Reducer

`qa_rail_reviews` currently uses `operator.add` (list concatenation). A rescue cycle's second pass would accumulate stale first-pass reviews, guaranteeing re-reject via the old veto. Replace with:

```python
def _merge_rail_reviews(existing: list, incoming: list) -> list:
    for item in incoming:
        if isinstance(item, dict) and item.get("__reset__"):
            return [x for x in incoming if not (isinstance(x, dict) and x.get("__reset__"))]
    return existing + incoming
```

Backward-compatible: nothing else sends a dict element with `__reset__`. Parallel rail atoms still work (each returns a single-item list that `_merge_rail_reviews` appends).

### `PipelineState` Changes

Add `_goto: str` to `PipelineState` (alongside `_halt`, `_halt_reason`):

- `_goto: str` — default `""`. Set to `"qa_rewrite"` by `qa.aggregate` on a rescuable reject; cleared to `""` on approve or hard reject.

Change `qa_rail_reviews` annotation from `Annotated[list, operator.add]` to `Annotated[list, _merge_rail_reviews]`.

`qa_rewrite_attempts: int` already exists (line 430). `qa.aggregate` already emits `"qa_rewrite_attempts": 0` on every run — this scaffolding is already in place.

### Settings

In `settings_defaults.py`:

- `'qa_rewrite_max_attempts': '1'` — default on (1 attempt). Operators can set to `0` to disable or `3` for up to 3 passes.

Existing reused settings (already seeded — no change needed):

- `content_router_qa_rewrite_max_tokens: '8000'`
- `content_router_qa_rewrite_timeout_seconds: '240'`

### Migration

New migration `YYYYMMDD_HHMMSS_reseed_canonical_blog_graph_def_qa_rescue_cycle.py`:

- Re-seeds `pipeline_templates.graph_def` for `canonical_blog` with the 37-node spec (`json.dumps(CANONICAL_BLOG_GRAPH_DEF)`, `active=True`).

---

## Bounds and Safety

| Concern                   | Mitigation                                                                                                                                          |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Infinite loop             | `qa_rewrite_attempts` counter in durable LangGraph checkpoint state. Max clamped to [0,3] at read time.                                             |
| Kill-and-resume           | Counter lives in postgres checkpointer checkpoint — resuming a killed run reloads the counter; cannot re-rescue.                                    |
| Fabrication rescued       | `is_rescuable_reject` gates on `provider ∈ _CRITIC_PROVIDERS`. Any `"programmatic"` or `"missing_required:*"` entry → hard reject, no rescue.       |
| Stale QA reviews          | `_merge_rail_reviews` respects `{"__reset__": True}` sentinel; second pass starts from empty review list.                                           |
| Graph compiler cycle      | `_validate_spec` exempts `"loop"` edges; `build_graph_from_spec` uses `_branch_router` for `"branch"` edges. Accidental back-edges still fail loud. |
| Writer errors on rewrite  | Degrade-to-reject: keep prior content, increment counter (loop terminates next pass), emit finding.                                                 |
| LangGraph recursion limit | `DEFAULT_RECURSION_LIMIT = 10007` (LangGraph 1.1.10). Rescue cycle adds ~14 steps; trivially within budget. Real bound = durable counter.           |

---

## Testing Plan

**TDD order** (failing tests first, then implementation):

1. `is_rescuable_reject()` — critic veto rescuable, score-threshold rescuable, programmatic veto NOT rescuable, missing_required NOT rescuable.
2. `_merge_rail_reviews` reducer — normal concat, reset sentinel clears stale, sentinel stripped from result.
3. `_validate_spec` — flagged `loop` edge validates, unflagged back-edge still errors, `branch` edge validates.
4. Branch-router compile test — `qa.aggregate` → `qa.rewrite` on `_goto`, → `seo_all_metadata` when `_goto == ""`.
5. `qa.rewrite` atom unit tests — successful revision, degrade-to-reject on writer error, counter increment, reset sentinel emitted.
6. `qa.aggregate` rescue/defer/clear unit tests — critic veto defers, score-threshold defers, exhausted attempts hard-rejects, fabrication hard-rejects immediately.
7. End-to-end `test_graphdef_pipeline` cycle tests:
   - Rescue fires once → QA block re-runs → approves; `finalize_task` sees revised `content`.
   - Fabrication veto → no rescue, halts at `qa.aggregate`, `finalize_task` never runs.
8. Full QA atom suite + `multi_model_qa` tests (regression guard).

---

## Files Touched

| File                                                                                | Change                                                                 |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `modules/content/atoms/qa_aggregate.py`                                             | Rescue dispatch logic; emit `_goto`; defer terminal side-effects       |
| `modules/content/atoms/_qa_rail_common.py`                                          | Add `is_rescuable_reject()`                                            |
| `modules/content/atoms/qa_rewrite.py`                                               | **New** — rewrite atom                                                 |
| `services/pipeline_architect.py`                                                    | `_validate_spec` loop-exemption; `build_graph_from_spec` branch-router |
| `services/template_runner.py`                                                       | Add `_goto` to `PipelineState`; `_merge_rail_reviews` reducer          |
| `services/canonical_blog_spec.py`                                                   | Add `qa_rewrite` node + branch/loop edges (36 → 37 nodes)              |
| `services/settings_defaults.py`                                                     | Add `qa_rewrite_max_attempts: '1'`                                     |
| `services/migrations/YYYYMMDD_*_reseed_canonical_blog_graph_def_qa_rescue_cycle.py` | **New** — reseed graph_def                                             |
| `tests/unit/services/atoms/test_qa_aggregate_atom.py`                               | Rescue/defer/exhaust/fabrication tests + fix 4 existing reject tests   |
| `tests/unit/services/atoms/test_qa_rewrite_atom.py`                                 | **New** — rewrite atom unit tests                                      |
| `tests/unit/services/atoms/test_qa_rail_common.py`                                  | `is_rescuable_reject` tests                                            |
| `tests/unit/services/test_merge_rail_reviews.py`                                    | **New** — reducer tests                                                |
| `tests/unit/services/test_pipeline_architect_validate.py`                           | loop-edge validation tests                                             |
| `tests/unit/services/test_pipeline_architect_branch.py`                             | **New** — branch-router compile tests                                  |
| `tests/integration/test_graphdef_pipeline.py`                                       | End-to-end cycle tests                                                 |

(The revise prompt is an inline `_REVISE_PROMPT_FALLBACK` constant in `qa_rewrite.py` — no `prompts/*.yaml` file, per the codebase convention.)

---

## Out of Scope

- Multiple rescue passes (max is 1 by default; setting exists for future tuning, but nothing changes behavior beyond 1 in this PR)
- `dev_diary` template (no QA block; no change needed)
- Cross-niche rescue tuning (single global `qa_rewrite_max_attempts` setting; per-niche override deferred)
- Grafana panel for rescue rate (deferred; can be added from `audit_log` `qa_rescue_scheduled` events)
