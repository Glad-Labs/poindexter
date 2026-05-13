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

| Service            | URL / Port                       | What it's for                                                                                                                      |
| ------------------ | -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Backend API        | http://localhost:8002            | FastAPI worker (poindexter-worker container)                                                                                       |
| Brain daemon       | Local process (brain/), no HTTP  | Self-healing watchdog — Telegram alerts on failure                                                                                 |
| Grafana            | http://localhost:3000            | 8 dashboards (Mission Control / Pipeline / Auto-Publish Gate / Cost / Observability / System Health / Integrations / **QA Rails**) |
| QA Rails dashboard | http://localhost:3000/d/qa-rails | Per-reviewer pass-rate, score distribution, latest QA passes (#329 Lane D) — created 2026-05-10                                    |
| Langfuse           | http://localhost:3010            | LLM trace explorer + prompt UI (UnifiedPromptManager edits land here, every reviewer LLM call is traced)                           |
| GlitchTip          | http://localhost:8080            | Self-hosted Sentry — runtime errors from worker / brain / voice agent (org `glad-labs`, project `poindexter`)                      |
| pgAdmin            | http://localhost:18443           | Postgres admin — direct DB access (login: see bootstrap.toml)                                                                      |
| Prefect            | http://localhost:4200            | Orchestration UI for the Prefect server (flow runs, schedules)                                                                     |
| Pyroscope          | http://localhost:4040            | Continuous profiler — flame graphs from worker / brain / voice (`service_name` tag)                                                |
| Uptime Kuma        | http://localhost:3002            | External-uptime monitor                                                                                                            |
| Tempo              | http://localhost:3200            | Trace storage (consumed via Grafana Explore — Tempo datasource)                                                                    |
| Loki               | http://localhost:3100            | Log storage (consumed via Grafana Explore — Loki datasource)                                                                       |
| Prometheus         | http://localhost:9091            | Metrics storage (consumed via Grafana datasource)                                                                                  |
| AlertManager       | http://localhost:9093            | Alert-routing UI                                                                                                                   |
| LiveKit (local)    | ws://localhost:7880              | Local LiveKit server (the public Tailscale Funnel proxies to this)                                                                 |
| SDXL server        | http://localhost:9836            | Local image generation backend                                                                                                     |

### Key Numbers (as of May 9, 2026)

- 56 live posts on gladlabs.io (222 posts total; 1,519 pipeline_tasks across all generation runs)
- ~285 Python files under `src/cofounder_agent/services/` (down from ~455 after the 2026-05-08 + 2026-05-09 cleanup passes). 17 services are load-bearing (table below). **2026-05-09 deletion**: the entire workflow*executor chain — `workflow_executor.py` + `custom_workflows_service.py` + `template_execution_service.py` + `workflow_validator.py` + `phase_mapper.py` + `phase_registry.py` + `workflow_progress_service.py` + `phases/` tree + `schemas/custom_workflow_schemas.py` + the `agents/` tree (`content_agent/` subtree + top-level `blog*\*\_agent.py`+`registry.py`) — all removed. ~3,800 LOC + ~28 source files + ~17 test files purged. The chain had 0% production traffic; `main.py`lifecycle wiring + DI seam +`startup_manager.py`init steps cleaned up alongside. The **2026-05-08 services audit** at`.shared-context/audits/2026-05-08-services-folder-audit.md`flagged the original ~5,000 LOC deletable; ~3,800 LOC has now landed. Remaining is`task_executor.py`'s eventual replacement by a `canonical_blog` LangGraph template (next phase of #356).
- **Migration files** — `0000_baseline.py` (the squashed history) plus the post-baseline migrations under `services/migrations/`. Latest as of 2026-05-10: `20260510_065631_drop_experiments_tables.py` (closes #202 — A/B harness moved from SQL tables to Langfuse Datasets/Traces/Scores, `services/langfuse_experiments.py` is the new home; legacy `services/experiment_service.py` deleted). Preceded by `20260510_044707_seed_default_template_slug.py` (Lane C cutover seam) and `20260510_040315_seed_rag_engine_master_switch.py` (Lane D #329 sub-issue 4). Lane D landed 4/4 sub-issues over 2026-05-09 → 2026-05-10: DeepEval / Ragas / Guardrails / LlamaIndex. The 169 historical migrations were squashed 2026-05-08 — see `services/migrations/0000_baseline.py` for the rationale. New schema changes still go in fresh `YYYYMMDD_HHMMSS*<slug>.py`files; the runner sorts`0000_baseline.py`first because`0`<`2` lexically.
- 7 Grafana dashboards (post-merge consolidation), 4 alert rules; Pyroscope app-profiles ship from worker/brain/voice agents under `service_name` tags (poindexter#406)
- 8,400+ Python unit tests across 369 test files (some skipped in container due to host/container path-depth quirks at `Path(__file__).parents[5]` — works on host)
- 717 app_settings keys (62 secret) plus 4 cost_tier mappings (`cost_tier.{free,budget,standard,premium}.model`) wired 2026-05-09 — the baseline seeds the non-secret defaults; secrets get configured per-operator via `poindexter setup` + bootstrap.toml
- PluginScheduler boots 33 jobs (taps + retention + memory hygiene + content surfaces) — see `plugins/registry.py:_SAMPLES`
- 5 declarative-data-plane tables (`external_taps` / `retention_policies` / `webhook_endpoints` / `publishing_adapters` / `qa_gates`) feeding the integrations handler registry's 14 handlers across 5 surfaces (`tap` / `retention` / `webhook` / `outbound` / `publishing`)
- 30,547 embeddings across posts / issues / audit / memory / brain / claude_sessions
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

**Standalone local process.** Independent of FastAPI — only needs Python + asyncpg.

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

**Key services (17 load-bearing):**

| Service                                   | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `content_router_service.py`               | 6-stage content pipeline with cross-model QA                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `content_validator.py`                    | Anti-hallucination rules (programmatic, no LLM)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `multi_model_qa.py`                       | Adversarial review (different LLMs check each other) + DeepEval rails + guardrails-ai rails + Ragas rail (all advisory). DeepEval (#329 sub-issue 1): `deepeval_brand_fabrication`, `deepeval_g_eval`, `deepeval_faithfulness`. guardrails-ai (#329 sub-issue 3): `guardrails_brand`, `guardrails_competitor`. Ragas (#329 sub-issue 2): `ragas_eval` averaging faithfulness + answer-relevancy + context-precision into one score (default-off — heavy). Six OSS QA rails total, all advisory while we calibrate the false-positive rate.                                                                                                                                                                                                                                   |
| `rag_engine.py`                           | LlamaIndex `BaseRetriever` over the existing `embeddings` pgvector table (#329 sub-issue 4 — closes Lane D). Optional routing path for `MemoryClient.search`; activated via `app_settings.rag_engine_enabled`. Default-off (legacy inline-pgvector path runs unchanged). Composes hybrid (BM25 + vector + RRF) + cross-encoder rerank wrappers. See [`docs/architecture/rag-retrieval-stack.md`](docs/architecture/rag-retrieval-stack.md) for the activation runbook.                                                                                                                                                                                                                                                                                                       |
| `qa_gates_db.py`                          | Declarative QA gate definitions (DB-driven)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `task_executor.py`                        | Background daemon: polls `pipeline_tasks` for pending work, owns heartbeat/sweep/retry. The actual stage-chain logic lives in `content_router_service.process_content_generation_task` — `task_executor` invokes it. Lane C cutover (#355) is **opt-in live** as of 2026-05-10: flipping `app_settings.default_template_slug='canonical_blog'` routes every new task through `TemplateRunner` + the LangGraph `canonical_blog` template instead of the legacy chunked StageRunner flow. See [`docs/architecture/langgraph-cutover.md`](docs/architecture/langgraph-cutover.md) for the staged rollout runbook. Don't add new logic to the legacy chunked path; new stages go on the LangGraph template.                                                                      |
| `template_runner.py`                      | LangGraph-backed dynamic-pipeline orchestrator (TemplateRunner). **1% production traffic** (only `dev_diary` template). Drives that template + future architect-composed pipelines. Postgres checkpointer enabled via `template_runner_use_postgres_checkpointer=true`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `prompt_manager.py`                       | UnifiedPromptManager — Langfuse-first, then YAML defaults (poindexter#47). Edits land in the Langfuse UI. **Lane A complete 2026-05-09:** all 7 inline production prompt constants (in `multi_model_qa.py`, `stages/cross_model_qa.py`, `image_decision_agent.py`, `topic_ranking.py`, `writer_rag_modes/deterministic_compositor.py`) migrated to YAML keys (`qa.topic_delivery`, `qa.consistency`, `qa.review`, `qa.aggregate_rewrite`, `image.decision`, `topic.ranking`, `narrative.system`). Snapshot tests pin every rendered body byte-for-byte. Tracked under `Glad-Labs/poindexter#450`.                                                                                                                                                                            |
| `settings_service.py`                     | DB-backed config (app_settings, 700+ active keys)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `site_config.py`                          | DI seam over settings — `class SiteConfig` constructed by `main.py` and DI'd via `Depends(get_site_config_dependency)`. The `glad-labs-stack#330` sweep landed 2026-05-08: only 3 legitimate callers still import the module-level singleton (the settings-reload endpoint, the reload job, and the test fixture). New code uses the DI seam; the final singleton deletion is tracked as `glad-labs-stack#333`. CI guardrail at `scripts/ci/check_site_config_singleton.py` blocks new offenders.                                                                                                                                                                                                                                                                            |
| `cost_guard.py`                           | Daily/monthly spend limits + energy estimates (watt-hours per 1K tokens) for the cost dashboard's "is Ollama actually greener than this cloud SKU?" comparison. Lines 72-100 are ENERGY defaults — NOT USD prices despite the audit's earlier wording. Operators tune per-model via `plugin.llm_provider.<provider>.model.<model>.energy_per_1k_wh`.                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `cost_lookup.py`                          | LiteLLM-backed cost lookup (wraps `litellm.model_cost`). The `model_router.py` / `usage_tracker.py` / `model_constants.py` trio is **deleted** (Phase 2 cleanup, 2026-05-08). Lane B introduced the `cost_tier` API: callers do `model = await resolve_tier_model(pool, "standard")` (in `services/llm_providers/dispatcher.py`); operators tune via `app_settings.cost_tier.<tier>.model` rows. End-of-Lane-B vestigial cleanup completed 2026-05-09: `quality_service.py` + `agents/blog_quality_agent.py` no longer accept `model_router=` kwargs. `firefighter_service.py:268` keeps its `model_router` param — that's a duck-typed `_ModelRouterLike` Protocol injected from `routes/triage_routes.py` (function reference for tests), unrelated to the deleted module. |
| `llm_providers/litellm_provider.py`       | LiteLLM-backed `LLMProvider` plugin (provider routing + cost tracking + retries via mature OSS). Distinct from #199; activated by setting `plugin.llm_provider.primary.standard='litellm'`. Production cutover gate.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `research_service.py` / `web_research.py` | Topic research + web fact-check                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `publish_service.py`                      | Final publish + scheduled_publisher integration                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `quality_service.py`                      | Quality scoring orchestration                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `internal_link_coherence.py`              | Auto-adds related post links                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `social_poster.py`                        | Generates X/LinkedIn posts via Ollama; distribution is row-driven through `publishing_adapters` (poindexter#112) — adding a new platform = insert a row + register a `publishing.<name>` handler.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `newsletter_service.py`                   | Weekly digest generator                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |

**Content pipeline stages:**

1. Research (Ollama) → 2. Draft (Ollama) → 3. QA Score (Ollama) → 3.5. Programmatic validator → 3.7. Cross-model review (Claude Haiku) → 4. SEO metadata → 5. Training data capture → 6. Finalize (awaiting_approval or auto-publish if score >= 80)

**Database tables (key ones):**

- `content_tasks` — pipeline task queue and results
- `posts` — published blog posts
- `app_settings` — all config (replaces env vars)
- `affiliate_links` — partner links (DB-managed)
- `page_views` — own analytics tracking
- `brain_knowledge` — knowledge graph (entity/attribute/value)
- `brain_queue` — reasoning queue for the brain
- `brain_decisions` — decision audit trail
- `pipeline_gate_history` — typed history of HITL gate approvals + regen retries (poindexter#366 phase 1, replaces gate-state slice of the dropped pipeline_events table)
- `audit_log` — canonical historical record (queried by `routes/pipeline_events_routes.py` despite the legacy URL prefix)
- `cost_logs` — LLM API cost tracking

### Frontend (`web/public-site/`)

Next.js 15 app router. ISR with 5-minute revalidation. Features:

- Blog posts with internal links, affiliate links, related reading
- Giscus comments (GitHub Discussions)
- Google AdSense (ca-pub-4578747062758519, pending approval)
- Google Analytics (G-NJMBCYNDWN)
- ViewTracker beacon (own analytics → page_views table)
- Sitemap.xml (dynamic, 72+ URLs)
- Google Search Console verified

### MCP Server (`mcp-server/`)

Custom MCP server for Claude desktop app. 25 tools across content / approval / settings / memory / observability surfaces. The sibling `mcp-server-gladlabs/` adds 3 operator-only tools layered on top (private to the Glad Labs operator overlay; not in the public mirror).

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

**Everything else lives in `app_settings` (450+ active keys).** Code accesses
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
(620 keys)`). The job receives the lifespan-bound SiteConfig via
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

- **Grafana (self-hosted):** http://localhost:3000 (or http://100.81.93.12:3000 from the tailnet) — 8 dashboards. Grafana Cloud was retired 2026-05-03; the local Docker container (poindexter-grafana) is the only Grafana now. Local Prometheus scrapes windows_exporter + nvidia-smi-exporter directly; Alloy was the Cloud shipper and is no longer used.
- **Dashboards:**
  - **Mission Control** — top-level operator view
  - **Pipeline** — content pipeline throughput + stage durations
  - **Auto-Publish Gate** — score distribution / approval-rate decisions
  - **Cost & Analytics** — LLM spend, energy, posts published
  - **Observability** — Pyroscope flame graphs, log volumes, error rates
  - **System Health** — worker / brain / voice container health
  - **Integrations & Admin** — qa_gates / publishing_adapters / external_taps tables
  - **QA Rails — Multi-Model Review** (`/d/qa-rails`) — per-reviewer pass-rate, score distribution, latest QA passes. Powered by `audit_log` rows where `event_type='qa_pass_completed'` (one row per `MultiModelQA.review` call, full reviewer breakdown in JSON details). Created 2026-05-10 alongside the Lane D #329 close-out.
- **Alerts → Telegram + Discord:** stuck tasks, failure rate, worker offline, GPU temp, VRAM usage. Routing rules in `infrastructure/grafana/provisioning/alerting/`.
- **Playlist:** "Glad Labs Command Center" cycles all dashboards every 30s.
- **Pyroscope app-profiles (Glad-Labs/poindexter#406):** CPU flame graphs ship from the worker, brain, and voice agents under four `service_name` values — `poindexter-worker`, `poindexter-brain`, `poindexter-voice-livekit`, `poindexter-voice-webrtc`. Master switch is `app_settings.enable_pyroscope` (default true post-#406); per-service panel lives on the Observability dashboard.
- **GlitchTip (self-hosted Sentry):** http://localhost:8080 — runtime exceptions from worker / brain / voice. Org `glad-labs`, project `poindexter`. Sentry SDK auto-initialised in `main.py` when `app_settings.sentry_dsn` is set (provisioned 2026-05-09).
- **Langfuse:** http://localhost:3010 — every reviewer LLM call (DeepEval g-eval / faithfulness, Ragas, the legacy critic) traces here. Use it to drill into a specific qa_pass_completed event and read the judge model's reasoning.

## Cron Jobs (re-create on new sessions)

```
Self-healing agent: hourly at :13 — health check + auto-fix
Code quality agent: every 4h at :37 — security/dead code/error handling scans
```

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
- **LangGraph cutover (Lane C):** [`docs/architecture/langgraph-cutover.md`](docs/architecture/langgraph-cutover.md) — staged rollout for replacing the legacy chunked StageRunner flow with the `canonical_blog` LangGraph template. Opt-in via `app_settings.default_template_slug`; default empty = current behaviour preserved.
- **Prefect cutover (#410):** [`docs/architecture/prefect-cutover.md`](docs/architecture/prefect-cutover.md) — Phase-0 shipped 2026-05-10. Replaces `task_executor.py`'s polling daemon + retry loop + stale-task sweep with native Prefect dispatch. Cutover seam: `app_settings.use_prefect_orchestration` (default `false`). When flipped to `true`, TaskExecutor short-circuits and Prefect's deployment owns dispatch entirely. Operator UI moves from custom metrics endpoint to Prefect UI at port 4200.
- **Module v1 (Glad-Labs/poindexter#490):** [`docs/architecture/module-v1.md`](docs/architecture/module-v1.md) — shipping incrementally as in-tree scaffolding, deferring physical code moves until 2+ business modules exist (avoids refactoring for sample-size-1 symmetry). Phase 1 (Module Protocol + `get_modules()` registry + manifest validation) shipped 2026-05-13. Phase 2 (per-module migration runner + `module_schema_migrations` table + boot wiring in `startup_manager._run_migrations`) shipped 2026-05-13. Phase 3-lite (in-tree `ContentModule` skeleton at `src/cofounder_agent/modules/content/`, no physical pipeline-code moves) shipped 2026-05-13. Phase 4-lite (route auto-discovery in `utils/route_registration.register_all_routes` — iterates `get_modules()` after substrate routes mount, calls each module's `register_routes(app)`) shipped 2026-05-13. **Decomposition philosophy** ([memory: `project_module_decomposition_axes`](#)): capability plugins (existing 20 entry-point groups: llm/image/video/audio/tts) and business modules (Module v1: ContentModule, future FinanceModule etc.) are orthogonal axes — business modules COMPOSE capability plugins. Deferred to Phase 3.5/4.5/5 (when 2+ modules concretely justify them): physical pipeline-code moves, dashboard auto-discovery, CLI subparser threading, brain-probe iteration, `visibility` sync-filter rewrite.
- **Latest session handoff:** `~/.claude/projects/C--Users-mattm/memory/session_62_handoff.md`
- **Architecture vision:** `~/.claude/projects/C--Users-mattm/memory/project_brain_architecture.md`
- **Revenue model:** `~/.claude/projects/C--Users-mattm/memory/project_revenue_model.md`
