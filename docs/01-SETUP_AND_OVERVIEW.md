# 01 - Setup & Overview

> **Complete Installation & System Overview for GLAD Labs Platform**
>
> Everything you need to get started: installation instructions, quick start guide, and system overview.

**Reading Time**: 15 minutes | **For**: New developers, DevOps engineers | **Next**: [02-ARCHITECTURE_AND_DESIGN.md](./02-ARCHITECTURE_AND_DESIGN.md)

---

## 📋 Quick Navigation

- [Prerequisites](#prerequisites)
- [Quick Start (5 Minutes)](#quick-start-5-minutes)
- [What is GLAD Labs?](#what-is-glad-labs)
- [System Overview](#system-overview)
- [Local Development Setup](#local-development-setup)
- [Verify Installation](#verify-installation)
- [Next Steps](#next-steps)

---

## Prerequisites

### Required Software

| Software    | Version | Purpose             |
| ----------- | ------- | ------------------- |
| **Node.js** | 18+     | Frontend & Strapi   |
| **Python**  | 3.12+   | Backend & AI agents |
| **Git**     | Latest  | Version control     |
| **npm**     | 10+     | Package management  |

### API Keys (Choose at least one)

- **OpenAI** (Recommended) - https://platform.openai.com/api-keys
- **Anthropic Claude** - https://console.anthropic.com/
- **Google Gemini** (Lowest Cost) - https://makersuite.google.com/app/apikey
- **Ollama** (Free, Local) - No API key needed

### Optional Software

- **Docker Desktop** - For containerized deployment
- **Ollama** - For local AI models (free)
- **VS Code** - Recommended IDE

---

## Quick Start (5 Minutes)

### Step 1: Clone Repository

```bash
git clone <repository-url>
cd glad-labs-website
```

### Step 2: Install Dependencies

```bash
npm run setup:all
```

This installs:

- All Node.js dependencies (frontend, CMS, backend)
- All Python dependencies (AI agents)
- All workspace dependencies

### Step 3: Start All Services

```bash
npm run dev
```

This starts:

- **Public Site**: http://localhost:3000
- **Admin Dashboard**: http://localhost:3001
- **Strapi CMS**: http://localhost:1337
- **Backend API**: http://localhost:8000

### Step 4: Create Strapi Account

1. Go to http://localhost:1337/admin
2. Create your admin account
3. Fill in the form and submit

### Step 5: Create Test Content

1. Navigate to **Content Manager** → **Posts** (in Strapi admin)
2. Click **Create new entry**
3. Fill in:
   - **Title**: "Test Post"
   - **Content**: "This is a test post"
   - **Slug**: "test-post"
4. Click **Save** then **Publish**

### Step 6: Verify on Frontend

Visit http://localhost:3000 and your test post should appear on the homepage!

---

## What is GLAD Labs?

### Core Mission

GLAD Labs is an **AI-powered autonomous business system** that combines:

- 🤖 **Intelligent AI Agents** - Autonomous task execution and decision making
- 📝 **Headless CMS** - Strapi v5 for content management
- 🌐 **Multi-Platform Web** - Next.js frontend + React admin
- ☁️ **Cloud Infrastructure** - Google Cloud + Railway + Vercel
- 💰 **Cost Optimized** - Free local AI + cloud fallback

### Strategic Pillars

| Pillar             | Focus                  | Goal                            |
| ------------------ | ---------------------- | ------------------------------- |
| **Core Product**   | Intelligent Automation | Scalable B2B AI services        |
| **Content Engine** | High-Fidelity Content  | Sophisticated, on-brand content |
| **Technology**     | Serverless Scalability | Cost-effective, pay-per-use     |

### Key Features

✅ **Autonomous Content Creation** - AI writes, reviews, and publishes content  
✅ **Multi-Agent System** - Specialized agents for different tasks  
✅ **Real-Time Dashboard** - Monitor system status and metrics  
✅ **Cost Optimization** - Choose between free local AI or cloud providers  
✅ **Production Ready** - Deployed and tested infrastructure

---

## System Overview

### Architecture at a Glance

```
┌─────────────────────────────────────────────────────────┐
│                GLAD LABS PLATFORM                        │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  ┌────────────┐    ┌──────────────┐    ┌────────────┐  │
│  │  Public    │    │   Strapi     │    │ Oversight  │  │
│  │   Site     │◀──▶│     CMS      │◀──▶│    Hub     │  │
│  │ (Next.js)  │    │   (v5)       │    │  (React)   │  │
│  └────────────┘    └──────────────┘    └────────────┘  │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │     AI Co-Founder (FastAPI Backend)                 │ │
│  │  ┌──────────────────────────────────────────────┐  │ │
│  │  │ Model Router (Ollama/OpenAI/Anthropic)      │  │ │
│  │  └──────────────────────────────────────────────┘  │ │
│  │  ┌─────────┬─────────┬──────────┬──────────────┐  │ │
│  │  │Content  │Financial│ Market   │ Compliance   │  │ │
│  │  │ Agent   │ Agent   │ Insight  │  Agent       │  │ │
│  │  └─────────┴─────────┴──────────┴──────────────┘  │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                           │
│  ┌──────────────────┬──────────────────────────────────┐ │
│  │  Database        │  Cloud Infrastructure            │ │
│  │  PostgreSQL      │  Google Cloud Storage & Run      │ │
│  │  Firestore       │  Railway (Backend)               │ │
│  └──────────────────┴──────────────────────────────────┘ │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

### Technology Stack

| Layer          | Technology                           | Purpose            |
| -------------- | ------------------------------------ | ------------------ |
| **Frontend**   | Next.js 15 + React 19                | Public website     |
| **Admin UI**   | React 18 + Material-UI               | Dashboard          |
| **CMS**        | Strapi v5                            | Content management |
| **Backend**    | Python 3.12 + FastAPI                | AI agents          |
| **Database**   | PostgreSQL                           | Primary data       |
| **Storage**    | Google Cloud Storage                 | Media files        |
| **APIs**       | GraphQL + REST                       | Content API        |
| **Deployment** | Vercel + Railway + GCP               | Cloud hosting      |
| **AI Models**  | Ollama + OpenAI + Anthropic + Gemini | Inference          |

---

## Local Development Setup

### Step 1: Create Environment Files

Create `.env` files in each service directory:

#### `cms/strapi-v5-backend/.env`

```bash
# Server
HOST=0.0.0.0
PORT=1337
NODE_ENV=development

# Security (Generate these with: node -e "console.log(require('crypto').randomBytes(16).toString('base64'))")
APP_KEYS=your-key1,your-key2,your-key3,your-key4
API_TOKEN_SALT=your-api-token-salt
ADMIN_JWT_SECRET=your-admin-jwt-secret
JWT_SECRET=your-jwt-secret
TRANSFER_TOKEN_SALT=your-transfer-token-salt

# Database (Use SQLite for local development)
DATABASE_CLIENT=sqlite
DATABASE_FILENAME=.tmp/data.db
```

#### `web/public-site/.env.local`

```bash
# API URLs
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
NEXT_PUBLIC_API_URL=http://localhost:8000

# Environment
NODE_ENV=development
```

#### `src/cofounder_agent/.env`

```bash
# AI Provider Keys (at least one required)
OPENAI_API_KEY=sk-your-openai-key
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key
GOOGLE_API_KEY=your-google-gemini-key

# Ollama (local AI - no key needed)
OLLAMA_BASE_URL=http://localhost:11434

# Environment
ENVIRONMENT=development
DEBUG=True
```

### Step 2: Verify Installation

```bash
# Check Node version
node --version          # Should be 18+

# Check Python version
python --version        # Should be 3.12+

# Check npm version
npm --version          # Should be 10+
```

### Step 3: Install Dependencies

```bash
# Install all dependencies
npm run setup:all

# Or manually:
npm install --workspaces
pip install -r requirements.txt
pip install -r src/cofounder_agent/requirements.txt
```

### Step 4: Start Services

**Option A: Start all services at once**

```bash
npm run dev
```

**Option B: Start services individually**

```bash
# Terminal 1: Strapi CMS
npm run dev:strapi

# Terminal 2: Public Site
npm run dev:public

# Terminal 3: Oversight Hub
npm run dev:oversight

# Terminal 4: Backend API (manual)
cd src/cofounder_agent
python -m uvicorn main:app --reload --port 8000
```

---

## Verify Installation

### Check All Services Are Running

Access these URLs:

| Service           | URL                         | Expected             |
| ----------------- | --------------------------- | -------------------- |
| **Public Site**   | http://localhost:3000       | Next.js homepage     |
| **Oversight Hub** | http://localhost:3001       | React dashboard      |
| **Strapi CMS**    | http://localhost:1337/admin | Admin panel          |
| **Backend API**   | http://localhost:8000       | FastAPI running      |
| **API Docs**      | http://localhost:8000/docs  | Interactive API docs |

### Verify Database Connection

1. Go to http://localhost:1337/admin
2. Click **Settings** → **Database**
3. Should show "Connected to SQLite"

### Verify Strapi Content Types

1. Go to http://localhost:1337/admin
2. Click **Settings** → **Content-Type Builder**
3. Should see:
   - 📄 Posts
   - 🏷️ Categories
   - 🏷️ Tags
   - ✍️ Authors
   - ℹ️ About
   - 🔒 Privacy Policy

---

## Next Steps

1. ✅ **Installation complete!** Proceed to [Architecture & Design](./02-ARCHITECTURE_AND_DESIGN.md) to understand the system

2. 📖 **Want step-by-step guides?** Check out [guides/](./guides/) folder

3. 🚀 **Ready to deploy?** Jump to [Deployment & Infrastructure](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)

4. 🔧 **Running into issues?** See [troubleshooting/](./troubleshooting/) folder

---

## Troubleshooting

### Port Already in Use

```bash
# Windows
netstat -ano | findstr :3000
taskkill /PID <process_id> /F

# macOS/Linux
lsof -ti:3000 | xargs kill -9
```

### Dependencies Not Installing

```bash
# Clear cache and reinstall
rm -rf node_modules package-lock.json
npm cache clean --force
npm install --workspaces
```

### Python Errors

```bash
# Upgrade pip
python -m pip install --upgrade pip

# Reinstall dependencies
pip install -r src/cofounder_agent/requirements.txt --force-reinstall
```

### Database Issues

```bash
# Clear Strapi database
rm -rf cms/strapi-v5-backend/.tmp/data.db

# Reinstall dependencies
npm run setup:all

# Restart Strapi
npm run dev:strapi
```

---

**← Previous**: Start here! | **Next →**: [02-ARCHITECTURE_AND_DESIGN.md](./02-ARCHITECTURE_AND_DESIGN.md)
