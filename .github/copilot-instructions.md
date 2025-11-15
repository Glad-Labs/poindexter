# 🤖 GitHub Copilot Instructions for AI Agents

**Last Updated:** November 14, 2025  
**Project:** Glad Labs AI Co-Founder System v3.0  
**Status:** PostgreSQL-First Backend | Strapi Removed | Direct DB Publishing | FastAPI REST API Only

---

## 🎯 Essential Context for AI Agents

### Architecture & Component Boundaries (Current State)

**Glad Labs** is a three-tier monorepo with direct PostgreSQL integration:

```
Frontend Tier (React)
├── Oversight Hub (port 3001): React dashboard for agent control, model management, cost tracking
│   └── Material-UI + Zustand state management
├── Public Site (port 3000): Next.js SSG site consuming PostgreSQL posts directly
│   └── Server-side rendering with ISR for content updates
└── Both frontends connect to FastAPI backend via JWT authentication

Backend Tier (FastAPI + PostgreSQL)
├── Co-Founder Agent (port 8000): Central orchestrator routing tasks to specialized agents
├── Multi-agent system: Content, Financial, Market, Compliance agents
├── Model router: Automatic fallback (Ollama → Claude → OpenAI → Gemini)
├── Memory system: PostgreSQL-backed persistent context + semantic search
├── Strapi Publisher: Direct PostgreSQL writes (no REST API, no separate CMS service)
└── CMS Routes: Direct database read/write to posts, categories, tags, media tables

Data Tier
├── PostgreSQL: PRIMARY DATA STORE
│   ├── Posts, categories, tags, media (CMS tables)
│   ├── Memories, knowledge clusters (AI memory tables)
│   ├── Tasks, workflows (command queue)
│   └── Users, sessions, audit logs
├── Local SQLite: Development option (DATABASE_URL env var)
└── Google Cloud: No longer used (Firestore/Pub/Sub removed)
```

**Key Integration Pattern (NO STRAPI SERVICE):**

- Frontend → FastAPI REST API (GET/POST/PUT/DELETE endpoints)
- FastAPI routes → PostgreSQL database directly (psycopg2/asyncpg)
- StrapiPublisher service → Writes directly to Strapi schema tables in PostgreSQL
- Model router → LLM calls with automatic provider fallback
- No separate Strapi service/port - CMS is database-driven

---

## 🎯 Critical Development Commands (Reference for AI Implementation)

### Starting Services (Workspace is pre-configured)

```bash
npm run dev              # ✅ Starts all frontends + backend together (recommended)
npm run dev:backend       # FastAPI backend + CMS database setup on port 8000
npm run dev:frontend      # Oversight Hub (3001) + Public Site (3000) together
npm run dev:oversight     # React dashboard on http://localhost:3001
npm run dev:public        # Next.js site on http://localhost:3000
npm run dev:cofounder     # FastAPI Co-Founder Agent on http://localhost:8000
```

**IMPORTANT: NO STRAPI SERVICE** - CMS is database-driven directly via FastAPI routes:
- CMS endpoints are in `src/cofounder_agent/routes/cms_routes.py`
- Data is stored in PostgreSQL (Strapi schema tables)
- StrapiPublisher writes directly to database (no REST API middleware)

### Code Quality (MUST run before committing)

```bash
npm run lint:fix         # Auto-fix ESLint + import sorting
npm run format           # Prettier on all files (.js, .jsx, .tsx, .json, .md)
npm test                 # Jest frontend + pytest Python backend
npm run test:python:smoke # Quick backend smoke tests for rapid iteration
```

### Build & Deploy

```bash
npm run build            # Build Next.js + React production bundles
npm run build:all        # Includes Strapi (will likely fail due to plugin issues)
```

### Key Workspace Commands

```bash
npm run setup:all        # Install all dependencies (Node + Python)
npm run clean:install    # Full reset: rm node_modules + fresh install
npm run install:all      # Just npm install across all workspaces
npm run setup:python     # Just pip install for backend
```

---

## � Code Patterns & Conventions (NOT aspirational - these are discovered patterns)

### Python Backend Patterns (src/cofounder_agent/)

**FastAPI Route Structure** (`src/cofounder_agent/routes/`)

- Routes are modular: `content_routes.py`, `cms_routes.py`, `models.py`, `auth_routes.py`, `task_routes.py`, etc.
- All routes injected into main FastAPI app in `main.py` (no Strapi service routes)
- Routes directly access PostgreSQL via psycopg2 or asyncpg (no REST layer)
- **PATTERN:** Routes handle HTTP validation; services handle business logic
- **KEY:** CMS routes read/write directly to PostgreSQL posts table (same schema as Strapi)

**Database Patterns** (`database.py`, `services/database_service.py`, `services/content_publisher.py`)

- SQLAlchemy models for local SQLite (dev) / PostgreSQL (prod)
- StrapiPublisher service writes directly to Strapi schema tables in PostgreSQL (no REST API)
- psycopg2 for sync database operations (cms_routes.py)
- asyncpg for async pool operations (content_publisher.py)
- Direct table access: posts, categories, tags, memories, tasks, knowledge_clusters
- **PATTERN:** Services handle database operations; routes handle HTTP

**Orchestrator Pattern** (`orchestrator_logic.py`, `services/poindexter_orchestrator.py`)

- Central orchestrator coordinates all agent execution
- Async methods for parallel task processing via `asyncio.gather()`
- Multi-provider model routing (Ollama → Claude → GPT → Gemini fallback)
- **KEY FILE:** `src/cofounder_agent/main.py` shows FastAPI setup and route registration
- **PATTERN:** Thin controllers, thick orchestrator

**Error Handling - Watch for:**

- Google Cloud integrations REMOVED: No Firestore/Pub/Sub - using PostgreSQL task store instead
- Database ALWAYS available: `DATABASE_SERVICE_AVAILABLE = True` (required)
- Model provider failures trigger automatic fallback (don't wrap in try/except - router handles it)
- Strapi REST API NOT used: All operations go directly to PostgreSQL

### React/Next.js Patterns (web/)

**Oversight Hub State Management** (`web/oversight-hub/src/`)

- **Single source of truth:** Redux/Context + local state management
- **API Client:** `cofounderAgentClient.js` - JWT-authenticated requests to FastAPI
- **Services:** Direct FastAPI calls (no Strapi - CMS routes are in FastAPI now)
- **PATTERN:** All API calls go through services, never scattered in components
- Components subscribe to specific data, not entire state

**Next.js Public Site Patterns** (`web/public-site/`)

- **SSG First:** Use `getStaticProps` and `getStaticPaths` (not SSR for performance)
- **ISR (Incremental Static Regeneration):** `revalidate: 3600` for content updates
- **Database client:** Queries PostgreSQL directly or via FastAPI CMS routes
- **PATTERN:** All database calls go through FastAPI `/api/posts` endpoints

**Component Organization:**

- Lightweight presentational components in `components/`
- Business logic in custom hooks or service layers
- Material-UI components (Oversight Hub), Tailwind CSS (Public Site)
- **PATTERN:** No nested components, use composition

### Testing Patterns (Already Existing)

**Frontend:** Jest + React Testing Library

- Test location: `__tests__/` folders parallel to source
- Mock FastAPI API responses in tests (don't hit real API)
- Example pattern: `expect(getByText(...)).toBeInTheDocument()`

**Backend:** pytest for Python

- Test location: `src/cofounder_agent/tests/`
- Mock external services (LLMs, PostgreSQL, model providers)
- Run with: `npm run test:python` or `npm run test:python:smoke` (faster)

---

## ⚠️ Known Constraints & Pain Points (For AI Agent Context)

**Strapi Service Removed**

- ✅ No separate Strapi service/port (not needed)
- ✅ CMS data stored directly in PostgreSQL (Strapi schema tables)
- ✅ Routes in FastAPI handle all CRUD operations
- ✅ StrapiPublisher writes directly to database (no REST API middleware)
- **Note:** cms/strapi-main/ folder exists for schema reference only

**Database is Primary Data Store**

- PostgreSQL is the single source of truth
- All CMS content managed via `/api/posts`, `/api/categories`, `/api/tags` routes
- Direct table access from FastAPI routes (psycopg2 for sync, asyncpg for async)
- No ORM abstraction layer - raw SQL or psycopg2 queries

**Async/Await Patterns in Python**

- Backend uses heavy async (FastAPI + asyncio)
- All orchestrator methods are `async`; use `await` when calling them
- Parallel execution via `asyncio.gather()` - don't use threading
- Some routes use sync psycopg2 (cms_routes.py), others use async asyncpg

**Frontend Port Conflicts**

- Both Oversight Hub and Public Site want port 3000
- System auto-assigns next available (3001, 3002) - don't hardcode ports
- Verify actual port in terminal output after `npm run dev`

**Environment Variables - Critical!**

- Local dev: Copy `.env.example` → `.env` (never commit .env)
- Production secrets: GitHub Secrets + Railway/Vercel dashboards
- No production secrets should ever appear in code or docs
- Model provider keys (OPENAI_API_KEY, etc.) are required for backend
- **DATABASE_URL must point to PostgreSQL:** `postgresql://user:pass@host:5432/dbname`

---

## � File Organization & Where to Look

| Need                     | Look In                                                                 |
| ------------------------ | ----------------------------------------------------------------------- |
| FastAPI backend logic    | `src/cofounder_agent/main.py`, `orchestrator_logic.py`, `routes/`       |
| AI agent implementations | `src/agents/{content,financial,market_insight,compliance}_agent/`       |
| React admin dashboard    | `web/oversight-hub/src/components/`, `store/useStore.js`                |
| Next.js public site      | `web/public-site/pages/`, `lib/api.js`, `components/`                   |
| CMS routes (NOT Strapi)  | `src/cofounder_agent/routes/cms_routes.py` (direct PostgreSQL access)   |
| Database schema          | `src/cofounder_agent/database.py` (SQLAlchemy models)                   |
| Strapi schema reference  | `cms/strapi-main/src/` (for understanding table structure, not running)  |
| Authentication flow      | `src/cofounder_agent/routes/auth_routes.py`, `middleware/auth.py`       |
| Audit logging            | `src/cofounder_agent/middleware/audit_logging.py` (type-safe, 0 errors) |
| PostgreSQL operations    | `src/cofounder_agent/services/database_service.py`, route files         |
| Direct to DB writes      | `src/cofounder_agent/services/content_publisher.py` (asyncpg, no REST)   |
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
- ❌ Create ANY documentation in the root folder (only docs/, archive/, and core files like README.md)
- ❌ Hardcode API endpoints (use environment variables from `.env`)
- ❌ Prop-drill state in React (use Zustand or URL params)
- ❌ Mix async/sync in Python orchestrator (everything must be async)
- ❌ Ignore type hints or leave Python functions untyped
- ❌ Commit secrets, API keys, or unencrypted sensitive data
- ❌ Modify Strapi plugins without extensive testing (known issues)
- ❌ Create documentation that will become stale (use pragmatic approach: active docs vs archive)

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

```
docs/
├── 00-README.md                      # Hub - Main navigation
├── 01-SETUP_AND_OVERVIEW.md          # Getting started
├── 02-ARCHITECTURE_AND_DESIGN.md     # System design
├── 03-DEPLOYMENT_AND_INFRASTRUCTURE.md
├── 04-DEVELOPMENT_WORKFLOW.md        # Git & testing
├── 05-AI_AGENTS_AND_INTEGRATION.md   # Agent system
├── 06-OPERATIONS_AND_MAINTENANCE.md
├── 07-BRANCH_SPECIFIC_VARIABLES.md   # Environment config
├── decisions/                        # Architectural decisions (WHY_*.md)
├── reference/                        # Technical references (API specs, schemas)
├── roadmap/                          # Future planning and milestones
├── guides/                           # How-to guides (minimal maintenance)
├── troubleshooting/                  # Problem solving (living document)
└── components/                       # Per-component documentation

archive/
├── phase-5/                          # Phase 5 deliverables (frozen)
├── phase-4/                          # Phase 4 deliverables (frozen)
├── sessions/                         # Session notes (frozen)
├── deliverables/                     # Major deliverables (frozen)
└── README.md                         # Archive index
```

### Documentation Strategy ⚠️ **PRAGMATIC APPROACH**

**Effective: November 14, 2025 - Framework Complete**

Glad Labs uses a **PRAGMATIC DOCUMENTATION** approach balancing usefulness with sustainability.

**5 Categories with Clear Ownership:**

1. **Architecture & Decisions** (MAINTAIN ACTIVELY - Quarterly)
   - Core docs 00-07 (system design, deployment, workflows)
   - `docs/decisions/` with WHY_*.md files (decision rationale)
   - `docs/roadmap/` for future planning
   - ✅ Update when architecture or strategic direction changes
   - **Example:** WHY_FASTAPI.md, WHY_POSTGRESQL.md

2. **Technical Reference** (MAINTAIN ACTIVELY - Ongoing)
   - `docs/reference/` with API contracts, schemas, standards
   - Component inventory and specifications
   - ✅ Update as APIs, database, or systems change
   - **Example:** API_CONTRACTS.md, TESTING.md

3. **How-To Guides** (MAINTAIN MINIMALLY - As Needed)
   - `docs/guides/` for stable, valuable topics only
   - Accept some staleness as acceptable trade-off
   - ✅ Complement core docs with practical examples
   - **Focus:** Evergreen content with lasting value

4. **Troubleshooting** (MAINTAIN AS NEEDED - Living Document)
   - `docs/troubleshooting/` for common issues and solutions
   - Grows organically as problems are solved
   - ✅ Actively encouraged contributions from fixes
   - **Pattern:** Problem → Solution → Prevention

5. **Archive & History** (NEVER MAINTAIN - Frozen)
   - `archive/` for completed phases, sessions, old implementations
   - Preserved for reference and knowledge transfer
   - ✅ Restore historical context without cluttering active docs
   - **70+ files** now archived, keeping root folder clean

**Update Schedules & Maintenance Levels:**

| Category | Frequency | Effort | Status |
|----------|-----------|--------|--------|
| Core Docs (00-07) | Quarterly review | 2-3 hours | Active ✅ |
| Architecture Decisions | When decision made | 1-2 hours | Active ✅ |
| API References | As APIs change | Ongoing | Active ✅ |
| Roadmaps | Plan updates | Monthly | Active ✅ |
| Guides | Minimal | 30 min per | Minimal ⚠️ |
| Troubleshooting | As issues solved | Variable | Living 📝 |
| Archive | Never | 0 hours | Frozen 🔒 |

**MAINTENANCE PHILOSOPHY:**

- Document what **survives architectural changes** ✅
- Document what **developers actually need** ✅
- Archive what **becomes stale quickly** ✅
- Keep **decisions documented** so we understand why ✅
- **Encourage troubleshooting entries** from every fix ✅

**KEY RULE: "Pragmatism > Purity"**

If something helps developers and is maintainable, document it. If it gets stale, archive it. We don't follow rigid rules that reduce usefulness.

**Current Metrics (Phase 1 Complete):**
- ✅ 8 new documents created (3,700+ lines)
- ✅ 5-category framework established
- ✅ 70+ historical files archived
- ✅ Root folder reduced from 100+ to 5 files
- ✅ Decision document pattern established (2 WHY_*.md examples)
- ✅ API reference complete (50+ endpoints documented)

### Update Process

1. Edit documentation files (core docs 00-07 only, unless exception)
2. Run markdown linter: `.markdownlint.json` rules applied
3. Check links are valid: Run link checker
4. Commit with `docs:` prefix
5. Update "Last Updated" date
6. DO NOT create guides or status documents

---

## 🔄 Common Development Tasks

### Add a New Feature

1. Create feature branch: `git checkout -b feature/my-feature`
2. Implement feature with tests
3. Run test suite: `npm run test`
4. Commit with conventional message: `feat: add my feature`
5. Push and create PR to `dev` branch
6. After merge, feature automatically deployed to staging
7. After verification, merge `dev` → `main` for production

### Fix a Bug

1. Create bugfix branch: `git checkout -b bugfix/issue-description`
2. Add test case that reproduces bug
3. Fix bug in FastAPI route or service (for backend bugs)
   - OR fix in React component/store (for frontend bugs)
4. Verify test passes
5. Commit: `fix: resolve issue description`
6. Follow PR process above

### Add a New API Endpoint

1. Create route file or add to existing route in `src/cofounder_agent/routes/`
2. Import database connection and define endpoint function
3. Use `psycopg2` for sync operations (like cms_routes.py) or `asyncpg` for async
4. Handle direct PostgreSQL access, not through Strapi API
5. Add input validation and error handling
6. Write test in `src/cofounder_agent/tests/`
7. Test locally: `curl http://localhost:8000/api/your-endpoint`
8. Commit and push

### Add a Database Table or Schema Change

1. Update SQLAlchemy models in `src/cofounder_agent/database.py`
2. For direct SQL operations, add migration script if needed
3. Update corresponding service that uses the table
4. Add tests for database operations
5. Test with both SQLite (local) and PostgreSQL (production)
6. **Never hardcode Strapi table names** - use schema reference in docs

### Update Documentation

1. Edit markdown files in `docs/`
2. Verify links are correct
3. Commit: `docs: update documentation topic`
4. No deployment needed - docs are version-controlled

### Deploy to Production

1. Ensure all tests pass on `dev`
2. Create PR: `dev` → `main`
3. Code review and approval required
4. Merge to main (triggers GitHub Actions deployment)
5. Verify in production environment
6. Tag release: `git tag v1.2.3 && git push --tags`

---

## 🐛 Troubleshooting Common Issues

### Node.js Version Errors

**Problem:** "Expected >=18.0.0 <=22.x.x, got 25.0.0"
**Solution:** Use NVM with `.nvmrc`

```bash
nvm use 22
```

### FastAPI Import Errors

**Problem:** "No module named uvicorn"
**Solution:** Verify venv activation

```bash
# Activate venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

# Run from project root (not from src/cofounder_agent)
python -m uvicorn src.cofounder_agent.main:app --reload
```

### Database Connection Issues

**Problem:** Cannot connect to PostgreSQL
**Solution:** Check connection string and credentials

```bash
# Verify .env has correct DATABASE_URL
echo $DATABASE_URL
# Format: postgresql://user:password@localhost:5432/dbname
```

**See:** [Documentation Hub](../docs/00-README.md)

---

## 📦 Root Folder Organization (CLEAN - Phase 1 Complete)

**Active Files in Root:**

- `README.md` - Project overview
- `LICENSE.md` - License information
- `PHASE_1_COMPLETE.md` - Current phase summary
- `DOCUMENTATION_STRATEGY.md` - Documentation framework
- `package.json` - Workspace configuration

**All Other Historical Files Archived:**

- ✅ 70+ files moved to `archive/`
- ✅ Phase 5 files → `archive/phase-5/`
- ✅ Phase 4 files → `archive/phase-4/`
- ✅ Session docs → `archive/sessions/`
- ✅ Deliverables → `archive/deliverables/`
- ✅ See `archive/README.md` for index

**Why Archive?**

- Keeps root folder clean and focused
- Reduces cognitive load for new developers
- Preserves history for reference
- Prevents outdated files from being used as templates
- Makes active documentation more discoverable

---

## 📚 Documentation Reference Map

## 📞 Getting Help

### Documentation Resources

- **Full Docs Hub:** [docs/00-README.md](../docs/00-README.md)
- **Architecture Deep Dive:** [docs/02-ARCHITECTURE_AND_DESIGN.md](../docs/02-ARCHITECTURE_AND_DESIGN.md)
- **Components:** [docs/components/](../docs/components/)

### Code Assistance

- **Type Definitions:** Check TypeScript `.d.ts` files
- **API Docs:** See OpenAPI/Swagger specs in Strapi and FastAPI
- **Examples:** Look for `example/` or `demo/` folders
- **Tests:** Test files often show usage examples

### When Stuck

1. Check troubleshooting guide
2. Search existing documentation
3. Review test files for examples
4. Check git log for similar fixes
5. Ask team or escalate to lead

---

## 🎓 Learning Resources

### For New Team Members

1. Start: [Setup Guide](../docs/01-SETUP_AND_OVERVIEW.md)
2. Read: [Architecture Overview](../docs/02-ARCHITECTURE_AND_DESIGN.md)
3. Try: Set up local environment
4. Explore: [Development Workflow](../docs/04-DEVELOPMENT_WORKFLOW.md)
5. Study: Your component's README in [docs/components/](../docs/components/)

### For Different Roles

**Frontend Developers:**

- [Next.js Docs](https://nextjs.org/docs)
- [web/oversight-hub/README.md](../web/oversight-hub/README.md)
- [web/public-site/README.md](../web/public-site/README.md)
- [Component Documentation](../docs/components/)

**Backend Developers:**

- [FastAPI Documentation](https://fastapi.tiangolo.com)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [src/cofounder_agent/README.md](../src/cofounder_agent/README.md)
- [AI Agents Guide](../docs/05-AI_AGENTS_AND_INTEGRATION.md)
- [Database Schema & Migration](../docs/reference/data_schemas.md)

**DevOps / Infrastructure:**

- [Deployment Guide](../docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md)
- [Operations Guide](../docs/06-OPERATIONS_AND_MAINTENANCE.md)
- [Branch Variables](../docs/07-BRANCH_SPECIFIC_VARIABLES.md)
- [Reference Docs](../docs/reference/)

**QA / Testing:**

- [Testing Guide](../docs/reference/TESTING.md)
- [Reference Docs](../docs/reference/)
- Test files in each component

---

## ✅ Before You Start Coding

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

## 📋 Document Control

| Field            | Value                                          |
| ---------------- | ---------------------------------------------- | 
| **Version**      | 2.1                                            |
| **Last Updated** | November 14, 2025                              |
| **Next Review**  | February 14, 2026 (quarterly)                  |
| **Author**       | GitHub Copilot & Glad Labs Team                |
| **Status**       | Active & Maintained | Production Ready       |
| **Audience**     | All team members (developers, DevOps, QA, PMs) |
| **Key Changes**  | Phase 1 complete - Archive structure, pragmatic documentation framework established |

---

**🚀 Ready to code? Start with the [Setup Guide](../docs/01-SETUP_AND_OVERVIEW.md)!**
