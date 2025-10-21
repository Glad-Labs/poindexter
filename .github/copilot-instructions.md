# GLAD Labs Copilot Instructions

**Last Updated:** October 21, 2025  
**Project:** GLAD Labs - AI-Powered Frontier Firm Platform (Monorepo)  
**For:** AI coding agents assisting with development

---

## 🎯 Project Overview

**GLAD Labs** is an integrated platform combining:

- **Next.js Public Site** - Content delivery + Strapi integration (port 3000)
- **React Dashboard** - Oversight Hub admin interface (port 3001)
- **Strapi CMS** - Headless content management on Railway (port 1337)
- **FastAPI Co-Founder Agent** - Python multi-agent AI orchestrator (port 8000)

**Key Architecture Pattern:** Monorepo using npm workspaces + Python agents. Frontend fetches from Strapi with 10-second timeout protection. All pages have markdown fallbacks for Strapi downtime.

---

## 🚀 Essential Workflows

### Local Development (All Services)

```bash
# Start everything at once (recommended)
npm run dev

# Environment auto-selected based on branch:
# feat/* branches → .env (local development)
# dev branch → .env.staging
# main branch → .env.production

# This launches:
# - Strapi CMS (http://localhost:1337/admin)
# - Public Site (http://localhost:3000)
# - Oversight Hub (http://localhost:3001)
# - Co-founder Agent (http://localhost:8000/docs)

# Verify all services running:
npm run services:check
```

### Building & Testing

```bash
# Test everything (frontend + Python)
npm run test

# Build all workspaces
npm run build

# Format & lint
npm run format && npm run lint:fix

# Manually select environment (if needed)
npm run env:select
```

### Deployment Workflow (Branch → Environment)

```bash
# FEATURE DEVELOPMENT (feat/* branches)
git checkout -b feat/my-feature
npm run dev                    # Uses .env (local SQLite)
# ... make changes, test, commit
git push origin feat/my-feature
# GitHub Actions: test-on-feat.yml runs tests

# STAGING (dev branch)
git checkout dev
git merge feat/my-feature
git push origin dev
# GitHub Actions: deploy-staging.yml
# - Loads .env.staging
# - Tests with staging database
# - Deploys to Railway staging

# PRODUCTION (main branch)
git checkout main
git merge dev
git push origin main
# GitHub Actions: deploy-production.yml
# - Loads .env.production
# - Tests with production database
# - Deploys to Vercel (frontend) + Railway (backend)
```

---

## 🌐 Source Control & Deployment Architecture

### Version Control Setup

**CRITICAL:** GitLab ↔ GitHub Mirror Architecture

**Structure:**

- **GitLab** (gitlab.com) - Private repository, source of truth
- **GitHub** (github.com) - Public mirror, triggers CI/CD
- **Why Two Repos?** Public development showcase + Private backup + GitHub Actions automation

**Push Workflow:**

```bash
# All work flows through GitLab first
git push origin main  # Pushes to GitLab (primary)

# GitLab → GitHub sync:
# - Configured via GitLab mirroring settings
# - GitHub receives push ~30 seconds later
# - GitHub Actions then trigger deployment
```

**Key Branch:** `main`

- Auto-deploy on push to main on GitHub
- This is your production deployment trigger

### Deployment Targets

**Frontend (Next.js Public Site)**

- **Target:** Vercel (https://vercel.com)
- **Repository:** `web/public-site/`
- **Trigger:** Push to main on GitHub
- **URL:** https://glad-labs.vercel.app (or custom domain)
- **Deployment Time:** ~3-5 minutes
- **Auto Rollback:** On build failure
- **Environment:** `NEXT_PUBLIC_STRAPI_API_URL`, `NEXT_PUBLIC_STRAPI_API_TOKEN` set in Vercel dashboard

**Backend (Strapi CMS)**

- **Target:** Railway.app
- **Repository:** `cms/strapi-v5-backend/`
- **Trigger:** Manual deployment or webhook (check Railway settings)
- **URL:** https://strapi.railway.app (or custom domain)
- **Port:** 1337 (Railway hosted)
- **Database:** PostgreSQL (hosted on Railway)
- **Environment:** Set in Railway dashboard (`DATABASE_URL`, `STRAPI_API_TOKEN`, etc.)

**Local Development**

- **Public Site:** http://localhost:3000
- **Strapi CMS:** http://localhost:1337/admin
- **Oversight Hub:** http://localhost:3001
- **Co-founder Agent:** http://localhost:8000/docs
- **All Services:** `npm run dev` from root

### Push to Production Checklist

Before `git push origin main`:

- ✅ Run `npm run test` locally (all tests pass)
- ✅ Run `npm run lint:fix` (code is formatted)
- ✅ Test locally: `npm run dev` → visit http://localhost:3000
- ✅ Verify Strapi content is accessible (if content changes)
- ✅ Review commit messages (clear, descriptive)
- ✅ Pull latest: `git pull origin main` (avoid conflicts)

After `git push origin main`:

- ✅ Check GitHub Actions in mirror repo
- ✅ Monitor Vercel deployment: https://vercel.com/dashboard
- ✅ Visit https://glad-labs.vercel.app (or custom domain) after ~5 min
- ✅ Verify all pages load (check for 404s or fallback content)
- ✅ Test Strapi integration on production: does content load? (check browser DevTools)

---

## 🔄 Documentation Maintenance Workflow

### Philosophy

**Golden Rule:** Update existing documentation, don't create new unnecessary files. All docs belong in `docs/` - NEVER create docs in root or component folders (web/public-site/, cms/strapi-main/, src/cofounder_agent/, web/oversight-hub/) except for component-specific README.md files.

After completing work:

1. ✅ Update existing docs in `docs/` hierarchy (or new docs to appropriate folders)
2. ✅ Link from `docs/00-README.md` (main hub) OR component README if component-specific
3. ❌ NEVER create docs in component root directories
4. ❌ NEVER create duplicate documentation (check `docs/` first!)
5. ✅ Commit changes with `docs:` prefix

### Documentation Structure (Complete)

```
docs/                                  ← ALL PROJECT DOCUMENTATION HERE
├── 00-README.md ......................... Documentation hub (UPDATE ONLY)
├── 01-SETUP_AND_OVERVIEW.md ............ Quick start & setup (UPDATE)
├── 02-ARCHITECTURE_AND_DESIGN.md ....... System design (UPDATE)
├── 03-DEPLOYMENT_AND_INFRASTRUCTURE.md  Production deployment (UPDATE)
├── 04-DEVELOPMENT_WORKFLOW.md ......... Git workflow & dev process (UPDATE)
├── 05-AI_AGENTS_AND_INTEGRATION.md .... Agent patterns (UPDATE)
├── 06-OPERATIONS_AND_MAINTENANCE.md ... Operations guide (UPDATE)
│
├── components/                       ← COMPONENT-SPECIFIC DOCUMENTATION
│   ├── README.md ........................ Component overview & index
│   ├── public-site/
│   │   ├── README.md .................. Public site overview
│   │   ├── DEPLOYMENT_READINESS.md ... Pre-deployment checklist
│   │   └── VERCEL_DEPLOYMENT.md ...... Vercel config guide
│   ├── oversight-hub/
│   │   └── README.md .................. Dashboard overview
│   ├── cofounder-agent/
│   │   ├── README.md .................. Agent overview
│   │   └── INTELLIGENT_COFOUNDER.md .. Agent architecture
│   └── strapi-cms/
│       └── README.md .................. CMS overview
│
├── guides/                          ← HOW-TO GUIDES & QUICK STARTS
│   ├── TESTING_SUMMARY.md ............. Testing initiative results
│   ├── PYTHON_TESTS_SETUP.md ......... Python test setup
│   ├── QUICK_START_TESTS.md .......... Test quick reference
│   ├── TEST_TEMPLATES_CREATED.md .... Test patterns
│   ├── STRAPI_BACKED_PAGES_GUIDE.md .. How to create Strapi pages
│   ├── CONTENT_POPULATION_GUIDE.md ... How to populate Strapi
│   └── [other how-tos]
│
├── reference/                       ← TECHNICAL SPECIFICATIONS
│   ├── API_REFERENCE.md ............... API documentation
│   ├── DATABASE_SCHEMA.md ............. Database structure
│   ├── DEPLOYMENT_COMPLETE.md ........ Deployment specs
│   ├── CI_CD_COMPLETE.md ............. CI/CD pipelines
│   └── [other specs]
│
├── troubleshooting/                 ← PROBLEM SOLUTIONS
│   ├── COMMON_ISSUES.md ............... FAQ & solutions
│   └── [category-specific]
│
└── archive-old/                     ← HISTORICAL (read-only reference)
    ├── PHASE1_SUCCESS.md
    ├── EXECUTION_STATUS.md
    └── [historical docs]

Root-level docs ONLY:
├── README.md                        ← Project entry point
└── .github/copilot-instructions.md  ← This file (AI agent guidelines)
```

### CRITICAL RULES

**❌ NEVER do these:**

1. Create new `.md` files in component folders (`web/public-site/`, `cms/strapi-main/`, etc.)
2. Create duplicate docs (always search `docs/` first!)
3. Create ANY summary files at root or in docs/ root
4. Bypass the `docs/` folder structure

**✅ ALWAYS do this:**

1. Check if doc already exists in `docs/`
2. Update existing doc instead of creating new one
3. Add links to `docs/00-README.md` or component README
4. Put component docs in `docs/components/[component]/`
5. Put guides in `docs/guides/`
6. Put specs in `docs/reference/`

### When You Complete Work

**Scenario 1: Bug fix or small feature**

```
✅ Update relevant doc in docs/ (e.g., docs/04-DEVELOPMENT_WORKFLOW.md)
✅ Add to docs/guides/ if it's a how-to
✅ Add link to docs/00-README.md
✅ Commit: git commit -m "docs: update [filename] - explain what changed"
❌ Never create new dated files like docs/FIX_SUMMARY_[DATE].md
```

**Scenario 2: New component documentation**

```
✅ Add to docs/components/[component]/
✅ Update docs/components/README.md with overview
✅ Add link to docs/00-README.md
✅ Keep component README.md in source folder (for developers finding it naturally)
✅ Commit: git commit -m "docs: add [component] documentation"
❌ Never create docs in component root (web/public-site/, src/cofounder_agent/, etc.)
```

**Scenario 3: Consolidation or restructuring**

```
✅ Move docs to docs/ structure
✅ Update docs/00-README.md to link consolidated docs
✅ Update docs/components/README.md if component-related
✅ Archive old location in docs/archive-old/ if historical
✅ Commit: git commit -m "docs: consolidate [topic]"
❌ Don't leave docs scattered across component folders
```

**Scenario 4: Troubleshooting content**

```
✅ Add to docs/troubleshooting/COMMON_ISSUES.md
✅ OR create docs/troubleshooting/[CATEGORY]_ISSUES.md if many related issues
✅ Cross-link from relevant main docs
✅ Commit: git commit -m "docs: add troubleshooting - [issue]"
❌ Never create docs/TROUBLESHOOTING_SESSION_[DATE].md (proliferation)
```

### Consolidation Strategy

**Before Creating ANY New Doc:**

1. ✅ Search `docs/` for existing doc on topic
2. ✅ Check if you should UPDATE existing doc instead
3. ✅ If new doc needed, put it in appropriate folder:
   - Component-specific → `docs/components/[component]/`
   - How-to guide → `docs/guides/`
   - Technical spec → `docs/reference/`
   - Problem/solution → `docs/troubleshooting/`
4. ✅ Add link to `docs/00-README.md` or component README
5. ✅ Delete/archive any duplicate docs

**Golden Rule:** Always check `docs/` first - all documentation should be organized in the structure shown above. Session notes and historical docs go to `docs/archive-old/`.

**Link Everything:** All active docs must be discoverable from `docs/00-README.md`

### Examples from Recent Work

**GOOD ✅ - Updated existing docs:**

- Moved component docs to `docs/components/` structure
- Updated `docs/00-README.md` with component links
- Added `docs/components/README.md` with overview
- Updated guides in `docs/guides/`

**AVOID ❌ - Would cause problems:**

- Creating `web/public-site/DEPLOYMENT_NOTES.md` (WRONG - use docs/components/public-site/)
- Creating `src/cofounder_agent/ARCHITECTURE.md` (WRONG - use docs/components/cofounder-agent/)
- Creating `docs/SESSION_NOTES_[DATE].md` (WRONG - update existing docs instead)
- Creating `cms/strapi-main/SETUP_GUIDE.md` (WRONG - use docs/components/strapi-cms/)

### Commit Message Pattern

```bash
# Use one of these prefixes:
git commit -m "docs: update [file] - describe what changed"
git commit -m "docs: add [topic] to [file]"
git commit -m "docs: consolidate [topic]"
git commit -m "docs: fix [file] - clarification"

# Examples:
git commit -m "docs: add component documentation structure"
git commit -m "docs: consolidate all component docs to docs/components/"
git commit -m "docs: update copilot instructions - docs consolidation policy"
```

---

## 🏗️ Critical Architecture Decisions

### 1. **Strapi Content with Markdown Fallbacks**

**Pattern:** All frontend pages (About, Privacy, Terms, Blog) fetch from Strapi with fallback content.

**Why:** Prevents Vercel builds from hanging if Strapi is unavailable during build time.

**Implementation:**

- `web/public-site/pages/about.js` → fetches `/api/about` with 10-second timeout
- `web/public-site/pages/privacy-policy.js` → fetches `/api/privacy-policy`
- `web/public-site/pages/terms-of-service.js` → fetches `/api/terms-of-service`

**Key Files:**

- `web/public-site/lib/api.js` - Contains `fetchAPI()` with **CRITICAL 10-second timeout**
- `docs/guides/STRAPI_BACKED_PAGES_GUIDE.md` - Complete setup guide

**When Adding New Pages:**

1. Create page in `web/public-site/pages/[page].js`
2. Use `getStaticProps()` to fetch from Strapi endpoint
3. Include markdown fallback content
4. Add revalidation: `revalidate: 60` (ISR)

### 2. **API Client Timeout Protection**

**Critical Pattern:** `fetchAPI()` in `lib/api.js` has a **REQUIRED 10-second timeout**.

**Why:** Without this, Vercel builds hang indefinitely if Strapi is slow/down.

**Code Reference:**

```javascript
const controller = new AbortController();
const timeout = setTimeout(() => controller.abort(), 10000); // 10 seconds
```

**Before modifying API client:** Read `docs/guides/FIXES_AND_SOLUTIONS.md` (see "Critical Fix: Vercel Build Timeout Issue")

### 3. **Multi-Agent System with MCP**

**Pattern:** Python FastAPI orchestrator routes requests to specialized agents via Model Context Protocol (MCP).

**Agents Available:**

- `Co-founder Agent` - Main AI decision maker (main.py)
- `Content Agent` - Content generation
- `Compliance Agent` - Regulatory checks
- `Financial Agent` - Analysis & forecasting
- `Market Insight Agent` - Market analysis

**Key Files:**

- `src/cofounder_agent/main.py` - FastAPI server
- `src/cofounder_agent/orchestrator_logic.py` - Agent routing
- `src/mcp/` - MCP server implementations

**When Working with Agents:**

- Config models in `.env` (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)
- Test via Swagger: http://localhost:8000/docs
- Read `docs/05-AI_AGENTS_AND_INTEGRATION.md`

### 4. **Workspace Structure & Scripts**

**Pattern:** Root `package.json` orchestrates workspaces via npm-run-all.

**Workspaces:**

- `web/public-site/` - Next.js frontend
- `web/oversight-hub/` - React dashboard
- `cms/strapi-main/` - Strapi CMS

**Key Scripts (from root):**

```bash
npm run dev                  # Start all services
npm run build               # Build all workspaces
npm run test                # Test frontend + Python
npm run test:python         # Python tests only
npm run lint:fix            # Lint & fix all
npm run format              # Format code
```

**Important:** Always run commands from workspace root, NOT from individual directories.

---

## 📝 Code Patterns & Conventions

### Frontend Data Fetching

**Pattern:** Use `getStaticProps()` with Strapi API calls. Always include fallback content.

```javascript
// CORRECT PATTERN:
export async function getStaticProps() {
  try {
    const data = await fetchAPI('/endpoint');
    return { props: { data }, revalidate: 60 };
  } catch (error) {
    return { props: { data: null }, revalidate: 60 }; // Fallback
  }
}

export default function Page({ data }) {
  const content = data || fallbackMarkdownContent; // Always fallback
  return <Markdown>{content}</Markdown>;
}
```

### API Client Usage

**Pattern:** All Strapi API calls go through `lib/api.js`.

```javascript
// From lib/api.js:
import { fetchAPI, getPaginatedPosts, getFeaturedPost } from '../lib/api';

// Fetch with timeout protection included:
const posts = await fetchAPI('/posts', { pagination: { limit: 10 } });
```

### Component Structure

**Pattern:** React components are functional, use hooks, minimal state.

```javascript
// Expected patterns:
- Use React.FC type if using TypeScript
- Use hooks (useState, useEffect) for state
- Tailwind CSS for styling
- Next.js Image component for images
- Extract components to `components/` directory
```

### Python Agent Development

**Pattern:** Agents extend base classes, implement required methods.

```python
# Agent structure:
class MyAgent:
    def __init__(self, config: Dict):
        self.config = config

    async def process(self, input: str) -> str:
        # Business logic here
        pass
```

---

## 🐛 Debugging & Troubleshooting

### Common Issues

**Issue:** Pages show 404 or fallback content

- ✅ Check Strapi is running: `curl http://localhost:1337/admin`
- ✅ Verify endpoints exist in Strapi
- ✅ Check env vars: `STRAPI_API_URL`, `STRAPI_API_TOKEN`

**Issue:** Build hangs on Vercel

- ✅ Check for missing 10-second timeout in new API calls
- ✅ Review `docs/guides/FIXES_AND_SOLUTIONS.md` for timeout handling
- ✅ Run `npm run services:check` to verify Strapi

**Issue:** Tests failing

- ✅ Frontend: `npm run test:public:ci`
- ✅ Python: `npm run test:python`
- ✅ Check dependencies: `npm run clean:install`

### Diagnostic Scripts

```bash
npm run services:check      # Health check all services
npm run services:kill       # Stop all background services
npm run services:restart    # Restart everything
npm run test:python:smoke   # Quick smoke test
```

---

## 📚 Key Documentation

**Must Read (In Order):**

1. `docs/00-README.md` - Documentation hub
2. `docs/01-SETUP_AND_OVERVIEW.md` - Quick start
3. `docs/02-ARCHITECTURE_AND_DESIGN.md` - System design
4. `docs/04-DEVELOPMENT_WORKFLOW.md` - Dev workflow & git
5. `docs/05-AI_AGENTS_AND_INTEGRATION.md` - Agent patterns
6. `docs/07-BRANCH_SPECIFIC_VARIABLES.md` - **NEW: Environment configuration per branch**

**Reference:**

- `docs/guides/BRANCH_SETUP_COMPLETE.md` - Branch-specific environments (feat/dev/main) with auto-selection
- `docs/reference/DEPLOYMENT_COMPLETE.md` - Complete deployment guide: Strapi architecture, Vercel config, pre-deployment checklist
- `docs/reference/CI_CD_COMPLETE.md` - CI/CD pipelines, GitHub Actions workflows, testing, linting
- `docs/guides/FIXES_AND_SOLUTIONS.md` - All critical fixes: timeout protection, Strapi fallbacks, security headers
- `docs/guides/STRAPI_BACKED_PAGES_GUIDE.md` - Detailed Strapi page setup
- `docs/guides/CONTENT_POPULATION_GUIDE.md` - Blog post templates
- `docs/07-BRANCH_SPECIFIC_VARIABLES.md` - Complete guide to branch-specific environments

**Environment Files:**

- `.env` - Local development (NEVER commit)
- `.env.staging` - Staging environment (commit, no secrets)
- `.env.production` - Production environment (commit, no secrets)
- `.env.example` - Template for all environments

**GitHub Actions Workflows:**

- `.github/workflows/test-on-feat.yml` - Test feature branches
- `.github/workflows/deploy-staging.yml` - Deploy dev branch to staging
- `.github/workflows/deploy-production.yml` - Deploy main branch to production

---

## 🎯 Golden Rules

1. **Always use 10-second timeout** in API calls → prevents Vercel hangs
2. **Always include markdown fallbacks** in page getStaticProps → graceful degradation
3. **Always run `npm run dev` from root** → not from workspaces
4. **Always update existing documentation** → don't create new summary files (see "Documentation Maintenance Workflow")
5. **Always test locally first** → before pushing to main (auto-deploys to Vercel)
6. **Always check Strapi connectivity** → before debugging frontend issues
7. **Always configure ISR revalidation** → set `revalidate: 60` in getStaticProps
8. **Always remember: GitLab (source) → GitHub (mirror) → Deployment** → this is the prod pipeline
9. **Always verify environment variables** → especially `NEXT_PUBLIC_STRAPI_API_URL` on Vercel before debugging production issues

---

## 🔑 Key Files by Purpose

| File                                        | Purpose                                              |
| ------------------------------------------- | ---------------------------------------------------- |
| `web/public-site/lib/api.js`                | Strapi API client with timeout protection (CRITICAL) |
| `web/public-site/pages/index.js`            | Homepage - shows how to fetch posts with fallback    |
| `src/cofounder_agent/main.py`               | AI orchestrator entry point                          |
| `src/cofounder_agent/orchestrator_logic.py` | Agent routing logic                                  |
| `package.json` (root)                       | Workspace orchestration & scripts                    |
| `docs/02-ARCHITECTURE_AND_DESIGN.md`        | System architecture & design patterns                |
| `docs/04-DEVELOPMENT_WORKFLOW.md`           | Git workflow & development process                   |
| `docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md`  | Production deployment guide                          |

---

## ⚙️ Environment Variables

**Frontend (.env.local):**

```
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
NEXT_PUBLIC_STRAPI_API_TOKEN=your-token-here
```

**Backend (.env):**

```
DATABASE_URL=postgresql://...
STRAPI_API_TOKEN=your-token
ANTHROPIC_API_KEY=your-key
OPENAI_API_KEY=your-key
```

See `.env.example` for complete reference.

---

## 🚀 When Starting

1. Read this file completely
2. Run `npm run dev` to start all services
3. Verify: http://localhost:3000 (public site loads)
4. Review `docs/02-ARCHITECTURE_AND_DESIGN.md` for system overview
5. Read `docs/04-DEVELOPMENT_WORKFLOW.md` for git workflow
6. Check recent commits to understand recent changes

---

**Questions?** Check the docs first - they're comprehensive and indexed at `docs/00-README.md`.
