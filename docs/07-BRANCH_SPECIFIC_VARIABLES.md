# Branch-Specific Variables & Deployment Environments

**Last Updated:** October 20, 2025  
**Purpose:** Configure Glad Labs to run different environments (local dev, staging, production) based on git branch  
**Scope:** Environment variables, deployment targets, API endpoints, database connections

---

## 🎯 Architecture Overview

**Three-Tier Deployment Strategy:**

```text
feat/*** branches
    ↓
[Local Development] ← npm run dev (localhost, SQLite, http://localhost:1337)
    ↓
    git push origin feat/...
    ↓
dev branch
    ↓
[Staging Environment] ← GitHub Actions staging workflow (dev.railway.app, shared test DB)
    ↓
    git push origin dev (via PR to main)
    ↓
main branch
    ↓
[Production Environment] ← GitHub Actions production workflow (Vercel + Railway.app)
    ↓
    Live traffic: https://glad-labs.vercel.app
```

---

## 📁 Environment Files Structure

**Recommended folder structure:**

```text
.
├── .env                         (NEVER commit - local dev defaults)
├── .env.example                 (Template with all variables)
├── .env.local                   (Next.js local override - NEVER commit)
├── .env.staging                 (Staging variables - COMMITTED, no secrets)
├── .env.production              (Production variables - COMMITTED, no secrets)
├── .env.secrets                 (NEVER commit - actual API keys locally)
├── .github/
│   └── workflows/
│       ├── deploy-staging.yml   (dev branch → Staging Railway)
│       ├── deploy-production.yml (main branch → Vercel + Production Railway)
│       └── test-on-feat.yml     (feat/* → Run tests only)
└── scripts/
    └── setup-env.sh             (Auto-select .env based on branch)
```

---

## 🔧 Implementation Steps

### Step 1: Create Branch-Specific .env Files

**`.env.staging`** (Commit this - no secrets!)

```bash
# STAGING ENVIRONMENT VARIABLES
# Deploy to: dev.railway.app
# Trigger: git push origin dev

NODE_ENV=staging
LOG_LEVEL=DEBUG

# === API Endpoints ===
NEXT_PUBLIC_STRAPI_API_URL=https://staging-cms.railway.app
STRAPI_API_TOKEN=<token-stored-in-GitHub-secrets>
NEXT_PUBLIC_API_BASE_URL=https://staging-api.railway.app
NEXT_PUBLIC_COFOUNDER_AGENT_URL=https://staging-agent.railway.app:8000

# === Database ===
DATABASE_CLIENT=postgres
DATABASE_HOST=staging-db.railway.app
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_staging
# DATABASE_USER and DATABASE_PASSWORD stored in GitHub Secrets

# === Services ===
STRAPI_PORT=1337
PUBLIC_SITE_PORT=3000
OVERSIGHT_HUB_PORT=3001
COFOUNDER_AGENT_PORT=8000

# === Feature Flags ===
ENABLE_ANALYTICS=true
ENABLE_ERROR_REPORTING=true
ENABLE_DEBUG_LOGS=false
ENABLE_PAYMENT_PROCESSING=false  # Staging uses test mode

# === Timeouts & Limits ===
API_TIMEOUT=15000
API_RETRY_ATTEMPTS=2
RATE_LIMIT_REQUESTS_PER_MINUTE=100
```

**`.env.production`** (Commit this - no secrets!)

```bash
# PRODUCTION ENVIRONMENT VARIABLES
# Deploy to: glad-labs.vercel.app (frontend) + Railway (backend)
# Trigger: git push origin main

NODE_ENV=production
LOG_LEVEL=INFO

# === API Endpoints ===
NEXT_PUBLIC_STRAPI_API_URL=https://cms.railway.app
STRAPI_API_TOKEN=<token-stored-in-GitHub-secrets>
NEXT_PUBLIC_API_BASE_URL=https://api.glad-labs.com
NEXT_PUBLIC_COFOUNDER_AGENT_URL=https://agent.glad-labs.com:8000

# === Database ===
DATABASE_CLIENT=postgres
DATABASE_HOST=prod-db.railway.app
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_production
# DATABASE_USER and DATABASE_PASSWORD stored in GitHub Secrets

# === Services ===
STRAPI_PORT=1337
PUBLIC_SITE_PORT=3000
OVERSIGHT_HUB_PORT=3001
COFOUNDER_AGENT_PORT=8000

# === Feature Flags ===
ENABLE_ANALYTICS=true
ENABLE_ERROR_REPORTING=true
ENABLE_DEBUG_LOGS=false
ENABLE_PAYMENT_PROCESSING=true  # Production uses live payments

# === Timeouts & Limits ===
API_TIMEOUT=10000
API_RETRY_ATTEMPTS=3
RATE_LIMIT_REQUESTS_PER_MINUTE=1000
```

**`.env` (Local Development - NEVER commit)**

```bash
# LOCAL DEVELOPMENT VARIABLES
# Branch: feat/*, dev (local only)
# Run: npm run dev

NODE_ENV=development
LOG_LEVEL=DEBUG

# === API Endpoints (all localhost) ===
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
STRAPI_API_TOKEN=dev-token-12345
NEXT_PUBLIC_API_BASE_URL=http://localhost:3000
NEXT_PUBLIC_COFOUNDER_AGENT_URL=http://localhost:8000

# === Database (SQLite for local dev) ===
DATABASE_CLIENT=sqlite
DATABASE_FILENAME=.tmp/data.db

# === Services ===
STRAPI_PORT=1337
PUBLIC_SITE_PORT=3000
OVERSIGHT_HUB_PORT=3001
COFOUNDER_AGENT_PORT=8000

# === Feature Flags (all enabled locally) ===
ENABLE_ANALYTICS=false  # Don't send data during dev
ENABLE_ERROR_REPORTING=false
ENABLE_DEBUG_LOGS=true
ENABLE_PAYMENT_PROCESSING=false

# === Timeouts & Limits (permissive for local debugging) ===
API_TIMEOUT=30000
API_RETRY_ATTEMPTS=1
RATE_LIMIT_REQUESTS_PER_MINUTE=10000
```

### Step 2: Update .gitignore

Make sure `.env` and `.env.secrets` are ignored:

```bash
# Environment files (local overrides)
.env
.env.local
.env.secrets
.env.*.local

# Committed config files that are not secrets
!.env.example
!.env.staging
!.env.production
```

### Step 3: Create Environment Selection Script

**`scripts/select-env.sh`** (macOS/Linux)

```bash
#!/bin/bash

# Auto-select environment based on current git branch
# Usage: source scripts/select-env.sh

BRANCH=$(git rev-parse --abbrev-ref HEAD)

if [[ $BRANCH == "main" ]]; then
    echo "📦 Loading PRODUCTION environment (.env.production)"
    cp .env.production .env.local
    export NODE_ENV=production
elif [[ $BRANCH == "dev" ]]; then
    echo "🚀 Loading STAGING environment (.env.staging)"
    cp .env.staging .env.local
    export NODE_ENV=staging
elif [[ $BRANCH == feat/* ]]; then
    echo "🔨 Loading LOCAL DEVELOPMENT environment (.env)"
    # Use .env if it exists, otherwise use .env.example
    if [ -f .env ]; then
        cp .env .env.local
    else
        cp .env.example .env.local
    fi
    export NODE_ENV=development
else
    echo "⚠️ Unknown branch: $BRANCH"
    echo "🔨 Defaulting to LOCAL DEVELOPMENT environment (.env)"
    cp .env .env.local
    export NODE_ENV=development
fi

echo "✅ Environment selected: $NODE_ENV"
```

**`scripts/select-env.ps1`** (Windows PowerShell)

```powershell
# Auto-select environment based on current git branch
# Usage: . scripts/select-env.ps1

$branch = git rev-parse --abbrev-ref HEAD

if ($branch -eq "main") {
    Write-Host "📦 Loading PRODUCTION environment (.env.production)" -ForegroundColor Green
    Copy-Item .env.production .env.local -Force
    $env:NODE_ENV = "production"
} elseif ($branch -eq "dev") {
    Write-Host "🚀 Loading STAGING environment (.env.staging)" -ForegroundColor Yellow
    Copy-Item .env.staging .env.local -Force
    $env:NODE_ENV = "staging"
} elseif ($branch -match "^feat/") {
    Write-Host "🔨 Loading LOCAL DEVELOPMENT environment (.env)" -ForegroundColor Cyan
    if (Test-Path .env) {
        Copy-Item .env .env.local -Force
    } else {
        Copy-Item .env.example .env.local -Force
    }
    $env:NODE_ENV = "development"
} else {
    Write-Host "⚠️ Unknown branch: $branch" -ForegroundColor Red
    Write-Host "🔨 Defaulting to LOCAL DEVELOPMENT environment (.env)" -ForegroundColor Cyan
    if (Test-Path .env) {
        Copy-Item .env .env.local -Force
    } else {
        Copy-Item .env.example .env.local -Force
    }
    $env:NODE_ENV = "development"
}

Write-Host "✅ Environment selected: $env:NODE_ENV" -ForegroundColor Green
```

### Step 4: Update package.json Scripts

Add environment-aware scripts:

```json
{
  "scripts": {
    "env:select": "node scripts/select-env.js",
    "dev": "npm run env:select && npm-run-all --parallel dev:*",
    "dev:strapi": "npm run develop --workspace=cms/strapi-main",
    "dev:oversight": "npm start --workspace=web/oversight-hub",
    "dev:public": "npm run dev --workspace=web/public-site",
    "build": "npm run env:select && npm run build --workspaces --if-present",
    "start": "npm run env:select && npm-run-all --parallel start:*"
  }
}
```

**`scripts/select-env.js`** (Node.js version for cross-platform)

```javascript
#!/usr/bin/env node

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// Get current branch
let branch;
try {
  branch = execSync('git rev-parse --abbrev-ref HEAD', {
    encoding: 'utf-8',
  }).trim();
} catch (error) {
  console.error('❌ Not in a git repository');
  process.exit(1);
}

let envFile;
let envLabel;

if (branch === 'main') {
  envFile = '.env.production';
  envLabel = 'PRODUCTION';
  process.env.NODE_ENV = 'production';
} else if (branch === 'dev') {
  envFile = '.env.staging';
  envLabel = 'STAGING';
  process.env.NODE_ENV = 'staging';
} else if (branch.startsWith('feat/')) {
  envFile = '.env';
  envLabel = 'LOCAL DEVELOPMENT';
  process.env.NODE_ENV = 'development';
} else {
  console.warn(`⚠️ Unknown branch: ${branch}`);
  envFile = '.env';
  envLabel = 'LOCAL DEVELOPMENT (default)';
  process.env.NODE_ENV = 'development';
}

// Copy env file to .env.local for Next.js
const source = path.join(__dirname, '..', envFile);
const dest = path.join(__dirname, '..', '.env.local');

if (!fs.existsSync(source)) {
  console.warn(`⚠️ ${envFile} not found, using .env.example`);
  fs.copyFileSync(path.join(__dirname, '..', '.env.example'), dest);
} else {
  fs.copyFileSync(source, dest);
}

console.log(`📦 Environment: ${envLabel} (${branch})`);
console.log(`✅ Loaded: ${envFile} → .env.local`);
```

---

## 🚀 GitHub Actions Workflows

### Workflow 1: Test on Feature Branches (feat/\*)

**`.github/workflows/test-on-feat.yml`**

```yaml
name: Test on Feature Branch

on:
  push:
    branches:
      - 'feat/**'
  pull_request:
    branches:
      - dev
      - main

jobs:
  test:
    runs-on: ubuntu-latest

    env:
      NODE_ENV: development

    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '18'
          cache: 'npm'

      - name: Install dependencies
        run: npm ci && npm ci --workspaces

      - name: Load local dev environment
        run: cp .env.example .env.local

      - name: Run tests
        run: npm run test

      - name: Run linting
        run: npm run lint:fix

      - name: Build check
        run: npm run build
```

### Workflow 2: Deploy to Staging (dev branch)

**`.github/workflows/deploy-staging.yml`**

```yaml
name: Deploy to Staging

on:
  push:
    branches:
      - dev

jobs:
  deploy-staging:
    runs-on: ubuntu-latest

    env:
      NODE_ENV: staging
      RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}

    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '18'
          cache: 'npm'

      - name: Install dependencies
        run: npm ci && npm ci --workspaces

      - name: Load staging environment
        run: cp .env.staging .env.local

      - name: Run tests
        run: npm run test

      - name: Build frontend
        run: npm run build --workspace=web/public-site
        env:
          NEXT_PUBLIC_STRAPI_API_URL: ${{ secrets.STAGING_STRAPI_URL }}
          NEXT_PUBLIC_STRAPI_API_TOKEN: ${{ secrets.STAGING_STRAPI_TOKEN }}

      - name: Deploy to Railway (staging)
        run: |
          npx railway link --project ${{ secrets.RAILWAY_STAGING_PROJECT_ID }}
          npx railway up
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
```

### Workflow 3: Deploy to Production (main branch)

**`.github/workflows/deploy-production.yml`**

```yaml
name: Deploy to Production

on:
  push:
    branches:
      - main

jobs:
  deploy-production:
    runs-on: ubuntu-latest

    env:
      NODE_ENV: production

    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '18'
          cache: 'npm'

      - name: Install dependencies
        run: npm ci && npm ci --workspaces

      - name: Load production environment
        run: cp .env.production .env.local

      - name: Run tests
        run: npm run test

      - name: Build frontend
        run: npm run build --workspace=web/public-site
        env:
          NEXT_PUBLIC_STRAPI_API_URL: ${{ secrets.PROD_STRAPI_URL }}
          NEXT_PUBLIC_STRAPI_API_TOKEN: ${{ secrets.PROD_STRAPI_TOKEN }}

      - name: Deploy to Vercel (frontend)
        uses: vercel/action@v5
        with:
          token: ${{ secrets.VERCEL_TOKEN }}
          projectId: ${{ secrets.VERCEL_PROJECT_ID }}
          orgId: ${{ secrets.VERCEL_ORG_ID }}

      - name: Deploy to Railway (backend)
        run: |
          npx railway link --project ${{ secrets.RAILWAY_PROD_PROJECT_ID }}
          npx railway up
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
```

---

## 🔐 GitHub Secrets Configuration

Set these secrets in GitHub repository settings (Settings → Secrets and variables → Actions):

**For complete, step-by-step setup instructions with examples, see: [GITHUB_SECRETS_SETUP.md](../reference/GITHUB_SECRETS_SETUP.md)**

**Development/Staging Secrets:**

```text
STAGING_STRAPI_URL
STAGING_STRAPI_TOKEN
RAILWAY_STAGING_PROJECT_ID
```

**Production Secrets:**

```text
PROD_STRAPI_URL
PROD_STRAPI_TOKEN
RAILWAY_TOKEN
RAILWAY_PROD_PROJECT_ID
VERCEL_TOKEN
VERCEL_PROJECT_ID
VERCEL_ORG_ID
```

👉 **Use [GITHUB_SECRETS_SETUP.md](../reference/GITHUB_SECRETS_SETUP.md) as your authoritative reference for all secrets configuration.**

---

## 📋 Workflow: Local Development → Staging → Production

### 1. Local Development (feat/\* branch)

```bash
# Switch to feature branch
git checkout -b feat/my-feature

# Start development environment (auto-loads .env)
npm run dev

# This launches:
# - Strapi: http://localhost:1337/admin (SQLite)
# - Public Site: http://localhost:3000
# - Oversight Hub: http://localhost:3001
# - Co-founder Agent: http://localhost:8000/docs

# Make changes, test, commit
git add .
git commit -m "feat: add my feature"
```

### 2. Push to Staging (dev branch)

```bash
# Create PR to dev branch
git push origin feat/my-feature

# Open GitHub PR: feat/my-feature → dev
# - Workflow: test-on-feat.yml runs tests, linting, build check
# - If all pass: Ready to merge to dev

# After review, merge to dev
git checkout dev
git merge --squash feat/my-feature
git push origin dev

# Workflow: deploy-staging.yml triggers
# - Loads .env.staging
# - Runs full test suite
# - Builds with staging API endpoints
# - Deploys to Railway staging environment
# - Available at: https://staging-cms.railway.app

# Test on staging, verify against test database
```

### 3. Promote to Production (main branch)

```bash
# Create PR: dev → main
git checkout main
git pull origin main
git merge --no-ff dev
git push origin main

# Workflow: deploy-production.yml triggers
# - Loads .env.production
# - Runs full test suite
# - Builds with production API endpoints
# - Deploys frontend to Vercel
# - Deploys backend to Railway production
# - Available at: https://glad-labs.vercel.app

# Live traffic immediately after successful deploy
```

---

## 🔀 Branch Strategy Summary

| Branch   | Environment | Variables         | Database              | Deploy Target         | Testing                 |
| -------- | ----------- | ----------------- | --------------------- | --------------------- | ----------------------- |
| `feat/*` | Local Dev   | `.env`            | SQLite (local)        | None (local only)     | Manual + GitHub Actions |
| `dev`    | Staging     | `.env.staging`    | Postgres (staging DB) | Railway staging       | GitHub Actions          |
| `main`   | Production  | `.env.production` | Postgres (prod DB)    | Vercel + Railway prod | GitHub Actions          |

---

## ✅ Setup Checklist

- [ ] Create `.env.staging` with staging endpoints
- [ ] Create `.env.production` with production endpoints
- [ ] Keep `.env` in `.gitignore` (never commit)
- [ ] Update `.gitignore` to allow `.env.staging` and `.env.production`
- [ ] Create `scripts/select-env.js` for automatic env selection
- [ ] Update `package.json` to call `npm run env:select` before dev/build
- [ ] Create GitHub Actions workflows: `test-on-feat.yml`, `deploy-staging.yml`, `deploy-production.yml`
- [ ] Add secrets to GitHub repository
- [ ] Test locally: `npm run dev` on feat/\* branch
- [ ] Test staging push: `git push origin dev` and monitor GitHub Actions
- [ ] Test production push: `git push origin main` and verify Vercel + Railway deployment

---

## 🐛 Troubleshooting

**Issue: "Cannot find .env file"**

````bash
## 🐛 Troubleshooting

**Issue: "Cannot find .env file"**

```bash
# Solution: Create it from example
cp .env.example .env

# Or let the script do it
npm run env:select
````

**Issue: "Environment variables not loading in Next.js"**

```bash
# Solution: Ensure .env.local exists and has NEXT_PUBLIC_* prefix
# Next.js only exposes variables with NEXT_PUBLIC_ prefix to browser

# Verify in browser DevTools → Application → Environment Variables
console.log(process.env.NEXT_PUBLIC_STRAPI_API_URL);
```

**Issue: "Different endpoints between local, staging, and production"**

```bash
# Verify each environment file has correct URLs:
grep "STRAPI_API_URL" .env .env.staging .env.production

# Expected:
# .env: http://localhost:1337
# .env.staging: https://staging-cms.railway.app
# .env.production: https://cms.railway.app
```

**Issue: "API calls failing in staging/production"**

```bash
# Check if API tokens are set in GitHub Secrets
# Go to: Settings → Secrets and variables → Actions
# Verify STAGING_STRAPI_TOKEN and PROD_STRAPI_TOKEN exist

# Check workflow logs: GitHub → Actions → [workflow name]
# Look for "Load staging environment" step to confirm env vars loaded
```

---

## 📚 Key Files Reference

| File                                      | Purpose                         |
| ----------------------------------------- | ------------------------------- |
| `.env`                                    | Local dev (NEVER commit)        |
| `.env.staging`                            | Staging config (commit)         |
| `.env.production`                         | Production config (commit)      |
| `.env.example`                            | Template (commit)               |
| `scripts/select-env.js`                   | Auto-select env based on branch |
| `.github/workflows/test-on-feat.yml`      | Test feature branches           |
| `.github/workflows/deploy-staging.yml`    | Deploy dev→staging              |
| `.github/workflows/deploy-production.yml` | Deploy main→production          |

---

## 🎯 Next Steps

1. Create `.env.staging` and `.env.production` files with your actual endpoints
2. Add production/staging secrets to GitHub
3. Create GitHub Actions workflows
4. Test the workflow: feat branch → push → staging deploy → main → production deploy
5. Monitor deployments via GitHub Actions logs and Vercel dashboard

---

**Questions?** Refer to `.github/workflows/` for workflow syntax or `docs/00-README.md` for general project structure.
