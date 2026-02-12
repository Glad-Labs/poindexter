# Comprehensive Codebase Analysis: Glad Labs AI Orchestration System

**Analysis Date:** February 11, 2026  
**Project:** Glad Labs AI Co-Founder System (Production-Ready)  
**Version:** 3.0.1  
**Status:** ✅ ACTIVE & MAINTAINED

---

## 1. CODEBASE STRUCTURE & ORGANIZATION

### 1.1 Root Directory Overview

```
glad-labs-website/ (Monorepo)
├── src/                           # Python backend (FastAPI + Agents)
│   ├── cofounder_agent/          # Main orchestrator service (228 .py files)
│   ├── mcp_server/               # Model Context Protocol servers
│   └── services/                 # Shared service modules
├── web/                           # Full-stack frontend
│   ├── public-site/              # Next.js 15 website (port 3000)
│   └── oversight-hub/            # React 18 admin UI (port 3001)
├── tests/                         # Comprehensive test suite (~30+ test files)
├── docs/                          # System documentation (7 core docs)
├── scripts/                       # Utility & automation scripts (50+)
├── package.json                  # Monorepo configuration (workspaces)
└── pyproject.toml               # Python environment (Poetry + Pytest)
```

### 1.2 Python Backend Structure (src/cofounder_agent/)

```
src/cofounder_agent/
├── main.py                    # FastAPI application entry point (426 lines)
├── agents/                    # Agent discovery and registry
│   ├── registry.py            # Central agent discovery (245 lines)
│   ├── content_agent/         # Still available for backward compatibility
│   ├── financial_agent/
│   ├── compliance_agent/
│   └── market_insight_agent/
├── routes/                    # API route modules (23 files)
│   ├── task_routes.py         # Task CRUD & execution
│   ├── agents_routes.py       # Agent management
│   ├── agent_registry_routes.py (NEW - Phase 2)
│   ├── service_registry_routes.py (NEW - Phase 2)
│   ├── model_routes.py        # LLM model selection
│   ├── chat_routes.py         # Real-time chat
│   ├── workflow_routes.py     # Workflow execution (NEW - Phase 3)
│   ├── workflow_history.py    # Workflow tracking
│   ├── cms_routes.py          # CMS integration (Strapi)
│   ├── media_routes.py        # Asset management
│   ├── websocket_routes.py    # Real-time WebSocket
│   ├── webhooks.py            # External integrations
│   └── [50+ endpoints total]
├── services/                  # Service modules (60+ files)
│   ├── DATABASE LAYER (5 modular databases):
│   │   ├── database_service.py     # Coordinator (asyncpg-based)
│   │   ├── users_db.py             # User accounts, OAuth, auth
│   │   ├── tasks_db.py             # Task CRUD, filtering, status
│   │   ├── content_db.py           # Posts, quality scores, publishing
│   │   ├── admin_db.py             # Logs, financial tracking, health
│   │   └── writing_style_db.py    # Writing samples, RAG queries
│   │
│   ├── UNIFIED SERVICES (NEW - Phase 4, 1,300+ lines):
│   │   ├── content_service.py      # 6-phase content pipeline (380 lines)
│   │   ├── financial_service.py    # Cost tracking, ROI analysis (160 lines)
│   │   ├── compliance_service.py   # Legal review, risk assessment (200 lines)
│   │   └── market_service.py       # Trend analysis, opportunities (220 lines)
│   │
│   ├── ORCHESTRATION & ROUTING:
│   │   ├── unified_orchestrator.py # Master AI orchestrator (1,146 lines)
│   │   ├── model_router.py         # Cost-optimized LLM fallback
│   │   ├── task_executor.py        # Task execution & scheduling
│   │   ├── workflow_engine.py      # Phase execution with retry (NEW - Phase 3)
│   │   └── workflow_composition.py # Dynamic workflow building (NEW - Phase 3)
│   │
│   ├── AI SERVICES:
│   │   ├── ai_content_generator.py # LLM-based content generation
│   │   ├── ai_cache.py             # Prompt/response caching
│   │   ├── nlp_intent_recognizer.py # Natural language routing
│   │   ├── content_critique_loop.py # Self-critiquing pipeline
│   │   └── quality_service.py      # Quality evaluation framework
│   │
│   ├── EXTERNAL INTEGRATIONS:
│   │   ├── oauth_manager.py        # OAuth (GitHub, Google, Facebook, LinkedIn, Microsoft)
│   │   ├── email_publisher.py      # Email distribution
│   │   ├── twitter_publisher.py    # Twitter/X publishing
│   │   ├── linkedin_publisher.py   # LinkedIn integration
│   │   ├── facebook_oauth.py       # Facebook auth & publishing
│   │   ├── serper_client.py        # Google search API
│   │   ├── pexels_client.py        # Stock image sourcing
│   │   ├── webhook_security.py     # Webhook validation
│   │   └── legacy_data_integration.py
│   │
│   ├── AUTHENTICATION & SECURITY:
│   │   ├── auth.py                 # JWT validation, token management
│   │   ├── permissions_service.py  # Role-based access control
│   │   ├── token_validator.py      # Token format validation
│   │   └── sentry_integration.py   # Error tracking
│   │
│   ├── MODEL & INFERENCE:
│   │   ├── ollama_client.py        # Ollama (local models)
│   │   ├── gemini_client.py        # Google Gemini API
│   │   ├── huggingface_client.py   # Hugging Face models
│   │   ├── model_constants.py      # Model definitions
│   │   ├── model_validator.py      # Model validation
│   │   ├── model_consolidation_service.py
│   │   └── fine_tuning_service.py
│   │
│   ├── UTILITY & SUPPORT:
│   │   ├── redis_cache.py          # Response caching
│   │   ├── metrics_service.py      # Usage & performance metrics
│   │   ├── health_service.py       # System health checks
│   │   ├── cost_aggregation_service.py
│   │   ├── seo_content_generator.py
│   │   ├── training_data_service.py
│   │   ├── logger_config.py        # Logging configuration
│   │   └── telemetry.py            # OpenTelemetry integration
│   │
│   └── [20+ additional services for specialized tasks]
├── models/                    # Model definitions
│   └── workflow.py            # Workflow model schemas
├── middleware/                # FastAPI middleware (auth, logging, errors)
├── tasks/                     # Task execution logic
├── config/                    # Configuration modules
├── schemas/                   # Pydantic request/response models (25+ schemas)
├── utils/                     # Utility functions & helpers
│   ├── route_registration.py  # Dynamic route setup
│   ├── startup_manager.py     # Service initialization
│   ├── exception_handlers.py
│   ├── middleware_config.py
│   ├── sql_safety.py          # SQL injection prevention
│   └── [utility modules]
├── migrations/                # Database schema migrations
└── tests/                     # Pytest test suite (~200+ tests)
```

### 1.3 Frontend Structure

#### Oversight Hub (React 18 + Material-UI)

```
web/oversight-hub/
├── src/
│   ├── App.jsx               # Main app container
│   ├── index.js              # React entry point
│   ├── components/           # React components (35+)
│   │   ├── pages/            # Page components
│   │   │   ├── OrchestratorPage.jsx
│   │   │   ├── UnifiedServicesPanel.jsx (NEW - Phase 4)
│   │   │   ├── TrainingDataDashboard.jsx
│   │   │   └── AuthCallback.jsx
│   │   ├── common/           # Reusable components
│   │   ├── modals/           # Modal dialogs
│   │   ├── ErrorBoundary.jsx # Error handling
│   │   ├── LayoutWrapper.jsx # Layout wrapper
│   │   ├── ModelSelectionPanel.jsx
│   │   ├── CostMetricsDashboard.jsx
│   │   ├── LangGraphStreamProgress.jsx
│   │   └── [25+ other components]
│   ├── services/             # API clients
│   │   ├── phase4Client.js   (NEW - Phase 4, 493 lines)
│   │   ├── orchestratorAdapter.js (NEW - Phase 4)
│   │   ├── authService.js
│   │   ├── cofounderAgentClient.js
│   │   └── errorLoggingService.js
│   ├── hooks/                # Custom React hooks
│   ├── context/              # React Context
│   ├── store/                # Zustand state management
│   ├── pages/                # Root pages (5 pages)
│   ├── routes/               # React Router configuration
│   ├── styles/               # CSS modules
│   │   └── UnifiedServicesPanel.css (NEW - 554 lines)
│   └── utils/                # Utility functions
├── package.json              # React deps: React 18.3.1, Material-UI 7.3.6
└── [Jest config, test files]
```

#### Public Site (Next.js 15)

```
web/public-site/
├── app/                      # Next.js app directory
│   ├── page.js              # Homepage
│   ├── layout.js            # Root layout
│   ├── blog/                # Blog routes
│   ├── api/                 # API routes (SSR fetching)
│   └── [dynamic routes]
├── components/              # React components (20+)
├── lib/                     # Next.js utilities
├── styles/                  # TailwindCSS styles
├── public/                  # Static assets
├── package.json             # Next.js 15.5.9, React 18.3.1
├── next.config.js           # Next.js configuration
├── tailwind.config.js       # TailwindCSS configuration
└── [Playwright e2e tests]
```

---

## 2. ARCHITECTURE ANALYSIS

### 2.1 System Architecture Overview (3-Tier)

```
┌─────────────────────────────────────────────────────────────────┐
│ TIER 1: PRESENTATION LAYER                                      │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────────────┬──────────────────────┐                │
│  │  Oversight Hub       │   Public Site        │                │
│  │  (React 18)          │   (Next.js 15)       │                │
│  │  Port 3001           │   Port 3000          │                │
│  └──────────────────────┴──────────────────────┘                │
└─────────────────────┬───────────────────────────────────────────┘
                      │ REST API (HTTP/WebSocket)
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│ TIER 2: APPLICATION LAYER (FastAPI Backend)                    │
├─────────────────────────────────────────────────────────────────┤
│  Port 8000 - src/cofounder_agent/main.py                        │
│                                                                  │
│  REQUEST FLOW:                                                   │
│  1. HTTP Request → FastAPI Router                               │
│  2. auth.py (JWT validation) → Middleware                       │
│  3. Route Handler → Service Call                                │
│  4. Service → Database/External APIs                            │
│  5. Response → JSON → Client                                    │
│                                                                  │
│  ┌─── NATURAL LANGUAGE ROUTING ────────────────────────┐        │
│  │ unified_orchestrator.py (1,146 lines)              │        │
│  │ - Intent Recognition (nlp_intent_recognizer.py)    │        │
│  │ - Request Type Classification                       │        │
│  │ - Agent/Service Selection                           │        │
│  │ - Result Storage (quality_service.py)               │        │
│  └────────────────────────────────────────────────────┘        │
│                                                                  │
│  ┌─── SERVICE LAYER ───────────────────────────────────┐        │
│  │ UNIFIED SERVICES (Phase 4):                        │        │
│  │ - ContentService (6-phase pipeline)                │        │
│  │ - FinancialService (ROI tracking)                  │        │
│  │ - ComplianceService (Legal review)                │        │
│  │ - MarketService (Trend analysis)                  │        │
│  │                                                    │        │
│  │ ORCHESTRATION:                                    │        │
│  │ - WorkflowEngine (phase execution, retry logic)   │        │
│  │ - WorkflowComposition (dynamic building)          │        │
│  │ - TaskExecutor (scheduling & queuing)             │        │
│  │                                                    │        │
│  │ MODEL ROUTING:                                    │        │
│  │ - Ollama (local, zero-cost) → Primary            │        │
│  │ - Anthropic Claude → Fallback 1                   │        │
│  │ - OpenAI GPT → Fallback 2                         │        │
│  │ - Google Gemini → Fallback 3                      │        │
│  │ - Echo/Mock → Final fallback                      │        │
│  │                                                    │        │
│  │ AGENT REGISTRY (agent_registry_routes.py):       │        │
│  │ - /api/agents/registry (list all agents)          │        │
│  │ - /api/agents/{name} (agent details)              │        │
│  │ - /api/agents/{name}/phases (phases available)    │        │
│  │ - /api/agents/{name}/capabilities (actions)       │        │
│  │ - /api/agents/phase/{phase} (find agents by phase)│        │
│  │ - /api/agents/capability/{cap} (by capability)    │        │
│  │ - /api/agents/category/{cat} (by category)        │        │
│  │ - /api/agents/search (full-text search)           │        │
│  │                                                    │        │
│  │ SERVICE REGISTRY (service_registry_routes.py):   │        │
│  │ - /api/services (list all services)               │        │
│  │ - /api/services/{name} (service details)          │        │
│  │ - /api/services/{name}/actions (actions/methods)  │        │
│  │ - /api/services/type/{type} (filter by type)      │        │
│  │ - /api/services/search (search services)          │        │
│  │ - /api/services/health (service health)           │        │
│  └────────────────────────────────────────────────────┘        │
│                                                                  │
│  ┌─── ROUTE MODULES (23 total) ────────────────────────┐        │
│  │ task_routes.py, chat_routes.py, workflow_routes.py│        │
│  │ model_routes.py, cms_routes.py, media_routes.py   │        │
│  │ analytics_routes.py, etc. (18+ more)              │        │
│  └─────────────────────────────────────────────────────┘       │
└─────────────────────┬───────────────────────────────────────────┘
                      │ asyncpg (async SQL)
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│ TIER 3: DATA LAYER (PostgreSQL)                                 │
├─────────────────────────────────────────────────────────────────┤
│ Database: glad_labs (PostgreSQL)                                │
│ Port: 5432                                                      │
│                                                                  │
│ ┌─── 5 MODULAR DATABASES ──────────────────────────┐            │
│ │ UsersDatabase                                     │            │
│ │  - users table (auth, basic info)                │            │
│ │  - oauth_accounts table (OAuth linkage)          │            │
│ │  Methods: get_user_by_email, create_user, etc.   │            │
│ │                                                  │            │
│ │ TasksDatabase                                    │            │
│ │  - tasks table (task crud, status tracking)      │            │
│ │  - task_status_history table (audit trail)       │            │
│ │  Methods: create_task, get_task, update_status   │            │
│ │                                                  │            │
│ │ ContentDatabase                                  │            │
│ │  - posts table (blog content)                    │            │
│ │  - quality_evaluations table (0-100 scores)     │            │
│ │  - categories, tags, authors tables              │            │
│ │  Methods: publish_post, get_quality_score        │            │
│ │                                                  │            │
│ │ AdminDatabase                                    │            │
│ │  - admin_logs table (audit events)               │            │
│ │  - financial_entries table (costs, budget)       │            │
│ │  - settings table (system config)                │            │
│ │  Methods: log_event, track_cost, get_settings    │            │
│ │                                                  │            │
│ │ WritingStyleDatabase                             │            │
│ │  - writing_samples table (RAG training)          │            │
│ │  Methods: add_sample, retrieve_samples           │            │
│ │                                                  │            │
│ └──────────────────────────────────────────────────┘            │
│                                                                  │
│ Tables: users, tasks, posts, categories, tags, authors,         │
│         quality_evaluations, admin_logs, financial_entries,     │
│         writing_samples, oauth_accounts, workflow_history,      │
│         + 5 system tables                          │            │
│                                                                  │
│ Total: ~18 core tables with indices & relationships            │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Phase 4 Architecture: Unified Service Pattern

**Objective:** Move from deeply nested agent folders to flat, composable services

**Before Phase 4 (Nested):**

```
agents/
├── content_agent/
│   ├── agents/
│   │   ├── research_agent.py
│   │   ├── creative_agent.py
│   │   ├── qa_agent.py
│   │   └── image_agent.py
│   └── services/ ← Nested inside
├── financial_agent/
│   ├── agents/financial_agent.py
│   └── services/...
└── ... (2+ more nested levels)
```

**After Phase 4 (Flat & Composable):**

```
services/
├── content_service.py ← Single unified interface
│   ├── execute_research() - Research phase
│   ├── execute_draft() - Creative phase
│   ├── execute_assess() - QA phase
│   ├── execute_refine() - Refinement phase
│   ├── execute_image_selection() - Image phase
│   ├── execute_finalize() - Publishing phase
│   └── execute_full_workflow() - 6-phase pipeline
│
├── financial_service.py ← Single unified interface
│   ├── analyze_content_cost()
│   ├── calculate_roi()
│   ├── forecast_budget()
│   └── get_service_metadata()
│
├── compliance_service.py ← Single unified interface
│   ├── check_legal_compliance()
│   ├── assess_privacy_compliance()
│   ├── risk_assessment()
│   └── get_service_metadata()
│
└── market_service.py ← Single unified interface
    ├── analyze_competitors()
    ├── identify_opportunities()
    ├── analyze_sentiment()
    └── get_service_metadata()
```

**Benefits:**

- ✅ 80% reduction in folder nesting
- ✅ Clearer entry points per service
- ✅ Better composability (pick & choose capabilities)
- ✅ Unified service discovery via registry
- ✅ Flat service layer is easier to navigate

### 2.3 Agent/Service Registry System

**Discovery Mechanism:**

1. **Central Registry** (`agents/registry.py` - 245 lines)
   - Maintains mapping of all agents and their capabilities
   - Gracefully handles missing agents with fallback logic
   - Supports dynamic agent instantiation

2. **Agent Registry Routes** (`routes/agent_registry_routes.py` - 8 endpoints)
   - `GET /api/agents/registry` - Full registry with metadata
   - `GET /api/agents/{name}` - Specific agent details
   - `GET /api/agents/{name}/phases` - Available phases
   - `GET /api/agents/{name}/capabilities` - Available actions
   - `GET /api/agents/phase/{phase}` - Agents by phase
   - `GET /api/agents/capability/{capability}` - Agents by capability
   - `GET /api/agents/category/{category}` - Agents by category
   - `GET /api/agents/search?q=...` - Full-text search

3. **Service Registry Routes** (`routes/service_registry_routes.py` - 6 endpoints)
   - `GET /api/services` - All services with metadata
   - `GET /api/services/{name}` - Service details
   - `GET /api/services/{name}/actions` - Available methods
   - `GET /api/services/type/{type}` - Filter by type
   - `GET /api/services/search?q=...` - Search services
   - `GET /api/services/health` - Health check

4. **UnifiedOrchestrator._get_agent_instance()** (1,146 lines)
   - Dual-path lookup (registry first, then direct import)
   - Graceful fallback when agent not found
   - Provides backward compatibility

### 2.4 Model Router with Multi-Provider Support

**Location:** `services/model_router.py`

**Intelligent Fallback Chain:**

```
1. Primary: Ollama (local, zero-cost, ~20ms latency)
   ├─ If available: Use Ollama exclusively
   └─ If unavailable: Try next provider

2. Fallback 1: Anthropic Claude
   ├─ Claude 3.5 Sonnet (balanced cost/quality)
   ├─ Claude 3 Opus (premium)
   └─ If no API key: Try next

3. Fallback 2: OpenAI
   ├─ GPT-4 Turbo
   ├─ GPT-4o
   └─ If no API key: Try next

4. Fallback 3: Google Gemini
   ├─ Gemini Pro
   ├─ Gemini Pro Vision
   └─ If no API key: Try next

5. Final Fallback: Echo/Mock Response
   └─ Returns mock response for testing
```

**Configuration via .env.local:**

```env
OLLAMA_BASE_URL=http://localhost:11434
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza-...
LLM_PROVIDER=claude  # Optional: force specific provider
DEFAULT_MODEL_TEMPERATURE=0.7
```

**Cost Tiers (in unified services):**

- `ultra_cheap` → Ollama
- `cheap` → Gemini
- `balanced` → Claude 3.5 Sonnet / GPT-4 Turbo
- `premium` → Claude 3 Opus
- `ultra_premium` → Multi-model ensemble

---

## 3. CODE STATISTICS

### 3.1 File Counts

| Component | Files | Type | LOC |
|-----------|-------|------|-----|
| **Backend (src/cofounder_agent/)** | 228 | Python | ~86,471 |
| **Services** | 60+ | Python | ~25,000 |
| **Routes** | 23 | Python | ~8,000 |
| **Tests** | 30+ | Python | ~15,000 |
| **Frontend (web/)** | 67+ | JS/JSX/TS | ~2,500 |
| **Oversight Hub** | 35+ | JSX | ~1,200 |
| **Public Site** | 20+ | JS/JSX | ~800 |
| **TOTAL** | 375+ | Mixed | ~138,000+ |

### 3.2 Backend Module Breakdown

**Services (60+ modules):**

- Database (6 modules): users_db, tasks_db, content_db, admin_db, writing_style_db, database_service
- Unified Services (4 modules): content_service, financial_service, compliance_service, market_service
- Orchestration (3 modules): unified_orchestrator, workflow_engine, workflow_composition
- AI/ML (8 modules): ai_content_generator, quality_service, nlp_intent_recognizer, etc.
- Integration (10 modules): OAuth (5 providers), email, social media, webhooks
- Infrastructure (15+ modules): caching, metrics, health, auth, security, config

**Routes (23 modules):**

1. task_routes.py - Task CRUD & execution
2. agents_routes.py - Agent management
3. agent_registry_routes.py - Agent discovery (NEW)
4. service_registry_routes.py - Service discovery (NEW)
5. model_routes.py - LLM model selection
6. chat_routes.py - Real-time chat
7. workflow_routes.py - Workflow execution (NEW)
8. workflow_history.py - Workflow tracking
9. cms_routes.py - CMS integration
10. media_routes.py - Media/asset handling
11. websocket_routes.py - Real-time WebSocket
12. webhooks.py - External webhooks
13. analytics_routes.py - Usage analytics
14. metrics_routes.py - Performance metrics
15. model_routes.py - Model health
16. command_queue_routes.py - Task queueing
17. bulk_task_routes.py - Batch operations
18. newsletter_routes.py - Email campaigns
19. ollama_routes.py - Ollama direct access
20. settings_routes.py - System settings
21. social_routes.py - Social media
22. writing_style_routes.py - Writing samples
23. privacy_routes.py - Privacy controls

### 3.3 API Endpoint Summary

**Total Endpoints:** 50+

| Category | Endpoints | Key Routes |
|----------|-----------|-----------|
| Agent Discovery | 8 | registry, by-phase, by-capability, search |
| Service Discovery | 6 | list, details, by-type, health, search |
| Task Management | 12 | create, list, update, delete, approve, assign |
| Workflow | 6 | execute, status, history, templates, compose |
| Model/LLM | 8 | list, health, select, cost, stats |
| Chat | 4 | send, history, stream, export |
| CMS/Content | 10 | post CRUD, publish, preview, quality metrics |
| Media | 6 | upload, list, delete, convert |
| Analytics | 8 | costs, performance, usage, trends |
| Settings | 5 | user, system, notifications, export |

### 3.4 React Components

**Oversight Hub (35+ components):**

- Pages: OrchestratorPage, UnifiedServicesPanel, TrainingDataDashboard, AuthCallback, Login
- Core: App, LayoutWrapper, ErrorBoundary, Header
- Dashboard: CostMetricsDashboard, CostBreakdownCards
- Model: ModelSelectionPanel, ModelSelectDropdown
- Workflow: LangGraphStreamProgress
- Messages: OrchestratorCommandMessage, OrchestratorErrorMessage
- Modals: ~10+ modal components
- Common: ~15+ reusable components

**Public Site (20+ components):**

- Layout components (header, footer, navigation)
- Blog components (post list, single post, archive)
- Content components (hero, featured, testimonials)
- CMS components for dynamic content rendering

---

## 4. TECHNOLOGY STACK

### 4.1 Backend Stack

**Runtime & Framework:**

- Python 3.10+ (requirement: >=3.10,<3.14)
- FastAPI (async REST framework)
- Uvicorn (ASGI server)
- asyncpg (async PostgreSQL driver)

**Key Dependencies:**

- `crewai-tools` (^1.7.2) - AI/agent tools
- `opencv-python-headless` (^4.8.0) - Image processing
- `markdown` (^3.10) - Markdown rendering
- `google-generativeai` (^0.8.6) - Google Gemini API
- `google-genai` (>=0.3.0) - New Google SDK
- `email-validator` (>=2.0.0) - Email validation
- `sentry-sdk` (>=1.40.0) - Error tracking
- `opentelemetry-*` (^1.21.0) - Distributed tracing

**Optional ML Dependencies** (in extras):

- `torch` (>=2.0.0) - PyTorch for SDXL
- `diffusers` (>=0.30.0) - Hugging Face diffusers
- `transformers` (>=4.40.0) - Hugging Face models
- `accelerate` (>=0.24.0) - Distributed training

**Testing:**

- `pytest` (>=9.0.2) - Test framework
- `pytest-asyncio` (>=1.3.0) - Async support
- `pytest-cov` (>=7.0.0) - Coverage reporting
- 9 test markers: unit, integration, api, e2e, performance, slow, voice, websocket, smoke

**Code Quality:**

- `black` (code formatting, line-length: 100)
- `isort` (import sorting, black profile)
- `mypy` (type checking, non-strict)
- `pylint` (linting)

### 4.2 Frontend Stack

**Oversight Hub (React 18):**

- React (^18.3.1)
- React DOM (^18.3.1)
- React Router DOM (^6.30.2) - Routing
- React Scripts (^5.0.1) - Create React App
- Material-UI (^7.3.6) - Component library
  - @mui/icons-material (^7.3.6)
  - @emotion/react & styled (^11.14.x)
- Zustand (^5.0.9) - State management
- Axios (^1.7.9) - HTTP client
- Firebase (^10.14.1) - Authentication
- React Markdown (^9.1.0) - Markdown rendering
- Chat UI Kit (^2.1.1) - Chat components
- TailwindCSS (^3.4.19) - Styling

**Public Site (Next.js):**

- Next.js (^15.5.9) - React framework
- React (^18.3.1)
- React DOM (^18.3.1)
- TypeScript (^5.9.3) - Type safety
- TailwindCSS (^3.4.19) - Styling
- date-fns (^4.1.0) - Date utilities
- gray-matter (^4.0.3) - YAML parsing
- marked (^14.1.4) - Markdown parsing
- react-markdown (^9.1.0) - Markdown component
- remark-gfm (^4.0.1) - GitHub markdown support
- qs (^6.14.1) - Query string parsing

**Testing & Quality:**

- Jest (^29.7.0) - Unit testing
- Playwright (^1.57.0) - E2E testing
- ESLint (^9.39.2) - Linting
- Prettier (^3.7.4) - Code formatting

### 4.3 Database

**PostgreSQL:**

- Version: 14+ (production) / Local database (dev)
- Connection: asyncpg (async Python driver)
- ORM: Custom asyncpg-based wrapper (no SQLAlchemy)
- Migrations: Custom migration system (`migrations/apply_migrations.py`)

**Database Modules:**

- UsersDatabase - User accounts & OAuth
- TasksDatabase - Task management & status
- ContentDatabase - Posts & quality metrics
- AdminDatabase - Logging & financial tracking
- WritingStyleDatabase - Writing samples for RAG

**Tables:** 18+ core tables with indices, foreign keys, and JSON metadata columns

### 4.4 Authentication

**JWT-Based:**

- Token generation in `services/auth.py`
- Token validation in middleware
- Support for Bearer token format
- Expiration and refresh logic

**OAuth Integrations:**

1. GitHub (github_oauth.py)
2. Google (google_oauth.py)
3. Facebook (facebook_oauth.py)
4. LinkedIn (linkedin_publisher.py)
5. Microsoft (microsoft_oauth.py)

**Manager Class:** `services/oauth_manager.py`

### 4.5 AI/LLM Model Support

**Primary Local Model:**

- Ollama (self-hosted, zero-cost)
- Models: llama2, mistral, neural-chat, etc.
- Port: 11434

**Cloud Providers:**

- **Anthropic:** Claude 3.5 Sonnet, Claude 3 Opus
- **OpenAI:** GPT-4 Turbo, GPT-4o
- **Google:** Gemini Pro, Gemini Pro Vision
- **Hugging Face:** Via API with transformers library

**Selection Strategy:** Cost-optimized automatic fallback

### 4.6 External Integrations

| Service | Purpose | Module |
|---------|---------|--------|
| Google Search API (Serper) | Web search results | serper_client.py |
| Pexels | Stock images | pexels_client.py |
| Twitter/X | Social publishing | twitter_publisher.py |
| LinkedIn | Job posting | linkedin_publisher.py |
| Facebook | Social auth & publishing | facebook_oauth.py |
| Email | Contact & newsletters | email_publisher.py |
| Strapi CMS | Legacy integration | cms_routes.py |
| Sentry | Error tracking | sentry_integration.py |

---

## 5. KEY FILE INVENTORY

### 5.1 Entry Points

| File | Type | Purpose |
|------|------|---------|
| `src/cofounder_agent/main.py` | Python | FastAPI app initialization, lifespan management |
| `web/oversight-hub/src/index.js` | JavaScript | React app entry point |
| `web/public-site/app/layout.js` | JavaScript | Next.js root layout |
| `web/public-site/app/page.js` | JavaScript | Next.js homepage |

### 5.2 Core Backend Files

**Orchestration:**

- `src/cofounder_agent/services/unified_orchestrator.py` (1,146 lines) - Master orchestrator
- `src/cofounder_agent/services/workflow_engine.py` (500+ lines) - Phase execution
- `src/cofounder_agent/services/workflow_composition.py` (350 lines) - Dynamic workflows

**Database:**

- `src/cofounder_agent/services/database_service.py` - Coordinator
- `src/cofounder_agent/services/users_db.py` - User operations
- `src/cofounder_agent/services/tasks_db.py` - Task operations
- `src/cofounder_agent/services/content_db.py` - Content operations
- `src/cofounder_agent/services/admin_db.py` - Admin operations
- `src/cofounder_agent/services/writing_style_db.py` - Writing samples

**Authentication & Security:**

- `src/cofounder_agent/services/auth.py` - JWT handling
- `src/cofounder_agent/services/oauth_manager.py` - OAuth orchestration
- `src/cofounder_agent/middleware/auth_middleware.py` - Request auth

**Model Routing:**

- `src/cofounder_agent/services/model_router.py` - LLM provider selection
- `src/cofounder_agent/services/ai_content_generator.py` - Content generation
- `src/cofounder_agent/services/ollama_client.py` - Ollama integration
- `src/cofounder_agent/services/gemini_client.py` - Google Gemini
- `src/cofounder_agent/services/huggingface_client.py` - HF models

**Quality & Metrics:**

- `src/cofounder_agent/services/quality_service.py` - Quality evaluation
- `src/cofounder_agent/services/metrics_service.py` - Usage metrics
- `src/cofounder_agent/services/health_service.py` - System health

### 5.3 Frontend Files

**Oversight Hub Key Files:**

- `web/oversight-hub/src/components/pages/UnifiedServicesPanel.jsx` - Phase 4 UI (430 lines)
- `web/oversight-hub/src/services/phase4Client.js` - Phase 4 API client (493 lines)
- `web/oversight-hub/src/services/orchestratorAdapter.js` - Adapter layer
- `web/oversight-hub/src/styles/UnifiedServicesPanel.css` - Phase 4 styles (554 lines)
- `web/oversight-hub/src/services/authService.js` - Auth management
- `web/oversight-hub/src/App.jsx` - App container

**Public Site Key Files:**

- `web/public-site/app/page.js` - Homepage
- `web/public-site/next.config.js` - Next.js configuration
- `web/public-site/tailwind.config.js` - TailwindCSS config

### 5.4 Database & Models

**Schemas:**

- `src/cofounder_agent/schemas/database_response_models.py` - Response models
- `src/cofounder_agent/schemas/content_schemas.py` - Content models
- `src/cofounder_agent/schemas/database_response_models.py` - DB responses
- `src/cofounder_agent/schemas/model_converter.py` - asyncpg → Pydantic

**Models:**

- `src/cofounder_agent/models/workflow.py` - Workflow definitions

### 5.5 Testing Framework

| File | Tests | Markers |
|------|-------|---------|
| `tests/test_phase4_refactoring.py` | 22 | unit, integration |
| `tests/test_e2e_workflows.py` | Multiple | e2e, smoke |
| `tests/test_full_stack_integration.py` | Multiple | integration |
| `tests/test_startup_manager.py` | Multiple | integration |
| `tests/e2e/` | Multiple | e2e, api |
| `tests/integration/` | Multiple | integration |

**Test Configuration:**

- Framework: pytest (^9.0.2)
- Async: pytest-asyncio
- Coverage: pytest-cov
- Config: `pyproject.toml` (markers, paths, asyncio_mode)

### 5.6 Configuration Files

| File | Purpose |
|------|---------|
| `.env.local` | Environment variables (single source of truth) |
| `pyproject.toml` | Python dependencies & pytest config |
| `package.json` | Node dependencies & NPM scripts |
| `tsconfig.json` | TypeScript config (Oversight Hub) |
| `next.config.js` | Next.js configuration |
| `tailwind.config.js` | TailwindCSS config |
| `jest.config.cjs` | Jest test configuration |

---

## 6. RECENT IMPLEMENTATION: PHASE 4 UI INTEGRATION

### 6.1 Phase 4 Implementation Summary

**Timeline:** January-February 2026
**Status:** ✅ COMPLETE & INTEGRATED
**Breaking Changes:** 0 (100% backward compatible)
**Lines Added:** 1,300+ (unified services) + 500+ (UI)

### 6.2 Newly Created Files (Phase 4)

**Backend (Python):**

```
src/cofounder_agent/services/
├── content_service.py (380 lines)     ← 6-phase pipeline
├── financial_service.py (160 lines)   ← ROI tracking
├── compliance_service.py (200 lines)  ← Legal review
└── market_service.py (220 lines)      ← Trend analysis

src/cofounder_agent/routes/
├── agent_registry_routes.py (400+ lines) ← Agent discovery
├── service_registry_routes.py (350 lines) ← Service discovery
└── workflow_routes.py (300 lines)     ← Workflow execution

src/cofounder_agent/services/
├── workflow_engine.py (500+ lines)    ← Phase execution
└── workflow_composition.py (350 lines) ← Dynamic building

tests/
└── test_phase4_refactoring.py (320 lines, 22 tests)
```

**Frontend (JavaScript/React):**

```
web/oversight-hub/src/
├── components/pages/
│   └── UnifiedServicesPanel.jsx (430 lines) ← Phase 4 UI
├── services/
│   ├── phase4Client.js (493 lines)    ← API client
│   └── orchestratorAdapter.js (???+)  ← Adapter layer
└── styles/
    └── UnifiedServicesPanel.css (554 lines) ← Styling
```

**Documentation:**

```
docs/
├── PHASE_4_COMPLETION_SUMMARY.md (468 lines)
└── [Phase 4 architecture documentation]

REFACTORING_COMPLETION_SUMMARY.md (556 lines) ← Complete summary
```

### 6.3 Newly Created Services

#### Content Service (380 lines)

```python
class ContentService:
    async execute_research() → research_output
    async execute_draft() → draft_output
    async execute_assess() → assessment_output
    async execute_refine() → refined_output
    async execute_image_selection() → image_metadata
    async execute_finalize() → finalized_content
    async execute_full_workflow() → complete_content
    get_service_metadata() → service_definition
```

**Endpoint:** `/api/services/content-service/execute`

#### Financial Service (160 lines)

```python
class FinancialService:
    async analyze_content_cost() → cost_breakdown
    async calculate_roi() → roi_metrics
    async forecast_budget() → budget_projection
    get_service_metadata() → service_definition
```

**Endpoint:** `/api/services/financial-service/execute`

#### Compliance Service (200 lines)

```python
class ComplianceService:
    async check_legal_compliance() → legal_assessment
    async assess_privacy_compliance() → privacy_assessment
    async risk_assessment() → risk_evaluation
    get_service_metadata() → service_definition
```

**Endpoint:** `/api/services/compliance-service/execute`

#### Market Service (220 lines)

```python
class MarketService:
    async analyze_competitors() → competitive_analysis
    async identify_opportunities() → opportunities
    async analyze_sentiment() → sentiment_data
    get_service_metadata() → service_definition
```

**Endpoint:** `/api/services/market-service/execute`

### 6.4 Backend Compatibility

**Backward Compatibility:** ✅ 100% MAINTAINED

```python
# Old agent paths still work:
from agents.content_agent.agents.research_agent import ResearchAgent

# New unified service:
from services.content_service import ContentService

# Both are registered in unified_orchestrator._get_agent_instance()
# Old paths use direct import fallback (line 260+)
```

**Registry Fallback Logic:**

1. Try: `agents.registry.get(agent_name)` ← Phase 4 registry
2. If not found: Direct import from old paths ← Backward compatibility
3. If still not found: Return detailed error with available agents

### 6.5 Frontend Integration

#### Unified Services Panel (UnifiedServicesPanel.jsx)

- Modern dashboard showcasing 4 services
- Real-time service health monitoring
- Expandable service cards showing:
  - Service metadata
  - Available phases/actions
  - Execution buttons
  - Live performance metrics
- Error boundaries for stability

#### Phase 4 Client (phase4Client.js)

- Provides clean API wrapper for:
  - Agent Discovery API (`/api/agents/*`)
  - Service Registry API (`/api/services/*`)
  - Workflow Execution API (`/api/workflows/*`)
  - Task Management API (`/api/tasks/*`)
- Uses same HTTP timeout & error handling as main client
- Includes JWT auth support

#### Orchestrator Adapter (orchestratorAdapter.js)

- Bridges old orchestrator client to new Phase 4 endpoints
- Maintains backward compatibility with existing UI
- Routes requests to appropriate service registry endpoints

### 6.6 Route Mapping

**New Routes Added (Phase 4):**

Agent Discovery:

- `/api/agents/registry` - GET full agent list
- `/api/agents/{name}` - GET agent details
- `/api/agents/{name}/phases` - GET agent phases
- `/api/agents/{name}/capabilities` - GET agent actions
- `/api/agents/phase/{phase}` - GET agents by phase
- `/api/agents/capability/{cap}` - GET agents by capability
- `/api/agents/category/{category}` - GET agents by category
- `/api/agents/search?q=...` - Search agents

Service Registry:

- `/api/services` - GET all services
- `/api/services/{name}` - GET service details
- `/api/services/{name}/actions` - GET available methods
- `/api/services/type/{type}` - Filter by type
- `/api/services/search?q=...` - Search services
- `/api/services/health` - Service health

Workflow:

- `/api/workflows/execute` - POST execute workflow
- `/api/workflows/status/{id}` - GET workflow status
- `/api/workflows/history` - GET execution history
- `/api/workflows/templates` - GET templates
- `/api/workflows/compose` - POST dynamic composition
- `/api/workflows/{id}/cancel` - DELETE stop execution

### 6.7 Known Endpoint Mappings

| Old Endpoint | New Endpoint | Adapter |
|--------------|--------------|---------|
| `/api/orchestrator/process` | `/api/workflows/execute` | orchestratorAdapter.js |
| `/api/agents` | `/api/agents/registry` | orchestratorAdapter.js |
| `/api/tasks` | `/api/tasks` (unchanged) | N/A |
| `/api/models` | `/api/models` (unchanged) | N/A |

---

## 7. QUALITY & HEALTH INDICATORS

### 7.1 Test Coverage

**Test Suite:**

- Framework: pytest (^9.0.2) with asyncio support
- Coverage tool: pytest-cov
- Total Test Files: 30+
- Test Markers: 9 (unit, integration, api, e2e, performance, slow, voice, websocket, smoke)

**Phase 4 Tests** (`tests/test_phase4_refactoring.py`):

- 22 tests total
- Coverage:
  - ✅ ContentService instantiation & metadata
  - ✅ FinancialService ROI calculation
  - ✅ ComplianceService privacy assessment & risk
  - ✅ MarketService competitor research, opportunities, sentiment
  - ✅ Agent initialization & service registration
  - ✅ UnifiedOrchestrator._get_agent_instance() fallback logic
  - ✅ Service discovery via registry
  - ✅ Module existence verification

**Test Execution:**

```bash
npm run test:python                # Full suite
npm run test:python:smoke         # Quick sanity checks
npm run test:python:integration   # Integration tests
npm run test:python:e2e           # End-to-end tests
npm run test:python:coverage      # With coverage report
```

### 7.2 Code Quality Status

**Formatting:**

- Tool: Black (line-length: 100)
- Status: ✅ Configured in pyproject.toml
- Command: `npm run format:python`

**Linting:**

- Tools: pylint, isort
- Status: ✅ Configured
- Commands: `npm run lint:python`, `npm run lint:python:sql`

**Type Checking:**

- Tool: mypy (non-strict mode)
- Status: ✅ Configured in pyproject.toml
- Command: `npm run type:check`

**Frontend Quality:**

- ESLint: ✅ Configured for React/Next.js
- Prettier: ✅ Code formatter
- Command: `npm run format:check`, `npm run lint`

### 7.3 Error Logging & Monitoring

**Log Files:**

- `src/cofounder_agent/backend.log` (2.6 KB) - Main backend log
- `src/cofounder_agent/server.log` - Server logs
- `tests.log` (Feb 11, 22:28) - Latest test run
- `console-error.log` (Feb 9, 19:18) - Browser console errors
- `console-full.log` (Feb 9, 19:18) - Full console output

**Logging System:**

- Module: `services/logger_config.py`
- Format: Structured logging with context
- Integration: Sentry (sentry_integration.py) for error tracking
- OpenTelemetry: Enabled for distributed tracing

**Log Levels:**

- DEFAULT: info
- AVAILABLE: debug, info, warning, error, critical
- Config: Can be adjusted via environment or code

### 7.4 System Health Checks

**Health Endpoint:** `GET /api/health`

**Health Checks Performed:**

- ✅ FastAPI server running
- ✅ PostgreSQL connectivity
- ✅ Redis cache (if enabled)
- ✅ LLM provider availability
  - Ollama health
  - Anthropic API key validation
  - OpenAI API key validation
  - Google API key validation
- ✅ Service initialization status

**Health Service:** `services/health_service.py`

**Runtime Status:**

- Startup Manager initializes all services in correct order
- Services report initialization status
- Graceful degradation if services fail
- Detailed error reporting in startup logs

---

## 8. KNOWN ISSUES & GAPS

### 8.1 API Endpoint Mismatches

**Status:** ⚠️ NEEDS ATTENTION

**Identified Mismatches:**

1. **Old vs New Orchestrator Endpoints**
   - Old: `/api/orchestrator/process`
   - New: `/api/workflows/execute`
   - Current: Both available (backward compatible)
   - Issue: Frontend may still reference old endpoint in some places

2. **Service Status Endpoint**
   - Old: `/api/agents/status`
   - New: `/api/services/health`
   - Status: Both should exist, needs verification

3. **Agent Metadata Endpoint**
   - Route: `/api/agents/{name}/metadata`
   - Issue: May not return full Phase 4 metadata
   - Fix: Ensure agents/registry.py returns complete data

### 8.2 Missing Endpoints

**Status:** ⚠️ PARTIALLY IMPLEMENTED

1. **Workflow Cancellation**
   - Route: `/api/workflows/{id}/cancel` - DELETE
   - Status: Framework exists (workflow_engine.py), needs route handler

2. **Service Action Execution**
   - Route: `/api/services/{name}/execute`
   - Status: Generic route exists, specific service mappings incomplete
   - Note: Each service (ContentService, FinancialService, etc.) has methods, but direct API exposure needs validation

3. **Advanced Filtering**
   - Missing advanced search parameters for:
     - Agent capability combinations
     - Service cost tier filtering
     - Workflow history filtering

### 8.3 Incomplete Migrations

**Status:** ⚠️ NEEDS REVIEW

1. **Legacy Agent Paths Still Used**
   - Location: `agents/content_agent/`, etc.
   - Status: Still present for backward compatibility
   - Action: Consider deprecation timeline

2. **Old Orchestrator Code**
   - Issue: Legacy orchestrator code may still exist
   - Status: Fallback logic maintains compatibility
   - Action: Schedule cleanup in next phase

3. **Database Schema Updates**
   - Tables appear current, but verify:
     - workflow_history table structure
     - quality_evaluations schema compatibility
     - service_metadata storage

### 8.4 Performance Bottlenecks

**Status:** ⚠️ IDENTIFIED

1. **Model Router Latency**
   - Issue: Health checks on every request
   - Impact: ~1000ms+ if Ollama unavailable
   - Solution: Cache provider health status (5-10 min TTL)
   - Current: ai_cache.py exists, needs expansion

2. **Database Connection Pooling**
   - Issue: asyncpg pool sizing not optimized
   - Current: Connection pool initialized in database_service.py
   - Action: Performance testing needed

3. **Unified Orchestrator Complexity**
   - File: `services/unified_orchestrator.py` (1,146 lines)
   - Issue: Single large file with many responsibilities
   - Action: Consider splitting into:
     - request_router.py
     - execution_engine.py
     - result_handler.py

### 8.5 Incomplete Features

**Status:** ⚠️ NOTED IN DOCUMENTATION

1. **Image Fallback Handler**
   - File: `services/IMAGE_FALLBACK_HANDLER_FUTURE_WORK.md`
   - Status: Feature planned but not implemented
   - Impact: Image generation failures may not have graceful fallback

2. **Performance Monitoring**
   - File: `services/PERFORMANCE_MONITOR_FUTURE_WORK.md`
   - Status: Monitoring infrastructure needed
   - Current: Metrics service exists, needs enhancement

3. **Cost Tracking Future Work**
   - File: `services/COST_TRACKING_FUTURE_WORK.md`
   - Status: Cost aggregation service exists, needs API endpoints
   - Impact: Financial tracking incomplete

4. **Title Generator Consolidation**
   - File: `services/TITLE_GENERATOR_CONSOLIDATION.md`
   - Status: Multiple title generators exist, should consolidate
   - Impact: Code duplication in generation pipeline

5. **Prompt Migration**
   - File: `services/PROMPT_MIGRATION_GUIDE.md`
   - Status: Prompt management still being refactored
   - Current: prompt_templates.py and prompt_manager.py exist

### 8.6 Legacy Code Not Migrated

**Status:** ⚠️ OBSERVATION

**Location:** `archive/` directory

Materials that may indicate incomplete migration:

- `archive/cofounder_backups/` - Backup copies of legacy code
- `archive/tests-unit-legacy-not-running/` - Old tests not in current suite

**Action:** Audit archive contents for:

- Features not yet migrated to Phase 4 services
- Test cases that should be included
- Documentation that should be updated

### 8.7 Type Safety & Validation

**Status:** ⚠️ PARTIAL

1. **Mypy Configuration**
   - Current: Non-strict mode (`disallow_untyped_defs = false`)
   - Coverage: Not all files have type hints
   - Action: Migrate to strict mode gradually

2. **Pydantic Validation**
   - Status: Good for routes, needs expansion for:
     - Internal service-to-service calls
     - Database query results
     - Workflow context passing

3. **TypeScript Coverage**
   - Current: React components are JSX, not TSX
   - Frontend: No type safety for Oversight Hub components
   - Action: Consider gradual migration to TypeScript

---

## 9. SUMMARY & RECOMMENDATIONS

### 9.1 Overall Assessment

| Aspect | Status | Score | Notes |
|--------|--------|-------|-------|
| **Architecture** | ✅ EXCELLENT | 9/10 | Clean separation, modular design |
| **Code Organization** | ✅ VERY GOOD | 8/10 | Flat services post-Phase 4, needs documentation |
| **Test Coverage** | ✅ GOOD | 7/10 | 22 Phase 4 tests, needs edge cases |
| **Type Safety** | ⚠️ FAIR | 6/10 | mypy non-strict, gradual migration needed |
| **Documentation** | ✅ GOOD | 8/10 | 7 core docs, Phase 4 docs complete |
| **Performance** | ⚠️ FAIR | 6/10 | Model router latency identified |
| **Backward Compatibility** | ✅ EXCELLENT | 10/10 | 100% compatible, dual paths |
| **Security** | ✅ GOOD | 8/10 | JWT, OAuth, SQL injection prevention |
| **Error Handling** | ✅ GOOD | 7/10 | Comprehensive, Sentry integration |

### 9.2 Strengths

1. ✅ **Excellent Phase 4 Implementation** - Clean unified service model
2. ✅ **100% Backward Compatible** - No breaking changes
3. ✅ **Strong Modular Database Layer** - 5 specialized databases
4. ✅ **Comprehensive API** - 50+ endpoints with clear organization
5. ✅ **Intelligent Model Routing** - Cost-optimized LLM fallback
6. ✅ **Strong Authentication** - JWT + 5 OAuth providers
7. ✅ **Good Error Tracking** - Sentry integration
8. ✅ **Clean Frontend Stack** - Modern React 18 + Next.js 15

### 9.3 Areas for Improvement

1. **Performance**
   - [ ] Cache LLM provider health status (5-10 min TTL)
   - [ ] Optimize database connection pooling
   - [ ] Profile unified_orchestrator for bottlenecks

2. **Type Safety**
   - [ ] Migrate mypy to strict mode
   - [ ] Add types to internal service calls
   - [ ] Convert Oversight Hub to TypeScript

3. **Code Organization**
   - [ ] Split unified_orchestrator.py into smaller modules
   - [ ] Consolidate title generators
   - [ ] Implement missing image fallback handler

4. **Testing**
   - [ ] Add edge case tests for Phase 4 services
   - [ ] Integrate E2E tests into CI/CD
   - [ ] Expand browser automation tests

5. **APIs**
   - [ ] Verify all Phase 4 endpoints return correct schema
   - [ ] Implement missing workflow cancellation route
   - [ ] Add advanced filtering to discovery endpoints

6. **Documentation**
   - [ ] Complete Phase 4 migration guide
   - [ ] Document all 50+ endpoints
   - [ ] Add architecture diagrams
   - [ ] Create deployment guide

### 9.4 Next Steps

**Priority 1 (This Sprint):**

1. Verify all Phase 4 endpoints work end-to-end
2. Performance: Implement LLM health check caching
3. Testing: Add edge case tests for unified services
4. Fix documented TODO items in services/

**Priority 2 (Next Sprint):**

1. Consolidate title generators
2. Implement image fallback handler
3. Complete missing API endpoints
4. Type safety: Migrate to strict mypy

**Priority 3 (Future):**

1. Code organization: Split large modules
2. Convert React components to TypeScript
3. Performance monitoring dashboard
4. Advanced security features (rate limiting, etc.)

---

## APPENDIX: Quick Reference

### Running the System

```bash
# Full development environment (all 3 services)
npm run dev

# Just backend
npm run dev:cofounder

# Just frontend
npm run dev:frontend

# Run tests
npm run test:python
npm run test:python:smoke
npm run test

# Check health
curl http://localhost:8000/health
curl http://localhost:3001  # React
curl http://localhost:3000  # Next.js
```

### Key Service Files

| Service | File | Lines | Purpose |
|---------|------|-------|---------|
| Main | main.py | 426 | FastAPI initialization |
| Orchestrator | unified_orchestrator.py | 1,146 | Master AI router |
| Content | content_service.py | 380 | 6-phase pipeline |
| Financial | financial_service.py | 160 | ROI tracking |
| Compliance | compliance_service.py | 200 | Legal review |
| Market | market_service.py | 220 | Trend analysis |
| Workflow | workflow_engine.py | 500+ | Phase execution |
| Database | database_service.py | 500+ | Data persistence |

### Important Environment Variables

```env
DATABASE_URL=postgresql://...
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza-...
OLLAMA_BASE_URL=http://localhost:11434
LLM_PROVIDER=[optional - force provider]
SENTRY_DSN=[optional - error tracking]
```

---

**Analysis Complete** | February 11, 2026 | For questions, see `docs/` directory or core documentation.
