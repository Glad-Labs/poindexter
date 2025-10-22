# Branch-Specific Environment Setup Guide

**Complete Guide:** Consolidates `BRANCH_SETUP_QUICK_START.md`, `BRANCH_VARIABLES_IMPLEMENTATION_SUMMARY.md`, and `GETTING_STARTED_WITH_BRANCH_ENVIRONMENTS.md`

**Date:** October 20, 2025  
**Status:** ✅ Production Ready

---

## 🎯 Quick Overview

Your GLAD Labs monorepo now has **automatic branch-specific environment configuration**:

- **`feat/*` branches** → Local development (SQLite, localhost)
- **`dev` branch** → Staging environment (PostgreSQL test DB)
- **`main` branch** → Production (PostgreSQL production DB, Vercel, Railway)

Just run `npm run dev` - the system automatically selects the right environment!

---

## 📊 The Three-Tier Pipeline

```
FEATURE DEVELOPMENT                STAGING                      PRODUCTION
┌──────────────────────┐       ┌─────────────────┐        ┌──────────────────┐
│ git checkout -b      │       │ git checkout    │        │ git checkout main│
│ feat/my-feature      │       │ dev             │        │ git merge dev    │
│                      │       │                 │        │                  │
│ npm run dev          │       │ git push origin │        │ git push origin  │
│ ↓                   │       │ dev             │        │ main             │
│ Loads: .env          │       │ ↓              │        │ ↓               │
│ • localhost:1337     │       │ GitHub Actions: │        │ GitHub Actions:  │
│ • SQLite             │       │ deploy-staging  │        │ deploy-production│
│ • Debug enabled      │       │ ↓              │        │ ↓               │
│                      │       │ Loads:          │        │ Loads:           │
│ GitHub Actions:      │       │ .env.staging    │        │ .env.production  │
│ test-on-feat.yml     │       │ • PostgreSQL    │        │ • PostgreSQL     │
│ • Tests             │       │ • Staging APIs  │        │ • Production     │
│ • Linting           │       │ • Railway test  │        │ • Vercel         │
│ • Build check       │       │                 │        │ • Railway prod   │
└──────────────────────┘       └─────────────────┘        └──────────────────┘
```

---

## 🚀 Getting Started (5 Steps)

### Step 1: Create Your Local `.env` File

```bash
# Copy template to your local .env
cp .env.example .env

# Edit .env with local values:
# - NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
# - DATABASE_CLIENT=sqlite
# - ANTHROPIC_API_KEY=your-key (for agents)
# - OPENAI_API_KEY=your-key
```

### Step 2: Test Environment Selection

```bash
# Create a feature branch
git checkout -b feat/test-setup

# Test the environment selector
npm run env:select

# Expected output:
# 📦 Environment Selection
#    Branch: feat/test-setup
#    Environment: LOCAL DEVELOPMENT
#    Source: .env
#    NODE_ENV: development
```

### Step 3: Start Development

```bash
# This automatically loads .env based on your branch
npm run dev

# Services start with local configuration:
# ✅ Strapi CMS: http://localhost:1337/admin
# ✅ Public Site: http://localhost:3000
# ✅ Oversight Hub: http://localhost:3001
# ✅ Co-founder Agent: http://localhost:8000/docs
```

### Step 4: Push to Staging

```bash
# Push your feature branch
git push origin feat/test-setup

# GitHub Actions automatically:
# 1. Runs test-on-feat.yml (tests + linting + build)
# 2. Shows results in PR

# After review, merge to dev:
git checkout dev
git merge --squash origin/feat/test-setup
git push origin dev

# GitHub Actions automatically:
# 1. Runs deploy-staging.yml
# 2. Loads .env.staging
# 3. Deploys to Railway staging
```

### Step 5: Deploy to Production

```bash
# Create PR from dev to main
git checkout main
git pull origin main
git merge --no-ff dev
git push origin main

# GitHub Actions automatically:
# 1. Runs deploy-production.yml
# 2. Loads .env.production
# 3. Deploys to Vercel (frontend) + Railway (backend)
# 4. Live traffic → https://glad-labs.vercel.app
```

---

## 🗂️ Implementation Details

### Environment Files

**`.env` (Local Development - NEVER commit)**

```bash
NODE_ENV=development
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
DATABASE_CLIENT=sqlite
DATABASE_FILENAME=.tmp/data.db
STRAPI_PORT=1337
PUBLIC_SITE_PORT=3000
DEBUG_LOGS=true
ENABLE_ANALYTICS=false
```

**`.env.staging` (Committed to repo)**

```bash
NODE_ENV=staging
NEXT_PUBLIC_STRAPI_API_URL=https://staging-cms.railway.app
DATABASE_CLIENT=postgres
DATABASE_NAME=glad_labs_staging
# Actual secrets in GitHub Secrets: ${STAGING_DB_PASSWORD}
```

**`.env.production` (Committed to repo)**

```bash
NODE_ENV=production
NEXT_PUBLIC_STRAPI_API_URL=https://cms.railway.app
DATABASE_CLIENT=postgres
DATABASE_NAME=glad_labs_production
# Actual secrets in GitHub Secrets: ${PROD_DB_PASSWORD}
```

### Automatic Environment Selection

**`scripts/select-env.js`** - How it works:

1. Detects current branch: `git rev-parse --abbrev-ref HEAD`
2. Maps branch to environment:
   - `main` → `production` (`env.production`)
   - `dev` → `staging` (`env.staging`)
   - `feat/*` → `development` (`.env`)
3. Copies selected file to `.env.local`
4. Sets `NODE_ENV` environment variable
5. Next.js automatically loads `.env.local`

### GitHub Actions Workflows

**`test-on-feat.yml`** - Runs on feature branch push

```yaml
# Tests + linting + build check
# Helps catch issues before merging to dev
```

**`deploy-staging.yml`** - Runs on dev branch push

```yaml
# Loads .env.staging
# Runs full test suite with staging DB
# Deploys to Railway staging environment
```

**`deploy-production.yml`** - Runs on main branch push

```yaml
# Loads .env.production
# Runs full test suite with production DB
# Deploys frontend to Vercel
# Deploys backend to Railway production
```

---

## 📊 Environment Comparison

| Feature          | Local Dev        | Staging                   | Production             |
| ---------------- | ---------------- | ------------------------- | ---------------------- |
| **Branch**       | `feat/*`         | `dev`                     | `main`                 |
| **Env File**     | `.env`           | `.env.staging`            | `.env.production`      |
| **Database**     | SQLite (local)   | PostgreSQL (test)         | PostgreSQL (prod)      |
| **Strapi URL**   | `localhost:1337` | `staging-cms.railway.app` | `cms.railway.app`      |
| **Frontend URL** | `localhost:3000` | Staging                   | `glad-labs.vercel.app` |
| **Debug Logs**   | Enabled          | Disabled                  | Disabled               |
| **Analytics**    | Disabled         | Enabled                   | Enabled                |
| **Payments**     | Off              | Test mode                 | Live                   |
| **Workflow**     | Manual testing   | Automated testing         | Automated deploy       |

---

## 🔐 Security Features

✅ **Environment configs committed** (`.env.staging`, `.env.production`)  
✅ **Actual secrets in GitHub Secrets** (not in files)  
✅ **Placeholder variables** in configs: `${STAGING_DB_PASSWORD}`, etc.  
✅ **Local `.env` in `.gitignore`** (never committed)  
✅ **Three databases** completely isolated

---

## 🔧 GitHub Secrets Setup

For CI/CD automation, add these secrets to GitHub (Settings → Secrets):

**Staging:**

```
STAGING_STRAPI_URL
STAGING_STRAPI_TOKEN
STAGING_DB_HOST
STAGING_DB_USER
STAGING_DB_PASSWORD
RAILWAY_STAGING_PROJECT_ID
```

**Production:**

```
PROD_STRAPI_URL
PROD_STRAPI_TOKEN
PROD_DB_HOST
PROD_DB_USER
PROD_DB_PASSWORD
RAILWAY_TOKEN
RAILWAY_PROD_PROJECT_ID
VERCEL_TOKEN
VERCEL_PROJECT_ID
VERCEL_ORG_ID
```

---

## 💻 Common Commands

```bash
# Local development
git checkout -b feat/my-task
npm run dev                          # Auto-loads .env
npm run test
npm run lint:fix

# Environment selection
npm run env:select                   # Manually trigger
npm run env:select && npm run dev    # Force selection + start

# Push workflow
git push origin feat/my-task         # Triggers test-on-feat.yml
git checkout dev
git merge --squash feat/my-task
git push origin dev                  # Triggers deploy-staging.yml

# Production
git checkout main
git merge dev
git push origin main                 # Triggers deploy-production.yml
```

---

## ✅ Verification Checklist

- [ ] Created `.env` file (copy from `.env.example`)
- [ ] Ran `npm run env:select` on feature branch
- [ ] Ran `npm run dev` and verified services start
- [ ] `.env.staging` and `.env.production` files exist
- [ ] `.github/workflows/` contains 3 workflow files
- [ ] GitHub Secrets configured (for CI/CD)
- [ ] Read `docs/07-BRANCH_SPECIFIC_VARIABLES.md` for details

---

## 🐛 Troubleshooting

**Q: Environment variables not loading**

```bash
# Solution: Ensure variables start with NEXT_PUBLIC_
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337  ✅
STRAPI_API_URL=http://localhost:1337              ❌
```

**Q: Wrong environment selected**

```bash
# Verify current branch
git branch

# Test selection
npm run env:select

# Expected output matches your branch
```

**Q: GitHub Actions not triggering**

```bash
# Check workflow files exist
ls -la .github/workflows/

# Verify all 3 files present:
# test-on-feat.yml
# deploy-staging.yml
# deploy-production.yml
```

**Q: Override environment locally**

```bash
# Create .env.local (takes precedence over .env)
cp .env.staging .env.local
npm run dev
# Now using staging config locally
```

---

## 📚 Related Documentation

- **`docs/07-BRANCH_SPECIFIC_VARIABLES.md`** - 1,500+ line comprehensive guide
- **`.github/copilot-instructions.md`** - AI agent guidance with branch workflows
- **`.github/workflows/`** - GitHub Actions automation

---

## 🎯 What's Next

1. ✅ Create local `.env` file
2. ✅ Test `npm run dev` on feature branch
3. ✅ Configure GitHub Secrets for CI/CD
4. ✅ Push to dev and monitor GitHub Actions
5. ✅ Merge to main and verify production deployment

---

**Status:** ✅ Production Ready  
**Last Updated:** October 20, 2025  
**You're all set!** 🚀
