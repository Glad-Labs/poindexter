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

| Service       | URL                                             |
| ------------- | ----------------------------------------------- |
| Public site   | https://gladlabs.io (→ www.gladlabs.io)         |
| Backend API   | http://localhost:8000                           |
| Brain daemon  | Local process (brain/)                          |
| Grafana       | https://gladlabs.grafana.net                    |
| GitHub        | https://github.com/Glad-Labs/glad-labs-codebase |
| Project board | https://github.com/orgs/Glad-Labs/projects/2    |

### Key Numbers (as of April 27, 2026)

- 46 live posts on gladlabs.io (212 posts total: 143 drafts, 23 archived; 1,072 pipeline_tasks across all generation runs)
- 313 Python files under `src/cofounder_agent/services/` (16 highlighted in the table below are the load-bearing ones)
- 6 Grafana dashboards (post-merge consolidation), 4 alert rules
- 6,800+ Python unit tests passing
- 310 app_settings keys (roughly 60 added in the #198 hardening sweep, plus ongoing per-feature additions)
- 15,300+ embeddings across posts / issues / audit / memory / brain (8× growth since the prior count was logged)
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

**Key services (16 load-bearing):**

| Service                                   | Purpose                                                    |
| ----------------------------------------- | ---------------------------------------------------------- |
| `content_router_service.py`               | 6-stage content pipeline with cross-model QA               |
| `content_validator.py`                    | Anti-hallucination rules (programmatic, no LLM)            |
| `multi_model_qa.py`                       | Adversarial review (different LLMs check each other)       |
| `qa_gates_db.py`                          | Declarative QA gate definitions (DB-driven)                |
| `workflow_executor.py`                    | Step-based workflow orchestration                          |
| `unified_orchestrator.py`                 | Cross-pipeline coordinator                                 |
| `settings_service.py`                     | DB-backed config (app_settings, 310 active keys)           |
| `site_config.py`                          | DI seam over settings (Phase H singleton replacement)      |
| `cost_guard.py`                           | Daily/monthly spend limits                                 |
| `model_router.py`                         | Cost-tiered model selection (free/budget/standard/premium) |
| `research_service.py` / `web_research.py` | Topic research + web fact-check                            |
| `publish_service.py`                      | Final publish + scheduled_publisher integration            |
| `quality_service.py`                      | Quality scoring orchestration                              |
| `internal_link_coherence.py`              | Auto-adds related post links                               |
| `social_poster.py`                        | Generates X/LinkedIn posts via Ollama                      |
| `newsletter_service.py`                   | Weekly digest generator                                    |

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
- `pipeline_events` — event bus (PostgreSQL LISTEN/NOTIFY)
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

Custom MCP server for Claude desktop app. 12 tools: create_post, approve, publish, check_health, get_budget, compose_plan, compose_execute, get/set/list_settings, get_post_count.

### Configuration (#198 — no hardcoded values in code)

**Bootstrap is the only config on disk.** Written by `poindexter setup`
to `~/.poindexter/bootstrap.toml`. Contains ONE value: `database_url`
(plus optional operator-notification channels for when the system
can't start cleanly). **No `.env` required.**

```toml
# ~/.poindexter/bootstrap.toml
database_url = "postgresql://..."
telegram_bot_token = ""
telegram_chat_id = ""
discord_ops_webhook_url = ""
```

Resolution priority in `brain.bootstrap.resolve_database_url()`:
explicit CLI arg → bootstrap.toml → DATABASE_URL → LOCAL_DATABASE_URL
→ POINDEXTER_MEMORY_DSN. If nothing resolves, `require_database_url()`
fires `notify_operator()` (Telegram → Discord → alerts.log → stderr)
then `sys.exit(2)`.

**Everything else lives in `app_settings` (200+ keys).** Code accesses
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

Do NOT write `from services.site_config import site_config` — that
module-level singleton was removed in Phase H step 5. Tests should
construct their own instance with `SiteConfig(initial_config={...})`
or use the `test_site_config` fixture in `tests/unit/conftest.py`.

For SaaS / A/B-testing readiness, every tunable should be a
DB-backed setting. Background algorithm windows (anomaly detection,
dedup lookback, failure rate windows) are NOT exceptions — they're
also settings with sensible defaults.

**Storage is provider-agnostic.** `storage_*` keys in app*settings
target any S3-compatible provider (R2, S3, B2, MinIO). The old
`cloudflare_r2*\*` keys still work as a fallback but are deprecated.

### Deployment

- `main` branch → Vercel (frontend) auto-deploy; backend + brain run locally
- `staging` branch → Vercel preview deploy (PR required)
- `dev` branch → working branch, runs tests on push
- **Workflow:** dev → PR to staging → verify → PR to main → production

## Key Principles

- **Async-everywhere:** FastAPI uses async/await throughout; never block the event loop
- **Brain architecture:** System modeled after human brain anatomy — each region independent
- **PostgreSQL as spinal cord:** All components communicate through shared DB tables, not imports
- **Anti-hallucination:** Three layers — prompts, LLM QA, programmatic validator
- **Config in DB, not code:** `app_settings` table replaces environment variables AND hardcoded constants. If you write a literal in production code, ask "could a customer tune this?" — if yes, it goes in app_settings.
- **Fail loud + notify:** Missing required config triggers `notify_operator()` (Telegram → Discord → alerts.log) then `sys.exit(2)`. No silent fallbacks.
- **Self-healing:** Brain daemon monitors and restarts services autonomously
- **Model router first:** Use cost tiers (`free`/`budget`/`standard`/`premium`) not hardcoded model names
- **Revenue-aware:** Content decisions informed by what generates traffic and money
- **Matt's preferences:** Autonomous work (don't ask "what's next"), minimize env vars, manage from phone via Telegram/Grafana, no client/agency work — fully automated passive income. "Think 5 years down the road if this is a SaaS product" — EVERY tunable goes in app_settings, not code.

## Monitoring

- **Grafana Cloud:** gladlabs.grafana.net — 6 dashboards (merged set)
- **Dashboards:** Ops (home), Performance, Hardware, Pipeline, Cost, Quality, plus built-in
- **Alerts → Telegram + Discord:** stuck tasks, failure rate, worker offline, GPU temp, VRAM usage
- **Playlist:** "Glad Labs Command Center" cycles all dashboards every 30s

## Cron Jobs (re-create on new sessions)

```
Self-healing agent: hourly at :13 — health check + auto-fix
Code quality agent: every 4h at :37 — security/dead code/error handling scans
```

## Reference Documentation

- **Operations docs:** `docs/operations/` (troubleshooting, local-development-setup, disaster-recovery, ci-deploy-chain, etc.)
- **Latest session handoff:** `~/.claude/projects/C--Users-mattm/memory/session_62_handoff.md`
- **Architecture vision:** `~/.claude/projects/C--Users-mattm/memory/project_brain_architecture.md`
- **Revenue model:** `~/.claude/projects/C--Users-mattm/memory/project_revenue_model.md`
