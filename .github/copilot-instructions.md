# 🤖 GitHub Copilot Instructions

**Last Updated:** October 23, 2025  
**Project:** GLAD Labs AI Co-Founder System  
**Status:** Production Ready | High-Level Documentation Policy Active

---

## 🎯 Project Overview

**GLAD Labs** is an enterprise-grade AI Co-Founder system combining:

- **Autonomous AI Agents** - Content, Financial, Compliance, Market Insight agents
- **Intelligent Orchestration** - Multi-agent coordination via Model Context Protocol (MCP)
- **Business Intelligence Dashboard** - Real-time metrics and AI-powered insights
- **Voice Interface** - Natural language interaction with the AI co-founder
- **Content Management** - Strapi v5 headless CMS with advanced content generation
- **Multi-Frontend Architecture** - Next.js applications (Public Site, Oversight Hub)

**Monorepo Structure:**

```
glad-labs-website/
├── cms/                 # Strapi v5 CMS backend
├── web/                 # Frontend applications
│   ├── oversight-hub/   # Admin/management dashboard (React/Next.js)
│   └── public-site/     # Public-facing website (Next.js)
├── src/                 # Python backend & AI agents
│   ├── agents/          # Specialized AI agents
│   ├── cofounder_agent/ # Main AI co-founder (FastAPI + orchestration)
│   └── mcp/             # Model Context Protocol servers
├── cloud-functions/     # GCP cloud functions (event-driven tasks)
└── docs/                # Comprehensive documentation (Phase 1 Consolidated)
```

---

## 📋 Technology Stack

| Layer                  | Technology                                             | Status         |
| ---------------------- | ------------------------------------------------------ | -------------- |
| **Frontend**           | Next.js 15, React, TypeScript, Tailwind CSS            | ✅ Running     |
| **CMS**                | Strapi v5, TypeScript, PostgreSQL                      | ✅ Running     |
| **Python Backend**     | FastAPI, Uvicorn, Python 3.12                          | ✅ Running     |
| **AI Orchestration**   | Model Context Protocol (MCP), Multi-agent coordination | ✅ Active      |
| **Database**           | PostgreSQL (Strapi), SQLite (local)                    | ✅ Configured  |
| **Deployment**         | Railway (backend), Vercel (frontends), GCP (functions) | ✅ Ready       |
| **Package Management** | npm (Node.js), pip (Python), hybrid strategy           | ✅ Implemented |
| **CI/CD**              | GitLab CI/CD, GitHub Actions (optional)                | ✅ Configured  |

---

## 🚀 Current System Status

### ✅ Running Services

- **Strapi CMS** - `npm run develop` at `cms/strapi-v5-backend/` (port 1337)
- **Oversight Hub** - `npm start` at `web/oversight-hub/` (port 3000+)
- **Public Site** - `npm run dev` at `web/public-site/` (port 3000+)
- **Co-Founder Agent** - `python -m uvicorn src.cofounder_agent.main:app --reload` (FastAPI on port 8000)
- **Intervene Trigger** - Python cloud function for event processing

### 📊 Project Status

- **Deployment:** Production Ready v3.0
- **Last Update:** October 22, 2025
- **Documentation:** Phase 1 Consolidation Complete (45% → 65% organization score)
- **Architecture:** Enterprise-grade monorepo with MCP integration
- **Testing:** Comprehensive test suite with CI/CD automation
- **Standards:** GLAD Labs Standards v2.0 Compliant

---

## 📚 Key Documentation

### Primary Resources (Start Here)

1. **[Docs Hub](../docs/00-README.md)** - Main navigation for all documentation
2. **[Setup Guide](../docs/01-SETUP_AND_OVERVIEW.md)** - Getting started and dependencies
3. **[Architecture](../docs/02-ARCHITECTURE_AND_DESIGN.md)** - System design and components
4. **[Deployment](../docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** - Cloud setup and environments
5. **[Development Workflow](../docs/04-DEVELOPMENT_WORKFLOW.md)** - Git strategy and testing
6. **[AI Agents](../docs/05-AI_AGENTS_AND_INTEGRATION.md)** - Agent orchestration and MCP
7. **[Operations](../docs/06-OPERATIONS_AND_MAINTENANCE.md)** - Production monitoring
8. **[Branch Variables](../docs/07-BRANCH_SPECIFIC_VARIABLES.md)** - Environment config

### Quick References

- **Component Docs:** [docs/components/](../docs/components/)
- **API Specs & References:** [docs/reference/](../docs/reference/)

---

## 🔧 Development Workflow

### Prerequisites

- **Node.js** 18.x - 22.x (❌ Not 25+; use .nvmrc files)
- **Python** 3.12+ with pip and venv
- **PostgreSQL** (for Strapi; local development can use SQLite)
- **Git** with SSH keys configured

### Quick Setup

```bash
# Clone repository
git clone <repo-url>
cd glad-labs-website

# Install all dependencies
npm run setup:all
```

### Running Locally

**Option 1: Run all services**

```bash
npm run start:all
# Or use VS Code task: "Start All Services"
```

**Option 2: Run individual services**

```bash
# Terminal 1: Strapi CMS
cd cms/strapi-v5-backend && npm run develop

# Terminal 2: Oversight Hub
cd web/oversight-hub && npm start

# Terminal 3: Public Site
cd web/public-site && npm run dev

# Terminal 4: Co-Founder Agent
cd src/cofounder_agent && python -m uvicorn main:app --reload
```

### Development Best Practices

#### Code Quality

- **Linting:** Run before commit: `npm run lint`
- **Formatting:** Use Prettier: `npm run format`
- **Tests:** Run test suite: `npm run test`
- **Type Checking:** TypeScript in web projects, Pylint/MyPy in Python

#### Git Workflow

```bash
# Branch naming
main              # Production releases
dev               # Active development
feature/[name]    # New features
bugfix/[name]     # Bug fixes
docs/[name]       # Documentation updates

# Commit messages (conventional)
feat: add new feature
fix: resolve bug
docs: update documentation
refactor: improve code structure
test: add test cases
```

#### Version Pinning

- Node.js: Use `.nvmrc` (currently pinned to 22.11.0)
- Python: Use `requirements.txt` with exact versions
- npm: Use `package-lock.json` (committed to repo)

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
cd cms/strapi-v5-backend
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
- `cms/strapi-v5-backend/tests/` (if applicable)
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

GLAD Labs maintains a **HIGH-LEVEL ONLY** documentation approach to reduce maintenance burden and prevent documentation staleness as the codebase evolves.

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
cd cms/strapi-v5-backend
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
| **Version**      | 1.0                                            |
| **Last Updated** | October 22, 2025                               |
| **Next Review**  | December 22, 2025 (quarterly)                  |
| **Author**       | GitHub Copilot & GLAD Labs Team                |
| **Status**       | Active & Maintained                            |
| **Audience**     | All team members (developers, DevOps, QA, PMs) |

---

**🚀 Ready to code? Start with the [Setup Guide](../docs/01-SETUP_AND_OVERVIEW.md)!**
