# Unified Orchestrator

**File:** `src/cofounder_agent/services/unified_orchestrator.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_unified_orchestrator.py`
**Last reviewed:** 2026-04-30

## Status: scaffolding-only — not wired into production

`UnifiedOrchestrator` was designed as the master "natural language
in, executed result out" entry point that routes any user request
(content creation, financial analysis, compliance check, decision
support, etc.) to the appropriate handler. **It is not currently
instantiated anywhere in the running app.**

The lifespan code in `main.py` and the deferred-task wiring in
`utils/startup_manager.py` still carry stale comments
(`"app.state.orchestrator will be set to UnifiedOrchestrator below"`,
`"WAFTER UnifiedOrchestrator is initialized"`) that point at a
planned wiring step that was never completed. In production today:

- `task_executor` calls
  `services.content_router_service.process_content_generation_task`
  directly for every queued content task.
- `app.state.orchestrator` is never set.
- Only the unit tests in `test_unified_orchestrator.py` exercise this
  class.

If you're documenting "how content gets generated," the answer is the
content router, not this file. If you're considering wiring this in,
read the "Public API" + "Failure modes" sections below first — there
are dead branches and stub handlers that need cleanup before it can
serve real traffic.

## What it does (when invoked from tests / a dev harness)

`UnifiedOrchestrator.process_request(user_input, context)` parses a
natural-language string with keyword matching, classifies it into one
of nine `RequestType` enum values, and dispatches to the matching
`_handle_*` method. The content-creation handler runs its own
six-stage pipeline (research → draft → QA loop → image →
formatting/publishing prep → approval assembly) using agents
instantiated through `_get_agent_instance()` (registry-first, falls
back to a hardcoded module:class mapping).

The class also tracks per-instance request stats (`total_requests`,
`successful_requests`, `failed_requests`) and, if a `database_service`
is provided, calls `_store_execution_result()` after each request —
though that method currently just logs a line without persisting
anything.

## Public API

- `UnifiedOrchestrator(database_service=None, model_router=None, quality_service=None, memory_system=None, **agents)` —
  constructor. `**agents` is a free-form kwargs bag —
  `content_orchestrator`, `financial_agent`, `compliance_agent`, etc.
  are stored as `self.agents[name]` and looked up by `_handle_*`
  methods.
- `await orch.process_request(user_input: str, context: dict | None = None) -> dict[str, Any]` —
  main entry. Returns either a legacy dict response (for older
  handlers) or `_result_to_dict(ExecutionResult)` for handlers that
  return the typed dataclass.

The internal `_handle_*` methods are private and tied to the
`RequestType` enum branches in `process_request`. The only one with
real implementation is `_handle_content_creation`; the others are
either thin delegations to injected agents (financial, compliance) or
no-op stubs that just echo "executed for: …" (task management,
information retrieval, decision support, system operation,
intervention).

## Key behaviors / invariants

- **Routing is keyword-based, not LLM-based.** Despite the docstring
  mentioning "natural language understanding," `_parse_request` is a
  cascade of `if any(kw in input_lower for kw in [...])` matches.
  Order matters: `"create content"` is checked before generic
  `"create"` keywords.
- **Default request type is content creation.** Anything that doesn't
  match a keyword falls into `_handle_content_creation`, which means
  unrecognized input runs the full six-stage pipeline. No-ops are not
  the default.
- **Per-stage timeouts are module-level constants**, not
  app_settings: `RESEARCH_TIMEOUT_S=120`, `DRAFT_TIMEOUT_S=180`,
  `QA_TIMEOUT_S=120`, `REFINEMENT_TIMEOUT_S=180`,
  `FORMATTING_TIMEOUT_S=120`. **This violates the project's "config in
  DB, not code" principle** — see "Status callout" below.
- **Stages emit `task_progress` WebSocket events** at fixed
  percentages (research 10%, draft 25%, qa 45%, images 60%,
  formatting 75%) via `services.websocket_event_broadcaster`.
- **The QA loop refines once.** `max_iterations=2`, so the writer
  re-runs at most one time after a rejection.
- **Constraint compliance is checked but not blocking.** Word-count
  violations get logged as `STRICT MODE VIOLATION` if `strict_mode` is
  on, but the result still flows through to `awaiting_approval`.

## Configuration

Reads no `app_settings` keys directly. All of its tunables are
hardcoded module constants:

- `RESEARCH_TIMEOUT_S` / `DRAFT_TIMEOUT_S` / `QA_TIMEOUT_S` /
  `REFINEMENT_TIMEOUT_S` / `FORMATTING_TIMEOUT_S` — per-stage
  asyncio timeouts.
- `max_iterations = 2` — QA refinement loop cap, defined inline in
  `_run_qa_stage`.
- Default constraints (`word_count=1500`, tolerance=10%, style from
  request) — defined inline in `_handle_content_creation`.

Indirect config flows through the agents it instantiates
(`research_agent`, `creative_agent`, `qa_agent`,
`image_agent`/`blog_image_agent`, `publishing_agent`/
`blog_publisher_agent`); those each read their own settings.

## Dependencies

- **Reads from:**
  - `agents.registry.get_agent_registry` — first-choice agent lookup.
  - Hardcoded `agent_mapping` dict in `_get_agent_instance` — fallback
    direct-import path.
  - Injected `database_service` (if provided) — but not actually used
    anywhere except a no-op `_store_execution_result` stub.
  - `services.websocket_event_broadcaster.emit_task_progress` —
    progress events at fixed checkpoints.
  - `services.writing_style_integration.WritingStyleIntegrationService` —
    fetches sample-derived style guidance during the draft stage.
  - `services.quality_service.get_content_quality_service` — reused
    in the QA loop with the injected `database_service` so each
    request doesn't open a new connection pool (issue #783).
  - `services.image_service.get_image_service` — for featured image
    selection.
- **Writes to:**
  - Nothing in production (the class isn't instantiated). The
    `_store_execution_result` stub on the class only logs;
    persistence would need to be implemented if this were wired in.
- **External APIs:** none directly — all flow through the agents.

## Failure modes

- **Agent not in registry AND not in fallback mapping** — raises
  `ValueError("Unknown agent: '<name>'. Not in registry or fallback
mapping.")`. Add the mapping in `_get_agent_instance` if you need a
  new agent type.
- **Stage timeout** — `_run_research_stage` swallows the timeout and
  continues with empty research. Every other stage re-raises as
  `TimeoutError(f"<stage> timed out after Ns")`, which propagates to
  `_handle_content_creation`'s `except Exception` and returns a
  `FAILED` `ExecutionResult`.
- **Writing-style sample lookup fails** — caught and logged as a
  warning; the draft stage proceeds without style guidance.
- **Image stage fails** — caught and logged; `featured_image_url`
  stays `None` and the pipeline continues.
- **Financial / compliance handlers with no agent injected** —
  return `ExecutionResult(status=FAILED,
output="<Type> agent not available")`. There is no fallback or LLM
  reasoning path.
- **The intervention / system_operation / decision_support handlers
  are stubs** — they always return `ExecutionStatus.COMPLETED` with a
  templated string. Don't trust them for real intervention semantics.

## Common ops

- **Verify it's still unwired:**
  `grep -rn "UnifiedOrchestrator(" src/cofounder_agent --include="*.py" | grep -v tests`
  should return zero hits.
- **Run the unit tests:**
  `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_unified_orchestrator.py -q`
- **Decide its fate.** Either:
  - **Wire it in** — instantiate in `main.py` lifespan, set
    `app.state.orchestrator`, route `/api/orchestrate` (or similar)
    to its `process_request`, migrate the hardcoded timeouts and
    `max_iterations` into `app_settings`, and replace the stub
    handlers (intervention, decision_support) with real
    implementations.
  - **Delete it** — clear out the stale comments in `main.py:88` and
    `utils/startup_manager.py:333,350,363`, drop the file and its
    test. The content router already covers the only branch that has
    a real implementation.

## See also

- `docs/architecture/services/content_router_service.md` — the
  canonical content pipeline, which is what production actually runs.
- `services.orchestrator_types` — the `Request`, `RequestType`,
  `ExecutionResult`, `ExecutionStatus` dataclasses + enums consumed
  by this class.
- `services.task_executor` — the worker loop that dispatches queued
  `content_tasks`. It bypasses `UnifiedOrchestrator` entirely and
  calls `process_content_generation_task` directly.
- `~/.claude/projects/C--Users-mattm/memory/feedback_db_first_config.md` —
  the project policy that says these hardcoded constants should be
  in `app_settings` if this class ever ships.
