# 🤖 GitHub Copilot Instructions for AI Agents

**Last Updated:** November 5, 2025 (ESLint v9 Migration Complete)  
**Project:** Glad Labs AI Co-Founder System v3.0  
**Status:** Production Ready | PostgreSQL Backend | Ollama AI Integration | ESLint v9 Configured | Windows PowerShell Required

---

## 🎯 Essential Context for AI Agents

### Architecture & Component Boundaries

**Glad Labs** is a three-tier monorepo orchestrating AI-powered business operations:

```
Web Tier (React/Next.js)
├── Oversight Hub (port 3001): React dashboard for agent control, model management, cost tracking
├── Public Site (port 3000): Next.js SSG site consuming Strapi content
└── Uses Zustand for state, Material-UI for components

API Tier (FastAPI + Strapi)
├── Co-Founder Agent (port 8000): Central orchestrator routing tasks to specialized agents
├── Multi-agent system: Content, Financial, Market, Compliance agents
├── Model router: Automatic fallback (Ollama → OpenAI → Claude → Gemini)
├── Memory system: Persistent context + semantic search for agent context
└── Strapi CMS (port 1337): Headless content management with TypeScript plugins

Data Tier
├── PostgreSQL: Production data (Strapi collections, audit logs)
├── SQLite: Local development
├── Redis: Caching (planned)
└── Google Cloud: Firestore + Pub/Sub (production integrations)
```

**Key Integration Patterns:**

- Oversight Hub → FastAPI REST endpoints to orchestrator (tasks, models, health)
- FastAPI → Specialized agents (parallel execution via asyncio)
- Agents → Strapi API for content CRUD operations
- All components → Model router for LLM calls with multi-provider fallback
- Logging → Audit middleware captures all state changes

---

## ⚡ Critical Development Commands (Windows PowerShell Required)

### ⚠️ TERMINAL REQUIREMENTS (IMPORTANT!)

**This project runs on Windows with PowerShell (5.1 or higher).**

- ✅ **ALL terminal commands MUST be formatted for PowerShell** (not bash/sh)
- ✅ **Use `;` to chain commands** (PowerShell syntax, not `&&`)
- ✅ **Each service should run in a SEPARATE terminal window**
- ✅ **DO NOT run multiple long-running processes in the same terminal** (causes conflicts and kills services)
- ✅ **Background tasks can use `-Background` flag in tool calls**

**Example PowerShell Commands:**

```powershell
# Correct - PowerShell syntax with semicolon
cd c:\Users\mattm\glad-labs-website; python -m uvicorn src.cofounder_agent.main:app --host 127.0.0.1 --port 8000

# Correct - Multiple commands chained
dir; ls -la; Get-Process

# Correct - Using Windows paths with backslashes
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent

# Wrong - bash/sh syntax (won't work in PowerShell)
cd ~/glad-labs-website && npm run dev
```

### Starting Services (Each in Separate Terminal Window)

**Window 1 - Backend API (FastAPI)**

```powershell
cd c:\Users\mattm\glad-labs-website
python -m uvicorn src.cofounder_agent.main:app --host 127.0.0.1 --port 8000
```

Runs on: `http://127.0.0.1:8000` (Health check: `/api/health`)

**Window 2 - Strapi CMS**

```powershell
cd c:\Users\mattm\glad-labs-website\cms\strapi-v5-backend
npm run develop
```

Runs on: `http://localhost:1337/admin`

**Window 3 - React Oversight Hub**

```powershell
cd c:\Users\mattm\glad-labs-website\web\oversight-hub
npm start
```

Runs on: `http://localhost:3001` (or next available port)

**Window 4 - Next.js Public Site**

```powershell
cd c:\Users\mattm\glad-labs-website\web\public-site
npm run dev
```

Runs on: `http://localhost:3000` (or next available port)

### Alternative: Start All Services via npm workspace commands

```powershell
cd c:\Users\mattm\glad-labs-website
npm run dev              # ✅ Starts multiple services (check port assignments)
npm run dev:oversight    # React admin dashboard on http://localhost:3001
npm run dev:public       # Next.js site on http://localhost:3000
npm run dev:backend      # FastAPI backend on http://localhost:8000
```

**Note:** Using npm workspace commands starts services in same process. For independent operation, use separate terminal windows.

### Code Quality (MUST run before committing)

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
- Files matched: `components/**`, `pages/**`, `lib/**`, `app/**` (*.{js,jsx})
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

```powershell
npm run build            # Build Next.js + React production bundles
npm run build:all        # Includes Strapi (may fail due to plugin issues)
```

### Key Workspace Commands

```powershell
npm run setup:all        # Install all dependencies (Node + Python)
npm run clean:install    # Full reset: rm node_modules + fresh install
npm run install:all      # Just npm install across all workspaces
npm run setup:python     # Just pip install for backend
```

---

## � Critical Development Commands (Reference for AI Implementation)

### Starting Services (Workspace is pre-configured)

```powershell
npm run dev              # ✅ Starts all services concurrently (recommended)
npm run dev:frontend     # Starts both React apps (Oversight Hub + Public Site)
npm run dev:backend      # Starts Strapi CMS + Co-founder Agent (Python)
npm run dev:oversight    # React admin dashboard on http://localhost:3001
npm run dev:public       # Next.js site on http://localhost:3000
npm run dev:cofounder    # FastAPI backend on http://localhost:8000
```

### Code Quality (MUST run before committing)

```powershell
npm run lint             # Run ESLint across all workspaces
npm run lint -- --fix    # Auto-fix all linting issues (ESLint v9)
npm run format           # Prettier on all files (.js, .jsx, .tsx, .json, .md)
npm test                 # Jest frontend + pytest Python backend
npm run test:python:smoke # Quick backend smoke tests for rapid iteration
```

### Build & Deploy

```powershell
npm run build            # Build Next.js + React production bundles
npm run clean            # Clean all build artifacts and node_modules
npm run clean:install    # Full reset and fresh install
```

### Key Workspace Commands

```powershell
npm run setup            # Install all dependencies (Node + Python)
npm run install:all      # Just npm install across all workspaces
npm run test:ci          # CI-mode testing (coverage, no watch)
npm run test:python      # Full pytest backend suite
```

---

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
|                          | - `content_agent/` - Content creation and management                   |
|                          | - `financial_agent/` - Business metrics and projections                |
|                          | - `market_insight_agent/` - Market analysis and trends                 |
|                          | - `compliance_agent/` - Regulatory compliance checking                 |
|                          | - `social_media_agent/` - Social media strategy and posting            |
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

```
docs/
├── 00-README.md                    # Hub - Main navigation
├── 01-SETUP_AND_OVERVIEW.md        # Getting started
├── 02-ARCHITECTURE_AND_DESIGN.md   # System design
├── 03-DEPLOYMENT_AND_INFRASTRUCTURE.md
├── 04-DEVELOPMENT_WORKFLOW.md      # Git & testing
├── 05-AI_AGENTS_AND_INTEGRATION.md # Agent system
├── 06-OPERATIONS_AND_MAINTENANCE.md
├── 07-BRANCH_SPECIFIC_VARIABLES.md # Environment config
├── components/                     # Per-component docs (minimal)
├── reference/                      # Technical references (schemas, API specs)
└── troubleshooting/                # Focused troubleshooting guides
```

### High-Level Documentation Policy ⚠️ **IMPORTANT**

**Effective: October 22, 2025**

Glad Labs maintains a **HIGH-LEVEL ONLY** documentation approach to reduce maintenance burden and prevent documentation staleness as the codebase evolves.

**DOCUMENTATION CREATED:**

- ✅ Core docs (00-07): Architecture-level, high-level guidance
- ✅ Components: Only when unique from core docs
- ✅ Reference: Technical specs, schemas, API definitions
- ✅ Troubleshooting: Focused, specific issues with solutions
- ✅ README files: In component folders for local setup

**DOCUMENTATION NOT CREATED:**

- ❌ How-to guides for every feature (feature code is the guide)
- ❌ Status updates or session-specific documents
- ❌ Duplicate documentation (consolidate into core docs)
- ❌ Step-by-step tutorials for changing code (too high maintenance)
- ❌ Outdated historical guides (archive or delete)
- ❌ Temporary project audit files

**PHILOSOPHY:**
Documentation should answer "WHAT is the architecture?" and "WHERE do I look?" — NOT "HOW do I implement X?" (That changes too fast and code is self-documenting).

**MAINTENANCE:**

- Update core docs (00-07) only when architecture changes
- Delete guides that become outdated
- Archive historical documents
- Keep docs < 8 files in root
- Archive > 50 files total in docs/

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
3. Fix bug, verify test passes
4. Commit: `fix: resolve issue description`
5. Follow PR process above

### Update Documentation

1. Edit markdown files in `docs/`
2. Verify links are correct
3. Commit: `docs: update documentation topic`
4. No deployment needed - docs are version-controlled

### Deploy to Production

1. Ensure all tests pass on `dev`
2. Create PR: `dev` → `main`
3. Code review and approval required
4. Merge to main (triggers deployment)
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

### Strapi Build Failures

**Problem:** Module not found, dependency errors
**Solution:** Clear cache and reinstall

```bash
cd cms/strapi-main
rm -rf node_modules yarn.lock package-lock.json
npm install
npm run develop
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
- [Strapi Documentation](https://docs.strapi.io)
- [src/cofounder_agent/README.md](../src/cofounder_agent/README.md)
- [AI Agents Guide](../docs/05-AI_AGENTS_AND_INTEGRATION.md)

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
| **Last Updated** | November 5, 2025 (ESLint v9 Migration)        |
| **Next Review**  | February 5, 2026 (quarterly)                   |
| **Author**       | GitHub Copilot & Glad Labs Team                |
| **Status**       | Active & Maintained | Production Ready        |
| **Audience**     | All team members (developers, DevOps, QA, PMs) |

---

**🚀 Ready to code? Start with the [Setup Guide](../docs/01-SETUP_AND_OVERVIEW.md)!**
