# 🔐 Complete GitHub Secrets Configuration Guide

**Status:** ✅ Authoritative Reference  
**Last Updated:** October 25, 2025  
**Purpose:** Single source of truth for GitHub Secrets setup across all environments

---

## 📋 Quick Overview

**Three Environments = Three Secret Groups:**

```
LOCAL DEVELOPMENT (feat/*)
  └─ Uses: .env file (NEVER committed)
  └─ Secrets: None (local only)

STAGING (dev branch)
  └─ Uses: .env.staging (committed, no secrets)
  └─ Secrets: GitHub Secrets (STAGING_*)

PRODUCTION (main branch)
  └─ Uses: .env.production (committed, no secrets)
  └─ Secrets: GitHub Secrets (PROD_*)
```

---

## 🔐 Complete GitHub Secrets Setup

### Location

`GitHub → Settings → Secrets and variables → Actions`

### Staging Environment Secrets

| Secret Name                  | Required    | Example Value                     | Purpose                                 |
| ---------------------------- | ----------- | --------------------------------- | --------------------------------------- |
| `STAGING_STRAPI_URL`         | ✅ Yes      | `https://staging-cms.railway.app` | Strapi endpoint for staging             |
| `STAGING_STRAPI_TOKEN`       | ✅ Yes      | API token from Strapi admin       | Strapi API authentication               |
| `RAILWAY_STAGING_PROJECT_ID` | ✅ Yes      | Project ID from Railway dashboard | Identify staging Railway project        |
| `STAGING_DATABASE_URL`       | ⚠️ Optional | `postgresql://user:pass@host/db`  | Override default staging DB (if needed) |

**Staging Secret Count:** 3-4 secrets

### Production Environment Secrets

| Secret Name               | Required    | Example Value                           | Purpose                                    |
| ------------------------- | ----------- | --------------------------------------- | ------------------------------------------ |
| `PROD_STRAPI_URL`         | ✅ Yes      | `https://cms.railway.app`               | Strapi endpoint for production             |
| `PROD_STRAPI_TOKEN`       | ✅ Yes      | API token from Strapi admin             | Strapi API authentication                  |
| `RAILWAY_TOKEN`           | ✅ Yes      | Railway CLI token                       | Authentication for Railway deployment      |
| `RAILWAY_PROD_PROJECT_ID` | ✅ Yes      | Project ID from Railway dashboard       | Identify production Railway project        |
| `VERCEL_TOKEN`            | ✅ Yes      | Token from Vercel account settings      | Vercel deployment authentication           |
| `VERCEL_PROJECT_ID`       | ✅ Yes      | Project ID from Vercel project settings | Identify Vercel project                    |
| `VERCEL_ORG_ID`           | ✅ Yes      | Team/Organization ID from Vercel        | Vercel organization context                |
| `PROD_DATABASE_URL`       | ⚠️ Optional | `postgresql://user:pass@host/db`        | Override default production DB (if needed) |

**Production Secret Count:** 7-8 secrets

### Total Secrets Required

- **Minimum (without optional DB overrides):** 10 secrets (3 staging + 7 production)
- **Complete (with DB overrides):** 12 secrets (4 staging + 8 production)

---

## 📝 How to Create Each Secret

### 1. STRAPI Tokens

**Where to find:**

1. Go to Strapi Admin: `https://your-strapi-instance/admin`
2. Settings → API Tokens → Create new API Token
3. Select "Full access" (for development) or "Custom" (for production)
4. Copy the generated token

**Save as:**

- Staging: `STAGING_STRAPI_TOKEN`
- Production: `PROD_STRAPI_TOKEN`

### 2. Railway Secrets

**For Railway Token:**

1. Go to Railway: `https://railway.app`
2. Account Settings → API Tokens
3. Create new token
4. Copy and save as: `RAILWAY_TOKEN`

**For Project IDs:**

1. Open each Railway project
2. Settings → Project → Project ID (copy it)
3. Save as: `RAILWAY_STAGING_PROJECT_ID` or `RAILWAY_PROD_PROJECT_ID`

### 3. Vercel Secrets

**For Vercel Token:**

1. Go to Vercel: `https://vercel.com`
2. Settings → Tokens → Create Token
3. Copy and save as: `VERCEL_TOKEN`

**For Project ID:**

1. Open project in Vercel dashboard
2. Settings → General → Project ID (copy it)
3. Save as: `VERCEL_PROJECT_ID`

**For Organization ID:**

1. Settings → Teams (if using team) or Personal
2. Copy the ID and save as: `VERCEL_ORG_ID`

### 4. API Endpoints

**For STAGING_STRAPI_URL:**

- Usually: `https://your-staging-cms.railway.app`
- From: Railway project settings

**For PROD_STRAPI_URL:**

- Usually: `https://your-prod-cms.railway.app` or custom domain
- From: Railway project settings

### 5. Database URLs (Optional)

**For STAGING_DATABASE_URL:**

- Format: `postgresql://username:password@host:port/database_name`
- From: Railway PostgreSQL service → Connect tab
- Only needed if using separate staging database

**For PROD_DATABASE_URL:**

- Format: `postgresql://username:password@host:port/database_name`
- From: Railway PostgreSQL service → Connect tab
- Only needed if using separate production database

---

## ✅ How to Add Secrets to GitHub

### Step-by-Step

1. **Open Repository Settings**
   - Go to: `https://github.com/your-username/glad-labs-website`
   - Click: **Settings**
   - Left sidebar: **Secrets and variables** → **Actions**

2. **Add Each Secret**
   - Click: **New repository secret**
   - **Name:** Exact name from table above (e.g., `STAGING_STRAPI_TOKEN`)
   - **Value:** Paste the actual value (e.g., API token)
   - Click: **Add secret**

3. **Repeat for All Secrets**
   - Add all staging secrets (3-4)
   - Add all production secrets (7-8)
   - Total: 10-12 secrets

4. **Verify**
   - All secrets should appear in the list
   - Values are hidden (shown as `●●●●●`)

---

## 🔄 How GitHub Actions Accesses Secrets

### In Workflow Files

**Example from deploy-staging.yml:**

```yaml
- name: Deploy to Railway (staging)
  env:
    RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
    STAGING_STRAPI_TOKEN: ${{ secrets.STAGING_STRAPI_TOKEN }}
  run: |
    npm run deploy:staging
```

**Syntax:** `${{ secrets.SECRET_NAME }}`

### Security Best Practices

1. ✅ **Secrets are only accessible during workflow runs**
2. ✅ **Secrets are masked in workflow logs** (shown as `***`)
3. ✅ **Secrets cannot be accessed locally** (only in GitHub Actions)
4. ✅ **Only repository maintainers** can add/view secret names (not values)
5. ✅ **Each environment has separate secrets** (staging doesn't access prod secrets)

---

## 📊 Environment Variable Mapping

### How Secrets Flow to Environment Files

```
GitHub Secrets (🔐 hidden)
       ↓
GitHub Actions Workflow
       ↓
.env.staging / .env.production (visible in repo)
       ↓
Railway / Vercel Services
       ↓
Application Runtime
```

### Example Flow for Staging

1. **GitHub Secret:** `STAGING_STRAPI_URL = https://staging-cms.railway.app`
2. **Workflow reads:** `${{ secrets.STAGING_STRAPI_URL }}`
3. **Exports to .env:** `NEXT_PUBLIC_STRAPI_API_URL=https://staging-cms.railway.app`
4. **Next.js reads:** `process.env.NEXT_PUBLIC_STRAPI_API_URL`
5. **Frontend uses:** API calls to staging endpoint

---

## 🔍 Verification Checklist

After adding all secrets, verify:

- [ ] All 10-12 secrets are listed in GitHub
- [ ] Each secret has correct name (case-sensitive)
- [ ] No typos in secret names
- [ ] Values are not empty or incorrect format
- [ ] Staging and production secrets are separate
- [ ] `.env.staging` and `.env.production` files use `<token-stored-in-GitHub-secrets>` placeholders
- [ ] Workflows can access secrets via `${{ secrets.SECRET_NAME }}`

---

## 🚀 Testing Secrets Configuration

### Local Test

```bash
# Verify .env files have correct structure
cat .env.staging
cat .env.production

# Should see placeholders like:
# STRAPI_API_TOKEN=<token-stored-in-GitHub-secrets>
```

### GitHub Actions Test

Create a test workflow to verify secrets:

```yaml
name: Verify Secrets

on: push

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - name: Check if secrets exist
        run: |
          if [ -z "${{ secrets.STAGING_STRAPI_TOKEN }}" ]; then
            echo "❌ STAGING_STRAPI_TOKEN not set"
            exit 1
          fi
          echo "✅ All required secrets are configured"
```

---

## ⚠️ Common Mistakes to Avoid

| Mistake                    | Issue                   | Solution                              |
| -------------------------- | ----------------------- | ------------------------------------- |
| Committing `.env` file     | Exposes secrets in git  | Keep `.env` in `.gitignore`           |
| Wrong secret name          | Workflow fails silently | Use exact names from table            |
| Expired tokens             | Deployments fail        | Regenerate and update annually        |
| Sharing secrets            | Security breach         | Never paste secrets in chat/docs      |
| Database URL format        | Connection fails        | Use full PostgreSQL connection string |
| Railway token scoped wrong | Can't deploy            | Regenerate with full permissions      |

---

## 🔐 Secret Rotation Policy

**Recommended Schedule:**

- **API Tokens:** Rotate every 90 days
- **Database Passwords:** Rotate every 6 months
- **After team member departure:** Immediately rotate all secrets

**How to Rotate:**

1. Generate new token from provider (Strapi, Railway, Vercel)
2. Update GitHub Secret with new value
3. Trigger a deployment to verify
4. Delete old token from provider
5. Document rotation date

---

## 📞 Troubleshooting

### Workflow Can't Access Secret

**Problem:** `Error: secret not found`

**Solutions:**

1. Verify secret name is exactly correct (case-sensitive)
2. Check secret is in correct repository (not organization)
3. Ensure workflow is in `.github/workflows/` directory
4. Wait 1-2 minutes after adding secret

### Deployment Fails with 401/403

**Problem:** Authentication error

**Solutions:**

1. Verify API token hasn't expired
2. Check token has correct permissions
3. Regenerate token and update secret
4. Verify endpoint URL in secret is correct

### Secret Not Masked in Logs

**Problem:** Actual secret value appears in logs

**Solutions:**

1. GitHub auto-masks common formats (tokens, keys)
2. If custom format, wrap output: `echo "::add-mask::$VALUE"`
3. Review GitHub Actions security documentation

---

## 📚 Related Documentation

- **[03-DEPLOYMENT_AND_INFRASTRUCTURE.md](./docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** - Deployment overview
- **[07-BRANCH_SPECIFIC_VARIABLES.md](./docs/07-BRANCH_SPECIFIC_VARIABLES.md)** - Environment files
- **[GitHub Secrets Documentation](https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions)**
- **[Railway Documentation](https://docs.railway.app)**
- **[Vercel Documentation](https://vercel.com/docs)**

---

## ✅ Setup Complete Checklist

- [ ] Created all 10-12 GitHub Secrets
- [ ] Verified secret names are case-sensitive
- [ ] Confirmed all values are valid tokens/URLs
- [ ] `.env` file is in `.gitignore`
- [ ] `.env.staging` uses `<token-stored-in-GitHub-secrets>` placeholders
- [ ] `.env.production` uses `<token-stored-in-GitHub-secrets>` placeholders
- [ ] Workflows reference secrets with `${{ secrets.NAME }}`
- [ ] Test workflow confirms secrets are accessible
- [ ] Team members understand secret access policy

---

**🎯 Status: Ready for Deployment**

Once all secrets are configured correctly, your GitHub Actions workflows can:

1. ✅ Access secure values safely
2. ✅ Deploy to staging and production
3. ✅ Authenticate with external services
4. ✅ Never expose secrets in logs or version control

---

**Last Verified:** October 25, 2025  
**Maintained By:** Glad Labs Development Team  
**Questions?** Check GitHub Actions documentation or Railway/Vercel support
