# Glad Labs Copilot Instructions

**Last Updated:** March 5, 2026  
**Project:** Glad Labs AI Co-Founder System  
**Version:** 2.2 (Phase 1C Complete: Error Handling Standardization, Phase 4-5 Complete: Workflows, Image Generation, Capability System)

## Project Overview

Glad Labs is a **production-ready AI orchestration system** combining autonomous agents, multi-provider LLM routing, and full-stack web applications. It's a monorepo with three main services:

- **Backend:** Python FastAPI server (port 8000) orchestrating specialized AI agents ("Poindexter")
- **Admin UI:** React 18 + Material-UI dashboard (port 3001) for monitoring and control
- **Public Site:** Next.js 15 website (port 3000) for content distribution

**Key Architecture:** Multi-agent system with comprehensive task orchestration, PostgreSQL persistence, intelligent model router with Ollama/OpenAI/Anthropic/Google support, unified REST API (29 route modules), workflow execution engine, capability-based task composition, and real-time WebSocket progress tracking.

---

## Critical Knowledge for Productivity

### 1. Service Architecture & Startup

**Three-service startup pattern** (all async, all required for full system):

```bash
# From repo root - PRIMARY COMMAND for dev environment (starts backend + both frontends):
npm run dev

# This uses concurrently to run:
# - npm run dev:cofounder  → Python FastAPI with uvicorn (port 8000) @ src/cofounder_agent/
# - npm run dev:frontend   → Both frontend apps
#   - npm run dev:public     → Next.js dev server (port 3000) @ web/public-site/
#   - npm run dev:oversight  → React dev server (port 3001) @ web/oversight-hub/

# Alternative: run services individually
npm run dev:cofounder     # Just backend
npm run dev:backend       # Alias for dev:cofounder
npm run dev:frontend      # Both React apps (public + oversight)
npm run dev:public        # Just Next.js
npm run dev:oversight     # Just React admin
npm run dev:all           # Explicitly run all three services
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
<<<<<<< HEAD
├── main.py                        # FastAPI initialization, CORS, middleware setup
├── routes/                        # 29 route modules (core + workflows + capabilities + analytics)
│   ├── agents_routes.py           # Agent management and retrieval
│   ├── agent_registry_routes.py   # Agent discovery & metadata (NEW)
│   ├── analytics_routes.py        # KPI tracking and system analytics
│   ├── approval_routes.py         # Human approval workflow queue
│   ├── auth_unified.py            # OAuth2 and unified authentication
│   ├── bulk_task_routes.py        # Batch task creation and processing
│   ├── capability_tasks_routes.py # Capability-based task composition (NEW)
│   ├── chat_routes.py             # Real-time chat with agents
│   ├── cms_routes.py              # Content management system integration
│   ├── command_queue_routes.py    # Task queueing and execution
│   ├── custom_workflows_routes.py # User-defined workflow builder (NEW)
│   ├── media_routes.py            # Asset and image management
│   ├── metrics_routes.py          # Performance and quality metrics
│   ├── model_routes.py            # LLM model selection and health
│   ├── newsletter_routes.py       # Newsletter distribution system
│   ├── ollama_routes.py           # Local Ollama model integration
│   ├── privacy_routes.py          # Privacy and GDPR endpoints
│   ├── profiling_routes.py        # Performance profiling (NEW)
│   ├── revalidate_routes.py       # ISR revalidation for Next.js (NEW)
│   ├── service_registry_routes.py # Service discovery (NEW)
│   ├── settings_routes.py         # System and user settings
│   ├── social_routes.py           # Social media integration
│   ├── task_routes.py             # Task CRUD and execution
│   ├── webhooks.py                # External webhook handlers
│   ├── websocket_routes.py        # Real-time WebSocket connections
│   ├── workflow_history.py        # Workflow execution history
│   ├── workflow_progress_routes.py# Real-time workflow progress (NEW)
│   ├── workflow_routes.py         # Workflow orchestration & execution (NEW)
│   └── writing_style_routes.py    # Writing style sample management
├── services/                      # 87 service modules (core + workflow engine + ML infrastructure)
│   ├── database_service.py        # Coordinator for 5 DB modules + connection pooling
│   ├── model_router.py            # Cost-optimized LLM routing with automatic fallback
│   ├── task_executor.py           # Task execution orchestration with event emission
│   ├── unified_orchestrator.py    # Master agent choreography and task distribution
│   ├── workflow_executor.py       # Phase-based workflow execution engine (NEW)
│   ├── workflow_engine.py         # Workflow state management and persistence (NEW)
│   ├── phase_registry.py          # Phase definitions and registration (NEW)
│   ├── phase_mapper.py            # Semantic phase input/output mapping (NEW)
│   ├── workflow_validator.py      # Workflow compatibility validation (NEW)
│   ├── capability_registry.py     # Service capability introspection (NEW)
│   ├── task_planning_service.py   # Intent-based task planning (NEW)
│   ├── content_router_service.py  # Intelligent content routing (NEW)
│   └── [73+ specialized]          # OAuth, caching, webhooks, ML, analytics, etc.
├── agents/                        # 4 core agent types with specialized sub-agents
│   ├── content_agent/             # 7 specialized agents + orchestrator (research, creative, QA, image, publishing)
│   ├── financial_agent/           # Cost/ROI tracking and analysis
│   ├── market_insight_agent/      # Trend analysis and market research
│   └── compliance_agent/          # Legal/risk review and compliance checks
├── models/                        # Pydantic request/response schemas
├── tasks/                         # Task execution logic and models
├── middleware/                    # Auth, logging, error handling, CORS
├── tests/                         # Pytest suite (~200+ tests)
└── config/                        # Configuration modules and environment loading
=======
├── main.py                    # FastAPI initialization, CORS, middleware setup
├── routes/                    # 27 route modules
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
├── services/                  # 60+ service modules (61 files)
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
├── tests/                     # Test configuration and fixtures
└── config/                    # Configuration modules
>>>>>>> origin/main
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
- **Documentation hygiene:** PRs with significant architectural changes must update affected `docs/`, remove stale references, audit moved or renamed internal links, and note any deferred larger doc update

**Testing:**

```bash
# Python backend (runs integration + e2e tests from tests/ root directory)
npm run test:python          # Full test suite (integration + e2e)
npm run test:python:integration  # Integration tests only
npm run test:python:e2e      # End-to-end tests only
npm run test:python:smoke    # Fast smoke tests

# Frontend
npm run test                 # Runs Jest for all workspaces

# Format check
npm run format:check
```

### 5. Content Generation Pipeline (Self-Critiquing with 7 Specialized Agents)

**7-Stage Agent Flow** for content creation (located in `src/cofounder_agent/agents/content_agent/`):

The content pipeline orchestrates 7 specialized sub-agents that work together to produce publication-ready content:

1. **Research Agent** (`research_agent.py`) - Gathers background research, identifies key points and sources
2. **Creative Agent** (`creative_agent.py`) - Generates initial draft with brand voice and messaging
3. **QA Agent** (`qa_agent.py`) - Critiques quality, structure, and messaging WITHOUT rewriting content
4. **Creative Agent (Refined)** (`creative_agent.py` - second pass) - Incorporates QA feedback and improves draft
5. **Image Agent** (`image_agent.py`) - Selects/generates visuals, alt text, metadata with Pexels API
6. **Publishing Agent** (`publishing_agent.py`) - Formats for CMS, adds SEO metadata, converts to markdown
7. **Postgres Publishing Agent** (`postgres_publishing_agent.py`) - Stores in PostgreSQL, handles publishing workflow

**Key Architectural Patterns:**

- **Agents critique without rewriting:** QA provides structured feedback; Creative agent applies improvements
- **Orchestrator pattern:** `orchestrator.py` coordinates sub-agents via `unified_orchestrator.py` with task executor
- **Image generation:** Integrated with Pexels API (free tier) and DALL-E/Midjourney for custom images
- **Publishing queue:** Human approval queue (approval_routes.py) for final review before publication
- **Event emission:** Real-time WebSocket updates track progress through all 7 stages

**Execution Flow:**

```
Research → Creative Draft → QA Critique → Creative Refinement → Image Selection → Publishing Prep → DB Storage
     ↓           ↓              ↓              ↓                 ↓                 ↓               ↓
   event       event          event          event             event            event           event
```

**Real-time Progress:** The workflow executor emits events after each agent stage, enabling real-time UI updates via WebSocket (/api/workflow-progress/{id}).

**Key Pattern:** Agents **critique without rewriting.** QA provides feedback, Creative agent applies it. This loop repeats if needed until quality threshold is met.

### 6. Multi-Provider AI Integration (MCP)

**Model Context Protocol** (`src/mcp/`) provides standardized tool access and cost optimization.

**Cost Tiers (in MCPContentOrchestrator):**

- `ultra_cheap`: Ollama (local)
- `cheap`: Gemini (low API cost)
- `balanced`: Claude 3.5 Sonnet / GPT-4 Turbo
- `premium`: Claude 3 Opus
- `ultra_premium`: Multi-model ensemble

**Pattern:** Don't hardcode model names. Use cost tier selection - system automatically picks best model available for the tier.

### 7. Workflow Execution System (Phase 4)

**New Workflow Endpoints** enable template-based and custom workflow orchestration:

```
POST   /api/workflows/execute/{template_name}       Execute predefined workflow
GET    /api/workflows/{id}                          Get workflow status & results
GET    /api/workflow/templates                      List available templates
POST   /api/custom-workflows                        Create custom workflow
GET    /api/workflow-progress/{id}                  Real-time progress (WebSocket)
```

**Built-in Templates:**

- `social_media` - Social post generation and scheduling
- `email` - Email campaign creation
- `blog_post` - Blog post generation
- `newsletter` - Newsletter creation and distribution
- `market_analysis` - Market research and reporting

**Phase-Based Architecture:**

- Workflows consist of sequential phases, each with input/output contracts
- Real-time WebSocket events emit after each phase completion
- Automatic input mapping between phase outputs and next phase inputs
- User can override phase inputs or skip phases
- Semantic validation ensures workflow compatibility

**Real-time Tracking:**

- WebSocket endpoint `/api/workflow-progress/{workflow_id}` streams events
- Events include phase completion, status changes, and quality metrics
- Oversight Hub UI listens and displays real-time progress

**Reference:** See [workflow_routes.py](src/cofounder_agent/routes/workflow_routes.py) and [workflow_executor.py](src/cofounder_agent/services/workflow_executor.py)

### 8. Capability-Based Task System (Sprint 5)

**New capability infrastructure** enables intent-based task routing and dynamic composition:

```
POST   /api/capability-tasks                        Create task from intent/capability
POST   /api/agents/introspect                       Discover agent capabilities
GET    /api/service-registry                        List available services
```

**How It Works:**

1. **Intent Parsing:** Natural language task request is analyzed for required capabilities
2. **Service Registry:** Available agents/services are queried for matching capabilities
3. **Task Planning:** Best agent combination is selected and composed
4. **Automatic Routing:** Task is routed to appropriate agent without manual assignment

**Capability Examples:**

- `image_generation` - Generate, edit, or enhance images
- `research` - Find and synthesize information
- `content_writing` - Create marketing copy, blog posts, newsletters
- `quality_evaluation` - Critique and suggest improvements
- `publishing` - Post to web, social media, email

**Reference:** See [capability_tasks_routes.py](src/cofounder_agent/routes/capability_tasks_routes.py) and [capability_registry.py](src/cofounder_agent/services/capability_registry.py)

### 9. Key Files Reference

| Purpose             | Path                                                   | What It Does                                                 |
| ------------------- | ------------------------------------------------------ | ------------------------------------------------------------ |
| FastAPI entry       | `src/cofounder_agent/main.py`                          | Route registration, middleware setup, app initialization     |
| Agent orchestration | `src/cofounder_agent/services/unified_orchestrator.py` | Coordinates agent fleet, task distribution                   |
| Database service    | `src/cofounder_agent/services/database_service.py`     | PostgreSQL queries, ORM models, persistence                  |
| Model routing       | `src/cofounder_agent/services/model_router.py`         | LLM provider selection with fallback chain                   |
| Content agent       | `src/cofounder_agent/agents/content_agent/`            | 7-stage self-critiquing pipeline with sub-agents             |
| Workflow executor   | `src/cofounder_agent/services/workflow_executor.py`    | Phase-based workflow execution and event emission            |
| Capability system   | `src/cofounder_agent/services/capability_registry.py`  | Agent capability introspection and dynamic routing           |
| Tasks               | `src/cofounder_agent/tasks/`                           | Task models, execution logic, status tracking                |
| Routes              | `src/cofounder_agent/routes/`                          | 29 REST endpoints for tasks, agents, workflows, capabilities |
| Oversight Hub       | `web/oversight-hub/`                                   | React dashboard, agent monitoring, task management           |
| Public Site         | `web/public-site/`                                     | Next.js content distribution, SEO optimization               |

### 10. Common Developer Patterns

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
<<<<<<< HEAD
│   │   ├── routes/         # 29 REST endpoint modules
│   │   ├── services/       # 87 service modules (model_router, database_service, task_executor)
│   │   ├── models/         # Pydantic schemas for requests/responses
│   │   ├── tasks/          # Task execution and scheduling
│   │   ├── middleware/     # Auth, logging, error handling
│   │   └── tests/          # pytest suite (~200+ tests)
│   ├── agents/             # Specialized agent implementations
│   │   ├── content_agent/  # 7-stage self-critiquing content pipeline with sub-agents
=======
│   │   ├── routes/         # 27 REST endpoint modules
│   │   ├── services/       # 60+ service modules (model_router, database_service, task_executor)
│   │   ├── models/         # Pydantic schemas for requests/responses
│   │   ├── tasks/          # Task execution and scheduling
│   │   ├── middleware/     # Auth, logging, error handling
│   │   └── agents/         # Agent implementations used by orchestrator
│   ├── agents/             # Specialized agent types
│   │   ├── content_agent/  # 6-stage self-critiquing content pipeline
>>>>>>> origin/main
│   │   ├── financial_agent/
│   │   ├── market_insight_agent/
│   │   └── compliance_agent/
│   ├── mcp/                # Model Context Protocol integration
│   ├── mcp_server/         # MCP server implementations
│   └── services/           # Shared service modules
├── tests/
│   ├── integration/        # Integration tests for backend services
│   └── e2e/               # End-to-end workflow tests
└── web/
    ├── public-site/        # **Content distribution** (Next.js 15, port 3000)
    └── oversight-hub/      # **Control center** (React 18 + Material-UI, port 3001)
```

---

## Environment Variables (Complete Reference)

All services read from `.env.local` at project root (single source of truth). See `.env.example` for full list of 52+ environment variables.

### Critical Variables (Minimum Required)

```env
# Database (required for all persistence)
DATABASE_URL=postgresql://user:password@localhost:5432/glad_labs

# LLM API Keys (at least ONE required - automatic fallback chain applies)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza-...

# Ollama (if using local models, no API key needed)
OLLAMA_BASE_URL=http://localhost:11434
```

### Database Configuration

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/glad_labs
DATABASE_POOL_MIN_SIZE=5             # Min connection pool size
DATABASE_POOL_MAX_SIZE=20            # Max connection pool size
```

### LLM Provider Configuration

```env
# API Keys
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza-...

# Optional: Force specific provider (fallback still applies)
LLM_PROVIDER=claude                  # Options: claude, gpt, gemini
DEFAULT_MODEL_TEMPERATURE=0.7
DEFAULT_MODEL_MAX_TOKENS=2000

# Ollama for local models
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=mistral               # Default local model
```

### Workflow System (Phase 4)

```env
ENABLE_WORKFLOW_BUILDER=true         # Enable custom workflow UI
ENABLE_WORKFLOW_EXECUTION=true       # Enable /api/workflows/execute endpoints
WORKFLOW_AUTO_RETRY=true             # Auto-retry failed phases
WORKFLOW_TIMEOUT_MINUTES=60          # Max workflow execution time
```

### Image Generation (Sprint 4)

```env
PEXELS_API_KEY=...                  # Free stock photos API
ENABLE_DALL_E=false                 # OpenAI image generation (requires API key)
IMAGE_GENERATION_PROVIDER=pexels    # Options: pexels, dalle, midjourney
```

### Capability System (Sprint 5)

```env
ENABLE_CAPABILITY_SYSTEM=true        # Enable capability-based task routing
ENABLE_SERVICE_REGISTRY=true         # Enable service discovery
TASK_ROUTING_STRATEGY=semantic       # Options: semantic, keyword, hybrid
```

### Analytics & Observability

```env
ENABLE_ANALYTICS=true                # Track KPIs and metrics
ENABLE_ERROR_REPORTING=true          # Send errors to monitoring
ENABLE_TRACING=false                 # Performance trace collection
SENTRY_DSN=                          # Error tracking (production)
LOG_LEVEL=info                       # Options: debug, info, warning, error
SQL_DEBUG=false                      # Log all SQL queries
```

### Feature Flags

```env
ENABLE_MEMORY_SYSTEM=true            # Persistent memory across sessions
ENABLE_MCP_SERVER=true               # Model Context Protocol server
ENABLE_DEBUG_LOGS=false              # Verbose debug logging
ENABLE_WEBHOOKS=true                 # External webhook handlers
ENABLE_APPROVAL_QUEUE=true           # Human approval workflow
ENABLE_QUALITY_SCORING=true          # Automatic quality evaluation
```

### API Rate Limiting & Timeouts

```env
RATE_LIMIT_PER_MINUTE=100           # Requests per minute per client
API_TIMEOUT=30                      # Default request timeout (seconds)
API_RETRY_ATTEMPTS=3                # Failed request retries
API_RETRY_BACKOFF=1000              # Backoff between retries (ms)
```

### Authentication

```env
# OAuth (GitHub)
GH_OAUTH_CLIENT_ID=...
GH_OAUTH_CLIENT_SECRET=...
OAUTH_CALLBACK_URL=http://localhost:8000/callback

# JWT (session tokens)
JWT_SECRET=your-secret-key-min-32-chars
JWT_EXPIRY_HOURS=24
```

### Optional Services

```env
STRAPI_API_URL=http://localhost:1337    # Strapi CMS (optional)
NEWSLETTER_PROVIDER=mailchimp          # Email provider
SOCIAL_MEDIA_PROVIDER=twitter          # Social posting
```

### Content Management

```env
WRITING_STYLE_ENABLED=true           # Writing style RAG mode
WRITING_STYLE_ANALYSIS_TYPE=regex    # Options: regex, llm, hybrid
DEFAULT_WRITING_STYLE_ID=...         # Default style for content
```

**Important:** Python and Node both read from same `.env.local`. Environment profiles exist (`.env.local`, `.env.staging`, `.env.production`) for different deployment stages.

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

6. **Workflow execution failing?**
   - Test workflow endpoint: `curl -X POST http://localhost:8000/api/workflows/execute/blog_post -H "Content-Type: application/json" -d '{}'`
   - Check workflow templates available: `curl http://localhost:8000/api/workflow/templates`
   - Monitor real-time progress: Connect WebSocket to `ws://localhost:8000/api/workflow-progress/{workflow_id}`
   - Verify phase registry is initialized in workflow_executor.py startup
   - Check `ENABLE_WORKFLOW_EXECUTION=true` in `.env.local`

---

## When to Reference Full Documentation

- **Architecture questions:** → `docs/02-Architecture/System-Design.md`
- **Deployment questions:** → `docs/05-Operations/Operations-Maintenance.md`
- **CI/CD and branching:** → `docs/04-Development/Development-Workflow.md`
- **Agent capabilities:** → `docs/02-Architecture/Multi-Agent-Pipeline.md`
- **Operations/monitoring:** → `docs/05-Operations/Monitoring-Diagnostics.md`
- **Recent implementations:** → `VERSION_HISTORY.md` (all phases and implementation milestones)
- **Troubleshooting:** → `docs/troubleshooting/` folder

---

## Latest Completions

### Phase 1C: Error Handling Standardization (Issue #6) ✅ COMPLETE

**Completion Date:** March 5, 2026  
**Impact:** All 312 exception handlers across 68 service files now standardized

- **Pattern:** All exceptions use `logger.error(f"[operation_name] message", exc_info=True)`
- **Coverage:** 100% (312/312 exceptions standardized)
- **Batches:** 15 systematic batches over ~24 hours
- **Validation:** Zero unstandardized handlers remaining (verified via automated script)
- **Files:** All 68 service files in `src/cofounder_agent/services/`

**Benefits:**

- Consistent error handling and logging across entire backend
- Stack traces captured for all exceptions
- Operation context preserved in all error logs
- Improved debugging and troubleshooting capabilities
- No regressions, all code compiles successfully

**Reference:** `docs/07-Appendices/Technical-Debt-Tracker.md` Issue #6 section for detailed progress

---

## Key Principles

1. **API-First Design:** Everything exposed via REST, no direct database access from frontend
2. **Async-Everywhere:** Python uses FastAPI + async/await, don't block event loops
3. **Model Router First:** Never hardcode model names - use cost tiers or automatic fallback
4. **PostgreSQL as Source of Truth:** All state (tasks, results, memories) persisted there
5. **Monorepo with Workspace:** All services installed with single `npm install`, managed together
6. **Self-Critiquing Quality:** Content agents critique each other's work, not manual review
7. **Cost Optimization:** Ollama → cheap APIs → premium APIs (intelligent fallback)
