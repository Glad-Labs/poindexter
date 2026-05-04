# Dynamic pipeline composition — LangGraph + capability-tiered atoms

**Status**: design — Day-1 lift in progress, full architecture phased.
**Author**: Claude (with Matt) — 2026-05-04, revised 2026-05-04 evening.
**Motivation**: dev_diary saga 2026-05-04. Repeated per-stage bypasses (`if writer_rag_mode == "DETERMINISTIC_COMPOSITOR": skip`) revealed that the canonical 12-stage blog pipeline doesn't fit non-blog artifacts. The fix isn't more bypasses — it's making pipelines first-class composable structures driven by intent, not procedural code paths.

**Scope frame**:

- **v1 (Phase 1, "Day 1") = proof-of-concept.** Lift LangGraph as the runner; hand-code 3 templates; ship dev_diary correctly. Minimum viable, no atom refactor, no architect, no streaming, no cost projection. Just enough to validate the loop and unblock dev_diary.
- **v2 (Phases 2-5) = production-grade.** Every cross-cutting concern below — error/retry/fallback, capability tiers, outcome feedback, approval gates, atom granularity, architect LLM, observability, cost projection, streaming, replay, permissions — designed in now so v1 doesn't paint v2 into a corner. Implementation is phased; design constraints are not.

## Vision

Poindexter is an autonomous content + business-ops system. Matt's interaction model: **high-level intent in, finished artifact out, single approve/reject decision**. The system handles all composition, model selection, error recovery, and observability autonomously. No drag-drop UI, no field config, no per-task hand-holding (`project_operator_high_level_only` memory note).

Eventually this extends past content creation to all aspects of the business: customer support, financial analysis, marketing, newsletter strategy, etc. The same orchestration layer drives all of it; only the block catalog grows.

The architecture target is **three decoupled layers** running on **LangGraph + Langfuse + the existing model_router** as the orchestration backbone:

```
┌────────────────────────────────────────────────────┐
│  ATOMS (block catalog)                             │ ← what action to take
│  generate_text, review_content, embed_text,        │
│  verify_url, update_task_status, send_telegram     │
│  Each declares: I/O schema, capability tier,       │
│  retry policy, version, cost class                 │
├────────────────────────────────────────────────────┤
│  ROUTERS (model_router et al)                      │ ← cost/quality/A-B policy
│  capability tier → concrete (provider, model)      │
│  DB-driven, experiment-aware, outcome-tuned        │
├────────────────────────────────────────────────────┤
│  PROVIDERS (LLM/image/audio/TTS/video plugins)     │ ← concrete implementation
│  Anthropic, Gemini, Ollama, SDXL, Flux, etc.       │
│  Already Protocol-shaped from v2.x refactor        │
└────────────────────────────────────────────────────┘
              ↑
              │ atoms composed into…
              │
┌─────────────┴──────────────────────────────────────┐
│  TEMPLATES (LangGraph StateGraphs)                 │
│  Phase 1: hand-coded Python functions              │
│  Phase 2+: cached architect-LLM compositions       │
└────────────────────────────────────────────────────┘
              ↑
              │ resolved by…
              │
┌─────────────┴──────────────────────────────────────┐
│  TEMPLATE RESOLVER                                 │
│  Phase 1: artifact_type → named template lookup    │
│  Phase 2+: architect LLM composes from intent      │
└────────────────────────────────────────────────────┘
              ↑
              │ invoked by…
              │
┌─────────────┴──────────────────────────────────────┐
│  INPUT LAYERS (CLI, MCP, REST, Telegram, cron,     │
│  brain) — each emits an intent. None contain       │
│  orchestration logic.                              │
└────────────────────────────────────────────────────┘
```

## Why this NOW

Three signals stacking up:

1. **Bypass debt is growing.** `cross_model_qa.py` + `task_executor.py` both grew compositor-specific bypasses today. Each new artifact type accumulates its own.
2. **Niche-specific overrides have hit a wall.** `niches.writer_prompt_override` + `niches.writer_rag_mode` differentiate behaviour at the writer stage only. Everything else is identical for every task — but a dev diary doesn't need SEO metadata, a social post doesn't need a featured image, a podcast doesn't need writer_self_review.
3. **Matt's intent_routing memory note has been there for weeks.** "System reads intent and assembles business processes dynamically, like how Claude/OpenClaw route skills." This is the bottoms-up technical scope to deliver it.

## Existing primitives — already Protocol-shaped

The good news: most building blocks already exist as Python Protocols. They just aren't unified under capability-tier abstractions, don't expose I/O metadata, and aren't composable.

| Primitive                     | Protocol location                                             | Examples (today)                                                                     |
| ----------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Stage                         | `plugins/stage.py`                                            | verify_task, generate_content, cross_model_qa, finalize_task (12 in canonical order) |
| Writer mode                   | `services/writer_rag_modes/__init__.py::dispatch_writer_mode` | TOPIC_ONLY, CITATION_BUDGET, STORY_SPINE, TWO_PASS, DETERMINISTIC_COMPOSITOR         |
| Topic source                  | `plugins/topic_source.py` Protocol                            | dev_diary_source, niche_batch sources, internal_rag_source                           |
| LLM provider                  | `plugins/llm_provider.py` Protocol                            | Anthropic, Gemini, Ollama (and OAI-compat fallbacks)                                 |
| Image provider                | `plugins/image_provider.py` Protocol                          | sdxl, pexels, flux_schnell                                                           |
| Audio / video / TTS providers | `plugins/{audio,video,tts}_provider.py` Protocol              | Several, all v2.x refactor                                                           |
| QA gate                       | `qa_gates` table (declarative rules)                          | citation_verifier, internal_consistency, topic_delivery, url_verifier, web_factcheck |
| Object store                  | `object_stores` table (declarative)                           | r2_public, r2_private, etc.                                                          |
| Model router                  | `services/model_router.py`                                    | tier-based selection (`free`/`budget`/`standard`/`premium`)                          |

Already running at the leaf level: **LangGraph** (in `writer_rag_modes/two_pass.py`), **Langfuse** (in `scripts/import_prompts_to_langfuse.py`).

The lift: take what's already at leaves and elevate it to the orchestration backbone.

## Atom design — get this right

Every atom is one observable outcome, one failure mode, one model/tool call (or one programmatic computation), no internal conditional branches. Branching becomes graph edges, not internal logic.

### Capability tier abstraction

Atoms declare WHAT KIND of model they need, not which model. The router resolves at execution time:

```python
# WRONG — bakes in provider
ollama_generate(prompt, model="glm-4.7-5090") -> str

# RIGHT — capability declaration, router-resolved
generate_text(prompt, *, tier="serious_writer", schema=None) -> Result
review_content(content, *, tier="cheap_critic") -> Review
generate_image(prompt, *, tier="featured_image") -> ImageResult
embed_text(text, *, tier="default_embed") -> Vector
```

Tiers are coarse semantic categories — `cheap_critic`, `serious_writer`, `vision_critic`, `featured_image`, `inline_explainer`, `default_embed`, `narrator_male`, etc. Each tier maps to a concrete (provider, model) via `model_router`, with experiment-aware A/B routing and DB-driven config.

A/B testing falls out for free: `poindexter experiments create cheap_critic claude-haiku gemini-2.5-flash --traffic 50/50`. Outcomes (quality_score, cost, latency, approval_rate) get logged per variant. Statistical winner gets traffic.

### Atom contract

```python
@dataclass(frozen=True)
class AtomMeta:
    name: str                          # "atoms.generate_text"
    version: str                       # semver: "1.0.0"
    description: str                   # one-liner for human + architect-LLM
    inputs: type[BaseModel]            # pydantic model — what the atom reads
    outputs: type[BaseModel]           # pydantic model — what it produces
    capability_tier: str | None        # "serious_writer", None for non-LLM atoms
    cost_class: Literal["free", "compute", "api", "premium"]
    idempotent: bool
    side_effects: list[str]            # human-readable: "writes to pipeline_versions"
    retry: RetryPolicy                 # max attempts, backoff, on which exception types
    fallback: list[str] | None         # capability tier chain on failure: ["primary", "budget", "free"]
    parallelizable: bool               # can multiple instances run concurrently
```

Block discovery: at startup, walk the plugin registry and collect `atom_meta()` from every registered class. Source of truth is the Python class; `pipeline_atoms` table is a write-through cache for the architect LLM to query without importing Python.

### Granularity sample

What today's coarse stages decompose into:

| Coarse stage today      | Atoms after refactor                                                                                                         |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `generate_content`      | `embed_query`, `fetch_top_n_similar`, `compose_writer_prompt`, `generate_text`, `extract_topic_from_draft`                   |
| `cross_model_qa`        | `programmatic_validator`, `claude_haiku_review`, `internal_consistency_review`, `aggregate_reviewer_scores`, `rewrite_draft` |
| `replace_inline_images` | `parse_image_markers`, `generate_image`, `swap_image_in_content`                                                             |
| `generate_seo_metadata` | `generate_seo_title`, `generate_seo_description`, `extract_seo_keywords`                                                     |
| `finalize_task`         | `update_task_status`, `emit_webhook`, `send_telegram_notification`                                                           |

Coarse stages become **named sub-templates** — first-class LangGraphs composed of atoms. Sub-templates ARE templates, just with smaller scope. The architect can compose at any level: drop in a sub-template when the pattern fits, assemble atoms directly when novel.

## Templates

A template is a named, versioned LangGraph `StateGraph`. Phase 1 templates are hand-coded Python functions:

```python
def build_dev_diary_template() -> StateGraph:
    g = StateGraph(DevDiaryState)
    g.add_node("gather", gather_bundle_atom)
    g.add_node("narrate", generate_text_atom)        # tier="dev_diary_narrator"
    g.add_node("image", generate_image_atom)          # tier="featured_image"
    g.add_node("persist", finalize_task_atom)
    g.set_entry_point("gather")
    g.add_edge("gather", "narrate")
    g.add_conditional_edges("narrate", lambda s: "image" if s["bundle"].has_real_signal else "persist")
    g.add_edge("image", "persist")
    return g.compile(checkpointer=PostgresCheckpointer(pool))
```

Phase 2+ templates are produced by the architect LLM from the live atom catalog and cached as named entries.

Storage:

```sql
CREATE TABLE pipeline_templates (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug        text UNIQUE NOT NULL,    -- "canonical_blog", "dev_diary"
  name        text NOT NULL,
  description text,
  version     int NOT NULL DEFAULT 1,
  active      bool NOT NULL DEFAULT true,
  graph_def   jsonb,                   -- Phase 2: serialized DAG; null in Phase 1 (Python-defined)
  python_factory text,                 -- Phase 1: importable function path
  created_by  text,                    -- "system", "operator", "architect_llm"
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE pipeline_tasks
  ADD COLUMN template_slug text;       -- nullable for backwards compat
```

## Cross-cutting concerns

Each is a property of atoms or graphs, declared not coded ad-hoc.

### Error handling, retry, fallback

Atom declares `retry: RetryPolicy` (max attempts, backoff strategy, retryable exception types). On exhaustion, atom declares one of: `block` (fail the graph), `skip` (continue, mark output as missing), `escalate` (pause graph + notify operator). Capability tier `fallback` chain handles "primary model unavailable, walk down": `["serious_writer", "budget_writer", "free_writer"]`.

### Approval gates

A first-class atom: `approval_gate(message, gate_name, context)`. Uses LangGraph's `interrupt` mechanism — pauses graph, sends Telegram with the gate context, waits for response, resumes. Maps to existing `gates` schema. Templates can have multiple approval points (draft OK → image OK → publish OK).

### Observability

- **Langfuse** for LLM-call tracing — built-in LangGraph integration, free with the lift.
- **Trace ID** threaded through the graph context so non-LLM atoms (DB writes, image gen, programmatic logic) attach to the same trace.
- **Cost attribution per atom** logged to `cost_logs` with the trace ID + atom name. Per-graph cost is a sum.
- **Structured logging**: every atom emits `[ATOM:<name>] start/end <args>` with the trace ID.

### A/B testing + outcome feedback loop

Existing `experiments` table + `model_router` already supports tier-level experiments. New: outcome callback. After a task lands at `awaiting_approval` and the operator approves/rejects, the outcome flows back to the router as a learning signal. Architect-LLM compositions get the same treatment: successful templates accumulate weight, failed compositions get penalized.

### Cost projection

Before a graph runs, estimate total cost from atom × tier × router weights. Compare to `daily_budget_remaining`. If projected cost exceeds budget, surface to operator with the cost breakdown. If under, run silently.

### Resource gating

Existing `gpu_scheduler` already gates Ollama calls. Lift it to the atom-execution layer so all GPU users go through one queue. Same shape for cloud-API rate limits per provider.

### Block versioning

Atoms version with semver. Templates pin a specific atom version OR use `latest`. When an atom's behaviour changes (e.g. validator rule update), bump the version; old templates keep working with the pinned version, new templates use `latest`.

### Streaming + intermediate results

LangGraph supports streaming. Operator gets Telegram updates as the graph progresses ("draft started... draft done... QA running... awaiting approval"). Avoids the silent 5-minute wait pattern. Phase 2.

### Replay + resume

LangGraph's Postgres checkpointer makes graphs resumable. If a graph fails mid-execution, operator can resume after fix. If operator rejects a result, replay with different config (e.g. different model, different writer mode). Phase 3.

### Migration / backward compat

Feature-flagged. Tasks with `template_slug = NULL` continue running on legacy `StageRunner`. Tasks with `template_slug` set use the new `TemplateRunner` (LangGraph-based). Both coexist during transition. Legacy `app_settings.pipeline.stages.order` becomes one row in `pipeline_templates` (`canonical_blog`).

### Permission / sandbox model for ops blocks

Deferred until ops blocks ship. When `send_invoice` lives next to `generate_text`, they need different trust levels — but the only operator is Matt today, so this is post-product-creation work.

## Phased migration

### Day 1 (immediate need — gets dev_diary correct)

**Scope**: 1 day, ~1.5 with tests. Cuts maximal-spec scope by 80%. No atom refactor, no architect LLM, no capability-tier abstraction yet — those are real future work but NOT blocking dev_diary.

1. New `services/template_runner.py` — wraps LangGraph as the orchestration backbone.
2. Identity adapter: each existing stage becomes a LangGraph node with no internal change.
3. Three hand-coded templates as Python functions:
   - `canonical_blog` — all 12 stages (current default)
   - `dev_diary` — gather_bundle → narrate → persist (3 nodes)
   - `social_repurpose` — TBD when needed
4. `pipeline_tasks.template_slug` column. Default `canonical_blog`. Dev_diary INSERT sets `dev_diary`.
5. Switch dev_diary cron to use new TemplateRunner. Validate end-to-end.

Acceptance: dev_diary task produces a 2-3 paragraph narrative post via the dev_diary template (no QA, no curator, no SEO), canonical_blog tasks behave identically to today, no regressions.

### Phase 2 (capability-tier abstraction — 2-3 days)

6. `AtomMeta` dataclass + `atom_meta()` Protocol. Every primitive declares I/O, tier, retry policy, version.
7. Lift `model_router` to be the resolver for ALL capability tiers — embedding, image, audio, TTS, vision, in addition to text generation. Rename `model_router` → `capability_router` (alias the old name).
8. Outcome → router feedback loop: approval/rejection signals tune the experiment weights.
9. Stream intermediate results to operator via Telegram.

### Phase 3 (atom granularity refactor — 5-7 days)

10. Decompose coarse stages into atomic blocks (the table above). Each becomes its own file under `atoms/`.
11. Rebuild canonical_blog as a sub-template composed of atoms (validates the decomposition).
12. Approval gates as first-class atoms with LangGraph interrupt semantics.

### Phase 4 (architect LLM — 1-2 days)

13. `services/pipeline_architect.py`. Reads request + atom catalog → returns LangGraph composition (Claude Sonnet via Vercel AI Gateway, behind cost_guard).
14. Architect compositions cached as named templates after operator review.

### Phase 5 (extension to ops — incremental)

15. Add ops atoms: `query_stripe`, `send_invoice`, `post_to_discord`, `send_email_via_resend`, `query_sentry_issues`, etc.
16. Architect now composes pipelines beyond product creation.
17. Permission/sandbox model for ops atoms (deferred from earlier phases).

## What this OBSOLETES

- The 2026-05-04 compositor bypasses in `cross_model_qa.py` + `task_executor.py` — gone once dev_diary template ships in Day 1.
- The `niches.writer_rag_mode` column — replaced by `template_slug` + per-template config.
- The `app_settings.pipeline.stages.order` global — replaced by canonical_blog template.
- The "every fix needs to thread niche-specific config through 3 layers" pattern.
- The `services/writer_rag_modes/deterministic_compositor.py` module — replaced by the dev_diary template.

## What this DOES NOT change

- Existing primitives — Protocols stay the same, gain `atom_meta()` in Phase 2.
- Existing tasks while migration is in flight — they continue running on `StageRunner` until migrated.
- `qa_gates` declarative rules — already template-shaped, the new system uses them as atom config.
- The CLI / MCP / REST / Telegram / cron / brain input layers — they keep their existing surfaces and gain a single new "run intent" entry point. Thin wrappers around the architect (Phase 4+); direct template invocation works in Day 1.

## Open questions

1. **Per-edge config overrides** — should atom config live on the template node or be overridable per-task? Initial answer: template-level only; per-task overrides go through the architect-LLM path (request includes the override → architect produces a new derived template). Phase 4 concern.

2. **Conditional branches** — `add_conditional_edges` covers skip-or-execute. Do we need full if/else with diverging downstream paths? Initial answer: not yet — push back any case that needs it to v2 of the schema.

3. **Architect-LLM cost containment** — at ~$0.005-0.02 per architect call (Sonnet, ~3K input + ~1K output), 50 novel-request calls/day = ~$0.50/day. Cap at `architect_max_calls_per_day` setting; block + notify operator when exceeded.

4. **Sub-template composition vs flat atom graphs** — when the architect composes, should it nest sub-templates or always flatten? Probably depends on cost: flattened graphs are easier to optimize/observe, nested are easier for the architect LLM to reason about. Decide after Phase 4 prototyping.

5. **Multi-niche scoping for templates** — should a `dev_diary` template have niche-specific variants, or do all dev_diary-class tasks share one template with niche injected via context? Lean: shared template, context injection. Niche-specific atoms only when the divergence is structural (e.g. video pipeline vs text pipeline).

## Tracking

- Umbrella issue: TBD (file alongside this commit).
- Child issues:
  - Day 1: Lift LangGraph as orchestration backbone (template_runner.py)
  - Day 1: Wrap existing stages as LangGraph nodes (identity adapter)
  - Day 1: Hand-code canonical_blog + dev_diary + social_repurpose templates
  - Day 1: pipeline_tasks.template_slug column + dev_diary cron migration
  - Phase 2: AtomMeta + capability_router rename + outcome feedback loop + streaming
  - Phase 3: Atom granularity refactor (cross_model_qa, generate_content, etc.)
  - Phase 3: Approval gates as LangGraph interrupts
  - Phase 4: pipeline_architect.py LLM composition service
  - Phase 5: Ops atoms (Stripe, Resend, Discord, Sentry, etc.)
  - Phase 5: Permission/sandbox model for ops atoms
- Estimated total: **Day 1 ships in ~1 day**. Phases 2-3 add ~10 days for content. Phase 4 (architect) adds ~2 days. Phase 5 (ops) is incremental.
