# 📚 Glad Labs Documentation Hub

<<<<<<< HEAD
**Last Updated:** November 5, 2025  
**Status:** ✅ Production Ready | 267 Tests Passing | v3.0  
**Structure:** 8 Core Docs | Organized Reference & Components | Archive  
**Policy:** 🎯 **HIGH-LEVEL ONLY** (Architecture-focused, low maintenance)
=======
**Last Updated:** December 23, 2025 (HIGH-LEVEL ONLY Policy Enforcement Complete)  
**Status:** ✅ All 8 Core Docs Complete | 271 Files Archived | Production Ready  
**Documentation Policy:** 🎯 HIGH-LEVEL ONLY (Architecture-Focused, Zero Maintenance Burden)

> **Policy:** This hub contains only high-level, architecture-stable documentation. Implementation details belong in code. Feature how-tos belong in code comments. Status updates are not maintained. This keeps documentation focused on what matters: system design, deployment, operations, and AI agent orchestration.
>>>>>>> feat/refine

**Session Consolidation:**

<<<<<<< HEAD
## 🎯 The 8 Core Docs - Your Starting Point
=======
- **December 23:** 11 files archived (LLM selection guides, session summaries, navigation guides, meta-documentation)
- **December 19:** 118 files archived (session summaries, implementation plans, analysis documents)  
  All files preserved with timestamp prefixes for audit trail.

---

## 🎯 Core Documentation - 8 Essential Files
>>>>>>> feat/refine

Start with any doc that matches your role, then use cross-links to explore. Each doc is self-contained and high-level.

### 📖 Getting Started (Pick Your Entry Point)

| Need                         | Start Here                                                                |
| ---------------------------- | ------------------------------------------------------------------------- |
| 🚀 **New Developer**         | [01 - Setup & Overview](./01-SETUP_AND_OVERVIEW.md)                       |
| 🏗️ **Understand System**     | [02 - Architecture & Design](./02-ARCHITECTURE_AND_DESIGN.md)             |
| 🌐 **Deploy to Cloud**       | [03 - Deployment & Infrastructure](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md) |
| 🔄 **Development Process**   | [04 - Development Workflow](./04-DEVELOPMENT_WORKFLOW.md)                 |
| 🧠 **AI Agents**             | [05 - AI Agents & Integration](./05-AI_AGENTS_AND_INTEGRATION.md)         |
| 📊 **Production Operations** | [06 - Operations & Maintenance](./06-OPERATIONS_AND_MAINTENANCE.md)       |
| ⚙️ **Environment Config**    | [07 - Branch-Specific Variables](./07-BRANCH_SPECIFIC_VARIABLES.md)       |

### 📋 All 8 Core Docs at a Glance

| #      | Document                                                             | Purpose                                                       | For Whom               |
| ------ | -------------------------------------------------------------------- | ------------------------------------------------------------- | ---------------------- |
| **00** | [Documentation Hub](./00-README.md)                                  | Navigation (you are here)                                     | Everyone               |
| **01** | [Setup & Overview](./01-SETUP_AND_OVERVIEW.md)                       | Prerequisites, local development, quick start                 | Developers, DevOps     |
| **02** | [Architecture & Design](./02-ARCHITECTURE_AND_DESIGN.md)             | System design, component relationships, AI agents, tech stack | Architects, Tech Leads |
| **03** | [Deployment & Infrastructure](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md) | Cloud deployment, environments, scaling, CI/CD                | DevOps, Infrastructure |
| **04** | [Development Workflow](./04-DEVELOPMENT_WORKFLOW.md)                 | Git strategy, testing, PR process, release procedure          | All Developers         |
| **05** | [AI Agents & Integration](./05-AI_AGENTS_AND_INTEGRATION.md)         | Agent architecture, MCP integration, orchestration            | AI/Agent Developers    |
| **06** | [Operations & Maintenance](./06-OPERATIONS_AND_MAINTENANCE.md)       | Production monitoring, backups, troubleshooting               | DevOps, SREs           |
| **07** | [Branch Variables & Config](./07-BRANCH_SPECIFIC_VARIABLES.md)       | Environment-specific settings, secrets management             | DevOps, Platform Eng   |

---

<<<<<<< HEAD
## 📂 Documentation Structure

This hub contains only the 8 essential core docs. All other materials are organized in subfolders:

### 📖 `/reference/` - Technical Specifications
=======
## 🏛️ Enterprise Documentation Framework

**New (Dec 19):** [Enterprise Documentation Framework](./ENTERPRISE_DOCUMENTATION_FRAMEWORK.md)

This document outlines the professional documentation standards used at Glad Labs:

- ✅ Documentation strategy and philosophy (HIGH-LEVEL ONLY)
- ✅ Folder structure and organization
- ✅ Documentation categories and maintenance responsibilities
- ✅ Decision record template for architectural decisions
- ✅ Quality metrics and success criteria
- ✅ Maintenance schedule and governance
- ✅ Anti-patterns to avoid
- ✅ Future documentation roadmap

**For documentation maintainers:** Start here to understand policy and standards.

---

## 📚 Additional Resources

### Architectural Decisions (What We Decided & Why)

- **[Master Decision Index](./decisions/DECISIONS.md)** - All major decisions at a glance
- **[Why FastAPI?](./decisions/WHY_FASTAPI.md)** - Framework selection rationale
- **[Why PostgreSQL?](./decisions/WHY_POSTGRESQL.md)** - Database selection rationale

### Components

- **[Co-founder Agent](./components/cofounder-agent/README.md)** - AI agent architecture and integration
- **[Oversight Hub](./components/oversight-hub/README.md)** - Admin dashboard and monitoring
- **[Public Site](./components/public-site/README.md)** - Customer-facing website

### Troubleshooting & Support

- **[Troubleshooting Guides](./troubleshooting/README.md)** - Common issues and solutions
- **[Decisions Log](./decisions/)** - Architectural decisions and trade-offs

### Historical Documentation

- **[Archive (Old Files)](./archive-old/)** - Historical session notes, status updates, and superseded guides
  - **December 23 Cleanup:** 11 files (LLM selection guides, session summaries, navigation guides, meta-documentation)
  - **December 19 Session:** 118 files (session summaries, implementation plans, analysis documents)
  - **Previous sessions:** 142 files (from October-December sessions)
  - **Total archived:** 271 files with timestamp prefixes for audit trail
  - **Status:** "Not maintained—reference only for historical context"
>>>>>>> feat/refine

Deep-dive technical materials:
- `TESTING.md` - Complete testing guide (93+ tests)
- `API_CONTRACT_*.md` - API specifications
- `GLAD-LABS-STANDARDS.md` - Code standards & naming
- `data_schemas.md` - Database schemas
- `GITHUB_SECRETS_SETUP.md` - Production secrets
- And more technical references

<<<<<<< HEAD
### 🧩 `/components/` - Per-Component Documentation

Individual service documentation:
- `strapi-cms/` - CMS architecture & troubleshooting
- `cofounder-agent/` - AI agent system details
- `oversight-hub/` - Admin dashboard docs
- `public-site/` - Public website docs

Each component includes: architecture, troubleshooting, setup, testing, and deployment guides.

### 🔧 `/troubleshooting/` - Problem Solutions

Quick solutions for common issues:
- Railway deployment problems
- Database migration issues
- GitHub Actions failures
- Build and compilation errors
- Component-specific problems

### 📦 `/archive/` - Historical Documentation

Read-only archive of historical documents:
- Session work logs (organized by date)
- Phase completion reports
- Project decision history
- Reference snapshots

**Note:** Archive files are not maintained. For current information, see the 8 core docs above.
=======
- **[API Contracts](./reference/API_CONTRACTS.md)** - Content creation and other API specifications
- **[Database Schemas](./reference/data_schemas.md)** - Data model definitions
- **[Glad Labs Standards](./reference/GLAD-LABS-STANDARDS.md)** - Code quality and naming conventions
- **[GitHub Secrets Setup](./reference/GITHUB_SECRETS_SETUP.md)** - Production secrets configuration
- **[Testing Guide](./reference/TESTING.md)** - Comprehensive testing strategies (93+ tests)
- **[CI/CD Reference](./reference/ci-cd/)** - GitHub Actions workflows and branch strategy
>>>>>>> feat/refine

---

## 🏗️ Documentation Structure Overview

**Enterprise-Grade Documentation** (December 23, 2025 - HIGH-LEVEL ONLY Policy Enforcement Complete)

- ✅ **Core Docs (00-07):** 8 files, 100% high-level architecture
- ✅ **Technical Reference:** 8+ essential specs (no duplicates)
- ✅ **Troubleshooting:** 4-5 focused guides + component-specific
- ✅ **Decisions:** 3 architectural decision records (Why FastAPI, Why PostgreSQL, Master Index)
- ✅ **Root Level:** Clean - README.md, LICENSE, config files only
- ✅ **Archive:** 271 files (11 from Dec 23 + 118 from Dec 19 + 142 previous)
- ✅ **Policy Enforcement:** 100% - HIGH-LEVEL ONLY across entire project
- 📊 **Total Active:** ~30 essential files
- 🎯 **Maintenance:** MINIMAL (stable, architecture-focused content only)

**December 23 Cleanup:**

- 11 violation files archived (session summaries, implementation checklists, temporary guides)
- Policy enforcement: 100% HIGH-LEVEL ONLY compliance achieved
- Documentation now architecture-focused with zero maintenance burden

### 👨‍💻 For Developers (First Week)

1. **Get Started:** [01-SETUP_AND_OVERVIEW.md](./01-SETUP_AND_OVERVIEW.md) - Local setup (15 min)
2. **Learn System:** [02-ARCHITECTURE_AND_DESIGN.md](./02-ARCHITECTURE_AND_DESIGN.md) - System design (40 min)
3. **Development:** [04-DEVELOPMENT_WORKFLOW.md](./04-DEVELOPMENT_WORKFLOW.md) - Git & testing (30 min)
4. **Your Component:** [components/](./components/) - Your service
5. **Testing:** [reference/TESTING.md](./reference/TESTING.md) - Test patterns (30+ min)

**Time Required:** 2-3 hours

### 🚀 For DevOps/Infrastructure

1. **Architecture First:** [02-ARCHITECTURE_AND_DESIGN.md](./02-ARCHITECTURE_AND_DESIGN.md) - Know the system (1 hour)
2. **Deployment:** [03-DEPLOYMENT_AND_INFRASTRUCTURE.md](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md) - Cloud setup (2 hours)
3. **Environment Config:** [07-BRANCH_SPECIFIC_VARIABLES.md](./07-BRANCH_SPECIFIC_VARIABLES.md) - Secrets (1 hour)
4. **Operations:** [06-OPERATIONS_AND_MAINTENANCE.md](./06-OPERATIONS_AND_MAINTENANCE.md) - Monitoring (2+ hours)
5. **CI/CD:** [reference/ci-cd/](./reference/ci-cd/) - GitHub Actions

**Time Required:** 1-2 days

### 🧠 For AI/Agent Developers

1. **Setup:** [01-SETUP_AND_OVERVIEW.md](./01-SETUP_AND_OVERVIEW.md) - Get running locally (30 min)
2. **Agent Architecture:** [05-AI_AGENTS_AND_INTEGRATION.md](./05-AI_AGENTS_AND_INTEGRATION.md) - Agent design (2 hours)
3. **System Design:** [02-ARCHITECTURE_AND_DESIGN.md](./02-ARCHITECTURE_AND_DESIGN.md) - Integration points (1 hour)
4. **Agent Code:** [components/cofounder-agent/](./components/cofounder-agent/) - Implementation

**Time Required:** 2-3 days

---

## 📋 Documentation Philosophy

Glad Labs uses a **HIGH-LEVEL DOCUMENTATION ONLY** approach:

✅ **We Document:**
- Architecture decisions and system design
- Deployment and operations procedures
- Code standards and testing strategies
- API contracts and data schemas

❌ **We Don't Document:**
- Feature how-tos (code demonstrates the feature)
- Status updates (implementation details change too fast)
- Session notes (not useful long-term)
- Duplicate content (consolidate into one authoritative source)

This keeps documentation clean, maintainable, and always relevant.

---

## ✅ Documentation Maintenance

### Update Schedule

- **Core Docs (00-07):** Quarterly reviews (next: Feb 5, 2026)
- **Reference Docs:** Updated as-needed when systems change
- **Component Docs:** Updated per release
- **Archive:** Read-only (historical reference)

<<<<<<< HEAD
### Quality Standards

- ✅ All links working (verified Nov 5, 2025)
- ✅ Code examples current
- ✅ Zero documentation debt
- ✅ No duplicate content
- ✅ No outdated information

---

## 🚀 Getting Started

### New to Glad Labs?

1. **Pick your role** from the entry point table above
2. **Read the first doc** (usually 15-30 minutes)
3. **Follow the learning path** for your role
4. **Reference other docs** as needed
5. **Check troubleshooting** if you hit issues

### Quick Commands

```bash
# Start all services
npm run dev

# Run tests
npm test

# Lint and format
npm run lint -- --fix
npm run format
```

---

## 🔗 Quick Links

| Resource | Link |
|----------|------|
| **Main Setup Guide** | [01-SETUP_AND_OVERVIEW.md](./01-SETUP_AND_OVERVIEW.md) |
| **System Architecture** | [02-ARCHITECTURE_AND_DESIGN.md](./02-ARCHITECTURE_AND_DESIGN.md) |
| **Deployment** | [03-DEPLOYMENT_AND_INFRASTRUCTURE.md](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md) |
| **Testing Guide** | [reference/TESTING.md](./reference/TESTING.md) |
| **Component Docs** | [components/](./components/) |
| **Troubleshooting** | [troubleshooting/](./troubleshooting/) |
=======
- **[Railway Deployment Failures](./troubleshooting/01-railway-deployment.md)** - Deploy errors, configuration, Docker build issues
- **[Build Errors](./troubleshooting/04-build-fixes.md)** - Node.js and Python build failures
- **[Compilation Issues](./troubleshooting/05-compilation.md)** - TypeScript and Python compilation errors

### Component-Specific Troubleshooting

- **[Co-Founder Agent Issues](./components/cofounder-agent/troubleshooting/)** - API, model routing, memory
- **[Oversight Hub Issues](./components/oversight-hub/troubleshooting/)** - State, API integration
- **[Public Site Issues](./components/public-site/troubleshooting/)** - Build, data fetching

---

## 📚 Technical Reference

Technical specifications and standards for developers and architects:

### API & Data

- **[API Contracts](./reference/API_CONTRACTS.md)** - API specifications and contracts
- **[Database Schemas](./reference/data_schemas.md)** - Complete data model definitions
- **[GitHub Secrets Setup](./reference/GITHUB_SECRETS_SETUP.md)** - Production secrets configuration

### Standards & Best Practices

- **[Glad Labs Standards](./reference/GLAD-LABS-STANDARDS.md)** - Code quality, naming conventions, best practices
- **[Testing Standards](./reference/TESTING.md)** - Comprehensive testing strategies (93+ tests)
- **[GitHub Actions Reference](./reference/ci-cd/GITHUB_ACTIONS_REFERENCE.md)** - CI/CD workflows and automation
- **[Branch Strategy](./reference/ci-cd/BRANCH_HIERARCHY_QUICK_REFERENCE.md)** - Git branching strategy

---

## 📊 Documentation Status

**Last Updated:** December 12, 2025  
**Enforcement:** ✅ HIGH-LEVEL ONLY Policy 100% Enforced | ✅ Root Cleanup Complete | ✅ 46 Violation Files Archived

**Current Metrics:**

- Core Docs: 8 files (00-07) ✅
- Technical Reference: 8 essential specs ✅ (API, Schemas, Standards, Testing, CI/CD, GitHub Secrets)
- Troubleshooting: 4 focused guides + component-specific ✅
- Components: 3 service docs ✅
- Decisions: 3 architectural decision files ✅
- Root Directory: Clean - 2 files only (README.md, LICENSE.md) ✅ **[NEW: Cleanup Complete]**
- Archive: 46 violation files + 70+ historical files properly organized ✅ **[NEW: +46 Root Files Archived]**
- **Total Active Docs:** 28 essential files
- **Maintenance Burden:** MINIMAL (no duplicates, no status files, stable content only)
>>>>>>> feat/refine

---

## 📞 Documentation Status

<<<<<<< HEAD
**Last Verified:** November 5, 2025  
**Core Docs:** 8 files ✅  
**Reference Docs:** 13+ files ✅  
**Component Docs:** 4 services ✅  
**Archive:** 60+ historical files ✅  
**Broken Links:** 0 ✅  
**Outdated Content:** 0 ✅  
=======
Component-specific troubleshooting guides are organized by component:

| Component            | Troubleshooting Guide                                                                             | Common Issues                                |
| -------------------- | ------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| **Co-Founder Agent** | [docs/components/cofounder-agent/troubleshooting/](./components/cofounder-agent/troubleshooting/) | API errors, model routing, memory issues     |
| **Oversight Hub**    | [docs/components/oversight-hub/troubleshooting/](./components/oversight-hub/troubleshooting/)     | State management, API integration, UI issues |
| **Public Site**      | [docs/components/public-site/troubleshooting/](./components/public-site/troubleshooting/)         | Build errors, data fetching, SEO issues      |

**Quick Links:**

- 🔴 **Frontend Build Error?** → Check [components/public-site/troubleshooting/](./components/public-site/troubleshooting/)
- 🔴 **Backend Issues?** → Check [components/cofounder-agent/troubleshooting/](./components/cofounder-agent/troubleshooting/)
>>>>>>> feat/refine

---

**Ready to code? Start with [01-SETUP_AND_OVERVIEW.md](./01-SETUP_AND_OVERVIEW.md)!**
