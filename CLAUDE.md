# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Glad Labs is an AI orchestration system (v3.0.43) — a monorepo with three integrated services:

- **Backend:** Python FastAPI orchestrator with 118 service modules (port 8000)
- **Admin UI:** React 18 + Material-UI dashboard for agent monitoring (port 3001)
- **Public Site:** Next.js 15 content distribution website (port 3000)

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
npm run dev                  # Start all three services concurrently (primary command)
npm run dev:cofounder        # Backend only (FastAPI + uvicorn)
npm run dev:public           # Next.js only
npm run dev:oversight        # React admin only
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

# JavaScript/React
npm run test                  # Jest for all workspaces
npm run test:ci               # CI mode (coverage, no watch)

# Single test file (Python):
cd src/cofounder_agent && poetry run pytest tests/unit/routes/test_task_routes.py -v

# Single test file (React — from workspace root):
cd web/oversight-hub && npm test -- src/components/__tests__/MyComponent.test.jsx

# Browser E2E
npm run test:e2e              # All Playwright tests (headless)
npm run test:e2e:headed       # With visible browser
npm run test:e2e:debug        # Debug mode
npm run test:public           # Public site tests only
npm run test:admin            # Admin UI tests only
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
# Oversight Hub output: web/oversight-hub/build/
# Public Site output: web/public-site/.next/
```

## Architecture

### Backend (`src/cofounder_agent/`)

**Entry point:** `main.py` — FastAPI app initializing service container, database pools, orchestrator, and registering all 28 route modules via `register_all_routes()`.

**Key services:**

- `services/model_router.py` — LLM provider selection with automatic fallback: Ollama → Anthropic → OpenAI → Google → echo/mock. **Never hardcode model names; use cost tiers.**
- `services/database_service.py` — Coordinates 5 specialized DB modules (Users, Tasks, Content, Admin, WritingStyle). PostgreSQL-only; no SQLite fallback.
- `services/unified_orchestrator.py` — Master agent choreography and task distribution
- `services/workflow_executor.py` — Phase-based workflow execution with real-time WebSocket progress events
- `services/capability_registry.py` — Intent-based task routing; auto-selects agents from natural language requests

**Agent system:** Four core agent types in `src/cofounder_agent/agents/` (Content, Financial, Market Insight, Compliance). The content agent runs a 6-stage self-critiquing pipeline: Research → Creative Draft → QA Critique → Creative Refinement → Image Selection → Publishing Prep (with DB Storage). QA agents critique without rewriting; Creative agents apply the feedback.

**Database:** asyncpg for direct PostgreSQL interaction + raw SQL migration files in `services/migrations/`. Five domain modules delegate from `DatabaseService`.

**Python toolchain:** Poetry for dependency management. Run with `poetry run` inside `src/cofounder_agent/`. pytest markers: `unit`, `integration`, `api`, `e2e`, `performance`, `slow`, `voice`.

### Frontend

**Oversight Hub** (`web/oversight-hub/`): Vite + React 18 admin app. Vitest for unit tests. Path alias `@/` → `src/`. State managed with Zustand. Real-time updates via WebSocket to `/api/workflow-progress/ws/{execution_id}` (backend URL from `VITE_API_URL`).

**Public Site** (`web/public-site/`): Next.js 15 app router (no `pages/` directory). Markdown content via gray-matter + marked. Static generation with ISR. Jest + React Testing Library for tests.

**API integration:** Both frontends call `http://localhost:8000/*`. No direct database access from frontend.

### Configuration

Each service reads from its own `.env.local` file:

- **Backend:** Reads `.env.local` from project root (configured in `src/cofounder_agent/config/__init__.py`)
- **Public Site (Next.js):** Reads `.env.local` from `web/public-site/`
- **Admin Hub (Vite):** Reads `.env.local` from `web/oversight-hub/`

**Minimum required:**

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/glad_labs_dev
# Plus at least ONE of: ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, or OLLAMA_BASE_URL
```

**Key feature flags:** `ENABLE_TRACING`, `ENABLE_QUERY_MONITORING`, `ENABLE_TRAINING_CAPTURE`, `SENTRY_ENABLED`, `REDIS_ENABLED`.

### Monorepo Structure

npm workspaces cover `web/public-site` and `web/oversight-hub`. `npm install` at root installs everything. Python deps are managed separately via Poetry in `src/cofounder_agent/`.

### Deployment

- `main` branch → Vercel (frontend) + Railway (backend) auto-deploy
- `dev` branch → Railway staging auto-deploy
- Feature branches (`feature/*`, `bugfix/*`) → local only, no CI cost

## Key Principles

- **Async-everywhere:** FastAPI uses async/await throughout; never block the event loop
- **API-first:** All state access goes through REST endpoints, not direct DB calls from frontend
- **PostgreSQL as source of truth:** All task results, agent memories, and content stored there
- **Model router first:** Use cost tiers (`free`/`budget`/`standard`/`premium`/`flagship`) not hardcoded model names
- **Monorepo with workspaces:** `npm install` once at root covers everything
- **API versioning policy:** All 160+ endpoints live at `/api/{resource}` (no `/v1/` prefix). This is the current v1 surface, documented via `version="3.0.x"` in `main.py` and OpenAPI at `/api/openapi.json`. **Policy:** Breaking changes to any public endpoint (field renames, status code changes, required field additions) MUST introduce a new URL version prefix (`/api/v2/`). Non-breaking additions (new optional fields, new endpoints) do not require a new version. Document breaking changes in `CHANGELOG.md`.

## Reference Documentation

- Architecture: `docs/02-Architecture/System-Design.md`
- Deployment/CI: `docs/05-Operations/Operations-Maintenance.md`, `docs/04-Development/Development-Workflow.md`
- AI agents: `docs/02-Architecture/Multi-Agent-Pipeline.md`
- Troubleshooting: `docs/troubleshooting/`
- Full env variable reference: `.env.example` (60+ variables)
