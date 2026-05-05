# Template Runner

**File:** `src/cofounder_agent/services/template_runner.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_template_runner.py` + integration fan-out tests
**Last reviewed:** 2026-05-04

## What it does

`TemplateRunner` is the LangGraph-backed orchestrator that replaces `WorkflowExecutor` (legacy phase-based engine). Phase 1 of poindexter#356 lifted LangGraph as the runner; phases 2-3 of the dynamic-pipeline-composition spec build on top with architect-LLM-composed graphs and atom-tier routing.

The runner is template-agnostic in intent — it takes a `StateGraph` and a `PipelineState` (a `TypedDict` shaped by the registered template), drives the graph to completion or halt, and returns a `TemplateRunSummary` with per-node metrics. Today it drives the dev_diary template directly; future architect-composed pipelines slot in via the same surface.

Three things make it useful beyond a vanilla LangGraph wrapper:

- **`make_stage_node(stage)`** — adapts an existing `Stage` instance (the legacy `services/stages/*.py` shape) into a LangGraph-compatible async node. The Stage's `execute(context)` becomes a node that reads `state`, runs the stage, returns the diff to merge back. Lets us migrate one stage at a time without rewriting them as atoms first.
- **`_emit_progress`** — fans node start/completion/failure events out to Discord via `notify_operator(critical=False)`. Gated by the `template_runner_progress_streaming` setting (default ON; Discord is the spam-friendly channel). NEVER routes to Telegram — that channel is reserved for critical alerts per `feedback_telegram_vs_discord`.
- **`PipelineState.qa_reviews: Annotated[list, operator.add]`** — the parallel-fan-out reducer. Critic atoms in an architect-composed graph (narrate → [critic_1, critic_2] → aggregate) all append to `qa_reviews` on the same step; without `operator.add` LangGraph's default last-value channel rejects concurrent writes with `InvalidUpdateError`. Each critic returns its review wrapped in a one-element list; the reducer concats.

## Key methods

- **`run(state, *, graph, capability_outcomes_writer=None)`** — async. Compiles + invokes the graph from the entrypoint. Returns `TemplateRunSummary(records, terminal_state)`. Each `record` is a `TemplateRunRecord(node_name, status, started_at, finished_at, metrics)` so callers can inspect per-node timing + outputs.
- **`make_stage_node(stage, *, fallback_pool=None)`** — adapter from the `Stage` interface. The `fallback_pool` kwarg is captured at registration time from `shared_context.get_database_service` so virtual-stage atoms don't crash when `state['database_service']` isn't seeded outside worker context.
- **`_emit_progress(pool, *, event_type, payload, notify_operator_message=None)`** — fire-and-forget Discord push. `pool` and `event_type`/`payload` parameters are kept on the signature for source-compat; their pipeline_events INSERT was dropped in poindexter#366 phase 4 (no consumer ever read those rows). Future Langfuse-trace wire-up will read `event_type` + `payload` to populate span attributes.

## Capability outcomes feedback loop

After a run completes, the runner writes per-node training signal into `capability_outcomes` (table from migration 0147) when the caller passes a `capability_outcomes_writer`. The router's next routing decision can read this — same atom + same input shape ought to produce similar quality, similar cost, similar latency. ML-first design per `feedback_always_keep_ml_in_mind`: every deterministic component pairs with a learned-successor sketch.

## Reads from / writes to

- **Reads:** `state['database_service']` → asyncpg pool for the stage adapters; `site_config` for the `template_runner_progress_streaming` setting.
- **Writes:**
  - `audit_log` (via stage adapters that call `audit_log_bg`) — the canonical historical record.
  - `capability_outcomes` — per-node metrics for the router's training loop.
  - Discord (via `notify_operator`) — operator-visible progress.
- **External APIs:** none directly. Stages own LLM/HTTP calls; the runner just orchestrates.

## Failure modes

- **Node raises** — captured in the `record.status='failed'` + `record.error` field; downstream nodes that depend on the failed node's output trigger LangGraph's default abort. The terminal_state still returns with whatever ran.
- **Concurrent fan-out without reducer** — `InvalidUpdateError`. State key needs `Annotated[T, reducer_fn]`. Already handled for `qa_reviews`; new fan-out targets need their own annotation.
- **Halt before completion** — gates (e.g., `atoms.approval_gate`) return `_halt=True`. The runner stops cleanly; the calling pipeline picks up where it left off on the next pass once the operator approves (gate state lives in `pipeline_gate_history` per poindexter#366 phase 1).
- **Discord delivery fails** — swallowed at debug level. The orchestrator continues; the operator just doesn't get the progress ping for that node.

## Migration in flight (poindexter#367)

`WorkflowExecutor` is being replaced by `TemplateRunner` in 4 sequential PRs. Today the dev_diary path uses TemplateRunner directly; the legacy custom_workflows + 5-template-execution paths still go through WorkflowExecutor. Don't extend WorkflowExecutor — new orchestration features land here.

## See also

- `services/workflow_executor.md` — legacy phase engine being deleted.
- `plugins/atom.py` — `AtomMeta` shape (capability tier, cost class, retry policy) used by future architect-composed graphs.
- `services/atom_registry.py` — bridges legacy stages into the atom catalog so the architect-LLM can drop a stage at any point in a composed graph.
- `docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md` — full spec.
