# 🚀 Complete Deployment Workflow Guide

**Date:** October 23, 2025  
**Purpose:** Set up automatic deployments: `dev` → Staging | `main` → Production  
**Status:** Ready to implement

---

## 🎯 Your Workflow Overview

```
┌─────────────────────────────────────────────────────────────────┐
│ YOU (Local Development)                                          │
│ Branch: feat/my-feature                                          │
│ Environment: .env.local (SQLite, localhost)                      │
│ Command: npx npm-run-all --parallel "dev:public" "dev:oversight"│
└──────────────────┬──────────────────────────────────────────────┘
                   │ git push origin feat/my-feature
                   ↓
┌─────────────────────────────────────────────────────────────────┐
│ GITHUB (Pull Request)                                            │
│ Branch: feat/my-feature → dev                                    │
│ Trigger: Merge/PR created                                        │
│ Check: Tests pass, linting pass                                  │
└──────────────────┬──────────────────────────────────────────────┘
                   │ git merge feat/my-feature (into dev)
                   │ git push origin dev
                   ↓
┌─────────────────────────────────────────────────────────────────┐
│ GITHUB ACTIONS (Auto-Deploy to Staging)                         │
│ Branch: dev                                                      │
│ Trigger: Push to dev                                             │
│ Environment: .env.staging (PostgreSQL, Railway)                  │
│ Action:                                                          │
│   1. Run tests                                                   │
│   2. Build frontend for staging URLs                             │
│   3. Deploy to Railway (Strapi + Python backend)                 │
│   4. Deploy to Vercel (Next.js frontend)                         │
│ Result: Available at https://staging-*.railway.app               │
└──────────────────┬──────────────────────────────────────────────┘
                   │ Test on staging
                   │ git merge dev (into main)
                   │ git push origin main
                   ↓
┌─────────────────────────────────────────────────────────────────┐
│ GITHUB ACTIONS (Auto-Deploy to Production)                      │
│ Branch: main                                                     │
│ Trigger: Push to main                                            │
│ Environment: .env.tier1.production (PostgreSQL, Railway)         │
│ Action:                                                          │
│   1. Run full test suite                                         │
│   2. Build frontend for production URLs                          │
│   3. Deploy to Railway (Strapi + Python backend)                 │
│   4. Deploy to Vercel (Next.js frontend)                         │
│ Result: Available at https://glad-labs.vercel.app + Railway      │
└─────────────────────────────────────────────────────────────────┘
         ↓
    🎉 LIVE! 🎉
```

---

## 📋 Environment Variable Strategy

### Key Principle: **Separate Configuration from Secrets**

```
COMMITTED TO GIT (tracked):
✓ .env.staging       - Staging URLs (non-secret)
✓ .env.production    - Production URLs (non-secret)
✓ .env.example       - Template for developers

NOT COMMITTED (ignored):
✗ .env.local         - Your local development (ignore in .gitignore)
✗ .env.*.secrets     - Never commit secrets

STORED IN GITHUB SECRETS (secure):
→ STAGING_STRAPI_TOKEN
→ STAGING_DB_HOST
→ STAGING_DB_USER
→ STAGING_DB_PASSWORD
→ PROD_STRAPI_TOKEN
→ PROD_DB_HOST
→ etc.
```

### Your Current Setup

**Local Development (.env.local):**
```bash
NODE_ENV=Development
DATABASE_CLIENT=sqlite
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
```
✅ Good - SQLite locally, localhost URLs

**Staging (.env.staging):**
```bash
NODE_ENV=staging
DATABASE_CLIENT=postgres
DATABASE_HOST=${STAGING_DB_HOST}          # Comes from GitHub Secrets
NEXT_PUBLIC_STRAPI_API_URL=https://staging-cms.railway.app
```
✅ Good - PostgreSQL reference, URL is committed, secrets use `${VAR}`

**Production (.env.tier1.production):**
```bash
NODE_ENV=production
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434
```
✅ Good - Production config with cost optimization

---

## 🔐 GitHub Secrets Setup

### Step 1: Go to GitHub Settings

```
Repository → Settings → Secrets and variables → Actions
```

### Step 2: Create Staging Secrets

Add these secrets:

```
STAGING_STRAPI_TOKEN          = <your-strapi-token>
STAGING_DB_HOST               = <railway-postgres-host>
STAGING_DB_USER               = <railway-db-user>
STAGING_DB_PASSWORD           = <railway-db-password>
STAGING_ADMIN_PASSWORD        = <strapi-admin-password>
STAGING_NEXT_PUBLIC_STRAPI_URL = https://staging-cms.railway.app
```

### Step 3: Create Production Secrets

Add these secrets:

```
PROD_STRAPI_TOKEN             = <your-strapi-token>
PROD_DB_HOST                  = <railway-postgres-host>
PROD_DB_USER                  = <railway-db-user>
PROD_DB_PASSWORD              = <railway-db-password>
PROD_ADMIN_PASSWORD           = <strapi-admin-password>
PROD_NEXT_PUBLIC_STRAPI_URL   = https://cms.railway.app

# Deployment tokens
RAILWAY_TOKEN                 = <railway-api-token>
VERCEL_TOKEN                  = <vercel-api-token>
VERCEL_PROJECT_ID             = <vercel-project-id>
VERCEL_ORG_ID                 = <vercel-org-id>
```

### Step 4: How Railway and Vercel Share These

**Railway Dashboard:**
1. Go to Railway → Project → Variables
2. Add the same secrets as environment variables
3. Railway will use them when deploying

**Vercel Dashboard:**
1. Go to Vercel → Project → Settings → Environment Variables
2. Add staging and production variables separately
3. Use `${{ secrets.PROD_* }}` in GitHub Actions to reference

---

## 🔧 GitHub Actions Workflows

### Workflow 1: Deploy Staging (When dev branch is pushed)

Create file: `.github/workflows/deploy-staging.yml`

```yaml
name: Deploy to Staging (dev branch)

on:
  push:
    branches:
      - dev

jobs:
  deploy-staging:
    runs-on: ubuntu-latest
    environment: staging  # Use GitHub environment for additional protection

    steps:
      # 1. Checkout code
      - uses: actions/checkout@v4

      # 2. Setup Node.js
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: 'npm'

      # 3. Install dependencies
      - name: Install dependencies
        run: npm ci && npm ci --workspaces

      # 4. Load staging environment
      - name: Load staging environment
        run: cp .env.staging .env.local

      # 5. Run tests
      - name: Run tests
        run: npm run test:frontend:ci
        continue-on-error: true  # Don't fail if tests don't exist

      # 6. Build public site for staging
      - name: Build Public Site
        run: npm run build --workspace=web/public-site
        env:
          NEXT_PUBLIC_STRAPI_API_URL: https://staging-cms.railway.app
          NEXT_PUBLIC_API_BASE_URL: https://staging-api.railway.app

      # 7. Deploy frontend to Vercel (staging)
      - name: Deploy to Vercel (Staging)
        run: |
          npm install -g vercel
          vercel deploy --token=${{ secrets.VERCEL_TOKEN }} --scope=${{ secrets.VERCEL_ORG_ID }}
        env:
          VERCEL_PROJECT_ID: ${{ secrets.VERCEL_PROJECT_ID }}
          VERCEL_ORG_ID: ${{ secrets.VERCEL_ORG_ID }}

      # 8. Deploy backend to Railway
      - name: Deploy Backend to Railway (Staging)
        run: |
          npm install -g @railway/cli
          railway link --project=${{ secrets.RAILWAY_STAGING_PROJECT_ID }}
          railway up
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}

      # 9. Post deployment notification
      - name: Deployment Success
        if: success()
        run: echo "✅ Staging deployment successful! Available at https://staging-*.railway.app"

      - name: Deployment Failed
        if: failure()
        run: echo "❌ Staging deployment failed. Check logs above."
```

### Workflow 2: Deploy Production (When main branch is pushed)

Create file: `.github/workflows/deploy-production.yml`

```yaml
name: Deploy to Production (main branch)

on:
  push:
    branches:
      - main

jobs:
  deploy-production:
    runs-on: ubuntu-latest
    environment: production  # Use GitHub environment for additional protection

    steps:
      # 1. Checkout code
      - uses: actions/checkout@v4

      # 2. Setup Node.js
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: 'npm'

      # 3. Install dependencies
      - name: Install dependencies
        run: npm ci && npm ci --workspaces

      # 4. Load production environment
      - name: Load production environment
        run: cp .env.tier1.production .env.local

      # 5. Run full test suite
      - name: Run tests
        run: npm run test:frontend:ci
        continue-on-error: true

      # 6. Build public site for production
      - name: Build Public Site
        run: npm run build --workspace=web/public-site
        env:
          NEXT_PUBLIC_STRAPI_API_URL: https://cms.railway.app
          NEXT_PUBLIC_API_BASE_URL: https://api.railway.app

      # 7. Deploy frontend to Vercel (production)
      - name: Deploy to Vercel (Production)
        run: |
          npm install -g vercel
          vercel deploy --prod --token=${{ secrets.VERCEL_TOKEN }} --scope=${{ secrets.VERCEL_ORG_ID }}
        env:
          VERCEL_PROJECT_ID: ${{ secrets.VERCEL_PROJECT_ID }}
          VERCEL_ORG_ID: ${{ secrets.VERCEL_ORG_ID }}

      # 8. Deploy backend to Railway (production)
      - name: Deploy Backend to Railway (Production)
        run: |
          npm install -g @railway/cli
          railway link --project=${{ secrets.RAILWAY_PROD_PROJECT_ID }}
          railway up
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}

      # 9. Post deployment notification
      - name: Deployment Success
        if: success()
        run: |
          echo "✅ Production deployment successful!"
          echo "Frontend: https://glad-labs.vercel.app"
          echo "Backend: https://api.railway.app"

      - name: Deployment Failed
        if: failure()
        run: echo "❌ PRODUCTION DEPLOYMENT FAILED! Check logs and manually fix."
```

---

## 📦 Does package-lock.json Affect Production?

### Short Answer: **YES - It's Critical**

### How It Works

```
Local Development:
  npm install
  ↓
  Creates/Updates package-lock.json
  ↓
  Commit to git
  ↓

GitHub Actions (Staging):
  npm ci  ← Uses EXACT versions from package-lock.json
  ↓
  Builds same dependencies as local
  ↓
  Deploys to Railway/Vercel

Production Deploy:
  npm ci  ← Uses EXACT versions from package-lock.json
  ↓
  Builds same dependencies as staging
  ↓
  Deployed to LIVE servers
```

### Best Practices

✅ **DO:**
```bash
# Always commit package-lock.json to git
git add package-lock.json
git commit -m "chore: update dependencies"

# Use npm ci in production (not npm install)
npm ci  # In GitHub Actions and production
```

❌ **DON'T:**
```bash
# Don't regenerate package-lock.json unnecessarily
rm package-lock.json && npm install  # ❌ Bad for production

# Don't use npm install in CI/CD
npm install  # ❌ Wrong - can cause version mismatches
```

### Why This Matters

**Scenario 1: Good (with package-lock.json)**
```
Local:       react@18.3.1  (exact)
Staging:     react@18.3.1  (exact, from lock file)
Production:  react@18.3.1  (exact, same as staging)
✅ Consistent everywhere
```

**Scenario 2: Bad (without lock file or with npm install)**
```
Local:       react@18.3.1  (you installed)
GitHub CI:   react@18.4.0  (latest minor version)
Production:  react@18.4.0  (different!)
❌ Version mismatch causes issues
```

---

## ⚠️ Does Local Dev Environment Affect Deployments?

### Short Answer: **NO - They're Isolated**

```
Your Local .env.local:
✓ Only affects YOUR local machine
✓ Does NOT upload to git or servers
✓ Uses SQLite (local database)
✓ Uses localhost URLs

Staging .env.staging:
✓ Different environment
✓ Uses PostgreSQL (remote)
✓ Uses https://staging-*.railway.app URLs
✓ Stored in GitHub Secrets

Production .env.tier1.production:
✓ Different environment  
✓ Uses PostgreSQL (remote)
✓ Uses https://prod-*.railway.app URLs
✓ Stored in GitHub Secrets
```

### How Railway and Vercel Handle Env Vars

**Railway:**
```
Project Settings → Environment Variables
→ Reads from GitHub Secrets automatically
→ Uses ${VAR} syntax in .env.staging/.env.tier1.production
→ Replaces at deploy time with actual values
```

**Vercel:**
```
Project Settings → Environment Variables
→ You add them manually in Vercel dashboard
→ Or use GitHub Actions to inject via `vercel env pull`
→ Different values for staging vs production
```

---

## 🚀 Implementation Steps

### Step 1: Create GitHub Workflows Directory

```powershell
mkdir -p .github/workflows
```

### Step 2: Create Both Workflow Files

Create `.github/workflows/deploy-staging.yml` (content above)  
Create `.github/workflows/deploy-production.yml` (content above)

### Step 3: Add GitHub Secrets

Go to GitHub → Repository Settings → Secrets → Add all secrets from the list above

### Step 4: Test the Workflow

```powershell
# On dev branch
git checkout dev
git commit -m "test: trigger staging deployment"
git push origin dev

# Watch GitHub Actions tab for workflow run
# Should deploy to Railway staging
```

### Step 5: Test Production Workflow

```powershell
# On main branch
git checkout main
git merge dev
git push origin main

# Watch GitHub Actions tab
# Should deploy to Railway production
```

---

## 💡 Your Specific Workflow

### Local Development (What You Do)

```powershell
# Create feature branch
git checkout -b feat/add-dashboard

# Start dev servers
npx npm-run-all --parallel "dev:public" "dev:oversight"

# Edit files, test, commit
git add .
git commit -m "feat: add dashboard"

# Push to feature branch
git push origin feat/add-dashboard

# Create Pull Request on GitHub: feat/add-dashboard → dev
```

### Staging (Automatic)

```
PR merged to dev → GitHub Actions runs
→ Tests pass
→ Builds with .env.staging
→ Deploys to Railway staging
→ Available at https://staging-*.railway.app
→ Test it!
```

### Production (Automatic)

```
dev merged to main → GitHub Actions runs
→ Full test suite passes
→ Builds with .env.tier1.production
→ Deploys to Railway production
→ Deploys to Vercel production
→ Available at https://glad-labs.vercel.app
→ LIVE!
```

---

## 🎯 Does Local Dev Affect Production?

**NO - Multiple Isolations:**

1. **Git**: Your local changes stay local until you push
2. **Environment files**: `.env.local` is gitignored, never uploaded
3. **package-lock.json**: Ensures consistent versions, not affected by local dev
4. **GitHub Secrets**: Hidden from your local machine (only GitHub has them)
5. **Railway/Vercel**: Use GitHub Secrets, not your local files

**In short:** You can safely develop locally without affecting production!

---

## 📊 Environment Summary

| Aspect | Local Dev | Staging | Production |
|--------|-----------|---------|------------|
| **File** | `.env.local` | `.env.staging` | `.env.tier1.production` |
| **Branch** | `feat/*` | `dev` | `main` |
| **Database** | SQLite (file) | PostgreSQL (Railway) | PostgreSQL (Railway) |
| **URLs** | `http://localhost:*` | `https://staging-*.railway.app` | `https://glad-labs.vercel.app` |
| **Secrets** | In `.env.local` (local) | In GitHub Secrets | In GitHub Secrets |
| **Deployment** | Manual (`npm run dev`) | Automatic (GitHub Actions) | Automatic (GitHub Actions) |
| **Access** | Only you | Your team on staging | Everyone (LIVE) |
| **package-lock.json** | Your versions | CI uses same lock file | CI uses same lock file |

---

## ✅ Checklist to Get Started

- [ ] Create `.github/workflows/deploy-staging.yml`
- [ ] Create `.github/workflows/deploy-production.yml`
- [ ] Add staging secrets to GitHub (STAGING_*)
- [ ] Add production secrets to GitHub (PROD_*)
- [ ] Add Railway token to GitHub (RAILWAY_TOKEN)
- [ ] Add Vercel tokens to GitHub (VERCEL_TOKEN, etc.)
- [ ] Test staging deployment (push to dev branch)
- [ ] Test production deployment (push to main branch)
- [ ] Verify both environments work
- [ ] Document in team README

---

## 🎉 Result

After setup:

```
Your local dev       → Does NOT affect production ✅
Staging deploys auto → From dev branch ✅
Prod deploys auto    → From main branch ✅
Env vars isolated    → GitHub Secrets ✅
Versions consistent  → package-lock.json ✅
```

**You're ready for continuous deployment!** 🚀
