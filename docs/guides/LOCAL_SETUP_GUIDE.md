# 🚀 GLAD Labs Local Setup & Testing Guide

## Overview

This guide will help you set up the complete GLAD Labs platform locally and test the full end-to-end pipeline.

---

## 📋 Prerequisites

### Required Software

- **Node.js** 20.11.1+ with npm
- **Python** 3.12+
- **Git** for version control
- **Ollama** (optional) for zero-cost local AI

### Optional (for full production testing)

- **Docker** and Docker Compose
- **PostgreSQL** 14+ (or use SQLite for development)
- **Redis** 7+ (for caching)

---

## 🔧 Step 1: Clone and Install Dependencies

```powershell
# Clone repository
cd C:\Users\mattm\glad-labs-website

# Install all Node.js dependencies
npm install

# Install Python dependencies
pip install -r scripts/requirements.txt
pip install -r src/cofounder_agent/requirements.txt
```

---

## 🔑 Step 2: Configure Environment Variables

### Root .env file

```powershell
# Copy example
Copy-Item .env.example .env

# Edit .env with your values
notepad .env
```

**Minimum required values for local testing:**

```env
# AI API Keys (at least one required)
OPENAI_API_KEY=sk-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
# OR for zero-cost local inference:
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434

# Strapi Configuration
STRAPI_JWT_SECRET=your-32-char-secret-here
STRAPI_ADMIN_JWT_SECRET=your-32-char-admin-secret-here
STRAPI_APP_KEYS=key1,key2,key3,key4
STRAPI_API_TOKEN_SALT=your-32-char-salt-here
STRAPI_API_TOKEN=your-strapi-api-token

# Database (SQLite for development)
DATABASE_CLIENT=sqlite
DATABASE_FILENAME=.tmp/data.db

# API URLs
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
REACT_APP_API_URL=http://localhost:8000
REACT_APP_STRAPI_URL=http://localhost:1337
```

### Generate Secrets

```powershell
# Generate random secrets (PowerShell)
[Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Minimum 0 -Maximum 256 }))
```

### Strapi CMS .env

```powershell
Copy-Item cms/strapi-v5-backend/.env.example cms/strapi-v5-backend/.env
notepad cms/strapi-v5-backend/.env
```

### AI Co-Founder .env

```powershell
Copy-Item src/cofounder_agent/.env.example src/cofounder_agent/.env
notepad src/cofounder_agent/.env
```

---

## 💰 Step 3: Setup Ollama (Zero-Cost Option)

### Install Ollama

```powershell
# Windows
winget install Ollama.Ollama

# Verify installation
ollama --version
```

### Pull Models

```powershell
# Recommended for development
ollama pull mistral

# Optional: Other models
ollama pull phi           # Smaller, faster
ollama pull codellama     # Code-focused
ollama pull mixtral       # More powerful
```

### Enable in GLAD Labs

```powershell
# Add to .env
$env:USE_OLLAMA = "true"
$env:OLLAMA_HOST = "http://localhost:11434"

# Or add to all .env files:
echo "USE_OLLAMA=true" >> .env
echo "OLLAMA_HOST=http://localhost:11434" >> .env
```

### Start Ollama Server

```powershell
# Start Ollama service (if not auto-started)
ollama serve

# Test connection
ollama list
```

---

## 🗄️ Step 4: Initialize Strapi CMS

### Build Strapi

```powershell
cd cms/strapi-v5-backend
npm install
npm run build
```

### Create Admin User

```powershell
# Start Strapi in development mode
npm run develop
```

1. Open browser: `http://localhost:1337/admin`
2. Create admin account (first-time setup)
3. Username: `admin`
4. Email: your email
5. Password: your password

### Generate API Token

1. Go to Settings → API Tokens → Create new API Token
2. Name: `Next.js Public Site`
3. Token type: `Full access` (for development)
4. Copy the token and add to `.env`:

```env
STRAPI_API_TOKEN=your-generated-token-here
```

### Create Content Types (if not existing)

1. Go to Content-Type Builder
2. Create collection type: **Posts**
   - Text field: `title` (required)
   - Text field: `slug` (required, unique)
   - Rich text: `content`
   - Text area: `excerpt`
   - Boolean: `featured`
   - Relation: `category` (belongs to Category)
   - Relation: `tags` (has many Tags)
3. Create collection type: **Categories**
   - Text: `name` (required)
   - Text: `slug` (required, unique)
4. Create collection type: **Tags**
   - Text: `name` (required)
   - Text: `slug` (required, unique)

### Configure Permissions

1. Settings → Roles → Public
2. Enable permissions for Posts, Categories, Tags:
   - `find` (list all)
   - `findOne` (get by ID)

---

## 🐍 Step 5: Setup Python Services

### Install Python Dependencies

```powershell
# Install co-founder agent dependencies
cd src/cofounder_agent
pip install -r requirements.txt

# Return to root
cd ../..
```

### Verify Python Setup

```powershell
# Test imports
python -c "from fastapi import FastAPI; from pydantic import BaseModel; print('✅ FastAPI OK')"

# Test Ollama client (if using Ollama)
python -c "import sys; sys.path.insert(0, 'src'); from cofounder_agent.services.ollama_client import OllamaClient; print('✅ Ollama client OK')"

# Test model router
python -c "import sys; sys.path.insert(0, 'src'); from cofounder_agent.services.model_router import ModelRouter; print('✅ Model router OK')"
```

---

## 🚀 Step 6: Start All Services

### Option A: Start Individual Services (Recommended for Development)

**Terminal 1: Strapi CMS**

```powershell
cd cms/strapi-v5-backend
npm run develop
# Runs on http://localhost:1337
```

**Terminal 2: AI Co-Founder API**

```powershell
cd src/cofounder_agent
python start_server.py
# Runs on http://localhost:8000
```

**Terminal 3: Next.js Public Site**

```powershell
cd web/public-site
npm install
npm run dev
# Runs on http://localhost:3000
```

**Terminal 4: Oversight Hub (Optional)**

```powershell
cd web/oversight-hub
npm install
npm start
# Runs on http://localhost:3001
```

### Option B: Use VS Code Tasks (Easiest)

1. Open VS Code
2. Press `Ctrl+Shift+P`
3. Type "Tasks: Run Task"
4. Select "Start All Services"

Or individually:

- "Start Strapi CMS"
- "Start Co-founder Agent"
- "Start Public Site"
- "Start Oversight Hub"

### Option C: Use npm scripts (from root)

```powershell
# Start all services in parallel
npm run dev

# Or individually:
npm run dev:strapi        # Strapi CMS
npm run dev:cofounder     # AI Co-Founder
npm run dev:public        # Public Site
npm run dev:oversight     # Oversight Hub
```

---

## ✅ Step 7: Verify Services are Running

### Health Checks

```powershell
# Strapi CMS
curl http://localhost:1337/_health

# AI Co-Founder API
curl http://localhost:8000/metrics/health

# Public Site
curl http://localhost:3000/api/health

# Oversight Hub
curl http://localhost:3001/health
```

### API Documentation

- **Strapi Admin**: http://localhost:1337/admin
- **AI Co-Founder Docs**: http://localhost:8000/docs
- **Public Site**: http://localhost:3000
- **Oversight Hub**: http://localhost:3001

---

## 🧪 Step 8: Test End-to-End Pipeline

### Test 1: Ollama Local Inference

```powershell
# Ensure Ollama is running
ollama list

# Test generation via API
curl -X POST http://localhost:8000/command `
  -H "Content-Type: application/json" `
  -d '{"command": "Generate a short blog post about AI", "context": {}}'
```

### Test 2: Content Creation Flow

1. **Open Oversight Hub**: http://localhost:3001
2. **Navigate to Content tab**
3. **Create new content task**:
   - Topic: "AI in Healthcare"
   - Type: "Blog Post"
4. **Monitor task progress**
5. **Verify in Strapi**: http://localhost:1337/admin
6. **Check Public Site**: http://localhost:3000

### Test 3: Cost Tracking

```powershell
# Get current cost metrics
curl http://localhost:8000/metrics/costs

# Get financial analysis
curl http://localhost:8000/financial/cost-analysis

# Get monthly summary
curl http://localhost:8000/financial/monthly-summary
```

### Test 4: Model Routing

```powershell
# Test with Ollama (zero-cost)
$env:USE_OLLAMA = "true"
curl -X POST http://localhost:8000/command `
  -H "Content-Type: application/json" `
  -d '{"command": "What is AI?", "context": {}}'

# Test with cloud API (costs money)
$env:USE_OLLAMA = "false"
curl -X POST http://localhost:8000/command `
  -H "Content-Type: application/json" `
  -d '{"command": "What is AI?", "context": {}}'
```

---

## 🐛 Common Issues and Fixes

### Issue 1: "Module not found" errors

**Symptom**: `ModuleNotFoundError: No module named 'services'`

**Fix**:

```powershell
# Ensure Python path is correct
cd src/cofounder_agent
python -c "import sys; print(sys.path)"

# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

### Issue 2: Strapi connection refused

**Symptom**: `ECONNREFUSED localhost:1337`

**Fix**:

```powershell
# Ensure Strapi is running
cd cms/strapi-v5-backend
npm run develop

# Check if port is in use
netstat -ano | findstr :1337
```

### Issue 3: Ollama connection failed

**Symptom**: `Connection refused to localhost:11434`

**Fix**:

```powershell
# Start Ollama service
ollama serve

# Verify Ollama is running
ollama list

# Check environment variable
echo $env:USE_OLLAMA
echo $env:OLLAMA_HOST
```

### Issue 4: Port already in use

**Symptom**: `Error: Port 8000 is already in use`

**Fix**:

```powershell
# Find process using port
netstat -ano | findstr :8000

# Kill process (replace PID with actual process ID)
Stop-Process -Id <PID> -Force

# Or use different port
$env:COFOUNDER_AGENT_PORT = "8001"
```

### Issue 5: API key not configured

**Symptom**: `401 Unauthorized` or `API key missing`

**Fix**:

```powershell
# Check .env file exists
Test-Path .env
Test-Path src/cofounder_agent/.env

# Verify API keys are set
echo $env:OPENAI_API_KEY
echo $env:ANTHROPIC_API_KEY

# Set temporarily
$env:OPENAI_API_KEY = "sk-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
```

### Issue 6: Strapi admin can't login

**Symptom**: JWT errors or "Invalid token"

**Fix**:

```powershell
# Regenerate JWT secrets
cd cms/strapi-v5-backend

# Add to .env (generate new secrets)
STRAPI_JWT_SECRET=<new-32-char-secret>
STRAPI_ADMIN_JWT_SECRET=<new-32-char-admin-secret>

# Rebuild and restart
npm run build
npm run develop
```

### Issue 7: React app blank page

**Symptom**: White screen in browser

**Fix**:

```powershell
# Check browser console for errors
# Clear npm cache
cd web/public-site
rm -rf node_modules package-lock.json
npm install

# Rebuild
npm run build
npm run dev
```

---

## 🧪 Running Tests

### Python Tests

```powershell
# All tests
cd src/cofounder_agent
python -m pytest tests/ -v

# Specific test file
python -m pytest tests/test_ollama_client.py -v

# Integration tests (requires Ollama running)
python -m pytest tests/integration/ -v -m integration

# Financial agent tests
cd ../agents/financial_agent
python -m pytest tests/ -v
```

### Frontend Tests

```powershell
# Next.js tests
cd web/public-site
npm test

# Oversight Hub tests
cd ../oversight-hub
npm test
```

---

## 📊 Monitoring and Logs

### View Logs

```powershell
# AI Co-Founder logs
Get-Content src/cofounder_agent/logs/cofounder.log -Tail 50 -Wait

# Strapi logs (in terminal running Strapi)

# Next.js logs (in terminal running Next.js)
```

### Performance Metrics

```powershell
# System health
curl http://localhost:8000/metrics/health

# Performance metrics
curl http://localhost:8000/metrics/performance?hours=24

# Cost metrics
curl http://localhost:8000/metrics/costs
```

---

## 🔄 Development Workflow

### Making Code Changes

1. **Edit code** in VS Code
2. **Save file** - services auto-reload
3. **Test changes** via API or UI
4. **Check logs** for errors
5. **Run tests** to verify

### Testing Ollama Changes

```powershell
# Enable Ollama
$env:USE_OLLAMA = "true"

# Make changes to model_router.py or ollama_client.py

# Restart AI Co-Founder API
cd src/cofounder_agent
python start_server.py

# Test via API
curl -X POST http://localhost:8000/command `
  -H "Content-Type: application/json" `
  -d '{"command": "Test Ollama routing", "context": {}}'
```

### Adding Content

1. **Create content in Strapi**: http://localhost:1337/admin
2. **Verify API response**:

```powershell
curl http://localhost:1337/api/posts
```

1. **Check Public Site**: http://localhost:3000
2. **Trigger rebuild** (if needed):

```powershell
cd web/public-site
npm run build
npm run dev
```

---

## 🎯 Next Steps

### For Development

1. **Explore Ollama models**: Try different models (phi, mistral, mixtral)
2. **Test cost optimization**: Monitor savings with `USE_OLLAMA=true`
3. **Create content**: Use Oversight Hub to generate blog posts
4. **Monitor performance**: Check dashboards and metrics

### For Production

1. **Setup PostgreSQL**: Migrate from SQLite
2. **Configure Redis**: Enable caching
3. **Setup CI/CD**: GitHub Actions workflows
4. **Deploy services**: Use Docker or cloud platforms

### Additional Resources

- **Ollama Setup**: [docs/OLLAMA_SETUP.md](./OLLAMA_SETUP.md)
- **Architecture**: [docs/ARCHITECTURE.md](./ARCHITECTURE.md)
- **Developer Guide**: [docs/DEVELOPER_GUIDE.md](./DEVELOPER_GUIDE.md)
- **Testing Guide**: [docs/TEST_IMPLEMENTATION_COMPLETE.md](./TEST_IMPLEMENTATION_COMPLETE.md)

---

## 📞 Support

**Issues found?**

1. Check logs for error messages
2. Review this guide's "Common Issues" section
3. Check documentation in `docs/` folder
4. Verify environment variables are set correctly

**Last Updated**: October 15, 2025  
**Version**: 1.0  
**Maintainer**: GLAD Labs Development Team
