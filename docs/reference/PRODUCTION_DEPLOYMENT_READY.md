# 🚀 Production Deployment Ready - Complete Guide

**Status**: Ready to Deploy  
**Last Updated**: October 20, 2025  
**Target**: gladlabs.io + Vercel + Railway + GCP + Local Ollama

---

## 📋 Quick Navigation

- **[← Back to Docs](./00-README.md)**
- **[Branch Workflow](#-branch-workflow-for-production)** - How to push to production
- **[GitHub Secrets](#-github-secrets-setup)** - What needs configuration
- **[Deployment Checklist](#-pre-deployment-checklist)** - Final verification
- **[Ollama Integration](#-ollama-local-llm-setup)** - Local LLM support
- **[Monitoring](#-production-monitoring)** - After deployment

---

## 🏗️ Production Architecture Overview

```txt
┌─────────────────────────────────────────────────────────────┐
│                  GLAD LABS PRODUCTION SETUP                 │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  FRONTEND (Vercel)                                           │
│  ├─ https://gladlabs.io ..................... Public Site    │
│  └─ https://app.gladlabs.io (optional) ....... Oversight Hub │
│                                                               │
│  BACKEND (Railway)                                           │
│  └─ https://glad-labs-website-production.up.railway.app     │
│      ├─ Strapi CMS (Port 1337)                              │
│      ├─ GraphQL API                                         │
│      └─ PostgreSQL Database                                 │
│                                                               │
│  AGENTS (GCP Cloud Functions)                               │
│  ├─ FastAPI Co-Founder Agent (HTTP trigger)                │
│  ├─ Content Agent                                           │
│  ├─ Compliance Agent                                        │
│  └─ Financial Agent                                         │
│                                                               │
│  LOCAL DEVELOPMENT (Your Machine)                           │
│  ├─ Ollama (Local LLM - Llama 2, Mistral, etc.)            │
│  ├─ Environment: OLLAMA_ENABLED=true                        │
│  └─ Fallback to online services if unavailable              │
│                                                               │
│  MONITORING & OBSERVABILITY                                 │
│  ├─ Vercel Analytics (Frontend)                             │
│  ├─ Railway Logs (Backend)                                  │
│  ├─ GCP Cloud Logging (Functions)                           │
│  └─ Application Health Checks                               │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔑 GitHub Secrets Setup

### Step 1: Generate Required Secrets

You need to configure these in GitHub Settings → Secrets and Variables → Actions:

#### **Vercel Secrets**

1. **Get Vercel Token:**

   ```bash
   # Login to Vercel dashboard
   # Go to: Settings → Tokens → Create token (Personal access token)
   ```

   - Secret Name: `VERCEL_TOKEN`
   - Secret Value: (your token)

2. **Get Vercel Organization ID:**

   ```bash
   # In Vercel dashboard, go to any project
   # Look for: Settings → General → "Vercel URL"
   # Copy the org/project ID
   # Or run: vercel project inspect
   ```

   - Secret Name: `VERCEL_ORG_ID`
   - Secret Value: (your org ID)

3. **Get Vercel Project ID:**

   ```bash
   # For each project (public-site, oversight-hub):
   # vercel project inspect public-site
   # Copy projectId
   ```

   - Secret Name: `VERCEL_PROJECT_ID_PUBLIC`
   - Secret Value: (public-site project ID)
   - Secret Name: `VERCEL_PROJECT_ID_OVERSIGHT`
   - Secret Value: (oversight-hub project ID)

#### **Railway Secrets**

1. **Get Railway Token:**

   ```bash
   # Login to Railway dashboard
   # Go to: Account → API Tokens → Create new token
   ```

   - Secret Name: `RAILWAY_TOKEN`
   - Secret Value: (your token)

2. **Get Railway Project ID:**

   ```bash
   # In Railway, go to your Strapi project
   # Look at URL: https://railway.app/project/{PROJECT_ID}
   ```

   - Secret Name: `RAILWAY_PROJECT_ID`
   - Secret Value: (your project ID)

#### **Strapi Secrets**

1. **Strapi API Token:**

   ```bash
   # Generate in Strapi admin panel
   # Go to: Settings → API Tokens → Create new API Token
   # Use "Full access" token for CI/CD
   ```

   - Secret Name: `STRAPI_API_TOKEN`
   - Secret Value: (full access token)

2. **Strapi Database URLs:**
   - Secret Name: `DATABASE_URL_PRODUCTION`
   - Secret Value: (from Railway PostgreSQL connection string)
   - Secret Name: `DATABASE_URL_STAGING`
   - Secret Value: (from Railway staging database)

#### **Google Cloud Secrets**

1. **GCP Service Account Key:**

   ```bash
   # Create service account in GCP Console
   # Project → Service Accounts → Create → Download JSON key
   ```

   - Secret Name: `GCP_SERVICE_ACCOUNT_KEY`
   - Secret Value: (JSON content, base64 encoded if needed)

2. **GCP Project ID:**
   - Secret Name: `GCP_PROJECT_ID`
   - Secret Value: (your GCP project ID)

#### **API Keys & Model Providers**

- Secret Name: `OPENAI_API_KEY`
- Secret Name: `ANTHROPIC_API_KEY`
- Secret Name: `GOOGLE_AI_API_KEY`
- Secret Name: `XAI_API_KEY`
- Secret Name: `META_API_KEY` (if using Meta Llama through API)

---

## 🔄 Branch Workflow for Production

### Step-by-Step: Local Development → Production

#### **Step 1: Feature Development (feat/test-branch)**

```bash
# You're on: feat/test-branch

# Make changes, test locally
npm run dev
npm run test
npm run lint:fix

# Commit and push
git add .
git commit -m "feat: add production-ready pages"
git push origin feat/test-branch
```

**What happens:**

- GitHub Actions: `test-on-feat.yml` runs
- Tests execute (Jest + Python)
- Linting check completes
- ✅ or ❌ appears on PR

#### **Step 2: Merge to Staging (dev branch)**

```bash
# Create PR: feat/test-branch → dev
# Review changes
# Approve & merge

# Or manually:
git checkout dev
git pull origin dev
git merge feat/test-branch
git push origin dev
```

**What happens:**

- GitHub Actions: `deploy-staging.yml` triggers
- Loads `.env.staging` environment
- Runs full test suite
- Deploys to Railway staging
- Wait 2-3 minutes for deployment

**Test staging:**

```bash
# Visit staging environment
https://staging-cms.railway.app
# Test all features
# Verify Strapi content works
# Check no errors in logs
```

#### **Step 3: Merge to Production (main branch)**

```bash
# Create PR: dev → main
# Final review (critical!)
# Approve & merge

# Or manually:
git checkout main
git pull origin main
git merge --no-ff dev
git push origin main
```

**What happens:**

- GitHub Actions: `deploy-production.yml` triggers
- Loads `.env.production` environment
- Runs complete test suite
- Deploys frontend to Vercel
- Deploys backend to Railway production
- Deploys functions to GCP
- Wait 5-10 minutes for full deployment

---

## ✅ Pre-Deployment Checklist

### Local Testing (Before Push)

```bash
# 1. Pull latest main
git checkout main
git pull origin main

# 2. Install dependencies
npm run clean:install

# 3. Run full test suite
npm run test

# 4. Run linting
npm run lint:fix

# 5. Test locally
npm run dev
# Visit: http://localhost:3000
# Visit: http://localhost:3001
# Visit: http://localhost:8000/docs

# 6. Manual testing
# - Homepage loads
# - About page loads
# - Privacy policy loads
# - Strapi content fetches correctly
# - No console errors
```

### Configuration Verification

```bash
# Check all environment files exist
ls -la .env .env.staging .env.production

# Verify GitHub Secrets are set (you can't see values, but check they exist)
# Go to: Settings → Secrets and Variables → Actions
# Should see all secrets from "GitHub Secrets Setup" section above
```

### Git & Branch Verification

```bash
# Verify you're on main
git branch -v

# Verify no uncommitted changes
git status
# Should show: "On branch main" and "nothing to commit"

# Verify commits are in order
git log --oneline -10
```

### Deployment Readiness

- [ ] All secrets configured in GitHub
- [ ] `.env.production` has correct values
- [ ] Tests pass locally (`npm run test`)
- [ ] Linting passes (`npm run lint`)
- [ ] `npm run dev` runs without blocking errors
- [ ] No console errors in browser
- [ ] Strapi content is accessible
- [ ] Ready for production release

---

## 🚀 Deployment Process

### Step-by-Step Production Push

```bash
# 1. FINAL LOCAL VERIFICATION
npm run test
npm run lint:fix
npm run build

# 2. PREPARE COMMITS
git checkout main
git pull origin main
git merge --no-ff dev
# Fix any merge conflicts if needed

# 3. PUSH TO MAIN (Triggers production deployment)
git push origin main

# 4. MONITOR GITHUB ACTIONS
# Go to: https://github.com/mattg-stack/glad-labs-website/actions
# Watch: deploy-production.yml workflow
# Wait for: ✅ All checks pass

# 5. MONITOR VERCEL DEPLOYMENT
# Go to: https://vercel.com/dashboard
# Watch: Deployment progress
# Wait for: Green checkmark

# 6. VERIFY PRODUCTION
# Frontend: https://gladlabs.io
# API Docs: https://glad-labs-website-production.up.railway.app/api/docs
# Strapi Admin: https://glad-labs-website-production.up.railway.app/admin

# 7. RUN SMOKE TESTS
# Visit each page
# Test key functionality
# Check network requests (DevTools)
```

---

## 🦙 Ollama Local LLM Setup

### What is Ollama?

Ollama lets you run LLMs locally on your machine. You can use it alongside online services (OpenAI, Anthropic, etc.) as a fallback or primary option.

### Installation

```bash
# Download Ollama from: https://ollama.ai

# Or on Mac:
brew install ollama

# Or on Linux:
curl https://ollama.ai/install.sh | sh

# Verify installation
ollama --version
```

### Running Ollama

```bash
# Start Ollama service
ollama serve

# In another terminal, pull a model
ollama pull llama2              # 3.8GB
ollama pull mistral             # 4.1GB
ollama pull neural-chat         # 4.0GB
ollama pull dolphin-mixtral     # 45GB (powerful but large)

# Test locally
curl http://localhost:11434/api/generate -d '{
  "model": "llama2",
  "prompt": "Why is the sky blue?",
  "stream": false
}'
```

### Configure FastAPI Agent to Use Ollama

**File: `src/cofounder_agent/main.py`**

```python
import os
from dotenv import load_dotenv

load_dotenv()

# Check if Ollama is enabled
OLLAMA_ENABLED = os.getenv('OLLAMA_ENABLED', 'false').lower() == 'true'
OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'llama2')

# Primary model configuration
if OLLAMA_ENABLED:
    PRIMARY_MODEL = f"ollama_{OLLAMA_MODEL}"
    print(f"[INFO] Using Ollama: {PRIMARY_MODEL} at {OLLAMA_BASE_URL}")
else:
    # Fall back to online services
    PRIMARY_MODEL = os.getenv('PRIMARY_MODEL', 'gpt-4')
    print(f"[INFO] Using online service: {PRIMARY_MODEL}")
```

**File: `.env` (Local Development)**

```bash
# Local development with Ollama
OLLAMA_ENABLED=true
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama2

# Fallback to online if needed
OPENAI_API_KEY=your-key
ANTHROPIC_API_KEY=your-key
```

**File: `.env.production`**

```bash
# Production: Use online services
OLLAMA_ENABLED=false

# Online service configuration
OPENAI_API_KEY=your-production-key
ANTHROPIC_API_KEY=your-production-key
GOOGLE_AI_API_KEY=your-production-key
XAI_API_KEY=your-production-key
```

### Testing Ollama Integration

```bash
# 1. Start Ollama
ollama serve

# 2. In another terminal, pull a model
ollama pull llama2

# 3. Start dev server
npm run dev

# 4. Test API endpoint
curl http://localhost:8000/api/generate \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hello, what is Glad Labs?"}'

# 5. Should respond with local LLM output
```

### Running Ollama on Production Machine (GCP)

If you want Ollama available in GCP (not recommended for cost/complexity, but possible):

```dockerfile
# Dockerfile for GCP Cloud Run with Ollama
FROM ollama/ollama

# Copy FastAPI app
COPY src/cofounder_agent /app

# Expose ports
EXPOSE 11434 8000

# Start both Ollama and FastAPI
CMD ["sh", "-c", "ollama serve & python /app/main.py"]
```

**However, easier approach:** Keep Ollama local only, use online services in production.

---

## 📊 Production Monitoring

### After Deployment: Verify Everything Works

#### **1. Frontend Verification (Vercel)**

```bash
# Visit site
https://gladlabs.io

# Check Console for errors
DevTools → Console → Should be clean

# Check Network tab
DevTools → Network → All requests should be 2xx/3xx

# Mobile responsive test
DevTools → Device toolbar → Test mobile view
```

#### **2. Backend Verification (Railway)**

```bash
# API Health Check
curl https://glad-labs-website-production.up.railway.app/api/health

# Strapi Admin
https://glad-labs-website-production.up.railway.app/admin
# Login with credentials
# Verify content is accessible

# API Documentation
https://glad-labs-website-production.up.railway.app/api/docs
# Test endpoints
```

#### **3. Agents Verification (GCP)**

```bash
# Test FastAPI endpoint
curl https://[YOUR-GCP-FUNCTION-URL]/api/test \
  -H "Authorization: Bearer [YOUR-TOKEN]"

# Check GCP Cloud Logging
# Go to: GCP Console → Cloud Logging
# Filter: resource.type="cloud_function"
# Look for errors or warnings
```

#### **4. Monitoring Dashboards**

**Vercel:**

- Dashboard: [vercel.com/dashboard](https://vercel.com/dashboard)
- Project: Watch deployment status
- Analytics: Check traffic patterns

**Railway:**

- Dashboard: [railway.app](https://railway.app)
- Logs: Real-time application logs
- Database: Check PostgreSQL status

**GitHub Actions:**

- Workflows: [GitHub Actions](https://github.com/mattg-stack/glad-labs-website/actions)
- Latest run: Should show green checkmarks

---

## 🔄 Continuous Deployment Workflow

### After Initial Production Deployment

**Daily Development:**

```bash
# 1. Feature work on feat/test-branch
git checkout -b feat/my-feature
npm run dev
# Make changes, test

# 2. Push to GitHub
git push origin feat/my-feature
# PR creates automatically
# Tests run

# 3. Code review & approval
# Merge to dev
git merge feat/my-feature
git push origin dev

# 4. Staging deployment automatic
# Test on staging

# 5. When ready: Merge dev → main
# Production deployment automatic
```

**Weekly Release:**

```bash
# Review all changes on dev
# Final testing on staging
# Create release notes
# Merge dev → main (triggers production)
# Monitor deployment
# Post-deployment verification
```

---

## 🚨 Rollback Strategy

### If Production Goes Wrong

```bash
# OPTION 1: Rollback via Git (Safest)
git checkout main
git revert HEAD    # Reverts last commit
git push origin main
# GitHub Actions automatically deploys previous version

# OPTION 2: Manual Rollback (Faster)
git checkout main
git log --oneline
# Find previous good commit
git revert [COMMIT_HASH]
git push origin main

# OPTION 3: Vercel Rollback (Frontend only)
# Vercel Dashboard → Deployments → Find previous working version → Click "Rollback"

# OPTION 4: Railway Rollback (Backend only)
# Railway Dashboard → Deployments → Find previous working version → Click "Rollback"
```

---

## 🐛 Troubleshooting Common Issues

### Deployment Fails: Tests Not Passing

```bash
# Local fix
npm run test
npm run lint:fix

# Commit and push
git add .
git commit -m "fix: resolve test failures"
git push origin feat/test-branch
```

### Strapi Content Not Loading in Production

```bash
# Check environment variables
# Railway Dashboard → Environment → Verify:
# - DATABASE_URL points to production DB
# - JWT_SECRET is set
# - API_TOKEN_SALT is set

# Redeploy Strapi
# Railway: Delete deployment → Redeploy
```

### Vercel Build Fails

```bash
# Check build logs
# Vercel Dashboard → Deployments → Failed build → View logs

# Common issues:
# - Missing environment variables: Add to Vercel dashboard
# - Port conflicts: Check PORT env var
# - Memory issues: Optimize bundle

# Common fixes:
npm run build
# Test locally first before pushing
```

### GCP Function Not Triggering

```bash
# Check function logs
gcloud functions logs read my-function --limit 50

# Verify trigger setup
gcloud functions describe my-function

# Redeploy function
gcloud functions deploy my-function --runtime python39
```

---

## 📚 Next Steps

1. **Configure GitHub Secrets** (see "GitHub Secrets Setup" above)
2. **Run Local Test** (see "Pre-Deployment Checklist")
3. **Deploy to Staging** (push to dev branch)
4. **Test Staging** (verify everything works)
5. **Deploy to Production** (push to main branch)
6. **Monitor & Verify** (check production is working)
7. **Setup Ollama** (for local LLM development)

---

## 📖 Related Documentation

- **[Deployment & Infrastructure](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** - Detailed deployment architecture
- **[CI/CD Complete](./reference/CI_CD_COMPLETE.md)** - GitHub Actions workflows
- **[Branch Setup](./guides/BRANCH_SETUP_COMPLETE.md)** - Environment configuration
- **[Development Workflow](./04-DEVELOPMENT_WORKFLOW.md)** - Git workflow

---

## ✅ Deployment Checklist Summary

**Before Pushing to Main:**

- [ ] All tests pass: `npm run test`
- [ ] Linting passes: `npm run lint:fix`
- [ ] Build succeeds: `npm run build`
- [ ] GitHub secrets configured
- [ ] No console errors locally
- [ ] Strapi content accessible
- [ ] README and docs updated

**After Pushing to Main:**

- [ ] GitHub Actions deploy-production.yml passes
- [ ] Vercel deployment completes
- [ ] Railway deployment completes
- [ ] Frontend loads at [https://gladlabs.io](https://gladlabs.io)
- [ ] API responds at Railway URL
- [ ] Strapi admin accessible
- [ ] No production errors
- [ ] Monitoring dashboards show green

---

**Ready to deploy? Follow the [Branch Workflow](#-branch-workflow-for-production) section above! 🚀**
