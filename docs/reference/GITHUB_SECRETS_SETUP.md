# GitHub Environment Secrets Setup (Authoritative)

**Last Updated:** March 4, 2026  
**Status:** Aligned with active deploy workflows

---

## Scope

This file documents secret names consumed by:

- `.github/workflows/deploy-staging-with-environments.yml`
- `.github/workflows/deploy-production-with-environments.yml`

Use GitHub **Environment Secrets** (`staging`, `production`) for deployment values.

---

## Required Staging Secrets

### Core deployment (staging)

- `RAILWAY_TOKEN`
- `RAILWAY_STAGING_PROJECT_ID`
- `DATABASE_STAGING_URL`
- `COFOUNDER_STAGING_JWT_SECRET`
- `VERCEL_TOKEN`
- `VERCEL_ORG_ID`

### Service/project IDs (staging)

- `PUBLIC_SITE_STAGING_PROJECT_ID`
- `OVERSIGHT_STAGING_PROJECT_ID`

### Backend provider/config (staging)

- `COFOUNDER_STAGING_OPENAI_API_KEY` (optional if using Anthropic/Google)
- `COFOUNDER_STAGING_ANTHROPIC_API_KEY` (optional)
- `COFOUNDER_STAGING_GOOGLE_API_KEY` (optional)
- `COFOUNDER_STAGING_REDIS_HOST` (optional)
- `COFOUNDER_STAGING_REDIS_PASSWORD` (optional)
- `COFOUNDER_STAGING_SENTRY_DSN` (optional)

### Frontend staging URLs

- `PUBLIC_SITE_STAGING_FASTAPI_URL`
- `PUBLIC_SITE_STAGING_SITE_URL`
- `PUBLIC_SITE_STAGING_GA_ID` (optional)
- `PUBLIC_SITE_STAGING_SENTRY_DSN` (optional)
- `OVERSIGHT_STAGING_API_URL`
- `OVERSIGHT_STAGING_AGENT_URL`
- `OVERSIGHT_STAGING_AUTH_SECRET`
- `OVERSIGHT_STAGING_SENTRY_DSN` (optional)

---

## Required Production Secrets

### Core deployment

- `RAILWAY_TOKEN`
- `RAILWAY_PROD_PROJECT_ID`
- `DATABASE_PROD_URL`
- `COFOUNDER_PROD_JWT_SECRET`
- `VERCEL_TOKEN`
- `VERCEL_ORG_ID`

### Service/project IDs

- `PUBLIC_SITE_PROD_PROJECT_ID`
- `OVERSIGHT_PROD_PROJECT_ID`

### Backend provider/config

- `COFOUNDER_PROD_OPENAI_API_KEY` (optional if using Anthropic/Google)
- `COFOUNDER_PROD_ANTHROPIC_API_KEY` (optional)
- `COFOUNDER_PROD_GOOGLE_API_KEY` (optional)
- `COFOUNDER_PROD_REDIS_HOST` (optional)
- `COFOUNDER_PROD_REDIS_PASSWORD` (optional)
- `COFOUNDER_PROD_SENTRY_DSN` (optional)

### Frontend production URLs

- `PUBLIC_SITE_PROD_FASTAPI_URL`
- `PUBLIC_SITE_PROD_SITE_URL`
- `PUBLIC_SITE_PROD_GA_ID` (optional)
- `PUBLIC_SITE_PROD_SENTRY_DSN` (optional)
- `OVERSIGHT_PROD_API_URL`
- `OVERSIGHT_PROD_AGENT_URL`
- `OVERSIGHT_PROD_AUTH_SECRET`
- `OVERSIGHT_PROD_SENTRY_DSN` (optional)

### Smoke/verification URLs

- `COFOUNDER_PROD_URL`
- `OVERSIGHT_PROD_URL`

---

## Naming Standards

- Use `DATABASE_STAGING_URL` / `DATABASE_PROD_URL` (not `STAGING_DATABASE_URL` / `PROD_DATABASE_URL`).
- Use `COFOUNDER_*_JWT_SECRET` in GitHub secrets; runtime maps to `JWT_SECRET_KEY`.
- Use `GH_OAUTH_CLIENT_ID` / `GH_OAUTH_CLIENT_SECRET` for local/runtime configuration docs.

---

## Security Rules

- Do not store real secret values in committed `.env*` files.
- Rotate any secret immediately if exposed.
- Keep staging and production credentials separate.
- Limit write access to GitHub environments.
