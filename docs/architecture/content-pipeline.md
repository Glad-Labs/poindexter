# Content pipeline — Stage architecture

**Last updated:** 2026-04-21
**Status:** Current. Replaces `multi-agent-pipeline.md` (2026-03-10,
pre-Phase-E refactor).
**Source of truth:** `src/cofounder_agent/plugins/stage_runner.py`,
`src/cofounder_agent/plugins/registry.py`,
`src/cofounder_agent/services/content_router_service.py`,
`src/cofounder_agent/services/stages/`.

> **Why this doc exists.** Historically Poindexter's pipeline was a
> 2,700-line inline loop inside `content_router_service.py`. Phase E
> (tracked at [GH-69](https://github.com/Glad-Labs/poindexter/issues/69))
> broke that monolith into twelve self-contained Stages, each behind
> a Protocol. This document describes the current shape of the
> pipeline — not the historical 6-agent model nor the future
> plugin-marketplace vision. For the long-range plugin roadmap see
> [`plugin-architecture.md`](./plugin-architecture.md).

---

## High-level flow

A content task follows this path from submission to "ready for
human approval":

```text
POST /api/tasks
       │
       ▼
┌────────────────────────┐
│ task row inserted      │   status = 'pending'
│ (content_tasks)        │
└────────────────────────┘
       │
       ▼ (5s poll, FOR UPDATE SKIP LOCKED)
┌────────────────────────┐
│ TaskExecutor picks it  │   status → 'in_progress'
└────────────────────────┘
       │
       ▼
┌────────────────────────────────────────────────────────────┐
│ process_content_generation_task (content_router_service.py)│
│                                                            │
│ Builds shared context dict → hands it to StageRunner       │
│ Stages run in 5 "chunks" with GPU-mode switches in between │
└────────────────────────────────────────────────────────────┘
       │
       ▼
┌────────────────────────┐
│ 12 Stages run in order │   each stage reads/writes shared
│ (see section below)    │   context; halts on failure by default
└────────────────────────┘
       │
       ▼
┌────────────────────────┐
│ status set by          │   'awaiting_approval'  (quality passed)
│ finalize_task stage    │   'rejected'           (QA rejected)
│                        │   'failed'             (stage crashed)
└────────────────────────┘
```

The entry point is `process_content_generation_task` at
`src/cofounder_agent/services/content_router_service.py:62`. The
stages themselves live one file per stage under
`src/cofounder_agent/services/stages/`.

---

## The twelve Stages

Default order (from `StageRunner.DEFAULT_STAGE_ORDER` in
`src/cofounder_agent/plugins/stage_runner.py:140`):

| #   | Stage name               | Purpose                                        | Halts pipeline on failure? |
| --- | ------------------------ | ---------------------------------------------- | -------------------------- |
| 1   | `verify_task`            | Confirm task row exists and is not a duplicate | yes                        |
| 2   | `generate_content`       | Write the draft with the configured writer LLM | yes                        |
| 3   | `writer_self_review`     | Writer re-reads its own draft, applies edits   | no                         |
| 4   | `quality_evaluation`     | Programmatic validator scores the draft        | yes                        |
| 5   | `url_validation`         | Verify external links resolve (HEAD checks)    | no                         |
| 6   | `replace_inline_images`  | Plan image insertion points (Ollama, planning) | no                         |
| 7   | `source_featured_image`  | SDXL generation or Pexels fallback (GPU: SDXL) | no                         |
| 8   | `cross_model_qa`         | Adversarial review + up to N rewrite attempts  | yes, may mark rejected     |
| 9   | `generate_seo_metadata`  | Title, meta description, keywords              | no                         |
| 10  | `generate_media_scripts` | Podcast script + video slides text             | no                         |
| 11  | `capture_training_data`  | Store embeddings in pgvector for future RAG    | no                         |
| 12  | `finalize_task`          | Set `status=awaiting_approval` + emit webhooks | yes                        |

The order list is **DB-configurable**: operators can reorder, add,
or remove stages by writing a JSON array to the `app_settings` key
`pipeline.stages.order`. A stage referenced in the order list that
isn't registered is skipped with a log line, not a crash — which
means operators can stage the order change before a new Stage
plugin has been deployed.

### Per-stage config (`plugin.stage.<name>`)

Each stage has a row in `app_settings` named
`plugin.stage.<stage_name>` whose value is a JSON blob read through
`plugins.config.PluginConfig`. The runner honors three fields:

- `enabled` (default `true`) — set to `false` to skip the stage
  entirely without removing it from the order list.
- `timeout_seconds` — per-invocation deadline enforced with
  `asyncio.wait_for`. Default comes from the stage's
  `timeout_seconds` attribute, falling back to 120s.
- `halts_on_failure` (default `true`) — if `false`, a failing stage
  logs the error and the runner continues. Stages marked "no" in the
  halt column above set this attribute at the class level.

Arbitrary per-stage config (model overrides, reviewer weights,
custom thresholds) goes under the same key. Stages read it via the
`config` argument of their `execute()` method.

Example: disable `generate_media_scripts` for an install that
doesn't use the podcast feature:

```sql
UPDATE app_settings
SET value = '{"enabled": false}'
WHERE key = 'plugin.stage.generate_media_scripts';
```

No redeploy. The runner re-reads config on every pipeline
invocation.

---

## Chunked execution

`process_content_generation_task` does not hand all twelve stages
to the runner in one call. It runs them in five chunks so it can
insert GPU-mode switches and audit events between groups
(`src/cofounder_agent/services/content_router_service.py:148-293`):

1. **Chunk 1 — Bootstrap.** `verify_task`, `generate_content`.
   After this chunk, the task has a draft and a chosen writer
   model. If the configured writer model differs from the one that
   actually produced text (Ollama silently fell back due to VRAM),
   a `writer_fallback` audit event fires — the symptom of the
   72B-writer trap described in the `72B Writer Infeasible` memory.
2. **Chunk 2 — Early QA.** `writer_self_review`, `quality_evaluation`,
   `url_validation`, `replace_inline_images`. This is the cheap
   programmatic review — running the local validator, HEAD-checking
   external URLs, planning inline image placements. `quality_evaluation`
   halting the pipeline means the draft was unsalvageably bad; the
   content_router surfaces it as a `RuntimeError` so the executor
   marks the task `failed`.
3. **Chunk 3 — GPU switch + image generation.** The orchestrator
   flips the GPU scheduler into SDXL mode, runs `source_featured_image`
   (which may call SDXL on port 9836 or fall back to Pexels), then
   switches back to Ollama mode. These switches are non-fatal — if
   the GPU scheduler isn't available the stages still run, just
   with worse performance.
4. **Chunk 4 — Cross-model QA + rewrite loop.** `cross_model_qa`
   alone. This is the expensive adversarial review: an LLM critic
   (different model family than the writer) reads the draft and
   scores it. Up to `qa_max_rewrites` (default 2) rewrite attempts
   if the critic rejects for fixable reasons. If QA rejects
   definitively, the stage returns `continue_workflow=False` and the
   task's `status` flips to `rejected`. The runner reports this via
   `StageRunSummary.halted_at == "cross_model_qa"` and the
   orchestrator early-returns, skipping SEO / finalize.
5. **Chunk 5 — Publish prep.** `generate_seo_metadata`,
   `generate_media_scripts`, `capture_training_data`, `finalize_task`.
   Only runs if QA approved. `finalize_task` is what actually moves
   the task into `awaiting_approval`; any halt in this chunk
   escalates to a `RuntimeError`.

Chunking exists because the GPU cannot run Ollama and SDXL
simultaneously on Matt's 32GB 5090 — the scheduler serializes
them. Splitting the stage list lets the orchestrator preempt with
`gpu.prepare_mode("sdxl")` between chunks. The chunking is
_not_ part of the Stage Protocol; it's an orchestration detail of
the content pipeline. A hypothetical pipeline that doesn't need
image generation could run all twelve stages in one
`runner.run_all` call.

---

## The Stage Protocol

From `src/cofounder_agent/plugins/stage.py`:

```python
@runtime_checkable
class Stage(Protocol):
    name: str
    description: str

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        ...


@dataclass
class StageResult:
    ok: bool
    detail: str
    context_updates: dict[str, Any] = field(default_factory=dict)
    continue_workflow: bool = True
    metrics: dict[str, Any] = field(default_factory=dict)
```

Stages are ordinary Python objects with a `name`, a one-line
`description`, and an async `execute`. The contract is narrow on
purpose — a stage takes the shared context, does its work, and
returns a `StageResult`.

**Context reads and writes.** Each stage documents in its module
docstring which keys it reads from `context` and which it writes
back. For example, `generate_content` reads `topic`, `style`,
`tone`, `target_length`, `tags`, `models_by_phase`,
`database_service`, `site_config`; it writes `content`, `content_length`,
`title`, `model_used`. The runner merges `StageResult.context_updates`
into the shared context after each stage. See
`src/cofounder_agent/services/stages/generate_content.py` for the
canonical shape.

The context dict also carries shared services that stages would
otherwise reach for as module-level singletons (Phase H, GH#95):

- `site_config` — the `SiteConfig` instance from `app.state.site_config`
- `image_service` — the `ImageService` dispatcher
- `settings_service` — `SettingsService` for app_settings
- `image_style_tracker` — `ImageStyleTracker` rotation state
- `database_service` — the `DatabaseService` coordinator

These are seeded once by `process_content_generation_task` when it
builds the initial context, so every stage reads from the same
DB-loaded instances. Stages should never do
`from services.site_config import site_config` — that module-level
attribute was removed.

**Early exit (`continue_workflow=False`).** A stage that has
succeeded but wants to stop the pipeline (e.g. cross-model QA that
rejected content — no point continuing to SEO) returns
`ok=True, continue_workflow=False`. The runner treats that as a
halt; the orchestrator reads `summary.halted_at` and decides what
to do.

**Specialized sub-protocols.** `Stage` has three narrower variants
used by later phases of the plugin refactor:

- `Reviewer(Stage)` — adds `score_dimensions: list[str]`. Examples
  targeted for Phase E cleanup: programmatic validator, LLM critic,
  SEO checker, URL verifier.
- `Adapter(Stage)` — adds `platform: str` and
  `requires_credentials: list[str]`. The existing social adapters
  under `services/social_adapters/` (Bluesky, LinkedIn, Mastodon,
  Reddit, YouTube) will be ported to this Protocol.
- `Provider(Stage)` — adds `media_type: str` and `model: str`.
  Image generators (SDXL, Pexels, future Midjourney/Flux) fit here.

None of the currently-registered stages implement the narrower
Protocols yet — that's a follow-up inside Phase E. The base
`Stage` Protocol is enough to run the current pipeline.

---

## StageRunner

From `src/cofounder_agent/plugins/stage_runner.py:161`:

```python
class StageRunner:
    def __init__(self, pool: Any, stages: list[Any]): ...
    def registered_names(self) -> list[str]: ...
    async def run_all(
        self,
        context: dict[str, Any],
        order: list[str] | None = None,
    ) -> StageRunSummary: ...
```

The runner owns three things the Stage Protocol doesn't: **ordering,
config lookup, and timeout enforcement.**

Instantiation gets a DB pool and the list of registered `Stage`
objects:

```python
from plugins.registry import get_core_samples
from plugins.stage_runner import StageRunner

runner = StageRunner(
    database_service.pool,
    get_core_samples().get("stages", []),
)
```

`get_core_samples()` is a temporary imperative loader — Phase E
packaging is tracked in follow-up work. Once
`pip install -e .` is the installation path, stages register via
`pyproject.toml` entry_points under `poindexter.stages` and the
runner discovers them via `importlib.metadata.entry_points`.

### Return shape (`StageRunSummary`)

```python
@dataclass
class StageRunSummary:
    ok: bool
    halted_at: str | None = None
    records: list[StageRunRecord] = field(default_factory=list)
```

Every stage run produces a `StageRunRecord` with `name`, `ok`,
`detail`, `skipped`, `halted`, `elapsed_ms`, and any `metrics` the
stage emitted. The orchestrator uses `halted_at` to decide whether
to early-return; brain daemon / audit logs use `to_dict()` to
persist a full trace of what ran and how long.

### Timeouts

Every stage call is wrapped in `asyncio.wait_for(timeout)`. The
timeout comes from (in order of precedence):

1. `plugin.stage.<name>.timeout_seconds` in `app_settings`.
2. The stage's class-level `timeout_seconds` attribute.
3. 120 seconds (runner default).

When a stage times out, the runner logs it, records the failure in
the summary, and either halts or continues based on the stage's
`halts_on_failure` setting. This is why the
[troubleshooting doc](../operations/troubleshooting.md)'s
"Pipeline task stuck in_progress" entry mostly no longer reproduces
— every external call now sits inside a stage with a finite
deadline.

---

## Observability

Each stage run surfaces on three channels:

1. **Logs.** `logger.info("stage_runner: %r ...", name)` for start /
   skip / disabled events; `logger.exception(...)` for raised
   exceptions and timeouts. Request-ID propagation is a Phase D
   deliverable.
2. **Audit log.** `process_content_generation_task` emits
   `pipeline_complete` or stage-specific events (e.g.
   `writer_fallback`, `qa_passed`, `qa_failed`) through
   `audit_log_bg` to the `admin_logs` table. The brain daemon
   reads those for incident detection.
3. **Cost log.** Stages that call an LLM write a row into
   `cost_logs` with tokens + estimated electricity cost. Grafana's
   Cost Tracking dashboard aggregates this per-stage.

There are no OpenTelemetry traces yet — that's deferred to Phase I
(see the "Deferred to Phase I" section of
[`plugin-architecture.md`](./plugin-architecture.md)).

---

## Operator playbooks

### "Where did my task get stuck?"

```sql
SELECT task_id, status, stage, updated_at,
       LEFT(topic, 60) AS topic
FROM content_tasks
WHERE status IN ('in_progress', 'failed')
ORDER BY updated_at DESC
LIMIT 20;
```

The `stage` column is updated by stages that write it to their
`context_updates`. If a task is `in_progress` and the `stage` hasn't
advanced in >5 minutes, the worker logs
(`docker logs poindexter-worker | grep <task_id>`) will show which
stage is running. Match it to the per-stage timeout; if the
timeout hasn't fired yet, the stage is still executing within its
budget.

### "Re-run just the SEO stage on an already-generated task"

Not directly supported — stages are not individually re-entrant
today. The supported workaround is
`POST /api/tasks/{task_id}/regenerate` which re-runs the full
pipeline on the existing topic. Per-stage re-entry is a candidate
for the Phase E follow-up work.

### "Temporarily bypass cross-model QA for a batch"

```sql
UPDATE app_settings
SET value = '{"enabled": false}'
WHERE key = 'plugin.stage.cross_model_qa';
```

All new tasks will skip the critic stage. Existing tasks in flight
are unaffected. Remember to re-enable after the batch; otherwise
the programmatic validator is your only QA gate and quality will
regress.

### "Add a custom stage"

Until the packaging fix lands, core stages register imperatively
inside `plugins/registry.py::get_core_samples`. A custom stage
today lives alongside the existing ones under
`src/cofounder_agent/services/stages/my_stage.py`, then gets added
to the `_SAMPLES` list in the registry. After the packaging fix,
this becomes a `pip install` of a separate package plus an entry
under `[project.entry-points."poindexter.stages"]` in its
`pyproject.toml` — no edits to core required. See the worked
example at the bottom of
[`plugin-architecture.md`](./plugin-architecture.md#plugin-discovery-setuptools-entry_points).

---

## Related documentation

- [ARCHITECTURE.md](../ARCHITECTURE.md) — system overview, the 7
  principles, tech stack.
- [plugin-architecture.md](./plugin-architecture.md) — long-range
  plugin design; Stage / Reviewer / Adapter / Provider split;
  Phase E / F / G / J roadmap.
- [database-schema.md](./database-schema.md) — the `content_tasks`
  table and its surrounding schema.
- [../operations/local-development-setup.md](../operations/local-development-setup.md)
  — setup walkthrough, includes a "submit a task end-to-end" curl.
- [../operations/troubleshooting.md](../operations/troubleshooting.md)
  — pipeline symptoms and fixes for issues that have hit
  production.
- [../api/README.md](../api/README.md) — REST surface,
  `/api/tasks` creation, approval, and rejection endpoints.
