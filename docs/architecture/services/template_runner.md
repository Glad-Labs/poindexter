# Template Runner

**File:** `src/cofounder_agent/services/template_runner.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_template_runner_postgres_checkpointer.py`, `tests/unit/services/test_template_runner_state_partition.py`, `tests/unit/services/test_checkpoint_resumable.py` + integration fan-out tests
**Last reviewed:** 2026-06-13

## What it does

`TemplateRunner` is the **sole pipeline path** as of 2026-05-16 (Lane C Stage 4). The legacy `WorkflowExecutor` + its `custom_workflows_service` / `template_execution_service` / `workflow_validator` / `phase_mapper` / `phase_registry` / `workflow_progress_service` / `phases/` chain — plus the `agents/` tree — were all deleted 2026-05-09 (~3,800 LOC). The chunked `StageRunner` flow in `content_router_service.py` and `plugins/stage_runner.py` itself followed on 2026-05-16. There is no remaining legacy orchestration engine to migrate off of.

The runner is template-agnostic in intent — it takes a `StateGraph` and a `PipelineState` (a `TypedDict` shaped by the registered template), drives the graph to completion or halt, and returns a `TemplateRunSummary` with per-node metrics. Today it drives the `canonical_blog` (prod default — `default_template_slug='canonical_blog'`) and `dev_diary` templates; future architect-composed pipelines slot in via the same surface.

Three things make it useful beyond a vanilla LangGraph wrapper:

- **`make_stage_node(stage)`** — adapts an existing `Stage` instance (the legacy `modules/content/stages/*.py` shape) into a LangGraph-compatible async node. The Stage's `execute(context)` becomes a node that reads `state`, runs the stage, returns the diff to merge back. Lets us migrate one stage at a time without rewriting them as atoms first.
- **`_emit_progress`** — fans node start/completion/failure events out to Discord via `notify_operator(critical=False)`. Gated by the `template_runner_progress_streaming` setting (default ON; Discord is the spam-friendly channel). NEVER routes to Telegram — that channel is reserved for critical alerts per `feedback_telegram_vs_discord`.
- **`PipelineState.qa_reviews: Annotated[list, operator.add]`** — the parallel-fan-out reducer. Critic atoms in an architect-composed graph (narrate → [critic_1, critic_2] → aggregate) all append to `qa_reviews` on the same step; without `operator.add` LangGraph's default last-value channel rejects concurrent writes with `InvalidUpdateError`. Each critic returns its review wrapped in a one-element list; the reducer concats.
- **`_resolve_checkpointer()`** — gated by the `template_runner_use_postgres_checkpointer` setting (default off). When on, builds an `AsyncPostgresSaver.from_conn_string(dsn)` per `run()` invocation and passes it into `compile(checkpointer=...)` so LangGraph state survives worker restarts and is resumable by `thread_id`. DSN comes from the constructor kwarg (tests) or `brain.bootstrap.resolve_database_url`. Fall-back posture per #371: missing DSN / missing dep / connection failure → log warning, fall back to `MemorySaver`. **Setup-time failure on a reachable Postgres** → raise `_CheckpointerSetupError` (loud) so a half-broken schema doesn't silently degrade durability.
- **State-vs-services partition (`_partition_state_and_services`, poindexter#382)** — `run()` splits the caller's `initial_state` into two channels before invoking the graph. Pure data (`task_id`, `topic`, `tags`, etc.) stays on the StateGraph and is checkpointed. Live service handles (`database_service`, `image_service`, `settings_service`, `image_style_tracker`, `site_config`, plus any other key whose value isn't `ormsgpack`-encodable) ride in `RunnableConfig.configurable["__services__"]`, which LangGraph threads through node calls without serializing. The `make_stage_node` adapter merges the services back into the legacy `context` dict so wrapped stages still read `context.get("database_service")` unchanged. Pre-#382 the runner pushed everything onto state and every checkpoint write logged `TypeError: Type is not msgpack serializable: DatabaseService` (non-fatal but noisy on every dev_diary run). **Annotation gotcha:** node `config` parameters MUST be annotated as bare `RunnableConfig` (or `Optional[RunnableConfig]`, or unannotated) — the `RunnableConfig | None` pipe form becomes a string under `from __future__ import annotations` and falls outside LangGraph's `KWARGS_CONFIG_KEYS` allow-list, so config silently arrives as `None`.

## Key methods

- **`run(state, *, graph, capability_outcomes_writer=None)`** — async. Compiles + invokes the graph from the entrypoint. Returns `TemplateRunSummary(records, terminal_state)`. Each `record` is a `TemplateRunRecord(node_name, status, started_at, finished_at, metrics)` so callers can inspect per-node timing + outputs.
- **`make_stage_node(stage, *, fallback_pool=None)`** — adapter from the `Stage` interface. The `fallback_pool` kwarg is captured at registration time from `shared_context.get_database_service` so virtual-stage atoms don't crash when `state['database_service']` isn't seeded outside worker context.
- **`_emit_progress(pool, *, event_type, payload, notify_operator_message=None)`** — fire-and-forget Discord push. `pool` and `event_type`/`payload` parameters are kept on the signature for source-compat; their pipeline_events INSERT was dropped in poindexter#366 phase 4 (no consumer ever read those rows). Future Langfuse-trace wire-up will read `event_type` + `payload` to populate span attributes.

## Capability outcomes feedback loop

After a run completes, the runner writes per-node training signal into `capability_outcomes` (table from migration 0147) when the caller passes a `capability_outcomes_writer`. The router's next routing decision can read this — same atom + same input shape ought to produce similar quality, similar cost, similar latency. ML-first design per `feedback_always_keep_ml_in_mind`: every deterministic component pairs with a learned-successor sketch.

## Reads from / writes to

- **Reads:** `config['configurable']['__services__']['database_service']` → asyncpg pool for the stage adapters (legacy `state['database_service']` also works inside wrapped stages because the adapter merges services into the context dict — see partition note above); `site_config` for the `template_runner_progress_streaming` setting.
- **Writes:**
  - `audit_log` (via stage adapters that call `audit_log_bg`) — the canonical historical record.
  - `capability_outcomes` — per-node metrics for the router's training loop.
  - Discord (via `notify_operator`) — operator-visible progress.
- **External APIs:** none directly. Stages own LLM/HTTP calls; the runner just orchestrates.

## Failure modes

- **Node raises** — captured in the `record.status='failed'` + `record.error` field; downstream nodes that depend on the failed node's output trigger LangGraph's default abort. The terminal_state still returns with whatever ran.
- **Concurrent fan-out without reducer** — `InvalidUpdateError`. State key needs `Annotated[T, reducer_fn]`. Already handled for `qa_reviews`; new fan-out targets need their own annotation.
- **Halt before completion** — gates (e.g., `atoms.approval_gate`) return `_halt=True`. The runner stops cleanly; the calling pipeline picks up where it left off on the next pass once the operator approves (gate state lives in `pipeline_gate_history` per poindexter#366 phase 1).
- **Resume atomicity** — `poindexter pipeline resume` records the gate approval BEFORE re-invoking `run(resume=True)`. A resume that _raises_ (the graph never advances past the gate — e.g. the checkpointer can't be set up) leaves a dangling approval, so the CLI rolls it back (`approval_service.rollback_resume_approval`) and restores the pause. A resume that _halts past the gate_ (a downstream node failed AFTER the gate passed) strands the task `in_progress` with an intact checkpoint and `awaiting_gate` already cleared — `has_resumable_checkpoint(pool, thread_id)` (this module) lets the CLI offer a _continue-resume_ from the checkpoint instead of refusing. Belt-and-suspenders: approvals are stamped with the task's `retry_count`, and `atoms.approval_gate` only honors an approval matching the current `retry_count`, so a stale approval can never auto-pass a post-sweep fresh run even if a rollback is missed. See `poindexter/cli/pipeline.py::resume_command`.
- **Discord delivery fails** — swallowed at debug level. The orchestrator continues; the operator just doesn't get the progress ping for that node.

## See also

- `plugins/atom.py` — `AtomMeta` shape (capability tier, cost class, retry policy) used by future architect-composed graphs.
- `services/atom_registry.py` — bridges legacy stages into the atom catalog so the architect-LLM can drop a stage at any point in a composed graph.
