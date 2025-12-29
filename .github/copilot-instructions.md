# Glad Labs Copilot Instructions

<<<<<<< HEAD
**Last Updated:** November 5, 2025 (ESLint v9 Migration Complete)  
**Project:** Glad Labs AI Co-Founder System v3.0  
**Status:** Production Ready | PostgreSQL Backend | Ollama AI Integration | ESLint v9 Configured | Windows PowerShell Required
=======
**Last Updated:** December 22, 2025  
**Project:** Glad Labs AI Co-Founder System  
**Version:** 1.0

## Project Overview

Glad Labs is a **production-ready AI orchestration system** combining autonomous agents, multi-provider LLM routing, and full-stack web applications. It's a monorepo with three main services:

- **Backend:** Python FastAPI server (port 8000) orchestrating specialized AI agents
- **Admin UI:** React dashboard (port 3001) for monitoring and control
- **Public Site:** Next.js website (port 3000) for content distribution

**Key Architecture:** Multi-agent system with self-critiquing content pipeline, PostgreSQL persistence, and intelligent model router (Ollama → Claude → GPT → Gemini fallback).
>>>>>>> feat/refine

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

<<<<<<< HEAD
```powershell
cd c:\Users\mattm\glad-labs-website

# ESLint v9 (migrated Nov 5, 2025 - IMPORTANT!)
npm run lint             # ESLint across all projects (CommonJS + ES Module configs)
npm run lint -- --fix    # Auto-fix ESLint issues

# Formatting & Testing
npm run format           # Prettier on all files (.js, .jsx, .tsx, .json, .md)
npm test                 # Jest frontend + pytest Python backend
npm run test:python:smoke # Quick backend smoke tests (5-10 min)
```

**⚠️ ESLint Migration (Nov 5, 2025):**

- Both frontend projects now use ESLint v9 with `eslint.config.js` (flat config format)
- Oversight Hub: CommonJS format (`require()`) for react-scripts compatibility
- Public Site: ES Module format (`import`) with `"type": "module"` in package.json
- `.eslintignore` files deprecated - patterns now in `eslint.config.js` under `ignores` section
- Current: ~670 linting issues identified across both projects (code quality, not config errors)
- **Status:** ✅ ESLint infrastructure complete | Code cleanup deferred to next phase
- **Note:** "npm run lint" now shows issues but does NOT block builds; code quality improvement ongoing

### ESLint v9 Configuration Details

**Oversight Hub** (`web/oversight-hub/eslint.config.js`):

- Uses CommonJS (required for react-scripts)
- Plugins: React, React Hooks
- Files matched: `src/**/*.{js,jsx}`
- Globals: browser, es2021, browser
- Key rule: Warnings for unused vars, console.log, prop validation

**Public Site** (`web/public-site/eslint.config.js`):

- Uses ES Module format (with `"type": "module"` in package.json)
- Plugins: React, Next.js
- Files matched: `components/**`, `pages/**`, `lib/**`, `app/**` (\*.{js,jsx})
- Globals: browser, es2021, node, jest
- Key rule: Errors for unescaped entities, warnings for missing prop types

**Common Ignore Patterns** (both projects):

```
node_modules/, build/, dist/, .next/, coverage/, .env, *.log, .DS_Store, config files
```

**How to fix linting issues**:

```powershell
npm run lint -- --fix              # Auto-fix what can be fixed
npm run lint                       # Review remaining issues
npm run lint -- --format=compact   # Compact output format
```

---

### Build & Deploy
=======
- **Primary:** Ollama (local, zero-cost)
- **Fallback 1:** Claude 3 Opus
- **Fallback 2:** GPT-4
- **Fallback 3:** Gemini
- **Final Fallback:** Echo/mock response

Route selection is determined by API key availability + model configuration in `.env.local`. This is **NOT manual selection** - it's automatic based on what keys are set.
>>>>>>> feat/refine

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

<<<<<<< HEAD
```powershell
npm run dev              # ✅ Starts all services concurrently (recommended)
npm run dev:frontend     # Starts both React apps (Oversight Hub + Public Site)
npm run dev:backend      # Starts Strapi CMS + Co-founder Agent (Python)
npm run dev:oversight    # React admin dashboard on http://localhost:3001
npm run dev:public       # Next.js site on http://localhost:3000
npm run dev:cofounder    # FastAPI backend on http://localhost:8000
=======
```bash
# Python backend
npm run test:python          # Full test suite
npm run test:python:smoke    # Fast smoke tests (e2e_fixed.py)

# Frontend
npm run test                 # Runs Jest for all workspaces

# Format check
npm run format:check
>>>>>>> feat/refine
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

| Purpose             | Path                                                                                                 | What It Does                                                    |
| ------------------- | ---------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| FastAPI entry       | [src/cofounder_agent/main.py](src/cofounder_agent/main.py)                                           | Route registration, middleware setup, app initialization        |
| Agent orchestration | [src/cofounder_agent/orchestrator_logic.py](src/cofounder_agent/orchestrator_logic.py)               | Coordinates agent fleet, task distribution                      |
| Database service    | [src/cofounder_agent/services/database_service.py](src/cofounder_agent/services/database_service.py) | PostgreSQL queries, ORM models, persistence                     |
| Model routing       | [src/cofounder_agent/services/model_router.py](src/cofounder_agent/services/model_router.py)         | LLM provider selection with fallback chain                      |
| Content agent       | [src/agents/content_agent/](src/agents/content_agent/)                                               | 6-stage self-critiquing pipeline                                |
| Tasks               | [src/cofounder_agent/tasks/](src/cofounder_agent/tasks/)                                             | Task models, execution logic, status tracking                   |
| Routes              | [src/cofounder_agent/routes/](src/cofounder_agent/routes/)                                           | `/tasks`, `/agents`, `/content`, `/models`, `/health` endpoints |
| Oversight Hub       | [web/oversight-hub/](web/oversight-hub/)                                                             | React dashboard, agent monitoring, task management              |
| Public Site         | [web/public-site/](web/public-site/)                                                                 | Next.js content distribution, SEO optimization                  |

### 8. Common Developer Patterns

**Starting fresh development:**

<<<<<<< HEAD
```powershell
npm run lint             # Run ESLint across all workspaces
npm run lint -- --fix    # Auto-fix all linting issues (ESLint v9)
npm run format           # Prettier on all files (.js, .jsx, .tsx, .json, .md)
npm test                 # Jest frontend + pytest Python backend
npm run test:python:smoke # Quick backend smoke tests for rapid iteration
=======
```bash
npm run clean:install    # Full reset of node_modules, cache, venv
npm run setup:all        # Install all Python + Node deps
npm run dev              # Start all three services
>>>>>>> feat/refine
```

**Checking if services are running:**

<<<<<<< HEAD
```powershell
npm run build            # Build Next.js + React production bundles
npm run clean            # Clean all build artifacts and node_modules
npm run clean:install    # Full reset and fresh install
=======
```bash
# Should return HTTP 200
curl http://localhost:8000/health         # Backend
curl http://localhost:3001/health         # Oversight Hub (if endpoint exists)
curl http://localhost:3000                # Public Site
>>>>>>> feat/refine
```

**Debugging Python backend:**

- Logs appear in terminal running `npm run dev:cofounder` or `npm run dev`
- Set `--log-level debug` in `dev:cofounder` script for verbose output
- PostgreSQL queries logged if `SQL_DEBUG=true` in `.env.local`

**Testing agent pipeline:**

<<<<<<< HEAD
```powershell
npm run setup            # Install all dependencies (Node + Python)
npm run install:all      # Just npm install across all workspaces
npm run test:ci          # CI-mode testing (coverage, no watch)
npm run test:python      # Full pytest backend suite
=======
```bash
# Fast smoke test
npm run test:python:smoke

# Full test with coverage
npm run test:python
>>>>>>> feat/refine
```

---

<<<<<<< HEAD
## 📋 Code Patterns & Conventions (NOT aspirational - these are discovered patterns)

### Frontend Linting & Code Quality (NEW - Nov 5, 2025)

**ESLint v9 Configuration** (`eslint.config.js` files - flat config format)

Both frontend projects were migrated from deprecated ESLint v8 `.eslintrc.json` to ESLint v9 on November 5, 2025:

- **Oversight Hub** (`web/oversight-hub/eslint.config.js`):
  - CommonJS format (required for react-scripts)
  - Imports: `@eslint/js`, `globals`, `eslint-plugin-react`, `eslint-plugin-react-hooks`
  - Pattern: `module.exports = [{ ignores: [...], files: [...], plugins: {...}, rules: {...} }]`

- **Public Site** (`web/public-site/eslint.config.js`):
  - ES Module format with `"type": "module"` in package.json
  - Imports: `@eslint/js`, `eslint-plugin-next`, `@next/eslint-plugin-next`, `globals`
  - Pattern: `export default [{ ignores: [...], files: [...], plugins: {...} }]`

**Critical Discovery**: Both projects have `~670` identified linting issues (not config errors):

- **Oversight Hub**: ~60 warnings (unused imports, console.log, prop validation)
- **Public Site**: ~80+ warnings/errors (unescaped entities, missing prop types, console.log)

**PATTERN TO FOLLOW**:

1. When editing React/Next.js code, run `npm run lint -- --fix` to auto-fix issues
2. For configuration changes, edit `eslint.config.js` directly (not `.eslintignore`)
3. When adding new rules, follow project-specific patterns (CommonJS vs ES Module)
4. Ignore patterns are in `config.ignores` array, not separate files

**When to NOT modify ESLint config**: Unless changing linting rules, use `npm run lint -- --fix` instead of manual editing.

### Python Backend Patterns (src/cofounder_agent/)

**FastAPI Route Structure** (`src/cofounder_agent/routes/`)

- Routes are modular: `content_router`, `models_router`, `auth_router`, `enhanced_content_router`
- All routes injected into main FastAPI app in `main.py`
- Routes depend on orchestrator and database services
- **PATTERN:** Routes handle HTTP validation; orchestrator handles business logic

**Specialized Agents** (`src/agents/`)

The multi-agent system includes 5 specialized agents working in parallel:

- **Content Agent:** Content generation, planning, SEO optimization
- **Financial Agent:** Cost tracking, budget management, ROI calculations
- **Market Insight Agent:** Market analysis, competitor research, trend detection
- **Compliance Agent:** Legal compliance, data privacy, regulatory checks
- **Social Media Agent:** Social media strategy, content scheduling, engagement

**PATTERN:** Each agent inherits from `BaseAgent`, implements async execution, integrates with orchestrator

**Orchestrator Pattern** (`orchestrator_logic.py`)

- Central `Orchestrator` class coordinates all agent execution
- Async methods for parallel task processing via `asyncio.gather()`
- Multi-provider model routing (Ollama → OpenAI → Claude → Gemini fallback)
- **KEY FILE:** `src/cofounder_agent/main.py` shows FastAPI setup and route registration
- **PATTERN:** Thin controllers, thick orchestrator

**Database Patterns** (`database.py`)

- SQLAlchemy models for local SQLite (dev) / PostgreSQL (prod)
- Audit logging middleware wraps all CRUD operations
- JWT token storage separate from user model
- **PATTERN:** All DB changes logged to `audit_logging.py` middleware

**Error Handling - Watch for:**

- Google Cloud integrations optional: `try/except ImportError` for Firestore/Pub/Sub
- Database optional during dev: check `DATABASE_AVAILABLE` flag before using db
- Model provider failures trigger automatic fallback (don't wrap in try/except - router handles it)

### React/Next.js Patterns (web/)

**Oversight Hub State Management** (`web/oversight-hub/src/`)

- **Single source of truth:** `store/useStore.js` (Zustand global state)
- Theme management: `useStore((state) => state.theme)` pattern used throughout
- **PATTERN:** Never prop-drill state; use Zustand selectors
- Components subscribe to specific store slices: `useStore(state => state.singleValue)` not entire state

**Next.js Public Site Patterns** (`web/public-site/`)

- **SSG First:** Use `getStaticProps` and `getStaticPaths` (not SSR for performance)
- **ISR (Incremental Static Regeneration):** `revalidate: 3600` for content updates
- Strapi API client in `lib/api.js` (centralized, never scatter API calls)
- **PATTERN:** All Strapi calls go through `lib/api.js` to enable caching and error handling

**Component Organization:**

- Lightweight presentational components in `components/`
- Business logic in custom hooks or store selectors
- Material-UI components preferred (already in dependencies)
- **PATTERN:** No nested components, use composition

### Testing Patterns (Already Existing)

**Frontend:** Jest + React Testing Library

- Test location: `__tests__/` folders parallel to source
- Mock Strapi API responses in tests (don't hit real API)
- Example pattern: `expect(getByText(...)).toBeInTheDocument()`

**Backend:** pytest for Python

- Test location: `src/cofounder_agent/tests/`
- Mock external services (Google Cloud, Strapi, LLMs)
- Run with: `npm run test:python` or `npm run test:python:smoke` (faster)

---

## ⚠️ Known Constraints & Pain Points (For AI Agent Context)

**Strapi v5 Build Issues (cms/strapi-v5-backend/)**

- Current status: ✅ **OPERATIONAL** (not a blocker)
- Runs reliably in development via `npm run develop`
- Uses SQLite locally, PostgreSQL in production
- Known plugin compatibility considerations documented
- **Current approach:** Use working locally, deploy PostgreSQL to production
- **Don't attempt:** Deep plugin debugging unless explicitly assigned

**Async/Await Patterns in Python**

- Backend uses heavy async (FastAPI + asyncio)
- All orchestrator methods are `async`; use `await` when calling them
- Parallel execution via `asyncio.gather()` - don't use threading
- Google Cloud operations non-blocking (Firestore, Pub/Sub async)

**Frontend Port Conflicts**

- Both Oversight Hub and Public Site want port 3000
- System auto-assigns next available (3001, 3002) - don't hardcode ports
- Verify actual port in terminal output after `npm run dev`

**Environment Variables - Critical!**

- Local dev: Copy `.env.example` → `.env` (never commit .env)
- Production secrets: GitHub Secrets + Railway/Vercel dashboards
- No production secrets should ever appear in code or docs
- Model provider keys (OPENAI_API_KEY, etc.) are required for backend

---

## � File Organization & Where to Look

| Need                     | Look In                                                                 |
| ------------------------ | ----------------------------------------------------------------------- |
| FastAPI backend logic    | `src/cofounder_agent/main.py`, `orchestrator_logic.py`, `routes/`       |
| AI agent implementations | `src/agents/` with specialized agents:                                  |
|                          | - `content_agent/` - Content creation and management                    |
|                          | - `financial_agent/` - Business metrics and projections                 |
|                          | - `market_insight_agent/` - Market analysis and trends                  |
|                          | - `compliance_agent/` - Regulatory compliance checking                  |
|                          | - `social_media_agent/` - Social media strategy and posting             |
| React admin dashboard    | `web/oversight-hub/src/components/`, `store/useStore.js`                |
| Next.js public site      | `web/public-site/pages/`, `lib/api.js`, `components/`                   |
| Strapi CMS setup         | `cms/strapi-v5-backend/src/` (production-ready, operational)            |
| Authentication flow      | `src/cofounder_agent/routes/auth_routes.py`, `middleware/auth.py`       |
| Audit logging            | `src/cofounder_agent/middleware/audit_logging.py` (type-safe, 0 errors) |
| Database models          | `src/cofounder_agent/models.py`, `database.py`                          |
| Tests                    | `src/cofounder_agent/tests/`, `**/__tests__/` (Jest)                    |
| NPM workspace configs    | Root `package.json` (`workspaces` array)                                |

## 🤖 For AI Agent Code Generation

### DO:

- ✅ Follow existing async/await patterns in Python backend
- ✅ Use Zustand selectors for React state (not Context)
- ✅ Centralize API calls in `lib/api.js` (Next.js) or route modules (FastAPI)
- ✅ Write tests alongside code (Jest for JS, pytest for Python)
- ✅ Use conventional commits: `feat:`, `fix:`, `refactor:`, etc.
- ✅ Add type hints to Python functions (all 20 previous errors now fixed)
- ✅ Check existing code for patterns before generating new implementations
- ✅ Reference `docs/04-DEVELOPMENT_WORKFLOW.md` for git workflow

### DON'T:

- ❌ Import from sibling workspaces directly (use published APIs/REST)
- ❌ Create ANY documentation in the root folder EXCEPT for a single README.md and LICENSE.md
- ❌ Hardcode API endpoints (use environment variables from `.env`)
- ❌ Prop-drill state in React (use Zustand or URL params)
- ❌ Mix async/sync in Python orchestrator (everything must be async)
- ❌ Ignore type hints or leave Python functions untyped
- ❌ Commit secrets, API keys, or unencrypted sensitive data
- ❌ Modify Strapi plugins without extensive testing (known issues)
- ❌ Write documentation that becomes stale (keep HIGH-LEVEL ONLY)

---

## 🏗️ Architecture Highlights

### Frontend Architecture

- **Next.js 15** with TypeScript
- **Tailwind CSS** for styling
- **Server Components** for performance
- **API Routes** for backend integration
- Deployed to **Vercel** (automatic deployments)

### Backend Architecture

- **FastAPI** for REST API
- **Strapi v5** for headless CMS
- **PostgreSQL** for data persistence
- **MCP Integration** for AI orchestration
- Deployed to **Railway** (Docker-based)

### AI/Agent Architecture

- **Multi-Agent System** via MCP (Model Context Protocol)
- **Specialized Agents:**
  - Content Agent - SEO-optimized content generation
  - Financial Agent - Business metrics and projections
  - Compliance Agent - Regulatory and legal checks
  - Market Insight Agent - Market analysis
- **Co-Founder Agent** - Main orchestrator combining all agents
- **Memory System** - Persistent context and learning
- **Notification System** - Real-time alerts and updates

### Database Schema

- **Strapi Collections:** Content, Media, Users, Roles, Permissions
- **Custom Tables:** Business metrics, agent memory, transaction logs
- **Cache Layer:** Redis for session management
- See [Reference Documentation](../docs/reference/README.md)

---

## 🚀 Deployment Guide

### Prerequisites for Deployment

- ✅ Node.js 22.x (not 25.x)
- ✅ Python 3.12
- ✅ Railway account (for backend)
- ✅ Vercel account (for frontends)
- ✅ GCP account (for cloud functions - optional)

### Deployment Checklist

**1. Environment Setup**

- Create `.env.production` with all secrets
- Set up Railway environment variables
- Configure Vercel environment variables
- Verify all services can communicate

**2. Backend Deployment (Railway)**

```bash
cd cms/strapi-main
# Deploy via Railway CLI or web dashboard
railway up
```

**3. Frontend Deployment (Vercel)**

```bash
# oversight-hub
cd web/oversight-hub
vercel --prod

# public-site
cd web/public-site
vercel --prod
```

**4. Verification**

- ✅ Strapi API responding at backend URL
- ✅ Oversight Hub accessible and authenticated
- ✅ Public Site loads all pages
- ✅ Agents communicating via MCP
- ✅ Database migrations completed

**See:** [Deployment Guide](../docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md)

---

## 🧪 Testing Requirements

### Test Coverage Goals

- **Unit Tests:** >80% coverage
- **Integration Tests:** All API endpoints
- **E2E Tests:** Critical user flows
- **Agent Tests:** MCP communication and responses

### Running Tests

```bash
# All tests
npm run test

# Watch mode
npm run test:watch

# Coverage report
npm run test:coverage

# Specific test file
npm run test -- [filename]
```

### Test Locations

- `web/oversight-hub/__tests__/`
- `web/public-site/__tests__/`
- `cms/strapi-main/tests/` (if applicable)
- `src/cofounder_agent/tests/`

---

## 📝 Documentation Standards

### Markdown Guidelines

- ✅ Use ATX-style headings (`#`, `##`, `###`)
- ✅ Include language specification in code blocks
- ✅ Maintain proper list formatting
- ✅ Use descriptive link text (not "click here")
- ✅ Include "Last Updated" date in docs
- ❌ Avoid bare URLs (wrap in links)

### File Organization
=======
## Project Structure (What Goes Where)
>>>>>>> feat/refine

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

<<<<<<< HEAD
- [ ] Read this file
- [ ] Clone repository: `git clone <repo-url>`
- [ ] Run setup: `npm run setup:all`
- [ ] Start services: `npm run start:all`
- [ ] Access endpoints:
  - Strapi Admin: `http://localhost:1337/admin`
  - Oversight Hub: `http://localhost:3001` (or next available)
  - Public Site: `http://localhost:3000` (or next available)
  - FastAPI Docs: `http://localhost:8000/docs`
- [ ] Run tests: `npm run test`
- [ ] Create feature branch and start coding!

---

## 🔗 Quick Links

| Resource          | Link                                                                                    |
| ----------------- | --------------------------------------------------------------------------------------- |
| **Main Docs Hub** | [docs/00-README.md](../docs/00-README.md)                                               |
| **Setup Guide**   | [docs/01-SETUP_AND_OVERVIEW.md](../docs/01-SETUP_AND_OVERVIEW.md)                       |
| **Architecture**  | [docs/02-ARCHITECTURE_AND_DESIGN.md](../docs/02-ARCHITECTURE_AND_DESIGN.md)             |
| **Components**    | [docs/components/](../docs/components/)                                                 |
| **Deployment**    | [docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md](../docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md) |

---

## � Documentation Policy: HIGH-LEVEL ONLY ⚠️ **CRITICAL**

### Philosophy

Glad Labs maintains a **HIGH-LEVEL ONLY** documentation approach to reduce maintenance burden and prevent documentation staleness as the codebase evolves.

**The Rule:** Document what is stable and architectural; let code document the rest.

### What To Document

**✅ CREATE & MAINTAIN:**

- Core docs (00-07): Architecture-level guidance that survives code changes
- System design: How components fit together
- Deployment procedures: Infrastructure and deployment strategy
- Operations: Monitoring, backups, maintenance
- API contracts: Interface definitions
- Database schemas: Data models
- Standards: Code quality and naming conventions

**❌ DO NOT CREATE:**

- How-to guides for features (code shows how; guides become stale)
- Status updates or session notes (version control is history)
- Project audit files (temporal, not useful long-term)
- Duplicate documentation (consolidate into core docs)
- Step-by-step tutorials (maintenance nightmare)
- Outdated historical guides (archive or delete)

### Documentation Structure

```
docs/
├── 00-README.md ✅ Main hub
├── 01-SETUP_AND_OVERVIEW.md ✅ Getting started
├── 02-ARCHITECTURE_AND_DESIGN.md ✅ System architecture
├── 03-DEPLOYMENT_AND_INFRASTRUCTURE.md ✅ Production deployment
├── 04-DEVELOPMENT_WORKFLOW.md ✅ Git workflow & testing
├── 05-AI_AGENTS_AND_INTEGRATION.md ✅ Agent architecture
├── 06-OPERATIONS_AND_MAINTENANCE.md ✅ Operations procedures
├── 07-BRANCH_SPECIFIC_VARIABLES.md ✅ Environment config
├── components/ # Minimal, linked to core docs
├── reference/ # Technical specs only (no guides)
└── troubleshooting/ # Focused solutions only
```

### When Asked To Document

**Ask yourself in this order:**

1. **Is this architecture-level?** (Will it still be true in 6 months?)
   - If yes → Add to appropriate core doc (00-07)
   - If no → Ask #2

2. **Is this a how-to guide?** (Step-by-step feature usage?)
   - If yes → Don't create; code demonstrates the feature
   - If no → Ask #3

3. **Does this duplicate existing docs?**
   - If yes → Consolidate into existing doc instead
   - If no → Ask #4

4. **Is this a focused, reusable reference?** (Schema, API spec, standard?)
   - If yes → Add to reference/ folder
   - If no → Don't create

### Maintenance Guidelines

- **Core docs (00-07):** Update when architecture changes (quarterly reviews)
- **Reference docs:** Update when API/schema changes (as needed)
- **Troubleshooting:** Add focused solutions; delete when issue is fixed
- **Everything else:** Archive or delete after 30 days if unused

### Metrics

Track these to keep documentation healthy:

- **Total files in docs/:** <20 (currently ~14) ✅
- **Core docs (00-07):** Always 8 files ✅
- **Maintenance burden:** ~1 hour/quarter for core docs ✅
- **Documentation debt:** 0 stale/outdated files 🎯

---

## �📋 Document Control

| Field            | Value                                          |
| ---------------- | ---------------------------------------------- | ---------------- |
| **Version**      | 3.0                                            |
| **Last Updated** | November 5, 2025 (High-Level Only Policy)      |
| **Next Review**  | February 5, 2026 (quarterly)                   |
| **Author**       | GitHub Copilot & Glad Labs Team                |
| **Status**       | Active & Maintained                            | Production Ready |
| **Audience**     | All team members (developers, DevOps, QA, PMs) |

---

**🚀 Ready to code? Start with the [Setup Guide](../docs/01-SETUP_AND_OVERVIEW.md)!**
=======
1. **API-First Design:** Everything exposed via REST, no direct database access from frontend
2. **Async-Everywhere:** Python uses FastAPI + async/await, don't block event loops
3. **Model Router First:** Never hardcode model names - use cost tiers or automatic fallback
4. **PostgreSQL as Source of Truth:** All state (tasks, results, memories) persisted there
5. **Monorepo with Workspace:** All services installed with single `npm install`, managed together
6. **Self-Critiquing Quality:** Content agents critique each other's work, not manual review
7. **Cost Optimization:** Ollama → cheap APIs → premium APIs (intelligent fallback)
>>>>>>> feat/refine
