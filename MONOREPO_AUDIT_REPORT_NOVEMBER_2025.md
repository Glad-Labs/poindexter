# 🔍 Monorepo Configuration Audit Report

**Date:** November 4, 2025  
**Status:** ⚠️ REQUIRES IMMEDIATE UPDATES  
**Severity:** High - Workspace inconsistencies detected  
**Priority:** P1 - Fix before next production deployment

---

## Executive Summary

Your monorepo has **evolved significantly**, but configuration files are not fully aligned:

✅ **Working Well:**

- 8 core documentation files in `/docs/` (00-07)
- GitHub Actions workflows configured (staging + production)
- Requirements.txt properly configured (asyncpg, no psycopg2)
- Node.js workspaces properly structured
- Python backend ready (psycopg2 fix applied)

⚠️ **Needs Immediate Attention:**

- Root `package.json` references `cms/strapi-main` but directory is `cms/strapi-main/` ✓ (actually OK)
- 8 core docs are **SEVERELY OUTDATED** - do NOT reflect current architecture
- GitHub Secrets setup not fully documented for current CI/CD flow
- Railway/Vercel configuration incomplete
- Node engine versions mismatched across workspaces
- Environment variable strategy inconsistent

❌ **Critical Issues:**

- Core docs (03-DEPLOYMENT.md especially) reference OLD platforms/setup
- No comprehensive secrets reference for GitHub Actions
- Missing production readiness validation checklist

---

## 🏗️ Part 1: Package.json Consistency Audit

### Root package.json (`package.json`)

```json
{
  "version": "3.0.0",
  "workspaces": [
    "web/public-site",
    "web/oversight-hub",
    "cms/strapi-main",        ✓ Correct path
    "src/cofounder_agent"     ✗ NOT a workspace (no package.json)
  ]
}
```

**ISSUE:** `src/cofounder_agent` is Python, not Node.js workspace. Should be removed from npm workspaces.

---

### Workspace: web/oversight-hub/package.json

```json
{
  "name": "dexters-lab",  ⚠️ Name mismatch! Should be "oversight-hub"
  "version": "0.1.0",     ⚠️ Should match root v3.0.0
  "engines": {
    "node": "Missing!"     ❌ No node version specified
  }
}
```

**Dependencies:** React 18, Material-UI 7, Zustand 5 ✓

---

### Workspace: web/public-site/package.json

```json
{
  "name": "glad-labs-public-site",
  "version": "0.1.0",      ⚠️ Should match root v3.0.0
  "engines": {
    "node": ">=18.0.0",    ✓ Has version
    "npm": ">=9.0.0"       ✓ Has npm version
  }
}
```

**Dependencies:** Next.js 15.1, React 18.3 ✓

---

### Workspace: cms/strapi-main/package.json

```json
{
  "name": "strapi",
  "version": "0.1.0",       ⚠️ Should match root v3.0.0
  "engines": {
    "node": ">=18.0.0 <=22.x.x",  ✓ Correctly restricted
    "npm": ">=6.0.0"               ✓ Has npm version
  }
}
```

**Dependencies:** Strapi 5.18.1, pg 8.8.0 ✓

---

### Python Backend: src/cofounder_agent/requirements.txt

```
mcp>=1.0.0
openai>=1.30.0
anthropic>=0.18.0
fastapi>=0.104.0
uvicorn>=0.24.0
sqlalchemy[asyncio]>=2.0.0
asyncpg>=0.29.0  ✓ NO psycopg2 (correct!)
```

**Status:** ✓ Properly configured for asyncpg, no psycopg2

---

## 🔴 Part 2: Critical Inconsistencies

### Issue 1: Version Mismatch

| Package       | Root  | Current | Should Be |
| ------------- | ----- | ------- | --------- |
| Root          | 3.0.0 | 3.0.0   | ✓         |
| oversight-hub | 0.1.0 | 0.1.0   | ❌ 3.0.0  |
| public-site   | 0.1.0 | 0.1.0   | ❌ 3.0.0  |
| strapi-main   | 0.1.0 | 0.1.0   | ❌ 3.0.0  |

**Fix Required:** Update all workspace versions to `"3.0.0"`

### Issue 2: Node Version Specification

| Package       | Specified         | Issue                |
| ------------- | ----------------- | -------------------- |
| Root          | `>=18.0.0`        | ✓ Good               |
| oversight-hub | **NOT SPECIFIED** | ❌ Should have it    |
| public-site   | `>=18.0.0`        | ✓ Good               |
| strapi-main   | `>=18 <=22.x.x`   | ✓ Good (restrictive) |

**Fix Required:** Add `"engines"` to oversight-hub

### Issue 3: Package Names

| Package     | Current Name          | Should Be                    |
| ----------- | --------------------- | ---------------------------- |
| Root        | glad-labs-monorepo    | ✓                            |
| Oversight   | `dexters-lab`         | ❌ Should be `oversight-hub` |
| Public Site | glad-labs-public-site | ✓ (acceptable)               |
| Strapi      | `strapi`              | ✓ (generic is OK)            |

---

## 🔑 Part 3: GitHub Actions Secrets Audit

### Current GitHub Actions Workflows Found:

```
✓ .github/workflows/test-on-feat.yml
✓ .github/workflows/test-on-dev.yml
✓ .github/workflows/deploy-staging-with-environments.yml
✓ .github/workflows/deploy-production-with-environments.yml
```

### Secrets Referenced in Workflows:

**STAGING DEPLOYMENT REQUIRES:**

```
RAILWAY_TOKEN                          ✓ Set for all Railway CLI operations
RAILWAY_STAGING_PROJECT_ID             ✓ Staging project ID
STRAPI_STAGING_DB_HOST                 ✓ Database host
STRAPI_STAGING_DB_USER                 ✓ Database user
STRAPI_STAGING_DB_PASSWORD             ✓ Database password
STRAPI_STAGING_ADMIN_PASSWORD          ✓ Strapi admin password
STRAPI_STAGING_JWT_SECRET              ✓ JWT secret
```

**PRODUCTION DEPLOYMENT REQUIRES:**

```
RAILWAY_TOKEN                          ✓ Same token as staging
RAILWAY_PROD_PROJECT_ID                ✓ Production project ID
STRAPI_PROD_DB_HOST                    ✓ Production database host
STRAPI_PROD_DB_USER                    ✓ Production database user
STRAPI_PROD_DB_PASSWORD                ✓ Production database password
STRAPI_PROD_ADMIN_PASSWORD             ✓ Production admin password
STRAPI_PROD_JWT_SECRET                 ✓ Production JWT secret
```

**MISSING SECRETS (CRITICAL):**

```
❌ OPENAI_API_KEY                  (Needed for FastAPI backend)
❌ ANTHROPIC_API_KEY               (Fallback model provider)
❌ GOOGLE_API_KEY                  (Fallback model provider)
❌ OLLAMA_HOST                     (If using local Ollama)
❌ VERCEL_TOKEN                    (Frontend deployment)
❌ VERCEL_PROJECT_ID               (Oversight Hub)
❌ VERCEL_ORG_ID                   (Vercel organization)
❌ FRONTEND_STAGING_URL            (For testing)
❌ FRONTEND_PROD_URL               (For testing)
```

---

## 🚀 Part 4: Deployment Platform Configuration

### Railway Configuration Status

**✓ WHAT'S CONFIGURED:**

- Strapi CMS deployment (database + Node.js)
- FastAPI backend deployment (Python)
- Both use GitHub Actions triggers

**❌ WHAT'S MISSING:**

- Environment variable documentation
- Railway secret injection process
- Service interdependencies configuration
- Health check endpoints setup
- Resource limits specification

### Vercel Configuration Status

**✓ WHAT'S CONFIGURED:**

- Two separate projects (public-site, oversight-hub)
- Next.js build configuration

**❌ WHAT'S MISSING:**

- Environment variables for both projects:
  - `NEXT_PUBLIC_STRAPI_API_URL`
  - `NEXT_PUBLIC_API_BASE_URL`
  - `REACT_APP_STRAPI_TOKEN` (for oversight-hub)
  - `REACT_APP_API_URL` (for oversight-hub)
- Build hooks
- Preview deployments setup

---

## 📚 Part 5: Documentation Status (Core Docs 00-07)

### Current State of 8 Core Documentation Files

| Doc | File                                | Status          | Last Update | Issues                              |
| --- | ----------------------------------- | --------------- | ----------- | ----------------------------------- |
| 00  | 00-README.md                        | ⚠️ STALE        | Oct 22      | Outdated project links              |
| 01  | 01-SETUP_AND_OVERVIEW.md            | ⚠️ STALE        | Oct 22      | Still references old platforms      |
| 02  | 02-ARCHITECTURE_AND_DESIGN.md       | ⚠️ STALE        | Oct 22      | Missing MCP & content agent details |
| 03  | 03-DEPLOYMENT_AND_INFRASTRUCTURE.md | 🔴 **CRITICAL** | Oct 22      | **SEVERELY OUTDATED** (see below)   |
| 04  | 04-DEVELOPMENT_WORKFLOW.md          | ⚠️ STALE        | Oct 22      | Branch strategy may be obsolete     |
| 05  | 05-AI_AGENTS_AND_INTEGRATION.md     | ⚠️ STALE        | Oct 22      | Missing current agent architecture  |
| 06  | 06-OPERATIONS_AND_MAINTENANCE.md    | ⚠️ STALE        | Oct 22      | Monitoring setup incomplete         |
| 07  | 07-BRANCH_SPECIFIC_VARIABLES.md     | ⚠️ STALE        | Oct 22      | May not match actual workflows      |

### 🔴 CRITICAL: 03-DEPLOYMENT_AND_INFRASTRUCTURE.md Issues

**Current Content References:**

- ❌ "Option 1: Railway Template" - outdated instructions
- ❌ "Strapi Production Configuration" - doesn't match current Strapi v5
- ❌ "FastAPI Production Configuration" - references old patterns
- ❌ No mention of asyncpg driver configuration
- ❌ Missing GitHub Secrets setup reference
- ❌ No Vercel environment variables documented
- ❌ No Railway-specific database connection setup
- ❌ References old branch names (staging/dev/main)

**What Needs to be Added:**

```
✓ Comprehensive GitHub Actions secrets guide
✓ Environment-specific variable strategy
✓ Railway + Vercel integration details
✓ Database connection string format (with asyncpg)
✓ Production readiness checklist
✓ Health check endpoint verification
✓ Monitoring & alerting setup
✓ Rollback procedures
✓ Cost optimization tips
```

---

## 🔧 Part 6: Current Environment Variable Strategy

### Local Development (.env)

```bash
NODE_ENV=development
USE_OLLAMA=true
DATABASE_CLIENT=sqlite
DATABASE_FILENAME=.tmp/data.db
# All localhost URLs
```

✓ Status: Correct

### Staging Environment (.env.staging)

```bash
NODE_ENV=staging
DATABASE_CLIENT=postgres
DATABASE_URL=${{ secrets.STAGING_DATABASE_URL }}
# Production URLs
```

⚠️ Status: Exists but not documented in current docs

### Production Environment (.env.production)

```bash
NODE_ENV=production
DATABASE_CLIENT=postgres
DATABASE_URL=${{ secrets.PROD_DATABASE_URL }}
# Production URLs
```

⚠️ Status: Exists but not documented in current docs

---

## 📋 Part 7: Required Actions (Priority Order)

### 🔴 IMMEDIATE (Do Now - Blocks Production)

**1. Fix package.json Versions**

```bash
# Update all workspace package.json files to v3.0.0
web/oversight-hub/package.json       → "version": "3.0.0"
web/public-site/package.json         → "version": "3.0.0"
cms/strapi-main/package.json         → "version": "3.0.0"
```

**2. Add Missing Node Engine Specification**

```json
// web/oversight-hub/package.json - add:
"engines": {
  "node": ">=18.0.0",
  "npm": ">=9.0.0"
}
```

**3. Fix oversight-hub Package Name**

```json
// web/oversight-hub/package.json - change:
"name": "dexters-lab"
// to:
"name": "oversight-hub"
```

**4. Remove src/cofounder_agent from npm Workspaces**

```json
// Root package.json - change:
"workspaces": [
  "web/public-site",
  "web/oversight-hub",
  "cms/strapi-main"
  // Remove: "src/cofounder_agent"
]
```

**5. Add Missing GitHub Secrets**

Go to: **GitHub Repository → Settings → Secrets and variables → Actions**

Add these secrets:

```
# AI Model API Keys (Choose at least one)
OPENAI_API_KEY=sk-...                    (Add if using OpenAI)
ANTHROPIC_API_KEY=sk-ant-...             (Add if using Anthropic)
GOOGLE_API_KEY=AIza-...                  (Add if using Google)

# Vercel Frontend Deployment
VERCEL_TOKEN=<your-vercel-token>         (Get from Vercel dashboard)
VERCEL_PROJECT_ID=<oversight-hub-id>     (From Vercel project settings)
VERCEL_ORG_ID=<org-id>                   (From Vercel organization settings)

# Database Configuration (optional - may be handled by Railway)
STAGING_DATABASE_URL=postgresql://...    (If not using Railway's DB)
PROD_DATABASE_URL=postgresql://...       (If not using Railway's DB)
```

### 🟡 HIGH PRIORITY (This Week)

**6. Update Core Documentation (All 8 Files)**

The documentation needs a complete refresh:

- **03-DEPLOYMENT_AND_INFRASTRUCTURE.md** - Rewrite entire file
  - Add Railway+Vercel integration guide
  - Document GitHub Secrets setup
  - Add environment variable strategy
  - Include health check procedures
  - Add monitoring setup

- **01-SETUP_AND_OVERVIEW.md** - Update setup section
  - Reflect current platform choices
  - Update PostgreSQL vs SQLite guidance
  - Add Railway quick-start link

- **02-ARCHITECTURE_AND_DESIGN.md** - Add missing sections
  - Document MCP integration
  - Detail content agent pipeline
  - Update deployment architecture diagram

- **04-DEVELOPMENT_WORKFLOW.md** - Update branch strategy
  - Verify branch names (dev/staging/main)
  - Update deployment flow diagrams
  - Add GitHub Actions info

- **05-AI_AGENTS_AND_INTEGRATION.md** - Refresh agent system
  - Document current content agent system
  - Add MCP details
  - Update examples

- **06-OPERATIONS_AND_MAINTENANCE.md** - Update monitoring
  - Add health check procedures
  - Document Railway monitoring
  - Add scaling procedures

- **07-BRANCH_SPECIFIC_VARIABLES.md** - Verify alignment
  - Match to current GitHub Actions workflows
  - Update deployment triggers

- **00-README.md** - Update hub navigation
  - Verify all links still valid
  - Update status indicators

### 🟢 MEDIUM PRIORITY (Next Sprint)

**7. Create GitHub Secrets Reference Document**

Create: `docs/reference/GITHUB_SECRETS_COMPLETE_GUIDE.md`

Contents:

```markdown
- All required secrets with descriptions
- How to generate/obtain each secret
- Where to find in each platform (Vercel, Railway, etc.)
- Staging vs Production differences
- Secret rotation procedures
- Backup/recovery procedures
```

**8. Create Production Readiness Checklist**

Create: `docs/PRODUCTION_READINESS_CHECKLIST.md`

Contents:

```markdown
- Pre-deployment verification steps
- GitHub Secrets validation
- Railway configuration verification
- Vercel environment variables check
- Health endpoint verification
- Monitoring setup confirmation
- Backup verification
- Rollback procedure testing
```

**9. Create Platform-Specific Guides**

- `docs/RAILWAY_CONFIGURATION_GUIDE.md`
- `docs/VERCEL_CONFIGURATION_GUIDE.md`
- `docs/GITHUB_ACTIONS_SECRETS_GUIDE.md`

---

## 📊 Part 8: Current Configuration Summary

### What's Currently Deployed

**Frontend (Vercel):**

- ✓ Public Site (Next.js)
- ✓ Oversight Hub (React)
- ⚠️ Environment variables may be incomplete

**Backend (Railway):**

- ✓ Strapi CMS (Node.js + SQLite/Postgres)
- ✓ FastAPI Co-Founder (Python)
- ✓ PostgreSQL Database
- ⚠️ No health checks documented

**CI/CD (GitHub Actions):**

- ✓ Test on feature branches
- ✓ Deploy to staging (dev branch)
- ✓ Deploy to production (main branch)
- ⚠️ Some secrets missing

---

## ✅ Recommended Production Deployment Flow

```
1. Commit changes locally
   └─ git add . && git commit -m "..."

2. Push to feature branch
   └─ git push origin feat/my-feature
   └─ GitHub Actions: Run tests only

3. Create PR to dev branch
   └─ Test on staging environment
   └─ Review and merge to dev
   └─ GitHub Actions: Deploy to Railway staging

4. Create PR to main branch
   └─ Test on production environment
   └─ Review and merge to main
   └─ GitHub Actions: Deploy to Vercel + Railway production

5. Verify production
   └─ Check health endpoints
   └─ Monitor logs
   └─ Confirm services online
```

---

## 🎯 Next Steps (Immediate Action Items)

### For Your Team:

1. ✅ **TODAY:**
   - [ ] Add missing GitHub Secrets (AI keys, Vercel tokens)
   - [ ] Update package.json versions to 3.0.0
   - [ ] Fix oversight-hub package name
   - [ ] Run `npm run clean:install` to verify everything builds

2. **THIS WEEK:**
   - [ ] Update all 8 core documentation files
   - [ ] Create GitHub Secrets reference guide
   - [ ] Create production readiness checklist
   - [ ] Test full deployment pipeline (staging → production)

3. **NEXT SPRINT:**
   - [ ] Create platform-specific configuration guides
   - [ ] Document health check procedures
   - [ ] Set up monitoring/alerting
   - [ ] Schedule quarterly documentation review

---

## 📞 Questions to Answer

Before proceeding with production deployment:

1. **AI Models:** Which providers are you using? (OpenAI, Anthropic, Google, Ollama?)
2. **Vercel Projects:** What are your exact project IDs and org ID?
3. **Railway Projects:** What are your exact project IDs for staging vs production?
4. **Domain:** What's your production domain? (for health check setup)
5. **Monitoring:** Do you want Sentry, DataDog, or basic logging?
6. **Backup:** Where should backups go? (S3, Railway backups, local?)

---

## 📎 Attachments & References

This audit references:

- `package.json` (root)
- `web/oversight-hub/package.json`
- `web/public-site/package.json`
- `cms/strapi-main/package.json`
- `src/cofounder_agent/requirements.txt`
- `.github/workflows/deploy-staging-with-environments.yml`
- `.github/workflows/deploy-production-with-environments.yml`
- `docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md`
- `.env.example`
- `pyproject.toml`

---

## 🏆 Success Criteria

Production deployment is ready when:

- ✅ All package.json files have consistent versions (3.0.0)
- ✅ All GitHub Secrets are configured
- ✅ Documentation is up-to-date (all 8 core files)
- ✅ Health check endpoints return 200 OK
- ✅ Both staging and production environments verified
- ✅ Backup procedures tested
- ✅ Rollback procedure documented
- ✅ Team trained on deployment process

---

**Status:** Ready for fixes → Ready for review → Ready for production

**Last Updated:** November 4, 2025  
**Reviewer:** GitHub Copilot
