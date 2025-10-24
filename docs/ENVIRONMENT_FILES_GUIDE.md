# 🔐 Environment Files Organization & GitHub Setup Guide

**Last Updated:** October 23, 2025  
**Status:** ✅ Complete & Ready to Implement  
**Purpose:** Organize all `.env` files across monorepo and configure GitHub Secrets

---

## 📋 Table of Contents

1. [Current State Audit](#current-state-audit)
2. [Environment Files Structure](#environment-files-structure)
3. [GitHub Secrets Setup](#github-secrets-setup)
4. [Local Development Setup](#local-development-setup)
5. [Implementation Checklist](#implementation-checklist)

---

## 🔍 Current State Audit

### Root Level (`.env*`)

| File | Status | Purpose | Committed? |
|------|--------|---------|-----------|
| `.env` | ❌ Missing | Local development | NO (in .gitignore) |
| `.env.example` | ✅ Exists | Template for all envs | YES |
| `.env.staging` | ✅ Exists | Staging config | YES (no secrets) |
| `.env.production` | ❌ Missing | Production config | Should be YES (no secrets) |

### Workspace Level

| Workspace | `.env.example` | Purpose |
|-----------|---|---|
| `web/public-site/` | ✅ Exists | Frontend - Strapi API config |
| `web/oversight-hub/` | ✅ Exists | React admin - Firebase config |
| `src/cofounder_agent/` | ✅ Exists | Python backend - LLM config |

### .gitignore Status

**Currently ignored (GOOD ✅):**
```
.env              # Local development
.env.local        # Next.js overrides
.env.*.local      # Workspace-level locals
.env.production   # Production secrets (ISSUE: Should exist but not ignored!)
```

**Issue Found:** `.env.production` is in `.gitignore` but you need it as a template (without secrets)

---

## 📁 Environment Files Structure

### Recommended Organization

```
glad-labs-website/
├── .env                      (LOCAL DEV - never commit)
├── .env.example              (TEMPLATE - commit, document all vars)
├── .env.staging              (STAGING TEMPLATE - commit, no secrets)
├── .env.production           (PRODUCTION TEMPLATE - commit, no secrets)
│
├── web/
│   ├── public-site/
│   │   ├── .env.local        (LOCAL OVERRIDE - never commit)
│   │   └── .env.example      (TEMPLATE - commit)
│   │
│   └── oversight-hub/
│       ├── .env.local        (LOCAL OVERRIDE - never commit)
│       └── .env.example      (TEMPLATE - commit)
│
├── src/
│   └── cofounder_agent/
│       ├── .env.local        (LOCAL OVERRIDE - never commit)
│       └── .env.example      (TEMPLATE - commit)
│
└── .gitignore                (Updated to exclude .local files only)
```

### File Categories

**NEVER COMMIT (Secrets) 🔒:**
- `.env` (root local)
- `.env.local` (workspace locals)
- `.env.*.local` (branch-specific locals)
- Actual credentials, API keys, passwords

**ALWAYS COMMIT (Templates) ✅:**
- `.env.example` (all variables with CHANGE_ME)
- `.env.staging` (staging URLs + placeholder vars)
- `.env.production` (production URLs + placeholder vars)
- Documentation and comments

---

## 🔐 GitHub Secrets Setup

### What Are GitHub Secrets?

GitHub Secrets are encrypted environment variables stored in your repository settings. They're only exposed during GitHub Actions workflows and never appear in logs.

### GitHub Secrets to Create

Create these secrets in: **Settings → Secrets and variables → Actions**

#### Frontend Secrets (Vercel/Public Site)

| Secret Name | Value | Source |
|-------------|-------|--------|
| `NEXT_PUBLIC_STRAPI_API_URL` | `https://cms.railway.app` | Your Strapi URL |
| `NEXT_PUBLIC_STRAPI_API_TOKEN` | `<your-strapi-api-token>` | Strapi Admin Panel → Settings → API Tokens |
| `VERCEL_TOKEN` | `<your-vercel-token>` | Vercel Dashboard → Settings → Tokens |
| `VERCEL_PROJECT_ID` | `<project-id>` | Vercel Project → Settings → Project ID |
| `VERCEL_ORG_ID` | `<org-id>` | Vercel Account → Settings → Team ID |

#### Backend Secrets (Railway/Strapi/Agent)

| Secret Name | Value | Source |
|-------------|-------|--------|
| `RAILWAY_TOKEN` | `<your-railway-token>` | Railway Dashboard → Account → API Tokens |
| `RAILWAY_STAGING_PROJECT_ID` | `<staging-project-id>` | Railway → Project Settings |
| `RAILWAY_PROD_PROJECT_ID` | `<production-project-id>` | Railway → Project Settings |
| `STAGING_DB_HOST` | `<staging-db-host>` | Railway → PostgreSQL → Connection String |
| `STAGING_DB_USER` | `<username>` | Railway → PostgreSQL → Connection String |
| `STAGING_DB_PASSWORD` | `<password>` | Railway → PostgreSQL → Connection String |
| `PROD_DB_HOST` | `<prod-db-host>` | Railway → PostgreSQL → Connection String |
| `PROD_DB_USER` | `<username>` | Railway → PostgreSQL → Connection String |
| `PROD_DB_PASSWORD` | `<password>` | Railway → PostgreSQL → Connection String |
| `STAGING_STRAPI_TOKEN` | `<token>` | Strapi (staging) → Settings → API Tokens |
| `PROD_STRAPI_TOKEN` | `<token>` | Strapi (production) → Settings → API Tokens |

#### AI Provider Secrets

| Secret Name | Value | Source |
|-------------|-------|--------|
| `STAGING_OPENAI_API_KEY` | `sk-...` | OpenAI Dashboard → API Keys |
| `PROD_OPENAI_API_KEY` | `sk-...` | OpenAI Dashboard → API Keys |
| `STAGING_ANTHROPIC_API_KEY` | `sk-ant-...` | Anthropic Console → API Keys |
| `PROD_ANTHROPIC_API_KEY` | `sk-ant-...` | Anthropic Console → API Keys |

#### Additional Infrastructure Secrets

| Secret Name | Value | Source |
|-------------|-------|--------|
| `STAGING_REDIS_HOST` | `<redis-host>` | Railway → Redis → Host |
| `STAGING_REDIS_PASSWORD` | `<password>` | Railway → Redis → Connection |
| `PROD_REDIS_HOST` | `<redis-host>` | Railway → Redis → Host |
| `PROD_REDIS_PASSWORD` | `<password>` | Railway → Redis → Connection |
| `STAGING_SMTP_HOST` | `<smtp-host>` | SendGrid/Mailgun |
| `STAGING_SMTP_USER` | `<user>` | SendGrid/Mailgun |
| `STAGING_SMTP_PASSWORD` | `<password>` | SendGrid/Mailgun |

### How to Add GitHub Secrets

1. Go to your repository on GitHub
2. Click **Settings** (top right)
3. Click **Secrets and variables** (left sidebar)
4. Click **Actions**
5. Click **New repository secret**
6. Enter secret name (e.g., `NEXT_PUBLIC_STRAPI_API_TOKEN`)
7. Enter secret value (your actual token)
8. Click **Add secret**

**Repeat for all secrets in the table above.**

---

## 💻 Local Development Setup

### Step 1: Create Root `.env` File

**Location:** `c:\Users\mattm\glad-labs-website\.env`

**Copy from:** `.env.example` and replace placeholders

```bash
# For local development, use these values:

NODE_ENV=development
LOG_LEVEL=DEBUG

# Port Configuration (keep defaults)
STRAPI_PORT=1337
PUBLIC_SITE_PORT=3000
OVERSIGHT_HUB_PORT=3001
COFOUNDER_AGENT_PORT=8000

# Strapi - Local (SQLite)
DATABASE_CLIENT=sqlite
DATABASE_FILENAME=.tmp/data.db

# Strapi CMS - Local
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
STRAPI_API_TOKEN=dev-token-12345

# Frontend URLs (localhost)
NEXT_PUBLIC_API_BASE_URL=http://localhost:3000
NEXT_PUBLIC_COFOUNDER_AGENT_URL=http://localhost:8000

# AI Provider Keys (choose at least ONE)
# Option 1: OpenAI
OPENAI_API_KEY=sk-your-openai-key-here

# Option 2: Anthropic Claude
# ANTHROPIC_API_KEY=sk-ant-your-key-here

# Option 3: Google Gemini
# GOOGLE_API_KEY=your-google-key-here

# Option 4: Use free local Ollama
# USE_OLLAMA=true
# OLLAMA_HOST=http://localhost:11434

# Feature flags (all enabled for local testing)
ENABLE_ANALYTICS=false
ENABLE_ERROR_REPORTING=false
ENABLE_DEBUG_LOGS=true
ENABLE_PAYMENT_PROCESSING=false
ENABLE_VOICE_INTERFACE=true

# Timeouts (permissive for local development)
API_TIMEOUT=30000
API_RETRY_ATTEMPTS=1
RATE_LIMIT_REQUESTS_PER_MINUTE=10000
```

**⚠️ Important:** Never commit this file. It's in `.gitignore` and contains YOUR local credentials.

### Step 2: Create Workspace `.env.local` Files

These files override workspace-level environment variables for local development.

**`web/public-site/.env.local`:**
```bash
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
```

**`web/oversight-hub/.env.local`:**
```bash
REACT_APP_API_URL=http://localhost:8000
REACT_APP_STRAPI_URL=http://localhost:1337
```

**`src/cofounder_agent/.env.local`:**
```bash
# Copy AI key from root .env
OPENAI_API_KEY=sk-your-key-here
# OR
ANTHROPIC_API_KEY=sk-ant-your-key-here
# OR use Ollama
USE_OLLAMA=true
```

### Step 3: Verify .gitignore

Make sure `.gitignore` prevents committing local files:

```bash
# Environment variables
.env                # Root local
.env.local          # Workspace locals
.env.*.local        # Branch-specific locals
.env.production     # REMOVE THIS LINE if storing as template
```

---

## 📋 Environment Variables Reference

### Root Level (`.env` / `.env.example`)

| Variable | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `NODE_ENV` | string | Yes | `production` | Environment (development/staging/production) |
| `LOG_LEVEL` | string | Yes | `INFO` | Logging level (DEBUG/INFO/WARN/ERROR) |
| `DATABASE_CLIENT` | string | Yes | `sqlite` | Database type (sqlite/postgres) |
| `DATABASE_FILENAME` | string | If sqlite | `.tmp/data.db` | SQLite file location |
| `DATABASE_HOST` | string | If postgres | N/A | PostgreSQL host |
| `NEXT_PUBLIC_STRAPI_API_URL` | URL | Yes | `http://localhost:1337` | Strapi CMS URL |
| `STRAPI_API_TOKEN` | string | Yes | N/A | Strapi API token for authentication |
| `NEXT_PUBLIC_API_BASE_URL` | URL | No | `http://localhost:3000` | Backend API URL |
| `OPENAI_API_KEY` | string | No | N/A | OpenAI API key (one of 3 required) |
| `ANTHROPIC_API_KEY` | string | No | N/A | Anthropic API key (one of 3 required) |
| `GOOGLE_API_KEY` | string | No | N/A | Google Gemini API key (one of 3 required) |
| `ENABLE_ANALYTICS` | boolean | No | `true` | Enable Google Analytics |
| `ENABLE_ERROR_REPORTING` | boolean | No | `true` | Enable error tracking (Sentry) |
| `API_TIMEOUT` | number | No | `10000` | API request timeout (ms) |

### Workspace Level Variables

**Frontend (`web/public-site/.env.example`):**
- `NEXT_PUBLIC_STRAPI_API_URL` - Strapi endpoint
- `NEXT_PUBLIC_SITE_URL` - Public site URL

**Admin (`web/oversight-hub/.env.example`):**
- `REACT_APP_API_URL` - Backend API endpoint
- `REACT_APP_STRAPI_URL` - Strapi endpoint

**Backend (`src/cofounder_agent/.env.example`):**
- `PARSING_LLM_PROVIDER` - LLM for parsing (gemini/ollama)
- `INSIGHTS_LLM_PROVIDER` - LLM for insights
- `CONTENT_LLM_PROVIDER` - LLM for content
- `GEMINI_API_KEY` - Google Gemini key (if using)
- `OLLAMA_API_URL` - Ollama server URL (if using local)

---

## 🚀 Implementation Checklist

### Phase 1: Create Missing Files (15 minutes)

- [ ] Create `.env.production` file at root level:
  - Copy from `.env.staging`
  - Replace staging URLs with production URLs
  - Keep placeholders like `${PROD_DB_HOST}` (for GitHub Actions)
  - Example:
    ```bash
    NODE_ENV=production
    DATABASE_HOST=${PROD_DB_HOST}
    NEXT_PUBLIC_STRAPI_API_URL=https://cms.railway.app
    OPENAI_API_KEY=${PROD_OPENAI_API_KEY}
    ```

- [ ] Create root `.env` file (LOCAL - never commit):
  - Use template from "Local Development Setup" section above
  - Add YOUR actual API keys
  - Add to `.gitignore` (already done)

- [ ] Create workspace `.env.local` files:
  - `web/public-site/.env.local`
  - `web/oversight-hub/.env.local`
  - `src/cofounder_agent/.env.local`

### Phase 2: Update .gitignore (5 minutes)

- [ ] Verify `.env` is ignored (don't commit local secrets)
- [ ] Verify `.env.local` is ignored (workspace locals)
- [ ] Allow `.env.example` (template)
- [ ] Allow `.env.staging` (template)
- [ ] Allow `.env.production` (template - once created)

**Update pattern:**
```bash
# Environment variables (local - never commit)
.env                # Root local development
.env.local          # Workspace locals
.env.*.local        # Branch-specific

# Allow templates (these should be committed)
!.env.example
!.env.staging
!.env.production
```

### Phase 3: Add GitHub Secrets (20 minutes)

1. [ ] Go to GitHub → Settings → Secrets and variables → Actions
2. [ ] Create all secrets from "GitHub Secrets Setup" table above
3. [ ] Verify each secret is set (green checkmark)
4. [ ] Document all secrets in a shared note (keep safe)

**Required Secrets (minimum):**
- [ ] `NEXT_PUBLIC_STRAPI_API_URL`
- [ ] `NEXT_PUBLIC_STRAPI_API_TOKEN`
- [ ] `RAILWAY_TOKEN`
- [ ] `VERCEL_TOKEN`

### Phase 4: Test Locally (10 minutes)

- [ ] Start services: `npm run dev`
- [ ] Verify Strapi loads: `http://localhost:1337`
- [ ] Verify frontend loads: `http://localhost:3000`
- [ ] Check logs for env var usage

### Phase 5: Git Commit (5 minutes)

```bash
# Stage new/modified committed files
git add .env.example .env.staging .env.production .gitignore

# Commit
git commit -m "chore: organize environment files and templates

- Add .env.production template for production environment
- Add .env.staging template for staging environment
- Consolidate environment variable documentation
- Update .gitignore to allow .env templates
- Ready for GitHub Secrets configuration"

# Push
git push origin feat/test-branch
```

---

## 🔄 GitHub Actions Integration

### How GitHub Secrets Flow to Environments

**Deployment Workflow:**

```
GitHub Secrets (Encrypted)
    ↓
GitHub Actions Workflow
    ├→ Reads all secrets (only in Actions, never logged)
    ├→ Renders .env.staging or .env.production with secret values
    ├→ Passes to Railway (backend) or Vercel (frontend)
    └→ Secrets NEVER exposed in logs or git history
```

### Example Workflow Variable Usage

In `.github/workflows/deploy-staging.yml`:

```yaml
env:
  # These come from GitHub Secrets, never exposed
  DATABASE_URL: postgresql://${{ secrets.STAGING_DB_USER }}:${{ secrets.STAGING_DB_PASSWORD }}@${{ secrets.STAGING_DB_HOST }}/glad_labs_staging

  # These come from .env.staging file
  NODE_ENV: staging
  LOG_LEVEL: debug
```

---

## ✅ Verification Steps

### Before Deploying

**1. Check Committed Files:**
```bash
git ls-files | grep "\.env"
# Expected output:
# .env.example
# .env.staging
# .env.production (once created)
```

**2. Check Ignored Files:**
```bash
git check-ignore .env .env.local
# Expected: Both files are ignored ✅
```

**3. Verify GitHub Secrets:**
```
GitHub → Settings → Secrets → Should show ~15+ secrets
```

**4. Test Environment Loading:**
```bash
# Root level
npm run env:select
# Should show: Environment selected: development

# Frontend
cd web/public-site && npm run dev
# Should load .env.local if exists, else .env.example
```

---

## 🚨 Troubleshooting

### Problem: "Environment variable not found"

**Solution:**
1. Check if variable is in `.env` file
2. Check if variable has `NEXT_PUBLIC_` prefix (for Next.js frontend)
3. Restart dev server after changing `.env`
4. For GitHub Actions: verify secret exists in Settings → Secrets

### Problem: "GitHub Actions build fails with undefined variables"

**Solution:**
1. Check GitHub Secrets are set: Settings → Secrets → should list all vars
2. Check `.env.staging` or `.env.production` has placeholder: `${SECRET_NAME}`
3. Verify GitHub Actions workflow uses `secrets.SECRET_NAME` syntax
4. Check GitHub Actions log for missing secrets

### Problem: "Local `.env` file committed to git"

**Solution:**
```bash
# Remove from git history
git rm --cached .env

# Verify it's ignored
git check-ignore .env  # Should confirm it's ignored

# Commit the removal
git commit -m "chore: stop tracking local .env file"
```

### Problem: "Different variables needed for different components"

**Solution:**
1. Each workspace can have its own `.env.example`
2. Each workspace can have its own `.env.local` (for local dev)
3. GitHub Secrets are shared across all workspaces
4. Use `${SECRET_NAME}` placeholders in `.env.staging` and `.env.production`

---

## 📚 Related Documentation

- **[04-DEVELOPMENT_WORKFLOW.md](./04-DEVELOPMENT_WORKFLOW.md)** - Git workflow
- **[03-DEPLOYMENT_AND_INFRASTRUCTURE.md](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** - Deployment details
- **[07-BRANCH_SPECIFIC_VARIABLES.md](./07-BRANCH_SPECIFIC_VARIABLES.md)** - Branch-specific config

---

## 🎯 Next Steps

1. **Create missing environment files** (Phase 1)
2. **Update .gitignore** (Phase 2)
3. **Add GitHub Secrets** (Phase 3)
4. **Test locally** (Phase 4)
5. **Commit and push** (Phase 5)
6. **Monitor first deployment** (Phase 6)

**Estimated Time:** 1 hour total

---

**Created by:** GitHub Copilot  
**Purpose:** Organize environment files and GitHub Secrets for GLAD Labs  
**Status:** ✅ Ready to implement
