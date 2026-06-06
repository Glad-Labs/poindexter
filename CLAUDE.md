# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## IMPORTANT: New session startup

If this is a new session, read these in order:

1. `~/.claude/projects/C--Users-mattm/memory/matts_voice.md` — How Matt thinks and communicates
2. `~/.claude/projects/C--Users-mattm/memory/decision_log.md` — Key decisions and WHY
3. The latest `session_*_handoff.md` — What was built and what's pending

## Project Overview

Glad Labs is an AI-operated content business — a solo founder using AI to run an autonomous content pipeline that generates, reviews, publishes, and monetizes blog content.

**Architecture inspired by human brain anatomy:**

- **Brainstem** (`brain/`) — standalone daemon (local), monitors all services, self-heals
- **Cerebrum** (`src/cofounder_agent/`) — FastAPI backend, content pipeline, business logic
- **Cerebellum** — anticipation engine + QA registry (learned patterns, quality calibration)
- **Limbic System** — brain_knowledge graph + revenue engine (memory, motivation, rewards)
- **Thalamus** — process composer + API layer (routes all inputs to the right processor)
- **Hypothalamus** — settings service + cost guard (homeostasis, budget regulation)
- **Spinal Cord** — PostgreSQL (all components communicate through shared DB)

### Production URLs

**Production / public surfaces:**

| Service         | URL                                                                             |
| --------------- | ------------------------------------------------------------------------------- |
| Public site     | https://gladlabs.io (→ www.gladlabs.io)                                         |
| Public docs     | https://gladlabs.mintlify.app                                                   |
| Voice (LiveKit) | https://nightrider.taild4f626.ts.net/voice/join (tap-to-join, Tailscale Funnel) |
| Private repo    | https://github.com/Glad-Labs/glad-labs-stack                                    |
| Public repo     | https://github.com/Glad-Labs/poindexter (auto-mirror)                           |
| Project board   | https://github.com/orgs/Glad-Labs/projects/2                                    |

**Local services** (Docker, accessible via http://localhost:&lt;port&gt; on Matt's PC, or via http://100.81.93.12:&lt;port&gt; on the Tailnet):

| Service            | URL / Port                       | What it's for                                                                                                                                                                                                  |
| ------------------ | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend API        | http://localhost:8002            | FastAPI worker (poindexter-worker container)                                                                                                                                                                   |
| Brain daemon       | Local process (brain/), no HTTP  | Self-healing watchdog — Telegram alerts on failure                                                                                                                                                             |
| Grafana            | http://localhost:3000            | 12 dashboards (Mission Control / Pipeline / Cost / Observability / System Health / Integrations / **QA Rails** / **Findings** / **Revenue** / **Experiments & Dry-Run** / **Database** / **Hardware & Power**) |
| QA Rails dashboard | http://localhost:3000/d/qa-rails | Per-reviewer pass-rate, score distribution, latest QA passes (#329 Lane D) — created 2026-05-10                                                                                                                |
| Findings dashboard | http://localhost:3000/d/findings | Probe-findings routing — emitted/pending-delivery counts, by-kind/severity, kind→delivery-policy, latest findings (#461 Phase 4) — created 2026-06-02                                                          |
| Langfuse           | http://localhost:3010            | LLM trace explorer + prompt UI (UnifiedPromptManager edits land here, every reviewer LLM call is traced)                                                                                                       |
| GlitchTip          | http://localhost:8080            | Self-hosted Sentry — runtime errors from worker / brain / voice agent (org `glad-labs`, project `poindexter`)                                                                                                  |
| pgAdmin            | http://localhost:18443           | Postgres admin — direct DB access (login: see bootstrap.toml)                                                                                                                                                  |
| Prefect            | http://localhost:4200            | Orchestration UI for the Prefect server (flow runs, schedules)                                                                                                                                                 |
| Pyroscope          | http://localhost:4040            | Continuous profiler — flame graphs from worker / brain / voice (`service_name` tag)                                                                                                                            |
| Uptime Kuma        | http://localhost:3002            | External-uptime monitor                                                                                                                                                                                        |
| Tempo              | http://localhost:3200            | Trace storage (consumed via Grafana Explore — Tempo datasource)                                                                                                                                                |
| Loki               | http://localhost:3100            | Log storage (consumed via Grafana Explore — Loki datasource)                                                                                                                                                   |
| Prometheus         | http://localhost:9091            | Metrics storage (consumed via Grafana datasource)                                                                                                                                                              |
| AlertManager       | http://localhost:9093            | Alert-routing UI                                                                                                                                                                                               |
| LiveKit (local)    | ws://localhost:7880              | Local LiveKit server (the public Tailscale Funnel proxies to this)                                                                                                                                             |
| SDXL server        | http://localhost:9836            | Local image generation backend                                                                                                                                                                                 |

### Key Numbers (as of May 27, 2026)

- 95 live posts on gladlabs.io (263 posts total; 1,659 pipeline_tasks across all generation runs)
- 333 Python files under `src/cofounder_agent/services/` (~298 substantive after migrations + `__init__.py` stubs; down from ~455 after the 2026-05-08+09+16 cleanup passes). The "load-bearing services" table below covers the services on a critical execution path — `flows/content_generation.py`, `worker_service.py`, and `auto_publish_gate.py` were missing from prior versions of the table.
- **2026-05-09 deletion**: the entire workflow_executor chain — `workflow_executor.py` + `custom_workflows_service.py` + `template_execution_service.py` + `workflow_validator.py` + `phase_mapper.py` + `phase_registry.py` + `workflow_progress_service.py` + `phases/` tree + `schemas/custom_workflow_schemas.py` + the `agents/` tree — all removed (~3,800 LOC).
- **2026-05-16 deletion**: `services/task_executor.py` (~1,500 LOC) — the legacy polling daemon, replaced by `services/flows/content_generation.py` (Prefect) per Glad-Labs/poindexter#410 Stage 4. The `_notify_discord` / `_notify_alert` helpers + `_auto_publish_task` / `_get_auto_publish_threshold` methods were ported to `services/integrations/operator_notify.py` and `services/auto_publish.py` respectively.
- **Audit-driven sweep (2026-05-27)**: 7 PRs from the full-project audit. #597 public-mirror leak sweep (Matthew Gladding name slipped through middle-initial regex, telegram_chat_id seeded in baseline). #598 auto_publish_gate niche-leak (dev_diary opt-in was leaking to glad-labs niche — caused unauthorized publish of "Claude Is Not Your Architect. Stop." 2026-05-26) + cost_guard key rename (`daily_spend_limit_usd` etc.). #599 inline images via bold-text pseudo-heading fallback + EOF anchor fix. #600 silence openclaw probe (upstream gateway port-busy false positive, no in-container recovery path). #601 DeepEval g_eval wired to OllamaModel (was OPENAI_API_KEY-erroring + scoring 100 advisory on every run for ~7 days). #602 writer prompt H2 markdown demand. #603 SDXL gate ignores stale local-diffusers flag (worker container lost `ml` extras, but SDXL HTTP server is what we actually use). + ops: SDXL server stuck-degraded restart, Grafana JWT env var restored.
- **Cleanup sweep (2026-05-16, complete)**: 10 PRs ranked by ROI, all landed except #10 (this doc-sync) and the worker-image rebuild for sentence-transformers. ✅ PR #1 llm_text → dispatcher consolidation. ✅ PR #2 `content_router_service.py` legacy chunked path deleted. ✅ PR #3 Prefect Stage 4 (`task_executor.py` deleted, ~1500 LOC). ✅ PR #4 direct httpx `/api/chat` + `/api/generate` callers migrated through `dispatch_complete`. ✅ PR #5 shared `httpx.AsyncClient` in lifespan + `app.state.http_client`. ✅ PR #6 Module v1 Phase 4 lifecycle wiring + `pyproject.toml` `poindexter.modules` entry-points + FinanceModule `/api/finance/*` operator routes (balances / transactions / healthcheck, OAuth-JWT protected). ✅ PR #7 shelf-ware deletion (voice orphans + `sync_shared_context` + DeepEval `brand_fabrication`). ✅ PR #8 settings discipline (env-var fallback + secret-read + `os.getenv` leaks fixed). ✅ PR #9 LlamaIndex hybrid+rerank wiring + Ragas Grafana panel + `sentence-transformers` pinned. **Cutover gates are all `true` on prod** — Prefect is the dispatcher, canonical_blog is the pipeline, LiteLLM is the LLM router, LlamaIndex+Ragas+DeepEval+Guardrails are all on. ContentModule physical pipeline-code move was intentionally deferred to Phase 3.5 (waiting on a 3rd business module to justify symmetry over sample-size-1 refactor cost).
- **Atom-cutover (#355, 2026-06-02)**: `canonical_blog` cut over from the hand-coded LangGraph factory to a DB-stored `graph_def` (21 nodes: 13 `stage.*` + 5 `qa.*` rail atoms + 3 `seo.*` atoms), compiled by `services/pipeline_architect.py::build_graph_from_spec`; `pipeline_use_graph_def=true` is the prod default. The `cross_model_qa` stage was **deleted** (`services/stages/cross_model_qa.py`, 733 LOC; ~1,220 with its tests) and replaced by five composable QA atoms (`qa.critic` / `qa.deepeval` / `qa.guardrails` / `qa.ragas` → `qa.aggregate`) in `services/atoms/` that delegate to the retained `multi_model_qa.py` rail library. New `atom_runs` table + `services/atom_runs.py` capture per-atom run + outcome (gated by `atom_runs_capture_enabled`).
- **Migration files** — `0000_baseline.py` (the squashed history) plus the post-baseline migrations under `services/migrations/`. Latest as of 2026-06-04: `20260604_143000_seed_voice_speaches_sidecar_keys.py` (seeds 6 app_settings keys for the Speaches STT/TTS warm-sidecar path — `voice_agent_stt_mode` / `voice_agent_stt_base_url` / `voice_agent_stt_model` / `voice_agent_tts_mode` / `voice_agent_tts_base_url` / `voice_agent_tts_model` — both `*_mode` keys default to `inprocess` so this is a behavior no-op until an operator flips to `sidecar`, targeting the ~12s Whisper cold-start; #1088) and `20260604_120000_drop_orphan_plugin_job_telemetry.py` (deletes 4 orphaned `plugin_job_last_{run,status}_{sync_newsletter_subscribers,sync_page_views}` app_settings rows — the sync jobs were removed in #571 and #955; PluginScheduler auto-writes these rows on job registration but never cleans up after unregistration). Before those, a 5-migration voice-infrastructure batch (#1006/#1000) — `20260604_020000_drop_legacy_voice_agent_brain_key.py` (drops the retired `voice_agent_brain` legacy key — superseded by `voice_agent_brain_mode`; the soft-transition fallback in `_resolve_brain_mode` was removed so the row is dead config), `20260604_030000_seed_voice_claude_code_room_keys_1006.py` (seeds the 3 operational knobs for the `claude-code` two-room split: `voice_agent_claude_code_enabled` / `voice_agent_claude_code_room_name` / `voice_agent_claude_code_identity` — mirrors the poindexter room's keys so the room is phone-tunable from the DB), `20260604_040000_seed_livekit_creds_app_settings_1000.py` (moves LiveKit `api_key` + `api_secret` into `app_settings` as empty-default secrets — empty = fall back to env so the migration is a no-op until an operator populates, collapsing the scattered env/file copies that caused a rotation desync 2026-06-02), `20260604_051500_seed_voice_claude_code_tts_voice_override.py` (seeds `voice_agent_claude_code_tts_voice` per-room Kokoro voice override — empty default = fall back to the shared `voice_agent_tts_voice`, so the two rooms can now run distinct voices without affecting each other), and `20260604_060000_seed_voice_transcript_discord_keys_1006.py` (seeds `voice_agent_claude_code_transcript_enabled` master switch + `voice_transcript_discord_webhook_url` secret — moves the claude-code room's turn-by-turn transcript mirror from Telegram to Discord ops channel; empty webhook falls back to the existing `discord_ops_webhook_url`). Before that, a 5-migration audit-cleanup wave — `20260603_010000_rewire_programmatic_validator_gate.py` (re-seeds `canonical_blog` graph_def v4 to restore the dropped programmatic anti-hallucination hard gate as `qa.programmatic`; demotes `url_verifier` to advisory), `20260603_010500_disable_dead_guardrails_rails.py` (sets `guardrails_enabled=false` + disables both `qa_gates` rows — guardrails-ai was uninstalled 2026-05-12 and the rails were fail-open no-ops), `20260603_011000_reconcile_dormant_ragas_flag.py` (flips `ragas_enabled=false` to match the already-disabled `qa_gates.ragas_eval` row), `20260603_042715_drop_orphan_cloudflare_beacon_url.py` (deletes the orphan `cloudflare_beacon_url` app_setting — no production readers; Vercel `NEXT_PUBLIC_BEACON_URL` is the real gate), and `20260603_043447_drop_legacy_logs_table.py` (drops the empty `logs` table and its dead `admin_db.add_log_entry`/`get_logs` references). Before that, the atom-cutover (#355) trio — `20260602_010711_create_atom_runs_table.py` (new `atom_runs` capture table; seeds `atom_runs_capture_enabled=true`), `20260602_023250_seed_canonical_blog_graph_def.py` (seeds `CANONICAL_BLOG_GRAPH_DEF` into `pipeline_templates.graph_def`), and `20260602_034251_flip_pipeline_use_graph_def_to_true.py` (flips the prod cutover flag). Before that, the 2026-05-10 batch: `20260510_065631_drop_experiments_tables.py` (closes #202 — A/B harness moved from SQL tables to Langfuse Datasets/Traces/Scores, `services/langfuse_experiments.py` is the new home; legacy `services/experiment_service.py` deleted). Preceded by `20260510_044707_seed_default_template_slug.py` (Lane C cutover seam) and `20260510_040315_seed_rag_engine_master_switch.py` (Lane D #329 sub-issue 4). Lane D landed 4/4 sub-issues over 2026-05-09 → 2026-05-10: DeepEval / Ragas / Guardrails / LlamaIndex. The 169 historical migrations were squashed 2026-05-08 — see `services/migrations/0000_baseline.py` for the rationale. New schema changes still go in fresh `YYYYMMDD_HHMMSS*<slug>.py`files; the runner sorts`0000_baseline.py`first because`0`<`2` lexically.
- 12 Grafana dashboards (Mission Control / Pipeline / Cost / Observability / System Health / Integrations / QA Rails / Findings / Revenue / Experiments & Dry-Run / Database / Hardware & Power), 18 Prometheus alert rules across 6 groups + 14 Grafana-managed rules; Pyroscope app-profiles ship from worker/brain/voice agents under `service_name` tags (poindexter#406). The dashboard set was restructured 2026-06-03 (poindexter#654): the dev-blog-only Auto-Publish Gate board was folded into the new **Experiments & Dry-Run** board, and the 75-panel System Health "junk drawer" was split — its Postgres internals → new **Database** board, its GPU/power panels → new **Hardware & Power** board (GPU single-sourced to Prometheus `nvidia_gpu_*`, #653) — leaving a slim ~45-panel System Health. Of the 18 Prometheus rules, 9 are static repo rules in `infrastructure/prometheus/alerts/*.yml` (3 groups — including the disk-space rules that replaced a broken Grafana SQL rule); the remaining 9 (`poindexter-business` / `poindexter-content` / `poindexter-infra`) are DB-sourced, rendered into `rules/*.yml` by `RenderPrometheusRulesJob` every 5 min — so the live count is the repo files plus whatever the DB currently seeds.
- 8,748 Python unit tests across ~531 test files (some skipped in container due to host/container path-depth quirks at `Path(__file__).parents[5]` — works on host; collection picks up ~21 errors as of 2026-05-27, tracked for cleanup)
- 918 app_settings keys (66 secret) plus 4 cost_tier mappings (`cost_tier.{free,budget,standard,premium}.model`) wired 2026-05-09 — the baseline seeds the non-secret defaults; secrets get configured per-operator via `poindexter setup` + bootstrap.toml. (Cost-guard key rename 2026-05-27 closed a silent fallthrough on `daily_spend_limit_usd` / `monthly_spend_limit_usd` — see #598.)
- PluginScheduler boots 39 jobs (taps + retention + memory hygiene + content surfaces) — see `plugins/registry.py:_SAMPLES`
- 5 declarative-data-plane tables (`external_taps` / `retention_policies` / `webhook_endpoints` / `publishing_adapters` / `qa_gates`) feeding the integrations handler registry's 14 handlers across 5 surfaces (`tap` / `retention` / `webhook` / `outbound` / `publishing`)
- 17,307 embeddings across posts / issues / audit / memory / brain / claude_sessions
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
also stays (it's imported by historical migrations). The substrate→content
imports the moves introduced (`main.py` → `quality_service`,
`post_pipeline_actions` → `auto_publish`, `routes/task_routes` → `stages`) are a
transitional state for a later holistic **thin-adapter / interface pass** that
routes substrate's use of content through the module's public surface. NOTE for
path lookups: many `services/<name>.py` references elsewhere in this file are
historical narrative; the live content code is under `modules/content/`.

**Key services (22 load-bearing):**

| Service                                   | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `content_router_service.py`               | Thin TemplateRunner dispatcher. Builds the shared pipeline context (image_service, settings, style tracker, site_config, models_by_phase, experiment assignment) and hands it to `TemplateRunner.run(template_slug, context)` keyed on `pipeline_tasks.template_slug`. The legacy chunked StageRunner flow was deleted 2026-05-16 (Lane C Stage 4); a NULL `template_slug` now fails loud per `feedback_no_silent_defaults`. New `canonical_blog` nodes go on the graph_def spec (`services/canonical_blog_spec.py`), NOT here.                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `content_validator.py`                    | Anti-hallucination rules (programmatic, no LLM). Includes `json_envelope_leak` detection (rule #10 split) so leaked `{"content":"..."}` writer output gets a producer-specific diagnostic.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `multi_model_qa.py`                       | Adversarial review (different LLMs check each other) + DeepEval rails + guardrails-ai rails + Ragas rail (all advisory). DeepEval (#329 sub-issue 1): `deepeval_brand_fabrication`, `deepeval_g_eval`, `deepeval_faithfulness`. guardrails-ai (#329 sub-issue 3): `guardrails_brand`, `guardrails_competitor`. Ragas (#329 sub-issue 2): `ragas_eval` averaging faithfulness + answer-relevancy + context-precision into one score. Six OSS QA rails total. All on in prod (`deepeval_enabled` / `guardrails_enabled` / `ragas_enabled` = `true`). **As of atom-cutover #355** the rails are invoked by the `qa.*` atoms (`services/atoms/qa_{critic,deepeval,guardrails,ragas}.py` → `qa.aggregate`) on the graph_def path — `multi_model_qa.py` is the rail library they delegate to (the monolithic `cross_model_qa` stage is deleted). Advisory-vs-hard-gate is DB-driven per rail via `qa_gates.<rail>.required_to_pass` (`_mark_advisory_if_configured`). |
| `rag_engine.py`                           | LlamaIndex `BaseRetriever` over the existing `embeddings` pgvector table (#329 sub-issue 4 — closes Lane D). `rag_engine_enabled=true` on prod (2026-05-16). Composes hybrid (BM25 + vector + RRF) + cross-encoder rerank wrappers — hybrid+rerank wiring is in flight. See [`docs/architecture/rag-retrieval-stack.md`](docs/architecture/rag-retrieval-stack.md) for the activation runbook.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `qa_gates_db.py`                          | Declarative QA gate definitions (DB-driven)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `flows/content_generation.py`             | Prefect-orchestrated content pipeline (Glad-Labs/poindexter#410). **Sole task dispatch path as of 2026-05-16** (Stage 4 deleted `task_executor.py`). Claims a pending `pipeline_tasks` row, runs it through `content_router_service.process_content_generation_task`, then fires post-pipeline side-effects via `post_pipeline_actions.run_post_pipeline_actions`. Cron / retry / heartbeat / stale-run sweep are all Prefect-native. Deployment registered by `src/cofounder_agent/scripts/deploy_content_flow.py`; operator UI at http://localhost:4200. Lane C cutover (#355) routes every task through `TemplateRunner` + the LangGraph `canonical_blog` template when `app_settings.default_template_slug='canonical_blog'`.                                                                                                                                                                                                                               |
| `template_runner.py`                      | LangGraph-backed dynamic-pipeline orchestrator (TemplateRunner). **PRIMARY PIPELINE PATH** (`default_template_slug=canonical_blog` on prod). `run()` prefers the DB-stored `graph_def` (compiled by `pipeline_architect.build_graph_from_spec` via `load_active_graph_def`) when `pipeline_use_graph_def=true` — **the prod default since #355** — else the legacy Python `TEMPLATES` factory (now `dev_diary`-only; the hand-coded `canonical_blog` factory + `_CANONICAL_BLOG_ORDER` were deleted). Postgres checkpointer enabled via `template_runner_use_postgres_checkpointer=true`.                                                                                                                                                                                                                                                                                                                                                                       |
| `prompt_manager.py`                       | UnifiedPromptManager — Langfuse-first, then YAML defaults (poindexter#47). Edits land in the Langfuse UI. **Lane A complete 2026-05-09:** all 7 inline production prompt constants migrated to YAML keys.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `settings_service.py`                     | DB-backed config (app_settings, ~685 active keys in-cache; ~717 with secrets)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `site_config.py`                          | DI seam over settings — `class SiteConfig` constructed by `main.py` and DI'd via `Depends(get_site_config_dependency)`. Per-module utilities own their own `site_config: SiteConfig` attribute that `main.py`'s lifespan wires via `set_site_config(loaded_instance)`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `cost_guard.py`                           | Daily/monthly spend limits + energy estimates (watt-hours per 1K tokens) for the cost dashboard. Lines 72-100 are ENERGY defaults — NOT USD prices. Operators tune per-model via `plugin.llm_provider.<provider>.model.<model>.energy_per_1k_wh`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `cost_lookup.py`                          | LiteLLM-backed cost lookup (wraps `litellm.model_cost`). The `model_router.py` / `usage_tracker.py` / `model_constants.py` trio is **deleted** (Phase 2 cleanup, 2026-05-08). Lane B introduced the `cost_tier` API: callers do `model = await resolve_tier_model(pool, "standard")` (in `services/llm_providers/dispatcher.py`); operators tune via `app_settings.cost_tier.<tier>.model` rows.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `llm_providers/litellm_provider.py`       | LiteLLM-backed `LLMProvider` plugin (provider routing + cost tracking + retries via mature OSS). **PRIMARY LLM ROUTER as of 2026-05-16** — `plugin.llm_provider.primary.{free,budget,standard,premium}='litellm'` on prod. All `dispatch_complete` calls route through it. Direct `httpx` callers against `/api/chat` + `/api/generate` were retired 2026-05-16 (cleanup sweep PR #4); every LLM call now flows through dispatcher. Langfuse callback auto-traces every call.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `llm_text.py`                             | Plain-text Ollama chat helper for atoms + writer_rag_modes. **2026-05-16**: rewritten to route through `dispatch_complete` when a pool is available — propagates provider-swappability to 6+ writer paths (`narrate_bundle`, `deterministic_compositor`, `pipeline_architect`, `two_pass._revise_node`, `review_with_critic`, `story_spine`). Direct-httpx fallback retained for tests/bootstrap. `maybe_unwrap_json` defense at result boundary catches models that emit JSON envelopes unprompted.                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `research_service.py` / `web_research.py` | Topic research + web fact-check                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `publish_service.py`                      | Final publish + scheduled_publisher integration                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `quality_service.py`                      | Quality scoring orchestration                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `auto_publish_gate.py`                    | Auto-publish decision logic — imported by `stages/finalize_task.py`. **Two gates stacked**: (1) the global `auto_publish_threshold` (default `0` = disabled) in `services/auto_publish.py::get_auto_publish_threshold`; (2) the per-niche edit-distance gate (`{niche}_auto_publish_threshold` / `_dry_run` / `_min_clean_runs` / `_max_edit_distance`) in this file. Gate 2 fires only when the niche has explicit opt-in keys AND `quality_score ≥ threshold` AND the trailing-N approves had `char_diff < max_edit_distance`. **2026-05-27 fix**: gate 2 now reads `{niche_slug}_auto_publish_*` (was hardcoded `dev_diary_*`, which leaked dev_diary's opt-in to glad-labs — caused the unauthorised auto-publish of "Claude Is Not Your Architect. Stop." on 2026-05-26). Helpers live in `services/auto_publish.py` (ported from the deleted `task_executor.py` in the 2026-05-16 Stage 4 deletion).                                                      |
| `worker_service.py`                       | Registers the worker, maintains the heartbeat, wires `faulthandler.dump_traceback_later` hang diagnostics. Without it the worker has no DB presence and the brain can't see it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `internal_link_coherence.py`              | Auto-adds related post links                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `social_poster.py`                        | Generates X/LinkedIn posts via Ollama; distribution is row-driven through `publishing_adapters` (poindexter#112) — adding a new platform = insert a row + register a `publishing.<name>` handler.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `newsletter_service.py`                   | Weekly digest generator                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |

**Content pipeline stages (`canonical_blog` graph_def — atom-cutover #355, live as of 2026-06-02):**

`canonical_blog` runs as a static `graph_def` stored in the `pipeline_templates` table (`active=true`), authored in `services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF` and compiled by `services/pipeline_architect.py::build_graph_from_spec` (which validates `requires`/`produces` reachability at build/seed time). `TemplateRunner.run` prefers the DB graph_def (via `load_active_graph_def`) when `app_settings.pipeline_use_graph_def='true'` — **now the prod default** (flipped 2026-06-02) — falling back to the legacy Python `TEMPLATES` factory otherwise. The hand-coded `canonical_blog` factory + `_CANONICAL_BLOG_ORDER` were **deleted** from `services/pipeline_templates/__init__.py`; only `dev_diary` remains in `TEMPLATES`.

The 21-node graph_def — 13 `stage.<name>` nodes + the 5 `qa.*` rail atoms (replacing the deleted `cross_model_qa` stage) + the 3 `seo.*` atoms (replacing `generate_seo_metadata`, #362) — runs as a linear chain:

1. `verify_task` → 2. `generate_content` (writer, may use RAG mode) → 3. `writer_self_review` → 4. `resolve_internal_link_placeholders` (closes leaked `[posts/<slug>]` markers — added 2026-05-15) → 5. `quality_evaluation` (pattern-based scorer) → 6. `url_validation` → 7. `replace_inline_images` → 8. `source_featured_image` → 9. `caption_images` (vision re-caption so alt text matches the rendered pixels) → **the `qa.*` rail block (nodes 10–14)**: `qa.critic` → `qa.deepeval` → `qa.guardrails` → `qa.ragas` → `qa.aggregate` → **the `seo.*` atom chain (nodes 15–17)**: `seo.generate_title` → `seo.generate_description` → `seo.extract_keywords` → 18. `generate_media_scripts` → 19. `generate_video_shot_list` → 20. `capture_training_data` → 21. `finalize_task` (awaiting_approval or auto-publish if score ≥ threshold).

**The `cross_model_qa` stage is deleted** (`services/stages/cross_model_qa.py`, 733 LOC; ~1,220 with its two test files). QA now runs as five composable atoms in `services/atoms/`: `qa.critic` / `qa.deepeval` / `qa.guardrails` / `qa.ragas` each delegate to the existing `MultiModelQA` rail methods (`_review_with_cloud_model` / `_check_deepeval_*` / `_check_guardrails_*` / `_check_ragas_eval`) and append to the new `qa_rail_reviews` PipelineState channel; `qa.aggregate` combines them into the gate decision — on reject it does the DB writes the old stage did (via `services/atoms/_qa_persist.py`) and halts the graph, on approve it promotes `quality_score` and populates `qa_reviews`. `multi_model_qa.py` stays — it's the rail library the atoms delegate to. Rail advisory is DB-driven: each rail reads `qa_gates.<rail>.required_to_pass` (via `MultiModelQA._mark_advisory_if_configured`), so graduating a rail (poindexter#454) is still `qa_gates.<rail>.required_to_pass=true`, now effective on the graph_def path. (The legacy `cross_model_qa` rewrite loop was **not** ported — `qa.aggregate` halts on reject rather than auto-rewriting.)

`dev_diary` template uses a 4-node subset: `verify_task` → `narrate_bundle` (atom) → `source_featured_image` → `finalize_task` — it has no graph_def row, so it falls back to its retained legacy `TEMPLATES` factory even with `pipeline_use_graph_def=true`.

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

Next.js 16 app router. On-demand tag-based revalidation (`revalidateTag('posts')` on publish, wired in `lib/posts.ts`), not time-based ISR; the only `export const revalidate` declarations are the podcast/video RSS feed routes at 3600s. Features:

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

**Singleton deleted 2026-05-09 (glad-labs-stack#330).** All production
code uses the DI seam in all new code; there is no longer a module-level
`site_config` instance to import. The
[glad-labs-stack#330](https://github.com/Glad-Labs/glad-labs-stack/issues/330)
sweep retired the singleton + lifespan-rebind shim entirely; per-module
utilities now own their own `site_config: SiteConfig` attribute that
`main.py`'s lifespan wires via `set_site_config(loaded_instance)`.

A scheduled `reload_site_config` job refreshes the DB-loaded values
every minute (verified live — worker logs show `site_config refreshed
(685 keys)`). The job receives the lifespan-bound SiteConfig via
`config["_site_config"]`, so calling `.reload(pool)` on it propagates
fresh values to every wired module that points at the same instance.

For NEW code, always use the DI seam (route handlers via
`Depends(get_site_config_dependency)`, services via constructor
injection, stages via `context.get("site_config")`, leaf utilities
via the per-module `site_config` attr seeded by `set_site_config`).

Tests construct their own `SiteConfig(initial_config={...})` instance
or use the shared instance in `tests/unit/conftest.py` (which fans out
to every module via `set_site_config` at collection time).

For SaaS / A/B-testing readiness, every tunable should be a
DB-backed setting. Background algorithm windows (anomaly detection,
dedup lookback, failure rate windows) are NOT exceptions — they're
also settings with sensible defaults.

**Storage is provider-agnostic.** `storage_*` keys in app*settings
target any S3-compatible provider (R2, S3, B2, MinIO). The old
`cloudflare_r2*\*` keys still work as a fallback but are deprecated.

### Deployment

Source of truth: `docs/operations/ci-deploy-chain.md`. Two-remote model (post-2026-04-30 gitea decommission):

- **`origin` = `Glad-Labs/glad-labs-stack`** (private GitHub) — full tree (public + Glad Labs operator/premium overlay). Vercel watches this and deploys `www.gladlabs.io`. Push your day-to-day work here.
- **`github` = `Glad-Labs/poindexter`** (public GitHub) — open-source product subset. Refreshed from origin via `scripts/sync-to-github.sh`, which strips private files (web/public-site, web/storefront, mcp-server-gladlabs, marketing, premium dashboards, writing_samples, gladlabs-config, .shared-context, CLAUDE.md, etc.).

**Cross-repo sync is automatic.** GitHub Actions workflow `.github/workflows/sync-to-public-poindexter.yml` runs on every push to `origin/main` and mirrors the filtered subset to Glad-Labs/poindexter in ~30s, using a write-enabled deploy key (private key stored as `POINDEXTER_DEPLOY_KEY` secret on glad-labs-stack). Just `git push origin main` and the public mirror updates itself.

**Mirror force-push posture (intentional):** Glad-Labs/poindexter has `allow_force_pushes: true` in its classic branch protection AND no `non_fast_forward` rule in its ruleset. The mirror is rebuilt from scratch on every sync (filter → force-push), so force-push protection on a derived branch would just keep the mirror permanently stale. The classic protection still requires the public-side CI checks (test-backend, migrations-smoke, Mintlify Deployment, link-rot) to pass on the resulting commit. **Do not re-enable force-push protection on the public mirror — it will silently break the sync workflow.**

**Bypass mechanism:** include `[skip-public-sync]` in the commit message to keep a particular commit private (in-progress branches, sensitive WIP).

**Local fallback:** `git pushe` (alias for `bash scripts/push-everywhere.sh`) does the same thing locally — useful when CI is broken or you want immediate feedback iterating on the sync filter itself. Set up by `bash scripts/install-git-hooks.sh` after a fresh clone.

Backend + brain run locally on Matt's PC; Vercel only handles the static/SSR frontend slice from glad-labs-stack.

## Key Principles

- **Async-everywhere:** FastAPI uses async/await throughout; never block the event loop
- **Brain architecture:** System modeled after human brain anatomy — each region independent
- **PostgreSQL as spinal cord:** All components communicate through shared DB tables, not imports
- **Anti-hallucination:** Three layers — prompts, LLM QA, programmatic validator. See [`docs/architecture/anti-hallucination.md`](docs/architecture/anti-hallucination.md) for the full layer-by-layer breakdown (rule groups, reviewers, prompts, aggregation logic).
- **Config in DB, not code:** `app_settings` table replaces environment variables AND hardcoded constants. If you write a literal in production code, ask "could a customer tune this?" — if yes, it goes in app_settings.
- **Fail loud + notify:** Missing required config triggers `notify_operator()` (Telegram → Discord → alerts.log) then `sys.exit(2)`. No silent fallbacks.
- **Self-healing:** Brain daemon monitors and restarts services autonomously
- **Model router first:** Use cost tiers (`free`/`budget`/`standard`/`premium`) not hardcoded model names
- **Revenue-aware:** Content decisions informed by what generates traffic and money
- **Matt's preferences:** Autonomous work (don't ask "what's next"), minimize env vars, manage from phone via Telegram/Grafana, no client/agency work — fully automated passive income. "Think 5 years down the road if this is a SaaS product" — EVERY tunable goes in app_settings, not code.

## Monitoring

- **Grafana (self-hosted):** http://localhost:3000 (or http://100.81.93.12:3000 from the tailnet) — 12 dashboards. Grafana Cloud was retired 2026-05-03; the local Docker container (poindexter-grafana) is the only Grafana now. Local Prometheus scrapes windows_exporter + nvidia-smi-exporter directly; Alloy was the Cloud shipper and is no longer used.
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

Nine autonomous Claude Code sessions run on Matt's PC via Windows Task
Scheduler, defined in `scripts/claude-sessions.ps1` (register/list/remove with
`.\claude-sessions.ps1 -Install | -List | -Uninstall`). Each runs in an
isolated git worktree off the latest `origin/main`, makes changes on a branch,
and opens a **code** PR against `Glad-Labs/glad-labs-stack` (the source of truth;
poindexter is a force-rebuilt mirror that can't take code) and never `main`
directly. Issues, by contrast, are content-routed to either repo (OSS →
poindexter, business/internal → glad-labs-stack). Per-session model is set in the
`Model` field (defaults to `claude-sonnet-4-6`).

| Session             | When (local) | Model      | Does                                                    |
| ------------------- | ------------ | ---------- | ------------------------------------------------------- |
| `alert-triage`      | daily 01:00  | sonnet-4-6 | files probe-bug issues from `alert_events`              |
| `codebase-audit`    | Wed 02:00    | sonnet-4-6 | `ruff` F401 fixes + `bandit` security issues            |
| `test-health`       | daily 03:00  | sonnet-4-6 | fixes simple unit-test failures                         |
| `test-expansion`    | daily 04:00  | sonnet-4-6 | adds tests to low-coverage files                        |
| `issue-resolver`    | daily 05:00  | opus-4-8   | fixes one scoped open issue                             |
| `dependency-review` | daily 06:30  | sonnet-4-6 | auto-merges green patch-bump dependabot PRs             |
| `doc-sync`          | Fri 05:00    | sonnet-4-6 | verifies CLAUDE.md file references resolve              |
| `triage-sweep`      | Mon 07:00    | sonnet-4-6 | applies derivable labels + surfaces triage proposals    |
| `claude-md-sync`    | daily 02:30  | sonnet-4-6 | syncs CLAUDE.md DB-derived counts + migration narrative |

## Database migrations

Migrations live in `src/cofounder_agent/services/migrations/`. The
169 historical files were squashed into a single `0000_baseline.py`
(plus `0000_baseline.schema.sql` + `0000_baseline.seeds.sql`) on
2026-05-08 — the file's docstring explains why and what it captured.
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

Read [`docs/operations/migrations.md`](docs/operations/migrations.md)
for the convention. Verify against a fresh DB with
[`docs/operations/fresh-db-setup.md`](docs/operations/fresh-db-setup.md)
or the CI smoke test (`python scripts/ci/migrations_smoke.py`). Lint
with `python scripts/ci/migrations_lint.py` — it catches collisions
and missing runner interface.

## Reference Documentation

- **Operations docs:** `docs/operations/` (troubleshooting, local-development-setup, disaster-recovery, ci-deploy-chain, etc.)
- **Anti-hallucination layers:** [`docs/architecture/anti-hallucination.md`](docs/architecture/anti-hallucination.md) — every QA reviewer's source line + decision logic, including the six OSS rails wired in via Lane D #329 (DeepEval ×3, guardrails-ai ×2, Ragas).
- **RAG retrieval stack:** [`docs/architecture/rag-retrieval-stack.md`](docs/architecture/rag-retrieval-stack.md) — Path A (legacy inline pgvector) vs Path B (LlamaIndex BaseRetriever, opt-in via `rag_engine_enabled`); activation runbook.
- **LangGraph pipeline (Lane C + atom-cutover #355):** `app_settings.default_template_slug='canonical_blog'`. As of #355 (2026-06-02) `canonical_blog` runs as a static `graph_def` row in the `pipeline_templates` table — authored in `services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF` (21 nodes: 13 `stage.*` + 5 `qa.*` rail atoms + 3 `seo.*` atoms), compiled by `services/pipeline_architect.py::build_graph_from_spec`, preferred by `TemplateRunner.run` when `pipeline_use_graph_def=true` (the prod default). The hand-coded `canonical_blog` factory + `_CANONICAL_BLOG_ORDER` were deleted from `services/pipeline_templates/__init__.py` (only `dev_diary` remains in `TEMPLATES`), and the `cross_model_qa` stage was deleted in favour of the `qa.*` rail atoms → `qa.aggregate`. The earlier legacy chunked StageRunner path (`content_router_service.py` + `plugins/stage_runner.py`) was already deleted 2026-05-16. New `canonical_blog` nodes go on the graph_def spec.
- **Prefect dispatch (#410, post-Stage-4):** `services/task_executor.py` was deleted entirely 2026-05-16 (~1500 LOC). Prefect's deployment owns dispatch entirely; retry / heartbeat / stale-task sweep are native Prefect features. Operator UI at port 4200. The `_notify_discord` / `_notify_alert` helpers moved to `services/integrations/operator_notify.py`; `_auto_publish_task` / `_get_auto_publish_threshold` moved to `services/auto_publish.py`.
- **Module v1 (Glad-Labs/poindexter#490):** [`docs/architecture/module-v1.md`](docs/architecture/module-v1.md) — shipping incrementally as in-tree scaffolding, deferring physical code moves until 2+ business modules exist (avoids refactoring for sample-size-1 symmetry). Phase 1 (Module Protocol + `get_modules()` registry + manifest validation) shipped 2026-05-13. Phase 2 (per-module migration runner + `module_schema_migrations` table + boot wiring in `startup_manager._run_migrations`) shipped 2026-05-13. Phase 3-lite (in-tree `ContentModule` skeleton at `src/cofounder_agent/modules/content/`, no physical pipeline-code moves) shipped 2026-05-13. Phase 4-lite (route auto-discovery in `utils/route_registration.register_all_routes` — iterates `get_modules()` after substrate routes mount, calls each module's `register_routes(app)`) shipped 2026-05-13. **Phase 4 lifecycle fully wired 2026-05-16**: `register_routes` confirmed live (it was — Phase 4-lite IS in `route_registration.py`), `pyproject.toml` `poindexter.modules` entry-points registered so `get_modules()` works outside the imperative core-samples fallback, and `register_cli` / `register_dashboards` / `register_probes` are now invoked at lifespan startup as safe no-ops (the worker process doesn't host CLI subparsers / Grafana / brain probes — those targets get `None`; misnamed-method failures fail loud per `feedback_no_silent_defaults`). FinanceModule operator routes live at `/api/finance/*` (balances / transactions / healthcheck), OAuth-JWT protected via the existing `verify_api_token` middleware, fail-loud 503 with remediation command when Mercury config is missing. **Decomposition philosophy** ([memory: `project_module_decomposition_axes`](#)): capability plugins (existing 20 entry-point groups: llm/image/video/audio/tts) and business modules (Module v1: ContentModule, FinanceModule) are orthogonal axes — business modules COMPOSE capability plugins. Deferred to Phase 3.5/4.5/5: physical pipeline-code moves, dashboard auto-discovery, CLI subparser threading, brain-probe iteration, `visibility` sync-filter rewrite.
- **Latest session handoff:** `~/.claude/projects/C--Users-mattm/memory/session_62_handoff.md`
- **Architecture vision:** `~/.claude/projects/C--Users-mattm/memory/project_brain_architecture.md`
- **Revenue model:** `~/.claude/projects/C--Users-mattm/memory/project_revenue_model.md`
