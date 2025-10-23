# 🔄 GLAD Labs Workflow & Environment Setup Guide

**Last Updated:** October 23, 2025  
**Status:** ✅ Ready to implement

---

## 🎯 Your Desired Workflow

```
feat/*** branches (Local Development)
    ↓
    npm run dev (uses .env.local)
    ↓ git commit & push

dev branch (Staging Environment)
    ↓
    GitHub Actions → Deploy to Railway staging
    ↓ git commit & push (merge to main)

main branch (Production Environment)
    ↓
    GitHub Actions → Deploy to Railway production
    ↓
    Live traffic on production URLs
```

---

## 📋 Current Setup Status

### ✅ What You Have

- **`.env.local`** - Local development (SQLite, localhost URLs)
- **`.env.staging`** - Staging configuration (PostgreSQL, Railway staging URLs)
- **`.env.tier1.production`** - Production configuration (PostgreSQL, Railway prod URLs)
- **`package.json` with npm scripts** - `npm run dev`, `npm run build`, etc.
- **`.gitignore`** - Properly ignores `.env.local`

### ⚠️ What's Missing

- **Automatic environment selection** based on git branch
- **Proper error handling** for workspace startup
- **Documentation** of the workflow

---

## 🚀 How to Make It Work

### Step 1: Understand Environment Variables by Branch

| Branch     | Environment File  | Uses           | Database   | URLs                    |
| ---------- | ----------------- | -------------- | ---------- | ----------------------- |
| `feat/***` | `.env.local`      | Local dev      | SQLite     | `localhost:*`           |
| `dev`      | `.env.staging`    | GitHub Secrets | PostgreSQL | `staging-*.railway.app` |
| `main`     | `.env.production` | GitHub Secrets | PostgreSQL | `*.railway.app`         |

### Step 2: Local Development Setup (What You're Doing Now)

You're on a `feat/***` branch, so you should use `.env.local`:

```bash
# Make sure you're on a feature branch
git branch
# Output: * feat/your-feature (not main or dev)

# Your .env.local should have:
NODE_ENV=development
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
NEXT_PUBLIC_SITE_URL=http://localhost:3000
REACT_APP_STRAPI_URL=http://localhost:1337
REACT_APP_API_URL=http://localhost:8000
```

### Step 3: Fix npm run dev

The `npm run dev` command is trying to start multiple workspaces in parallel. Here's what's likely failing:

#### **Problem:** Python server startup

Your package.json has:

```json
"dev:cofounder": "python src/cofounder_agent/start_server.py"
```

**But** this might fail if:

- Python isn't configured correctly
- Dependencies aren't installed
- Virtual environment not activated

#### **Solution: Start workspaces individually first**

```powershell
# Terminal 1: Strapi CMS
cd cms\strapi-main
npm run develop

# Terminal 2: Next.js Public Site (new terminal)
cd web\public-site
npm run dev

# Terminal 3: React Oversight Hub (new terminal)
cd web\oversight-hub
npm start

# Terminal 4: AI Co-Founder (new terminal, optional)
cd src\cofounder_agent
python -m uvicorn main:app --reload
```

#### **Or, run them manually without Python:**

```powershell
# If you don't need the Python backend, just run the frontend/CMS:
npx npm-run-all --parallel "npm run develop --workspace=cms/strapi-main" "npm run dev --workspace=web/public-site" "npm start --workspace=web/oversight-hub"
```

#### **Better Solution: Create a Custom Dev Script**

Replace the failing `npm run dev` script in `package.json`:

```json
{
  "scripts": {
    "dev": "npx npm-run-all --parallel dev:web dev:strapi",
    "dev:web": "npx npm-run-all --parallel dev:public dev:oversight",
    "dev:strapi": "npm run develop --workspace=cms/strapi-main",
    "dev:public": "npm run dev --workspace=web/public-site",
    "dev:oversight": "npm start --workspace=web/oversight-hub"
  }
}
```

This removes the Python backend from the parallel startup (since it often fails).

---

## 🔧 How Each Environment Works

### Local Development (feat/\* branches)

```bash
# 1. You're on a feature branch
git checkout -b feat/my-feature

# 2. .env.local is loaded automatically
# 3. All services run on localhost

npm run dev
# Starts:
# - Strapi: http://localhost:1337
# - Public Site: http://localhost:3000
# - Oversight Hub: http://localhost:3001
```

### Staging (dev branch)

```bash
# 1. Push to dev branch
git checkout dev
git merge feat/my-feature
git push origin dev

# 2. GitHub Actions reads .env.staging
# 3. GitHub Secrets provide actual values (STAGING_DB_HOST, etc.)
# 4. Deploys to Railway staging environment
# 5. Available at:
# - https://staging-cms.railway.app
# - https://staging-api.glad-labs.com
```

### Production (main branch)

```bash
# 1. Push to main branch
git checkout main
git merge dev
git push origin main

# 2. GitHub Actions reads .env.tier1.production
# 3. GitHub Secrets provide actual values (PROD_DB_HOST, etc.)
# 4. Deploys to Railway production environment
# 5. Available at:
# - https://cms.railway.app (or your custom domain)
# - https://api.glad-labs.com (or your custom domain)
```

---

## 🛠️ Setting Up npm Scripts Properly

### Update Your package.json

Replace your `dev` script section with this:

```json
{
  "scripts": {
    "//": "=== DEVELOPMENT ===",
    "dev": "npm-run-all --parallel dev:web dev:strapi",
    "dev:web": "npm-run-all --parallel dev:public dev:oversight",
    "dev:strapi": "npm run develop --workspace=cms/strapi-main",
    "dev:public": "npm run dev --workspace=web/public-site",
    "dev:oversight": "npm start --workspace=web/oversight-hub",
    "dev:cofounder": "python src/cofounder_agent/start_server.py",

    "//": "=== OPTIONAL: Start just Strapi + Frontends ===",
    "dev:full": "npm-run-all --parallel dev:web dev:strapi dev:cofounder",

    "//": "=== INDIVIDUAL WORKSPACE STARTUP ===",
    "start:all": "npm-run-all --parallel start:strapi start:public start:oversight",
    "start:strapi": "npm run start --workspace=cms/strapi-main",
    "start:public": "npm run start --workspace=web/public-site",
    "start:oversight": "npm run build --workspace=web/oversight-hub && npm run start --workspace=web/oversight-hub",
    "start:cofounder": "python src/cofounder_agent/main.py"
  }
}
```

---

## 📝 Environment Files Checklist

### ✅ .env.local (for local dev on feat/\* branches)

```bash
# Should have:
NODE_ENV=development
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
NEXT_PUBLIC_SITE_URL=http://localhost:3000
DATABASE_CLIENT=sqlite
DATABASE_FILENAME=.tmp/data.db
```

### ✅ .env.staging (for dev branch → Railway staging)

```bash
# Should have:
NODE_ENV=staging
NEXT_PUBLIC_STRAPI_API_URL=https://staging-cms.railway.app
DATABASE_CLIENT=postgres
DATABASE_HOST=${STAGING_DB_HOST}  # Replaced by GitHub Secrets
```

### ✅ .env.tier1.production (for main branch → Railway prod)

```bash
# Should have:
NODE_ENV=production
NEXT_PUBLIC_STRAPI_API_URL=https://cms.railway.app
DATABASE_CLIENT=postgres
DATABASE_HOST=${PROD_DB_HOST}  # Replaced by GitHub Secrets
```

---

## 🚨 Troubleshooting npm run dev

### Issue 1: "npm-run-all: command not found"

**Solution:**

```bash
npm install -g npm-run-all
# Or use the local version:
npx npm-run-all --parallel dev:strapi dev:public dev:oversight
```

### Issue 2: Port already in use

**Error:** `EADDRINUSE: address already in use :::1337`

**Solution:**

```powershell
# Find what's using the port
netstat -ano | findstr :1337
# Kill the process
taskkill /PID <process_id> /F
```

### Issue 3: Python backend fails to start

**Error:** `ModuleNotFoundError: No module named 'uvicorn'`

**Solution:**

```bash
# Skip Python in dev for now, just run frontends:
npm run dev
# This now starts only:
# - Strapi (port 1337)
# - Public Site (port 3000)
# - Oversight Hub (port 3001)
#
# You can start Python separately if needed:
cd src/cofounder_agent
python -m uvicorn main:app --reload
```

### Issue 4: Next.js won't start

**Error:** `Error: ENOENT: no such file or directory`

**Solution:**

```bash
cd web/public-site
npm install
npm run dev
```

### Issue 5: React Oversight Hub won't start

**Error:** `Cannot find module '@chatscope/chat-ui-kit-react'`

**Solution:**

```bash
cd web/oversight-hub
npm install
npm start
```

---

## ✅ Step-by-Step: Get Development Working

### 1. Verify You're on a Feature Branch

```bash
git branch
# Should show: * feat/your-feature-name
# NOT: * main or * dev
```

If not:

```bash
git checkout -b feat/setup-dev-workflow
```

### 2. Verify .env.local Exists and Has Correct Values

```bash
# Check if file exists
test -f .env.local && echo "EXISTS" || echo "MISSING"

# If missing, copy from example:
cp .env.example .env.local

# Verify critical values (should be localhost):
grep "NEXT_PUBLIC_STRAPI_API_URL" .env.local
# Should output: NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337

grep "NODE_ENV" .env.local
# Should output: NODE_ENV=development (or Development)
```

### 3. Install Dependencies

```bash
npm run install:all
```

### 4. Update npm Scripts (Optional but Recommended)

Edit `package.json` and update the `dev` scripts as shown above.

### 5. Start Development

**Option A: Terminal per workspace (Most Reliable)**

```powershell
# Terminal 1
cd cms\strapi-main
npm run develop

# Terminal 2 (wait for Strapi to start, then run)
cd web\public-site
npm run dev

# Terminal 3 (new terminal)
cd web\oversight-hub
npm start
```

**Option B: npm run dev (All Together)**

```bash
npm run dev
```

If this fails, use Option A.

### 6. Verify Everything Started

- **Strapi Admin:** http://localhost:1337/admin ✅
- **Public Site:** http://localhost:3000 ✅
- **Oversight Hub:** http://localhost:3001 ✅

---

## 📊 Complete Workflow Example

### Scenario: Develop a feature, push to staging, then production

```bash
# 1. START: Create feature branch from dev
git checkout dev
git pull origin dev
git checkout -b feat/add-new-feature

# 2. DEVELOP: Make changes locally
npm run dev  # Start local services with .env.local
# Make changes to code
# Test locally

# 3. COMMIT: Commit and push feature
git add .
git commit -m "feat: add new feature"
git push origin feat/add-new-feature

# 4. PULL REQUEST: Create PR to dev
# In GitHub: feat/add-new-feature → dev
# Tests run automatically
# Code review
# Merge to dev

# 5. STAGING: dev branch triggers automatic deployment
# GitHub Actions:
# - Reads .env.staging
# - Uses secrets from GitHub (STAGING_DB_HOST, etc.)
# - Deploys to Railway staging
# - Available at https://staging-cms.railway.app

# 6. TEST STAGING: Verify changes work
# Visit: https://staging-cms.railway.app
# Test functionality

# 7. PRODUCTION: Merge dev to main
git checkout main
git pull origin main
git merge dev
git push origin main

# 8. PRODUCTION DEPLOYMENT: main branch triggers automatic deployment
# GitHub Actions:
# - Reads .env.tier1.production
# - Uses secrets from GitHub (PROD_DB_HOST, etc.)
# - Deploys to Railway production
# - Available at https://cms.railway.app

# 9. VERIFY: Check production
# Visit: https://cms.railway.app
# Confirm changes are live
```

---

## 🔐 GitHub Secrets Setup

For the workflow to work, you need to add these GitHub Secrets:

### For Staging (dev branch)

```
STAGING_STRAPI_TOKEN
STAGING_DB_HOST
STAGING_DB_USER
STAGING_DB_PASSWORD
STAGING_ADMIN_PASSWORD
```

### For Production (main branch)

```
PROD_STRAPI_TOKEN
PROD_DB_HOST
PROD_DB_USER
PROD_DB_PASSWORD
PROD_ADMIN_PASSWORD
```

**How to add:**

1. GitHub repo → Settings → Secrets and variables → Actions
2. Click "New repository secret"
3. Add each one

---

## 📋 Checklist: Workflow Setup Complete

- [ ] You're on a `feat/***` branch
- [ ] `.env.local` exists in repository root
- [ ] `.env.local` has `localhost` URLs for Strapi and APIs
- [ ] `.env.local` has `NODE_ENV=development`
- [ ] `.env.staging` exists with staging URLs
- [ ] `.env.tier1.production` exists with production URLs
- [ ] `npm run install:all` completes successfully
- [ ] Strapi starts: `npm run develop` (in cms/strapi-main)
- [ ] Public Site starts: `npm run dev` (in web/public-site)
- [ ] Oversight Hub starts: `npm start` (in web/oversight-hub)
- [ ] All three can access Strapi at http://localhost:1337
- [ ] GitHub Secrets are configured for staging and production
- [ ] `.gitignore` includes `.env.local` (don't commit!)

---

## 🎯 Quick Reference

### Development (Local)

```bash
git checkout -b feat/my-feature
npm run dev
# Or run individually:
# Terminal 1: cd cms/strapi-main && npm run develop
# Terminal 2: cd web/public-site && npm run dev
# Terminal 3: cd web/oversight-hub && npm start
```

### Staging (Automatic on dev branch)

```bash
git checkout dev
git merge feat/my-feature
git push origin dev
# → GitHub Actions deploys to staging automatically
```

### Production (Automatic on main branch)

```bash
git checkout main
git merge dev
git push origin main
# → GitHub Actions deploys to production automatically
```

---

**Questions?** Check `.env.local`, make sure you're on `feat/***` branch, and verify Strapi is running!
