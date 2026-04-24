# Poindexter Services Reference

**Last Updated:** 2026-04-23

A skimmable catalog of every service in `src/cofounder_agent/services/`.
Use this when you want to know "what's responsible for X" without
reading the source.

Services are grouped by responsibility. Each entry lists the file,
a one-line purpose, and the main class or entry-point function.
Approximate line counts are shown as rough complexity indicators —
not a target; just a hint.

> **Auto-drift warning:** this reference is maintained by hand. If you
> add or rename a service, update this file in the same PR. A docs
> linter to enforce this is tracked as a follow-up.

---

## Core

The runtime spine — configuration, DI, DB coordination, logging.

- **`site_config.py`** — DB-first configuration with env-var fallback; loads `app_settings` on startup. `SiteConfig` class. ~250 lines.
- **`container.py`** — Dependency-injection registry for service instantiation. `ServiceContainer`. ~150 lines.
- **`database_service.py`** — PostgreSQL coordinator orchestrating the six specialized DB modules below. Supports dual pools (cloud + local). ~300 lines.
- **`logger_config.py`** — Structured logging via structlog. `get_logger()`. ~120 lines.

## Database modules

Each module wraps a subset of DB tables and exposes typed CRUD + query helpers.
All are reached via `database_service.DatabaseService` — callers don't import
these directly.

- **`admin_database.py`** — Admin ops: audit trails, financial tracking, agent status, settings. `AdminDatabase`. ~400 lines.
- **`content_database.py`** — Posts, quality evaluations, categories, tags, authors. `ContentDatabase`. ~350 lines.
- **`tasks_database.py`** — Task CRUD, status filters, pagination, date-range queries. `TasksDatabase`. ~280 lines.
- **`writing_style_database.py`** — Writing-sample CRUD for per-user style matching. `WritingStyleDatabase`. ~200 lines.
- **`embeddings_database.py`** — Embedding storage + pgvector similarity search. `EmbeddingsDatabase`. ~220 lines.
- **`users_database.py`** — User CRUD + OAuth account linking. `UsersDatabase`. ~180 lines.
- **`content_task_store.py`** — Thin CRUD adapter over `content_tasks`. `ContentTaskStore`. ~150 lines.

## Pipeline orchestration & routing

The pipeline glue — who runs what when.

- **`content_router_service.py`** — Unified blog-post pipeline orchestration. Runs the 12-stage `StageRunner` chain. `ContentRouterService`. ~500 lines.
- **`task_executor.py`** — Background polling loop that claims pending tasks via `SELECT ... FOR UPDATE SKIP LOCKED`. Calls `ContentRouterService`.
- **`enhanced_status_change_service.py`** — Validates state transitions and logs status changes. `EnhancedStatusChangeService`. ~180 lines.
- **`handle_task_status_change.py`** — Async handler for status-change events emitted by the executor. ~120 lines.

## LLM & inference

- **`ollama_client.py`** — Local LLM inference via Ollama with cost tracking (GPU watts × duration × rate). `OllamaClient`. ~400 lines.
- **`ai_content_generator.py`** — Legacy generator orchestrator. Being replaced by stage-based pipeline; kept for template fallback. `AIContentGenerator`. ~350 lines.
- **`gpu_scheduler.py`** — Serializes GPU access between Ollama and SDXL. Yields to gaming workloads automatically. ~100 lines.

## Image generation & selection

- **`image_service.py`** — Post-Phase-G dispatcher for SDXL + Pexels. Unified interface. `ImageService`. ~250 lines.
- **`image_generation_config.py`** — SDXL model registry and prompt-template selection. ~150 lines.
- **`image_selection_service.py`** — Strategy router: SDXL vs Pexels based on availability + failure cascades. ~200 lines.
- **`image_prompt_builder.py`** — Builds editorial SDXL prompts from topic / tags / style. ~180 lines.
- **`image_generation_runner.py`** — Executes the SDXL render; R2 upload with local fallback. Path-traversal guards. ~120 lines.

## Content quality & validation (QA stack)

- **`content_validator.py`** — Deterministic programmatic quality gate. Catches fake people, fake stats, unlinked citations, hallucinated libraries. `validate_content()`. ~1,200 lines. **The highest-leverage file in the repo.**
- **`quality_evaluation.py`** — Fast pattern-based QA. Produces `quality_score`, `truncation_detected`, findings. ~200 lines.
- **`citation_verifier.py`** — HTTP-HEAD dead-link detection. Advisory, never blocks publish alone. ~120 lines.
- **`multi_model_qa.py`** — Multi-model critic + gate reviewers (consistency, url_verifier, web_factcheck, vision_gate). Aggregates weighted scores. ~1,600 lines.
- **`quality_checker.py`** — Legacy QA evaluation abstraction. `PATTERN_BASED` + LLM modes. ~100 lines.

## Content processing & SEO

- **`excerpt_generator.py`** — 1–2 sentence summaries for index / RSS / social. `generate_excerpt()`. ~90 lines.
- **`alt_text.py`** — Alt-text sanitation + budget-aware summarization. `strip_pipeline_tokens()`, `sanitize_alt_text()`. ~110 lines.
- **`html_sanitizer.py`** — HTML cleaning with safe-tag filtering. ~130 lines.
- **`slugify_service.py`** — URL-safe slug generation. Dupe-prevention. ~80 lines.
- **`category_resolver.py`** — Maps topic keywords to category UUIDs via taxonomy. ~110 lines.

## Media & transcription

- **`media_script_generator.py`** — Podcast script + video scene generation from draft. Two separate LLM calls for reliability. ~180 lines.
- **`transcription_service.py`** — Audio-to-text via external provider. ~120 lines.

## Cost, analytics & observability

- **`cost_aggregation_service.py`** — Cost analytics by phase / model / provider. Queries `cost_logs`. `CostAggregationService`. ~200 lines.
- **`audit_log.py`** — Fire-and-forget audit logger. Non-blocking background writes. `AuditLogger`, `audit_log_bg()`. ~140 lines.

## Caching & performance

- **`redis_cache.py`** — Async Redis cache with TTL + health checks. `RedisCache`. ~220 lines.
- **`decorators.py`** — `@log_query_performance` etc. Slow-query thresholds configurable. ~190 lines.

## Search & embeddings

- **`embedding_service.py`** — Orchestrates embedding generation, storage, deduplication. Uses the LLM Provider Protocol. ~180 lines.
- **`rag_embeddings_service.py`** — Vector context retrieval for RAG prompts. ~140 lines.
- **`vector_similarity_search.py`** — Cosine-distance queries over pgvector. ~100 lines.

## Decision logging (ML loop)

- **`decision_service.py`** — Standard interface for ML decision logging + outcome tracking. `log_decision()`, `record_outcome()`. ~140 lines.
- **`stateless_decision_handler.py`** — Ephemeral decision computations; no persistence. ~100 lines.

## Integration

- **`devto_service.py`** — Dev.to cross-posting with canonical URL. `DevToCrossPostService`. ~140 lines.
- **`notifications_service.py`** — User / system notification dispatch. ~130 lines.

## Utilities & helpers

- **`error_handler.py`** — Domain exception hierarchy: `AppError`, `ValidationError`, `NotFoundError`, `DatabaseError`. ~100 lines.
- **`paging_helpers.py`** — Offset/limit utilities for list queries. ~90 lines.
- **`database_mixin.py`** — Shared row-to-dict / type-coercion helpers. `DatabaseServiceMixin`. ~80 lines.
- **`bootstrap_defaults.py`** — Centralized localhost / fallback URLs. ~60 lines.
- **`default_author.py`** — Gets or creates the "Poindexter AI" author row. `get_or_create_default_author()`. ~50 lines.
- **`content_revisions_logger.py`** — Write-through helper for `content_revisions` table. Tracks QA iteration history. `log_revision()`. ~70 lines.
- **`idle_worker.py`** — Background maintenance when the pipeline is idle. Cleanup, optimization, scheduled tasks. `IdleWorker`. ~120 lines.

---

## Pipeline stages (12)

Stages live under `services/stages/` and are plugged into `StageRunner`.
Each implements the `Stage` protocol (`name`, `run(context)`).

| #       | File                        | Purpose                                                                                        |
| ------- | --------------------------- | ---------------------------------------------------------------------------------------------- |
| 1       | `verify_task.py`            | Confirm task exists; catch invalid task_id early.                                              |
| 2       | `generate_content.py`       | Core generation: model selection, RAG context, LLM call, title dedup, scrubbing.               |
| 2A.5    | `writer_self_review.py`     | LLM self-review for claim conflicts and revision.                                              |
| 2B      | `quality_evaluation.py`     | Fast pattern-based QA; records scores + truncation detection.                                  |
| 2B.1    | `url_validation.py`         | Extract and HTTP-HEAD-verify URLs; flags broken links (non-fatal).                             |
| 2C      | `replace_inline_images.py`  | Find `[IMAGE-N]` placeholders and inject rendered images (SDXL primary, Pexels fallback).      |
| 3       | `source_featured_image.py`  | Featured image via SDXL editorial illustration or Pexels photo. R2 upload with local fallback. |
| 3.5–3.7 | `cross_model_qa.py`         | Multi-model QA + iterative rewrite loop. Up to `qa_max_rewrites` (default 2) attempts.         |
| 4       | `generate_seo_metadata.py`  | SEO title / description / keywords from draft. Hard caps (60 / 160 chars).                     |
| 4B      | `generate_media_scripts.py` | Podcast script + video scenes. Non-critical — failures don't block publish.                    |
| 6       | `capture_training_data.py`  | Write draft + score into `quality_evaluations` + training tables.                              |
| 7       | `finalize_task.py`          | Persist `content_tasks` row; sets `status=awaiting_approval`. Publishing is a separate stage.  |

See [`architecture/content-pipeline.md`](../architecture/content-pipeline.md) for
the full pipeline narrative and [`architecture/plugin-architecture.md`](../architecture/plugin-architecture.md)
for the `Stage` protocol itself.

---

## Conventions

- **All services take `site_config` via DI.** Post-Phase-H (GH#95), services
  do NOT import `services.site_config.site_config` at module scope. They
  accept a `site_config` kwarg / constructor argument.
- **All async.** Blocking work runs in `asyncio.to_thread` or a worker queue.
- **Errors are typed** via `error_handler.py`. Services raise domain exceptions;
  routes translate them to HTTP status codes.
- **Decisions are logged** via `decision_service.log_decision()` for the ML loop.
- **Never import from `stages/*`**. Stages are plugged into `StageRunner`; they
  don't call each other. Cross-stage data flows through the pipeline context dict.
