# ��� **Glad Labs AI Co-Founder System**

![Production Ready](https://img.shields.io/badge/Status-Production_Ready-brightgreen)
![Glad Labs Standards](https://img.shields.io/badge/Standards-v2.0_Compliant-blue)
![Next.js](https://img.shields.io/badge/Frontend-Next.js_15-black)
![Python](https://img.shields.io/badge/Backend-Python_3.12-blue)
![AI Powered](https://img.shields.io/badge/AI-Powered_Co--Founder-purple)

> **Revolutionary AI-powered business co-founder system featuring autonomous agents, intelligent orchestration, and comprehensive business intelligence - delivering the world's first complete AI business partner.**

## **��� Documentation**

��� **[��� START HERE: Documentation Hub](./docs/00-README.md)** - Complete guide with setup, architecture, deployment, operations, and troubleshooting.

**Core Documentation (7 files in `docs/` folder):**
- [01 Setup & Overview](./docs/01-SETUP_AND_OVERVIEW.md) - Quick start guide (15 minutes)
- [02 Architecture & Design](./docs/02-ARCHITECTURE_AND_DESIGN.md) - System design and components
- [03 Deployment & Infrastructure](./docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md) - Production deployment
- [04 Development Workflow](./docs/04-DEVELOPMENT_WORKFLOW.md) - Git strategy, testing, CI/CD
- [05 AI Agents & Integration](./docs/05-AI_AGENTS_AND_INTEGRATION.md) - Agent architecture and MCP
- [06 Operations & Maintenance](./docs/06-OPERATIONS_AND_MAINTENANCE.md) - Monitoring and backups
- [07 Branch-Specific Variables](./docs/07-BRANCH_SPECIFIC_VARIABLES.md) - Environment configuration

---

## **⚡ Quick Start**

### **Prerequisites**
- Node.js 18+ and Python 3.12+
- Git

### **Installation (3 steps)**

```bash
# 1. Clone the repository
git clone <repository-url> && cd glad-labs-website

# 2. Install all dependencies
npm run setup:all

# 3. Start all services
npm run dev
```

### **Access Points**

| Service | URL | Purpose |
|---------|-----|---------|
| **Public Site** | http://localhost:3000 | Next.js website |
| **Oversight Hub** | http://localhost:3001 | React admin dashboard |
| **AI Co-Founder** | http://localhost:8000 | Python API server |

---

## **���️ System Overview**

| Service | Technology | Purpose |
|---------|-----------|---------|
| **Public Site** | Next.js 15 | High-performance public website |
| **Oversight Hub** | React 18 | Admin interface for AI management |
| **AI Co-Founder** | Python 3.12 | AI business intelligence system |
| **Content Agent** | Python | Autonomous content creation |

**Project Structure:**
```
glad-labs-website/
├── web/                    # Frontend applications
│   ├── public-site/        # Next.js 15 website
│   └── oversight-hub/      # React dashboard
├── src/                    # Backend services
│   ├── cofounder_agent/    # AI Co-Founder
│   └── mcp/                # Model Context Protocol
└── docs/                   # Documentation (start here!)
```

---

## **��� License**

**GNU Affero General Public License v3.0 (AGPL 3.0)** - See [LICENSE.md](./LICENSE.md)

**Commercial License:** Contact sales@gladlabs.io

---

## **��� Support**

- **Documentation:** [��� Documentation Hub](./docs/00-README.md)
- **Issues:** GitHub issues
- **Commercial:** sales@gladlabs.io

**Owner:** Matthew M. Gladding | **Organization:** Glad Labs, LLC
