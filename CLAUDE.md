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

| Service         | URL                                                                             |
| --------------- | ------------------------------------------------------------------------------- |
| Public site     | https://gladlabs.io (→ www.gladlabs.io)                                         |
| Backend API     | http://localhost:8002                                                           |
| Brain daemon    | Local process (brain/)                                                          |
| Grafana         | http://localhost:3000 (or http://100.81.93.12:3000 via Tailscale)               |
| Pyroscope       | http://localhost:4040                                                           |
| Voice (LiveKit) | https://nightrider.taild4f626.ts.net/voice/join (tap-to-join, Tailscale Funnel) |
| Public docs     | https://gladlabs.mintlify.app                                                   |
| Private repo    | https://github.com/Glad-Labs/glad-labs-stack                                    |
| Public repo     | https://github.com/Glad-Labs/poindexter (auto-mirror)                           |
| Project board   | https://github.com/orgs/Glad-Labs/projects/2                                    |

### Key Numbers (as of May 6, 2026)

- 52 live posts on gladlabs.io (218 posts total: 143 drafts, 23 archived; 1,515 pipeline_tasks across all generation runs)
- ~455 Python files under `src/cofounder_agent/services/` (18 highlighted in the table below are the load-bearing ones — added template_runner, prompt_manager, and the LiteLLM provider plugin during the 2026-05-04 OSS-migration push). **2026-05-08 services audit** at `.shared-context/audits/2026-05-08-services-folder-audit.md` flagged ~5,000 LOC as deletable + 3 supposedly-completed migrations as still in-flight; Phase 1 of the cleanup is in progress.
- 161 migration files in `services/migrations/` (UTC-timestamp prefix post-#378; old `0xxx_*.py` stay as-is)
- 7 Grafana dashboards (post-merge consolidation), 4 alert rules; Pyroscope app-profiles ship from worker/brain/voice agents under `service_name` tags (poindexter#406)
- 7,900+ Python unit tests passing (329 test files)
- 674 app_settings keys (up from 453 on May 3 — the in-code DI layer accepts hundreds more; see PR sweeps #198, #221, and the 2026-05-06 audit)
- 25,000+ embeddings across posts / issues / audit / memory / brain / claude_sessions
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

**Key services (18 load-bearing):**

| Service                                   | Purpose                                                                                                                                                                                                                                                                                                                                      |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `content_router_service.py`               | 6-stage content pipeline with cross-model QA                                                                                                                                                                                                                                                                                                 |
| `content_validator.py`                    | Anti-hallucination rules (programmatic, no LLM)                                                                                                                                                                                                                                                                                              |
| `multi_model_qa.py`                       | Adversarial review (different LLMs check each other)                                                                                                                                                                                                                                                                                         |
| `qa_gates_db.py`                          | Declarative QA gate definitions (DB-driven)                                                                                                                                                                                                                                                                                                  |
| `task_executor.py`                        | Currently 99% of blog production traffic flows through this. Reads `pipeline_tasks` rows where `template_slug IS NULL` and executes the legacy stage chain. Being phased out alongside `workflow_executor.py` as the LangGraph migration (#356) absorbs templates one at a time. Don't add new logic here.                                   |
| `workflow_executor.py`                    | Legacy phase-based orchestration. **0% production traffic** as of 2026-05-08 (no route handlers reference it) but still imported by `content_agent` + `custom_workflows_service` + `template_execution_service`. Deletable as part of #356 once those import chains unwind.                                                                  |
| `template_runner.py`                      | LangGraph-backed dynamic-pipeline orchestrator (TemplateRunner). **1% production traffic** (only `dev_diary` template). Drives that template + future architect-composed pipelines. Postgres checkpointer enabled via `template_runner_use_postgres_checkpointer=true`.                                                                      |
| `prompt_manager.py`                       | UnifiedPromptManager — Langfuse-first, then DB overrides, then YAML defaults (poindexter#47). Edits land in the Langfuse UI. **Note:** ~12 prompt constants in `multi_model_qa.py`, `stages/cross_model_qa.py`, `writer_rag_modes/deterministic_compositor.py`, etc. still inline; migration to Langfuse is pending.                         |
| `settings_service.py`                     | DB-backed config (app_settings, 700+ active keys)                                                                                                                                                                                                                                                                                            |
| `site_config.py`                          | DI seam over settings — `class SiteConfig` constructed by `main.py` and DI'd via `Depends(get_site_config_dependency)`. **CAUTION:** the module-level `site_config = SiteConfig()` singleton at line 226 is NOT deleted yet — 187 callers still import it; Phase 1c sweep (filed) will retire it. Use the DI seam in NEW code.               |
| `cost_guard.py`                           | Daily/monthly spend limits. **Note:** still has 14 hardcoded model prices at lines 74-94 — should consult `cost_lookup.py` instead. Pending follow-up.                                                                                                                                                                                       |
| `cost_lookup.py`                          | LiteLLM-backed cost lookup (wraps `litellm.model_cost`). **Note:** the `model_router.py` / `usage_tracker.py` / `model_constants.py` trio it was meant to replace is **still load-bearing** in `multi_model_qa.py:28`, `quality_service.py:104`, `firefighter_service.py:9-20`. Migration completion tracked under poindexter#199 follow-up. |
| `llm_providers/litellm_provider.py`       | LiteLLM-backed `LLMProvider` plugin (provider routing + cost tracking + retries via mature OSS). Distinct from #199; activated by setting `plugin.llm_provider.primary.standard='litellm'`. Production cutover gate.                                                                                                                         |
| `research_service.py` / `web_research.py` | Topic research + web fact-check                                                                                                                                                                                                                                                                                                              |
| `publish_service.py`                      | Final publish + scheduled_publisher integration                                                                                                                                                                                                                                                                                              |
| `quality_service.py`                      | Quality scoring orchestration                                                                                                                                                                                                                                                                                                                |
| `internal_link_coherence.py`              | Auto-adds related post links                                                                                                                                                                                                                                                                                                                 |
| `social_poster.py`                        | Generates X/LinkedIn posts via Ollama                                                                                                                                                                                                                                                                                                        |
| `newsletter_service.py`                   | Weekly digest generator                                                                                                                                                                                                                                                                                                                      |

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

Do NOT write `from services.site_config import site_config` in NEW
code. The module-level `site_config = SiteConfig()` singleton at
`services/site_config.py:226` was supposed to be removed in Phase H
step 5 but the deletion never landed — 187 callers still import it
today (most read from a _blank_ default-loaded instance and silently
fall back to `os.getenv`, which is the bug `feedback_module_singleton_gotcha`
warns about). The audit at
`.shared-context/audits/2026-05-08-services-folder-audit.md` documents
the discrepancy; Phase 1c of the cleanup sweeps the import sites onto
the DI seam and then deletes the singleton + adds a CI lint that fails
on the import.

Tests should construct their own instance with
`SiteConfig(initial_config={...})` or use the `test_site_config`
fixture in `tests/unit/conftest.py`.

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

- **Grafana (self-hosted):** http://localhost:3000 (or http://100.81.93.12:3000 from the tailnet) — 7 dashboards (merged set). Grafana Cloud was retired 2026-05-03; the local Docker container (poindexter-grafana) is the only Grafana now. Local Prometheus scrapes windows_exporter + nvidia-smi-exporter directly; Alloy was the Cloud shipper and is no longer used.
- **Dashboards:** Ops (home), Performance, Hardware, Pipeline, Cost, Quality, plus built-in
- **Alerts → Telegram + Discord:** stuck tasks, failure rate, worker offline, GPU temp, VRAM usage
- **Playlist:** "Glad Labs Command Center" cycles all dashboards every 30s
- **Pyroscope app-profiles (Glad-Labs/poindexter#406):** CPU flame graphs ship from the worker, brain, and voice agents under four `service_name` values — `poindexter-worker`, `poindexter-brain`, `poindexter-voice-livekit`, `poindexter-voice-webrtc`. Master switch is `app_settings.enable_pyroscope` (default true post-#406); per-service panel lives on the Observability dashboard.

## Cron Jobs (re-create on new sessions)

```
Self-healing agent: hourly at :13 — health check + auto-fix
Code quality agent: every 4h at :37 — security/dead code/error handling scans
```

## Database migrations

Migrations live in `src/cofounder_agent/services/migrations/`. As of
Glad-Labs/poindexter#378 (2026-05-05) **new migrations use a UTC
timestamp prefix** (`YYYYMMDD_HHMMSS_<slug>.py`) — old `0xxx_*.py`
files stay as-is. The runner sorts lexically; `0xxx` (starts with
`0`) always sorts before `2xxx_xxxxxx_*` (starts with `2`).

Generate one with:

```bash
python scripts/new-migration.py "describe what the migration does"
```

Read [`docs/operations/migrations.md`](docs/operations/migrations.md)
for the full convention. Verify against a fresh DB with
[`docs/operations/fresh-db-setup.md`](docs/operations/fresh-db-setup.md)
or the CI smoke test (`python scripts/ci/migrations_smoke.py`). Lint
with `python scripts/ci/migrations_lint.py` — it catches collisions,
missing runner interface, and post-cutoff legacy prefixes.

## Reference Documentation

- **Operations docs:** `docs/operations/` (troubleshooting, local-development-setup, disaster-recovery, ci-deploy-chain, etc.)
- **Latest session handoff:** `~/.claude/projects/C--Users-mattm/memory/session_62_handoff.md`
- **Architecture vision:** `~/.claude/projects/C--Users-mattm/memory/project_brain_architecture.md`
- **Revenue model:** `~/.claude/projects/C--Users-mattm/memory/project_revenue_model.md`
