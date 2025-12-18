# 📚 Glad Labs Documentation Hub

**Last Updated:** December 17, 2025 (Root-Level Cleanup Complete)  
**Status:** ✅ All 8 Core Docs Complete | HIGH-LEVEL ONLY Policy 100% Enforced (142 Files Archived) | Production Ready  
**Documentation Policy:** 🎯 HIGH-LEVEL ONLY (Architecture-Focused, Zero Maintenance Burden)

> **Policy:** This hub contains only high-level, architecture-stable documentation. Implementation details belong in code. Feature how-tos belong in code comments. Status updates are not maintained. This keeps documentation focused on what matters: system design, deployment, operations, and AI agent orchestration.

---

## 🎯 Core Documentation - 8 Essential Files

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

## 📚 Additional Resources

### Components

- **[Co-founder Agent](./components/cofounder-agent/README.md)** - AI agent architecture and integration
- **[Oversight Hub](./components/oversight-hub/README.md)** - Admin dashboard and monitoring
- **[Public Site](./components/public-site/README.md)** - Customer-facing website

### Troubleshooting & Support

- **[Troubleshooting Guides](./troubleshooting/README.md)** - Common issues and solutions
- **[Decisions Log](./decisions/)** - Why we chose PostgreSQL, FastAPI, and other key decisions

### Historical Documentation

- **[Archive (Old Files)](../archive-old/)** - Historical session notes, status updates, and superseded guides
  - **Root archive:** 69 files (session summaries, implementation reports, Cloudinary/S3 guides, image storage documentation)
  - **Previous archives:** 73 files (from October-December sessions)
  - **Total archived:** 142 violation files eliminated from active codebase
  - **Session tagged:** `20251217_SESSION_IMAGE_STORAGE_AND_DOCS_*` (69 new files from today)
  - **Marked as:** "Not maintained—reference only"

### Technical References

- **[API Contracts](./reference/API_CONTRACTS.md)** - Content creation and other API specifications
- **[Database Schemas](./reference/data_schemas.md)** - Data model definitions
- **[Glad Labs Standards](./reference/GLAD-LABS-STANDARDS.md)** - Code quality and naming conventions
- **[GitHub Secrets Setup](./reference/GITHUB_SECRETS_SETUP.md)** - Production secrets configuration
- **[Testing Guide](./reference/TESTING.md)** - Comprehensive testing strategies (93+ tests)
- **[CI/CD Reference](./reference/ci-cd/)** - GitHub Actions workflows and branch strategy

---

## 🏗️ Documentation Structure Overview

**Clean, Maintainable Documentation** (December 12, 2025 - Subfolder Cleanup Complete)

- ✅ **Core Docs (00-07):** 8 files, 100% high-level architecture
- ✅ **Technical Reference:** 8 essential specs (no duplicates)
- ✅ **Troubleshooting:** 4 focused guides + component-specific
- ✅ **Root Level:** 0 violation files (73 archived across root + subfolders)
- ✅ **src/cofounder_agent/:** 0 violation files (25 archived to local archive/)
- ✅ **web/oversight-hub/:** 0 violation files (2 archived to local archive/)
- ✅ **docs/ Subfolders:** 100% compliant (decisions, reference, troubleshooting verified)
- ✅ **Policy Enforcement:** Complete across entire codebase
- ✅ **Components:** 3 service docs + architecture guides
- ✅ **Decisions:** 3 architectural decision records
- ✅ **Root Directory:** Clean - 2 files only (README.md, LICENSE.md) **[VERIFIED]**
- ✅ **Archive:** 46 root violation files + 70+ historical files archived **[NEW]**
- 📊 **Total Active:** 28 essential files
- 🎯 **Maintenance:** MINIMAL (stable, non-duplicated content only, policy enforced)

### 👨‍💻 For Developers (First Week)

1. **Get Started:** [01-SETUP_AND_OVERVIEW.md](./01-SETUP_AND_OVERVIEW.md) - Local setup in 15 minutes
2. **Learn System:** [02-ARCHITECTURE_AND_DESIGN.md](./02-ARCHITECTURE_AND_DESIGN.md) - Understand how components fit together
3. **Development:** [04-DEVELOPMENT_WORKFLOW.md](./04-DEVELOPMENT_WORKFLOW.md) - Git workflow, testing, CI/CD
4. **Your Component:** [components/](./components/) - Deep dive into your specific service
5. **Testing:** [reference/TESTING.md](./reference/TESTING.md) - Writing tests (93+ existing tests to learn from)

### 🚀 For DevOps/Infrastructure

1. **Architecture First:** [02-ARCHITECTURE_AND_DESIGN.md](./02-ARCHITECTURE_AND_DESIGN.md) - Know the system
2. **Deployment:** [03-DEPLOYMENT_AND_INFRASTRUCTURE.md](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md) - Cloud setup (Railway + Vercel)
3. **Environment Config:** [07-BRANCH_SPECIFIC_VARIABLES.md](./07-BRANCH_SPECIFIC_VARIABLES.md) - Secrets and variables
4. **Operations:** [06-OPERATIONS_AND_MAINTENANCE.md](./06-OPERATIONS_AND_MAINTENANCE.md) - Monitoring, backups, scaling
5. **CI/CD:** [reference/ci-cd/](./reference/ci-cd/) - GitHub Actions deep dive

### 🧠 For AI/Agent Developers

1. **Setup:** [01-SETUP_AND_OVERVIEW.md](./01-SETUP_AND_OVERVIEW.md) - Get system running locally
2. **Agent Architecture:** [05-AI_AGENTS_AND_INTEGRATION.md](./05-AI_AGENTS_AND_INTEGRATION.md) - Agent design, MCP, orchestration
3. **System Design:** [02-ARCHITECTURE_AND_DESIGN.md](./02-ARCHITECTURE_AND_DESIGN.md) - Integration points
4. **Agent Code:** [components/cofounder-agent/](./components/cofounder-agent/) - Agent implementation details

---

## 📋 Documentation Philosophy

Glad Labs uses a **HIGH-LEVEL DOCUMENTATION ONLY** approach:

- ✅ **Core docs (00-07):** Architecture-level guidance that stays relevant
- ✅ **Technical references:** API specs, schemas, standards, **testing**
- ✅ **Focused troubleshooting:** Common issues with solutions
- ❌ **No feature guides:** Code demonstrates how to use features
- ❌ **No status updates:** Unnecessary maintenance burden
- ❌ **No duplicate content:** Consolidate into core docs

This keeps documentation clean, maintainable, and useful.

---

## � Troubleshooting & Quick Solutions

Quick answers to common deployment and development problems:

### Deployment & Infrastructure Issues

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

---

## 🔧 Component Troubleshooting Guides

Component-specific troubleshooting guides are organized by component:

| Component            | Troubleshooting Guide                                                                             | Common Issues                                |
| -------------------- | ------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| **Co-Founder Agent** | [docs/components/cofounder-agent/troubleshooting/](./components/cofounder-agent/troubleshooting/) | API errors, model routing, memory issues     |
| **Oversight Hub**    | [docs/components/oversight-hub/troubleshooting/](./components/oversight-hub/troubleshooting/)     | State management, API integration, UI issues |
| **Public Site**      | [docs/components/public-site/troubleshooting/](./components/public-site/troubleshooting/)         | Build errors, data fetching, SEO issues      |

**Quick Links:**

- 🔴 **Frontend Build Error?** → Check [components/public-site/troubleshooting/](./components/public-site/troubleshooting/)
- 🔴 **Backend Issues?** → Check [components/cofounder-agent/troubleshooting/](./components/cofounder-agent/troubleshooting/)

---

\*\*👉 Pick your role above and start reading!
