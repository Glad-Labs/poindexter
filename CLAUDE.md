# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## IMPORTANT: New session startup

If this is a new session, read these in order:

1. `~/.claude/projects/C--Users-mattm/memory/user_profile.md` — How Matt thinks and communicates
2. `~/.claude/projects/C--Users-mattm/memory/decision_log.md` — Key decisions and WHY
3. `~/.claude/projects/C--Users-mattm-glad-labs-website/memory/MEMORY.md` — auto-memory index (recent context, feedback, project state)

## Project Overview

Glad Labs is an AI-operated content business — a solo founder using AI to run an autonomous content pipeline that generates, reviews, publishes, and monetizes blog content.

**Architecture vocabulary — kernel / module / capability.** The code's real
structure is **kernel** (the substrate everything rents — plugin registry, DI
container, pipeline engine, settings; `plugins/`, `services/`), **modules**
(manifested business functions — `modules/content/`, `modules/finance/`; Module
v1), and **capabilities** (the 20 entry-point plugin groups modules compose —
llm / image / video / audio / tts). New work answers "where does this go?" in
those terms; the kitchen-metaphor and brain-region framings in older docs map
onto this one. Two anatomy labels still pay rent and stay as proper nouns:

- **Brainstem** (`brain/`) — standalone self-healing watchdog daemon, genuinely
  independent of the FastAPI app (only needs Python + asyncpg).
- **Spinal Cord** — PostgreSQL as the bus: components really do communicate
  through shared DB tables, not imports.

> _Historical note._ The system was originally framed entirely in brain
> anatomy. Five of those labels no longer pay rent: **Cerebrum** (the FastAPI
> backend), **Cerebellum** (anticipation engine and QA registry), **Limbic
> System** (brain_knowledge graph and revenue engine), **Thalamus** (process
> composer and API layer), and **Hypothalamus** (settings service and cost
> guard). They were doc labels over arbitrary groupings that business modules
> cut straight across, so new code shouldn't reach for them — they're kept
> here only for reading older docs and commits.

### Production URLs

**Production / public surfaces:**

| Service         | URL                                                                                                                                   |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Public site     | https://gladlabs.io (→ www.gladlabs.io)                                                                                               |
| Public docs     | https://gladlabs.mintlify.app                                                                                                         |
| Voice (LiveKit) | https://nightrider.taild4f626.ts.net/voice/join (tap-to-join, Tailscale Serve — tailnet-only; moved off the public Funnel 2026-06-02) |
| Private repo    | https://github.com/Glad-Labs/glad-labs-stack                                                                                          |
| Public repo     | https://github.com/Glad-Labs/poindexter (auto-mirror)                                                                                 |
| Project board   | https://github.com/orgs/Glad-Labs/projects/2                                                                                          |

**Local services** (Docker, accessible via http://localhost:&lt;port&gt; on Matt's PC, or via http://100.81.93.12:&lt;port&gt; on the Tailnet):

| Service            | URL / Port                       | What it's for                                                                                                                                                                                                                    |
| ------------------ | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend API        | http://localhost:8002            | FastAPI worker (poindexter-worker container)                                                                                                                                                                                     |
| Brain daemon       | Local process (brain/), no HTTP  | Self-healing watchdog — Telegram alerts on failure                                                                                                                                                                               |
| Grafana            | http://localhost:3000            | 13 dashboards (Mission Control / Pipeline / Cost / Observability / System Health / Integrations / **QA Rails** / **Findings** / **Revenue** / **Experiments & Dry-Run** / **Database** / **Hardware & Power** / **SEO Harvest**) |
| QA Rails dashboard | http://localhost:3000/d/qa-rails | Per-reviewer pass-rate, score distribution, latest QA passes (#329 Lane D) — created 2026-05-10                                                                                                                                  |
| Findings dashboard | http://localhost:3000/d/findings | Probe-findings routing — emitted/pending-delivery counts, by-kind/severity, kind→delivery-policy, latest findings (#461 Phase 4) — created 2026-06-02                                                                            |
| Langfuse           | http://localhost:3010            | LLM trace explorer + prompt UI (UnifiedPromptManager edits land here, every reviewer LLM call is traced)                                                                                                                         |
| GlitchTip          | http://localhost:8080            | Self-hosted Sentry — runtime errors from worker / brain / voice agent (org `glad-labs`, project `poindexter`)                                                                                                                    |
| pgAdmin            | http://localhost:18443           | Postgres admin — direct DB access (login: see bootstrap.toml)                                                                                                                                                                    |
| Prefect            | http://localhost:4200            | Orchestration UI for the Prefect server (flow runs, schedules)                                                                                                                                                                   |
| Pyroscope          | http://localhost:4040            | Continuous profiler — flame graphs from worker / brain / voice (`service_name` tag)                                                                                                                                              |
| Uptime Kuma        | http://localhost:3002            | External-uptime monitor                                                                                                                                                                                                          |
| Tempo              | http://localhost:3200            | Trace storage (consumed via Grafana Explore — Tempo datasource)                                                                                                                                                                  |
| Loki               | http://localhost:3100            | Log storage (consumed via Grafana Explore — Loki datasource)                                                                                                                                                                     |
| Prometheus         | http://localhost:9091            | Metrics storage (consumed via Grafana datasource)                                                                                                                                                                                |
| AlertManager       | http://localhost:9093            | Alert-routing UI                                                                                                                                                                                                                 |
| LiveKit (local)    | ws://localhost:7880              | Local LiveKit server (the Tailscale **Serve** tailnet proxy fronts `/voice/join` → this; moved off the public Funnel 2026-06-02)                                                                                                 |
| SDXL server        | http://localhost:9836            | Local image generation backend                                                                                                                                                                                                   |

### Key Numbers (as of June 10, 2026)

- 104 live posts on gladlabs.io (273 posts total; 1,722 pipeline_tasks across all generation runs)
- 353 Python files under `src/cofounder_agent/services/` (~291 substantive after `__init__.py` stubs; down from ~455 after the 2026-05-08+09+16 cleanup passes). The "load-bearing services" table below covers the services on a critical execution path — `flows/content_generation.py`, `worker_service.py`, and `auto_publish_gate.py` were missing from prior versions of the table.
- **2026-05-09 deletion**: the entire workflow_executor chain — `workflow_executor.py` + `custom_workflows_service.py` + `template_execution_service.py` + `workflow_validator.py` + `phase_mapper.py` + `phase_registry.py` + `workflow_progress_service.py` + `phases/` tree + `schemas/custom_workflow_schemas.py` + the `agents/` tree — all removed (~3,800 LOC).
- **2026-05-16 deletion**: `services/task_executor.py` (~1,500 LOC) — the legacy polling daemon, replaced by `services/flows/content_generation.py` (Prefect) per Glad-Labs/poindexter#410 Stage 4. The `_notify_discord` / `_notify_alert` helpers + `_auto_publish_task` / `_get_auto_publish_threshold` methods were ported to `services/integrations/operator_notify.py` and `services/auto_publish.py` respectively.
- **Audit-driven sweep (2026-05-27)**: 7 PRs from the full-project audit. #597 public-mirror leak sweep (Matthew Gladding name slipped through middle-initial regex, telegram_chat_id seeded in baseline). #598 auto_publish_gate niche-leak (dev_diary opt-in was leaking to glad-labs niche — caused unauthorized publish of "Claude Is Not Your Architect. Stop." 2026-05-26) + cost_guard key rename (`daily_spend_limit_usd` etc.). #599 inline images via bold-text pseudo-heading fallback + EOF anchor fix. #600 silence openclaw probe (upstream gateway port-busy false positive, no in-container recovery path). #601 DeepEval g_eval wired to OllamaModel (was OPENAI_API_KEY-erroring + scoring 100 advisory on every run for ~7 days). #602 writer prompt H2 markdown demand. #603 SDXL gate ignores stale local-diffusers flag (worker container lost `ml` extras, but SDXL HTTP server is what we actually use). + ops: SDXL server stuck-degraded restart, Grafana JWT env var restored.
- **Cleanup sweep (2026-05-16, complete)**: 10 PRs ranked by ROI, all landed except #10 (this doc-sync) and the worker-image rebuild for sentence-transformers. ✅ PR #1 llm_text → dispatcher consolidation. ✅ PR #2 `content_router_service.py` legacy chunked path deleted. ✅ PR #3 Prefect Stage 4 (`task_executor.py` deleted, ~1500 LOC). ✅ PR #4 direct httpx `/api/chat` + `/api/generate` callers migrated through `dispatch_complete`. ✅ PR #5 shared `httpx.AsyncClient` in lifespan + `app.state.http_client`. ✅ PR #6 Module v1 Phase 4 lifecycle wiring + `pyproject.toml` `poindexter.modules` entry-points + FinanceModule `/api/finance/*` operator routes (balances / transactions / healthcheck, OAuth-JWT protected). ✅ PR #7 shelf-ware deletion (voice orphans + `sync_shared_context` + DeepEval `brand_fabrication`). ✅ PR #8 settings discipline (env-var fallback + secret-read + `os.getenv` leaks fixed). ✅ PR #9 LlamaIndex hybrid+rerank wiring + Ragas Grafana panel + `sentence-transformers` pinned. **Cutover gates are all `true` on prod** — Prefect is the dispatcher, canonical_blog is the pipeline, LiteLLM is the LLM router, LlamaIndex+Ragas+DeepEval+Guardrails are all on. ContentModule physical pipeline-code move was intentionally deferred to Phase 3.5 (waiting on a 3rd business module to justify symmetry over sample-size-1 refactor cost).
- **Atom-cutover (#355, 2026-06-02)**: `canonical_blog` cut over from the hand-coded LangGraph factory to a DB-stored `graph_def` (21 nodes at cutover — 13 `stage.*` + 5 `qa.*` + 3 `seo.*`; since grown to 37, see the Content-pipeline-stages section), compiled by `services/pipeline_architect.py::build_graph_from_spec`; `pipeline_use_graph_def=true` is the prod default. The `cross_model_qa` stage was **deleted** (`services/stages/cross_model_qa.py`, 733 LOC; ~1,220 with its tests) and replaced by composable QA atoms (5 at cutover, now 12) → `qa.aggregate`, in `modules/content/atoms/` (moved there in the 2026-06-04 content-module migration) that delegate to the retained `multi_model_qa.py` rail library. New `atom_runs` table + `services/atom_runs.py` capture per-atom run + outcome (gated by `atom_runs_capture_enabled`).
- **Migration files** — `0000_baseline.py` (the squashed history) plus the post-baseline migrations under `services/migrations/`. The baseline is the **Phase E squash (2026-06-06, #1194)** — it supersedes the Phase D baseline (2026-05-29, which absorbed 235 migrations through `20260529_*`) and the original 2026-05-08 squash, folding in every timestamped migration through `20260606_233518_*`. So the files in tree are `0000_baseline.py` plus the 44 migrations from `20260607_*` onward (45 `.py` files total); latest as of 2026-06-15: `20260615_033048_promote_niche_sources_to_niche_bound_topic_taps.py`. Notable post-baseline batches: the `20260607_*` settings seeds (Stable Audio Open music/SFX, podcast Kokoro TTS, and the `qa.self_consistency` rail; #621); `create_skill_catalog` + `create_bench_run_results` + `graduate_eval_rails_to_required` (2026-06-07); the `20260608_*` media-pipeline `graph_def` batch (`seed_media_pipeline_graph_def` plus render/caption/qa/persist/audio-qa reseeds) and `drop_dead_qa_guardrails_node`; the retention-summary tables + sensor-downsample policy (`20260610_*`/`20260611_*`); `reseed_canonical_blog_graph_def_v5_seo_collapsed` (the #734 single-call SEO metadata) and `seed_citation_reconciliation` (#765); the SEO-refresh `graph_def` + `seo_opportunities` table batch (`20260612_*`); `fix_poindexter_set_in_app_settings_descriptions` + `repair_rubric_pipeline_version_titles` (2026-06-13/14); and the topic-discovery V2 + media-approval grandfathering batch (`20260615_*`): `grandfather_video_media_approvals_for_already_live_videos`, `create_topic_pool_table_and_external_taps_niche_id`, `stamp_dispatched_at_on_grandfather_media_approvals_to_defuse_re_dispatch`, and `promote_niche_sources_to_niche_bound_topic_taps` (migrates `niche_sources` rows → niche-bound `external_taps` taps targeting the new `topic_pool` table). New schema changes still go in fresh `YYYYMMDD_HHMMSS_<slug>.py` files; the runner sorts `0000_baseline.py` first because `0` < `2` lexically.
- 13 Grafana dashboards (Mission Control / Pipeline / Cost / Observability / System Health / Integrations / QA Rails / Findings / Revenue / Experiments & Dry-Run / Database / Hardware & Power), 16 static Prometheus alert rules across 5 groups (`infrastructure/prometheus/alerts/*.yml`) plus DB-rendered + Grafana-managed rules; Pyroscope app-profiles ship from worker/brain/voice agents under `service_name` tags (poindexter#406). The dashboard set was restructured 2026-06-03 (poindexter#654): the dev-blog-only Auto-Publish Gate board was folded into the new **Experiments & Dry-Run** board, and the 75-panel System Health "junk drawer" was split — its Postgres internals → new **Database** board, its GPU/power panels → new **Hardware & Power** board (GPU single-sourced to Prometheus `nvidia_gpu_*`, #653) — leaving a slim ~45-panel System Health. The static repo rules are 16 alerts across 5 groups in 5 `infrastructure/prometheus/alerts/*.yml` files (deadmans-switch / infrastructure / observability-sidecars / postgres-connections / prometheus-alertmanager — incl. the disk-space rules that replaced a broken Grafana SQL rule); on top of those, the DB-sourced rules (`poindexter-business` / `poindexter-content` / `poindexter-infra`) are rendered into `rules/*.yml` by `RenderPrometheusRulesJob` every 5 min — so the live count is the 16 repo rules plus whatever the DB currently seeds.
- 10,223 test functions across 634 test files (551 unit + 16 integration + 11 integration_db + 1 benchmark) — latest nightly: 10,352 passed, 84 skipped, 1 xfailed, 0 failures, **0 collection errors** (counts verified 2026-06-09). A handful still skip in-container due to the `Path(__file__).parents[5]` path-depth quirk and run on host; the prior "~21 collection errors" line described a since-fixed problem.
- 1,031 app_settings keys (67 secret) plus 4 cost_tier mappings (`cost_tier.{free,budget,standard,premium}.model`) wired 2026-05-09 — the baseline seeds the non-secret defaults; secrets get configured per-operator via `poindexter setup` + bootstrap.toml. (Cost-guard key rename 2026-05-27 closed a silent fallthrough on `daily_spend_limit_usd` / `monthly_spend_limit_usd` — see #598.)
- `plugins/registry.py` `_SAMPLES` registers 44 job-type plugins (taps + retention + memory hygiene + content surfaces); several are dormant behind master switches, so the live scheduled count is lower
- 5 declarative-data-plane tables (`external_taps` / `retention_policies` / `webhook_endpoints` / `publishing_adapters` / `qa_gates`) feeding the integrations handler registry's 14 handlers across 5 surfaces (`tap` / `retention` / `webhook` / `outbound` / `publishing`)
- 22,550 embeddings across posts / issues / audit / memory / brain / claude_sessions (retention prunes claude_sessions/brain vectors, so this count drifts)
- $0/month infra cost (fully self-hosted; only business-level paid services sit outside the pipeline)

## Development Commands

### Starting Services

```bash
npm run dev                  # Start both services concurrently (primary command)
npm run dev:cofounder        # Backend only (FastAPI + uvicorn)
npm run dev:public           # Next.js only
```

### Testing

```bash
# Python backend
cd src/cofounder_agent && poetry run pytest tests/unit/ -q    # Unit tests
cd src/cofounder_agent && poetry run pytest tests/integration/ -q  # Integration

# JavaScript (public site)
npm run test                  # Jest for public site

# Playwright E2E
npm run test:e2e              # All Playwright tests (headless)
```

### Code Quality

```bash
npm run lint                  # ESLint all workspaces
npm run format                # Prettier
npm run type:check            # Python mypy
```

## Architecture

### Brain Daemon (`brain/`)

**Containerized daemon (`poindexter-brain-daemon`).** Independent of FastAPI — only needs Python + asyncpg. Runs as a sibling container in the stack (was a host process pre-2026-05; the containerization happened during the same cleanup wave that moved diffusers/torch out of the worker image).

- Monitors site, API (5-minute cycles)
- Self-maintains knowledge graph (brain_knowledge table)
- Processes reasoning queue (brain_queue table)
- Logs all decisions (brain_decisions table)
- Alerts via Telegram when services are down
- Auto-restarts local services when running on Matt's PC

### Backend (`src/cofounder_agent/`)

**Entry point:** `main.py` — FastAPI app with two deployment modes:

- `DEPLOYMENT_MODE=coordinator` — minimal read-only API (intended for future cloud host; currently unused)
- `DEPLOYMENT_MODE=worker` (local PC) — claims tasks, runs content pipeline via Ollama

**Modules** (Module v1 — `src/cofounder_agent/modules/`):

The 20th entry-point group in the plugin registry. Each Module bundles
capability-plugin contributions plus DB migrations, jobs, HTTP routes,
and Grafana panels into a manifested business function. As of 2026-05-16
(Phase 4 lifecycle fully wired):

| Module    | Path               | Visibility | What it does                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| --------- | ------------------ | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `content` | `modules/content/` | public     | Reference Module. Owns its content code after the incremental Phase 3 migration (2026-06-04): `content_validator`, `stages/`, `atoms/`, `multi_model_qa`, `ai_content_generator`, `internal_link_coherence`, `quality_service`, `auto_publish(_gate)`. Generic engine (`template_runner`, `pipeline_architect`, `prompt_manager`, `llm_text`, `atom_registry`) + `canonical_blog_spec` (migration-anchored) stay in substrate; content rents the engine via the DB graph_def seam. |
| `finance` | `modules/finance/` | private    | Mercury read-only banking (Glad Labs operator overlay). HTTP surface live at `/api/finance/{healthcheck,balances,transactions}` (OAuth-JWT protected). Stripped from public mirror via sync filter.                                                                                                                                                                                                                                                                                |

Adding a new business module (HR, customer support, ops/security) follows
the [extending-poindexter §9 walkthrough](docs/operations/extending-poindexter.md).
Operator-overlay specifics for the finance module live in
[docs/operations/finance-module-operator.md](docs/operations/finance-module-operator.md)
(also stripped from public mirror).

**Content module owns its code (incremental Phase 3, 2026-06-04).** The
content-pipeline code physically moved from `services/` into `modules/content/`
over a chain of squash-merged PRs (#1111 validator → #1113 `stages/` → #1114
`atoms/` → #1115 `multi_model_qa` + `ai_content_generator` → #1117
`internal_link_coherence` → #1123 `quality_service` + `auto_publish(_gate)`).
This resolved the long-deferred Phase 3 not as a big-bang but as one-file/one-tree
relocations, enabled by Phase 5's presence-based discovery. **Generic pipeline
engine stays in substrate** (`template_runner`, `pipeline_architect`,
`prompt_manager`, `llm_text`, `atom_registry`) — content rents it via the DB
`graph_def` seam, so the engine never imports content. `canonical_blog_spec`
also stays (it's imported by historical migrations). **`modules/content/api.py` thin-adapter boundary shipped (PR #1389)** — all 10 executable imports across 8 substrate files now route through it (`main.py` → `UnifiedQualityService`; `post_pipeline_actions.py` → gate/publish/QA helpers; `publish_service.py` → `record_post_approve_metrics`; `deepeval_rails.py` + `guardrails_rails.py` → `content_validator`; `research_context.py` → `InternalLinkCoherenceFilter`; `topic_proposal_service.py` → `build_topic_decision_artifact`; `pipeline_templates/__init__.py` → `narrate_bundle`). **3 string-path registries remain out-of-scope** (dynamic `importlib` — cannot route via Python import): `plugins/registry.py` `_SAMPLES` (14 `modules.content.stages.*` paths), `services/atom_registry.py` walk-root, `services/http_client.py` `WIRED_HTTP_CLIENT_MODULES` (2 content paths). NOTE for path lookups: many `services/<name>.py` references elsewhere in this file are historical narrative; the live content code is under `modules/content/`.

**Key services (22 load-bearing):**

| Service                                   | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `content_router_service.py`               | Thin TemplateRunner dispatcher. Builds the shared pipeline context (image_service, settings, style tracker, site_config, models_by_phase, experiment assignment) and hands it to `TemplateRunner.run(template_slug, context)` keyed on `pipeline_tasks.template_slug`. The legacy chunked StageRunner flow was deleted 2026-05-16 (Lane C Stage 4); a NULL `template_slug` now fails loud per `feedback_no_silent_defaults`. New `canonical_blog` nodes go on the graph_def spec (`services/canonical_blog_spec.py`), NOT here.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `content_validator.py`                    | Anti-hallucination rules (programmatic, no LLM). Includes `json_envelope_leak` detection (rule #10 split) so leaked `{"content":"..."}` writer output gets a producer-specific diagnostic.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `multi_model_qa.py`                       | Adversarial review (different LLMs check each other) + DeepEval rails + native guardrails rails + Ragas rail (all advisory). DeepEval (#329 sub-issue 1): `deepeval_brand_fabrication`, `deepeval_g_eval`, `deepeval_faithfulness`. guardrails (#329 sub-issue 3; reimplemented **native / dep-free** in `services/guardrails_rails.py` by #996 (2026-06-03) after `guardrails-ai` was dropped for CVE-2026-45758): `guardrails_brand` (runs `content_validator` patterns), `guardrails_competitor` (regex over the `guardrails_competitor_list` CSV). Ragas (#329 sub-issue 2): `ragas_eval` averaging faithfulness + answer-relevancy + context-precision into one score. Six Lane-D QA rails total (DeepEval ×3, guardrails ×2 — now native, Ragas). All on in prod (`deepeval_enabled` / `guardrails_enabled` / `ragas_enabled` = `true`). **As of atom-cutover #355** the rails are invoked by 12 `qa.*` atoms (`modules/content/atoms/qa_*.py` → `qa.aggregate`) on the graph_def path — `multi_model_qa.py` is the rail library they delegate to (the monolithic `cross_model_qa` stage is deleted). Advisory-vs-hard-gate is DB-driven per rail via `qa_gates.<rail>.required_to_pass` (`_mark_advisory_if_configured`). |
| `rag_engine.py`                           | LlamaIndex `BaseRetriever` over the existing `embeddings` pgvector table (#329 sub-issue 4 — closes Lane D). `rag_engine_enabled=true` on prod (2026-05-16). Full three-mode stack: vector-only → `HybridRRFRetriever` (BM25 tsvector + pgvector + RRF fusion, `rag_hybrid_enabled=true` on prod) → `CrossEncoderRerankRetriever` (sentence-transformers cross-encoder, `rag_rerank_enabled=true` on prod). The `text_search` column on `embeddings` is `GENERATED ALWAYS AS (to_tsvector('simple', text_preview))` — auto-populated, 100% coverage, GIN-indexed (`idx_embeddings_text_search`). All three modes live as of 2026-06-15. See [`docs/architecture/rag-retrieval-stack.md`](docs/architecture/rag-retrieval-stack.md).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `qa_gates_db.py`                          | Declarative QA gate definitions (DB-driven)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `flows/content_generation.py`             | Prefect-orchestrated content pipeline (Glad-Labs/poindexter#410). **Sole task dispatch path as of 2026-05-16** (Stage 4 deleted `task_executor.py`). Claims a pending `pipeline_tasks` row, runs it through `content_router_service.process_content_generation_task`, then fires post-pipeline side-effects via `post_pipeline_actions.run_post_pipeline_actions`. Cron / retry / heartbeat / stale-run sweep are all Prefect-native. Deployment registered by `src/cofounder_agent/scripts/deploy_content_flow.py`; operator UI at http://localhost:4200. Lane C cutover (#355) routes every task through `TemplateRunner` + the LangGraph `canonical_blog` template when `app_settings.default_template_slug='canonical_blog'`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `template_runner.py`                      | LangGraph-backed dynamic-pipeline orchestrator (TemplateRunner). **PRIMARY PIPELINE PATH** (`default_template_slug=canonical_blog` on prod). `run()` prefers the DB-stored `graph_def` (compiled by `pipeline_architect.build_graph_from_spec` via `load_active_graph_def`) when `pipeline_use_graph_def=true` — **the prod default since #355** — else the legacy Python `TEMPLATES` factory (now `dev_diary`-only; the hand-coded `canonical_blog` factory + `_CANONICAL_BLOG_ORDER` were deleted). Postgres checkpointer enabled via `template_runner_use_postgres_checkpointer=true`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `prompt_manager.py`                       | UnifiedPromptManager — Langfuse-first, then YAML defaults (poindexter#47). Edits land in the Langfuse UI. **Lane A complete 2026-05-09:** all 7 inline production prompt constants migrated to YAML keys.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `settings_service.py`                     | DB-backed config (app_settings, ~685 active keys in-cache; ~717 with secrets)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `site_config.py`                          | DI seam over settings — `class SiteConfig` constructed once per entry point and reached through the `AppContainer` composition root (`services/container.py`). Route handlers DI it via `Depends(get_site_config_dependency)`; services take `site_config` as a constructor arg. The legacy per-module `set_site_config(loaded_instance)` fan-out is retired (#272 / #788 capstone — `di_wiring.WIRED_MODULES` is empty); do **not** add new `set_site_config` setters.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `cost_guard.py`                           | Daily/monthly spend limits + energy estimates (watt-hours per 1K tokens) for the cost dashboard. Lines 72-100 are ENERGY defaults — NOT USD prices. Operators tune per-model via `plugin.llm_provider.<provider>.model.<model>.energy_per_1k_wh`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `cost_lookup.py`                          | LiteLLM-backed cost lookup (wraps `litellm.model_cost`). The `model_router.py` / `usage_tracker.py` / `model_constants.py` trio is **deleted** (Phase 2 cleanup, 2026-05-08). Lane B introduced the `cost_tier` API: callers do `model = await resolve_tier_model(pool, "standard")` (in `services/llm_providers/dispatcher.py`); operators tune via `app_settings.cost_tier.<tier>.model` rows.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `llm_providers/litellm_provider.py`       | LiteLLM-backed `LLMProvider` plugin (provider routing + cost tracking + retries via mature OSS). **PRIMARY LLM ROUTER as of 2026-05-16** — `plugin.llm_provider.primary.{free,budget,standard,premium}='litellm'` on prod. All `dispatch_complete` calls route through it. Direct `httpx` callers against `/api/chat` + `/api/generate` were retired 2026-05-16 (cleanup sweep PR #4); every LLM call now flows through dispatcher. Langfuse callback auto-traces every call.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `llm_text.py`                             | Plain-text Ollama chat helper for atoms + writer_rag_modes. **2026-05-16**: rewritten to route through `dispatch_complete` when a pool is available — propagates provider-swappability to 6+ writer paths (`narrate_bundle`, `deterministic_compositor`, `pipeline_architect`, `two_pass._revise_node`, `review_with_critic`, `story_spine`). Direct-httpx fallback retained for tests/bootstrap. `maybe_unwrap_json` defense at result boundary catches models that emit JSON envelopes unprompted.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `research_service.py` / `web_research.py` | Topic research + web fact-check                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `publish_service.py`                      | Final publish + scheduled_publisher integration                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `quality_service.py`                      | Quality scoring orchestration                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `auto_publish_gate.py`                    | Auto-publish decision logic — imported by `stages/finalize_task.py`. **Two gates stacked**: (1) the global `auto_publish_threshold` (default `0` = disabled) in `services/auto_publish.py::get_auto_publish_threshold`; (2) the per-niche edit-distance gate (`{niche}_auto_publish_threshold` / `_dry_run` / `_min_clean_runs` / `_max_edit_distance`) in this file. Gate 2 fires only when the niche has explicit opt-in keys AND `quality_score ≥ threshold` AND the trailing-N approves had `char_diff < max_edit_distance`. **2026-05-27 fix**: gate 2 now reads `{niche_slug}_auto_publish_*` (was hardcoded `dev_diary_*`, which leaked dev_diary's opt-in to glad-labs — caused the unauthorised auto-publish of "Claude Is Not Your Architect. Stop." on 2026-05-26). Helpers live in `services/auto_publish.py` (ported from the deleted `task_executor.py` in the 2026-05-16 Stage 4 deletion).                                                                                                                                                                                                                                                                                                                       |
| `worker_service.py`                       | Registers the worker, maintains the heartbeat, wires `faulthandler.dump_traceback_later` hang diagnostics. Without it the worker has no DB presence and the brain can't see it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `internal_link_coherence.py`              | Auto-adds related post links                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `social_poster.py`                        | Generates X/LinkedIn posts via Ollama; distribution is row-driven through `publishing_adapters` (poindexter#112) — adding a new platform = insert a row + register a `publishing.<name>` handler.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `newsletter_service.py`                   | Weekly digest generator                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |

**Content pipeline stages (`canonical_blog` graph_def — atom-cutover #355, live as of 2026-06-02):**

`canonical_blog` runs as a static `graph_def` stored in the `pipeline_templates` table (`active=true`), authored in `services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF` and compiled by `services/pipeline_architect.py::build_graph_from_spec` (which validates `requires`/`produces` reachability at build/seed time). `TemplateRunner.run` prefers the DB graph_def (via `load_active_graph_def`) when `app_settings.pipeline_use_graph_def='true'` — **now the prod default** (flipped 2026-06-02) — falling back to the legacy Python `TEMPLATES` factory otherwise. The hand-coded `canonical_blog` factory + `_CANONICAL_BLOG_ORDER` were **deleted** from `services/pipeline_templates/__init__.py`; only `dev_diary` remains in `TEMPLATES`.

The graph_def is **36 nodes** — 10 `stage.*` + 12 `content.*` + 12 `qa.*` + 1 `seo.*` + 1 `atoms.approval_gate` — run as a linear chain. (It was 21 at the #355 cutover; #362 decomposed coarse stages into fine-grained atoms, #363 added `draft_gate`; #730 removed dead `qa.guardrails`; #734 collapsed 3 `seo.*` atoms into `seo.generate_all_metadata` (saves ~2 min/post); #765 added `content.reconcile_citations` + advisory `qa.unlinked_attribution`.) Authoritative order is `services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF`:

1. `verify_task` → **writer block** 2. `content.generate_draft` → 3. `content.generate_title` → 4. `content.check_title_originality` → 5. `content.normalize_draft` → 6. `draft_gate` (`atoms.approval_gate`, seeded disabled) → 7. `writer_self_review` → 8. `resolve_internal_link_placeholders` → 9. `content.reconcile_citations` (deterministic citation repair — re-links named sources the writer dropped, #765) → 10. `quality_evaluation` (pattern-based scorer) → 11. `url_validation` → **image block** 12. `content.plan_image_markers` → 13. `content.generate_images` → 14. `content.inject_images` → 15. `source_featured_image` → 16. `caption_images` (vision re-caption) → **QA-rail block (17–28)** `qa.programmatic` → `qa.critic` → `qa.deepeval` → `qa.ragas` → `qa.vision` → `qa.topic_delivery` → `qa.citations` → `qa.unlinked_attribution` (advisory — flags residual unlinked sources post-repair, #765) → `qa.consistency` → `qa.self_consistency` (advisory, #1447) → `qa.web_factcheck` → `qa.aggregate` → **SEO block (29)** `seo.generate_all_metadata` (single structured call, #734) → 30. `generate_media_scripts` → 31. `generate_video_shot_list` → 32. `capture_training_data` → **finalize block (33–36)** `content.compile_meta` → `content.persist_task` → `content.record_pipeline_version` → `content.evaluate_auto_publish` (awaiting_approval or auto-publish if score ≥ threshold).

**The `cross_model_qa` stage is deleted** (`services/stages/cross_model_qa.py`, 733 LOC; ~1,220 with its two test files). QA now runs as 12 composable `qa.*` atoms in `modules/content/atoms/` (`qa.programmatic` / `qa.critic` / `qa.deepeval` / `qa.ragas` / `qa.vision` / `qa.topic_delivery` / `qa.citations` / `qa.unlinked_attribution` / `qa.consistency` / `qa.self_consistency` / `qa.web_factcheck`) that delegate to the existing `MultiModelQA` rail methods and append to the `qa_rail_reviews` PipelineState channel; `qa.aggregate` combines them into the gate decision — on reject it does the DB writes the old stage did (via `modules/content/atoms/_qa_persist.py`) and halts the graph, on approve it promotes `quality_score` and populates `qa_reviews`. `multi_model_qa.py` stays — it's the rail library the atoms delegate to. Rail advisory is DB-driven: each rail reads `qa_gates.<rail>.required_to_pass` (via `MultiModelQA._mark_advisory_if_configured`), so graduating a rail (poindexter#454) is still `qa_gates.<rail>.required_to_pass=true`, now effective on the graph_def path. (The legacy `cross_model_qa` rewrite loop was **not** ported — `qa.aggregate` halts on reject rather than auto-rewriting.)

`dev_diary` template uses a 5-node subset: `verify_task` → `narrate_bundle` (atom) → `generate_seo_metadata` → `source_featured_image` → `finalize_task` — it has no graph_def row, so it falls back to its retained legacy `TEMPLATES` factory even with `pipeline_use_graph_def=true`.

The legacy 6-stage chunked StageRunner flow (`content_router_service.process_content_generation_task`) was **deleted 2026-05-16** along with `plugins/stage_runner.py` itself (no production caller remained after Lane C Stage 3). `content_router_service` is now a thin TemplateRunner dispatcher; a NULL `template_slug` on a `pipeline_tasks` row fails loud per `feedback_no_silent_defaults`. New `canonical_blog` nodes go on the graph_def spec (`services/canonical_blog_spec.py`, re-seeded into `pipeline_templates.graph_def`); `dev_diary` nodes still go on its `TEMPLATES` factory.

**Database tables (key ones):**

- `pipeline_tasks` — pipeline task queue (worker claims rows; Prefect flow dispatches). The canonical seam back from a `posts` row to its source task is `posts.metadata->>'pipeline_task_id'` (added 2026-05-28); `scheduled_publisher` / `/go-live` / the promote-existing-approved path read this key to sync `pipeline_tasks.status` in lockstep with `posts.status` promotions.
- `pipeline_versions` — generated content + qa_feedback per task version
- `atom_runs` — per-atom run + outcome capture on the graph_def path (run_id, atom, node_id, tier/model, latency, status, io-key digests + outcome join); gated by `app_settings.atom_runs_capture_enabled`. Complementary to `capability_outcomes` (per-(atom,tier,model) router scoring). Added 2026-06-02 (#355).
- `posts` — published blog posts. `metadata->>'pipeline_task_id'` is the canonical seam to the source `pipeline_tasks.task_id` — populated by `publish_service.publish_post_from_task` at insert and backfilled for historical rows by migration `20260528_021920`.
- `app_settings` — all config (replaces env vars)
- `page_views` — own analytics tracking
- `brain_knowledge` — knowledge graph (entity/attribute/value)
- `brain_queue` — reasoning queue for the brain
- `brain_decisions` — decision audit trail
- `pipeline_gate_history` — typed history of HITL gate approvals + regen retries (poindexter#366 phase 1, replaces gate-state slice of the dropped pipeline_events table)
- `audit_log` — canonical historical record (queried by `routes/pipeline_events_routes.py` despite the legacy URL prefix)
- `cost_logs` — LLM API cost tracking

### Frontend (`web/public-site/`)

Next.js 16 app router. On-demand tag-based revalidation: cache tags are declared in `lib/posts.ts` and `revalidateTag('posts')` is fired in `app/api/revalidate/route.js` on publish. Several routes ALSO set `export const revalidate = 3600` as a self-healing ISR backstop — the index (`page.js`), `posts/`, `archive/[page]/`, `sitemap.ts`, and all three RSS feeds (`feed.xml` / `podcast-feed.xml` / `video-feed.xml`). Features:

- Blog posts with internal links, affiliate links, related reading
- Giscus comments (GitHub Discussions)
- Google AdSense (ca-pub-4578747062758519, pending approval)
- Google Analytics (G-NJMBCYNDWN)
- ViewTracker beacon → Cloudflare Worker (`infrastructure/cloudflare/page-views-beacon/`) → CF Analytics Engine → `page_views` table (5-min aggregate sync via `services/jobs/sync_cloudflare_analytics.py`). The legacy same-origin `/api/page-views` Vercel route was deleted 2026-05-28 (it 404s by design now — Vercel functions can't reach the local Docker net); production sets `NEXT_PUBLIC_BEACON_URL` to the Worker.
- Sitemap.xml (dynamic, 72+ URLs)
- Google Search Console verified

### MCP Server (`mcp-server/`)

Custom MCP server for Claude desktop app. 28 tools across content / approval / settings / memory / observability surfaces (incl. `findings_list` — probe-findings triage, #461 Phase 4). The sibling `mcp-server-gladlabs/` adds 3 operator-only tools layered on top (private to the Glad Labs operator overlay; not in the public mirror).

**Authentication:** OAuth 2.1 Client Credentials Grant only (Phase 3 #249 closed the dual-auth window 2026-05-05). Every consumer mints JWTs through `POST /token` against a registered `oauth_clients` row; the legacy static-Bearer fallback (and the `POINDEXTER_KEY` / `GLADLABS_KEY` / `app_settings.api_token` plumbing) was removed. Provision a client with `poindexter auth migrate-cli` (or `migrate-mcp` / `migrate-brain` / `migrate-scripts` / `migrate-mcp-gladlabs` / `migrate-openclaw` / `mint-grafana-token` per consumer). `poindexter setup` provisions the initial CLI client out-of-the-box on fresh installs.

### Configuration (#198 — no hardcoded values in code)

**Bootstrap is the only config on disk.** Written by `poindexter setup`
to `~/.poindexter/bootstrap.toml`. Contains the database URL plus the
machine secrets needed to bring the Docker stack up before any DB row
is reachable (Postgres / Grafana / pgAdmin passwords, the OAuth
signing key, etc.) plus optional operator-notification channels for
when the system can't start cleanly. **No `.env` required.**

```toml
# ~/.poindexter/bootstrap.toml
database_url = "postgresql://..."
telegram_bot_token = ""
telegram_chat_id = ""
discord_ops_webhook_url = ""
# Worker auth — OAuth 2.1 only as of Phase 3 (#249). The initial CLI
# client is provisioned by `poindexter setup`; other consumers register
# theirs via `poindexter auth migrate-*`.
```

Resolution priority in `brain.bootstrap.resolve_database_url()`:
explicit CLI arg → bootstrap.toml → DATABASE_URL → LOCAL_DATABASE_URL
→ POINDEXTER_MEMORY_DSN. If nothing resolves, `require_database_url()`
fires `notify_operator()` (Telegram → Discord → alerts.log → stderr)
then `sys.exit(2)`.

**Everything else lives in `app_settings` (~685 active keys).** Code accesses
settings through a `SiteConfig` instance that is dependency-injected
(Phase H, GH#95). `main.py` constructs the canonical instance, loads it
from the DB at startup, and attaches it to `app.state.site_config`.

Get a reference to the instance through the appropriate DI seam:

- **Route handlers:** `site_config: SiteConfig = Depends(get_site_config_dependency)`
- **Services:** accept `site_config` in `__init__` (ctor kwarg) or the
  method signature, store on `self._site_config`
- **Pipeline stages:** `context.get("site_config")` — seeded by
  `content_router_service.process_content_generation_task`
- **Image providers / taps / topic sources:** `config.get("_site_config")` —
  seeded by the dispatcher/runner

Then call methods on the instance:

- `site_config.get(key, default)` — sync, reads from in-memory cache
  populated at startup
- `site_config.get_secret(key, default)` — **async**, hits DB each call
  (secrets are filtered out of the cache, so `is_secret=true` keys
  MUST be fetched via this method)

**Singleton AND the per-module `set_site_config` fan-out are both retired
(glad-labs-stack#330 → #272 / #788).** GH#330 first deleted the module-level
`site_config` singleton. The #272 SiteConfig-DI migration then moved every
service to **constructor DI**, and its **#788 capstone** (commit `55b6f751e`)
migrated the last four ambient-singleton modules (`gpu_scheduler`,
`ollama_client`, `prompt_manager`, `route_utils`) onto the process-wide
**`AppContainer`** accessor — leaving `services.di_wiring.WIRED_MODULES` an
**empty tuple**. `AppContainer` (`services/container.py`) is the composition
root: constructed once per entry point (worker lifespan, Prefect subprocess,
CLI, brain, test fixture) by `services.bootstrap.build_container`, it holds the
one loaded `SiteConfig` and exposes each migrated service as a `cached_property`.
The old `set_site_config(loaded_instance)` lifespan loop survives only as a
near-dead seam — `wire_site_config_modules` now wires 0 modules and merely
re-publishes the instance to `services.integrations.shared_context` for
`notify_operator` secret resolution (a separate concern, pending its own cleanup).

A scheduled `reload_site_config` job refreshes the DB-loaded values every minute
(verified live — worker logs show `site_config refreshed (685 keys)`). The job
receives the entry point's `SiteConfig` via `config["_site_config"]` and calls
`.reload(pool)` on it; because `AppContainer` holds that same instance by
reference, fresh DB values propagate to every service the container constructed.

For NEW code, reach `SiteConfig` through the container/DI — never through a
`set_site_config` setter: route handlers via `Depends(get_site_config_dependency)`
or `Depends(get_container_dependency)`, services via **constructor injection**
(`def __init__(self, *, site_config: SiteConfig, ...)`), stages via
`context.get("site_config")`. Adding a new module-level `site_config` global +
`set_site_config` setter reproduces the retired seam — don't.

Tests construct their own `SiteConfig(initial_config={...})` and pass it via
constructor DI (`Service(site_config=...)` / `fn(site_config=...)`), or rely on
`tests/unit/conftest.py`'s `default_container_active` fixture, which registers a
seeded `SiteConfig` on an `AppContainer` so the container-accessor modules
(`prompt_manager`, `gpu_scheduler`, …) see the brand seed. The old
`_SHARED_TEST_MODULES` `set_site_config` fan-out is now empty — every module on
it migrated to constructor DI.

For SaaS / A/B-testing readiness, every tunable should be a
DB-backed setting. Background algorithm windows (anomaly detection,
dedup lookback, failure rate windows) are NOT exceptions — they're
also settings with sensible defaults.

**Storage is provider-agnostic.** `storage_*` keys in `app_settings`
target any S3-compatible provider (R2, S3, B2, MinIO). The old
`cloudflare_r2_*` keys still work as a fallback but are deprecated.

### Deployment

Source of truth: `docs/operations/ci-deploy-chain.md`. Two-remote model (post-2026-04-30 gitea decommission):

- **`origin` = `Glad-Labs/glad-labs-stack`** (private GitHub) — full tree (public + Glad Labs operator/premium overlay). Vercel watches this and deploys `www.gladlabs.io`. Push your day-to-day work here.
- **`github` = `Glad-Labs/poindexter`** (public GitHub) — open-source product subset. Refreshed from origin via `scripts/sync-to-github.sh`, which strips private files (web/public-site, web/storefront, mcp-server-gladlabs, marketing, premium dashboards, writing_samples, gladlabs-config, .shared-context, CLAUDE.md, etc.).

**Cross-repo sync is automatic.** GitHub Actions workflow `.github/workflows/sync-to-public-poindexter.yml` runs on every push to `origin/main` and mirrors the filtered subset to Glad-Labs/poindexter in ~30s, authenticating with a dedicated **GitHub App** (`glad-labs-mirror-sync`, installed on poindexter with Contents + Workflows read+write) that mints a short-lived installation token per run — `MIRROR_SYNC_APP_ID` + `MIRROR_SYNC_APP_PRIVATE_KEY` secrets on glad-labs-stack. Migrated 2026-06-13 from a fine-grained PAT (`POINDEXTER_SYNC_TOKEN`) that silently expired and froze the mirror for ~4 pushes; the App mints ephemeral tokens, so there's no annual expiry cliff. (The Workflows permission is required because the mirror tree includes `.github/workflows/*`.) Just `git push origin main` and the public mirror updates itself.

**Mirror force-push posture (intentional):** Glad-Labs/poindexter has `allow_force_pushes: true` in its classic branch protection AND no `non_fast_forward` rule in its ruleset. The mirror is rebuilt from scratch on every sync (filter → force-push), so force-push protection on a derived branch would just keep the mirror permanently stale. The required public-side CI checks (test-backend, migrations-smoke, Mintlify Deployment, the two CodeQL `Analyze` checks, link-rot) live in the **`Main` ruleset** — moved there from classic protection 2026-06-13 when the sync went App-auth. They gate any human direct-pusher, while the `glad-labs-mirror-sync` App is a ruleset **bypass actor** (alongside org/repo admins) so the sync can force-push past them. The old PAT bypassed these as an admin (it ran as Matt); an App installation token is not an admin, hence the explicit ruleset bypass. **Do not re-enable force-push protection on the public mirror, and do not remove the mirror-sync App from the ruleset bypass list — either will silently break the sync workflow.**

**Bypass mechanism:** include `[skip-public-sync]` in the commit message to keep a particular commit private (in-progress branches, sensitive WIP).

**Local fallback:** `git pushe` (alias for `bash scripts/push-everywhere.sh`) does the same thing locally — useful when CI is broken or you want immediate feedback iterating on the sync filter itself. Set up by `bash scripts/install-git-hooks.sh` after a fresh clone.

Backend + brain run locally on Matt's PC; Vercel only handles the static/SSR frontend slice from glad-labs-stack.

**Worker image includes ffmpeg (#1449)** — Stage-2 media rendering (podcast/video) bakes ffmpeg directly into `poindexter-prefect-worker`; no separate sidecar or host install needed. After any worker image change, `docker compose up -d --build poindexter-prefect-worker` to apply.

## Key Principles

- **Async-everywhere:** FastAPI uses async/await throughout; never block the event loop
- **Kernel / module / capability:** New code goes in as a business **module** on the kernel substrate, composing **capability** plugins — see the architecture-vocabulary note in Project Overview ("brainstem" and "spinal cord" are the only load-bearing anatomy labels left)
- **PostgreSQL as spinal cord:** All components communicate through shared DB tables, not imports
- **Anti-hallucination:** Three layers — prompts, LLM QA, programmatic validator. See [`docs/architecture/anti-hallucination.md`](docs/architecture/anti-hallucination.md) for the full layer-by-layer breakdown (rule groups, reviewers, prompts, aggregation logic).
- **Config in DB, not code:** `app_settings` table replaces environment variables AND hardcoded constants. If you write a literal in production code, ask "could a customer tune this?" — if yes, it goes in app_settings.
- **Fail loud + notify:** Missing required config triggers `notify_operator()` (Telegram → Discord → alerts.log) then `sys.exit(2)`. No silent fallbacks.
- **Self-healing:** Brain daemon monitors and restarts services autonomously
- **Model router first:** Use cost tiers (`free`/`budget`/`standard`/`premium`) not hardcoded model names
- **Revenue-aware:** Content decisions informed by what generates traffic and money
- **Matt's preferences:** Autonomous work (don't ask "what's next"), minimize env vars, manage from phone via Telegram/Grafana, no client/agency work — fully automated passive income. "Think 5 years down the road if this is a SaaS product" — EVERY tunable goes in app_settings, not code.

## Monitoring

- **Grafana (self-hosted):** http://localhost:3000 (or http://100.81.93.12:3000 from the tailnet) — 13 dashboards. Grafana Cloud was retired 2026-05-03; the local Docker container (poindexter-grafana) is the only Grafana now. Local Prometheus scrapes windows_exporter + nvidia-smi-exporter directly; Alloy was the Cloud shipper and is no longer used.
- **Dashboards:**
  - **Mission Control** — top-level operator glance (trimmed 2026-06-03: deep media/director detail moved to Pipeline; keeps a single media-pending glance stat)
  - **Pipeline** — content pipeline throughput, plus the consolidated quality/QA rows ("Quality — scores & output" + "QA — rejections & validation", deduped 2026-06-03) and the Media Approval Queue (now holds the media/director detail relocated from Mission Control)
  - **Cost & Analytics** — LLM spend, energy, posts published
  - **Observability** — Tempo traces (RED-by-service), Pyroscope flame graphs, Loki logs (volume / error rate / live feed), and the API HTTP RED row (request rate / 4xx-5xx % / p95-p99 latency)
  - **System Health** — slim core-services board (service up/down, exporter signals, audit-log breakdown, GlitchTip triage) plus the **Scheduled-publish queue** panel set (depth / next slot / past-due / upcoming-24h table) and the **Approved-queue** panel set ("Approved q" stat + "Approved — awaiting publish" table over `pipeline_tasks.status='approved'`). DB internals and GPU/power were split out to the Database and Hardware & Power boards (2026-06-03).
  - **Experiments & Dry-Run** (`/d/experiments-dryrun`) — auto-publish gate dry-run observability (edit-distance, clean-run counts, gate-state) + Dry-Run Mode + Variant Experiments (Lab). Created 2026-06-03 (absorbed the former dev-blog-only Auto-Publish Gate board).
  - **Database** (`/d/database`) — Postgres internals: db size, connections + states, table row counts/sizes, index usage, active queries, cache-hit ratio, transactions, dead tuples. Extracted from System Health 2026-06-03; the natural home for postgres_exporter metrics (#650).
  - **Hardware & Power** (`/d/hardware-power`) — GPU live (Prometheus `nvidia_gpu_*`), PSU/wall/CPU power sensors (Corsair HX1500i + EIA-rate electricity cost), and GPU history tables. GPU is single-sourced to Prometheus (#653); the redundant `gpu_metrics` SQL live-gauges were dropped. Created 2026-06-03.
  - **Integrations & Admin** — qa_gates / publishing_adapters / external_taps tables
  - **QA Rails — Multi-Model Review** (`/d/qa-rails`) — per-reviewer pass-rate, score distribution, latest QA passes. Powered by `audit_log` rows where `event_type='qa_pass_completed'` (one row per `MultiModelQA.review` call, full reviewer breakdown in JSON details). Created 2026-05-10 alongside the Lane D #329 close-out.
  - **Findings — Probe Routing** (`/d/findings`) — emitted vs pending-delivery counts (pending = routable findings above `app_settings.findings_alert_route_watermark`), per-hour by severity, per-kind volume, and the live `kind → findings.<kind>.delivery` policy join. Powered by `audit_log` rows where `event_type='finding'`. Created 2026-06-02 (#461 Phase 4).
  - **Revenue** (`/d/revenue`) — net revenue / revenue today + month / orders / new subscriptions / refunds / revenue-over-time-by-event-type, sourced from the revenue engine.
- **Alerts → Telegram + Discord:** stuck tasks, failure rate, worker offline, GPU temp, VRAM usage. Routing rules in `infrastructure/grafana/provisioning/alerting/`.
- **Playlist:** "Glad Labs Command Center" cycles all dashboards every 30s.
- **Pyroscope app-profiles (Glad-Labs/poindexter#406):** CPU flame graphs ship from the worker, brain, and voice agents under four `service_name` values — `poindexter-worker`, `poindexter-brain`, `poindexter-voice-livekit`, `poindexter-voice-webrtc`. Master switch is `app_settings.enable_pyroscope` (default true post-#406); per-service panel lives on the Observability dashboard.
- **GlitchTip (self-hosted Sentry):** http://localhost:8080 — runtime exceptions from worker / brain / voice. Org `glad-labs`, project `poindexter`. Sentry SDK auto-initialised in `main.py` when `app_settings.sentry_dsn` is set (provisioned 2026-05-09).
- **Langfuse:** http://localhost:3010 — every reviewer LLM call (DeepEval g-eval / faithfulness, Ragas, the legacy critic) traces here. Use it to drill into a specific qa_pass_completed event and read the judge model's reasoning.

## Scheduled agents (Windows Task Scheduler)

**STATUS 2026-06-10: all currently DISABLED.** The nine sessions below — plus the Startup and Watchdog tasks — were disabled in Windows Task Scheduler (2026-06-09) as cost-control ahead of Anthropic API billing going to full rate 2026-06-15; the definitions remain in place (re-enable via `.\claude-sessions.ps1 -Install`). NOTE: the Windows `claude-md-sync` agent is among the disabled, but the **repo-derivable** stats (file counts, dashboard count, latest-migration name) still auto-sync daily via the `.github/workflows/sync-claude-md.yml` GitHub Actions workflow (06:17 UTC), so those stay current. Only the **DB-derived** counts (posts, embeddings, app_settings totals) — which the Windows agent refreshed via a prod-DB probe — go stale until it's re-enabled.

Nine autonomous Claude Code sessions run on Matt's PC via Windows Task
Scheduler, defined in `scripts/claude-sessions.ps1` (register/list/remove with
`.\claude-sessions.ps1 -Install | -List | -Uninstall`). Each runs in an
isolated git worktree off the latest `origin/main`, makes changes on a branch,
and opens a **code** PR against `Glad-Labs/glad-labs-stack` (the source of truth;
poindexter is a force-rebuilt mirror that can't take code) and never `main`
directly. Issues, by contrast, are content-routed to either repo (OSS →
poindexter, business/internal → glad-labs-stack). Per-session model is set via a
`Model` key on the session block; **currently no session sets it, so all run the
`claude-sonnet-4-6` default** — add a `Model` key in `claude-sessions.ps1` if you
want a heavier model for a session (e.g. opus for `issue-resolver`).

| Session             | When (local) | Model      | Does                                                    |
| ------------------- | ------------ | ---------- | ------------------------------------------------------- |
| `alert-triage`      | daily 01:00  | sonnet-4-6 | files probe-bug issues from `alert_events`              |
| `codebase-audit`    | Wed 02:00    | sonnet-4-6 | `ruff` F401 fixes + `bandit` security issues            |
| `test-health`       | daily 03:00  | sonnet-4-6 | fixes simple unit-test failures                         |
| `test-expansion`    | daily 04:00  | sonnet-4-6 | adds tests to low-coverage files                        |
| `issue-resolver`    | daily 05:00  | sonnet-4-6 | fixes one scoped open issue                             |
| `dependency-review` | daily 06:30  | sonnet-4-6 | auto-merges green patch-bump dependabot PRs             |
| `doc-sync`          | Fri 05:00    | sonnet-4-6 | verifies CLAUDE.md file references resolve              |
| `triage-sweep`      | Mon 07:00    | sonnet-4-6 | applies derivable labels + surfaces triage proposals    |
| `claude-md-sync`    | daily 02:30  | sonnet-4-6 | syncs CLAUDE.md DB-derived counts + migration narrative |

## Database migrations

Migrations live in `src/cofounder_agent/services/migrations/`. The
migration history is squashed into a single `0000_baseline.py`
(plus `0000_baseline.schema.sql` + `0000_baseline.seeds.sql`),
re-rolled most recently by the **Phase E squash (2026-06-06,
Glad-Labs/poindexter#1194)** — the file's docstring explains what each
generation captured.
The runner still sorts lexically; `0000_baseline.py` runs first
because `0` < `2`.

**New migrations use a UTC timestamp prefix** (`YYYYMMDD_HHMMSS_<slug>.py`)
per Glad-Labs/poindexter#378 (2026-05-05). Generate one with:

```bash
python scripts/new-migration.py "describe what the migration does"
```

The runner records each filename in `schema_migrations(name)` and
skips already-applied entries. The baseline self-records on first
run; on Matt's prod (where the schema is already in place) every
`CREATE TABLE IF NOT EXISTS` no-ops, every seed `INSERT ... ON
CONFLICT DO NOTHING` no-ops, and the only mutation is the row
recording the baseline as applied.

**New `app_settings` keys belong in `settings_defaults.py`, not migration
files.** `src/cofounder_agent/services/settings_defaults.py` holds a
`DEFAULTS: dict[str, str]` that is applied idempotently on every boot via
`StartupManager._run_migrations()` → `seed_all_defaults(pool)` using
`INSERT … ON CONFLICT (key) DO NOTHING`. Add new default values there.
Migration files are for schema DDL (new tables, columns, indexes, and
constraint changes) and non-settings data mutations only. Seeding a setting
inside a migration file causes drift because the migration runs once and is
never re-evaluated; the seeder runs every boot and stays current.

Read [`docs/operations/migrations.md`](docs/operations/migrations.md)
for the convention. Verify against a fresh DB with
[`docs/operations/fresh-db-setup.md`](docs/operations/fresh-db-setup.md)
or the CI smoke test (`python scripts/ci/migrations_smoke.py`). Lint
with `python scripts/ci/migrations_lint.py` — it catches collisions
and missing runner interface.

## Reference Documentation

- **Operations docs:** `docs/operations/` (troubleshooting, local-development-setup, disaster-recovery, ci-deploy-chain, etc.)
- **Anti-hallucination layers:** [`docs/architecture/anti-hallucination.md`](docs/architecture/anti-hallucination.md) — every QA reviewer's source line + decision logic, including the six rails wired in via Lane D #329 (DeepEval ×3, guardrails ×2 — now native/dep-free per #996, Ragas).
- **RAG retrieval stack:** [`docs/architecture/rag-retrieval-stack.md`](docs/architecture/rag-retrieval-stack.md) — Path A (legacy inline pgvector) vs Path B (LlamaIndex BaseRetriever, opt-in via `rag_engine_enabled`); activation runbook.
- **LangGraph pipeline (Lane C + atom-cutover #355):** `app_settings.default_template_slug='canonical_blog'`. As of #355 (2026-06-02) `canonical_blog` runs as a static `graph_def` row in the `pipeline_templates` table — authored in `services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF` (36 nodes — see the Content-pipeline-stages section), compiled by `services/pipeline_architect.py::build_graph_from_spec`, preferred by `TemplateRunner.run` when `pipeline_use_graph_def=true` (the prod default). The hand-coded `canonical_blog` factory + `_CANONICAL_BLOG_ORDER` were deleted from `services/pipeline_templates/__init__.py` (only `dev_diary` remains in `TEMPLATES`), and the `cross_model_qa` stage was deleted in favour of the `qa.*` rail atoms → `qa.aggregate`. The earlier legacy chunked StageRunner path (`content_router_service.py` + `plugins/stage_runner.py`) was already deleted 2026-05-16. New `canonical_blog` nodes go on the graph_def spec.
- **Prefect dispatch (#410, post-Stage-4):** `services/task_executor.py` was deleted entirely 2026-05-16 (~1500 LOC). Prefect's deployment owns dispatch entirely; retry / heartbeat are native Prefect features. Operator UI at port 4200. The `_notify_discord` / `_notify_alert` helpers moved to `services/integrations/operator_notify.py`; `_auto_publish_task` / `_get_auto_publish_threshold` moved to `services/auto_publish.py`. **Stale in-progress reclaim (2026-06-09):** `reclaim_stale_inprogress_tasks` Prefect task fires at the top of every `content_generation_flow` run — it calls `TasksDatabase.sweep_stale_tasks(timeout_minutes=content_flow_stale_inprogress_minutes)` (default 30 min) to reset orphaned rows to `pending` (or fail them after max retries) and clear poisoned LangGraph checkpoints. Without this, a task killed mid-graph stays `in_progress` forever because the flow only claims `pending` rows. See the checkpoint-poisoning note in repo operational notes.
- **Module v1 (Glad-Labs/poindexter#490):** [`docs/architecture/module-v1.md`](docs/architecture/module-v1.md) — shipping incrementally as in-tree scaffolding, deferring physical code moves until 2+ business modules exist (avoids refactoring for sample-size-1 symmetry). Phase 1 (Module Protocol + `get_modules()` registry + manifest validation) shipped 2026-05-13. Phase 2 (per-module migration runner + `module_schema_migrations` table + boot wiring in `startup_manager._run_migrations`) shipped 2026-05-13. Phase 3-lite (in-tree `ContentModule` skeleton at `src/cofounder_agent/modules/content/`, no physical pipeline-code moves) shipped 2026-05-13. Phase 4-lite (route auto-discovery in `utils/route_registration.register_all_routes` — iterates `get_modules()` after substrate routes mount, calls each module's `register_routes(app)`) shipped 2026-05-13. **Phase 4 lifecycle fully wired 2026-05-16**: `register_routes` confirmed live (it was — Phase 4-lite IS in `route_registration.py`), `pyproject.toml` `poindexter.modules` entry-points registered so `get_modules()` works outside the imperative core-samples fallback, and `register_cli` / `register_dashboards` / `register_probes` are now invoked at lifespan startup as safe no-ops (the worker process doesn't host CLI subparsers / Grafana / brain probes — those targets get `None`; misnamed-method failures fail loud per `feedback_no_silent_defaults`). FinanceModule operator routes live at `/api/finance/*` (balances / transactions / healthcheck), OAuth-JWT protected via the existing `verify_api_token` middleware, fail-loud 503 with remediation command when Mercury config is missing. **Decomposition philosophy** ([memory: `project_module_decomposition_axes`](#)): capability plugins (existing 20 entry-point groups: llm/image/video/audio/tts) and business modules (Module v1: ContentModule, FinanceModule) are orthogonal axes — business modules COMPOSE capability plugins. Deferred to Phase 3.5/4.5/5: physical pipeline-code moves, dashboard auto-discovery, CLI subparser threading, brain-probe iteration, `visibility` sync-filter rewrite.
- **Auto-memory index (recent context / feedback / project state):** `~/.claude/projects/C--Users-mattm-glad-labs-website/memory/MEMORY.md`
- **Architecture vision:** `~/.claude/projects/C--Users-mattm/memory/project_brain_architecture.md`
- **Monetization / revenue model:** `~/.claude/projects/C--Users-mattm-glad-labs-website/memory/project_monetization.md`
