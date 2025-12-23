# Glad Labs Copilot Instructions

**Last Updated:** December 22, 2025  
**Project:** Glad Labs AI Co-Founder System  
**Version:** 1.0

## Project Overview

Glad Labs is a **production-ready AI orchestration system** combining autonomous agents, multi-provider LLM routing, and full-stack web applications. It's a monorepo with three main services:

- **Backend:** Python FastAPI server (port 8000) orchestrating specialized AI agents
- **Admin UI:** React dashboard (port 3001) for monitoring and control
- **Public Site:** Next.js website (port 3000) for content distribution

**Key Architecture:** Multi-agent system with self-critiquing content pipeline, PostgreSQL persistence, and intelligent model router (Ollama → Claude → GPT → Gemini fallback).

---

## Critical Knowledge for Productivity

### 1. Service Architecture & Startup

**Three-service startup pattern** (all async, all required for full system):

```bash
# From repo root - ALWAYS use this command for full dev environment:
npm run dev

# This concurrently starts:
# - python -m uvicorn main:app --reload (port 8000) @ src/cofounder_agent/
# - npm run dev (port 3000) @ web/public-site/
# - npm start (port 3001) @ web/oversight-hub/
```

**Services always listen together.** Client code expects all three running. If debugging one service, still keep the others running.

**Config Pattern:** All services use `.env.local` at project root (not per-service). Python agents read from `os.path.join(project_root, '.env.local')`.

### 2. Backend: FastAPI + Specialized Agents

**Entry Point:** [src/cofounder_agent/main.py](src/cofounder_agent/main.py) - FastAPI app with routes for tasks, agents, content, models, and health checks.

**Core Pattern - Multi-Agent Orchestration:**

```python
# Typical agent execution flow:
from orchestrator_logic import Orchestrator

# Orchestrator coordinates agent fleet:
# - Content Agent (6-stage self-critiquing pipeline: research → create → critique → refine → image → publish)
# - Financial Agent (cost tracking, ROI analysis, budget alerts)
# - Market Insight Agent (trend analysis, competitive intelligence)
# - Compliance Agent (legal review, risk assessment)

# Agents DON'T run independently - they're choreographed by Orchestrator
```

**Database:** PostgreSQL with [services/database_service.py](src/cofounder_agent/services/database_service.py). All persistent data (tasks, results, memories) flows through PostgreSQL, not in-memory.

**Model Router Pattern:** [services/model_router.py](src/cofounder_agent/services/model_router.py) implements intelligent fallback:
- **Primary:** Ollama (local, zero-cost)
- **Fallback 1:** Claude 3 Opus
- **Fallback 2:** GPT-4
- **Fallback 3:** Gemini
- **Final Fallback:** Echo/mock response

Route selection is determined by API key availability + model configuration in `.env.local`. This is **NOT manual selection** - it's automatic based on what keys are set.

### 3. Frontend: React (Oversight Hub) + Next.js (Public Site)

**Oversight Hub** ([web/oversight-hub/](web/oversight-hub/)) - React 18 + Material-UI + Zustand state management. This is the control center for monitoring agents, viewing tasks, configuring models. REST calls to port 8000.

**Public Site** ([web/public-site/](web/public-site/)) - Next.js 15 with static generation for content. Consumes from PostgreSQL via FastAPI endpoints.

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

**6-Stage Agent Flow** for content creation (located in [src/agents/content_agent/](src/agents/content_agent/)):

1. **Research Agent** - Gathers background, identifies key points
2. **Creative Agent** - Generates initial draft with brand voice
3. **QA Agent** - Critiques quality, suggests improvements WITHOUT rewriting
4. **Creative Agent (Refined)** - Incorporates feedback, improves draft
5. **Image Agent** - Selects/generates visuals, alt text, metadata
6. **Publishing Agent** - Formats for CMS, adds SEO metadata, converts to markdown

**Key Pattern:** Agents **critique without rewriting.** QA provides feedback, Creative agent applies it. This loop repeats if needed for quality threshold.

### 6. Multi-Provider AI Integration (MCP)

**Model Context Protocol** ([src/mcp/](src/mcp/)) provides standardized tool access and cost optimization.

**Cost Tiers (in MCPContentOrchestrator):**
- `ultra_cheap`: Ollama (local)
- `cheap`: Gemini (low API cost)
- `balanced`: Claude 3.5 Sonnet / GPT-4 Turbo
- `premium`: Claude 3 Opus
- `ultra_premium`: Multi-model ensemble

**Pattern:** Don't hardcode model names. Use cost tier selection - system automatically picks best model available for the tier.

### 7. Key Files Reference

| Purpose | Path | What It Does |
|---------|------|--------------|
| FastAPI entry | [src/cofounder_agent/main.py](src/cofounder_agent/main.py) | Route registration, middleware setup, app initialization |
| Agent orchestration | [src/cofounder_agent/orchestrator_logic.py](src/cofounder_agent/orchestrator_logic.py) | Coordinates agent fleet, task distribution |
| Database service | [src/cofounder_agent/services/database_service.py](src/cofounder_agent/services/database_service.py) | PostgreSQL queries, ORM models, persistence |
| Model routing | [src/cofounder_agent/services/model_router.py](src/cofounder_agent/services/model_router.py) | LLM provider selection with fallback chain |
| Content agent | [src/agents/content_agent/](src/agents/content_agent/) | 6-stage self-critiquing pipeline |
| Tasks | [src/cofounder_agent/tasks/](src/cofounder_agent/tasks/) | Task models, execution logic, status tracking |
| Routes | [src/cofounder_agent/routes/](src/cofounder_agent/routes/) | `/tasks`, `/agents`, `/content`, `/models`, `/health` endpoints |
| Oversight Hub | [web/oversight-hub/](web/oversight-hub/) | React dashboard, agent monitoring, task management |
| Public Site | [web/public-site/](web/public-site/) | Next.js content distribution, SEO optimization |

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
│   │   ├── routes/         # 50+ REST endpoints
│   │   ├── services/       # model_router, database_service, task_executor
│   │   ├── models/         # Pydantic schemas for requests/responses
│   │   ├── tasks/          # Task execution and scheduling
│   │   ├── middleware/     # Auth, logging, error handling
│   │   └── tests/          # pytest suite
│   ├── agents/             # Specialized agent implementations
│   │   ├── content_agent/  # 6-stage self-critiquing content pipeline
│   │   ├── financial_agent/
│   │   ├── market_insight_agent/
│   │   └── compliance_agent/
│   └── mcp/                # Model Context Protocol integration
└── web/
    ├── public-site/        # **Content distribution** (Next.js, port 3000)
    └── oversight-hub/      # **Control center** (React, port 3001)
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
