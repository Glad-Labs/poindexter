# 01 - Setup Guide

> **Complete Installation & Deployment Guide for GLAD Labs Platform**

This guide covers everything from initial setup to production deployment across multiple environments.

---

## 📋 Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start (5 Minutes)](#quick-start-5-minutes)
3. [Local Development Setup](#local-development-setup)
4. [Docker Deployment](#docker-deployment)
5. [Ollama Local AI Setup](#ollama-local-ai-setup)
6. [Production Deployment](#production-deployment)
7. [Environment Configuration](#environment-configuration)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Software

| Software    | Version | Purpose            | Download                              |
| ----------- | ------- | ------------------ | ------------------------------------- |
| **Node.js** | 20+     | Frontend & Strapi  | [nodejs.org](https://nodejs.org/)     |
| **Python**  | 3.11+   | Backend API        | [python.org](https://www.python.org/) |
| **npm**     | 10+     | Package management | (Included with Node.js)               |
| **Git**     | Latest  | Version control    | [git-scm.com](https://git-scm.com/)   |

### Optional Software

| Software           | Purpose              | Download                                                |
| ------------------ | -------------------- | ------------------------------------------------------- |
| **Docker Desktop** | Container deployment | [docker.com](https://www.docker.com/)                   |
| **Ollama**         | Local AI models      | [ollama.ai](https://ollama.ai/)                         |
| **VS Code**        | Recommended IDE      | [code.visualstudio.com](https://code.visualstudio.com/) |

### API Keys (At Least One Required)

You'll need API keys for AI providers:

- **OpenAI** (Recommended): https://platform.openai.com/api-keys
- **Anthropic Claude**: https://console.anthropic.com/
- **Google Gemini** (Lowest Cost): https://makersuite.google.com/app/apikey
- **Ollama** (Free, Local): No API key needed

---

## Quick Start (5 Minutes)

### Option 1: Automated Setup (Windows)

```powershell
# Clone repository
git clone <repository-url>
cd glad-labs-website

# Run automated setup
.\scripts\setup-dependencies.ps1

# Start all services
npm run dev
```

### Option 2: Manual Setup (All Platforms)

```bash
# 1. Install dependencies
npm install
pip install -r requirements.txt
pip install -r src/cofounder_agent/requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 3. Start services
npm run dev          # Start all services
# OR start individually:
npm run dev:public   # Public site (port 3000)
npm run dev:oversight # Oversight Hub (port 3001)
npm run dev:strapi   # Strapi CMS (port 1337)
python -m uvicorn src.cofounder_agent.main:app --reload  # Backend (port 8000)
```

### Verify Installation

Access these URLs to confirm everything is running:

- **Public Site**: http://localhost:3000
- **Oversight Hub**: http://localhost:3001
- **Strapi CMS**: http://localhost:1337/admin
- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs

---

## Local Development Setup

### Step 1: Clone Repository

```bash
git clone <repository-url>
cd glad-labs-website
```

### Step 2: Project Structure

```
glad-labs-website/
├── web/
│   ├── public-site/        # Next.js public website
│   └── oversight-hub/      # React admin dashboard
├── cms/
│   └── strapi-v5-backend/  # Strapi CMS
├── src/
│   ├── agents/             # AI agents
│   └── cofounder_agent/    # FastAPI backend
├── scripts/                # Helper scripts
└── docs/                   # Documentation
```

### Step 3: Install Node.js Dependencies

```bash
# Install root dependencies
npm install

# Install workspace dependencies (all services)
npm install --workspaces

# Or install individually
cd web/public-site && npm install
cd ../oversight-hub && npm install
cd ../../cms/strapi-v5-backend && npm install
```

### Step 4: Install Python Dependencies

```bash
# Upgrade pip
python -m pip install --upgrade pip

# Install core dependencies
pip install -r requirements.txt

# Install backend dependencies
pip install -r src/cofounder_agent/requirements.txt

# Install Google Gemini (for Phase 2 features)
pip install google-generativeai
```

### Step 5: Environment Configuration

Create `.env` files in each service directory:

#### Backend API: `src/cofounder_agent/.env`

```bash
# AI Provider API Keys (at least one required)
OPENAI_API_KEY=sk-your-openai-key-here
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key-here
GOOGLE_API_KEY=your-google-gemini-key-here

# Ollama (local AI - no key needed)
OLLAMA_BASE_URL=http://localhost:11434

# Firebase/Firestore (optional for development)
GOOGLE_CLOUD_PROJECT=your-project-id
FIRESTORE_EMULATOR_HOST=localhost:8080

# Rate Limiting
RATE_LIMIT_PER_MINUTE=60

# Environment
ENVIRONMENT=development
DEBUG=True
```

#### Strapi CMS: `cms/strapi-v5-backend/.env`

```bash
# Server
HOST=0.0.0.0
PORT=1337

# Database
DATABASE_CLIENT=sqlite
DATABASE_FILENAME=.tmp/data.db

# Admin
ADMIN_JWT_SECRET=your-generated-secret-here
API_TOKEN_SALT=your-generated-salt-here
APP_KEYS=key1,key2,key3,key4
JWT_SECRET=your-jwt-secret-here

# Environment
NODE_ENV=development
```

#### Public Site: `web/public-site/.env.local`

```bash
# API URLs
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_STRAPI_URL=http://localhost:1337

# Environment
NODE_ENV=development
```

#### Oversight Hub: `web/oversight-hub/.env`

```bash
# API URLs
REACT_APP_API_URL=http://localhost:8000
REACT_APP_STRAPI_URL=http://localhost:1337

# Environment
NODE_ENV=development
```

### Step 6: Start Development Servers

#### Option A: Start All Services

```bash
npm run dev
```

This starts:

- Public Site on port 3000
- Oversight Hub on port 3001
- Strapi CMS on port 1337

Then manually start the backend:

```bash
cd src/cofounder_agent
python -m uvicorn main:app --reload --port 8000
```

#### Option B: Use VS Code Tasks

Press `Ctrl+Shift+P` → "Tasks: Run Task" → Select:

- "Start Public Site"
- "Start Oversight Hub"
- "Start Strapi CMS"
- "Start Co-founder Agent"

---

## Docker Deployment

### Prerequisites

- Docker Desktop installed and running
- 8GB+ RAM available

### Build and Run

```bash
# Build all images
docker-compose build

# Start all services
docker-compose up

# Or run in background
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Docker Services

| Service       | Port | Container Name            |
| ------------- | ---- | ------------------------- |
| Public Site   | 3000 | glad-labs-public-site     |
| Oversight Hub | 3001 | glad-labs-oversight-hub   |
| Strapi CMS    | 1337 | glad-labs-strapi          |
| Backend API   | 8000 | glad-labs-cofounder-agent |
| PostgreSQL    | 5432 | glad-labs-postgres        |

### Docker Environment

Create `docker-compose.env`:

```bash
# Database
POSTGRES_USER=gladlabs
POSTGRES_PASSWORD=your-secure-password
POSTGRES_DB=gladlabs_db

# API Keys
OPENAI_API_KEY=your-key
ANTHROPIC_API_KEY=your-key
GOOGLE_API_KEY=your-key

# Strapi
ADMIN_JWT_SECRET=your-secret
API_TOKEN_SALT=your-salt
```

---

## Ollama Local AI Setup

Run powerful AI models locally without API costs!

### 1. Install Ollama

#### Windows

```powershell
# Download and run installer
# https://ollama.ai/download/windows
```

#### macOS

```bash
brew install ollama
```

#### Linux

```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

### 2. Pull Models

```bash
# Recommended models
ollama pull llama3.2       # Meta's latest (3B params, fast)
ollama pull mistral        # Excellent for code (7B params)
ollama pull codellama      # Specialized for coding (7B params)
ollama pull llava          # Vision model (7B params)

# List installed models
ollama list

# Start Ollama service
ollama serve
```

### 3. Configure Backend

Ollama runs on `http://localhost:11434` by default. No configuration needed!

### 4. Test Ollama

```bash
# Test via CLI
ollama run llama3.2

# Test via API
curl http://localhost:11434/api/generate -d '{
  "model": "llama3.2",
  "prompt": "Why is the sky blue?"
}'
```

### 5. Use in Oversight Hub

1. Navigate to http://localhost:3001/models
2. Find "Ollama" section
3. Toggle "Active"
4. Select a model (e.g., llama3.2)
5. Click "Test Connectivity"

### Ollama Tips

**Performance:**

- GPU acceleration automatic (CUDA/Metal)
- Adjust context window: `ollama run llama3.2 --ctx-size 4096`
- Monitor usage: Task Manager/Activity Monitor

**Cost Savings:**

- Ollama models are 100% free
- Run unlimited requests
- No API rate limits
- Perfect for development

---

## Production Deployment

### Strapi CMS Deployment (Railway Template - RECOMMENDED)

**Status:** ✅ PRODUCTION LIVE

The easiest way to deploy Strapi to production is using the official Railway Strapi Template:

**Template URL:** https://railway.com/template/strapi

**Features:**
- One-click deployment
- Includes PostgreSQL database
- Automatic SSL/HTTPS
- Environment variables pre-configured
- Automatic backups
- Full monitoring dashboard

**Setup Steps:**
1. Click the template link above
2. Authorize Railway with GitHub
3. Deploy (takes ~5 minutes)
4. Copy the admin URL from Railway dashboard
5. Create content types via admin panel

**Admin Panel:** https://glad-labs-strapi-v5-backend-production.up.railway.app/admin

**Complete Setup Guide:** See [RAILWAY_STRAPI_TEMPLATE_SETUP.md](./RAILWAY_STRAPI_TEMPLATE_SETUP.md)

---

### Cloud Providers (For Backend & Frontend)

#### Google Cloud Platform (Recommended)

1. **Setup GCP Project**

```bash
# Install gcloud CLI
# https://cloud.google.com/sdk/docs/install

# Login and set project
gcloud auth login
gcloud config set project your-project-id
```

2. **Deploy Backend (Cloud Run)**

```bash
# Build and push image
gcloud builds submit --tag gcr.io/your-project/cofounder-agent

# Deploy
gcloud run deploy cofounder-agent \
  --image gcr.io/your-project/cofounder-agent \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated
```

3. **Deploy Frontend (Firebase Hosting)**

```bash
# Install Firebase CLI
npm install -g firebase-tools

# Login
firebase login

# Initialize
firebase init hosting

# Deploy
firebase deploy --only hosting
```

4. **Setup Firestore Database**

```bash
# Enable Firestore
gcloud firestore databases create --region=us-central1

# Create indexes
gcloud firestore indexes create --collection=tasks --field=status --field=created_at
```

#### AWS Alternative

See [AWS Deployment Guide](./archive/AWS_DEPLOYMENT.md) (coming soon)

### Environment Variables (Production)

Use **Google Secret Manager** or **AWS Secrets Manager**:

```bash
# Create secrets
gcloud secrets create openai-api-key --data-file=-
# Paste your key, then Ctrl+D

# Grant access
gcloud secrets add-iam-policy-binding openai-api-key \
  --member="serviceAccount:your-sa@project.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### Production Checklist

- [ ] Environment variables secured (Secret Manager)
- [ ] Database backups configured
- [ ] SSL certificates installed
- [ ] CDN configured for static assets
- [ ] Monitoring & alerting setup
- [ ] Rate limiting enabled
- [ ] CORS configured properly
- [ ] Error tracking (Sentry/Rollbar)
- [ ] Load testing completed
- [ ] Security audit passed

See full [Production Deployment Checklist](./PRODUCTION_DEPLOYMENT_CHECKLIST.md)

---

## Environment Configuration

### Required Variables

| Variable            | Description       | Example                  | Required       |
| ------------------- | ----------------- | ------------------------ | -------------- |
| `OPENAI_API_KEY`    | OpenAI API key    | `sk-...`                 | One of AI keys |
| `ANTHROPIC_API_KEY` | Anthropic API key | `sk-ant-...`             | One of AI keys |
| `GOOGLE_API_KEY`    | Google Gemini key | `AIza...`                | One of AI keys |
| `OLLAMA_BASE_URL`   | Ollama server URL | `http://localhost:11434` | No (auto)      |

### Optional Variables

| Variable                | Description         | Default | Notes                      |
| ----------------------- | ------------------- | ------- | -------------------------- |
| `DATABASE_URL`          | Database connection | SQLite  | PostgreSQL for production  |
| `REDIS_URL`             | Redis cache         | None    | Recommended for production |
| `RATE_LIMIT_PER_MINUTE` | API rate limit      | 60      | Adjust based on usage      |
| `LOG_LEVEL`             | Logging level       | `INFO`  | `DEBUG` for development    |
| `CORS_ORIGINS`          | Allowed origins     | `*`     | Restrict in production     |

### Social Media (Phase 2)

| Variable                 | Description      | Where to Get                                   |
| ------------------------ | ---------------- | ---------------------------------------------- |
| `TWITTER_API_KEY`        | Twitter API key  | https://developer.twitter.com/                 |
| `FACEBOOK_APP_ID`        | Facebook app ID  | https://developers.facebook.com/               |
| `INSTAGRAM_ACCESS_TOKEN` | Instagram token  | https://developers.facebook.com/docs/instagram |
| `LINKEDIN_CLIENT_ID`     | LinkedIn OAuth   | https://www.linkedin.com/developers/           |
| `TIKTOK_CLIENT_KEY`      | TikTok API key   | https://developers.tiktok.com/                 |
| `YOUTUBE_API_KEY`        | YouTube Data API | https://console.cloud.google.com/              |

---

## Troubleshooting

### Common Issues

#### Issue: Port Already in Use

**Symptom:** `Error: listen EADDRINUSE: address already in use :::3000`

**Solution:**

```powershell
# Windows
netstat -ano | findstr :3000
taskkill /PID <process_id> /F

# macOS/Linux
lsof -ti:3000 | xargs kill -9
```

#### Issue: Module Not Found

**Symptom:** `Error: Cannot find module 'xyz'`

**Solution:**

```bash
# Clear and reinstall
rm -rf node_modules package-lock.json
npm install

# Or for Python
pip install --upgrade --force-reinstall -r requirements.txt
```

#### Issue: Ollama Not Responding

**Symptom:** `Connection refused to http://localhost:11434`

**Solution:**

```bash
# Start Ollama service
ollama serve

# Check if running
curl http://localhost:11434/api/tags

# Restart if needed
pkill ollama
ollama serve
```

#### Issue: Database Connection Error

**Symptom:** `OperationalError: unable to open database file`

**Solution:**

```bash
# Create database directory
mkdir -p cms/strapi-v5-backend/.tmp

# Reset database
rm cms/strapi-v5-backend/.tmp/data.db
npm run dev:strapi
```

#### Issue: API Authentication Failed

**Symptom:** `401 Unauthorized` or `Invalid API key`

**Solution:**

1. Check `.env` file exists in `src/cofounder_agent/`
2. Verify API key is correct (no spaces/quotes)
3. Check API key has sufficient credits/quota
4. Test key directly:

```bash
# Test OpenAI
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer YOUR_API_KEY"

# Test Anthropic
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: YOUR_API_KEY" \
  -H "anthropic-version: 2023-06-01"
```

#### Issue: Strapi Admin Panel Not Loading

**Symptom:** Blank page or endless loading

**Solution:**

```bash
# Clear Strapi cache
cd cms/strapi-v5-backend
rm -rf .cache build

# Rebuild
npm run build
npm run develop
```

### Getting Help

1. **Check Logs:**
   - Backend: Console output from FastAPI
   - Frontend: Browser console (F12)
   - Strapi: Terminal where `npm run dev:strapi` is running

2. **Verify Services:**

   ```bash
   .\scripts\quick-test-api.ps1  # Test all API endpoints
   ```

3. **Enable Debug Mode:**

   ```bash
   # In .env
   DEBUG=True
   LOG_LEVEL=DEBUG
   ```

4. **Community Support:**
   - GitHub Issues
   - Documentation: http://localhost:3001/docs

---

## Next Steps

After setup is complete:

1. ✅ **Test the System**
   - Run quick test: `.\scripts\quick-test-api.ps1`
   - Check Oversight Hub: http://localhost:3001
   - Try creating a task

2. ✅ **Configure AI Models**
   - Navigate to Models page: http://localhost:3001/models
   - Toggle active providers
   - Test each model

3. ✅ **Explore Features**
   - See [Oversight Hub Quick Start](./OVERSIGHT_HUB_QUICK_START.md)
   - Try [Phase 2 Features](./PHASE_2_IMPLEMENTATION.md)
   - Read [Developer Guide](./02-DEVELOPER_GUIDE.md)

4. ✅ **Start Developing**
   - Read [Technical Design](./03-TECHNICAL_DESIGN.md)
   - Check [API Reference](./04-API_REFERENCE.md)
   - Follow [GLAD Labs Standards](./GLAD-LABS-STANDARDS.md)

---

## Quick Reference

### Essential Commands

```bash
# Start all services
npm run dev

# Stop all services
Ctrl+C (in each terminal)

# Run tests
npm test
pytest src/

# Check API health
curl http://localhost:8000/

# View logs
docker-compose logs -f

# Rebuild after changes
npm run build
```

### Port Reference

| Service       | Port  | URL                        |
| ------------- | ----- | -------------------------- |
| Public Site   | 3000  | http://localhost:3000      |
| Oversight Hub | 3001  | http://localhost:3001      |
| Strapi CMS    | 1337  | http://localhost:1337      |
| Backend API   | 8000  | http://localhost:8000      |
| API Docs      | 8000  | http://localhost:8000/docs |
| Ollama        | 11434 | http://localhost:11434     |

---

<div align="center">

**[← Back to Documentation Hub](./00-README.md)**

[Developer Guide](./02-DEVELOPER_GUIDE.md) • [Technical Design](./03-TECHNICAL_DESIGN.md) • [API Reference](./04-API_REFERENCE.md)

</div>
