# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## IMPORTANT: New session startup

If this is a new session, read these in order:

1. `~/.claude/projects/C--users-mattm-glad-labs-website/memory/matts_voice.md` — How Matt thinks and communicates
2. `~/.claude/projects/C--users-mattm-glad-labs-website/memory/decision_log.md` — Key decisions and WHY
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
| Backend API   | http://localhost:8002                           |
| Brain daemon  | Local process (brain/)                          |
| Grafana       | https://gladlabs.grafana.net                    |
| GitHub        | https://github.com/Glad-Labs/glad-labs-codebase |
| Project board | https://github.com/orgs/Glad-Labs/projects/2    |

### Key Numbers (as of April 16, 2026)

- 34+ published posts on gladlabs.io
- 16 custom services built
- 7 Grafana dashboards, 90+ panels, 5 alert rules
- 5,097+ Python unit tests passing
- 200+ app_settings keys (roughly 60 added in the #198 hardening sweep)
- 1,900+ embeddings across posts / issues / audit / memory / brain
- $30/month operating cost

## Development Commands

### Starting Services

```bash
npm run dev                  # Start both services concurrently (primary command)
npm run dev:cofounder        # Backend only (FastAPI + uvicorn)
npm run dev:public           # Next.js only
```

### Worker (local GPU content generation)

```powershell
.\scripts\start-worker.ps1   # Start worker connected to production DB
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

**Key services (16 custom-built):**

| Service                     | Purpose                                              |
| --------------------------- | ---------------------------------------------------- |
| `content_router_service.py` | 6-stage content pipeline with cross-model QA         |
| `content_validator.py`      | Anti-hallucination rules (programmatic, no LLM)      |
| `multi_model_qa.py`         | Adversarial review (different LLMs check each other) |
| `qa_registry.py`            | Composable QA workflows (dynamic from DB)            |
| `process_composer.py`       | Intent → plan → approve → execute orchestration      |
| `settings_service.py`       | DB-backed config (app_settings, 33+ keys)            |
| `cost_guard.py`             | Daily/monthly spend limits                           |
| `revenue_engine.py`         | Content performance analysis + topic suggestions     |
| `anticipation_engine.py`    | Observe gaps → propose proactive actions             |
| `big_brain.py`              | Self-maintaining knowledge graph                     |
| `internal_linker.py`        | Auto-adds related post links                         |
| `affiliate_linker.py`       | Auto-injects affiliate links (DB-backed)             |
| `social_poster.py`          | Generates X/LinkedIn posts via Ollama                |
| `newsletter_service.py`     | Weekly digest generator                              |
| `finance_service.py`        | Mercury banking integration + P&L reports            |
| `business_report.py`        | Daily/weekly metrics summaries                       |

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
settings through `services.site_config`:

- `site_config.get(key, default)` — sync, reads from in-memory cache
  populated at startup
- `site_config.get_secret(key, default)` — **async**, hits DB each call
  (secrets are filtered out of the cache, so `is_secret=true` keys
  MUST be fetched via this method)

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

- **Grafana Cloud:** gladlabs.grafana.net — 7 dashboards, 90+ panels
- **Dashboards:** Ops (home), Performance, Hardware, Pipeline, Cost, Quality, plus built-in
- **Alerts → Telegram + Discord:** stuck tasks, failure rate, worker offline, GPU temp, VRAM usage
- **Playlist:** "Glad Labs Command Center" cycles all dashboards every 30s

## Cron Jobs (re-create on new sessions)

```
Self-healing agent: hourly at :13 — health check + auto-fix
Code quality agent: every 4h at :37 — security/dead code/error handling scans
```

## Reference Documentation

- **Operations runbook:** `docs/operations/runbook.md` (current as of March 30)
- **Session handoff:** `~/.claude/projects/.../memory/session_45_handoff.md`
- **Architecture vision:** `~/.claude/projects/.../memory/project_brain_architecture.md`
- **Revenue model:** `~/.claude/projects/.../memory/project_revenue_model.md`
- **Marketing drafts:** `docs/marketing/hacker-news-post.md`, `docs/marketing/twitter-thread.md`
