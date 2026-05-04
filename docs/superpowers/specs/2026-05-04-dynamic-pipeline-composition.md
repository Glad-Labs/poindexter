# Dynamic pipeline composition + named templates

**Status**: design / scope — not yet implemented.
**Author**: Claude (with Matt) — 2026-05-04.
**Motivation**: dev_diary saga 2026-05-04. Repeated per-stage bypasses (`if writer_rag_mode == "DETERMINISTIC_COMPOSITOR": skip`) revealed that the canonical 12-stage blog pipeline doesn't fit non-blog artifacts. The fix isn't more bypasses — it's making pipelines first-class composable structures.

## Vision (one paragraph)

Poindexter should treat **stages, writer modes, topic sources, providers, and gates as building blocks** that the system composes into pipelines per task. Common request shapes — daily blog post, dev diary, social repurpose, podcast episode, newsletter digest — get **named templates** (DAGs of block invocations with per-edge config). Novel requests trigger a **composition engine** that calls an LLM "pipeline architect" with the block registry and produces a custom DAG, which is then cached as a new template if the operator approves it. Make.com / Zapier shape, but for AI content artifacts. Templates are the first-class abstraction; the current global `pipeline.stages.order` becomes one specific template (`canonical_blog`) among many.

## Why this NOW

Three signals stacking up over the last week:

1. **Bypass debt is growing.** `cross_model_qa.py` + `task_executor.py` both grew compositor-specific bypasses today. Each new artifact type will accumulate its own.
2. **Niche-specific overrides have hit a wall.** `niches.writer_prompt_override` + `niches.writer_rag_mode` differentiate behaviour at the writer stage only. Everything else (research, QA, image, SEO, finalize) is identical for every task — but a dev diary doesn't need SEO metadata generation, a social post doesn't need a featured image, a podcast doesn't need writer_self_review.
3. **Matt's intent_routing memory note.** "System reads intent and assembles business processes dynamically, like how Claude/OpenClaw route skills." The architecture target has been documented for weeks; this is the bottoms-up technical scope to deliver it.

## Existing primitives — already Protocol-shaped

The good news: most of the building blocks already exist as Python Protocols. They just aren't unified under a registry, don't expose metadata, and aren't composable.

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

Missing layer: a **block registry** that exposes metadata about each primitive (`inputs`, `outputs`, `requires`, `produces`, `cost_class`, `side_effects`) and a **template DAG** schema that composes them.

## Proposed architecture

### 1. Block metadata Protocol

Add a `BlockMeta` dataclass that every primitive returns from a `block_meta()` classmethod:

```python
@dataclass(frozen=True)
class BlockMeta:
    name: str                          # globally unique slug, e.g. "stage.cross_model_qa"
    type: Literal[
        "stage", "writer_mode", "topic_source",
        "llm_provider", "image_provider", "audio_provider",
        "video_provider", "tts_provider", "qa_gate", "object_store",
    ]
    description: str                   # one-liner for human + architect-LLM
    inputs: dict[str, FieldSpec]       # context keys read; required vs optional
    outputs: dict[str, FieldSpec]      # context keys produced
    requires: list[str]                # preconditions: "context_bundle", "draft", "preview_url"
    produces: list[str]                # artifacts: "draft", "image_url", "audio_file"
    cost_class: Literal["free", "compute", "api", "premium"]
    idempotent: bool                   # can re-run safely
    side_effects: list[str]            # human-readable list of writes / external calls
```

Block discovery: at startup, walk the plugin registry and collect `block_meta()` from every registered class. Store in `pipeline_blocks` table (write-through cache; Python introspection is the source of truth, the table is for the composition engine LLM to query without importing Python).

### 2. Pipeline Template DAG schema

A template is a named DAG of block invocations:

```yaml
# Stored in pipeline_templates.dag_jsonb
name: dev_diary_v3
slug: dev_diary
description: Daily what-we-shipped narrative post — bundle in, prose out, no SEO/QA/curator
version: 3
active: true
inputs:
  context_bundle: required # template-level inputs the caller must provide
  date: required
outputs:
  post_id: produced # what the template ultimately yields
nodes:
  - id: narrate
    block: writer_mode.bundle_narrator
    config:
      max_words: 250
      voice: third_person
      forbid: ["What you'll learn", 'Marek', 'daily.dev', 'kbir-dev']
    inputs:
      bundle: $.context_bundle
  - id: image
    block: image_provider.sdxl
    when: $.context_bundle.has_real_signal
    config:
      prompt_strategy: derive_from_first_pr_title
  - id: persist
    block: stage.finalize_task
    inputs:
      content: $.narrate.draft
      featured_image_url: $.image.url
edges:
  - narrate -> persist
  - image -> persist
```

Reference syntax: `$.<node_id>.<output_key>` for edge data, `$.<input_key>` for template-level inputs. Conditional execution via `when:` (a JMESPath/JSONLogic expression over the context).

### 3. Composition engine

```
┌───────────────────────────┐
│  task creation request    │
│  {type: dev_diary, ...}   │
└──────────────┬────────────┘
               │
               ▼
┌──────────────────────────┐
│  template_resolver       │
│  - match request.type    │
│    → named template      │
│  - else → architect LLM  │
└──────────────┬───────────┘
               │
               ▼
┌──────────────────────────┐
│  TemplateRunner          │
│  - topo-sort DAG nodes   │
│  - execute each block    │
│  - thread context        │
│  - eval `when:` clauses  │
│  - persist final outputs │
└──────────────────────────┘
```

For known request types: direct template lookup by `slug`. For novel ones: send the request + the block registry catalog to a cloud LLM (Claude Sonnet via Vercel AI Gateway — strict prompt-following, behind cost_guard) with a system prompt like "given this request and these blocks, output a DAG that satisfies the request. Use only blocks listed. No invented block names." Architect output → operator review → cache as new named template.

### 4. Storage schema

```sql
-- New: pipeline_blocks (block registry, write-through from Python)
CREATE TABLE pipeline_blocks (
  name        text PRIMARY KEY,        -- "stage.cross_model_qa", "writer_mode.two_pass", etc.
  type        text NOT NULL,
  meta        jsonb NOT NULL,          -- BlockMeta serialized
  active      bool NOT NULL DEFAULT true,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- New: pipeline_templates (named DAGs)
CREATE TABLE pipeline_templates (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug        text UNIQUE NOT NULL,    -- "canonical_blog", "dev_diary", "social_repurpose"
  name        text NOT NULL,
  description text,
  version     int NOT NULL DEFAULT 1,
  active      bool NOT NULL DEFAULT true,
  dag         jsonb NOT NULL,          -- template DAG definition
  created_by  text,                    -- "system", "operator", "architect_llm"
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Existing: pipeline_tasks gains a column
ALTER TABLE pipeline_tasks
  ADD COLUMN template_slug text;       -- nullable for backwards compat
```

The current `app_settings.pipeline.stages.order` becomes one row in `pipeline_templates` with `slug='canonical_blog'`. The legacy global key stays as a fallback for tasks with `template_slug = NULL`.

### 5. Migration plan

**Phase 1 — block registry (1-2 days)**

- Add `BlockMeta` dataclass + `block_meta()` Protocol method to each primitive type.
- Implement `block_meta()` on every existing primitive (stages, writer modes, topic sources, providers, gates).
- Wire `pipeline_blocks` table population at startup.
- CLI: `poindexter blocks list` for inspection.

Acceptance: every existing primitive returns a `BlockMeta` and lands in `pipeline_blocks`. No behaviour change.

**Phase 2 — template schema + canonical blog conversion (1 day)**

- Migration creates `pipeline_templates` table.
- Seed `canonical_blog` template from `DEFAULT_STAGE_ORDER`.
- New `TemplateRunner` class executes DAGs (topo-sort, `when:` eval, edge data).
- Legacy `StageRunner` keeps working; `TemplateRunner` is opt-in via task `template_slug`.

Acceptance: a task with `template_slug='canonical_blog'` runs via `TemplateRunner` and produces an identical post to one running via `StageRunner`. Side-by-side on dev_diary task with bundle.

**Phase 3 — dev_diary template (½ day)**

- Create `dev_diary` template that runs only `narrate` (bundle_narrator writer mode) + `image` (sdxl) + `persist` (finalize). No QA, no curator, no SEO, no media scripts.
- Update `run_dev_diary_post.py` to set `template_slug='dev_diary'` instead of relying on `writer_rag_mode='DETERMINISTIC_COMPOSITOR'`.
- Remove the per-stage bypasses added 2026-05-04 (cross_model_qa skip, auto_curator skip).

Acceptance: dev_diary task produces a 2-3 paragraph narrative post with a featured image, no QA rewriting, no auto-curator rejection. Bypasses gone from cross_model_qa.py + task_executor.py.

**Phase 4 — composition engine for novel requests (1-2 days)**

- New service: `pipeline_architect.py` — given a request + block catalog, returns a DAG via Claude Sonnet (cloud, behind cost_guard).
- Operator review UI: Grafana panel or Next.js page that shows architect's DAG for approval before caching.
- Architect-generated templates land in `pipeline_templates` with `created_by='architect_llm'`.

Acceptance: a request like `{type: 'changelog_email', commits_since: '2026-05-01'}` produces a DAG the architect composed from existing blocks (likely: gather_commits → narrate → email_format → send_via_resend), operator approves, template cached.

**Phase 5 — visual editor (later, not blocking)**

- Grafana panel or small Next.js page that renders templates as a visual DAG and allows drag-edit. Save → upserts the template row.
- Out of scope for the initial composition push.

### 6. Open questions

- **Per-edge config overrides** — should config live on the template node (`narrate.config`) or be overridable per-task? Initial answer: template-level only; per-task overrides go through the architect-LLM path (request includes the override → architect produces a new derived template).
- **Conditional branches** — `when:` covers skip-or-execute. Do we need full if/else with diverging downstream paths? Initial answer: not yet — push back any case that needs it to v2 of the schema.
- **Error recovery** — what happens when a node fails? Retry? Fall through? Initial answer: surface the failure to the operator (Telegram), pause the task at the failing node, allow manual `poindexter pipeline resume <task_id>`. No automatic retry without explicit `retry: { max: N, backoff: ... }` config on the node.
- **Backwards compatibility window** — how long does `StageRunner` (legacy) coexist with `TemplateRunner` (new)? Initial answer: until every existing task type has a template + every dispatch site sets `template_slug`. After that, drop StageRunner and `pipeline.stages.order`.
- **Architect-LLM cost containment** — at $0.005-0.02 per architect call (Sonnet, ~3K input + ~1K output), 50 novel-request calls/day = ~$0.50/day. Cap at `architect_max_calls_per_day` setting; block + notify operator when exceeded.

### 7. Tracking

- Umbrella issue: TBD (file after Matt sees this scope).
- Child issues: 5 (one per phase).
- Estimated total: **3-5 focused days** for phases 1-3 (the high-leverage refactor); phases 4-5 layer on later.

## What this OBSOLETES

- The 2026-05-04 compositor bypasses in `cross_model_qa.py` + `task_executor.py` — gone once dev_diary template exists.
- The `niches.writer_rag_mode` column — replaced by template_slug (writer mode becomes a per-template node config).
- The `app_settings.pipeline.stages.order` global — replaced by the canonical_blog template.
- The "every fix needs to thread niche-specific config through 3 layers" pattern.

## What this DOES NOT change

- Existing primitives — their Protocols stay the same, they just gain `block_meta()`.
- Existing tasks while migration is in flight — they continue running on `StageRunner` until migrated.
- `qa_gates` declarative rules — already template-shaped, the new system uses them as block config.
