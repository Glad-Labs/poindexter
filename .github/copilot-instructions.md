# Glad Labs Copilot Instructions

**Last Updated:** January 21, 2026  
**Project:** Glad Labs AI Co-Founder System  
**Version:** 2.0 (Updated with Current Codebase Analysis)

## Project Overview

Glad Labs is a **production-ready AI orchestration system** combining autonomous agents, multi-provider LLM routing, and full-stack web applications. It's a monorepo with three main services:

- **Backend:** Python FastAPI server (port 8000) orchestrating specialized AI agents ("Poindexter")
- **Admin UI:** React 18 + Material-UI dashboard (port 3001) for monitoring and control
- **Public Site:** Next.js 15 website (port 3000) for content distribution

**Key Architecture:** Multi-agent system with comprehensive task orchestration, PostgreSQL persistence, intelligent model router with Ollama/OpenAI/Anthropic/Google support, and unified REST API (18+ route modules).

---

## Critical Knowledge for Productivity

### 1. Service Architecture & Startup

**Three-service startup pattern** (all async, all required for full system):

```bash
# From repo root - PRIMARY COMMAND for full dev environment:
npm run dev

# This uses concurrently to run:
# - npm run dev:cofounder  → Python FastAPI with uvicorn (port 8000) @ src/cofounder_agent/
# - npm run dev:public     → Next.js dev server (port 3000) @ web/public-site/
# - npm run dev:oversight  → React dev server (port 3001) @ web/oversight-hub/

# Alternative: run services individually
npm run dev:cofounder     # Just backend
npm run dev:backend       # Alias for dev:cofounder
npm run dev:frontend      # Both React apps
npm run dev:public        # Just Next.js
npm run dev:oversight     # Just React admin
npm run dev:all           # All three (same as npm run dev but setup may vary)
```

**Service Startup Details:**

- **Backend:** Uses `poetry run uvicorn main:app --reload` with hot reloading
- **Public Site:** Next.js dev server (TypeScript + React 18 + TailwindCSS)
- **Oversight Hub:** React 18 + Material-UI (port 3001)
- **Config Path:** All services read from `.env.local` at project root (single source of truth)

**Services always listen together.** Client code expects all three running. If debugging one service, still keep the others running.

**Ports Always Used:**

- Backend: `8000`
- Public Site: `3000`
- Oversight Hub: `3001`
- PostgreSQL: `5432` (local or remote)

### 2. Backend: FastAPI + Specialized Agents

**Entry Point:** `src/cofounder_agent/main.py` - FastAPI app with comprehensive route modules and middleware.

**Project Structure (Backend):**

```
src/cofounder_agent/
├── main.py                    # FastAPI initialization, CORS, middleware setup
├── routes/                    # 18+ route modules
│   ├── task_routes.py         # Task CRUD and execution
│   ├── agents_routes.py       # Agent management
│   ├── model_routes.py        # LLM model selection/health
│   ├── chat_routes.py         # Real-time chat/agent communication
│   ├── workflow_history.py    # Workflow tracking
│   ├── analytics_routes.py    # Analytics and metrics
│   ├── command_queue_routes.py# Task queueing system
│   ├── bulk_task_routes.py    # Batch operations
│   ├── cms_routes.py          # CMS integration (Strapi)
│   ├── media_routes.py        # Media/asset management
│   ├── websocket_routes.py    # Real-time WebSocket
│   ├── webhooks.py            # External webhooks
│   └── [12+ other routes]     # Settings, social, auth, etc.
├── services/                  # 60+ service modules
│   ├── database_service.py    # Coordinator for 5 DB modules
│   ├── model_router.py        # Cost-optimized LLM routing
│   ├── task_executor.py       # Task execution orchestration
│   ├── content_critique_loop.py # Self-critiquing pipeline
│   ├── unified_orchestrator.py # Master agent choreography
│   └── [55+ specialized]      # OAuth, caching, webhooks, ML, etc.
├── agents/                    # 4 core agent types
│   ├── content_agent/         # 6-stage self-critiquing pipeline
│   ├── financial_agent/       # Cost/ROI tracking
│   ├── market_insight_agent/  # Trend analysis
│   └── compliance_agent/      # Legal/risk review
├── models/                    # Pydantic request/response schemas
├── tasks/                     # Task execution logic
├── middleware/                # Auth, logging, error handling
├── tests/                     # Pytest suite (~200+ tests)
└── config/                    # Configuration modules
```

**Specialized Database Modules (DatabaseService Delegates To):**

- `UsersDatabase` - User accounts, OAuth, authentication
- `TasksDatabase` - Task CRUD, filtering, status tracking
- `ContentDatabase` - Posts, quality scores, publishing metrics
- `AdminDatabase` - Logging, financial tracking, health, settings
- `WritingStyleDatabase` - Writing samples for RAG style matching

**Model Router Pattern:** `services/model_router.py` implements intelligent fallback:

- **Primary:** Ollama (local, zero-cost, ~20ms latency)
- **Fallback 1:** Anthropic Claude (configurable models)
- **Fallback 2:** OpenAI (configurable models)
- **Fallback 3:** Google Gemini
- **Final Fallback:** Echo/mock response

Route selection is **automatic** based on API key availability + model configuration in `.env.local`. This is NOT manual selection - it's intelligent fallback.

### 3. Frontend: React (Oversight Hub) + Next.js (Public Site)

**Oversight Hub** - React 18 + Material-UI + React Scripts. This is the control center for monitoring agents, viewing tasks, configuring models. REST calls to port 8000.

**Public Site** - Next.js 15 with TypeScript + TailwindCSS. Markdown-based content distribution with static generation. Consumes from PostgreSQL via FastAPI endpoints.

**API Integration Pattern:** Both frontends use REST calls to `http://localhost:8000/*` endpoints. No direct database access from frontend.

### 4. Development Workflow

**Branch Strategy:** Four-tier system (Tier 1: local, Tier 2: feature branches, Tier 3: dev/staging, Tier 4: main/production).

- **Local work:** Feature branches cost $0 (no CI/CD triggered)
- **Feature branches:** `feature/*`, `bugfix/*`, `docs/*` - test locally with `npm run dev`
- **Staging:** `dev` branch auto-deploys to Railway staging on push
- **Production:** `main` branch auto-deploys to Vercel (frontend) + Railway (backend)

**Testing:**

```bash
# Python backend
npm run test:python          # Full test suite
npm run test:python:smoke    # Fast smoke tests (e2e_fixed.py)

# Frontend
npm run test                 # Runs Jest for all workspaces

# Format check
npm run format:check
```

### 5. Content Generation Pipeline (Self-Critiquing)

**6-Stage Agent Flow** for content creation (located in `src/cofounder_agent/agents/content_agent/`):

1. **Research Agent** - Gathers background, identifies key points
2. **Creative Agent** - Generates initial draft with brand voice
3. **QA Agent** - Critiques quality, suggests improvements WITHOUT rewriting
4. **Creative Agent (Refined)** - Incorporates feedback, improves draft
5. **Image Agent** - Selects/generates visuals, alt text, metadata
6. **Publishing Agent** - Formats for CMS, adds SEO metadata, converts to markdown

**Key Pattern:** Agents **critique without rewriting.** QA provides feedback, Creative agent applies it. This loop repeats if needed for quality threshold.

### 6. Multi-Provider AI Integration (MCP)

**Model Context Protocol** (`src/mcp/`) provides standardized tool access and cost optimization.

**Cost Tiers (in MCPContentOrchestrator):**

- `ultra_cheap`: Ollama (local)
- `cheap`: Gemini (low API cost)
- `balanced`: Claude 3.5 Sonnet / GPT-4 Turbo
- `premium`: Claude 3 Opus
- `ultra_premium`: Multi-model ensemble

**Pattern:** Don't hardcode model names. Use cost tier selection - system automatically picks best model available for the tier.

### 7. Key Files Reference

| Purpose             | Path                                                   | What It Does                                                    |
| ------------------- | ------------------------------------------------------ | --------------------------------------------------------------- |
| FastAPI entry       | `src/cofounder_agent/main.py`                          | Route registration, middleware setup, app initialization        |
| Agent orchestration | `src/cofounder_agent/services/unified_orchestrator.py` | Coordinates agent fleet, task distribution                      |
| Database service    | `src/cofounder_agent/services/database_service.py`     | PostgreSQL queries, ORM models, persistence                     |
| Model routing       | `src/cofounder_agent/services/model_router.py`         | LLM provider selection with fallback chain                      |
| Content agent       | `src/cofounder_agent/agents/content_agent/`            | 6-stage self-critiquing pipeline                                |
| Tasks               | `src/cofounder_agent/tasks/`                           | Task models, execution logic, status tracking                   |
| Routes              | `src/cofounder_agent/routes/`                          | `/tasks`, `/agents`, `/content`, `/models`, `/health` endpoints |
| Oversight Hub       | `web/oversight-hub/`                                   | React dashboard, agent monitoring, task management              |
| Public Site         | `web/public-site/`                                     | Next.js content distribution, SEO optimization                  |

### 8. Common Developer Patterns

**Starting fresh development:**

```bash
npm run clean:install    # Full reset of node_modules, cache, venv
npm run setup:all        # Install all Python + Node deps
npm run dev              # Start all three services
```

**Checking if services are running:**

```bash
# Should return HTTP 200
curl http://localhost:8000/health         # Backend
curl http://localhost:3001/health         # Oversight Hub (if endpoint exists)
curl http://localhost:3000                # Public Site
```

**Debugging Python backend:**

- Logs appear in terminal running `npm run dev:cofounder` or `npm run dev`
- Set `--log-level debug` in `dev:cofounder` script for verbose output
- PostgreSQL queries logged if `SQL_DEBUG=true` in `.env.local`

**Testing agent pipeline:**

```bash
# Fast smoke test
npm run test:python:smoke

# Full test with coverage
npm run test:python
```

---

## Project Structure (What Goes Where)

```
glad-labs-website/
├── .env.local              # SINGLE SOURCE: All service config (Python + Node)
├── .github/                # GitHub Actions, copilot instructions
├── docs/                   # 7 core docs + troubleshooting/reference
├── scripts/                # Utility scripts (setup, migrate, health checks)
├── src/
│   ├── cofounder_agent/    # **Main orchestrator** (FastAPI, port 8000)
│   │   ├── main.py         # App entry point
│   │   ├── routes/         # 18+ REST endpoint modules
│   │   ├── services/       # 60+ service modules (model_router, database_service, task_executor)
│   │   ├── models/         # Pydantic schemas for requests/responses
│   │   ├── tasks/          # Task execution and scheduling
│   │   ├── middleware/     # Auth, logging, error handling
│   │   └── tests/          # pytest suite (~200+ tests)
│   ├── agents/             # Specialized agent implementations
│   │   ├── content_agent/  # 6-stage self-critiquing content pipeline
│   │   ├── financial_agent/
│   │   ├── market_insight_agent/
│   │   └── compliance_agent/
│   ├── mcp/                # Model Context Protocol integration
│   ├── mcp_server/         # MCP server implementations
│   └── services/           # Shared service modules
└── web/
    ├── public-site/        # **Content distribution** (Next.js 15, port 3000)
    └── oversight-hub/      # **Control center** (React 18 + Material-UI, port 3001)
```

---

## Environment Variables (Critical)

Set in `.env.local` at project root:

```env
# Database (required for persistence)
DATABASE_URL=postgresql://user:pass@localhost:5432/glad_labs

# LLM API Keys (at least ONE required - tested in priority order)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza-...

# Ollama (if using local models, no key needed)
OLLAMA_BASE_URL=http://localhost:11434

# Optional: Model preferences
LLM_PROVIDER=claude  # Force specific provider (fallback still applies)
DEFAULT_MODEL_TEMPERATURE=0.7

# Optional: Debugging
SQL_DEBUG=false
LOG_LEVEL=info
SENTRY_DSN=       # Error tracking (production)
```

**Important:** Python and Node both read from same `.env.local`. No per-service env files.

---

## Debugging Checklist

1. **Services not starting?**
   - Ensure Node 18+ and Python 3.12+ installed
   - Run `npm run clean:install` to reset dependencies
   - Check `.env.local` exists and has at least one LLM API key

2. **PostgreSQL errors?**
   - Verify `DATABASE_URL` in `.env.local` is correct
   - Run migrations: See docs/reference for migration scripts
   - Check PostgreSQL is running: `psql -U postgres`

3. **Model router failing?**
   - Check which API keys are set in `.env.local`
   - Run `curl http://localhost:8000/health` to see model status
   - Ollama should be running on port 11434 for local models

4. **React/Next.js not loading?**
   - Check CORS headers - FastAPI allows all origins by default
   - Verify ports 3000 and 3001 aren't in use
   - Clear browser cache: Hard refresh (Ctrl+Shift+R)

5. **Agent tasks not executing?**
   - Check PostgreSQL has `tasks` table
   - Verify Orchestrator is initialized in main.py startup
   - Check agent logs: `tail -f server.log` in cofounder_agent/

---

## When to Reference Full Documentation

- **Architecture questions:** → `docs/02-ARCHITECTURE_AND_DESIGN.md`
- **Deployment questions:** → `docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md`
- **CI/CD and branching:** → `docs/04-DEVELOPMENT_WORKFLOW.md`
- **Agent capabilities:** → `docs/05-AI_AGENTS_AND_INTEGRATION.md`
- **Operations/monitoring:** → `docs/06-OPERATIONS_AND_MAINTENANCE.md`
- **Troubleshooting:** → `docs/troubleshooting/` folder

---

## Key Principles

1. **API-First Design:** Everything exposed via REST, no direct database access from frontend
2. **Async-Everywhere:** Python uses FastAPI + async/await, don't block event loops
3. **Model Router First:** Never hardcode model names - use cost tiers or automatic fallback
4. **PostgreSQL as Source of Truth:** All state (tasks, results, memories) persisted there
5. **Monorepo with Workspace:** All services installed with single `npm install`, managed together
6. **Self-Critiquing Quality:** Content agents critique each other's work, not manual review
7. **Cost Optimization:** Ollama → cheap APIs → premium APIs (intelligent fallback)
