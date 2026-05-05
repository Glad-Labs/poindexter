# Content Router Service

**File:** `src/cofounder_agent/services/content_router_service.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_content_router_service.py` (and any integration test that exercises a full pipeline run)
**Last reviewed:** 2026-04-30

## What it does

The content router is the single entry point for "given a topic, run the
whole content pipeline." It threads a shared `result` dict through a
sequence of stage plugins (`verify_task` → `generate_content` →
`writer_self_review` → `quality_evaluation` → `url_validation` →
`replace_inline_images` → `source_featured_image` → `cross_model_qa` →
`generate_seo_metadata` → `generate_media_scripts` →
`capture_training_data` → `finalize_task`), audits significant
transitions to `audit_log`, and persists the final state on
`content_tasks`. Stages communicate by reading and writing keys on the
shared `result` dict — there's no per-stage adapter layer.

The router also owns two cross-cutting concerns the stages can't see:
the GPU mode switch (Ollama → SDXL → Ollama) around the featured image
stage, and the writer-fallback canary that compares
`pipeline_writer_model` against the model the draft actually came back
with so a silent 72B → 27B downgrade is logged loudly instead of going
unnoticed.

## Public API

- `process_content_generation_task(topic, style, tone, target_length, ...) -> dict[str, Any]` —
  runs the full pipeline. Required: `database_service` (for
  `content_tasks` persistence). Optional: `task_id`,
  `generate_featured_image`, `models_by_phase`, `quality_preference`,
  `category`, `target_audience`, `tags`. Returns the shared `result`
  dict — see the `result["status"]` field for `pending`, `published`,
  `awaiting_approval`, `rejected`, or `failed`.

The function is the only public surface — there are no classes or
helper exports. Other modules call this via `task_executor` (worker
loop) or `/api/tasks/generate` (HTTP entry).

## Configuration

The router itself reads only one DB-configured setting directly:

- `pipeline_writer_model` (default: writer chain decides) — compared
  against the model that produced the draft to detect silent fallback.
- `pipeline_dry_run_mode` (default `false`) — when `true`, the writer
  chain short-circuits with `AllModelsFailedError`. The router demotes
  the resulting halt to a `dry_run_halt` audit event at `severity=info`
  instead of the usual `error` so it doesn't drown real failures.

Every other tunable lives on the individual stages — see
`docs/architecture/multi-agent-pipeline.md` and
`docs/architecture/anti-hallucination.md` for the per-stage settings.

## Dependencies

- **Reads from:**
  - `services.container.get_service("settings")` (DI seam, falls back to
    `None` outside lifespan)
  - `services.image_style_rotation.ImageStyleTracker`
  - `services.image_service.get_image_service`
  - `services.gpu_scheduler.gpu` for the SDXL/Ollama mode switch
  - `services.site_config.site_config` for the writer-fallback +
    dry-run checks
  - `plugins.registry.get_core_samples()` for the stage list
- **Writes to:**
  - `content_tasks` (status, error_message, task_metadata) via
    `database_service.update_task`
  - `audit_log` (multiple event_types: `task_started`,
    `generation_complete`, `qa_passed`/`qa_failed`,
    `pipeline_complete`, `dry_run_halt`, `error`, `writer_fallback`)
  - `webhook_events` indirectly via webhook delivery
    (`emit_webhook_event("task.failed", ...)` on failure). Earlier
    docs called this `pipeline_events`; the actual writer was always
    `webhook_events`. The unrelated `pipeline_events` table was
    dropped 2026-05-04 (poindexter#366).
- **External APIs:** none directly — stages own the LLM/HTTP calls.

## Failure modes

- **Stage halt before content exists** — `generate_content` returns
  `continue_workflow=False`. Router raises `RuntimeError` with the
  stage's `detail`; nothing further runs and the task is marked
  `failed`. Diagnose via the `error` audit event payload (full
  traceback in logs).
- **Stage halt after content exists** — `quality_evaluation` returns
  halt. Same pattern. The partially-generated content, image,
  and SEO metadata are preserved in `task_metadata` so an operator can
  still review what was produced.
- **Cross-model QA rejection** — `cross_model_qa` sets
  `result["status"] = "rejected"` and `continue_workflow=False`. Router
  early-returns the `result` dict cleanly; not raised as an error.
- **Silent writer fallback** — primary model timed out or 500'd, writer
  chain succeeded with the next model. Detected by comparing
  `pipeline_writer_model` to `result["model_used"]` after
  `generate_content`. Fires `writer_fallback` audit event at
  `severity=warning`. Visible on the /pipeline Grafana dashboard.
- **Dry-run halt** — when `pipeline_dry_run_mode=true`, the writer
  chain raises `AllModelsFailedError`. The router demotes the audit
  severity from `error` to `info` so the 24h error count isn't
  poisoned by intentional dry-run noise.

## Common ops

- **Re-run a failed task:** call
  `process_content_generation_task(...)` with the same `task_id`. The
  function tolerates re-entry; downstream stages handle idempotency
  (e.g. publish_service has a slug-suffix guard).
- **Toggle dry-run:** set `pipeline_dry_run_mode=true` via OpenClaw or
  `poindexter set` to short-circuit the writer chain without
  consuming GPU/cloud time.
- **Check writer-fallback events:**
  `SELECT * FROM audit_log WHERE event_type = 'writer_fallback' ORDER BY created_at DESC LIMIT 20;`
- **Disable featured-image stage for one task:** pass
  `generate_featured_image=False`. The stage runs but short-circuits.
- **Inspect what halted:** the failure path stores `error_stage` and
  `error_message` in `content_tasks.task_metadata`.

## See also

- `docs/architecture/multi-agent-pipeline.md` — stage-by-stage
  breakdown with the prompts and gates each stage owns.
- `docs/architecture/anti-hallucination.md` — how the validator + QA
  stages slot into the run order.
- `docs/architecture/services/multi_model_qa.md` — the
  `cross_model_qa` stage's review engine.
- `~/.claude/projects/C--Users-mattm/memory/feedback_writer_model_canary.md`
  — operator playbook for diagnosing pipeline-wide approval-rate drops
  via the writer-fallback event.
