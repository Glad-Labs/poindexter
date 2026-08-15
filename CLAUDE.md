# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## IMPORTANT: New session startup

If this is a new session, read these in order:

All three live in `~/.claude/projects/-home-mattm-glad-labs-website/memory/`:

1. `user_profile.md` — How Matt thinks and communicates
2. `decision_log.md` — Key decisions and WHY
3. `MEMORY.md` — auto-memory index (recent context, feedback, project state)

(The old `C--Users-mattm*` project dirs were the Windows-era paths and no
longer exist — the Pop!_OS migration re-keyed the memory dir to the Linux
checkout path.)

## Project Overview

Glad Labs is an AI-operated content business — a solo founder using AI to run an autonomous content pipeline that generates, reviews, publishes, and monetizes blog content.

**Architecture vocabulary — kernel / module / capability.** The code's real
structure is **kernel** (the substrate everything rents — plugin registry, DI
container, pipeline engine, settings; `plugins/`, `services/`), **modules**
(manifested business functions — `modules/content/`, `modules/finance/`; Module
v1), and **capabilities** (the 18 entry-point plugin groups modules compose —
llm / image / video / audio / tts). New work answers "where does this go?" in
those terms; the kitchen-metaphor and brain-region framings in older docs map
onto this one. Two anatomy labels still pay rent and stay as proper nouns:

- **Brainstem** (`brain/`) — standalone self-healing watchdog daemon, genuinely
  independent of the FastAPI app (only needs Python + asyncpg).
- **Spinal Cord** — PostgreSQL as the bus: components really do communicate
  through shared DB tables, not imports.

> _Decoding older docs only — never reach for these in new code:_ Cerebrum =
> FastAPI backend, Cerebellum = anticipation engine + QA registry, Limbic System
> = brain_knowledge graph + revenue engine, Thalamus = process composer + API
> layer, Hypothalamus = settings service + cost guard. They were labels over
> arbitrary groupings that business modules cut straight across.

### Production URLs

**Production / public surfaces:**

| Service | URL |
| --- | --- |
| Public site | https://gladlabs.io (→ www.gladlabs.io) |
| Public docs | https://gladlabs.mintlify.app |
| Voice (LiveKit) | https://nightrider-1.taild4f626.ts.net/voice/join (tap-to-join, Tailscale Serve — tailnet-only; moved off the public Funnel 2026-06-02). **DOWN since the Pop!_OS migration** — `tailscale serve status` on this box reports "No serve config", so the Serve proxy still has to be re-established (open Matt action, see the migration memory). |
| Private repo | https://github.com/Glad-Labs/glad-labs-stack |
| Public repo | https://github.com/Glad-Labs/poindexter (auto-mirror) |
| Project board | https://github.com/orgs/Glad-Labs/projects/2 |

**Local services** (Docker, accessible via http://localhost:&lt;port&gt; on Matt's PC, or via http://nightrider-1.taild4f626.ts.net:&lt;port&gt; on the Tailnet — prefer the MagicDNS **name** over the tailnet IP, which was `100.81.93.12` on the retired Windows node and moved during the Pop!_OS migration; the name follows the node, a baked-in IP does not):

| Service | URL / Port | What it's for |
| --- | --- | --- |
| Backend API | http://localhost:8002 | FastAPI worker (poindexter-worker container) |
| Brain daemon | Local process (brain/), no HTTP | Self-healing watchdog — Telegram alerts on failure |
| Grafana | http://localhost:3000 | 12 dashboards (Mission Control / Pipeline / Cost / Observability / System Health / Integrations / **QA Rails** / **Findings** / **Experiments & Dry-Run** / **Database** / **Hardware & Power** / **SEO Harvest**; Revenue is parked in `dashboards-parked/` until revenue_events has data) |
| QA Rails dashboard | http://localhost:3000/d/qa-rails | Per-reviewer pass-rate, score distribution, latest QA passes (#329 Lane D) — created 2026-05-10 |
| Findings dashboard | http://localhost:3000/d/findings | Probe-findings routing — emitted/pending-delivery counts, by-kind/severity, kind→delivery-policy, latest findings (#461 Phase 4) — created 2026-06-02 |
| Langfuse | http://localhost:3010 | LLM trace explorer + prompt-catalog review UI (read-only mirror of the SKILL.md packs since poindexter#825 — prompt edits go in the repo; every reviewer LLM call is traced) |
| GlitchTip | http://localhost:8080 | Self-hosted Sentry — runtime errors from worker / brain / voice agent (org `glad-labs`, project `poindexter`) |
| pgAdmin | http://localhost:18443 | Postgres admin — direct DB access (login: see bootstrap.toml) |
| Prefect | http://localhost:4200 | Orchestration UI for the Prefect server (flow runs, schedules) |
| Pyroscope | http://localhost:4040 | Continuous profiler — flame graphs from worker / brain / voice (`service_name` tag) |
| Uptime Kuma | http://localhost:3002 | External-uptime monitor |
| Tempo | http://localhost:3200 | Trace storage (consumed via Grafana Explore — Tempo datasource) |
| Loki | http://localhost:3100 | Log storage (consumed via Grafana Explore — Loki datasource) |
| Prometheus | http://localhost:9091 | Metrics storage (consumed via Grafana datasource) |
| AlertManager | http://localhost:9093 | Alert-routing UI |
| LiveKit (local) | ws://localhost:7880 | Local LiveKit server (the Tailscale **Serve** tailnet proxy fronts `/voice/join` → this; moved off the public Funnel 2026-06-02) |
| image-gen server | http://localhost:9836 | Local image generation backend |
| Postiz | http://localhost:5003 | Self-hosted social distribution hub (opt-in: `--profile postiz`). Connect X/LinkedIn/Reddit/Mastodon/TikTok/Instagram OAuth accounts here; copy integration UUIDs into `postiz_integration_id_*` app_settings. |

### Key Numbers (repo-derived stats auto-synced daily by CI; DB-derived counts last refreshed 2026-08-14)

> **Editing note:** `.github/workflows/sync-claude-md.yml` rewrites exactly three
> phrases in this section by regex — `<N> Python files under
> `src/cofounder_agent/services/``, `<N> test files`, and `<N> Grafana
> dashboards` (first match only). Keep those wordings intact or the sync
> silently stops updating — that is not hypothetical: rewording the
> `app_settings` bullet here killed its anchor, and the very next nightly run
> (#2825) refreshed `pipeline_tasks` and `embeddings` while leaving that count
> frozen, with nothing in the log to say so. A missed anchor now prints a
> `WARNING:` into the PR body instead of passing as "already correct".
>
> The heading's own `DB-derived counts last refreshed <date>` clause is an
> anchor too: `scripts/sync_claude_md_db_stats.py` restamps the date whenever it
> moves a DB count, so the marker tracks the numbers rather than the last human
> edit. The nightly PR itself was broken from 2026-06-10 to 2026-07-26 (`gh pr
> create` ran against the shared checkout instead of the session worktree, fixed
> in #2809; first clean unattended run #2825, 2026-07-26).

- 179 live posts on gladlabs.io (353 posts total; 2,033 pipeline_tasks across all generation runs)
- 427 Python files under `src/cofounder_agent/services/` (~291 substantive after `__init__.py` stubs)
- **Deleted trees** (May 2026 cleanup wave; full detail in git) — if a doc or commit references one, it is gone and should not be recreated: the whole `workflow_executor` chain (plus `phases/`, `agents/`, `schemas/custom_workflow_schemas.py`, ~3,800 LOC); `services/task_executor.py` (~1,500 LOC, replaced by `services/flows/content_generation.py` on Prefect); and `plugins/stage_runner.py` with the legacy chunked `content_router_service` path.
- **Cutover gates are all `true` on prod** — Prefect is the dispatcher, `canonical_blog` is the pipeline, LiteLLM is the LLM router, LlamaIndex + Ragas + DeepEval + Guardrails are all on. `atom_runs` + `services/atom_runs.py` capture per-atom run + outcome, gated by `atom_runs_capture_enabled`.
- **Migration files** — the Phase G squash (2026-07-11) folded the Phase F baseline and its 42 post-baseline migrations into a fresh `0000_baseline.py` (+ `.schema.sql` + `.seeds.sql`). 32 ordinary migrations have accumulated since (`20260712_084907_rename_opening_to_content_originality.py` … `20260809_180123_reseed_dev_diary_graph_def_onto_shared_canonical_atoms.py` — spell the newest one out in full, `.py` and all: `claude_md_sync`'s `migration_drift_note` is a literal substring test for that filename, so a `_*` glob leaves the nightly Discord note firing no matter how current the count is). Baseline seeds: non-secret `app_settings` 692 (+2 empty secret placeholders), `pipeline_templates` 6, `qa_gates` 19, `retention_policies` 32. Mechanics in the Database-migrations section.
- 12 Grafana dashboards (named in Monitoring below; Revenue parked until `revenue_events` has real writes). Alerting is two-sourced: **19 static** rules across 4 active `infrastructure/prometheus/alerts/*.yml` files (`postgres-connections.yml` is a 5th, comment-only placeholder since its rules went DB-rendered for app_settings-tunable thresholds), plus **38 DB-sourced defaults** rendered into `rules/*.yml` by `RenderPrometheusRulesJob` every 5 min (incl. the six `Ups*` NUT rules, poindexter#958, and the `Cpu Temperature*` pair) — so the live count is 19 + whatever the DB currently seeds. Verify with `len(DEFAULT_RULES)` rather than trusting this number; it read "~29" while the real count was 36. Worker-gauge rules use a long `for:` with `last_over_time`/`max_over_time` to bridge deploy-restart scrape holes; see the restart-gap policy comment atop `DEFAULT_RULES` in `services/prometheus_rule_builder.py`.
- ~17,210 test functions across 1024 test files under `tests/unit` (+ 16 integration + 40 integration_db). Last full-green run 2026-08-12 on `4ee768bb2` — 17,190 passed, 21 skipped, 1 xfailed, 0 failed, 0 collection errors; 10m46s serial on host. Re-derive with a full run rather than trusting this number; it read "~11,440 / 10,352 passed" from the 2026-06-09 nightly for two months, understating the suite by ~5,800 functions. The 21 host skips are environmental, not flake: 13 memory tests want `LOCAL_DATABASE_URL`, 5 want ffmpeg/ffprobe on PATH, 1 wants the `rerank` extra, and 2 are deliberate opt-in canaries (`REGEN_GRAPH_DEF_FP`, the release-please version-drift check). A *different* handful skips in-container due to the `Path(__file__).parents[5]` path-depth quirk — those run on host.
- 1,438 app_settings keys live on prod (69 secret; drifts as keys are added). Three sources seed the table — `settings_defaults.py` 769, `0000_baseline.seeds.sql` 694 (= 692 non-secret + 2 placeholders), `brain/seed_app_settings.json` 80 free-tier — and overlapping keys must agree, enforced by `scripts/ci/settings_seed_value_drift_lint.py`.
- `plugins/registry.py` `_SAMPLES` holds 104 core-sample plugins, of which 61 are job-type (taps + retention + memory hygiene + content surfaces); the rest are 14 stages / 8 taps / 8 topic_sources / 6 image / 4 llm / 1 probe / 1 video / 1 audio. Several jobs are dormant behind master switches, so the live scheduled count is lower. The image providers cover three `kind`s — `search` (pexels, pexels_video), `generate` (image_gen, ai_generation, flux_schnell), and `screenshot` (screenshot, poindexter#1002: renders an **allow-listed** operator surface for posts about Poindexter itself; see [`docs/architecture/screenshot-image-provider.md`](docs/architecture/screenshot-image-provider.md)).
- 5 declarative-data-plane tables (`external_taps` / `retention_policies` / `webhook_endpoints` / `publishing_adapters` / `qa_gates`) feeding the integrations handler registry's 14 handlers across 4 handler surfaces (`tap` ×4 / `retention` ×6 / `outbound` ×2 / `publishing` ×2). `webhook_endpoints` is a declarative _table_ with no handler surface of its own — its rows are consumed by `integrations/outbound_dispatcher.py` + `operator_notify.py`. A 6th `services.declarative_config_service`-managed table, `alert_rules` (`poindexter alerts`), is CRUD'd through the same generic service but synced by the bespoke `brain/alert_sync.py` loop rather than the handler registry.
- 68,341 embeddings across posts / issues / audit / memory / brain / claude_sessions (retention prunes claude_sessions/brain vectors, so this drifts)
- $0/month infra cost (fully self-hosted; only business-level paid services sit outside the pipeline)

## Development Commands

### Starting Services

```bash
npm run dev                  # Backend (:8002) + frontend (:3000) concurrently, for host dev
npm run dev:cofounder        # Backend only (uvicorn via the backend poetry env, port 8002)
npm run dev:public           # Next.js only (port 3000 — collides with Grafana while the operator Docker stack is up)
npm run setup                # npm install + poetry -C src/cofounder_agent install

# Docker backend stack (how the backend actually runs in production):
docker compose -f docker-compose.consumer.yml up -d                     # Consumer stack (8-16 GB VRAM / 32 GB RAM)
docker compose -f docker-compose.consumer.yml --profile image-gen up -d      # + image-gen generation
bash scripts/start-stack.sh up -d                                        # Full operator stack (Matt's PC — decrypts bootstrap.toml first)
```

### Testing

```bash
# Python backend (equivalently: npm run test:python / test:python:integration)
cd src/cofounder_agent && poetry run pytest tests/unit/ -q    # Unit tests
cd src/cofounder_agent && poetry run pytest tests/integration/ -q  # Integration

# JavaScript
npm run test                  # Jest for public site
npm run test:console          # Operator-console JS tests (node:test + vm, no jsdom)

# Playwright E2E
npm run test:e2e              # All Playwright tests (headless)
```

### Code Quality

```bash
npm run lint                  # ESLint all workspaces
npm run format                # Prettier
npm run lint:python           # Ruff over the backend
npm run type:check            # Python mypy (backend poetry env)
```

## Architecture

### Brain Daemon (`brain/`)

**Containerized daemon (`poindexter-brain-daemon`).** Independent of FastAPI — only needs Python + asyncpg. Runs as a sibling container in the stack (was a host process pre-2026-05; the containerization happened during the same cleanup wave that moved diffusers/torch out of the worker image).

- Monitors site, API (5-minute cycles)
- Self-maintains knowledge graph (brain_knowledge table)
- Logs all decisions (brain_decisions table)
- Alerts via Telegram when services are down
- Auto-restarts local services when running on Matt's PC

### Backend (`src/cofounder_agent/`)

**Entry point:** `main.py` — FastAPI app with two deployment modes:

- `DEPLOYMENT_MODE=coordinator` — minimal read-only API (intended for future cloud host; currently unused)
- `DEPLOYMENT_MODE=worker` (local PC) — claims tasks, runs content pipeline via Ollama

**Modules** (Module v1 — `src/cofounder_agent/modules/`):

The 18th (and newest) entry-point group in the plugin registry. Each Module bundles
capability-plugin contributions plus DB migrations, jobs, HTTP routes,
and Grafana panels into a manifested business function.

| Module | Path | Visibility | What it does |
| --- | --- | --- | --- |
| `content` | `modules/content/` | public | Reference Module. Owns the content code: `content_validator`, `stages/`, `atoms/`, `multi_model_qa`, `ai_content_generator`, `internal_link_coherence`, `quality_service`, `auto_publish(_gate)`. |
| `finance` | `modules/finance/` | private | Mercury read-only banking (Glad Labs operator overlay). `/api/finance/{healthcheck,balances,transactions}`, OAuth-JWT protected. Stripped from public mirror via sync filter. |

**Generic pipeline engine stays in substrate** — `template_runner`,
`pipeline_architect`, `prompt_manager`, `llm_text`, `atom_registry`, plus
`canonical_blog_spec` (imported by historical migrations). Content rents the
engine via the DB `graph_def` seam, so the engine never imports content.

**`modules/content/api.py` is the thin-adapter boundary** — substrate reaches
content through it, never by direct import. Three string-path registries are
out of scope (dynamic `importlib`, so they can't route through a Python import):
`plugins/registry.py` `_SAMPLES`, `services/atom_registry.py` walk-root,
`services/http_client.py` `WIRED_HTTP_CLIENT_MODULES`.

NOTE for path lookups: many `services/<name>.py` references elsewhere in this
file are historical narrative; the live content code is under `modules/content/`.

Adding a new business module (HR, customer support, ops/security) follows
the [extending-poindexter §9 walkthrough](docs/operations/extending-poindexter.md);
finance operator specifics live in
[docs/operations/finance-module-operator.md](docs/operations/finance-module-operator.md)
(also stripped from public mirror).

**Key services (22 load-bearing):**

| Service | Purpose |
| --- | --- |
| `content_router_service.py` | Thin TemplateRunner dispatcher. Builds the shared pipeline context (image_service, settings, style tracker, site_config, models_by_phase, experiment assignment) and hands it to `TemplateRunner.run(template_slug, context)` keyed on `pipeline_tasks.template_slug`. A NULL `template_slug` fails loud per `feedback_no_silent_defaults`. New `canonical_blog` nodes go on the graph_def spec (`services/canonical_blog_spec.py`), NOT here. |
| `content_validator.py` | Anti-hallucination rules (programmatic, no LLM). Includes `json_envelope_leak` detection so leaked `{"content":"..."}` writer output gets a producer-specific diagnostic. |
| `multi_model_qa.py` | The QA **rail library** the `qa.*` atoms delegate to (the monolithic `cross_model_qa` stage is deleted). Six rails: DeepEval ×3 (`deepeval_brand_fabrication`, `deepeval_g_eval`, `deepeval_faithfulness`); guardrails ×2, reimplemented native/dep-free in `services/guardrails_rails.py` after `guardrails-ai` was dropped for CVE-2026-45758 (`guardrails_brand` runs `content_validator` patterns, `guardrails_competitor` regexes the `guardrails_competitor_list` CSV); and Ragas ×1 (`ragas_eval`, averaging faithfulness + answer-relevancy + context-precision). All on in prod (`deepeval_enabled` / `guardrails_enabled` / `ragas_enabled` = `true`). Advisory-vs-hard-gate is DB-driven per rail via `qa_gates.<rail>.required_to_pass` (`_mark_advisory_if_configured`). |
| `rag_engine.py` | LlamaIndex `BaseRetriever` over the `embeddings` pgvector table (`rag_engine_enabled=true` on prod). Three modes stack: vector-only → `HybridRRFRetriever` (BM25 tsvector + pgvector + RRF fusion, `rag_hybrid_enabled=true` — pure SQL, default on since it needs no extra dependency) → `CrossEncoderRerankRetriever` (sentence-transformers cross-encoder, `rag_rerank_enabled=true` on Matt's prod but code-defaults **false**, since it needs the optional `rerank` poetry extra and a fresh install should opt in rather than warn-spam). `embeddings.text_search` is `GENERATED ALWAYS AS (to_tsvector('simple', text_preview))` — auto-populated, GIN-indexed (`idx_embeddings_text_search`). See [`docs/architecture/rag-retrieval-stack.md`](docs/architecture/rag-retrieval-stack.md). |
| `qa_gates_db.py` | Declarative QA gate definitions (DB-driven) |
| `flows/content_generation.py` | Prefect-orchestrated content pipeline — **sole task dispatch path**. Claims a pending `pipeline_tasks` row, runs it through `content_router_service.process_content_generation_task`, then fires post-pipeline side-effects via `post_pipeline_actions.run_post_pipeline_actions`. Cron / retry / heartbeat / stale-run sweep are all Prefect-native. Deployment registered by `src/cofounder_agent/scripts/deploy_content_flow.py`; operator UI at http://localhost:4200. |
| `template_runner.py` | LangGraph-backed dynamic-pipeline orchestrator (TemplateRunner). **PRIMARY PIPELINE PATH** (`default_template_slug=canonical_blog` on prod). `run()` prefers the DB-stored `graph_def` (compiled by `pipeline_architect.build_graph_from_spec` via `load_active_graph_def`) when `pipeline_use_graph_def=true` — the prod default — else the legacy Python `TEMPLATES` factory, now `dev_diary`-only. Postgres checkpointer enabled via `template_runner_use_postgres_checkpointer=true`. |
| `prompt_manager.py` | UnifiedPromptManager — **SKILL.md packs are authoritative**: prompt edits land in `skills/*/*/SKILL.md` via PR; Langfuse is a read-only mirror kept current by `SyncPromptCatalogToLangfuseJob` (6-hourly). Legacy Langfuse-first override lookup sits behind `langfuse_prompt_overrides_enabled` (default `false`) — enabling it re-introduces the masking trap, so deliberate live experiments only. |
| `settings_service.py` | DB-backed config (app_settings, ~685 active keys in-cache; ~717 with secrets) |
| `site_config.py` | DI seam over settings — `class SiteConfig` constructed once per entry point and reached through the `AppContainer` composition root (`services/container.py`). Route handlers DI it via `Depends(get_site_config_dependency)`; services take `site_config` as a constructor arg. The legacy per-module `set_site_config(loaded_instance)` fan-out is retired (`di_wiring.WIRED_MODULES` is empty); do **not** add new `set_site_config` setters. |
| `cost_guard.py` | Daily/monthly spend limits + energy estimates (watt-hours per 1K tokens) for the cost dashboard. Its ENERGY defaults block is watt-hours — **NOT** USD prices. Operators tune per-model via `plugin.llm_provider.<provider>.model.<model>.energy_per_1k_wh`. |
| `cost_lookup.py` | LiteLLM-backed cost lookup (wraps `litellm.model_cost`) — per-token pricing only. Model selection is per-step `*_model` pins read at each call site; the `cost_tier.*` tier→model API and `resolve_tier_model` were removed. See [`docs/architecture/cost-tier-routing.md`](docs/architecture/cost-tier-routing.md). |
| `llm_providers/litellm_provider.py` | LiteLLM-backed `LLMProvider` plugin (provider routing + cost tracking + retries via mature OSS). **PRIMARY LLM ROUTER** — `plugin.llm_provider.primary.{free,budget,standard,premium}='litellm'` on prod. Every LLM call flows through `dispatch_complete`; the direct `httpx` callers against `/api/chat` + `/api/generate` are retired. Langfuse callback auto-traces every call. |
| `llm_text.py` | Plain-text Ollama chat helper for atoms + writer_rag_modes. Routes through `dispatch_complete` when a pool is available, propagating provider-swappability to the writer paths (`narrate_bundle`, `deterministic_compositor`, `pipeline_architect`, `two_pass._revise_node`, `story_spine`). Direct-httpx fallback retained for tests/bootstrap. `maybe_unwrap_json` at the result boundary catches models that emit JSON envelopes unprompted. |
| `research_service.py` / `web_research.py` | Topic research + web fact-check |
| `publish_service.py` | Final publish + scheduled_publisher integration |
| `quality_service.py` | Quality scoring orchestration |
| `auto_publish_gate.py` | Auto-publish decision logic — imported by `stages/finalize_task.py`. **Two gates stacked**: (1) the global `auto_publish_threshold` (default `0` = disabled) in `modules/content/auto_publish.py::get_auto_publish_threshold`; (2) the per-niche edit-distance gate (`{niche_slug}_auto_publish_threshold` / `_dry_run` / `_min_clean_runs` / `_max_edit_distance`) in this file. Gate 2 fires only when the niche has explicit opt-in keys AND `quality_score ≥ threshold` AND the trailing-N approves had `char_diff < max_edit_distance`. **Three invariants, each earned by an outage — do not regress them:** niche keys must be `{niche_slug}_*` and never hardcoded to one niche (a hardcoded `dev_diary_*` once leaked that niche's opt-in to glad-labs and auto-published a post without authorisation); `niche_slug` MUST stay a declared `PipelineState` channel, or LangGraph drops it at ainvoke and every evaluation returns `disabled: "niche_slug missing"` (this silently starved the gate for six weeks); and the trailing-history query is niche-ONLY — an `OR category` clause lets other niches' rows pollute the clean-run window. `evaluate()` resolves the task's own niche from `pipeline_tasks` when the caller passes None (fail-closed). Helpers live in `modules/content/auto_publish.py`. **Veto window (2026-08-14):** a niche with `{niche_slug}_auto_publish_delay_hours > 0` fires in hold-mode — the post is staged (`stage_only`) + slotted `delay` hours out via `scheduling_service.assign_slot` (posts row carries `metadata.auto_publish_veto_window='true'`), and the operator is pinged with the veto command. Doing nothing ships it at the slot; `poindexter auto-publish veto <task>` — or rejecting the task from ANY surface — cancels it: `scheduled_publisher._demote_vetoed_auto_posts` re-checks the source task at fire time (social-scheduler approve-at-fire-time pattern) and demotes + deletes the stage-time `approver='auto_publish'` edit-metrics row, so a vetoed run never counts as a clean run in the gate's trust window. |
| `worker_service.py` | Registers the worker, maintains the heartbeat, wires `faulthandler.dump_traceback_later` hang diagnostics. Without it the worker has no DB presence and the brain can't see it. |
| `internal_link_coherence.py` | Auto-adds related post links |
| `social_poster.py` | Generates X/LinkedIn posts via Ollama; distribution is row-driven through `publishing_adapters` — adding a new platform = insert a row + register a `publishing.<name>` handler. |
| `newsletter_service.py` | Weekly digest generator |

**Content pipeline stages (`canonical_blog` graph_def):**

`canonical_blog` runs as a static `graph_def` row in `pipeline_templates` (`active=true`), authored in `services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF` and compiled by `services/pipeline_architect.py::build_graph_from_spec` (which validates `requires`/`produces` reachability at build/seed time). `TemplateRunner.run` prefers it when `pipeline_use_graph_def='true'` — the prod default — else falls back to the legacy Python `TEMPLATES` factory, which now holds `dev_diary` only. New `canonical_blog` nodes go on the graph_def spec, not on a factory.

**`CANONICAL_BLOG_GRAPH_DEF` is the authoritative node list — read it rather than trusting a count here** (this line has drifted from the spec before). At the v9 reseed (2026-08-06 — `v<N>` is `pipeline_templates.version` on the stored row, so check that, not this date) it was 44 nodes: 11 `stage.*` + 14 `content.*` + 14 `qa.*` + 1 `qa.rewrite` + 1 `seo.*` + 2 `atoms.approval_gate` + 1 `social.generate_drafts` — a mostly-linear chain plus **three** bounded backward cycles: one QA rescue plus two `preview_gate` regen paths (both documented below). Blocks in order:

1. **verify** — `verify_task`
2. **writer** — `content.generate_draft` → `content.generate_title` (draft-grounded: the prompt reads an article digest — a `title_content_excerpt_chars` excerpt plus the section-heading skeleton — with the topic demoted to assignment-label context, plus a `services/title_avoidance.py` variety block that names the recent corpus's *habits* rather than pasting its titles — see [`docs/architecture/title-variety.md`](docs/architecture/title-variety.md)) → `content.check_title_originality` → `content.normalize_draft` → `draft_gate` (`atoms.approval_gate`, seeded disabled) → `writer_self_review` → `resolve_internal_link_placeholders` → `content.reconcile_citations` (deterministic citation repair — re-links named sources the writer dropped) → `content.llm_reconcile_citations` (grounded-LLM tail; the LLM proposes, code verifies url∈corpus + text-verbatim before applying) → `content.inject_affiliate_links` (keyword-matched, dark-launched behind `affiliate_injection_enabled`) → `quality_evaluation` (pattern-based scorer) → `url_validation`
3. **image** — `content.plan_image_markers` (honours the writer's `[HERO-IMAGE:]` subject and numbers inline `[IMAGE:]` markers up to `writer_max_inline_images`, falling back to the body-fed Image Decision Agent when the writer placed none) → `content.generate_images` → `content.inject_images` → `source_featured_image` (grounded on the writer's hero subject via `_resolve_featured_subject`, else the decision-agent pick, else topic; art style rotates least-recently-used via `_select_style_lru`) → `caption_images` (vision re-caption)
4. **QA rails** — `qa.programmatic` → `qa.critic` → `qa.deepeval` → `qa.ragas` → `qa.vision` → `qa.topic_delivery` → `qa.citations` → `qa.unlinked_attribution` → `qa.consistency` → `qa.self_consistency` → `qa.content_originality` → `qa.title_coherence` → `qa.web_factcheck` → `qa.aggregate`
5. **SEO** — `seo.generate_all_metadata` (single structured call; draft-grounded with a topic-echo guard — `seo_title` must never be the raw topic/directive)
6. **media** — `generate_media_scripts` → `generate_video_shot_list` → `review_video_shot_list` (director self-critique, revising the shot list before Gate 1 on the writer-grade `video_director_model`) → `capture_training_data`
7. **finalize** — `content.compile_meta` → `content.persist_task` → `social.generate_drafts` (Postiz draft rows, one per platform per post) → `content.record_pipeline_version` → `preview_gate` (`atoms.approval_gate`, seeded disabled; **not just a pause — it can route the graph backward into the image or writer block**, see the regen-cycle note below) → `content.evaluate_auto_publish`

**QA rescue cycle:** a `qa.rewrite` node sits off the linear chain, reached by a conditional `qa_aggregate → qa_rewrite` branch edge (`"branch": true`) plus a `qa_rewrite → qa_programmatic` loop edge (`"loop": true`, exempt from the compiler's DAG validation). Only a _rescuable_ reject — a soft critic veto, or a below-threshold score with no hard veto, never a fabrication/gate/`missing_required` veto, and never a draft the #984 truncation detector flags (the severed tail is unrecoverable by revision — poindexter#986) (`_qa_rail_common.is_rescuable_reject`) — gets a revision pass before hard-reject. Bounded by the durable `qa_rewrite_attempts` counter against `app_settings.qa_rewrite_max_attempts` (default 2). `ProbeRescueYieldJob` (daily) pages `qa_rescue_yield_zero` on a 0-for-N conversion streak; yield renders on the QA Rails board.

**preview_gate regen cycle:** `preview_gate` is a _component-scoped regen_ gate (#1851), not merely a pause — so the graph legitimately runs **backward out of the finalize block**. Its spec config carries `regen_targets` (`{"images": "plan_image_markers", "text": "generate_draft"}`), and the atom emits a `_goto` for whichever the operator asked for (`_pending_regen` is read BEFORE pausing, so a queued regen reroutes instead of re-pausing). Two `"branch": true, "loop": true` edges back this — `preview_gate → plan_image_markers` (re-run the image block only, text untouched) and `preview_gate → generate_draft` (re-run the writer block; images refresh on the cascade) — loop-flagged so they're exempt from the compiler's DAG validation, same as the rescue cycle above. Seeded disabled (`pipeline_gate_preview_gate='off'`), so it is dormant until an operator opts in; the edges exist in the graph either way. Design: [`docs/architecture/2026-06-21-component-scoped-regen-gate.md`](docs/architecture/2026-06-21-component-scoped-regen-gate.md).

QA runs as composable `qa.*` atoms in `modules/content/atoms/` that delegate to the `MultiModelQA` rail methods and append to the `qa_rail_reviews` PipelineState channel. `canonical_blog` wires **13** of them (listed in block 4 above); more rail atoms exist on disk than this graph uses — `atom_registry` discovers them all, so an ad-hoc `graph_def` can compose any of them. `qa.aggregate` combines them into the gate decision: on reject it does the DB writes via `modules/content/atoms/_qa_persist.py` and **halts the graph** (outside the rescue cycle it does not auto-rewrite); on approve it promotes `quality_score` and populates `qa_reviews`. Graduating a rail from advisory to blocking is `qa_gates.<rail>.required_to_pass=true`.

`dev_diary` is also a `graph_def` row (`services/dev_diary_spec.py::DEV_DIARY_GRAPH_DEF`, v3, 8 nodes): `verify_task` → `narrate_bundle` → `seo.generate_all_metadata` → `source_featured_image` → `compile_meta` → `persist_task` → `record_pipeline_version` → `evaluate_auto_publish`. **Every node but the writer is the same atom `canonical_blog` runs** — that invariant is pinned by `tests/unit/services/test_dev_diary_spec.py`, and it is the point: fix an atom once and both graphs improve. No QA rails by design (a status report makes no external claims).

> **This doc previously said dev_diary "has no graph_def row, so it falls back to the legacy `TEMPLATES` factory".** That was false, and the false belief was load-bearing: it was copied into `test_graph_def_contract_freshness.py`, which therefore excluded dev_diary from the drift gate, so nothing watched the graph actually running the dev blog. Meanwhile a 2026-06-02 SEO fix was applied to the (dead) factory and never reached the stored spec — dev_diary posts published without a `<meta description>` for three months (5/25 May → 5/5 August, vs 0/40 for canonical_blog) behind a green test asserting the factory had the node. **`TEMPLATES` is now an empty dict**: no hand-coded factories remain, and it survives only as a test injection seam.

**Database tables (key ones):**

- `pipeline_tasks` — pipeline task queue (worker claims rows; Prefect flow dispatches). The canonical seam back from a `posts` row to its source task is `posts.metadata->>'pipeline_task_id'` (added 2026-05-28); `scheduled_publisher` / `/go-live` / the promote-existing-approved path read this key to sync `pipeline_tasks.status` in lockstep with `posts.status` promotions.
- `pipeline_versions` — generated content + qa_feedback per task version
- `atom_runs` — per-atom run + outcome capture on the graph_def path (run_id, atom, node_id, tier/model, latency, status, io-key digests + outcome join); gated by `app_settings.atom_runs_capture_enabled`. Complementary to `capability_outcomes` (per-(atom,tier,model) router scoring). Added 2026-06-02 (#355).
- `posts` — published blog posts. `metadata->>'pipeline_task_id'` is the canonical seam to the source `pipeline_tasks.task_id` — populated by `publish_service.publish_post_from_task` at insert and backfilled for historical rows by migration `20260528_021920`.
- `app_settings` — all config (replaces env vars). `last_read_at` (#756) records when `SiteConfig.get`/`SettingsService.get` last consulted each key: `FlushSettingsReadTelemetryJob` drains both the `SiteConfig` instance set and the shared `services/settings_read_sink.py` (which `SettingsService.get` records into — the 2026-07 follow-up that closed the false-positive tail) and stamps them every minute (throttled ~1 write/key/hour), `ProbeZeroReaderSettingsJob` surfaces never-read keys past a 30-day grace as an advisory `settings_zero_reader_keys` finding → Discord + the "Settings Lifecycle — Orphan Candidates" panel on the Integrations & Admin board. Advisory only: keys read solely via raw SQL or the brain daemon's own asyncpg process still aren't stamped. See [`docs/architecture/services/site_config.md`](docs/architecture/services/site_config.md).
- `page_views` — own analytics tracking. Ingested rows carry an `is_bot` flag set by `FlagBotPageViewsJob` (windowed `(user_agent, path)` flood-cap + per-UA distinct-paths sweep for full-site crawlers, poindexter#973); reader surfaces (console `/api/analytics/views`, `posts.view_count`, `lab_outcomes_v1`) read the `page_views_human` view, while liveness/anomaly/freshness signals stay on raw `page_views`. See [`docs/architecture/page-views-bot-flag.md`](docs/architecture/page-views-bot-flag.md).
- `brain_knowledge` — knowledge graph (entity/attribute/value)
- `brain_decisions` — decision audit trail
- `pipeline_gate_history` — typed history of HITL gate approvals + regen retries (poindexter#366 phase 1, replaces gate-state slice of the dropped pipeline_events table)
- `audit_log` — canonical historical record (queried by `routes/pipeline_events_routes.py` despite the legacy URL prefix). Also the generic **job-metrics sink**: the scheduler writes an `event_type='job_run'` row (severity `info`, `details={ok, changes_made, duration_ms, metrics}`) per metrics-emitting job fire so any job's `JobResult.metrics` reaches a Grafana panel via the Postgres datasource — gated by `scheduler_job_metrics_capture_enabled`. See [`docs/architecture/job-run-metrics.md`](docs/architecture/job-run-metrics.md). `details` is otherwise an untyped JSONB blob; `services/audit_event_schemas.py` (poindexter#758) is a minimal shape registry validating the two dashboard-load-bearing types — `qa_pass_completed` (QA Rails board, written by both `qa.aggregate` and the dev_diary-path `multi_model_qa.py`) and `finding` (Findings board, written by `utils/findings.py::emit_finding`) — at write time; a shape mismatch logs loud but never drops the row. Unregistered event_types pass through unvalidated; more are added opportunistically, not as a batch sweep.
- `cost_logs` — LLM API cost tracking
- `social_post_drafts` — pending / scheduled / posted / failed / rejected Postiz draft rows, one per platform per post. Created by the `social.generate_drafts` atom after `content.persist_task`; approved via `poindexter social approve <id>` / `POST /api/social/drafts/<id>/approve` / MCP `approve_social_draft`. The `post_id` column is backfilled by `publish_post_from_task` once the post exists. `RetryFailedSocialDraftsJob` hourly retries rows in `failed` status up to `social_draft_max_retries` (default 3). Drafts are generated **speculatively at pipeline finalize — before operator sign-off** (a QA-flagged post rides through to awaiting_approval), so a post rejected afterward would strand its promos in `pending`; `CancelOrphanedSocialDraftsJob` (every 15 min) reaps them, cancelling live (`pending`/`scheduled`/`failed`) drafts whose content task reached a terminal-reject status (`rejected_final`/`rejected`/`dismissed`) — path-independent (keys off task status, not one reject entry point) and self-backfilling. `approve_draft`'s publish-gate is the complementary safety net: it refuses to post unless the promoted post is live (`posts.status='published'`), so an orphan can never actually post. (Retracting an **already-posted** social promo when its published post is later pulled is a known gap — not yet cascaded.) Creation is idempotent per `(pipeline_task_id, platform, platform_config->>'subreddit')` (poindexter#833): the atom pre-filters keys that already have a live-or-posted draft (finalize re-runs spend no LLM calls), `create_draft` skips DB-side via NOT EXISTS + the `ux_social_post_drafts_active_key` partial unique index, and a rejected-only key inserts fresh (reject + regen = new copy to review). **The four key-holding statuses live in one constant, `services/social_drafts.py::_KEY_HELD_STATUSES`** (pending/scheduled/failed/posted) — the guard, `existing_draft_keys`, and the index predicate must agree, so bind the constant rather than inlining a status list. **The table only grows** — 67 of 77 rows were `posted`/`rejected` tombstones by 2026-07-27. The `social_post_drafts.rejected` retention policy (`ttl_prune`, `created_at`, 90d, `filter_sql: status = 'rejected'`) prunes the discarded half; **`posted` is deliberately NOT pruned** and must not be added, because it is a key-holding status — deleting one makes its `(task, platform, subreddit)` key look fresh, so a finalize re-run would regenerate and **re-post** the promo. Pinned by `tests/unit/services/test_social_drafts_retention_policy.py`, which reads `_KEY_HELD_STATUSES` directly so a newly-added status is covered the moment it's added. `GET /api/social/drafts` is therefore paged (`limit` default 50, max 500; `offset`), and `list_drafts` **sorts live rows (`_LIVE_STATUSES` — pending/scheduled/failed) ahead of `created_at DESC`** so the cap can only ever drop tombstones — never strand an approval that the console's action inbox derives from that same list. Counts (`total`, `status_counts`) come back spanning the whole table, because the console's KPI row, filter chips, and rail badge would otherwise report the window and call it the table. Keep both invariants when touching the query.
- **Social scheduling** — `social_post_drafts.scheduled_at` + the `scheduled` status are a **local** post-timing queue, so timing a promo no longer means opening the Postiz UI. `ScheduleSocialDraftsJob` (every 1 minute) auto-slots then fires; firing goes through `approve_draft`, so the publish gate is re-checked **at fire time**. That is why the queue is not pushed to Postiz as `type: schedule` — Postiz would run the gate when the operator picked the time, and a post whose publish slipped would still promote itself at a 404. Manual: `poindexter social schedule <id> "tomorrow 9am"` (also `unschedule` / `queue`, `POST /api/social/drafts/<id>/{schedule,unschedule}`, MCP `schedule_social_draft`). Auto-drip is **double-gated and off by default**: `social_schedule_enabled=true` AND a per-platform entry in `social_schedule_prime_times` (`twitter=09:00,12:30,17:00; linkedin=08:00`) or `social_schedule_offsets` (`twitter=0m,linkedin=3h`) — a platform in neither map is never auto-slotted, and enabling it means promo copy ships on the *post's* approval rather than its own. **Prime times are the setting for time-of-day posting** and take precedence over `social_schedule_quiet_hours`: a quiet window only says where NOT to post, so an 11pm publish with `22:00-07:00` clamped four platforms onto the same 07:00 minute and destroyed the stagger. Slots are de-duplicated per platform so several posts published overnight spread across the listed hours rather than firing as one burst. Operator-typed times resolve in `operator_timezone` (`scheduling_service.parse_when` grew a `tz` kwarg, defaulting to UTC for existing blog callers). A draft past `social_schedule_max_lateness_minutes` (default 180) is **not** fired — it stays queued and raises a `social_draft_overdue` finding, because an outage silently turning a timed promo into an untimed one is worse than a late one. See [`docs/architecture/social-post-scheduling.md`](docs/architecture/social-post-scheduling.md).

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

Custom MCP server for Claude desktop app. 41 tools across content / approval / settings / memory / observability / topics / social surfaces (incl. `findings_list` — probe-findings triage, #461 Phase 4, and `schedule_post` — approve + publish slot in one call, the phone-side mirror of the console slot picker). The sibling `mcp-server-gladlabs/` adds 3 operator-only tools layered on top (private to the Glad Labs operator overlay; not in the public mirror).

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

**Time is UTC-stored, operator-local presented.** `app_settings.operator_timezone`
(IANA; OSS default `UTC`, operator overlay `America/New_York`) drives cron
fire-times and operator-facing timestamps via `services/clock.py`. Storage,
logs, metrics, and traces stay UTC. Wall-clock crons (`0 7 * * *`) are authored
in operator-local time — the scheduler evaluates them in `operator_timezone`
(DST-correct via `zoneinfo`), so don't hand-convert to UTC. See
[`docs/architecture/system-timezone.md`](docs/architecture/system-timezone.md).

### Deployment

Source of truth: `docs/operations/ci-deploy-chain.md`. Two-remote model (post-2026-04-30 gitea decommission):

- **`origin` = `Glad-Labs/glad-labs-stack`** (private GitHub) — full tree (public + Glad Labs operator/premium overlay). Vercel watches this and deploys `www.gladlabs.io`. Push your day-to-day work here.
- **`github` = `Glad-Labs/poindexter`** (public GitHub) — open-source product subset. Refreshed from origin via `scripts/sync-to-github.sh`, which strips private files (web/public-site, web/storefront, mcp-server-gladlabs, marketing, premium dashboards, writing_samples, gladlabs-config, .shared-context, CLAUDE.md, etc.).

**Cross-repo sync is automatic.** GitHub Actions workflow `.github/workflows/sync-to-public-poindexter.yml` runs on every push to `origin/main` and mirrors the filtered subset to Glad-Labs/poindexter in ~30s, authenticating with a dedicated **GitHub App** (`glad-labs-mirror-sync`, installed on poindexter with Contents + Workflows read+write) that mints a short-lived installation token per run — `MIRROR_SYNC_APP_ID` + `MIRROR_SYNC_APP_PRIVATE_KEY` secrets on glad-labs-stack. Migrated 2026-06-13 from a fine-grained PAT (`POINDEXTER_SYNC_TOKEN`) that silently expired and froze the mirror for ~4 pushes; the App mints ephemeral tokens, so there's no annual expiry cliff. (The Workflows permission is required because the mirror tree includes `.github/workflows/*`.) Just `git push origin main` and the public mirror updates itself.

**Mirror force-push posture (intentional):** Glad-Labs/poindexter has `allow_force_pushes: true` in its classic branch protection AND no `non_fast_forward` rule in its ruleset. The mirror is rebuilt from scratch on every sync (filter → force-push), so force-push protection on a derived branch would just keep the mirror permanently stale. The required public-side CI checks (test-backend, migrations-smoke, Mintlify Deployment, the two CodeQL `Analyze` checks, link-rot) live in the **`Main` ruleset** — moved there from classic protection 2026-06-13 when the sync went App-auth. They gate any human direct-pusher, while the `glad-labs-mirror-sync` App is a ruleset **bypass actor** (alongside org/repo admins) so the sync can force-push past them. The old PAT bypassed these as an admin (it ran as Matt); an App installation token is not an admin, hence the explicit ruleset bypass. **Do not re-enable force-push protection on the public mirror, and do not remove the mirror-sync App from the ruleset bypass list — either will silently break the sync workflow.**

**Bypass mechanism:** include `[skip-public-sync]` in the commit message to keep a particular commit private (in-progress branches, sensitive WIP).

**Local fallback:** `git pushe` (alias for `bash scripts/push-everywhere.sh`) does the same thing locally — useful when CI is broken or you want immediate feedback iterating on the sync filter itself. Set up by `bash scripts/install-git-hooks.sh` after a fresh clone.

Backend + brain run locally on Matt's PC; Vercel only handles the static/SSR frontend slice from glad-labs-stack.

**Consumer stack (`docker-compose.consumer.yml`, PR #1924, 2026-06-24):** Minimal variant for 8-16 GB VRAM / 32 GB RAM hardware. Omits the operator observability tier (Langfuse, GlitchTip, Loki/Promtail/Tempo/Pyroscope, pgAdmin) — idle RAM ~4-6 GB vs ~20+ GB for the full operator stack. image-gen behind `--profile image-gen`. `LANGFUSE_TRACING_ENABLED=false` + `:-` sentinels so missing keys never fail-fast; `ENABLE_TRACING=false` (no Tempo sink). Brain mounts `docker-compose.consumer.yml` as `/app/docker-compose.local.yml` so `compose_drift_probe` audits the right service set. Volume names mirror `docker-compose.local.yml` (same `gladlabs-*` named volumes) so upgrading consumer → operator is seamless. Target hardware: RTX 5060 Ti / 5070 class (8-16 GB VRAM), 32 GB RAM — see glad-labs-stack milestone #9 (Poindexter v1).

**Worker image includes ffmpeg (#1449)** — Stage-2 media rendering (podcast/video) bakes ffmpeg directly into `poindexter-prefect-worker`; no separate sidecar or host install needed. After any worker image change, `docker compose up -d --build poindexter-prefect-worker` to apply.

## Key Principles

- **Async-everywhere:** FastAPI uses async/await throughout; never block the event loop
- **Kernel / module / capability:** New code goes in as a business **module** on the kernel substrate, composing **capability** plugins — see the architecture-vocabulary note in Project Overview ("brainstem" and "spinal cord" are the only load-bearing anatomy labels left)
- **PostgreSQL as spinal cord:** All components communicate through shared DB tables, not imports
- **Atoms are independent graph nodes:** an atom (`modules/content/atoms/`) may import utilities/libraries downward (a `_`-prefixed `atoms/_helper` — the registry skips `_` modules so they're libraries not nodes, e.g. `atoms/_image_helpers.py` — or `services.*`) but must **NOT** import a **stage** (`modules.content.stages.*`, the retiring pre-atom layer) or a **sibling atom**. An architect LLM composes atoms into ad-hoc `graph_def`s, so the graph must be the whole truth of what runs; if atom A imports atom B, placing A on a graph silently runs B with B's contract invisible to the graph. Enforced by `scripts/ci/atom_independence_lint.py` (ratchet). Both fossils are now burned down (2026-07), so the baseline is empty: the **image** helper web moved into `atoms/_image_helpers.py` and the vestigial `ReplaceInlineImagesStage` stub was deleted; the **writer** core relocated out of the stage into the module-root `modules/content/writer_core.py` library, which `content.generate_draft` now imports instead of the `generate_content` stage. Precedent for the target shape: the atom-cutover deleted the `cross_model_qa` **stage** and replaced it with `qa.*` atoms delegating to the `multi_model_qa` **library**.
- **Service layer is the contract:** the HTTP API, the `poindexter` CLI, and the MCP servers are thin **adapters** that delegate to service functions — no adapter holds business logic or raw SQL (ADR [`docs/architecture/2026-06-10-transport-adapter-contract.md`](docs/architecture/2026-06-10-transport-adapter-contract.md), epic #1340). The one permanent exception is the bootstrap-direct allowlist (`setup` / `migrate` / `auth` provisioning, which run before the API/schema/first OAuth client exist). The `scripts/ci/adapter_purity_lint.py` ratchet enforces it (no net-new inline SQL in `routes/` / `poindexter/cli/` / `mcp-server/`).
- **Noisy static analysis is a ratchet, never an issue-filer:** `scripts/ci/bandit_lint.py` + `bandit_baseline.json` gate net-new bandit findings (2026-07-17). Bandit has no dataflow analysis, so it can't tell a real injection from the sanctioned `services/` pattern (hardcoded identifier + asyncpg bind params) — it filed **91 GitHub issues, every one examined a false positive** (#2594-#2623, closed by #2644), burying the 18 genuine issues three pages deep. The ratchet holds the same line, blocks the regression *before* it lands, and files nothing; the `codebase-audit` ops session is now ruff-only. Its baseline is keyed per-file-per-**rule** (unlike the other three, which each guard a single rule) so a new `B605` can't ride in behind a deleted `B404`. Escape hatch is bandit's own `# nosec <RULE> - <why>` — and the comment must sit inside the flagged node's line span, which for a multi-line triple-quoted f-string is the **closing** `"""` line. Findings are mostly false positives, so read the line and annotate; if one is genuinely unsafe, fix it rather than annotating it away. This is the shape to reach for whenever a scanner's signal-to-noise is bad: ratchet it, don't let it file.
- **Anti-hallucination:** Three layers — prompts, LLM QA, programmatic validator. See [`docs/architecture/anti-hallucination.md`](docs/architecture/anti-hallucination.md) for the full layer-by-layer breakdown (rule groups, reviewers, prompts, aggregation logic).
- **Config in DB, not code:** `app_settings` table replaces environment variables AND hardcoded constants. If you write a literal in production code, ask "could a customer tune this?" — if yes, it goes in app_settings.
- **Fail loud + notify:** Missing required config triggers `notify_operator()` (Telegram → Discord → alerts.log) then `sys.exit(2)`. No silent fallbacks.
- **Self-healing:** Brain daemon monitors and restarts services autonomously
- **Model router first:** Use cost tiers (`free`/`budget`/`standard`/`premium`) not hardcoded model names
- **Revenue-aware:** Content decisions informed by what generates traffic and money
- **No subagent delegation:** Subagents/Task-tool dispatch are disabled — Anthropic bills them at full metered API rates, separate from the Max subscription. Never propose or ask about delegating to a subagent (including when a skill like `dispatching-parallel-agents` or `subagent-driven-development` would normally suggest it) — do the work inline in the same conversation, sequentially if needed.
- **Matt's preferences:** Autonomous work (don't ask "what's next"), minimize env vars, manage from phone via Telegram/Grafana, no client/agency work — fully automated passive income. "Think 5 years down the road if this is a SaaS product" — EVERY tunable goes in app_settings, not code.

## Monitoring

- **Grafana (self-hosted):** http://localhost:3000 (or http://nightrider-1.taild4f626.ts.net:3000 from the tailnet) — 12 dashboards (Revenue parked 2026-07-01). Grafana Cloud was retired 2026-05-03; the local Docker container (poindexter-grafana) is the only Grafana now. Local Prometheus scrapes node_exporter (host, `:9100`) + the nvidia-smi gpu-exporter container directly (post-Linux-migration; windows_exporter retired with the Windows host); Alloy was the Cloud shipper and is no longer used. **The operator console (`/console/`) serves the deep-telemetry surfaces natively under a Telemetry tab** — Loki logs (worker proxy `GET /api/logs`), Langfuse LLM traces (`GET /api/traces`, waterfall deeplinks out to Langfuse), and native HISTORY / GPU & POWER / DATABASE chart sections (browser-direct Prometheus `query_range` against `:9091`, CORS-allowed) — so day-to-day operation no longer requires opening Grafana directly. The former Grafana `/d-solo` iframe embeds were removed 2026-07 ("Killing the Iframe"); `GF_SECURITY_ALLOW_EMBEDDING=true` remains in the compose `grafana` service but the console no longer depends on it.
- **Dashboards:**
  - **Mission Control** — top-level operator glance (trimmed 2026-06-03: deep media/director detail moved to Pipeline; keeps a single media-pending glance stat)
  - **Pipeline** — content pipeline throughput, plus the consolidated quality/QA rows ("Quality — scores & output" + "QA — rejections & validation", deduped 2026-06-03) and the Media Approval Queue (now holds the media/director detail relocated from Mission Control)
  - **Cost & Analytics** — LLM spend, energy, posts published
  - **Observability** — Tempo traces (RED-by-service), Pyroscope flame graphs, Loki logs (volume / error rate / live feed), and the API HTTP RED row (request rate / 4xx-5xx % / p95-p99 latency)
  - **System Health** — slim core-services board (service up/down, exporter signals, audit-log breakdown, GlitchTip triage) plus the **Scheduled-publish queue** panel set (depth / next slot / past-due / upcoming-24h table) and the **Approved-queue** panel set ("Approved q" stat + "Approved — awaiting publish" table over `pipeline_tasks.status='approved'`). DB internals and GPU/power were split out to the Database and Hardware & Power boards (2026-06-03).
  - **Experiments & Dry-Run** (`/d/experiments-dryrun`) — auto-publish gate dry-run observability (edit-distance, clean-run counts, gate-state) + Dry-Run Mode + Variant Experiments (Lab). Created 2026-06-03 (absorbed the former dev-blog-only Auto-Publish Gate board).
  - **Database** (`/d/database`) — Postgres internals: db size, connections + states, table row counts/sizes, index usage, active queries, cache-hit ratio, transactions, dead tuples. Extracted from System Health 2026-06-03; the natural home for postgres_exporter metrics (#650).
  - **Hardware & Power** (`/d/hardware-power`) — GPU live (Prometheus `nvidia_gpu_*`), wall power (Shelly plug `psu_*`) + PSU internals (HX1500i via the `corsair-psu` kernel hwmon → `node_hwmon_*`; chip label rotates per boot so queries match `1b1c:1c1f`, never a literal) + EIA-rate electricity cost — the Power row opens with a "How this board is metered" provenance panel mapping every series to its instrument and physical node (chain: wall → Shelly → UPS → HX AC-in → PSU → rails; see [docs/operations/wall-power-metering.md](docs/operations/wall-power-metering.md)), the **UPS row** (`network_ups_tools_*` from the profile-gated `nut-exporter` — input/output voltage, load% vs inverter rating, battery charge/runtime with the driver's shutdown floors, status-flag timeline; poindexter#958, see [docs/operations/ups-monitoring.md](docs/operations/ups-monitoring.md)), host-memory pressure, and GPU history tables. GPU is single-sourced to Prometheus (#653); the redundant `gpu_metrics` SQL live-gauges were dropped. Created 2026-06-03. **No CPU-package power panel** — `system_cpu_package_power_watts` / `system_cpu_core_power_watts` are produced by `scripts/nvidia-smi-exporter.py` from **Windows** Energy Meter performance counters, and Linux RAPL `energy_uj` is root-only since the PLATYPUS mitigation, so both panels were dead on Pop!_OS and were removed 2026-07-27. Re-add only alongside a Linux CPU-power producer.
  - **Integrations & Admin** — qa_gates / publishing_adapters / external_taps tables
  - **QA Rails — Multi-Model Review** (`/d/qa-rails`) — per-reviewer pass-rate, score distribution, latest QA passes. Powered by `audit_log` rows where `event_type='qa_pass_completed'` (one row per `MultiModelQA.review` call, full reviewer breakdown in JSON details). Created 2026-05-10 alongside the Lane D #329 close-out. The **"Advisory Rails — What the Gate Ignores"** row (stack#2125, 2026-08-10) surfaces the advisory blind spot: `final_score` counts only rails that can gate while `qa_all_rail_score` counts every rail, and **the gap between them is what the gate is choosing not to hear** — plus a "shipped over an advisory objection" table (advisory rails that scored under threshold on an *approved* pass) and a per-rail objection rate that is the `required_to_pass` graduation instrument. See [`docs/architecture/anti-hallucination.md`](docs/architecture/anti-hallucination.md).
  - **Findings — Probe Routing** (`/d/findings`) — emitted vs pending-delivery counts (pending = routable findings above `app_settings.findings_alert_route_watermark`), per-hour by severity, per-kind volume, and the live `kind → findings.<kind>.delivery` policy join. Powered by `audit_log` rows where `event_type='finding'`. Created 2026-06-02 (#461 Phase 4).
  - **Revenue** (`/d/revenue`) — **PARKED 2026-07-01** (`infrastructure/grafana/dashboards-parked/revenue.json`): `revenue_events` holds a single April row, so 12 no-data panels were eroding dashboard trust. Unpark via `git mv` when monetization writes real events — see `dashboards-parked/README.md`.
- **Alerts → Telegram + Discord:** stuck tasks, failure rate, worker offline, GPU temp, VRAM usage. Routing rules in `infrastructure/grafana/provisioning/alerting/`.
- **Playlist:** ~~"Glad Labs Command Center" cycles all dashboards every 30s.~~ **GONE since the Pop!_OS migration** — `GET /api/playlists` returns empty (verified 2026-08-07). Playlists live in Grafana's own DB, not in `infrastructure/grafana/dashboards/`, so the new host's fresh Grafana DB never had it and nothing in the repo recreates it. Grafana has no file-provisioning for playlists; recreating it means an API call (worth a small idempotent script if it's wanted back).
- **Pyroscope app-profiles (Glad-Labs/poindexter#406):** CPU flame graphs ship from the worker, brain, and voice agents under four `service_name` values — `poindexter-worker`, `poindexter-brain`, `poindexter-voice-livekit`, `poindexter-voice-webrtc`. Master switch is `app_settings.enable_pyroscope` — enabled on Matt's operator stack, but code-defaults `false` (glad-labs-stack#2133: it needs the optional `profiling` poetry extra, which ships no Windows wheels, so a fresh/consumer install must opt in rather than inherit warn-spam from a missing dep); per-service panel lives on the Observability dashboard.
- **GlitchTip (self-hosted Sentry):** http://localhost:8080 — runtime exceptions from worker / brain / voice. Org `glad-labs`, project `poindexter`. Sentry SDK auto-initialised in `main.py` when `app_settings.sentry_dsn` is set (provisioned 2026-05-09).
- **Langfuse:** http://localhost:3010 — every reviewer LLM call (DeepEval g-eval / faithfulness, Ragas, the legacy critic) traces here. Use it to drill into a specific qa_pass_completed event and read the judge model's reasoning.

## Scheduled agents (systemd timers)

**STATUS 2026-07-09: rewired off Claude Code OAuth.** The sessions no longer run through Claude Code on the Max subscription — that OAuth token decayed (expired access token + blank refresh token → `401`), and the 2026-06-15 billing split is closing the automated-on-subscription path anyway. Seven worthwhile sessions were re-homed onto **deterministic Python scripts + the local Ollama fleet** (`scripts/ops_sessions/`), which never expire and never bill; two frontier-model sessions (`issue-resolver`, `test-expansion`) are kept **disabled** pending a metered-API decision. Full runbook: [`docs/operations/scheduled-agents.md`](docs/operations/scheduled-agents.md); rationale: [`docs/superpowers/specs/2026-07-09-scheduled-agents-rewire-design.md`](docs/superpowers/specs/2026-07-09-scheduled-agents-rewire-design.md).

**Scheduling is systemd, not Windows Task Scheduler** (the Pop!_OS migration
ported this — `scripts/claude-sessions.ps1` is dead weight kept only for
reference). Each session is an instance of the
`infrastructure/systemd/poindexter-session@.service` template, fired by a
per-session timer that `scripts/linux/install-session-timers.sh` writes;
inspect the fleet with `systemctl list-timers 'poindexter-session@*'`. The unit
runs `scripts/linux/run-session.sh <name>`, which dispatches the matching
`ops_sessions/*.py` under the **main checkout's** poetry env — a fresh worktree
has no venv, so scripts reuse `sys.executable` for their own tooling. The four
committing sessions (`codebase-audit`, `doc-sync`, `claude-md-sync`,
`test-health`) run in an isolated worktree off fresh `origin/main` and open a PR
against `Glad-Labs/glad-labs-stack`; the rest merge PRs / file issues / edit
labels from the shared checkout. Logs land in
`~/.poindexter/logs/claude-sessions/<name>-<stamp>.log`. Issues are
content-routed (OSS → poindexter, business → glad-labs-stack). Local-LLM model
pins are the `OPS_OLLAMA_MODEL_*` env knobs.

| Session | Tier | When (local) | Does |
| --- | --- | --- | --- |
| `dependency-review` | deterministic | daily 06:30 | auto-merges green patch-bump dependabot PRs |
| `codebase-audit` | deterministic | Wed 02:00 | `ruff --fix` F401/F841 → lint PR (bandit moved to a CI ratchet) |
| `doc-sync` | deterministic | Fri 05:00 | verifies/repairs CLAUDE.md path references |
| `claude-md-sync` | deterministic | daily 02:30 | syncs DB-derived counts + surfaces migration drift |
| `triage-sweep` | deterministic | Mon 07:00 | applies keyword area-labels + Discord digest |
| `alert-triage` | local LLM | daily 01:00 | classifies noisy `alert_events` → probe-bug issues |
| `test-health` | local LLM | daily 03:00 | fixes simple test failures behind a re-run gate |
| `pro-freshness` | deterministic | Sun 04:30 | rebuilds `Glad-Labs/poindexter-pro` from the live system (tuned seed / SKILL.md prompt packs / premium boards) behind a PII-scrub gate; pushes + Discord note |
| `issue-resolver` | **disabled** | daily 05:00 | (frontier) fixes one scoped open issue |
| `test-expansion` | **disabled** | daily 04:00 | (frontier) adds tests to low-coverage files |

NOTE: repo-derivable CLAUDE.md stats still auto-sync daily via the `.github/workflows/sync-claude-md.yml` GitHub Action (06:17 UTC) independent of these sessions; the deterministic `claude-md-sync` refreshes the **DB-derived** counts (posts, embeddings, app_settings totals) via a prod-DB probe. **The DB half is currently broken** — the session computes correct counts and logs "opened CLAUDE.md sync PR", but no PR appears (the run also emits `rc=1 stderr=must be on a branch named differently than "main"`), so those counts froze from 2026-06-10 to the 2026-07-26 manual refresh.

## Database migrations

Migrations live in `src/cofounder_agent/services/migrations/`. The
migration history is squashed into `0000_baseline.py`
(plus `0000_baseline.schema.sql` + `0000_baseline.seeds.sql`) with
ordinary timestamped migrations accumulating on top of it,
re-rolled most recently by the **Phase G squash (2026-07-11)** — which
folded the Phase F baseline (2026-06-22) plus its 42 post-baseline
migrations (`20260622_200222_*` through `20260711_202250_*`) into a fresh
baseline. The file's docstring explains what each generation captured. The
baseline was regenerated fold-forward from a throwaway DB that ran the full
pre-squash chain (correct-by-construction, not a prod re-dump) and verified
byte-for-byte against a chain `pg_dump` — schema identical + all 11 seed
tables md5-identical. **Phase G is true baseline-only: no post-baseline
migration survives.** Phase F had to keep one convergence migration (a
baseline only `CREATE TABLE IF NOT EXISTS`, so it can add but never drop a
column prod still carried); by Phase G prod is verified current through
`20260711_202250_*`, so the `drop_pipeline_tasks_category` survivor folds
away and the tree holds just `0000_baseline.py`. Orphan `schema_migrations`
rows for the 42 deleted files are harmless — the runner skips by filename
and never reconciles in reverse. The runner still sorts lexically;
`0000_baseline.py` runs first because `0` < `2`. Post-squash migrations have
accumulated normally since (32 as of 2026-08-09) — "baseline-only" describes
the state at the squash, not a standing invariant.

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

**But three sources seed `app_settings`, and they must agree.** `DEFAULTS` is
NOT the only seed home: `0000_baseline.seeds.sql` seeds ~692 non-secret keys
and `brain/seed_app_settings.json` seeds 81. All three use `ON CONFLICT DO
NOTHING`, so **first writer wins**, and the order varies by install path:

- `docker compose up` on an empty DB → the brain daemon seeds first (`worker`
  declares `depends_on: brain-daemon: service_healthy`, and `seed_loader` does
  its own `CREATE TABLE IF NOT EXISTS`), so **brain > baseline > `DEFAULTS`**.
- `poindexter setup` → migrations + `seed_all_defaults` run before any
  container, so **baseline > `DEFAULTS`** and the brain seed no-ops.

Consequence: for the ~337 keys the baseline **also** seeds, `DEFAULTS` is
unreachable on a fresh install unless it matches the baseline. So a key the
baseline seeds must carry the **same value in both** `settings_defaults.py` and
`0000_baseline.seeds.sql`. `brain/seed_app_settings.json` is a deliberate
`_meta.tier == "free"` profile and MAY differ — but only where declared in the
`TIER_POLICY` allowlist in the lint below. Enforced by
`scripts/ci/settings_seed_value_drift_lint.py` (wired into `migrations-smoke`):
a value disagreement fails CI. A NEW key that no other source seeds is
`DEFAULTS`-only and unaffected by this.

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
- **Title variety + duplicate detection:** [`docs/architecture/title-variety.md`](docs/architecture/title-variety.md) — the two independent title paths (`content.generate_title` for canonical_blog, the `TITLE:` line in `atoms.narrate_bundle` for dev_diary — dev_diary had NO avoidance mechanism at all before stack#3209), why the recent-titles dump primed the formula it was meant to suppress, and the **two originality axes** (stack#3213): `qa_title_similarity_threshold` is DuckDuckGo-only (did we restate someone else's headline?) while `title_internal_similarity_threshold` compares against our own published+queued titles (did we already write this?). Separate switches on purpose — `is_original` is false when either trips, which routes a collision into the existing regeneration loop. `originality_rank` ranks candidates on `(axes_colliding, worst_similarity)`, NOT on `max_similarity`, which is external-only and silently discarded internal fixes. **Which title SHIPS is a third axis (stack#3217):** `publish_post_from_task` preferred the draft's leading heading over the canonical title AND read it from `merged['title']` (stage_data, absent for 90% of tasks), so 71.3% of canonical_blog posts published under the writer's H1 and the two mechanisms above governed a string no reader saw. Precedence is now canonical → heading → topic with topic-echoes skipped; `publish_title_source='body_heading'` reverts it.
- **Featured-image reliability:** [`docs/architecture/featured-image-reliability.md`](docs/architecture/featured-image-reliability.md) — the hero path (subject → LRU style → prompt → render → R2) and the two silent failures fixed in poindexter#3229. (1) `_render_image_gen` raised on read-timeout / GPU-lock-timeout, so those flew **past** `_try_image_gen_featured`'s retry loop into its outer `except` — one attempt instead of the configured two, and 7 published posts shipped with no hero. Transient failures are now RETURNED as `(None, {"transient": True, …})`; the table there says which failures are windows and which are verdicts. (2) The prompt model answers by restating the brief and `image_prompt_max_tokens` truncates it, so a `Subject:/Constraints:/Output ONLY` scratchpad went to SDXL verbatim on 26 of 39 recent posts — `services/image_prompt_sanitizer.py::clean_image_prompt` strips it (shared with the inline atom path, which is why it sits in `services/`: an atom may not import a stage). **`str()` on `httpx.ReadTimeout` is the EMPTY STRING** — the old log read `render failed ()` and named no cause for weeks; use `describe_exception()`, never a bare `%s` on an exception here.
- **Findings routing:** [`docs/architecture/findings-routing.md`](docs/architecture/findings-routing.md) — `emit_finding` → `audit_log` → `FindingsAlertRouterJob` → `alert_events` → dispatcher. Covers the per-kind policy quad (`delivery` / `fallback` / `min_severity` / `cooldown_minutes`) and **the two throttles that are easy to confuse**: dispatcher dedup is keyed by *fingerprint* (collapses the same finding repeating), per-kind cooldown is keyed by *kind* (throttles kinds like `stale_task_reclaimed` whose subject changes every fire, so every fire is a new fingerprint dedup structurally cannot collapse). `critical` is never cooled; `findings.default.*` is deliberately inert.
- **LangGraph pipeline (Lane C + atom-cutover #355):** `app_settings.default_template_slug='canonical_blog'`. As of #355 (2026-06-02) `canonical_blog` runs as a static `graph_def` row in the `pipeline_templates` table — authored in `services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF` (44 nodes — see the Content-pipeline-stages section), compiled by `services/pipeline_architect.py::build_graph_from_spec`, preferred by `TemplateRunner.run` when `pipeline_use_graph_def=true` (the prod default). The hand-coded `canonical_blog` factory + `_CANONICAL_BLOG_ORDER` were deleted from `services/pipeline_templates/__init__.py` (only `dev_diary` remains in `TEMPLATES`), and the `cross_model_qa` stage was deleted in favour of the `qa.*` rail atoms → `qa.aggregate`. The earlier legacy chunked StageRunner path (`content_router_service.py` + `plugins/stage_runner.py`) was already deleted 2026-05-16. New `canonical_blog` nodes go on the graph_def spec.
- **Prefect dispatch (#410, post-Stage-4):** `services/task_executor.py` was deleted entirely 2026-05-16 (~1500 LOC). Prefect's deployment owns dispatch entirely; retry / heartbeat are native Prefect features. Operator UI at port 4200. The `_notify_discord` / `_notify_alert` helpers moved to `services/integrations/operator_notify.py`; `_auto_publish_task` / `_get_auto_publish_threshold` moved to `modules/content/auto_publish.py`. **Stale in-progress reclaim (2026-06-09):** `reclaim_stale_inprogress_tasks` Prefect task fires at the top of every `content_generation_flow` run — it calls `TasksDatabase.sweep_stale_tasks(timeout_minutes=content_flow_stale_inprogress_minutes)` (default 30 min) to reset orphaned rows to `pending` (or fail them after max retries) and clear poisoned LangGraph checkpoints. Without this, a task killed mid-graph stays `in_progress` forever because the flow only claims `pending` rows. See the checkpoint-poisoning note in repo operational notes.
- **Module v1 (Glad-Labs/poindexter#490):** [`docs/architecture/module-v1.md`](docs/architecture/module-v1.md). Phases 1–4 all shipped May 2026: Module Protocol + `get_modules()` registry + manifest validation; per-module migration runner + `module_schema_migrations`; the in-tree `ContentModule`; and route auto-discovery in `utils/route_registration.register_all_routes` (iterates `get_modules()` after substrate routes mount). Lifecycle hooks `register_cli` / `register_dashboards` / `register_probes` fire at lifespan startup as safe no-ops in the worker process (those targets get `None`; misnamed-method failures fail loud per `feedback_no_silent_defaults`). FinanceModule operator routes live at `/api/finance/*`, OAuth-JWT protected via `verify_api_token`, fail-loud 503 with a remediation command when Mercury config is missing. **Phase 3's physical code move is done** (2026-06-04 — see the content-module note in the Backend section); still deferred: dashboard auto-discovery, CLI subparser threading, brain-probe iteration, `visibility` sync-filter rewrite. **Decomposition philosophy** (memory: `project_module_decomposition_axes`): capability plugins and business modules are orthogonal axes — business modules COMPOSE capability plugins.
- **Memory dir** (all three below live in `~/.claude/projects/-home-mattm-glad-labs-website/memory/`):
  - **Auto-memory index (recent context / feedback / project state):** `MEMORY.md`
  - **Architecture vision:** `project_brain_architecture.md`
  - **Monetization / revenue model:** `project_monetization.md`
