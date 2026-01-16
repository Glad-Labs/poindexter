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

<<<<<<< HEAD
**Documentation Hub:**

- [� **00 Documentation Hub**](./docs/00-README.md) - Complete navigation guide with reference materials, troubleshooting, and learning paths

## **🎯 Executive Summary**

Glad Labs is a comprehensive AI Co-Founder ecosystem that combines autonomous content creation with intelligent business management. The system features a sophisticated AI Co-Founder that provides strategic insights, manages business operations, orchestrates specialized agents, and delivers real-time business intelligence through advanced dashboards and voice interfaces.

**Current Status:** ✅ **Production Ready v3.1** - PostgreSQL Migration in Progress  
**Last Updated:** October 26, 2025  
**Architecture:** Enterprise-grade monorepo with PostgreSQL + FastAPI + Next.js  
**Phase:** 5 - Final Cleanup & Testing Integration

---

## **🚀 Quick Start**

### **Prerequisites (Quick Start)**

- Node.js 18+ and Python 3.12+
- Git and a code editor

### **Installation**

```bash
# Clone the repository
git clone <repository-url>
cd glad-labs-website

# Install all dependencies (Python + Node.js)
npm run setup:all

# Start all services in development mode
npm run dev
```

### **Python Installation Options**

Glad Labs provides tiered Python installation files optimized for different scenarios. Choose based on your use case:

| Scenario                      | Command             | Size   | Purpose                            |
| ----------------------------- | ------------------- | ------ | ---------------------------------- |
| **Production / Deployments**  | `npm run setup`     | 500 MB | Lean production setup              |
| **GitHub Actions / CI/CD**    | `npm run setup:ci`  | 600 MB | Optimized for testing (fast)       |
| **Local Development**         | `npm run setup:dev` | 1 GB   | Full dev tools + testing           |
| **Local Dev + ML/Embeddings** | `npm run setup:ml`  | 9 GB   | Optional: semantic search features |

**Recommendation:** Use `npm run setup` for production and `npm run setup:ci` for CI/CD. This saves 8+ GB of disk space by excluding large ML packages that aren't needed for testing.

To manually use specific requirement files:

```bash
# Core only (production)
pip install -r scripts/requirements-core.txt

# CI/CD optimized
pip install -r scripts/requirements-ci.txt

# Development tools
pip install -r scripts/requirements-dev.txt

# Optional ML packages (semantic search, embeddings)
pip install -r scripts/requirements-ml.txt
```

### **Access Points**

| Service           | URL                     | Purpose               |
| ----------------- | ----------------------- | --------------------- |
| **Public Site**   | <http://localhost:3000> | Next.js website       |
| **Oversight Hub** | <http://localhost:3001> | React admin dashboard |
| **Strapi CMS**    | <http://localhost:1337> | Content management    |
| **AI Co-Founder** | <http://localhost:8000> | Python API server     |

### **Available Commands**

```bash
npm run dev           # Start all services
npm run build         # Build for production
npm test              # Run all tests
npm run lint          # Check code quality
```

## **🏗️ System Architecture**

The system is designed as a modern monorepo with clear separation of concerns and automated AI workflows.

| Service           | Technology  | Port | Status   | Description                       |
| ----------------- | ----------- | ---- | -------- | --------------------------------- |
| **Public Site**   | Next.js 15  | 3000 | ✅ Ready | High-performance public website   |
| **Oversight Hub** | React 18    | 3001 | ✅ Ready | Admin interface for AI management |
| **Strapi CMS**    | Strapi v5   | 1337 | ✅ Ready | Headless content management       |
| **AI Co-Founder** | Python 3.12 | 8000 | ✅ Ready | AI business intelligence system   |
| **Content Agent** | Python      | -    | ✅ Ready | Autonomous content creation       |

### **Workspace Structure**

```text
glad-labs-website/
├── 📁 web/
│ ├── public-site/ # Next.js 15 public website
│ └── oversight-hub/ # React admin dashboard
├── 📁 cms/
│ └── strapi-main/ # Strapi CMS backend
├── 📁 src/
│ ├── cofounder_agent/ # AI Co-Founder system
│ └── mcp/ # Model Context Protocol
├── 📁 agents/
│ └── content-agent/ # Content generation agents
└── 📁 docs/ # Documentation
```

=======

- [01 Setup & Overview](./docs/01-SETUP_AND_OVERVIEW.md) - Quick start guide (15 minutes)
- [02 Architecture & Design](./docs/02-ARCHITECTURE_AND_DESIGN.md) - System design and components
- [03 Deployment & Infrastructure](./docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md) - Production deployment
- [04 Development Workflow](./docs/04-DEVELOPMENT_WORKFLOW.md) - Git strategy, testing, CI/CD
- [05 AI Agents & Integration](./docs/05-AI_AGENTS_AND_INTEGRATION.md) - Agent architecture and MCP
- [06 Operations & Maintenance](./docs/06-OPERATIONS_AND_MAINTENANCE.md) - Monitoring and backups
- [07 Branch-Specific Variables](./docs/07-BRANCH_SPECIFIC_VARIABLES.md) - Environment configuration
  > > > > > > > feat/refine

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

| Service           | URL                   | Purpose               |
| ----------------- | --------------------- | --------------------- |
| **Public Site**   | http://localhost:3000 | Next.js website       |
| **Oversight Hub** | http://localhost:3001 | React admin dashboard |
| **AI Co-Founder** | http://localhost:8000 | Python API server     |

---

## **���️ System Overview**

| Service           | Technology  | Purpose                           |
| ----------------- | ----------- | --------------------------------- |
| **Public Site**   | Next.js 15  | High-performance public website   |
| **Oversight Hub** | React 18    | Admin interface for AI management |
| **AI Co-Founder** | Python 3.12 | AI business intelligence system   |
| **Content Agent** | Python      | Autonomous content creation       |

**Project Structure:**

```text
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

**Commercial License:** Contact <sales@gladlabs.io>

---

## **��� Support**

- **Documentation:** [📚 Documentation Hub](./docs/00-README.md)
- **Issues:** GitHub issues
- **Commercial:** <sales@gladlabs.io>

**Owner:** Matthew M. Gladding | **Organization:** Glad Labs, LLC
