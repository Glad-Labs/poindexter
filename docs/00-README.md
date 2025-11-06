# 📚 Glad Labs Documentation Hub

**Last Updated:** November 5, 2025  
**Status:** ✅ Production Ready | 267 Tests Passing | v3.0  
**Structure:** 8 Core Docs | Organized Reference & Components | Archive  
**Policy:** 🎯 **HIGH-LEVEL ONLY** (Architecture-focused, low maintenance)

---

## 🎯 The 8 Core Docs - Your Starting Point

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

## 📂 Documentation Structure

This hub contains only the 8 essential core docs. All other materials are organized in subfolders:

### 📖 `/reference/` - Technical Specifications

Deep-dive technical materials:
- `TESTING.md` - Complete testing guide (93+ tests)
- `API_CONTRACT_*.md` - API specifications
- `GLAD-LABS-STANDARDS.md` - Code standards & naming
- `data_schemas.md` - Database schemas
- `GITHUB_SECRETS_SETUP.md` - Production secrets
- And more technical references

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

---

## 🎓 Learning Paths by Role

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

---

## 📞 Documentation Status

**Last Verified:** November 5, 2025  
**Core Docs:** 8 files ✅  
**Reference Docs:** 13+ files ✅  
**Component Docs:** 4 services ✅  
**Archive:** 60+ historical files ✅  
**Broken Links:** 0 ✅  
**Outdated Content:** 0 ✅  

---

**Ready to code? Start with [01-SETUP_AND_OVERVIEW.md](./01-SETUP_AND_OVERVIEW.md)!**
