# GLAD Labs Copilot Instructions

**Last Updated:** October 20, 2025  
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
```

### Deployment

```bash
# Frontend deploys automatically to Vercel on push to main
git push origin main

# Check Vercel deployment: https://vercel.com/dashboard

# Monitor: `web/public-site/.vercel/README.json` after build
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
const timeout = setTimeout(() => controller.abort(), 10000);  // 10 seconds
```

**Before modifying API client:** Read `docs/RECENT_FIXES/TIMEOUT_FIX_SUMMARY.md`

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
    return { props: { data: null }, revalidate: 60 };  // Fallback
  }
}

export default function Page({ data }) {
  const content = data || fallbackMarkdownContent;  // Always fallback
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
- ✅ Review `docs/RECENT_FIXES/TIMEOUT_FIX_SUMMARY.md`
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

**Reference:**
- `STRAPI_ARCHITECTURE_CORRECTION.md` - Strapi-backed pages guide
- `docs/guides/STRAPI_BACKED_PAGES_GUIDE.md` - Detailed setup
- `docs/RECENT_FIXES/TIMEOUT_FIX_SUMMARY.md` - Timeout issue explanation
- `QUICK_REFERENCE.md` - At-a-glance deployment status

---

## 🎯 Golden Rules

1. **Always use 10-second timeout** in API calls → prevents Vercel hangs
2. **Always include markdown fallbacks** in page getStaticProps → graceful degradation
3. **Always run `npm run dev` from root** → not from workspaces
4. **Always commit documentation** when changing architecture
5. **Always test locally first** → before pushing to main (auto-deploys to Vercel)
6. **Always check Strapi connectivity** → before debugging frontend issues
7. **Always configure ISR revalidation** → set `revalidate: 60` in getStaticProps

---

## 🔑 Key Files by Purpose

| File | Purpose |
|------|---------|
| `web/public-site/lib/api.js` | Strapi API client with timeout protection (CRITICAL) |
| `web/public-site/pages/index.js` | Homepage - shows how to fetch posts with fallback |
| `src/cofounder_agent/main.py` | AI orchestrator entry point |
| `src/cofounder_agent/orchestrator_logic.py` | Agent routing logic |
| `package.json` (root) | Workspace orchestration & scripts |
| `docs/02-ARCHITECTURE_AND_DESIGN.md` | System architecture & design patterns |
| `docs/04-DEVELOPMENT_WORKFLOW.md` | Git workflow & development process |
| `docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md` | Production deployment guide |

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
