# 🔐 Complete GitHub Secrets Configuration Guide

**Last Updated:** November 4, 2025  
**Version:** 1.0  
**Status:** ✅ For Production Deployment  
**Audience:** DevOps, SREs, Deployment Engineers

---

## Quick Navigation

- **[Secrets Inventory](#secrets-inventory)** - All secrets at a glance
- **[Setup Instructions](#setup-instructions)** - Step-by-step GitHub setup
- **[By Platform](#secrets-by-platform)** - Organized by where to get them
- **[CI/CD Integration](#cicd-integration)** - How workflows use them
- **[Rotation & Maintenance](#rotation--maintenance)** - Best practices
- **[Troubleshooting](#troubleshooting)** - Common issues

---

## 📋 Secrets Inventory

### Complete List of All GitHub Secrets (18 total)

| #   | Secret Name                     | Purpose                 | Required?   | Sensitivity |
| --- | ------------------------------- | ----------------------- | ----------- | ----------- |
| 1   | `RAILWAY_TOKEN`                 | Deploy to Railway       | ✅ YES      | 🔴 CRITICAL |
| 2   | `RAILWAY_STAGING_PROJECT_ID`    | Staging environment     | ✅ YES      | 🟠 HIGH     |
| 3   | `RAILWAY_PROD_PROJECT_ID`       | Production environment  | ✅ YES      | 🔴 CRITICAL |
| 4   | `STRAPI_STAGING_DB_HOST`        | Staging database        | ✅ YES      | 🟠 HIGH     |
| 5   | `STRAPI_STAGING_DB_USER`        | Staging DB user         | ✅ YES      | 🟠 HIGH     |
| 6   | `STRAPI_STAGING_DB_PASSWORD`    | Staging DB password     | ✅ YES      | 🔴 CRITICAL |
| 7   | `STRAPI_STAGING_ADMIN_PASSWORD` | Strapi staging admin    | ✅ YES      | 🟠 HIGH     |
| 8   | `STRAPI_STAGING_JWT_SECRET`     | Staging JWT key         | ✅ YES      | 🔴 CRITICAL |
| 9   | `STRAPI_PROD_DB_HOST`           | Production database     | ✅ YES      | 🟠 HIGH     |
| 10  | `STRAPI_PROD_DB_USER`           | Production DB user      | ✅ YES      | 🟠 HIGH     |
| 11  | `STRAPI_PROD_DB_PASSWORD`       | Production DB password  | ✅ YES      | 🔴 CRITICAL |
| 12  | `STRAPI_PROD_ADMIN_PASSWORD`    | Strapi production admin | ✅ YES      | 🔴 CRITICAL |
| 13  | `STRAPI_PROD_JWT_SECRET`        | Production JWT key      | ✅ YES      | 🔴 CRITICAL |
| 14  | `OPENAI_API_KEY`                | OpenAI API access       | ✅ YES\*    | 🔴 CRITICAL |
| 15  | `ANTHROPIC_API_KEY`             | Claude API access       | ⚠️ Optional | 🔴 CRITICAL |
| 16  | `GOOGLE_API_KEY`                | Gemini API access       | ⚠️ Optional | 🔴 CRITICAL |
| 17  | `VERCEL_TOKEN`                  | Deploy to Vercel        | ✅ YES      | 🔴 CRITICAL |
| 18  | `VERCEL_PROJECT_ID`             | Vercel project ID       | ✅ YES      | 🟠 HIGH     |

\*Choose at least one AI provider (OpenAI, Anthropic, or Google)

---

## 🚀 Setup Instructions

### Step 1: Access GitHub Secrets

1. Go to **GitHub Repository**
2. Click **Settings** (top navigation)
3. Go to **Secrets and variables** → **Actions**
4. Click **New repository secret**

### Step 2: Add Each Secret

Repeat this process for each secret:

1. Enter **Name** (exactly as shown above)
2. Enter **Value** (from instructions below)
3. Click **Add secret**

### Step 3: Verify All Secrets Are Present

```bash
# Check GitHub CLI (if installed)
gh secret list

# Expected output: All 18 secrets listed
```

---

## 🔑 Secrets by Platform

### Railway Secrets

#### 1. `RAILWAY_TOKEN`

**Where to Get:**

1. Go to https://railway.app/account/tokens
2. Click "Create token"
3. Name it: "GitHub Actions Deployment"
4. Copy the token (starts with `$2a$`)

**Example:** `$2a$10$xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

**Usage:** All Railway CLI commands in GitHub Actions

---

#### 2. `RAILWAY_STAGING_PROJECT_ID`

**Where to Get:**

1. Go to https://railway.app/dashboard
2. Click your **staging project** (e.g., "Glad Labs Staging")
3. Click **Settings** (top)
4. Copy **Project ID** (visible in URL or settings)

**Example:** `abcd1234-ef56-7890-ghij-klmnopqrstuv`

**Usage:** Deploy to staging environment

---

#### 3. `RAILWAY_PROD_PROJECT_ID`

**Where to Get:**

1. Go to https://railway.app/dashboard
2. Click your **production project** (e.g., "Glad Labs Production")
3. Click **Settings** (top)
4. Copy **Project ID**

**Example:** `xyz9876-ab54-3210-cdef-ghijklmnopqr`

**Usage:** Deploy to production environment

---

### Database Secrets (Railway Postgres)

#### 4-6. Database Staging Secrets

**Where to Get:**

1. Go to https://railway.app/dashboard
2. Click **PostgreSQL** service in staging project
3. Click **Connect** tab
4. Copy these from the connection string:

   **`postgresql://USER:PASSWORD@HOST:5432/DATABASE`**
   - `STRAPI_STAGING_DB_HOST` = `HOST` part
   - `STRAPI_STAGING_DB_USER` = `USER` part
   - `STRAPI_STAGING_DB_PASSWORD` = `PASSWORD` part

**Example Values:**

```
STRAPI_STAGING_DB_HOST = staging-db.b12345.postgres.railway.app
STRAPI_STAGING_DB_USER = postgres
STRAPI_STAGING_DB_PASSWORD = AbcDef1234GhIjkL5678MnOp
```

**Usage:** Strapi CMS database connection during staging deployment

---

#### 9-11. Database Production Secrets

Same process as above, but for **production PostgreSQL** service:

**Example Values:**

```
STRAPI_PROD_DB_HOST = prod-db.c98765.postgres.railway.app
STRAPI_PROD_DB_USER = postgres
STRAPI_PROD_DB_PASSWORD = XyZ9876AbCdEf1234GhIjkL567
```

**Usage:** Strapi CMS database connection during production deployment

---

### Strapi Admin Secrets

#### 7 & 12. `STRAPI_STAGING_ADMIN_PASSWORD` and `STRAPI_PROD_ADMIN_PASSWORD`

**Where to Get / Generate:**

Generate a strong password:

```powershell
# PowerShell command to generate secure password
$bytes = [System.BitConverter]::GetBytes($(Get-Random))
$password = [System.Convert]::ToBase64String($bytes).Substring(0, 20)
Write-Host $password
```

Or use an online generator: https://www.random.org/passwords/

**Requirements:**

- Minimum 12 characters
- Mix of uppercase, lowercase, numbers, symbols
- No quotes or special shell characters

**Example:** `SecurePass123!@#Abc`

**Usage:** Strapi admin login credentials (change after first deployment)

---

#### 8 & 13. `STRAPI_STAGING_JWT_SECRET` and `STRAPI_PROD_JWT_SECRET`

**Where to Get / Generate:**

Generate a long random string:

```powershell
# PowerShell (on Windows)
-join ((33..126) | Get-Random -Count 64 | ForEach-Object {[char]$_})

# Or use online tool:
# https://tools.ietf.org/html/rfc4648 → Base64 encode random bytes
```

**Requirements:**

- 32+ characters
- Random string (cryptographically secure)
- Never use simple patterns

**Example:** `aB3cD4eF5gH6iJ7kL8mN9oPq0rStUvWxYz1A2B3C4D5E6F7G8H9I0J`

**Usage:** JWT token signing for Strapi authentication

---

### AI Model Provider Secrets

#### 14. `OPENAI_API_KEY`

**Where to Get:**

1. Go to https://platform.openai.com/api-keys
2. Click "Create new secret key"
3. Name: "Glad Labs GitHub Actions"
4. Copy the key (starts with `sk-`)

**Format:** `sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

**⚠️ IMPORTANT:** This key will be visible only once - copy immediately!

**Usage:** FastAPI backend for GPT-4 model access

---

#### 15. `ANTHROPIC_API_KEY` (Optional)

**Where to Get:**

1. Go to https://console.anthropic.com/account/keys
2. Click "Create Key"
3. Name: "Glad Labs GitHub Actions"
4. Copy the key (starts with `sk-ant-`)

**Format:** `sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

**Usage:** FastAPI backend for Claude model access (fallback provider)

**Note:** Only required if using Anthropic. If only using OpenAI, this can be skipped.

---

#### 16. `GOOGLE_API_KEY` (Optional)

**Where to Get:**

1. Go to https://makersuite.google.com/app/apikey
2. Click "Create API Key"
3. Copy the key

**Format:** `AIza_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

**Usage:** FastAPI backend for Gemini model access (final fallback)

**Note:** Free tier available, unlimited requests within quota.

---

### Vercel Secrets

#### 17. `VERCEL_TOKEN`

**Where to Get:**

1. Go to https://vercel.com/account/tokens
2. Click "Create" (next to "Tokens")
3. Name: "GitHub Actions"
4. Select **All Projects** or specific projects
5. Copy the token (starts with `vercel_`)

**Format:** `vercel_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

**⚠️ IMPORTANT:** This token will be visible only once - copy immediately!

**Usage:** Deploy frontend to Vercel

---

#### 18. `VERCEL_PROJECT_ID`

**Where to Get:**

1. Go to https://vercel.com/dashboard
2. Click your frontend project (oversight-hub)
3. Click **Settings** → **General**
4. Copy **Project ID** (under "Project ID" label)

**Example:** `prj_abcdef1234567890abcdef1234567890`

**⚠️ NOTE:** This is NOT sensitive - can be in documentation

**Usage:** Identify which Vercel project to deploy to

---

## 🔄 CI/CD Integration

### How GitHub Actions Uses These Secrets

#### Staging Deployment (`.github/workflows/deploy-staging-with-environments.yml`)

```yaml
# When you push to 'dev' branch:
- name: Deploy Strapi to Railway Staging
  env:
    RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
    RAILWAY_PROJECT_ID: ${{ secrets.RAILWAY_STAGING_PROJECT_ID }}
    DATABASE_HOST: ${{ secrets.STRAPI_STAGING_DB_HOST }}
    DATABASE_PASSWORD: ${{ secrets.STRAPI_STAGING_DB_PASSWORD }}
    STRAPI_ADMIN_PASSWORD: ${{ secrets.STRAPI_STAGING_ADMIN_PASSWORD }}
```

#### Production Deployment (`.github/workflows/deploy-production-with-environments.yml`)

```yaml
# When you push to 'main' branch:
- name: Deploy Strapi to Railway Production
  env:
    RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
    RAILWAY_PROJECT_ID: ${{ secrets.RAILWAY_PROD_PROJECT_ID }}
    DATABASE_HOST: ${{ secrets.STRAPI_PROD_DB_HOST }}
    DATABASE_PASSWORD: ${{ secrets.STRAPI_PROD_DB_PASSWORD }}
    STRAPI_ADMIN_PASSWORD: ${{ secrets.STRAPI_PROD_ADMIN_PASSWORD }}
```

#### Frontend Deployment

```yaml
# Vercel deployment happens separately
- name: Deploy Frontend to Vercel
  uses: vercel/action@v5
  with:
    token: ${{ secrets.VERCEL_TOKEN }}
    projectId: ${{ secrets.VERCEL_PROJECT_ID }}
```

---

## 🔒 Security Best Practices

### ✅ DO:

- ✅ Use strong, randomly generated secrets (32+ chars)
- ✅ Rotate secrets every 90 days
- ✅ Use separate secrets for staging vs production
- ✅ Restrict token scope in platforms (Railway, Vercel, OpenAI)
- ✅ Enable 2FA on all platform accounts
- ✅ Keep secrets in GitHub only (never in code)
- ✅ Audit access to secrets regularly
- ✅ Document secret rotation dates

### ❌ DON'T:

- ❌ Commit secrets to Git (use `.gitignore`)
- ❌ Use simple/predictable secrets
- ❌ Share secrets via email, chat, or Slack
- ❌ Reuse secrets across environments
- ❌ Give admin access for single-use deployments
- ❌ Store secrets in `.env` files in Git
- ❌ Log secrets to console output

---

## 🔄 Rotation & Maintenance

### Secret Rotation Schedule

| Secret                               | Rotation Frequency  | Method                                |
| ------------------------------------ | ------------------- | ------------------------------------- |
| API Keys (OpenAI, Anthropic, Google) | 90 days             | Regenerate in platform, update secret |
| Database Passwords                   | 180 days            | Change in Railway, update secret      |
| JWT Secrets                          | Annual or on breach | Generate new, update all services     |
| Tokens (Railway, Vercel)             | 180 days            | Regenerate in platform, update secret |

### Rotation Procedure

1. **Generate New Secret** (in the platform)
2. **Test New Secret** (locally in `.env`)
3. **Update GitHub Secret**
   - Go to Settings → Secrets and variables → Actions
   - Click the secret name
   - Click "Update"
   - Paste new value
   - Click "Update secret"
4. **Verify Deployment** (redeploy to staging first)
5. **Document Date** (log rotation date)

---

## 🐛 Troubleshooting

### Issue: "Permission denied" deploying to Railway

**Solution:** Verify `RAILWAY_TOKEN` is set and has correct permissions

```bash
# Test token locally (if Railway CLI installed)
railway token ${RAILWAY_TOKEN}
```

### Issue: "Project not found" deploying

**Solution:** Verify `RAILWAY_STAGING_PROJECT_ID` or `RAILWAY_PROD_PROJECT_ID` is correct

```bash
# List your projects
railway projects
```

### Issue: Database connection fails in deployed app

**Solution:** Verify database secrets match connection string format

```bash
# Connection string should be:
postgresql://USER:PASSWORD@HOST:5432/DBNAME

# All parts must match:
# USER = STRAPI_STAGING_DB_USER
# PASSWORD = STRAPI_STAGING_DB_PASSWORD
# HOST = STRAPI_STAGING_DB_HOST
```

### Issue: Vercel deployment fails

**Solution:** Verify `VERCEL_TOKEN` and `VERCEL_PROJECT_ID`

```bash
# Check token scope
# Go to: Vercel → Settings → Tokens
# Verify it has access to the project
```

### Issue: OpenAI API calls fail

**Solution:** Verify `OPENAI_API_KEY` has quota remaining

```bash
# Check OpenAI dashboard: https://platform.openai.com/account/billing
# Ensure API key has access to GPT-4
# Check rate limits
```

### Issue: GitHub Actions shows "Secret not found"

**Solution:** Secret name must match exactly (case-sensitive)

```bash
# Go to: Settings → Secrets and variables → Actions
# Verify secret name matches what workflow expects:
# Correct: RAILWAY_TOKEN
# Wrong: railway_token or RailwayToken
```

---

## 📋 Pre-Deployment Checklist

Before your first production deployment, verify all secrets:

```
☐ RAILWAY_TOKEN - Can deploy to Railway
☐ RAILWAY_STAGING_PROJECT_ID - Points to staging
☐ RAILWAY_PROD_PROJECT_ID - Points to production
☐ STRAPI_STAGING_DB_HOST - Connects to staging DB
☐ STRAPI_STAGING_DB_USER - Valid staging user
☐ STRAPI_STAGING_DB_PASSWORD - Matches staging password
☐ STRAPI_STAGING_ADMIN_PASSWORD - Can login to Strapi staging
☐ STRAPI_STAGING_JWT_SECRET - Strapi can sign tokens
☐ STRAPI_PROD_DB_HOST - Connects to production DB
☐ STRAPI_PROD_DB_USER - Valid production user
☐ STRAPI_PROD_DB_PASSWORD - Matches production password
☐ STRAPI_PROD_ADMIN_PASSWORD - Can login to Strapi production
☐ STRAPI_PROD_JWT_SECRET - Strapi can sign tokens
☐ OPENAI_API_KEY - Valid and has quota
☐ ANTHROPIC_API_KEY - Valid (optional)
☐ GOOGLE_API_KEY - Valid (optional)
☐ VERCEL_TOKEN - Can deploy to Vercel
☐ VERCEL_PROJECT_ID - Correct project ID
```

---

## ✅ Validation Script

```bash
#!/bin/bash
# Check which secrets are configured (GitHub CLI)

echo "Checking GitHub Secrets..."
gh secret list

# Expected output should include:
# RAILWAY_TOKEN
# RAILWAY_STAGING_PROJECT_ID
# RAILWAY_PROD_PROJECT_ID
# STRAPI_STAGING_DB_HOST
# ...etc
```

---

## 📞 Support & Documentation

- **GitHub Secrets Docs:** https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions
- **Railway Docs:** https://docs.railway.app/
- **Vercel Docs:** https://vercel.com/docs
- **OpenAI Docs:** https://platform.openai.com/docs

---

## 🎯 Summary

You need **18 GitHub Secrets** configured:

**CRITICAL (Must Have):**

- Railway: TOKEN, STAGING_PROJECT_ID, PROD_PROJECT_ID
- Database: 6 secrets (staging + prod)
- Strapi: 4 secrets (2 staging + 2 prod)
- AI: 1+ API keys (OpenAI OR Anthropic OR Google)
- Vercel: TOKEN, PROJECT_ID

**Time to Setup:** ~20 minutes  
**Difficulty:** Easy  
**Frequency:** Setup once, rotate every 90 days

---

**Status:** Ready for production  
**Last Updated:** November 4, 2025  
**Next Review:** February 4, 2026 (quarterly)
