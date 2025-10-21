# Quick Start: Branch-Specific Variables Setup

**Goal:** Run local dev on `feat/*` branches, staging on `dev` branch, production on `main` branch with automatic environment configuration.

**Status:** ✅ All files created and committed

---

## 📋 What Was Created

### 1. **Environment Configuration Files**

- ✅ `.env.staging` - Staging environment variables (committed)
- ✅ `.env.production` - Production environment variables (committed)
- ✅ `.env` - Local development (in `.gitignore`, you create locally)
- ✅ `.env.example` - Template with all variables

### 2. **Automatic Environment Selection**

- ✅ `scripts/select-env.js` - Auto-selects .env based on git branch
- ✅ Updated `package.json` - `npm run dev` and `npm run build` call `npm run env:select`

### 3. **GitHub Actions Workflows**

- ✅ `.github/workflows/test-on-feat.yml` - Tests feature branches
- ✅ `.github/workflows/deploy-staging.yml` - Deploys dev → staging
- ✅ `.github/workflows/deploy-production.yml` - Deploys main → production

### 4. **Documentation**

- ✅ `docs/07-BRANCH_SPECIFIC_VARIABLES.md` - Comprehensive setup guide (1,500+ lines)
- ✅ Updated `.github/copilot-instructions.md` - Branch workflow guidance

---

## 🚀 Getting Started (5 Steps)

### Step 1: Create Local .env File

```bash
# Copy the example to .env for your local development
cp .env.example .env

# Edit .env with your local values:
# - NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
# - DATABASE_CLIENT=sqlite (for local dev)
# - ANTHROPIC_API_KEY=your-key-here (if testing agents)
# - OPENAI_API_KEY=your-key-here
```

### Step 2: Test Environment Selection

```bash
# Make sure you're on a feature branch
git checkout -b feat/test-env-setup

# Test the selection script
npm run env:select

# You should see:
# 📦 Environment Selection
#    Branch: feat/test-env-setup
#    Environment: LOCAL DEVELOPMENT
#    Source: .env
#    Loaded: .env.local
```

### Step 3: Start Development

```bash
# This now automatically loads .env based on your branch
npm run dev

# All services start with local environment variables:
# - Strapi: http://localhost:1337/admin
# - Public Site: http://localhost:3000
# - Oversight Hub: http://localhost:3001
# - Co-founder Agent: http://localhost:8000/docs
```

### Step 4: Push to Staging (dev branch)

```bash
# Create a PR to dev branch
git push origin feat/test-env-setup

# GitHub Actions automatically:
# 1. Runs test-on-feat.yml (tests + linting + build check)
# 2. Shows results in the PR

# After review, merge to dev:
git checkout dev
git merge --squash origin/feat/test-env-setup
git push origin dev

# GitHub Actions automatically:
# 1. Runs deploy-staging.yml
# 2. Loads .env.staging environment
# 3. Builds with staging API endpoints
# 4. Deploys to Railway staging (when configured)
```

### Step 5: Promote to Production (main branch)

```bash
# Create PR from dev to main
git checkout main
git pull origin main
git merge --no-ff dev
git push origin main

# GitHub Actions automatically:
# 1. Runs deploy-production.yml
# 2. Loads .env.production environment
# 3. Builds with production API endpoints
# 4. Deploys to Vercel (frontend) + Railway (backend)
```

---

## 🔧 Configure GitHub Secrets

For CI/CD to work, add these secrets to GitHub (Settings → Secrets and variables → Actions):

### Development/Staging Secrets

```
STAGING_STRAPI_URL=https://staging-cms.railway.app
STAGING_STRAPI_TOKEN=your-token
STAGING_DB_HOST=your-db-host
STAGING_DB_USER=your-user
STAGING_DB_PASSWORD=your-password
RAILWAY_STAGING_PROJECT_ID=your-project-id
```

### Production Secrets

```
PROD_STRAPI_URL=https://cms.railway.app
PROD_STRAPI_TOKEN=your-token
PROD_DB_HOST=your-db-host
PROD_DB_USER=your-user
PROD_DB_PASSWORD=your-password
RAILWAY_TOKEN=your-token
RAILWAY_PROD_PROJECT_ID=your-project-id
VERCEL_TOKEN=your-token
VERCEL_PROJECT_ID=your-project-id
VERCEL_ORG_ID=your-org-id
```

---

## 📊 Branch → Environment Mapping

| Branch   | Environment | .env File                     | Database        | Deploy Target     |
| -------- | ----------- | ----------------------------- | --------------- | ----------------- |
| `feat/*` | Local Dev   | `.env` (you create)           | SQLite (local)  | None (local only) |
| `dev`    | Staging     | `.env.staging` (committed)    | Postgres (test) | Railway staging   |
| `main`   | Production  | `.env.production` (committed) | Postgres (prod) | Vercel + Railway  |

---

## ✅ Verification Checklist

- [ ] Created `.env` file locally (copy from `.env.example`)
- [ ] Ran `npm run env:select` on a feature branch
- [ ] Ran `npm run dev` and verified services start
- [ ] Checked that `.env.staging` and `.env.production` files exist and are committed
- [ ] Reviewed `.github/workflows/` folder contains 3 workflow files
- [ ] Read `docs/07-BRANCH_SPECIFIC_VARIABLES.md` for detailed setup
- [ ] Added GitHub secrets (staging + production) for CI/CD

---

## 🐛 Troubleshooting

**Q: "Environment variables not loading in Next.js"**

A: Ensure variables start with `NEXT_PUBLIC_` prefix:

```bash
# ✅ Correct - exposed to browser
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337

# ❌ Wrong - not exposed to browser
STRAPI_API_URL=http://localhost:1337
```

**Q: "Different environments when pushing to dev vs main"**

A: Verify branch selection:

```bash
# Check current branch
git branch

# Verify env files exist
ls -la .env.staging .env.production

# Test selection
npm run env:select
```

**Q: "GitHub Actions workflows not triggering"**

A: Check workflow files exist:

```bash
ls -la .github/workflows/
# Should show:
# test-on-feat.yml
# deploy-staging.yml
# deploy-production.yml
```

**Q: "How do I override environment locally?"**

A: Create `.env.local` to override `.env`:

```bash
# .env.local takes precedence over .env
# Use for temporary local testing of staging config
cp .env.staging .env.local
npm run dev
```

---

## 📚 For More Details

- **Complete Setup Guide:** `docs/07-BRANCH_SPECIFIC_VARIABLES.md`
- **Copilot Instructions:** `.github/copilot-instructions.md`
- **Environment Selection Script:** `scripts/select-env.js`
- **Workflow Configuration:** `.github/workflows/`

---

## 🎯 Next Steps

1. ✅ Create your local `.env` file
2. ✅ Test `npm run dev` on a feature branch
3. ✅ Configure GitHub secrets for CI/CD
4. ✅ Push to dev branch and monitor GitHub Actions
5. ✅ Merge to main and verify Vercel + Railway deployment

You now have a complete branch-specific environment setup! 🚀
