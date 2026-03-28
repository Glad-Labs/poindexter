# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Glad Labs is an AI orchestration system (v0.1.0) — a monorepo with two integrated services:

- **Backend:** Python FastAPI orchestrator with ~76 service modules (port 8000)
- **Public Site:** Next.js 15 content distribution website (port 3000)

Operations management is handled via **OpenClaw** (external ops platform) and **Grafana** for monitoring.

**Latest Milestone:** Phase 1C (Issue #6) Complete — All 312 exception handlers standardized with consistent error handling patterns. Error handling uniformity achieved across all service files (March 5, 2026).

### Phase 1C: Error Handling Standardization ✅ COMPLETE

**Pattern Established:** All exception handlers now use `logger.error(f"[operation_name] message", exc_info=True)` with appropriate fallback strategies.

- **Coverage:** 312/312 exceptions (100%) across 68 service files
- **Completion Time:** ~24 hours across 15 batches
- **Verification:** Zero unstandardized handlers remaining (automated script verified)
- **Code Quality:** All files compile successfully, no regressions

**Impact:** Consistent error handling, stack traces captured for all exceptions, improved debugging capabilities.

## Development Commands

### Starting Services

```bash
npm run dev                  # Start both services concurrently (primary command)
npm run dev:cofounder        # Backend only (FastAPI + uvicorn)
npm run dev:public           # Next.js only
```

### Setup & Installation

```bash
npm run clean:install        # Full reset: remove node_modules, reinstall everything
npm run install:all          # Install all Node + Python deps
npm run setup                # Full setup with environment config
```

### Testing

```bash
# Python backend
npm run test:python           # Integration + e2e (full suite)
npm run test:python:unit      # Unit tests only
npm run test:python:smoke     # Fast smoke tests
npm run test:python:coverage  # With coverage report

# JavaScript (public site)
npm run test                  # Jest for public site
npm run test:ci               # CI mode (coverage, no watch)

# Single test file (Python):
cd src/cofounder_agent && poetry run pytest tests/unit/routes/test_task_routes.py -v

# Browser E2E
npm run test:e2e              # All Playwright tests (headless)
npm run test:e2e:headed       # With visible browser
npm run test:e2e:debug        # Debug mode
npm run test:public           # Public site tests only
```

### Code Quality

```bash
npm run lint                  # ESLint all workspaces
npm run lint:fix              # Fix ESLint issues
npm run lint:python           # Python pylint
npm run format                # Prettier (JS/TS/JSON/MD)
npm run format:check          # Check without writing
npm run format:python         # Black + isort
npm run type:check            # Python mypy
```

### Build

```bash
npm run build                 # Build all workspaces
# Public Site output: web/public-site/.next/
```

## Architecture

### Backend (`src/cofounder_agent/`)

**Entry point:** `main.py` — FastAPI app initializing service container, database pools, orchestrator, and registering all 31 route modules via `register_all_routes()`.

**Key services:**

- `services/model_router.py` — LLM provider selection with automatic fallback: Ollama → Anthropic → OpenAI → Google → echo/mock. **Never hardcode model names; use cost tiers.**
- `services/database_service.py` — Coordinates 5 specialized DB modules (Users, Tasks, Content, Admin, WritingStyle). PostgreSQL-only; no SQLite fallback.
- `services/unified_orchestrator.py` — Master agent choreography and task distribution
- `services/workflow_executor.py` — Phase-based workflow execution with real-time WebSocket progress events
- `services/capability_registry.py` — Intent-based task routing; auto-selects agents from natural language requests

**Agent system:** Four core agent types in `src/cofounder_agent/agents/` (Content, Financial, Market Insight, Compliance). The content agent runs a 6-stage self-critiquing pipeline: Research → Creative Draft → QA Critique → Creative Refinement → Image Selection → Publishing Prep (with DB Storage). QA agents critique without rewriting; Creative agents apply the feedback.

**Database:** asyncpg for direct PostgreSQL interaction + Python migration modules (containing raw SQL) in `services/migrations/`. Five domain modules delegate from `DatabaseService`.

**Python toolchain:** Poetry for dependency management. Run with `poetry run` inside `src/cofounder_agent/`. pytest markers: `unit`, `integration`, `api`, `e2e`, `performance`, `slow`, `voice`, `websocket`.

### Frontend

**Public Site** (`web/public-site/`): Next.js 15 app router (no `pages/` directory). Markdown content via gray-matter. Static generation with ISR. Jest + React Testing Library for tests.

**API integration:** The public site calls `http://localhost:8000/*`. No direct database access from frontend.

**Authentication:** API endpoints are protected by `API_TOKEN` header validation. Set `API_TOKEN` in `.env.local` for the backend and `NEXT_PUBLIC_API_TOKEN` for the public site.

### Configuration

Each service reads from its own `.env.local` file:

- **Backend:** Reads `.env.local` from project root (configured in `src/cofounder_agent/config/__init__.py`)
- **Public Site (Next.js):** Reads `.env.local` from `web/public-site/`

**Minimum required:**

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/glad_labs_dev
# Plus at least ONE of: ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, or OLLAMA_BASE_URL
```

**Key feature flags:** `ENABLE_TRACING`, `ENABLE_QUERY_MONITORING` (in `.env.example`); `ENABLE_TRAINING_CAPTURE`, `SENTRY_ENABLED`, `REDIS_ENABLED` (code-only, not in `.env.example`).

### Monorepo Structure

npm workspaces cover `web/public-site`. `npm install` at root installs everything. Python deps are managed separately via Poetry in `src/cofounder_agent/`.

### Deployment

- `main` branch → Vercel (frontend) + Railway (backend) production auto-deploy + GitHub Release tag
- `staging` branch → Railway staging auto-deploy; Release Please manages changelog + version bumps here
- `dev` branch → runs tests only (no deployment)
- Feature branches (`feature/*`, `bugfix/*`) → runs tests on PR, no deployment

## Key Principles

- **Async-everywhere:** FastAPI uses async/await throughout; never block the event loop
- **API-first:** All state access goes through REST endpoints, not direct DB calls from frontend
- **PostgreSQL as source of truth:** All task results, agent memories, and content stored there
- **Model router first:** Use cost tiers (`free`/`budget`/`standard`/`premium`/`flagship`) not hardcoded model names
- **Monorepo with workspaces:** `npm install` once at root covers everything
- **API versioning policy:** All ~193 endpoints live at `/api/{resource}` (no `/v1/` prefix). This is the current v1 surface, version read from `pyproject.toml` at startup, OpenAPI at `/api/openapi.json`. **Policy:** Breaking changes to any public endpoint (field renames, status code changes, required field additions) MUST introduce a new URL version prefix (`/api/v2/`). Non-breaking additions (new optional fields, new endpoints) do not require a new version. Document breaking changes in `CHANGELOG.md`.

## Reference Documentation

- Documentation index: `docs/README.md`
- Architecture: `docs/architecture/system-design.md`
- Deployment/CI: `docs/operations/deployment.md`, `docs/development/workflow.md`
- AI agents: `docs/architecture/multi-agent-pipeline.md`
- Troubleshooting: `docs/operations/troubleshooting.md`
- Environment variables: `docs/operations/env-vars.md`, `.env.example`
