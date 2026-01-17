# GitHub Secrets Setup Guide

**Purpose**: Centralized reference for all GitHub organization and environment secrets needed for deployment.

**Status**: Complete setup guide for production and staging environments

---

## Quick Start

1. Go to: GitHub Repository → Settings → Secrets and variables → Actions
2. Create **Organization Secrets** (shared by all environments)
3. Create **Environment Secrets** for "production" and "staging"
4. Follow the exact variable names and descriptions below

---

## Organization Secrets (Shared by All Environments)

These are shared across all branches and deployments. Create these first.

| Secret Name     | Value                       | Notes                               |
| --------------- | --------------------------- | ----------------------------------- |
| `RAILWAY_TOKEN` | Your Railway API token      | From Railway → Account → API Tokens |
| `VERCEL_TOKEN`  | Your Vercel auth token      | From Vercel → Settings → Tokens     |
| `VERCEL_ORG_ID` | Your Vercel organization ID | From Vercel → Settings → General    |

---

## Production Environment Secrets

**Location**: Settings → Environments → production → Environment secrets

### Critical Secrets (Must Create)

| Secret Name                 | Example Value                                     | How to Get                      |
| --------------------------- | ------------------------------------------------- | ------------------------------- |
| `DATABASE_PROD_URL`         | `postgresql://user:pass@host:5432/glad_labs_prod` | Railway PostgreSQL service info |
| `RAILWAY_PROD_PROJECT_ID`   | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`            | Railway → Project → Settings    |
| `COFOUNDER_PROD_JWT_SECRET` | Generate via `openssl rand -base64 32`            | **Create unique, secure value** |

### Recommended Secrets (Frontend URLs)

| Secret Name                    | Example Value                          | Notes                            |
| ------------------------------ | -------------------------------------- | -------------------------------- |
| `PUBLIC_SITE_PROD_PROJECT_ID`  | Your Vercel project ID                 | Vercel → Project Settings        |
| `PUBLIC_SITE_PROD_FASTAPI_URL` | `https://agent-prod.railway.app`       | Your production API endpoint     |
| `PUBLIC_SITE_PROD_SITE_URL`    | `https://glad-labs.com`                | Your production domain           |
| `PUBLIC_SITE_PROD_GA_ID`       | `G-XXXXXXXXXX`                         | Google Analytics ID (optional)   |
| `PUBLIC_SITE_PROD_SENTRY_DSN`  | `https://xxxxx@sentry.io/yyyy`         | Sentry error tracking (optional) |
| `OVERSIGHT_PROD_PROJECT_ID`    | Your Vercel project ID                 | Vercel project for admin hub     |
| `OVERSIGHT_PROD_API_URL`       | `https://agent-prod.railway.app`       | Backend API endpoint             |
| `OVERSIGHT_PROD_AGENT_URL`     | `https://agent-prod.railway.app`       | Same as API URL                  |
| `OVERSIGHT_PROD_AUTH_SECRET`   | Generate via `openssl rand -base64 32` | **Create unique value**          |
| `OVERSIGHT_PROD_SENTRY_DSN`    | `https://xxxxx@sentry.io/yyyy`         | Sentry error tracking (optional) |

### AI Model Secrets (Choose One or More)

Use at least one of these for LLM provider fallback.

| Secret Name                        | Example Value | When to Use                   |
| ---------------------------------- | ------------- | ----------------------------- |
| `COFOUNDER_PROD_ANTHROPIC_API_KEY` | `sk-ant-...`  | If using Claude (recommended) |
| `COFOUNDER_PROD_OPENAI_API_KEY`    | `sk-proj-...` | If using ChatGPT as fallback  |
| `COFOUNDER_PROD_GOOGLE_API_KEY`    | `AIzaSy...`   | If using Gemini as fallback   |

### Optional Production Secrets

| Secret Name                     | Example Value                   | When Needed              |
| ------------------------------- | ------------------------------- | ------------------------ |
| `COFOUNDER_PROD_REDIS_HOST`     | `redis-prod.railway.app`        | If using Redis caching   |
| `COFOUNDER_PROD_REDIS_PASSWORD` | Your Redis password             | If using Redis caching   |
| `PEXELS_API_KEY`                | `563492ad6f917000010000009e...` | For image search feature |

---

## Staging Environment Secrets

**Location**: Settings → Environments → staging → Environment secrets

### Critical Secrets (Must Create)

| Secret Name                    | Value                                    |
| ------------------------------ | ---------------------------------------- |
| `DATABASE_STAGING_URL`         | PostgreSQL connection string for staging |
| `RAILWAY_STAGING_PROJECT_ID`   | Staging Railway project ID               |
| `COFOUNDER_STAGING_JWT_SECRET` | **Create unique JWT secret**             |

### Frontend URLs (Staging)

| Secret Name                       | Example Value                       |
| --------------------------------- | ----------------------------------- |
| `PUBLIC_SITE_STAGING_PROJECT_ID`  | Staging Vercel project ID           |
| `PUBLIC_SITE_STAGING_FASTAPI_URL` | `https://agent-staging.railway.app` |
| `PUBLIC_SITE_STAGING_SITE_URL`    | `https://staging.glad-labs.com`     |
| `OVERSIGHT_STAGING_PROJECT_ID`    | Staging Vercel admin hub project    |
| `OVERSIGHT_STAGING_API_URL`       | `https://agent-staging.railway.app` |
| `OVERSIGHT_STAGING_AGENT_URL`     | `https://agent-staging.railway.app` |
| `OVERSIGHT_STAGING_AUTH_SECRET`   | **Create unique value**             |

### AI Model Secrets (Staging)

| Secret Name                           |
| ------------------------------------- |
| `COFOUNDER_STAGING_ANTHROPIC_API_KEY` |
| `COFOUNDER_STAGING_OPENAI_API_KEY`    |
| `COFOUNDER_STAGING_GOOGLE_API_KEY`    |

### Optional Staging Secrets

| Secret Name                        |
| ---------------------------------- |
| `COFOUNDER_STAGING_REDIS_HOST`     |
| `COFOUNDER_STAGING_REDIS_PASSWORD` |

---

## Deprecated Secrets (Do NOT Create)

These secrets are referenced by old workflows but should **NOT** be created:

```
❌ PUBLIC_SITE_PROD_STRAPI_URL       (CMS removed)
❌ PUBLIC_SITE_STAGING_STRAPI_URL    (CMS removed)
❌ OVERSIGHT_PROD_STRAPI_URL         (CMS removed)
❌ OVERSIGHT_STAGING_STRAPI_URL      (CMS removed)
❌ COFOUNDER_PROD_MCP_SERVER_TOKEN   (Removed from workflows)
❌ COFOUNDER_STAGING_MCP_SERVER_TOKEN (Removed from workflows)
```

---

## Setup Checklist

### Production Environment Setup

1. **Create Production Environment**
   - [ ] Go to Settings → Environments
   - [ ] Create new environment named "production"
   - [ ] Set "Deployment protection rules" if desired

2. **Create Organization Secrets**
   - [ ] `RAILWAY_TOKEN`
   - [ ] `VERCEL_TOKEN`
   - [ ] `VERCEL_ORG_ID`

3. **Create Production Environment Secrets**
   - [ ] `DATABASE_PROD_URL` (critical)
   - [ ] `RAILWAY_PROD_PROJECT_ID` (critical)
   - [ ] `COFOUNDER_PROD_JWT_SECRET` (critical)
   - [ ] `PUBLIC_SITE_PROD_PROJECT_ID`
   - [ ] `PUBLIC_SITE_PROD_FASTAPI_URL`
   - [ ] `PUBLIC_SITE_PROD_SITE_URL`
   - [ ] `OVERSIGHT_PROD_PROJECT_ID`
   - [ ] `OVERSIGHT_PROD_API_URL`
   - [ ] `OVERSIGHT_PROD_AGENT_URL`
   - [ ] `OVERSIGHT_PROD_AUTH_SECRET`
   - [ ] At least one: `COFOUNDER_PROD_ANTHROPIC_API_KEY` OR `COFOUNDER_PROD_OPENAI_API_KEY` OR `COFOUNDER_PROD_GOOGLE_API_KEY`

4. **Optional Production Secrets**
   - [ ] `COFOUNDER_PROD_REDIS_HOST`
   - [ ] `COFOUNDER_PROD_REDIS_PASSWORD`
   - [ ] `PUBLIC_SITE_PROD_SENTRY_DSN`
   - [ ] `PUBLIC_SITE_PROD_GA_ID`
   - [ ] `PEXELS_API_KEY`

### Staging Environment Setup

1. **Create Staging Environment**
   - [ ] Go to Settings → Environments
   - [ ] Create new environment named "staging"

2. **Create Staging Environment Secrets**
   - [ ] `DATABASE_STAGING_URL` (critical)
   - [ ] `RAILWAY_STAGING_PROJECT_ID` (critical)
   - [ ] `COFOUNDER_STAGING_JWT_SECRET` (critical)
   - [ ] `PUBLIC_SITE_STAGING_PROJECT_ID`
   - [ ] `PUBLIC_SITE_STAGING_FASTAPI_URL`
   - [ ] `PUBLIC_SITE_STAGING_SITE_URL`
   - [ ] `OVERSIGHT_STAGING_PROJECT_ID`
   - [ ] `OVERSIGHT_STAGING_API_URL`
   - [ ] `OVERSIGHT_STAGING_AGENT_URL`
   - [ ] `OVERSIGHT_STAGING_AUTH_SECRET`
   - [ ] At least one LLM API key

---

## Verification

After creating secrets, verify them:

```bash
# Check which secrets are defined (from GitHub CLI)
gh secret list --env production
gh secret list --env staging
```

Expected output should include all critical secrets.

---

## Security Best Practices

1. **Never commit secrets** to version control
2. **Use strong JWT secrets**: `openssl rand -base64 32`
3. **Rotate JWT secrets** periodically (requires rebuild)
4. **Separate production and staging secrets** (different values)
5. **Use service accounts** for Railway and Vercel (not personal accounts)
6. **Enable branch protection** on main branch
7. **Audit secret access** regularly via GitHub Activity Log

---

## Common Issues

**Issue**: Deployment fails with "undefined secret"

- **Solution**: Verify secret name is exactly correct (case-sensitive)
- Run `gh secret list --env production` to confirm it exists

**Issue**: Frontend can't reach API

- **Solution**: Verify `PUBLIC_SITE_PROD_FASTAPI_URL` is correct Railway URL
- Check that Railway deployment is actually live

**Issue**: Authentication failing in production

- **Solution**: Verify `COFOUNDER_PROD_JWT_SECRET` is set correctly
- Ensure all services are reading from secrets, not environment

**Issue**: CI/CD pipeline failing with "Context access might be invalid"

- **Solution**: This is a warning, not an error
- Verify the secret will be created before running workflow

---

## Related Documentation

- **Environment Configuration**: See `ENV_CONFIGURATION_AUDIT.md`
- **Workflow Files**: `.github/workflows/deploy-production-with-environments.yml`
- **Setup Guide**: `docs/01-SETUP_AND_OVERVIEW.md`
- **Deployment Guide**: `docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md`

---

**Last Updated**: January 11, 2026  
**Status**: Ready for implementation
