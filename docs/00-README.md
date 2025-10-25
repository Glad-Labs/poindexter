# 📚 GLAD Labs Documentation Hub

**Last Updated:** October 25, 2025  
**Status:** ✅ Production Ready  
**Documentation Policy:** 🎯 HIGH-LEVEL ONLY (Architecture-Focused, Maintenance-Friendly)

> **Policy:** This hub contains only high-level, architecture-stable documentation. Implementation details belong in code. Feature how-tos belong in code comments. Status updates are not maintained. This keeps documentation focused on what matters: system design, deployment, operations, and AI agent orchestration.

---

## 🎯 Core Documentation - 8 Essential Files

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

### Components & Troubleshooting

- **[Component Docs](./components/)** - Architecture of individual services (Strapi, Co-founder Agent, Oversight Hub, Public Site)
- **[Troubleshooting Guides](./components/)** - Common issues and solutions for each component

### Technical References

- **[API Contracts](./reference/API_CONTRACT_CONTENT_CREATION.md)** - Content creation API specification
- **[Database Schemas](./reference/data_schemas.md)** - Data model definitions
- **[GLAD Labs Standards](./reference/GLAD-LABS-STANDARDS.md)** - Code quality and naming conventions
- **[GitHub Secrets Setup](./reference/GITHUB_SECRETS_SETUP.md)** - Production secrets configuration
- **[Testing Guide](./reference/TESTING.md)** - Comprehensive testing strategies (93+ tests)
- **[CI/CD Reference](./reference/ci-cd/)** - GitHub Actions workflows and branch strategy

---

## 🎓 Learning Paths by Role

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

GLAD Labs uses a **HIGH-LEVEL DOCUMENTATION ONLY** approach:

- ✅ **Core docs (00-07):** Architecture-level guidance that stays relevant
- ✅ **Technical references:** API specs, schemas, standards, **testing**
- ✅ **Focused troubleshooting:** Common issues with solutions
- ❌ **No feature guides:** Code demonstrates how to use features
- ❌ **No status updates:** Unnecessary maintenance burden
- ❌ **No duplicate content:** Consolidate into core docs

This keeps documentation clean, maintainable, and useful.

---

## 🔄 Last Updated

**Date:** October 23, 2025  
**Status:** ✅ Phase 1-5 Complete | High-Level Documentation Only  
**Core Docs:** 8 files | **Components:** 4 + troubleshooting | **Reference:** 5 files | **Archive:** 16 files  
**Total:** 18 active files | **Organization:** 95% ✨

---

## 🔧 Troubleshooting Guides

Component-specific troubleshooting guides are organized by component:

| Component            | Troubleshooting Guide                                                                             | Common Issues                                             |
| -------------------- | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| **Strapi CMS**       | [docs/components/strapi-cms/troubleshooting/](./components/strapi-cms/troubleshooting/)           | Plugin incompatibilities, build errors, connection issues |
| **Co-Founder Agent** | [docs/components/cofounder-agent/troubleshooting/](./components/cofounder-agent/troubleshooting/) | API errors, model routing, memory issues                  |
| **Oversight Hub**    | [docs/components/oversight-hub/troubleshooting/](./components/oversight-hub/troubleshooting/)     | State management, API integration, UI issues              |
| **Public Site**      | [docs/components/public-site/troubleshooting/](./components/public-site/troubleshooting/)         | Build errors, data fetching, SEO issues                   |

**Quick Links:**

- 🔴 **Strapi v5 Plugin Issue?** → [STRAPI_V5_PLUGIN_ISSUE.md](./components/strapi-cms/troubleshooting/STRAPI_V5_PLUGIN_ISSUE.md)
- 🔴 **Frontend Build Error?** → Check [components/public-site/troubleshooting/](./components/public-site/troubleshooting/)
- 🔴 **Backend Issues?** → Check [components/cofounder-agent/troubleshooting/](./components/cofounder-agent/troubleshooting/)

---

\*\*👉 Pick your role above and start reading!
