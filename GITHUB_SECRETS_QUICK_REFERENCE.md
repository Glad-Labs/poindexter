# GitHub Secrets - Quick Reference Card

**Status:** ✅ Production Ready  
**Created:** October 24, 2025

---

## ⚡ The Answer to Your Question

**Q:** Can I set up GitHub secrets by component (Strapi, Co-founder, Public Site, Oversight Hub) and environment (staging, production)?

**A:** ✅ **YES!** Use GitHub Environments. GitHub Actions will automatically recognize and provide the correct variables.

---

## 🎯 Setup Pattern

### In GitHub (Settings → Environments)

```
Create: staging environment
├─ Deployment branch: dev
├─ STRAPI_STAGING_* (7 secrets)
├─ COFOUNDER_STAGING_* (9 secrets)
├─ PUBLIC_SITE_STAGING_* (6 secrets)
└─ OVERSIGHT_STAGING_* (5 secrets)

Create: production environment
├─ Deployment branch: main
├─ STRAPI_PROD_* (7 secrets)
├─ COFOUNDER_PROD_* (9 secrets)
├─ PUBLIC_SITE_PROD_* (6 secrets)
└─ OVERSIGHT_PROD_* (5 secrets)
```

### In Your Workflow

```yaml
jobs:
  deploy:
    environment: staging  # 👈 GitHub auto-loads staging secrets
    runs-on: ubuntu-latest
    steps:
      - name: Deploy Strapi
        env:
          DB_PASSWORD: ${{ secrets.STRAPI_STAGING_DB_PASSWORD }}
          API_KEY: ${{ secrets.STRAPI_STAGING_API_TOKEN }}
        run: npm run deploy
```

---

## 📊 Component Secret Names

### Strapi CMS (7 secrets per environment)

```
STRAPI_STAGING_DB_HOST
STRAPI_STAGING_DB_USER
STRAPI_STAGING_DB_PASSWORD
STRAPI_STAGING_ADMIN_PASSWORD
STRAPI_STAGING_JWT_SECRET
STRAPI_STAGING_API_TOKEN
STRAPI_STAGING_TRANSFER_TOKEN
```

### Co-Founder Agent (9 secrets per environment)

```
COFOUNDER_STAGING_OPENAI_API_KEY
COFOUNDER_STAGING_ANTHROPIC_API_KEY
COFOUNDER_STAGING_REDIS_HOST
COFOUNDER_STAGING_REDIS_PORT
COFOUNDER_STAGING_REDIS_PASSWORD
COFOUNDER_STAGING_MEMORY_DB_URL
COFOUNDER_STAGING_MCP_SERVER_TOKEN
COFOUNDER_STAGING_LOG_LEVEL
COFOUNDER_STAGING_SENTRY_DSN
```

### Public Site (6 secrets per environment)

```
PUBLIC_SITE_STAGING_STRAPI_URL
PUBLIC_SITE_STAGING_COFOUNDER_URL
PUBLIC_SITE_STAGING_GA_ID
PUBLIC_SITE_STAGING_POSTHOG_KEY
PUBLIC_SITE_STAGING_SENTRY_DSN
PUBLIC_SITE_STAGING_VERCEL_TOKEN
```

### Oversight Hub (5 secrets per environment)

```
OVERSIGHT_STAGING_STRAPI_URL
OVERSIGHT_STAGING_COFOUNDER_URL
OVERSIGHT_STAGING_AUTH_SECRET
OVERSIGHT_STAGING_SENTRY_DSN
OVERSIGHT_STAGING_VERCEL_TOKEN
```

### Shared Secrets (Repository Level - NOT in environments)

```
RAILWAY_TOKEN          # For Railway deployments
VERCEL_TOKEN          # For Vercel deployments
GCP_PROJECT_ID        # For Cloud Functions
GCP_SERVICE_ACCOUNT   # For GCP authentication
```

---

## 🔄 How It Works

### Step 1: Push Code
```bash
git push origin dev
```

### Step 2: GitHub Actions Triggers
```yaml
on:
  push:
    branches: [dev]  # 👈 Matches staging environment deployment branch
```

### Step 3: Workflow Uses Environment
```yaml
jobs:
  deploy:
    environment: staging  # 👈 GitHub loads staging secrets
```

### Step 4: Secrets Automatically Available
```yaml
- name: Deploy
  env:
    DB_PASSWORD: ${{ secrets.STRAPI_STAGING_DB_PASSWORD }}  # ✅ Works!
  run: npm run deploy
```

---

## ✅ Verification Checklist

| Item | Status |
|---|---|
| GitHub Environments created (staging, production) | ☐ |
| Branch rules set (dev→staging, main→production) | ☐ |
| All 76 component secrets added (38 per environment) | ☐ |
| 4 shared repository secrets added | ☐ |
| Workflows have `environment: staging/production` line | ☐ |
| Staging deployment tested on dev branch | ☐ |
| Production deployment tested on main branch | ☐ |
| Secrets properly masked in logs | ☐ |
| No production secrets accessible from staging | ☐ |

---

## 🚀 Automatic Workflow

```
┌──────────────────────────────────────────┐
│  Push to dev branch                      │
└──────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────┐
│  Workflow triggered                      │
│  environment: staging                    │
└──────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────┐
│  GitHub loads staging environment        │
│  ✅ STRAPI_STAGING_* available           │
│  ✅ COFOUNDER_STAGING_* available        │
│  ✅ PUBLIC_SITE_STAGING_* available      │
│  ✅ OVERSIGHT_STAGING_* available        │
│  ✅ Shared secrets available             │
└──────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────┐
│  Secrets injected into workflow env      │
│  ${{ secrets.SECRET_NAME }} → value      │
└──────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────┐
│  Deployment runs with correct secrets    │
│  ✅ Staging components deployed          │
└──────────────────────────────────────────┘
```

---

## 🔐 Security Features

| Feature | Benefit |
|---|---|
| **Environment Isolation** | Staging & production secrets completely separate |
| **Branch Enforcement** | Staging secrets only on dev, production only on main |
| **Auto-Masking** | Secrets automatically hidden in workflow logs |
| **Manual Approval** | Can require approval for production deployments |
| **Audit Trail** | GitHub records who approved deployments |

---

## 💡 Pro Tips

### Tip 1: Organization by Component
Use consistent naming: `{COMPONENT}_{ENVIRONMENT}_{SECRET}`
- ✅ `STRAPI_STAGING_DB_HOST`
- ✅ `COFOUNDER_PROD_API_KEY`
- ❌ `DB_HOST_STAGING_STRAPI` (confusing order)

### Tip 2: Environment-Specific URLs
Staging and production have different deployment targets:
- Staging: `https://staging-cms.railway.app`
- Production: `https://cms.railway.app`

### Tip 3: Use Shared Secrets for Common Tools
- `RAILWAY_TOKEN` - used by ALL Railway deployments
- `VERCEL_TOKEN` - used by ALL Vercel deployments
- These don't change by environment, so repository-level is fine

### Tip 4: Rotate Secrets Periodically
- Database passwords: quarterly
- API keys: semi-annually
- JWT secrets: annually or after staff changes

---

## 🆘 Common Issues

### Issue: Secret not found

**Symptoms:** `Error: Secret STRAPI_STAGING_DB_HOST not found`

**Solution:**
1. Check environment name matches: `environment: staging`
2. Check secret exists in GitHub Settings
3. Check secret name spelling (case-sensitive)

### Issue: Wrong environment's secrets used

**Symptoms:** Staging workflow uses production secrets

**Solution:**
1. Verify `environment:` line in workflow
2. Verify branch triggers correct environment (dev→staging, main→production)
3. Check GitHub Settings → Environments → deployment branches

### Issue: Secret visible in logs

**Symptoms:** Actual secret value appears in workflow logs

**Solution:**
GitHub automatically masks known secrets. If custom logging:
```yaml
- run: |
    # ❌ Wrong - exposes secret
    echo "Password is: ${{ secrets.MY_SECRET }}"
    
    # ✅ Correct - GitHub masks it
    export PASSWORD=${{ secrets.MY_SECRET }}
    npm run deploy
```

---

## 📋 Implementation Timeline

| Phase | Time | Action |
|---|---|---|
| **1: Setup** | 5 min | Create environments in GitHub Settings |
| **2: Add Secrets** | 30 min | Add all 79 secrets (38 staging + 38 prod + 3 shared) |
| **3: Update Workflows** | 10 min | Add `environment:` line to workflows |
| **4: Test Staging** | 10 min | Push to dev → verify staging deployment |
| **5: Test Production** | 10 min | Push to main → verify production deployment |
| **6: Verify Security** | 5 min | Check logs are masked, approval gates work |
| **Total** | ~70 min | Complete implementation |

---

## 📚 Related Docs

| Document | Purpose |
|---|---|
| `GITHUB_SECRETS_SETUP.md` | Complete reference guide (40+ sections) |
| `GITHUB_SECRETS_QUICK_SETUP.md` | 5-minute quick start |
| `.github/workflows/deploy-staging-with-environments.yml` | Staging workflow example |
| `.github/workflows/deploy-production-with-environments.yml` | Production workflow example |
| `GITHUB_SECRETS_IMPLEMENTATION_SUMMARY.md` | Setup summary & checklist |

---

## 🎯 Bottom Line

✅ GitHub Environments automatically handle secrets by component and environment  
✅ Specify `environment: staging` or `environment: production` in workflow  
✅ GitHub injects correct secrets based on branch  
✅ Staging and production completely isolated  
✅ Zero human error in secret selection  
✅ Full audit trail of approvals  

**Start here:** `GITHUB_SECRETS_QUICK_SETUP.md`

---

**Last Updated:** October 24, 2025 | **Status:** Production Ready ✅
