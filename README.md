# 🚀 GLAD Labs - AI-Powered Frontier Firm Platform

> **An intelligent, autonomous business system combining AI agents, headless CMS, and multi-platform presence.**
>
> Transform your business with intelligent automation. Get started in 5 minutes.

---

## 📖 Documentation Overview

All documentation is organized in the `docs/` folder by purpose. Start with the **[Documentation Hub →](./docs/)**

### 🎯 Quick Links by Role

**👨‍💼 Project Manager?** → [Executive Summary](./docs/01-SETUP_AND_OVERVIEW.md#executive-summary)

**🚀 New Developer?** → [Quick Start Guide](./docs/01-SETUP_AND_OVERVIEW.md#quick-start-in-5-minutes)

**🔧 DevOps Engineer?** → [Deployment Guide](./docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md)

**🎨 Frontend Developer?** → [Frontend Setup](./docs/02-ARCHITECTURE_AND_DESIGN.md#frontend-layer)

**🤖 AI/Agent Developer?** → [Agent Architecture](./docs/05-AI_AGENTS_AND_INTEGRATION.md)

---

## 📚 Core Documentation

### Main Guides (Read in Order)

| #                                                    | Title                           | Purpose                                             | Time   |
| ---------------------------------------------------- | ------------------------------- | --------------------------------------------------- | ------ |
| **[1️⃣](./docs/01-SETUP_AND_OVERVIEW.md)**            | **Setup & Overview**            | Installation, quick start, system overview          | 15 min |
| **[2️⃣](./docs/02-ARCHITECTURE_AND_DESIGN.md)**       | **Architecture & Design**       | System design, tech stack, component architecture   | 20 min |
| **[3️⃣](./docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** | **Deployment & Infrastructure** | Production deployment, cloud setup, environments    | 25 min |
| **[4️⃣](./docs/04-DEVELOPMENT_WORKFLOW.md)**          | **Development Workflow**        | Local development, git workflow, testing, debugging | 15 min |
| **[5️⃣](./docs/05-AI_AGENTS_AND_INTEGRATION.md)**     | **AI Agents & Integration**     | Agent architecture, model routing, implementation   | 20 min |
| **[6️⃣](./docs/06-OPERATIONS_AND_MAINTENANCE.md)**    | **Operations & Maintenance**    | Monitoring, troubleshooting, optimization, updates  | 15 min |

### Supporting Documentation

**Guides** - Step-by-step how-to guides in `docs/guides/`

- Local setup, Docker, Ollama, Railway, Vercel, Cost optimization

**Reference** - Technical specifications in `docs/reference/`

- Architecture, schemas, API specs, standards, testing

**Troubleshooting** - Solutions to common problems in `docs/troubleshooting/`

- Error fixes, performance issues, deployment problems

---

## �️ Project Structure

```
glad-labs-website/
├── README.md                    # This file - Start here!
├── docs/                        # 📚 All documentation
│   ├── 01-SETUP_AND_OVERVIEW.md
│   ├── 02-ARCHITECTURE_AND_DESIGN.md
│   ├── 03-DEPLOYMENT_AND_INFRASTRUCTURE.md
│   ├── 04-DEVELOPMENT_WORKFLOW.md
│   ├── 05-AI_AGENTS_AND_INTEGRATION.md
│   ├── 06-OPERATIONS_AND_MAINTENANCE.md
│   ├── guides/                  # How-to guides
│   ├── reference/               # Technical specs
│   ├── troubleshooting/         # Problem solutions
│   └── archive/                 # Historical docs
├── web/                         # Frontend applications
│   ├── public-site/             # Next.js public website
│   └── oversight-hub/           # React admin dashboard
├── cms/
│   └── strapi-v5-backend/       # Headless CMS (Railway)
├── src/
│   ├── agents/                  # AI agent implementations
│   └── cofounder_agent/         # Main AI orchestrator
├── cloud-functions/             # GCP serverless functions
└── scripts/                     # Automation scripts
```

---

## � Key Features

### 🤖 AI-Powered Automation

- Multi-agent system with specialized roles
- Autonomous content creation and optimization
- Intelligent task management and prioritization
- Real-time market insight generation

### 📝 Headless CMS

- Strapi v5 for content management
- REST + GraphQL APIs
- Rich media support
- Role-based access control

### 🌐 Multi-Platform Presence

- Next.js public website
- React admin dashboard
- Social media integration
- Mobile-responsive design

### ☁️ Cloud-Native Architecture

- Serverless deployment (Google Cloud Run)
- PostgreSQL on Railway
- Auto-scaling infrastructure
- Pay-as-you-go pricing

---

## 🔄 Development Workflow

### Local Development

```bash
# Install dependencies
npm run setup:all

# Start all services
npm run dev

# Run tests
npm test

# Build for production
npm run build
```

### Git Workflow

```bash
# Create feature branch
git checkout -b feature/your-feature

# Commit changes
git add .
git commit -m "feat: description"

# Push and create PR
git push origin feature/your-feature
```

### Deployment

```bash
# Merge to main
git merge develop
git push origin main

# Auto-deploys to:
# - Vercel (frontend)
# - Railway (backend/CMS)
```

---

## 📊 Tech Stack

| Layer          | Technology                           | Purpose                        |
| -------------- | ------------------------------------ | ------------------------------ |
| **Frontend**   | Next.js 15 + React 19                | Public site & content delivery |
| **Admin UI**   | React 18 + Material-UI               | Admin dashboard                |
| **CMS**        | Strapi v5                            | Content management             |
| **Backend**    | Python 3.12 + FastAPI                | AI agents & orchestration      |
| **Database**   | PostgreSQL                           | Primary data store             |
| **Storage**    | Google Cloud Storage                 | Media & assets                 |
| **APIs**       | GraphQL + REST                       | Content delivery               |
| **Deployment** | Vercel + Railway + GCP               | Cloud hosting                  |
| **AI Models**  | Ollama + OpenAI + Anthropic + Gemini | Multi-provider inference       |

---

## 🚀 Quick Links

| Need                  | Link                                                     | Time   |
| --------------------- | -------------------------------------------------------- | ------ |
| **Deploy frontend**   | [Vercel Guide](./docs/guides/vercel-deployment.md)       | 10 min |
| **Deploy backend**    | [Railway Guide](./docs/guides/railway-deployment.md)     | 15 min |
| **Setup locally**     | [Local Setup](./docs/guides/local-setup-guide.md)        | 20 min |
| **Understand system** | [Architecture](./docs/02-ARCHITECTURE_AND_DESIGN.md)     | 20 min |
| **Fix an error**      | [Troubleshooting](./docs/troubleshooting/)               | Varies |
| **Read the history**  | [Developer Journal](./docs/archive/DEVELOPER_JOURNAL.md) | 30 min |

---

## ✅ Getting Started Checklist

- [ ] Read [Setup & Overview](./docs/01-SETUP_AND_OVERVIEW.md)
- [ ] Run `npm run setup:all` and `npm run dev`
- [ ] Access http://localhost:3000
- [ ] Read [Architecture & Design](./docs/02-ARCHITECTURE_AND_DESIGN.md)
- [ ] Identify your role and read relevant guide
- [ ] Create a test post in Strapi
- [ ] Verify content appears on homepage
- [ ] Read [Development Workflow](./docs/04-DEVELOPMENT_WORKFLOW.md)

---

## 🆘 Need Help?

1. **Documentation**: Check [docs/](./docs/) - Comprehensive guides for every topic
2. **Troubleshooting**: See [docs/troubleshooting/](./docs/troubleshooting/) - Common issues & solutions
3. **API Reference**: Visit http://localhost:8000/docs - Interactive API documentation
4. **Strapi Admin**: http://localhost:1337/admin - CMS documentation built-in

---

## � Development Status

✅ **Production Ready** - All core systems operational

- Phase 1: Foundation & Core Features ✅
- Phase 2: Gemini Integration & Social Media ✅
- Phase 3: Enhanced Operations (In Progress)

**Current Version**: 3.0 | **Last Updated**: October 18, 2025

---

**Built with ❤️ by GLAD Labs | Frontier Firm Automation Platform**

---

## **🤝 Contributing**

### **Development Setup**

1. **Fork the repository**
2. **Create feature branch**: `git checkout -b feature/new-capability`
3. **Follow code standards**: ESLint, Prettier, component conventions
4. **Add comprehensive tests**: Unit and integration testing
5. **Update documentation**: Keep all docs current
6. **Create pull request**: Detailed description of changes

### **Testing Strategy**

- **Frontend**: Jest + React Testing Library
- **Backend**: Strapi built-in testing framework
- **Content Agent**: Python unittest framework
- **Integration**: End-to-end testing with Playwright

For step-by-step local testing instructions on Windows PowerShell (including virtualenv setup), see the Testing Guide: `TESTING.md`.

---

## **📞 Support & Contact**

**Project Owner:** Matthew M. Gladding  
**Organization:** Glad Labs, LLC  
**License:** MIT

**Architecture Status:** ✅ Production Ready v2.0  
**Last Documentation Update:** October 13, 2025
