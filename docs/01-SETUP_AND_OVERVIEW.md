# 01 - Setup & Overview

**Last Updated:** October 22, 2025  
**Version:** 1.0  
**Status:** ✅ Production Ready

---

## 🎯 Quick Links

- **Want Quick Start?** → [Quick Start (5 Minutes)](#quick-start-5-minutes)
- **Local Development?** → [Local Development Setup](#local-development-setup)
- **Production Deploy?** → [Production Deployment](#production-deployment)
- **Issues?** → [Troubleshooting](#troubleshooting)

---

## 📖 Overview

Welcome to GLAD Labs! This guide covers everything from initial setup through production deployment. GLAD Labs is a comprehensive AI Co-Founder system that combines autonomous content creation, business intelligence, and multi-agent orchestration.

**This document will get you:**
- ✅ Local development environment running in 15 minutes
- ✅ All services (Strapi, FastAPI, Next.js) operational
- ✅ Ready to test the end-to-end pipeline
- ✅ Connected to AI models (free Ollama or paid APIs)

---

## 📋 Prerequisites

### Required Software

| Software | Version | Purpose | Download |
|----------|---------|---------|----------|
| **Node.js** | 18.x - 22.x | Frontend & Strapi | [nodejs.org](https://nodejs.org/) |
| **Python** | 3.12+ | Backend API | [python.org](https://www.python.org/) |
| **npm** | 10+ | Package management | (Included with Node.js) |
| **Git** | Latest | Version control | [git-scm.com](https://git-scm.com/) |

### Optional Software

| Software | Purpose | Download |
|----------|---------|----------|
| **Docker Desktop** | Container deployment | [docker.com](https://www.docker.com/) |
| **Ollama** | Local AI models (zero-cost) | [ollama.ai](https://ollama.ai/) |
| **VS Code** | Recommended IDE | [code.visualstudio.com](https://code.visualstudio.com/) |
| **PostgreSQL** | Production database | [postgresql.org](https://www.postgresql.org/) |

### AI API Keys (At Least One Required)

Choose based on your preference:

- **OpenAI** (Most Popular): https://platform.openai.com/api-keys
- **Anthropic Claude** (Best Quality): https://console.anthropic.com/
- **Google Gemini** (Lowest Cost): https://makersuite.google.com/app/apikey
- **Ollama** (Free, Local, No Key): https://ollama.ai (Zero cost!)

---

## 🚀 Quick Start (5 Minutes)

### Step 1: Clone Repository

```bash
git clone https://gitlab.com/glad-labs-org/glad-labs-website.git
cd glad-labs-website
```

### Step 2: Install Dependencies

```bash
# Install all Node.js dependencies
npm install

# Install Python dependencies
pip install -r requirements.txt
pip install -r src/cofounder_agent/requirements.txt
```

### Step 3: Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit with your API keys (at least one required)
# nano .env  (or use your favorite editor)

# At minimum, set one of these:
# OPENAI_API_KEY=sk-xxxxxxx
# ANTHROPIC_API_KEY=sk-ant-xxxxxxx
# GOOGLE_API_KEY=AIza-xxxxxxx
# OR use Ollama (no key needed)
```

### Step 4: Start All Services

**Option A: Using npm (simplest)**

```bash
npm run dev
```

**Option B: Using VS Code Tasks**

1. Press `Ctrl+Shift+P`
2. Type "Tasks: Run Task"
3. Select "Start All Services"

**Option C: Manual (one terminal per service)**

```bash
# Terminal 1: Strapi CMS
cd cms/strapi-v5-backend && npm run develop

# Terminal 2: AI Co-Founder Backend
cd src/cofounder_agent && python -m uvicorn main:app --reload

# Terminal 3: Public Site
cd web/public-site && npm run dev

# Terminal 4: Oversight Hub (optional)
cd web/oversight-hub && npm start
```

### Step 5: Verify Installation

Access these URLs to confirm everything is running:

- **Public Site**: http://localhost:3000
- **Oversight Hub**: http://localhost:3001
- **Strapi CMS Admin**: http://localhost:1337/admin
- **Backend API Docs**: http://localhost:8000/docs

**✅ If all URLs work, you're ready to use GLAD Labs!**

---

## 🔧 Local Development Setup

### Project Structure

```
glad-labs-website/
├── web/
│   ├── public-site/          # Next.js public website (port 3000)
│   └── oversight-hub/        # React admin dashboard (port 3001)
├── cms/
│   └── strapi-v5-backend/    # Strapi CMS (port 1337)
├── src/
│   ├── agents/               # Specialized AI agents
│   └── cofounder_agent/      # FastAPI backend (port 8000)
├── cloud-functions/          # GCP cloud functions
├── scripts/                  # Helper scripts
├── docs/                     # Documentation
└── .env                      # Environment configuration
```

### Step-by-Step Setup

#### 1. Install Node.js Dependencies

```bash
# Install root-level dependencies
npm install

# Install all workspace dependencies
npm install --workspaces

# Or install individually:
cd web/public-site && npm install
cd ../oversight-hub && npm install
cd ../../cms/strapi-v5-backend && npm install
```

#### 2. Install Python Dependencies

```bash
# Upgrade pip
python -m pip install --upgrade pip

# Install core dependencies
pip install -r requirements.txt

# Install backend dependencies
pip install -r src/cofounder_agent/requirements.txt

# Optional: Install Google Gemini support
pip install google-generativeai
```

#### 3. Create Environment Files

**Root `.env` file:**

```bash
# Copy example
cp .env.example .env

# Edit with API keys (choose at least one):
OPENAI_API_KEY=sk-your-key-here
# OR
ANTHROPIC_API_KEY=sk-ant-your-key-here
# OR
GOOGLE_API_KEY=your-google-key-here
# OR use free local Ollama
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434
```

**Strapi `.env` file:**

```bash
cd cms/strapi-v5-backend
cp .env.example .env

# Generated secrets - replace with actual values:
ADMIN_JWT_SECRET=your-secret-here
API_TOKEN_SALT=your-salt-here
APP_KEYS=key1,key2,key3,key4
JWT_SECRET=your-jwt-secret-here
```

**Backend `.env` file:**

```bash
cd src/cofounder_agent
cp .env.example .env

# Add your API keys from root .env
OPENAI_API_KEY=sk-your-key-here
ENVIRONMENT=development
DEBUG=True
```

#### 4. Setup Strapi CMS (First Time)

```bash
cd cms/strapi-v5-backend

# Build Strapi
npm run build

# Start in development mode
npm run develop
```

1. Open browser: http://localhost:1337/admin
2. Create admin account (first-time setup)
3. Set username: `admin`
4. Set email and password
5. Generate API Token:
   - Settings → API Tokens → Create new API Token
   - Name: `Next.js Public Site`
   - Type: `Full access` (for development)
   - Copy the token and add to `.env`: `STRAPI_API_TOKEN=your-token`

#### 5. Start All Services

```bash
# From repository root
npm run dev

# Or use VS Code Tasks:
# Ctrl+Shift+P → "Tasks: Run Task" → "Start All Services"
```

Monitor each terminal for successful startup:

- Strapi: "Server is running at: http://localhost:1337"
- Backend: "Application startup complete"
- Public Site: "Local: http://localhost:3000"
- Oversight Hub: "Compiled successfully"

---

## 🎯 Setup Ollama (Free Local AI)

If you want to use Ollama instead of paid APIs:

### 1. Install Ollama

```bash
# Windows
winget install Ollama.Ollama

# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.ai/install.sh | sh
```

### 2. Pull Models

```bash
# Recommended models for development:
ollama pull mistral       # Excellent for general use (7B)
ollama pull llama3.2      # Meta's latest model (3B, fast)
ollama pull phi           # Small and fast (2.7B)

# List installed models
ollama list
```

### 3. Start Ollama Service

```bash
# Start Ollama server
ollama serve

# Verify it's working
curl http://localhost:11434/api/tags
```

### 4. Configure GLAD Labs to Use Ollama

In `.env` file:

```bash
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434
```

### 5. Test in Oversight Hub

1. Go to http://localhost:3001/models
2. Find "Ollama" section
3. Toggle "Active"
4. Select a model (e.g., mistral)
5. Click "Test Connectivity"

**Benefits of Ollama:**
- ✅ 100% free - unlimited requests
- ✅ No API rate limits
- ✅ No latency from API calls
- ✅ Perfect for development and testing
- ✅ GPU acceleration (auto-detected)

---

## 🌍 Production Deployment

### Before Deploying

Ensure:
- ✅ All services run locally without errors
- ✅ Tests pass: `npm test && pytest src/`
- ✅ Environment variables configured
- ✅ Database backups planned
- ✅ Monitoring/alerting configured

### Deployment Platforms

#### Backend (FastAPI) - Railway (Recommended)

```bash
# 1. Install Railway CLI
npm install -g railway

# 2. Login
railway login

# 3. Deploy
cd src/cofounder_agent
railway up

# 4. Set environment variables in Railway dashboard
# - Add all API keys from .env
# - Set NODE_ENV=production
# - Set ENVIRONMENT=production
```

#### Frontend (Next.js) - Vercel (Recommended)

```bash
# 1. Install Vercel CLI
npm install -g vercel

# 2. Deploy public site
cd web/public-site
vercel --prod

# 3. Deploy oversight hub
cd ../oversight-hub
vercel --prod
```

#### Strapi CMS - Railway Template

Use the official Railway Strapi Template for one-click deployment:

**https://railway.com/template/strapi**

Features:
- One-click deployment
- PostgreSQL included
- SSL/HTTPS automatic
- Backups included
- Full monitoring dashboard

See full [Deployment Guide](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)

---

## ⚙️ Environment Configuration

### Essential Variables

| Variable | Required | Example | Notes |
|----------|----------|---------|-------|
| `OPENAI_API_KEY` | One of three | `sk-...` | If using OpenAI |
| `ANTHROPIC_API_KEY` | One of three | `sk-ant-...` | If using Anthropic |
| `GOOGLE_API_KEY` | One of three | `AIza...` | If using Google Gemini |
| `USE_OLLAMA` | Optional | `true` | Use free local Ollama instead |
| `OLLAMA_HOST` | If Ollama | `http://localhost:11434` | Local Ollama server |

### Optional Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `DATABASE_URL` | SQLite | PostgreSQL for production |
| `REDIS_URL` | None | Cache layer (recommended for production) |
| `RATE_LIMIT_PER_MINUTE` | 60 | API rate limiting |
| `LOG_LEVEL` | INFO | DEBUG for development |
| `CORS_ORIGINS` | `*` | Restrict in production |

### Generate Secure Secrets

```bash
# Generate random secrets for .env
# PowerShell:
[Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Minimum 0 -Maximum 256 }))

# Bash:
openssl rand -base64 32
```

---

## 🐛 Troubleshooting

### Port Already in Use

**Symptom:** `EADDRINUSE: address already in use :::3000`

**Solution:**

```bash
# Windows - Find and kill process
netstat -ano | findstr :3000
taskkill /PID <process_id> /F

# macOS/Linux - Kill process on port
lsof -ti:3000 | xargs kill -9
```

### Module Not Found

**Symptom:** `Error: Cannot find module 'xyz'`

**Solution:**

```bash
# Clear and reinstall
rm -rf node_modules package-lock.json
npm install

# For Python
pip install --upgrade --force-reinstall -r requirements.txt
```

### Ollama Connection Refused

**Symptom:** `Connection refused to http://localhost:11434`

**Solution:**

```bash
# Start Ollama service
ollama serve

# Test connection
curl http://localhost:11434/api/tags

# If still failing, restart
pkill ollama
ollama serve
```

### Strapi Admin Won't Load

**Symptom:** Blank page or endless loading

**Solution:**

```bash
cd cms/strapi-v5-backend

# Clear cache
rm -rf .cache build

# Rebuild
npm run build
npm run develop
```

### API Key Authentication Failed

**Symptom:** `401 Unauthorized` or `Invalid API key`

**Solution:**

1. Verify `.env` file exists in correct location
2. Check API key has no extra spaces or quotes
3. Verify API key has sufficient quota
4. Test key directly with provider

### Database Connection Error

**Symptom:** `unable to open database file`

**Solution:**

```bash
# Create database directory
mkdir -p cms/strapi-v5-backend/.tmp

# Reset database
rm cms/strapi-v5-backend/.tmp/data.db

# Restart Strapi
cd cms/strapi-v5-backend
npm run develop
```

### React App Blank Page

**Symptom:** White screen in browser

**Solution:**

```bash
# Check browser console (F12) for errors
# Clear and reinstall
cd web/public-site
rm -rf node_modules package-lock.json
npm install
npm run dev
```

---

## ✅ Common Commands Reference

### Starting Services

```bash
npm run dev              # Start all services
npm run dev:public      # Public site only
npm run dev:oversight   # Oversight hub only
npm run dev:strapi      # Strapi CMS only
npm run dev:cofounder   # AI Co-Founder backend only
```

### Testing

```bash
npm test                # Run all tests
pytest src/             # Python tests
npm run test:coverage   # Coverage report
```

### Building

```bash
npm run build           # Build all
npm run build:public    # Build public site
npm run build:oversight # Build oversight hub
```

### Other Useful Commands

```bash
npm run lint            # Lint code
npm run format          # Format code
npm run type-check      # TypeScript checks
./scripts/quick-test-api.ps1  # Test all APIs
```

### Service URLs

| Service | URL | Admin |
|---------|-----|-------|
| Public Site | http://localhost:3000 | N/A |
| Oversight Hub | http://localhost:3001 | N/A |
| Strapi CMS | http://localhost:1337 | http://localhost:1337/admin |
| Backend API | http://localhost:8000 | http://localhost:8000/docs |
| Ollama | http://localhost:11434 | N/A |

---

## 🎯 Next Steps

After successful setup:

1. **Test the System**
   - Run quick test: `./scripts/quick-test-api.ps1`
   - Check Oversight Hub: http://localhost:3001
   - Try creating a task

2. **Configure AI Models**
   - Navigate to Models page: http://localhost:3001/models
   - Toggle active providers
   - Test each model

3. **Create Your First Content**
   - Use Oversight Hub to generate content
   - Monitor progress
   - Check output in Strapi

4. **Explore Documentation**
   - [Architecture & Design](./02-ARCHITECTURE_AND_DESIGN.md) - System overview
   - [AI Agents & Integration](./05-AI_AGENTS_AND_INTEGRATION.md) - Agent details
   - [Developer Guide](./guides/DEVELOPER_GUIDE.md) - Development patterns
   - [Troubleshooting](./guides/troubleshooting/) - Problem solutions

---

## 📚 Additional Resources

- **Full Architecture**: [02-ARCHITECTURE_AND_DESIGN.md](./02-ARCHITECTURE_AND_DESIGN.md)
- **Deployment Guide**: [03-DEPLOYMENT_AND_INFRASTRUCTURE.md](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)
- **Development Workflow**: [04-DEVELOPMENT_WORKFLOW.md](./04-DEVELOPMENT_WORKFLOW.md)
- **AI Agents**: [05-AI_AGENTS_AND_INTEGRATION.md](./05-AI_AGENTS_AND_INTEGRATION.md)
- **All Guides**: [docs/guides/](./guides/)
- **Troubleshooting**: [docs/guides/troubleshooting/](./guides/troubleshooting/)

---

## 📞 Getting Help

1. **Check Logs:**
   - Backend: Console output from FastAPI terminal
   - Frontend: Browser console (F12)
   - Strapi: Terminal where npm run develop is running

2. **Verify Services:**
   - Run: `./scripts/quick-test-api.ps1`
   - Check: http://localhost:8000/docs

3. **Enable Debug Mode:**
   - Add to `.env`: `DEBUG=True` and `LOG_LEVEL=DEBUG`

4. **Community Support:**
   - Check [Troubleshooting Guide](./guides/troubleshooting/)
   - Review test files for examples
   - Check git log for similar fixes

---

<div align="center">

**[← Back to Documentation Hub](./00-README.md)**

[Architecture](./02-ARCHITECTURE_AND_DESIGN.md) • [Deployment](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md) • [Development](./04-DEVELOPMENT_WORKFLOW.md) • [Guides](./guides/)

</div>
