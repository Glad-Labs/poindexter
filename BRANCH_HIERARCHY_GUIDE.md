# 🌳 New Branch Hierarchy Setup

**Last Updated:** October 24, 2025  
**Status:** ✅ Configured | Ready for Use  
**Free Tier Cost:** ~120 min/month (all workflows stay under 2,000 minute limit)

---

## 📋 Branch Hierarchy Overview

```
feat/*              → 🟢 Development (NO workflows - commit freely)
   ↓ (merge to dev when ready)
dev                 → 🟡 Testing (Comprehensive test suite runs)
   ↓ (merge to staging when tests pass)
staging             → 🟠 Staging (Deploy to staging environment)
   ↓ (merge to main when verified)
main                → 🔴 Production (Full tests + security + deploy to prod)
```

---

## 🚀 How It Works

### Phase 1: Development (feat/\* branches)

**No CI/CD runs** - Commit as much as you want!

```bash
git checkout -b feat/my-awesome-feature
# Make 50 commits, push 100 times, no workflows trigger
# Perfect for rapid iteration and frequent commits
```

**Testing:** Manual - Run locally before pushing

```bash
npm test                    # All tests
npm run test:frontend:ci    # Frontend only
npm run test:python         # Backend only
```

**When ready to test in CI/CD:** Create PR or merge to dev

---

### Phase 2: Testing (dev branch)

**Test suite runs automatically** - Full validation before staging

```bash
git checkout dev
git merge feat/my-awesome-feature

# ✅ Workflow: test-on-dev.yml triggers automatically
# ├─ Frontend tests (52)
# ├─ Backend tests (41)
# ├─ Linting
# └─ Build check
```

**Tests run:**

- ✅ npm run test:frontend:ci (52+ React tests)
- ✅ npm run test:python (41 backend tests)
- ✅ npm run lint:fix
- ✅ npm run build

**Duration:** ~8-10 minutes  
**Cost:** ~10 min/run × 10 runs/month = 100 min/month (FREE)

---

### Phase 3: Staging (staging branch)

**Deploy to staging environment** - after dev tests pass

```bash
git checkout staging
git merge dev

# ✅ Workflow: deploy-staging-with-environments.yml triggers
# ├─ Frontend tests (52)
# ├─ Backend tests (41)
# ├─ Build all workspaces
# ├─ Deploy Strapi CMS → Railway staging
# ├─ Deploy Co-Founder Agent → Railway staging
# ├─ Deploy Public Site → Vercel staging
# └─ Deploy Oversight Hub → Vercel staging
```

**Tests run:**

- ✅ npm run test:frontend:ci
- ✅ npm run test:python
- ✅ npm run build

**Deployment targets:**

- Strapi: `strapi-staging.railway.app`
- API: `agent-staging.railway.app`
- Public Site: `public-site-staging.vercel.app`
- Oversight: `oversight-staging.vercel.app`

**Duration:** ~15-20 minutes (includes deployment)  
**Cost:** ~5 runs/month = 100 min/month (FREE)

---

### Phase 4: Production (main branch)

**Deploy to production** - after staging verified

```bash
git checkout main
git merge staging

# ✅ Workflow: deploy-production-with-environments.yml triggers
# ├─ Frontend tests (52)
# ├─ Backend tests (41)
# ├─ Build all workspaces
# ├─ Security audit (npm audit)
# ├─ Deploy Strapi CMS → Railway production
# ├─ Deploy Co-Founder Agent → Railway production
# ├─ Deploy Public Site → Vercel production
# └─ Deploy Oversight Hub → Vercel production
```

**Tests run:**

- ✅ npm run test:frontend:ci
- ✅ npm run test:python
- ✅ npm run build
- ✅ npm audit --audit-level=moderate

**Deployment targets:**

- Strapi: `cms.railway.app`
- API: `api.glad-labs.com`
- Public Site: `https://glad-labs.com`
- Oversight: `https://oversight.glad-labs.com`

**Duration:** ~20-25 minutes (includes deployment)  
**Cost:** ~2 runs/month = 20 min/month (FREE)

---

## 📊 Workflow Files

### 1. test-on-dev.yml (NEW)

**Purpose:** Run comprehensive tests before staging deployment

**Triggers:**

- Push to `dev` branch only

**Tests:**

- Frontend tests (52)
- Backend tests (41)
- Linting
- Build check

**File location:** `.github/workflows/test-on-dev.yml`

```yaml
on:
  push:
    branches:
      - dev

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - npm run test:frontend:ci
      - npm run test:python
      - npm run lint:fix
      - npm run build --if-present
```

---

### 2. test-on-feat.yml (DISABLED)

**Purpose:** None - feature branches do no run workflows

**Status:** Disabled (only manual trigger via workflow_dispatch)

**File location:** `.github/workflows/test-on-feat.yml`

```yaml
# ⚠️ DISABLED - Feature branches do not run workflows
on:
  workflow_dispatch: # Only manual trigger (effectively disabled)
```

**Why:** Allows you to commit frequently without CI/CD overhead

---

### 3. deploy-staging-with-environments.yml (UPDATED)

**Purpose:** Test and deploy to staging environment

**Triggers:**

- Push to `staging` branch

**Tests before deploy:**

- Frontend tests (52)
- Backend tests (41)
- Build check

**Deployments:**

- Strapi CMS → Railway staging
- Co-Founder Agent → Railway staging
- Public Site → Vercel staging
- Oversight Hub → Vercel staging

**File location:** `.github/workflows/deploy-staging-with-environments.yml`

---

### 4. deploy-production-with-environments.yml (UPDATED)

**Purpose:** Test, security check, and deploy to production

**Triggers:**

- Push to `main` branch

**Tests before deploy:**

- Frontend tests (52)
- Backend tests (41)
- Build check
- Security audit

**Deployments:**

- Strapi CMS → Railway production
- Co-Founder Agent → Railway production
- Public Site → Vercel production
- Oversight Hub → Vercel production

**File location:** `.github/workflows/deploy-production-with-environments.yml`

---

## 💰 Cost Analysis

### Monthly Cost Breakdown

| Branch    | Workflow        | Frequency    | Duration        | Cost                |
| --------- | --------------- | ------------ | --------------- | ------------------- |
| feat/\*   | NONE            | N/A          | N/A             | 🟢 $0               |
| dev       | test-on-dev.yml | 10/month     | 8 min           | 80 min total        |
| staging   | deploy-staging  | 5/month      | 20 min          | 100 min total       |
| main      | deploy-prod     | 2/month      | 25 min          | 50 min total        |
| **TOTAL** | **3 workflows** | **17/month** | **~18 min avg** | **🟢 $0 (230 min)** |

**GitHub Free Tier:** 2,000 min/month  
**Your Usage:** 230 min/month (11.5% of free tier)  
**Monthly Cost:** **$0 - completely free**

---

## 🔄 Complete Workflow Example

### Step 1: Create Feature Branch (dev works locally)

```bash
git checkout -b feat/add-new-feature

# Make changes...
git add .
git commit -m "feat: add awesome feature"

# Test locally (manually)
npm test

# Commit again
git add .
git commit -m "fix: address test failures"

# Push - NO WORKFLOWS RUN ✅
git push origin feat/add-new-feature

# Push again - NO WORKFLOWS RUN ✅
git add .
git commit -m "refactor: improve code quality"
git push origin feat/add-new-feature
```

**Key: Commit as frequently as you want - nothing triggers!**

---

### Step 2: Merge to Dev (tests run)

```bash
# When ready for CI/CD testing
git checkout dev
git pull origin dev
git merge feat/add-new-feature
git push origin dev

# ✅ Workflow triggers: test-on-dev.yml
# Runs:
#   ✅ Frontend tests (52)
#   ✅ Backend tests (41)
#   ✅ Linting
#   ✅ Build check
# Duration: 8-10 minutes
# Cost: ~10 minutes
```

**Possible outcomes:**

- ✅ All tests pass → Ready for staging
- ❌ Tests fail → Fix and commit to feat/, merge to dev again

---

### Step 3: Merge to Staging (deploy to staging)

```bash
# After dev tests pass, move to staging
git checkout staging
git pull origin staging
git merge dev
git push origin staging

# ✅ Workflow triggers: deploy-staging-with-environments.yml
# Runs:
#   ✅ Frontend tests (52)
#   ✅ Backend tests (41)
#   ✅ Build check
#   ✅ Deploy to Railway staging
#   ✅ Deploy to Vercel staging
# Duration: 15-20 minutes
# Cost: ~20 minutes

# Available at:
#   Strapi: https://strapi-staging.railway.app/admin
#   API: https://agent-staging.railway.app/docs
#   Public: https://public-site-staging.vercel.app
#   Oversight: https://oversight-staging.vercel.app
```

**Verify staging:**

- Test in staging environment
- Check logs and metrics
- Verify all features work

---

### Step 4: Merge to Main (deploy to production)

```bash
# After staging verification, move to production
git checkout main
git pull origin main
git merge staging
git push origin main

# ✅ Workflow triggers: deploy-production-with-environments.yml
# Runs:
#   ✅ Frontend tests (52)
#   ✅ Backend tests (41)
#   ✅ Build check
#   ✅ Security audit
#   ✅ Deploy to Railway production
#   ✅ Deploy to Vercel production
# Duration: 20-25 minutes
# Cost: ~25 minutes

# Available at:
#   Strapi: https://cms.railway.app/admin
#   API: https://api.glad-labs.com
#   Public: https://glad-labs.com
#   Oversight: https://oversight.glad-labs.com
```

**Monitoring:**

- Check production health
- Monitor error rates
- Track performance metrics

---

## ✅ Commit Frequency Example

### Before (Limited by CI/CD)

```
feat/feature → Push 1 → Triggers workflows (3-5 min wait)
           → Push 2 → Triggers workflows (3-5 min wait)
           → Push 3 → Triggers workflows (3-5 min wait)
           → Push 4 → Triggers workflows (3-5 min wait)
           → Push 5 → Triggers workflows (3-5 min wait)
           → Merge to dev → Tests run
```

Total: ~15-25 minute wait before tests run on dev

---

### After (Unlimited on feat branches)

```
feat/feature → Push 1 → 🟢 No workflows
           → Push 2 → 🟢 No workflows
           → Push 3 → 🟢 No workflows
           → Push 4 → 🟢 No workflows
           → Push 5 → 🟢 No workflows
           → Merge to dev → Tests run immediately
```

Total: Instant feedback on dev, no waiting on feature branches

---

## 🔐 GitHub Secrets Required

### For Staging Deployment

```
RAILWAY_TOKEN
RAILWAY_STAGING_PROJECT_ID
STRAPI_STAGING_DB_HOST
STRAPI_STAGING_DB_USER
STRAPI_STAGING_DB_PASSWORD
STRAPI_STAGING_ADMIN_PASSWORD
STRAPI_STAGING_JWT_SECRET
STRAPI_STAGING_API_TOKEN
COFOUNDER_STAGING_OPENAI_API_KEY
COFOUNDER_STAGING_ANTHROPIC_API_KEY
COFOUNDER_STAGING_REDIS_HOST
COFOUNDER_STAGING_REDIS_PASSWORD
COFOUNDER_STAGING_MCP_SERVER_TOKEN
COFOUNDER_STAGING_SENTRY_DSN
VERCEL_TOKEN
PUBLIC_SITE_STAGING_PROJECT_ID
OVERSIGHT_STAGING_PROJECT_ID
VERCEL_ORG_ID
```

### For Production Deployment

```
RAILWAY_TOKEN
RAILWAY_PROD_PROJECT_ID
STRAPI_PROD_DB_HOST
STRAPI_PROD_DB_USER
STRAPI_PROD_DB_PASSWORD
STRAPI_PROD_ADMIN_PASSWORD
STRAPI_PROD_JWT_SECRET
STRAPI_PROD_API_TOKEN
COFOUNDER_PROD_OPENAI_API_KEY
COFOUNDER_PROD_ANTHROPIC_API_KEY
COFOUNDER_PROD_REDIS_HOST
COFOUNDER_PROD_REDIS_PASSWORD
COFOUNDER_PROD_MCP_SERVER_TOKEN
COFOUNDER_PROD_SENTRY_DSN
VERCEL_TOKEN
PUBLIC_SITE_PROD_PROJECT_ID
OVERSIGHT_PROD_PROJECT_ID
VERCEL_ORG_ID
```

---

## 📝 Quick Reference

### Testing Locally (Before pushing)

```bash
npm test                    # All tests (frontend + backend)
npm run test:frontend:ci    # Frontend only
npm run test:python         # Backend only
npm run lint:fix            # Fix linting issues
npm run build               # Build check
```

### Pushing Code (Use freely)

```bash
git push origin feat/my-feature    # No workflows ✅
git push origin feat/my-feature    # No workflows ✅
git push origin feat/my-feature    # No workflows ✅
```

### Merging to Dev (Tests run)

```bash
git checkout dev
git merge feat/my-feature
git push origin dev    # Tests run automatically
```

### Merging to Staging (Deploy to staging)

```bash
git checkout staging
git merge dev
git push origin staging    # Tests + deploy to staging
```

### Merging to Main (Deploy to production)

```bash
git checkout main
git merge staging
git push origin main    # Tests + security + deploy to production
```

---

## 🎯 Summary

| Item                         | Before             | After                    |
| ---------------------------- | ------------------ | ------------------------ |
| **Commits on feat branches** | Triggered CI/CD    | ✅ No workflows          |
| **Testing on feat branches** | ~80 min/month      | ✅ $0 (local only)       |
| **Testing on dev**           | Frontend only      | ✅ Frontend + Backend    |
| **Cost for 10x commits**     | ~800 min           | ✅ Still free            |
| **Commit frequency penalty** | High               | ✅ Zero                  |
| **Total monthly cost**       | ~120 min (free)    | ✅ ~230 min (still free) |
| **Fastest feedback**         | After merge to dev | ✅ Same (no regression)  |

---

## ✨ Benefits of This Setup

✅ **Commit Freely** - No CI/CD overhead on feature branches  
✅ **Safety Net** - Full tests before staging deployment  
✅ **Security** - Full security audit before production  
✅ **Cost Effective** - Still completely free (11.5% of free tier)  
✅ **Clear Gates** - Each branch has specific purpose  
✅ **Fast Feedback** - Tests run on merge to dev, not on every commit  
✅ **Developer Friendly** - No waiting for workflows on feature branches  
✅ **Production Ready** - Multiple validation gates before production

---

## 🚀 You're All Set!

Your new branch hierarchy is ready:

```
✅ feat/*     → No workflows (commit frequently)
✅ dev        → Full testing before staging
✅ staging    → Deploy to staging environment
✅ main       → Deploy to production
```

**Next steps:**

1. Configure GitHub Secrets (see above)
2. Start using the new workflow (feat → dev → staging → main)
3. Enjoy unlimited commits on feature branches! 🎉

---

**Questions?** Check `.github/workflows/` directory for all workflow files.
