# 🚂 Railway Deployment Guide - Python Co-Founder Agent

**Date**: October 22, 2025  
**Project**: GLAD Labs Co-Founder Agent (FastAPI + Python)  
**Deployment**: `src/cofounder_agent/` to Railway

---

## 📋 Overview

Deploy your FastAPI Python application (Co-Founder Agent) to Railway for:

- ✅ Production-grade hosting
- ✅ Auto-scaling capabilities
- ✅ PostgreSQL database integration
- ✅ Environment variable management
- ✅ Automatic HTTPS
- ✅ Monitoring & logging

---

## 🎯 Prerequisites

Before starting, ensure you have:

### 1. Railway Account

- [ ] Sign up at https://railway.app
- [ ] Verify email
- [ ] Add payment method (free tier available for testing)

### 2. Required Tools

```bash
# Install Railway CLI
npm install -g @railway/cli

# Or with Homebrew (macOS)
brew install railway

# Verify installation
railway --version
```

### 3. Git Setup

```bash
# Ensure you're on the correct branch
git status

# Expected output: On branch feat/cost-optimization
# (or whichever branch you want to deploy)

# Check remote
git remote -v
# Expected: origin → github.com/mattg-stack/glad-labs-website
```

### 4. Environment Variables

Collect all required environment variables (see checklist below)

---

## 🔐 Environment Variables Checklist

### Required for FastAPI Server

```bash
# ========== LLM Provider Configuration ==========
LLM_PROVIDER="local"                    # or "gemini"
LOCAL_LLM_API_URL="http://localhost:11434"
LOCAL_LLM_MODEL_NAME="neural-chat:13b"

# ========== Gemini API (Fallback) ==========
GEMINI_API_KEY="your_gemini_key_here"
GEMINI_MODEL="gemini-2.5-flash"

# ========== Google Cloud Setup ==========
GCP_PROJECT_ID="gen-lang-client-0031944915"
GCP_SERVICE_ACCOUNT_EMAIL="content-agent-sa@gen-lang-client-0031944915.iam.gserviceaccount.com"
GCP_REGION="us-central1"

# ========== Strapi CMS ==========
STRAPI_API_URL="https://your-strapi.railway.app/api"
STRAPI_API_TOKEN="your_strapi_token_here"

# ========== Google Cloud Storage ==========
GCS_BUCKET_NAME="content-agent-images"

# ========== Firestore ==========
FIRESTORE_COLLECTION="agent_runs"

# ========== External APIs ==========
PEXELS_API_KEY="wdq7jNG49KWxBipK90hu32V5RLpXD0I5J81n61WeQzh31sdGJ9sua1qT"
SERPER_API_KEY="fcb6eb4e893705dc89c345576950270d75c874b3"

# ========== Google Cloud Pub/Sub ==========
PUBSUB_TOPIC="agent-commands"
PUBSUB_SUBSCRIPTION="content-agent-subscription"

# ========== Development Mode ==========
DEV_MODE="false"
USE_MOCK_SERVICES="false"

# ========== Application ==========
ENVIRONMENT="production"
DEBUG="false"
```

---

## 🚀 Step-by-Step Deployment

### Step 1: Initialize Railway Project

```bash
# Navigate to workspace root
cd c:\Users\mattm\glad-labs-website

# Login to Railway
railway login
# Opens browser for authentication

# Create new Railway project
railway init
# Follow prompts:
# - Choose "Create a new project"
# - Name: "glad-labs-cofounder-agent"
# - Environment: Leave default or choose "production"

# Or link existing project
railway link
# If you already have a Railway project
```

### Step 2: Configure Railway for Python/FastAPI

Railway will auto-detect but let's ensure it's correct:

```bash
# Check current configuration
railway whoami

# Set project (if needed)
railway project select
# Select "glad-labs-cofounder-agent"

# Verify environment
railway environment list
```

### Step 3: Add PostgreSQL Database (Optional)

If your app needs PostgreSQL:

```bash
# Add PostgreSQL plugin
railway add
# Select: PostgreSQL
# Accept defaults for credentials

# This auto-sets DATABASE_URL env var
```

### Step 4: Set Environment Variables

```bash
# Add each environment variable
railway variables set LLM_PROVIDER="local"
railway variables set GEMINI_API_KEY="your_key"
railway variables set STRAPI_API_URL="https://strapi.railway.app/api"
# ... (repeat for all env vars from checklist above)

# Or batch import from file
# Create file: railway-env.txt
# Format:
# KEY1=value1
# KEY2=value2

# Then:
# railway variables import < railway-env.txt

# Verify variables set
railway variables
```

### Step 5: Create Procfile (CRITICAL!)

Railway needs a `Procfile` to know how to start your FastAPI app.

**File location**: Create at PROJECT ROOT: `Procfile`  
**File contents**:

```
web: cd src/cofounder_agent && python -m uvicorn main:app --host 0.0.0.0 --port $PORT
```

**Important**:

- ⚠️ File must be named exactly `Procfile` (no extension)
- ⚠️ Must be at PROJECT ROOT (same level as `package.json`)
- ⚠️ Must use `$PORT` variable (NOT hardcoded port)
- ⚠️ Command must start with `web:`

**Why**: Railway's Railpack auto-detects FastAPI ONLY if:

1. `main.py` is at project root, OR
2. A `Procfile` tells it where to start

Since our `main.py` is in `src/cofounder_agent/`, the Procfile is REQUIRED.

**To verify**:

```bash
# Check file exists at project root
ls Procfile

# Check contents (should show exactly):
# web: cd src/cofounder_agent && python -m uvicorn main:app --host 0.0.0.0 --port $PORT
cat Procfile
```

**Add to Git**:

```bash
git add Procfile
git commit -m "Add Procfile for Railway deployment"
```

### Step 6: Add Python Dependencies

Railway auto-detects `requirements.txt`. Ensure it exists:

```bash
# Check if exists
ls src/cofounder_agent/requirements.txt

# If not, create it from your pip environment
cd src/cofounder_agent
pip freeze > requirements.txt

# Or copy from your existing setup
# Add key dependencies at minimum:
# - fastapi
# - uvicorn
# - pydantic
# - google-cloud-firestore (if using GCP)
# - google-cloud-storage
# - aiohttp
# - httpx
# - requests
# - structlog
# - python-dotenv
```

### Step 7: Deploy!

```bash
# Option A: Deploy from current branch
railway up
# This uses Railway CLI to push code

# Option B: Deploy from GitHub (Recommended)
# 1. Push code to GitHub first
git add .
git commit -m "feat: Deploy to Railway with Pexels + Serper APIs"
git push origin feat/cost-optimization

# 2. Go to https://railway.app
# 3. Create new project
# 4. Select GitHub repo: glad-labs-website
# 5. Set branch: feat/cost-optimization
# 6. Set root directory: src/cofounder_agent
# 7. Add environment variables
# 8. Deploy!
```

### Step 8: Monitor Deployment

```bash
# Watch deployment logs
railway logs --follow

# Expected output:
# INFO: Uvicorn running on http://0.0.0.0:5000
# INFO: Application startup complete
# ...healthy logs...

# Check deployment status
railway status

# Visit your app
railway open
# Opens https://your-app.railway.app in browser
```

---

## ✅ Verification Checklist

After deployment, verify everything works:

### Health Check

```bash
# Test API health
curl https://your-app.railway.app/health

# Expected response:
# {"status": "healthy"}
```

### Test Endpoints

```bash
# Get models
curl https://your-app.railway.app/api/v1/models/available

# Create blog post (test)
curl -X POST https://your-app.railway.app/api/v1/content/create-blog-post \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Test Post",
    "generate_featured_image": true
  }'
```

### Monitor Logs

```bash
# Check for errors
railway logs | grep ERROR

# Should be empty or minimal
```

### Check Costs

```bash
# View Railway project costs
# Go to: https://railway.app → Projects → your-project → Billing
# Expected: $0-5/month for small usage
```

---

## 🔗 Integration with Other Services

### Connect to Strapi (Railway)

If Strapi is also on Railway:

```bash
# Get Strapi internal URL from Railway dashboard
# Usually: http://strapi:1337 (internal) or https://strapi.railway.app (external)

# Set environment variable
railway variables set STRAPI_API_URL="http://strapi:1337/api"

# Or use external URL if they're separate projects
railway variables set STRAPI_API_URL="https://your-strapi.railway.app/api"
```

### Connect to Frontend (Vercel)

If Oversight Hub is on Vercel:

```bash
# Add CORS header to allow Vercel domain
# Add to your FastAPI app (main.py):
# CORS origins: ["https://hub.gladlabs.ai", "http://localhost:3001"]

# Update Oversight Hub env var to point to Railway:
# REACT_APP_COFOUNDER_URL=https://your-app.railway.app
```

---

## 🐛 Troubleshooting

### Deployment Fails - "No start command was found"

**Error**: Railpack repeatedly shows "No start command was found" and deployment fails

**Root cause**: Railway can't find how to start your app because:

1. `main.py` is not at project root
2. No `Procfile` exists to tell it where to start

**Solution**:

```bash
# 1. Create Procfile at PROJECT ROOT
# File: Procfile (not Procfile.txt, not in src/)
# Contents:
web: cd src/cofounder_agent && python -m uvicorn main:app --host 0.0.0.0 --port $PORT

# 2. Verify it's in the right place
ls Procfile
# Should find it at project root

# 3. Commit it
git add Procfile
git commit -m "Add Procfile for Railway deployment"
git push origin feat/cost-optimization

# 4. Retry deployment
# Go to Railway dashboard and retry build
```

**Why this works**: Procfile is the official way to tell Railway how to start an app.

### Deployment Fails - Port Binding

**Error**: `Port already in use` or `Connection refused`

**Solution**:

```bash
# Ensure Procfile uses $PORT variable
web: cd src/cofounder_agent && python -m uvicorn main:app --host 0.0.0.0 --port $PORT

# Not this:
# web: cd src/cofounder_agent && python -m uvicorn main:app --port 8000
```

### App Crashes - Missing Dependencies

**Error**: `ModuleNotFoundError: No module named 'fastapi'`

**Solution**:

```bash
# Regenerate requirements.txt
cd src/cofounder_agent
pip freeze > requirements.txt

# Push to Railway
git add requirements.txt
git commit -m "Update requirements.txt"
git push origin feat/cost-optimization

# Redeploy
railway up
```

### API Returns 502 Bad Gateway

**Error**: All requests return 502

**Solution**:

```bash
# Check logs for startup errors
railway logs

# Common causes:
# 1. Import errors in main.py
# 2. Missing environment variables
# 3. Database connection failed
# 4. Port not binding correctly

# Check each:
# 1. Verify all imports work: python -c "from main import app"
# 2. Check variables: railway variables
# 3. Check DB connection string
# 4. Check port: grep -n "port" main.py
```

### Environment Variables Not Working

**Error**: App can't read env vars

**Solution**:

```bash
# Verify they're set
railway variables

# Check Python is loading them
# Add to main.py:
import os
print(f"DEBUG: STRAPI_URL = {os.getenv('STRAPI_API_URL')}")

# Redeploy and check logs
railway logs

# Make sure env vars set BEFORE deployment
# Not after
```

---

## 📊 Monitoring & Logging

### View Logs

```bash
# Real-time logs
railway logs --follow

# Last N lines
railway logs --tail 100

# Filter by level
railway logs --level error
railway logs --level warn

# Export logs
railway logs > deployment.log
```

### Set Up Alerts

```bash
# In Railway dashboard:
# 1. Go to Project → Settings
# 2. Alerts section
# 3. Add alert for:
#    - Deployment failed
#    - High CPU usage
#    - High memory usage
#    - Error rate spike
```

### Monitor Performance

```bash
# Check resource usage
railway status

# Expected:
# - CPU: <50% normal
# - Memory: <500MB for FastAPI
# - Network: <10Mb/s
```

---

## 🔄 Updates & Redeployment

### Deploy Code Changes

```bash
# Make code changes locally
# ... edit files ...

# Commit
git add .
git commit -m "fix: Update API endpoint"

# Push to Railway (auto-deploys)
git push origin feat/cost-optimization

# Or manual deploy
railway up
```

### Update Environment Variables

```bash
# Update single variable
railway variables set MY_VAR="new_value"

# Remove variable
railway variables unset MY_VAR

# View all
railway variables
```

### Rollback to Previous Deployment

```bash
# View deployment history
railway deployments list

# Rollback to specific deployment
railway deployments rollback <deployment-id>

# Verify rollback
railway logs
```

---

## 💡 Pro Tips

### 1. Use Railway CLI for Local Testing

```bash
# Run with Railway environment locally
railway run python -m uvicorn main:app --reload

# This loads all Railway env vars locally
```

### 2. Set Different Env Per Environment

```bash
# Create staging environment
railway environment create staging

# Set staging-specific vars
railway --environment staging variables set DEBUG="true"

# Deploy to staging first, then production
railway --environment staging up
```

### 3. Monitor Cost

```bash
# Railway auto-scales, monitor spend:
# - Go to Project → Billing
# - Set spending limits
# - Get alerts at thresholds

# Cost breakdown:
# - Compute: ~$0.50/month (free tier)
# - Database: ~$7/month (PostgreSQL)
# - Total: $7-15/month typical
```

### 4. Use Railway CLI Shortcuts

```bash
# Status shortcut
alias railway-status="railway status"

# Log shortcut
alias railway-tail="railway logs --follow"

# Deploy shortcut
alias railway-deploy="git push && railway up"
```

---

## ✨ Success Indicators

After deployment, you should see:

✅ App accessible at `https://your-app.railway.app`  
✅ `/health` endpoint returns `{"status": "healthy"}`  
✅ API endpoints respond correctly  
✅ No error logs in first 5 minutes  
✅ Logs show "Application startup complete"  
✅ Dashboard shows green deployment status

---

## 📚 Additional Resources

- **Railway Docs**: https://docs.railway.app
- **Railway Dashboard**: https://railway.app
- **FastAPI Docs**: https://fastapi.tiangolo.com
- **Uvicorn Docs**: https://www.uvicorn.org

---

## 🎯 Next Steps

1. ✅ Set up Railway account
2. ✅ Install Railway CLI
3. ✅ Prepare environment variables
4. ✅ Deploy code (push to GitHub or use `railway up`)
5. ✅ Verify deployment works
6. ✅ Set up monitoring & alerts
7. ✅ Update Strapi/Frontend to use new URL

**Status**: Ready to deploy! 🚀
