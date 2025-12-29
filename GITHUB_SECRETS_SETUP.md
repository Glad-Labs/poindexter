# GitHub Secrets Setup Guide

> **Last Updated:** December 29, 2025  
> **For:** Glad Labs AI Co-Founder System (Monorepo)

## Overview

This guide helps you configure GitHub Secrets for automated deployments to **staging** (Railway dev) and **production** (Vercel + Railway main).

**Deployment Flow:**

```
Local (dev)     → .env.local (NEVER commit)
Feature branch  → Uses .env.local values
dev branch      → Auto-deploys to staging (Railway) using GitHub Secrets
main branch     → Auto-deploys to production (Vercel + Railway) using GitHub Secrets
```

---

## Quick Start: Add Secrets to GitHub

1. Go to your repo: **Settings → Secrets and Variables → Actions**
2. Click **New repository secret**
3. Add each secret below with exact names and values

---

## Required GitHub Secrets by Environment

### 🔵 STAGING Secrets (dev branch deployment)

> Use for testing before production

| Secret Name                 | Value                                                      | Source                                             |
| --------------------------- | ---------------------------------------------------------- | -------------------------------------------------- |
| `DATABASE_URL_STAGING`      | `postgresql://user:password@host:5432/db`                  | Railway PostgreSQL                                 |
| `DATABASE_HOST_STAGING`     | `host.railway.internal`                                    | Railway PostgreSQL                                 |
| `DATABASE_NAME_STAGING`     | `staging_db`                                               | Railway PostgreSQL                                 |
| `DATABASE_USER_STAGING`     | `postgres`                                                 | Railway PostgreSQL                                 |
| `DATABASE_PASSWORD_STAGING` | (strong password)                                          | Railway PostgreSQL                                 |
| `ANTHROPIC_API_KEY`         | `sk-ant-xxx...`                                            | [Anthropic Console](https://console.anthropic.com) |
| `JWT_SECRET_STAGING`        | (generate: `openssl rand -base64 32`)                      | Run locally                                        |
| `SENTRY_DSN_STAGING`        | `https://xxx@o123.ingest.us.sentry.io/456`                 | [Sentry](https://sentry.io)                        |
| `PEXELS_API_KEY`            | `wdq7jNG49KWxBipK90hu32V5RLpXD0I5J81n61WeQzh31sdGJ9sua1qT` | Existing key                                       |

### 🔴 PRODUCTION Secrets (main branch deployment)

> Real credentials - treat with extreme care

| Secret Name                    | Value                                                      | Source                                                  |
| ------------------------------ | ---------------------------------------------------------- | ------------------------------------------------------- |
| `DATABASE_URL_PRODUCTION`      | `postgresql://user:password@host:5432/db`                  | Railway PostgreSQL                                      |
| `DATABASE_HOST_PRODUCTION`     | `host.railway.internal`                                    | Railway PostgreSQL                                      |
| `DATABASE_NAME_PRODUCTION`     | `prod_db`                                                  | Railway PostgreSQL                                      |
| `DATABASE_USER_PRODUCTION`     | `postgres`                                                 | Railway PostgreSQL                                      |
| `DATABASE_PASSWORD_PRODUCTION` | (strong password)                                          | Railway PostgreSQL                                      |
| `REDIS_HOST_PRODUCTION`        | `redis-host`                                               | Railway Redis                                           |
| `REDIS_PASSWORD_PRODUCTION`    | (strong password)                                          | Railway Redis                                           |
| `ANTHROPIC_API_KEY`            | `sk-ant-xxx...`                                            | [Anthropic Console](https://console.anthropic.com)      |
| `OPENAI_API_KEY`               | `sk-proj-xxx...`                                           | [OpenAI API Keys](https://platform.openai.com/api-keys) |
| `JWT_SECRET_PRODUCTION`        | (generate: `openssl rand -base64 32`)                      | Run locally                                             |
| `SENTRY_DSN_PRODUCTION`        | `https://xxx@o123.ingest.us.sentry.io/789`                 | [Sentry](https://sentry.io)                             |
| `PEXELS_API_KEY`               | `wdq7jNG49KWxBipK90hu32V5RLpXD0I5J81n61WeQzh31sdGJ9sua1qT` | Existing key                                            |

---

## Step-by-Step Setup

### 1️⃣ Get Database Credentials from Railway

#### For Staging Database:

```bash
# In Railway dashboard:
# 1. Go to your PostgreSQL service (dev environment)
# 2. Click "Connect" tab
# 3. Copy the DATABASE_URL
# Example: postgresql://postgres:xxxxx@containers-us-west-xyz.railway.app:5432/railway

# Break it down:
# DATABASE_URL_STAGING = postgresql://postgres:xxxxx@containers-us-west-xyz.railway.app:5432/railway
# DATABASE_HOST_STAGING = containers-us-west-xyz.railway.app
# DATABASE_NAME_STAGING = railway
# DATABASE_USER_STAGING = postgres
# DATABASE_PASSWORD_STAGING = xxxxx (the password from URL)
```

#### For Production Database:

```bash
# Repeat for main environment PostgreSQL service
# Use PRODUCTION suffix instead of STAGING
```

### 2️⃣ Generate Secure Secrets

Generate strong JWT secrets locally (never commit these):

```bash
# Generate a random 32-byte base64 string
openssl rand -base64 32

# Example output: (copy this value)
# aBcD1234eFgH5678iJkL9101mNoPqR+ST+UVWxYZ1234=
```

Generate two different secrets:

- One for `JWT_SECRET_STAGING`
- One for `JWT_SECRET_PRODUCTION`

### 3️⃣ Get AI Model API Keys

#### Anthropic Claude:

1. Go to [Anthropic Console](https://console.anthropic.com)
2. Click "API Keys" in left sidebar
3. Create or copy your API key
4. Add as `ANTHROPIC_API_KEY` (used by both staging and production)

#### OpenAI (Optional - fallback):

1. Go to [OpenAI Platform](https://platform.openai.com/api-keys)
2. Create a new secret key
3. Add as `OPENAI_API_KEY`

#### Google Gemini (Optional - tertiary fallback):

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Create a new API key
3. Add as `GOOGLE_API_KEY`

### 4️⃣ Get Sentry Error Tracking Credentials

#### Create Sentry Projects:

1. Go to [Sentry](https://sentry.io) and log in
2. Create new project for **Staging**:
   - Name: "Glad Labs Staging"
   - Platform: Python (for backend)
3. Copy the DSN (looks like `https://xxx@o123.ingest.us.sentry.io/456`)
4. Add as `SENTRY_DSN_STAGING`
5. Repeat for **Production** → `SENTRY_DSN_PRODUCTION`

### 5️⃣ Get Redis Credentials (Production Only)

1. In Railway dashboard, create Redis service for main environment
2. Click "Connect" tab
3. Copy Redis URL: `redis://:password@host:port`
4. Parse out:
   - `REDIS_HOST_PRODUCTION` = host
   - `REDIS_PASSWORD_PRODUCTION` = password

---

## Adding Secrets to GitHub

### Via Web UI (Recommended):

```
1. Go to: github.com/youruser/glad-labs-website
2. Click: Settings (top navigation)
3. Click: Secrets and Variables → Actions (left sidebar)
4. Click: New repository secret
5. Name: JWT_SECRET_STAGING
6. Value: (paste generated secret)
7. Click: Add secret
8. Repeat for all secrets in the table above
```

### Via GitHub CLI:

```bash
# Install GitHub CLI: https://cli.github.com

# Add staging secrets
gh secret set DATABASE_URL_STAGING -b "postgresql://..."
gh secret set JWT_SECRET_STAGING -b "generated-secret-here"

# Add production secrets
gh secret set DATABASE_URL_PRODUCTION -b "postgresql://..."
gh secret set JWT_SECRET_PRODUCTION -b "generated-secret-here"

# List all secrets
gh secret list
```

---

## Using Secrets in CI/CD Workflows

Your GitHub Actions workflows should already reference these secrets:

```yaml
# Example: .github/workflows/deploy.yml
jobs:
  deploy-staging:
    if: github.ref == 'refs/heads/dev'
    steps:
      - name: Deploy to Railway Staging
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL_STAGING }}
          JWT_SECRET: ${{ secrets.JWT_SECRET_STAGING }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          npm run deploy:staging

  deploy-production:
    if: github.ref == 'refs/heads/main'
    steps:
      - name: Deploy to Vercel + Railway
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL_PRODUCTION }}
          JWT_SECRET: ${{ secrets.JWT_SECRET_PRODUCTION }}
          REDIS_HOST: ${{ secrets.REDIS_HOST_PRODUCTION }}
        run: |
          npm run deploy:production
```

---

## Troubleshooting

### ❌ "Secret not found" error during deployment

**Problem:** Workflow can't access a GitHub Secret

**Solution:**

1. Verify secret name matches exactly (case-sensitive)
2. Check it's added to the correct repository (not organization)
3. Wait 1-2 minutes after adding new secret (GitHub propagates)
4. Check workflow YAML uses correct syntax: `${{ secrets.SECRET_NAME }}`

### ❌ "Database connection refused"

**Problem:** Staging/Production deployment fails at database step

**Solution:**

1. Verify `DATABASE_URL_STAGING` or `DATABASE_URL_PRODUCTION` is correct
2. Check Railway PostgreSQL service is running
3. Ensure password doesn't have special characters that need escaping
4. Test connection locally: `psql postgresql://user:pass@host:port/db`

### ❌ "API Key invalid" error

**Problem:** AI model requests fail

**Solution:**

1. Verify `ANTHROPIC_API_KEY` is from [Anthropic Console](https://console.anthropic.com)
2. Check key hasn't expired or been revoked
3. Ensure you have API credits available
4. Test key locally in Python:
   ```python
   import anthropic
   client = anthropic.Anthropic(api_key="YOUR_KEY")
   message = client.messages.create(model="claude-opus-4-1", max_tokens=100, messages=[{"role": "user", "content": "test"}])
   ```

---

## Security Best Practices

✅ **DO:**

- Use strong, unique secrets for each environment
- Rotate secrets every 90 days in production
- Use Railway's built-in secret management for sensitive data
- Enable branch protection rules requiring approval
- Audit GitHub Secrets access in repository settings

❌ **DON'T:**

- Commit `.env.local` or any `.env` files with real secrets
- Share secrets via Slack, email, or messaging apps
- Use same JWT secret in staging and production
- Commit API keys even as "test" values
- Use default/placeholder values in production

---

## Reference: Environment File Templates

### .env.local (for local development - NEVER commit)

See [.env.local](.env.local) in repo root

### .env.staging (template for staging - NEVER commit)

See [.env.staging](.env.staging) in repo root

### .env.production (template for production - NEVER commit)

See [.env.production](.env.production) in repo root

All actual secrets go in **GitHub Secrets**, not in these files.

---

## Deployment Command Reference

```bash
# Deploy to staging (automatic on git push to dev)
npm run deploy:staging
# OR triggered automatically by GitHub Actions

# Deploy to production (automatic on git push to main)
npm run deploy:production
# OR triggered automatically by GitHub Actions

# Check deployed secrets (local only - cannot list GitHub Secrets from CLI)
gh secret list
```

---

## Next Steps

1. ✅ Generate JWT secrets: `openssl rand -base64 32`
2. ✅ Get all API keys and credentials from external services
3. ✅ Create/configure Railway databases and Redis
4. ✅ Create Sentry projects and get DSNs
5. ✅ Add all secrets to GitHub (Settings → Secrets and Variables)
6. ✅ Create GitHub Actions workflows for staging/production deployments
7. ✅ Test: Push to `dev` branch → verify staging deployment
8. ✅ Test: Push to `main` branch → verify production deployment

---

## Questions?

Refer to:

- Railway Docs: https://docs.railway.app
- GitHub Secrets: https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions
- Sentry Setup: https://sentry.io/onboarding/
- Anthropic API: https://docs.anthropic.com/claude/reference/getting-started-with-the-api
