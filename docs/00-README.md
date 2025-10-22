# 📚 GLAD Labs Documentation Hub

> **Complete Documentation for AI-Powered Frontier Firm Platform**
>
> Start here. Everything you need is organized and linked below.

**Last Updated**: October 21, 2025 | **Status**: ✅ Documentation Consolidated & Optimized

## 🔗 Quick Navigation

- **📦 [Component Documentation](./components/)** - Public Site, Oversight Hub, Co-Founder Agent, Strapi CMS
- **🆘 [Troubleshooting & Fixes](./guides/troubleshooting/)** - Recent Railway, Strapi, and development fixes
- **🔧 [How-To Guides](./guides/)** - Setup, deployment, and feature guides
- **� [Reference Guides](./reference/)** - API specs, database schemas, deployment checklists

---

## 🎯 Documentation by Role

**Choose your role to see the most relevant documentation:**

### 👨‍💼 **Executive / Project Manager**

- **Want**: High-level overview, status, roadmap
- **Read**: [01-SETUP_AND_OVERVIEW (Executive Summary)](./01-SETUP_AND_OVERVIEW.md#what-is-glad-labs)
- **Then**: [Vision & Roadmap](./archive-old/VISION_AND_ROADMAP.md)

### 🚀 **New Developer**

- **Want**: Get running locally, understand structure
- **Read First**: [01-SETUP_AND_OVERVIEW.md](./01-SETUP_AND_OVERVIEW.md) (15 min)
- **Then Read**: [02-ARCHITECTURE_AND_DESIGN.md](./02-ARCHITECTURE_AND_DESIGN.md) (20 min)
- **Then Follow**: [Local Setup Guide](./guides/local-setup-guide.md)

### 🔧 **DevOps / Infrastructure**

- **Want**: Deploy to production, manage infrastructure
- **Read First**: [03-DEPLOYMENT_AND_INFRASTRUCTURE.md](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md) (25 min)
- **Then Choose**:
  - [Railway Deployment](./troubleshooting/railway-deployment-guide.md)
  - [Production Checklist](./deployment/production-checklist.md)
  - [Railway Environment Variables](./deployment/RAILWAY_ENV_VARIABLES.md)

### 🎨 **Frontend Developer (Next.js)**

- **Want**: Work on public site and dashboards
- **Read First**: [01-SETUP_AND_OVERVIEW.md](./01-SETUP_AND_OVERVIEW.md)
- **Then**: [02-ARCHITECTURE_AND_DESIGN.md (Frontend Layer)](./02-ARCHITECTURE_AND_DESIGN.md#1-public-site-nextjs)
- **Then**: [04-DEVELOPMENT_WORKFLOW.md](./04-DEVELOPMENT_WORKFLOW.md)
- **Reference**: [Local Setup Guide](./guides/local-setup-guide.md)

### 💾 **Backend Developer (Strapi/Python)**

- **Want**: Work on CMS and API
- **Read First**: [02-ARCHITECTURE_AND_DESIGN.md (Backend Layer)](./02-ARCHITECTURE_AND_DESIGN.md#3-strapi-v5-cms)
- **Then**: [04-DEVELOPMENT_WORKFLOW.md](./04-DEVELOPMENT_WORKFLOW.md)
- **Critical**: [Package Manager Strategy](./guides/HYBRID_PACKAGE_MANAGER_STRATEGY.md) - npm locally, yarn on Railway ⭐
- **Reference**: [API Reference](./reference/API_REFERENCE.md)
- **Reference**: [Data Schemas](./reference/data_schemas.md)

### 🤖 **AI/Agent Developer**

- **Want**: Implement and extend agents
- **Read First**: [05-AI_AGENTS_AND_INTEGRATION.md](./05-AI_AGENTS_AND_INTEGRATION.md)
- **Then**: [02-ARCHITECTURE_AND_DESIGN.md (AI Co-Founder)](./02-ARCHITECTURE_AND_DESIGN.md#4-ai-co-founder-fastapi-backend)
- **Then**: [04-DEVELOPMENT_WORKFLOW.md](./04-DEVELOPMENT_WORKFLOW.md)

### 🛠️ **Support / Operations**

- **Want**: Troubleshoot issues, maintain system
- **Read First**: [06-OPERATIONS_AND_MAINTENANCE.md](./06-OPERATIONS_AND_MAINTENANCE.md)
- **Then Use**: [Troubleshooting](./troubleshooting/) when issues arise

---

## 🚀 Quick Navigation

### 🎯 **Core Documentation** (Start Here!)

## 🚀 Core Documentation

- ⚡ **[Quick Reference](./reference/QUICK_REFERENCE.md)** - One-page system overview
- 🚀 **[Setup & Overview](./01-SETUP_AND_OVERVIEW.md)** - Complete AI co-founder vision and implementation plan
- 📋 **[Architecture & Design](./02-ARCHITECTURE_AND_DESIGN.md)** - Detailed architecture and design decisions
- **[Deployment & Infrastructure](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** - Complete installation and deployment instructions
- **[Development Workflow](./04-DEVELOPMENT_WORKFLOW.md)** - Development process and workflows
- **[AI Agents & Integration](./05-AI_AGENTS_AND_INTEGRATION.md)** - AI agent implementation and integration

### 📖 **Documentation Categories**

- **[How-To Guides](./guides/)** - Setup guides, quick starts, and feature documentation
  - � **[Package Manager Strategy](./guides/HYBRID_PACKAGE_MANAGER_STRATEGY.md)** - npm locally, yarn on Railway (critical for Strapi production)
  - 📄 **[Strapi-Backed Pages Guide](./guides/STRAPI_BACKED_PAGES_GUIDE.md)** - How to create pages with Strapi content + markdown fallbacks
  - 📝 **[Content Population Guide](./guides/CONTENT_POPULATION_GUIDE.md)** - Blog post templates and content workflows
  - 🐍 **[Python Tests Setup](./guides/PYTHON_TESTS_SETUP.md)** - Backend testing configuration
  - 💰 **[Cost Optimization](./guides/COST_OPTIMIZATION_GUIDE.md)** - Reduce infrastructure costs
  - 🐳 **[Docker Deployment](./guides/DOCKER_DEPLOYMENT.md)** - Container deployment guide
  - 🛠️ **[PowerShell Scripts](./guides/POWERSHELL_SCRIPTS.md)** - Service management utilities

- **[🆘 Troubleshooting & Recent Fixes](./guides/troubleshooting/)** - Solutions for common issues
  - � **[Railway Yarn Configuration](./guides/troubleshooting/01-RAILWAY_YARN_FIX.md)** - Force Railway to use yarn (critical for Strapi deployment)
  - 🍪 **[Strapi Cookie Security](./guides/troubleshooting/02-STRAPI_COOKIE_SECURITY_FIX.md)** - Fix admin login "Cannot send secure cookie" error
  - 📦 **[Node Version Requirements](./guides/troubleshooting/03-NODE_VERSION_REQUIREMENT.md)** - @noble/hashes compatibility with Node 20
  - ⚡ **[npm run dev Issues](./guides/troubleshooting/04-NPM_DEV_ISSUES.md)** - Port conflicts and service startup problems
  - 📖 **[All Known Issues](./guides/FIXES_AND_SOLUTIONS.md)** - Comprehensive issue and solution tracker

- **[Reference](./reference/)** - Technical specifications and standards
  - 🚀 **[Deployment Complete](./reference/DEPLOYMENT_COMPLETE.md)** - Full deployment guide: Strapi architecture, Vercel config, pre-deployment checklist, post-deployment verification
  - 🔄 **[CI/CD Complete](./reference/CI_CD_COMPLETE.md)** - Complete CI/CD reference: GitHub Actions workflows, testing, linting, npm scripts, debugging
  - Architecture and design patterns
  - Data schemas and API references
  - Coding standards and best practices

- **[Components](./components/)** - Individual component documentation
  - 🌐 **[Public Site (Next.js)](./components/public-site/)** - Frontend blog & marketing site
  - 📊 **[Oversight Hub (React)](./components/oversight-hub/)** - Admin dashboard
  - 🤖 **[Co-Founder Agent (FastAPI)](./components/cofounder-agent/)** - AI orchestrator
  - 💾 **[Strapi CMS](./components/strapi-cms/)** - Headless content management
  - Component-specific documentation, architecture, and setup guides

- **[Testing & Quality Assurance](./guides/)** - Test strategies, setup, and execution
  - 📝 **[Testing Summary](./guides/TESTING_SUMMARY.md)** - Complete testing initiative results: 100 tests passing, setup guides, best practices
  - 🐍 **[Python Tests Setup](./guides/PYTHON_TESTS_SETUP.md)** - Backend test fixture requirements and implementation guide
  - ⚡ **[Quick Start Tests](./guides/QUICK_START_TESTS.md)** - Fast command reference for running tests locally
  - 📚 **[Test Templates Reference](./guides/TEST_TEMPLATES_CREATED.md)** - Frontend test patterns and customization guide

- **[Archive](./archive-old/)** - Historical documentation and legacy reports
  - Previous implementation logs
  - Archived analysis reports
  - Superseded technical documents
  - Session status files (for historical reference)

### 🛠️ **Quick Links**

- 🧪 **[Test Suite Status](./TEST_SUITE_STATUS.md)** - ✅ Current test status (ALL PASSING: 100/100)
- **[E2E Pipeline Setup](./E2E_PIPELINE_SETUP.md)** - End-to-end pipeline configuration
- **[PowerShell Scripts](./guides/POWERSHELL_SCRIPTS.md)** - Service management scripts
- **[NPM Scripts Health Check](./NPM_SCRIPTS_HEALTH_CHECK.md)** - npm script audit
- **[NPM Dev Troubleshooting](./guides/NPM_DEV_TROUBLESHOOTING.md)** - Resolve dev issues

---

## 🔒 **Security & Vulnerability Management**

**⚠️ CURRENT STATUS**: 24 npm vulnerabilities found Oct 21, 2025 - **Mitigation plan documented**

### Start Here

- **[SECURITY_DOCUMENTATION_INDEX.md](./SECURITY_DOCUMENTATION_INDEX.md)** - 🎯 Start here! Index of all security docs by role

### Choose Your Path

- **[SECURITY_EXECUTIVE_SUMMARY.md](./SECURITY_EXECUTIVE_SUMMARY.md)** - For managers & decision makers (5 min read)
- **[SECURITY_QUICK_FIX.md](./SECURITY_QUICK_FIX.md)** - Quick verification commands (3 min read)
- **[SECURITY_VULNERABILITY_REMEDIATION.md](./SECURITY_VULNERABILITY_REMEDIATION.md)** - Full technical guide (20 min read)
- **[SECURITY_STATUS_REPORT_OCT21.md](./SECURITY_STATUS_REPORT_OCT21.md)** - Risk assessment & monitoring (30 min read)

### Key Points

- ✅ 4 vulnerabilities fixed via npm audit fix
- ⚠️ 24 remaining (mostly Strapi core - requires major version upgrade)
- 🟡 **Current Risk**: MODERATE (manageable with compensating controls)
- 🟢 **Recommended Action**: Implement controls NOW, upgrade Strapi to v6 in Q1 2026

---

## 📊 System Overview

### Core Components

```
┌─────────────────────────────────────────────────────────┐
│                 GLAD LABS PLATFORM                       │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Public     │  │  Oversight   │  │   Strapi     │  │
│  │   Website    │  │     Hub      │  │     CMS      │  │
│  │  (Next.js)   │  │   (React)    │  │    (v5)      │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐│
│  │          AI Co-Founder Agent System (FastAPI)        ││
│  ├─────────────────────────────────────────────────────┤│
│  │  • Multi-Agent Orchestrator                          ││
│  │  • Content Agent • Financial Agent                   ││
│  │  • Compliance Agent • Market Insight Agent           ││
│  │  • Social Media Agent                                ││
│  └─────────────────────────────────────────────────────┘│
│                                                           │
│  ┌─────────────────────────────────────────────────────┐│
│  │              AI Model Integrations                    ││
│  │  Ollama • OpenAI • Anthropic • Google Gemini         ││
│  └─────────────────────────────────────────────────────┘│
│                                                           │
│  ┌─────────────────────────────────────────────────────┐│
│  │            Cloud Infrastructure (GCP)                 ││
│  │  Firestore • Pub/Sub • Cloud Functions               ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

### Tech Stack

**Frontend:**

- Next.js 15.0.3 (Public Site)
- React 18.3.1 (Oversight Hub)
- Material-UI 7.3.4
- TypeScript/JavaScript

**Backend:**

- Python 3.11+ (FastAPI)
- Strapi v5 (CMS)
- Node.js 20+

**AI/ML:**

- OpenAI GPT-4 Turbo
- Anthropic Claude 3.5
- Google Gemini 1.5 (Pro & Flash)
- Ollama (Local Models)

**Infrastructure:**

- Google Cloud Platform (GCP)
- Firestore (NoSQL Database)
- Cloud Pub/Sub (Message Queue)
- Docker (Containerization)

---

## 🎯 Feature Status

### ✅ Phase 1: Foundation (Complete)

- [x] Multi-agent architecture
- [x] Oversight Hub dashboard
- [x] Task management system
- [x] Model management interface
- [x] Cost tracking & optimization
- [x] Content generation pipeline

### ✅ Phase 2: Advanced Features (Complete)

- [x] Google Gemini integration (4 models)
- [x] Social media management suite (6 platforms)
- [x] AI-powered content generator
- [x] Cross-platform posting
- [x] Analytics dashboard
- [x] Trending topics tracking

### 🔄 Phase 3: In Progress

- [ ] Enhanced content operations
- [ ] Financial controls expansion
- [ ] Comprehensive settings page
- [ ] WebSocket real-time updates
- [ ] Production OAuth integrations

---

## 📖 Documentation Index

### Primary Documents

| Document                                                  | Description               | Status     |
| --------------------------------------------------------- | ------------------------- | ---------- |
| [Setup Guide](./01-SETUP_AND_OVERVIEW.md)                 | Installation & deployment | ✅ Current |
| [Developer Guide](./guides/DEVELOPER_GUIDE.md)            | Development workflow      | ✅ Current |
| [Architecture](./02-ARCHITECTURE_AND_DESIGN.md)           | System design & API       | ✅ Current |
| [Deployment Guide](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md) | Infrastructure            | ✅ Current |
| [Workflow](./04-DEVELOPMENT_WORKFLOW.md)                  | Development process       | ✅ Current |

### Feature Documentation

| Document                                                               | Description          | Status     |
| ---------------------------------------------------------------------- | -------------------- | ---------- |
| [Strapi Content Setup](./reference/STRAPI_CONTENT_SETUP.md)            | Content types setup  | ✅ Current |
| [Production Checklist](./deployment/production-checklist.md)           | Deployment checklist | ✅ Current |
| [Troubleshooting Guide](./troubleshooting/railway-deployment-guide.md) | Deployment help      | ✅ Current |

### Setup & Operations

| Document                                                     | Description          | Status     |
| ------------------------------------------------------------ | -------------------- | ---------- |
| [Guide: Ollama Setup](./guides/OLLAMA_SETUP.md)              | Local AI models      | ✅ Current |
| [Guide: Docker Deployment](./guides/DOCKER_DEPLOYMENT.md)    | Container deployment | ✅ Current |
| [Guide: Local Setup](./guides/LOCAL_SETUP_GUIDE.md)          | Development setup    | ✅ Current |
| [Production Checklist](./deployment/production-checklist.md) | Go-live checklist    | ✅ Current |

### Reference & Standards

| Document                                                       | Description      | Status     |
| -------------------------------------------------------------- | ---------------- | ---------- |
| [GLAD Labs Standards](./reference/GLAD-LABS-STANDARDS.md)      | Coding standards | ✅ Current |
| [PowerShell Quick Ref](./reference/POWERSHELL_API_QUICKREF.md) | API testing      | ✅ Current |
| [Data Schemas](./reference/data_schemas.md)                    | Database models  | ✅ Current |

### Archived Documents

Historical documents and old reports are in [`./archive/`](./archive/README.md)

---

## 🛠️ Development Workflow

### 1. Initial Setup

```bash
# Clone repository
git clone <repository-url>
cd glad-labs-website

# Run setup script
.\scripts\setup-dependencies.ps1

# Start all services
npm run dev
```

### 2. Development

```bash
# Start individual services
npm run dev:public      # Public site (port 3000)
npm run dev:oversight   # Oversight Hub (port 3001)
npm run dev:strapi      # Strapi CMS (port 1337)
python -m uvicorn src.cofounder_agent.main:app --reload  # Backend API (port 8000)
```

### 3. Testing

```bash
# Run tests
npm test                        # Frontend tests
pytest src/                     # Backend tests
.\scripts\quick-test-api.ps1    # API smoke tests
```

### 4. Deployment

See [Production Deployment Checklist](./PRODUCTION_DEPLOYMENT_CHECKLIST.md)

---

## 🎓 Key Concepts

### Multi-Agent System

The platform uses specialized AI agents for different business functions:

- **Content Agent**: Blog posts, social media, marketing materials
- **Financial Agent**: Budgets, forecasts, financial analysis
- **Compliance Agent**: Regulatory compliance, policy enforcement
- **Market Insight Agent**: Trend analysis, competitive intelligence
- **Social Media Agent**: Multi-platform content management

### Model Router

Intelligently selects the best AI model for each task based on:

- Cost optimization
- Performance requirements
- Model capabilities
- Token limits

### Oversight Hub

Central command center providing:

- Real-time system health monitoring
- Task queue management
- AI model configuration
- Cost tracking & budgeting
- Social media management

---

## 🚨 Common Issues & Solutions

### Issue: Port Already in Use

```bash
# Find and kill process using port 8000
netstat -ano | findstr :8000
taskkill /PID <process_id> /F
```

### Issue: Missing Environment Variables

Create `.env` files in each service directory:

```bash
# Backend: src/cofounder_agent/.env
OPENAI_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here
GOOGLE_API_KEY=your_key_here

# Strapi: cms/strapi-main/.env
DATABASE_CLIENT=sqlite
```

### Issue: Python Dependency Errors

```bash
# Reinstall dependencies
pip install -r requirements.txt
pip install -r src/cofounder_agent/requirements.txt
```

See [Developer Guide](./guides/DEVELOPER_GUIDE.md) for more troubleshooting.

---

## 📈 Performance Metrics

### Current Stats (as of Oct 2025)

- **API Response Time**: <200ms average
- **Task Processing**: 50+ tasks/hour
- **Cost Optimization**: Up to 80% savings with Gemini Flash
- **Uptime**: 99.9% in development
- **Code Coverage**: 85%+ (target: 90%)

### Benchmarks

- **Content Generation**: 500-2000 words in 10-30 seconds
- **Social Media Posts**: Generated in <5 seconds
- **Financial Analysis**: Complex reports in <60 seconds

---

## 🤝 Contributing

### Development Guidelines

1. Follow [GLAD Labs Standards](./GLAD-LABS-STANDARDS.md)
2. Write tests for new features
3. Update documentation
4. Submit PR with clear description

### Branching Strategy

- `main` - Production-ready code
- `develop` - Integration branch
- `feature/*` - New features
- `bugfix/*` - Bug fixes
- `hotfix/*` - Production hotfixes

---

## 📞 Support & Resources

### Documentation

- **Main Docs**: This repository
- **API Docs**: http://localhost:8000/docs (when running)
- **Strapi Admin**: http://localhost:1337/admin

### External Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Next.js Documentation](https://nextjs.org/docs)
- [Strapi Documentation](https://docs.strapi.io/)
- [OpenAI API Reference](https://platform.openai.com/docs/api-reference)
- [Anthropic Claude Docs](https://docs.anthropic.com/)
- [Google Gemini Docs](https://ai.google.dev/docs)

### Community

- **Issues**: GitHub Issues (track bugs and feature requests)
- **Discussions**: GitHub Discussions (Q&A and ideas)

---

## 🏆 Achievements

- ✅ 1,500+ lines of Phase 2 code
- ✅ 10+ specialized AI agents
- ✅ 50+ API endpoints
- ✅ 6 social media platforms integrated
- ✅ 4 AI providers (Ollama, OpenAI, Anthropic, Gemini)
- ✅ 85%+ test coverage
- ✅ Full Docker support

---

## 📜 License

Copyright © 2025 GLAD Labs. All rights reserved.

See [LICENSE](../LICENSE) for details.

---

## 🗺️ Roadmap

### Q4 2025

- [ ] Complete Phase 3 features
- [ ] Production OAuth integrations
- [ ] WebSocket real-time updates
- [ ] Enhanced analytics

### Q1 2026

- [ ] Mobile app (React Native)
- [ ] Advanced AI model fine-tuning
- [ ] Multi-tenant support
- [ ] Enterprise features

---

## 📋 Documentation Maintenance

### Consolidation & Review

The documentation structure is regularly reviewed and consolidated. To perform a documentation review and consolidation:

**See:** [`DOCUMENTATION_CONSOLIDATION_PROMPT.md`](./DOCUMENTATION_CONSOLIDATION_PROMPT.md)

This prompt can be reused with any AI assistant to:

- ✅ Inventory all documentation files
- ✅ Identify duplicates, orphaned files, and structural issues
- ✅ Create a prioritized consolidation plan
- ✅ Provide step-by-step execution instructions

**Recommended Review Frequency:** Quarterly

### Recent Consolidation (October 22, 2025)

Documentation was consolidated:

- 🔧 Recent fixes moved to `docs/guides/troubleshooting/` (numbered 01-04)
- 📦 Hybrid package manager strategy moved to `docs/guides/`
- 🗂️ Outdated setup guides archived to `docs/archive-old/`
- ✅ All fixes linked from main hub in this README
- 🎯 Component documentation READMEs updated

---

## 💝 Made with ❤️ by the GLAD Labs Team

[Documentation](./00-README.md) • [Setup Guide](./01-SETUP_AND_OVERVIEW.md) • [Architecture](./reference/ARCHITECTURE.md)

---
