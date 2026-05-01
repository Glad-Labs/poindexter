# Workflow Executor

**File:** `src/cofounder_agent/services/workflow_executor.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_workflow_executor.py`
**Last reviewed:** 2026-04-30

## What it does

`WorkflowExecutor` runs a `CustomWorkflow` (a user-defined sequence of
phases like `research → draft → qa → image → publishing`) by executing
each phase in index order, threading outputs from one phase into the
inputs of the next via `PhaseMapper`, and recording an `InputTrace` on
every value so the UI can show "this came from phase X / field Y" or
"the user typed this." It's the engine behind the custom-workflow
builder, not the canonical content pipeline (that's
`content_router_service.process_content_generation_task`).

Phases are not LLM calls themselves — each phase has an `agent_type`
string that maps to an agent factory (`research_agent`,
`creative_agent`, `qa_agent`, `blog_image_agent`,
`blog_publisher_agent`, etc.). The executor imports those agent
modules lazily, caches them at the class level so repeated workflow
runs don't re-import, and calls `agent.run(inputs)`. The agent owns the
LLM call; the executor only orchestrates ordering, input-mapping,
timing, and progress callbacks.

## Public API

- `WorkflowExecutor(registry=None, mapper=None)` — constructor.
  Defaults to `PhaseRegistry.get_instance()` and a fresh `PhaseMapper`
  bound to that registry.
- `await executor.execute_workflow(workflow, initial_inputs=None, execution_id=None, progress_service=None) -> dict[str, PhaseResult]` —
  the only public entry point. Returns a `{phase_name: PhaseResult}`
  dict where each `PhaseResult` carries `status` (`completed` /
  `failed` / `skipped`), `output` (the agent's return dict), `error`,
  `execution_time_ms`, `model_used` (the agent_type string),
  `tokens_used` (currently always `None`), and `input_trace` (a dict
  mapping every input key to where it came from).

The class also exposes the class-level `_agent_module_cache` dict for
diagnostics, but that's an implementation detail — don't reach into it
from callers.

## Key behaviors / invariants

- **Phases run sequentially.** Sorted by `phase.index`; no parallel
  execution. A `phase.skip = True` short-circuits to a `skipped`
  `PhaseResult` with zero duration.
- **Input precedence (highest → lowest).** Per phase, inputs are merged
  in this order, with later sources only filling unset keys:
  1. `phase.user_inputs` (operator-provided overrides for that phase)
  2. Auto-mapped fields from the immediately previous phase
     (`PhaseMapper`-derived `target_key ← source_key` map)
  3. All other prior-phase outputs, walked in reverse-chronological
     order (so the most recent prior phase wins for a given key)
  4. `initial_inputs` (the workflow-level seed: topic, style, tone,
     target_length)
  5. `phase_def.input_schema` defaults from the registry
- **Halts on first failure.** The first phase whose agent raises or
  returns `{"status": "failed", ...}` causes
  `WorkflowExecutionError` to be raised, and every subsequent phase is
  marked `status="skipped"` with `error="Workflow execution halted"`.
- **Progress callbacks are best-effort.** Every interaction with
  `progress_service` is wrapped in `try/except` — a broken progress
  reporter never aborts a workflow.
- **Agent invocation is sync-or-async transparent.**
  `asyncio.iscoroutinefunction(agent.run)` decides whether to `await`
  or call directly, so legacy sync agents still work.

## Configuration

`WorkflowExecutor` reads no `app_settings` keys directly — all
tunables live on the agents themselves (`research_agent` reads
research settings, `creative_agent` reads writer settings, etc.).

Phase definitions and the agent_type → module mapping are static:

- `PhaseRegistry` — owns the phase catalog. Agents register phase
  definitions there at import time.
- `_get_agent()` — hardcoded `agent_type → (module_path, factory)`
  mapping inside the executor. Adding a new phase type means editing
  the `agent_mapping` dict here AND registering with `PhaseRegistry`.

## Dependencies

- **Reads from:**
  - `services.phase_registry.PhaseRegistry` — for phase definitions.
  - `services.phase_mapper.PhaseMapper` /
    `build_full_phase_pipeline(phase_names)` — produces the
    `target_key ← source_key` map between adjacent phases.
- **Writes to:** nothing directly. Agents may write to DB; the
  executor only returns results in memory.
- **External APIs:** none directly. The agents own all LLM / HTTP /
  image-API calls.
- **Callers:**
  - `services.custom_workflows_service.CustomWorkflowsService` —
    constructs and holds a `WorkflowExecutor` for the
    `/api/custom-workflows/*` routes.
  - `services.template_execution_service.TemplateExecutionService` —
    same, for the workflow-template runner.
  - Both of those are wired into the FastAPI app from `main.py` and
    routed via `route_utils`.

## Failure modes

- **Phase mapping error before any phase runs** —
  `PhaseMapper.build_full_phase_pipeline` raises `PhaseMappingError`
  (typically: an unknown phase name, or a phase whose declared output
  schema doesn't satisfy the next phase's input schema). The executor
  re-raises as `WorkflowExecutionError("Failed to build phase
pipeline: …")` before touching any agent.
- **Phase agent raises** — caught inside the per-phase `try/except`,
  converted to a `PhaseResult(status="failed", error=str(e))`, and
  then the outer block raises `WorkflowExecutionError` carrying the
  failed phase name + index.
- **Phase agent returns `status="failed"`** — same handling. The
  agent's full output dict is preserved in `result.output` so callers
  can introspect what the agent saw before deciding to fail.
- **Unknown `agent_type`** — `_get_agent()` returns `None`, the phase
  is recorded as failed with `error="Agent '<type>' not found"`. Add
  the mapping in `_get_agent()` if you're introducing a new phase type.
- **Agent module import fails** — `_get_agent()` catches `ImportError`,
  logs with traceback, returns `None`. Same downstream behavior as
  unknown agent_type.
- **Skipped phases never produce outputs.** If a downstream phase
  expected a value from a skipped phase, the input mapping silently
  falls through to `initial_inputs` / defaults. This can produce
  undefined behavior — phase skipping is a power tool, not a safe
  default.

## Common ops

- **Trace why a phase received a particular value:** check
  `result.input_trace[key]` — `user_provided=True` means the operator
  typed it, `auto_mapped=True` with `source_phase=X / source_field=Y`
  means it came from phase X's output, all-`False` means it came from
  the registry's default for that input.
- **Reset the agent module cache (test isolation):**
  `WorkflowExecutor._agent_module_cache.clear()`. The cache is
  class-level and persists across instances within a process.
- **Find which workflows have run recently:** workflow execution rows
  live wherever the calling service (CustomWorkflowsService /
  TemplateExecutionService) persists them — the executor itself
  doesn't write to DB.
- **Inspect a halted workflow:** the returned dict still contains
  `PhaseResult`s for every phase up to and including the failure;
  downstream phases will be `skipped`. The first `status="failed"`
  entry's `error` field has the reason.

## See also

- `docs/architecture/services/content_router_service.md` — the
  canonical content pipeline. `WorkflowExecutor` is for user-defined
  custom workflows; the content router is for the standard topic →
  publish flow.
- `services.custom_workflows_service` — wraps the executor with
  persistence + a public route surface.
- `services.template_execution_service` — wraps the executor for the
  workflow-template builder.
- `schemas.custom_workflow_schemas` — the `CustomWorkflow`,
  `WorkflowPhase`, `PhaseResult`, and `InputTrace` dataclasses.
