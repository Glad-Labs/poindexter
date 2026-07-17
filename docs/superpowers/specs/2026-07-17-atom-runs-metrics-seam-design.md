# atom_runs metrics seam — design

**Status:** Approved (operator confirmed the design)
**Issue:** [Glad-Labs/poindexter#873](https://github.com/Glad-Labs/poindexter/issues/873)
**Related:** poindexter#868 (writer prompt-size metrics — the change that surfaced this)

## Problem

`atom_runs.metrics` never receives `StageResult.metrics` on the graph_def path.

Measured against live prod (2026-07-17): **6,243 `atom_runs` rows, zero** carrying
`content_length`, `model_used`, or `prompt_template_key`. Every row, for every node
type (`content.generate_draft`, `atoms.narrate_bundle`, `stage.finalize_task`), holds
exactly four keys — `input_keys`, `output_keys`, `input_digest`, `output_digest`.

Anything that assumed `StageResult.metrics` reaches `atom_runs` has been writing to
nowhere since the #355 atom-cutover (2026-06-02).

### Root cause — two independent discard points

**1. The stage shim discards the record** (`services/atom_registry.py::_make_stage_runner`):

```python
node = make_stage_node(stage, pool, record_sink=None)   # <-- None
return await node(state)
```

`make_stage_node` (`services/template_runner.py:974-984`) already builds the correct
record — `metrics=dict(result.metrics or {})` — but it is guarded by
`if record_sink is not None`. Passing `None` throws it away. This affects all 11
`stage.*` virtual atoms.

**2. The atom wrapper hardcodes its metrics** (`services/pipeline_architect.py::_wrap_atom`,
~line 1238):

```python
metrics={
    "input_keys": input_keys,
    "output_keys": output_keys,
    "input_digest": digest_keys(input_keys),
    "output_digest": digest_keys(output_keys),
},
```

There is no merge of atom-supplied metrics. `_wrap_atom` (single call site, line 836)
wraps **every** graph_def node, so this discards everything downstream of point 1.

**3. The one hand-written stage-wrapping atom drops it too**
(`modules/content/atoms/content_generate_draft.py:68-75`) reads only
`result.context_updates`; `result.metrics` is never read.

### Why it went unnoticed

- `scripts/ci/grafana_panels_lint.py` runs `EXPLAIN`. Valid SQL against a JSONB key
  that is never present passes clean — zero matching rows is indistinguishable from
  a correct query awaiting data.
- Unit tests that mock the seam pass while the real chain is broken.
- Only live data reveals it. This is the same class as poindexter#308's four silently
  broken panels.

## Decision

Introduce a **reserved `_atom_metrics` key** that an atom may return. `_wrap_atom` pops
it and merges it into the `TemplateRunRecord`.

`_atom_metrics` is deliberately **NOT** declared in `PipelineState`.

### Forks considered

| Approach                                               | Verdict          | Why                                                                                                                                                                                                                                                                                                                                           |
| ------------------------------------------------------ | ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Reserved `_atom_metrics`, popped**                | **Chosen**       | Mirrors how `_halt` is consumed from the atom's direct return. Popping before `output_keys` is computed keeps digests/previews byte-identical. Cannot bleed across nodes.                                                                                                                                                                     |
| B. Declare `_atom_metrics: dict` in PipelineState      | Rejected         | Consistent with `_halt`/`_halt_reason` being declared channels, but makes metrics **checkpointer-durable**: every node inherits its predecessor's dict unless it overwrites, smearing identical metrics across the graph. This is the exact failure that produced a phantom `2093` for `writer_prompt_revise_chars` in poindexter#868 Task 3. |
| C. Declarative metrics field on `AtomMeta`/`FieldSpec` | Rejected (YAGNI) | Most self-describing and fits the AI-first catalog principle, but it is new registry machinery for a problem two dict lines solve. Revisit if a third consumer needs it.                                                                                                                                                                      |

## Design

Three surgical edits. No new table, migration, `app_settings` key, or Prometheus metric.

### 1. `services/pipeline_architect.py::_wrap_atom`

Pop `_atom_metrics` from `out` immediately after `out` is normalised (~line 1213) and
**before** `output_keys` is computed (~line 1218), then merge into the success record:

```python
metrics={
    **atom_metrics,
    "input_keys": input_keys,
    "output_keys": output_keys,
    "input_digest": digest_keys(input_keys),
    "output_digest": digest_keys(output_keys),
},
```

Structural keys are spread **last** so the wrapper always wins — an atom cannot
corrupt `input_keys`/digests by returning its own.

Ordering is load-bearing: popping before `output_keys` means atoms that do not use
`_atom_metrics` produce byte-identical rows to today, and atoms that do never leak the
key into `output_keys`, the digest, or the `output_preview`.

The exception-path record (~line 1274) is left unchanged — a raising atom has no `out`.

### 2. `services/atom_registry.py::_make_stage_runner`

Pass a local throwaway sink instead of `None`, and lift the metrics onto the return:

```python
sink: list = []
node = make_stage_node(stage, pool, record_sink=sink)
out = await node(state)
if sink and isinstance(out, dict):
    out["_atom_metrics"] = dict(getattr(sink[0], "metrics", {}) or {})
return out
```

This reuses the record `make_stage_node` already builds, so it needs no change to
`template_runner`. The sink is local and never reaches the graph's real `record_sink`,
so no duplicate row is emitted. A plain `list` has no `append_and_notify`, so
`_emit_record` just appends — no DB write, no incremental-capture side effect.

One edit sweeps all 11 `stage.*` nodes.

### 3. `modules/content/atoms/content_generate_draft.py`

The only hand-written stage-wrapping atom (verified: every other atom's `.execute(` is
SQL — `pool.execute` / `conn.execute` — not a stage):

```python
out = {k: updates[k] for k in (...)}
out["_atom_metrics"] = dict(result.metrics or {})
return out
```

Revives, for the writer: the eight `writer_prompt_*` fields (poindexter#868),
`content_length`, `model_used`, `prompt_template_key`, `prompt_template_version`,
`niche_slug`, and the `variant_id` / `experiment_*` lab-harness fields.

## Consequences

### Query rule: always filter by `atom`

Generic keys (`content_length`, `model_used`) are emitted by many stages. Post-fix,
dozens of rows will each carry their own copy. That is correct per-node data, but any
query that does not filter by `atom` blends unrelated nodes into a meaningless average.

**Every `atom_runs.metrics` query must carry an `atom = '<name>'` predicate.** The five
poindexter#868 panels already do.

The `writer_prompt_*` fields are emitted by `content.generate_draft` alone — one row per
task, so `AVG()` is exactly one sample per post. No over-count.

### Column activation (intended, not a regression)

`services/atom_runs.py:116-118` promotes values out of the metrics dict into dedicated
columns:

```python
model   = metrics.get("model_used") or metrics.get("model")
cost    = metrics.get("cost")
retries = int(metrics.get("retries", 0) or 0)
```

Those columns are NULL/0 today **only because** the metrics dict is hollow. Post-fix,
`atom_runs.model` and `.retries` begin populating — that is the columns' intended
purpose finally working.

`atom_runs.cost` stays NULL: no stage emits `cost` in `StageResult.metrics` (verified by
survey). Nothing sums `atom_runs.cost` anyway — the only Grafana reference to
`atom_runs` is a `max(created_at)` freshness check on the Database board. So there is no
double-count against `cost_logs`. Should a stage ever emit `cost`, that column would
activate and become a genuine double-count risk against `cost_logs`; out of scope here,
but worth knowing.

### No duplication

- One record per node per run: the shim's sink is local; only `_wrap_atom` writes to the
  graph's `record_sink`.
- `atom_runs` upserts on `(run_id, seq)`, so incremental + batch writes converge.
- `_wrap_atom`'s retry loop emits one record after the final attempt, not per attempt.

## Scope

**In:** the three edits above; unit tests; end-to-end verification against live
`atom_runs`; doc correction to `docs/architecture/rag-retrieval-stack.md` (its
poindexter#868 section currently claims the fields live on `atom_runs.metrics` — true
only after this ships).

**Out:** routing `variant_id` into `capability_outcomes` (a separate table and path);
opting non-stage-backed atoms into `_atom_metrics` (they have no `StageResult`);
backfilling historical rows (the data was never captured and cannot be reconstructed);
Approach C's declarative registry field.

## Testing

Unit:

1. An atom returning `_atom_metrics` lands those keys in the record's metrics.
2. `_atom_metrics` is stripped from the state returned to LangGraph.
3. `output_keys` / `input_digest` / `output_digest` are unchanged when `_atom_metrics`
   is absent (byte-identical to today).
4. `output_keys` excludes `_atom_metrics` when present.
5. Structural keys win when an atom returns a conflicting `input_keys`.
6. `_make_stage_runner` attaches the stage's `StageResult.metrics` as `_atom_metrics`.
7. A stage returning no metrics attaches nothing (no `_atom_metrics` key).

End-to-end (the step skipped in poindexter#868, and the only kind that would have
caught this):

8. After deploy, assert against live `atom_runs` that a real `content.generate_draft`
   row carries `writer_prompt_draft_chars` — a DB query, not a mock.

## Routing

Pipeline/substrate code is public → issue on `Glad-Labs/poindexter` (#873). PR against
`Glad-Labs/glad-labs-stack` (auto-mirrors), closing #873.
