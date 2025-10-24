# GitHub Secrets Setup Guide

**Last Updated:** October 24, 2025  
**Status:** Production Ready  
**Version:** 1.0

---

## 📋 Overview

GitHub Environments allow you to organize secrets by component and deployment target. This guide shows how to structure GLAD Labs secrets across **4 components** and **2 environments**.

### ✅ Quick Answer

**YES** - You can use GitHub Environments to automatically assign correct secrets by environment. GitHub Actions will recognize the environment and provide the correct variables.

---

## 🏗️ Architecture

### Components

1. **Strapi CMS** - Headless CMS backend
2. **Co-Founder Agent** - FastAPI AI orchestrator
3. **Public Site** - Next.js public-facing website
4. **Oversight Hub** - React admin dashboard

### Environments

- **staging** - dev branch → Railway staging
- **production** - main branch → Railway/Vercel production

---

## 🔧 GitHub Environments Setup

### Step 1: Create GitHub Environments

Go to: **Settings → Environments**

Create two environments:

1. **staging**
   - Deployment branch: `dev`
   - Protected rules: None (or optional reviewers)

2. **production**
   - Deployment branch: `main`
   - Protected rules: **Require reviewers** (recommended)

### Step 2: Add Secrets to Each Environment

Follow the sections below for your component.

---

## 📝 Secrets by Component & Environment

### **Component 1: Strapi CMS**

#### Staging (Environment: `staging`)

| Secret Name                     | Value                                    | Notes                    |
| ------------------------------- | ---------------------------------------- | ------------------------ |
| `STRAPI_STAGING_DB_HOST`        | PostgreSQL host for staging              | e.g., `db.railway.app`   |
| `STRAPI_STAGING_DB_PORT`        | `5432`                                   | Standard PostgreSQL port |
| `STRAPI_STAGING_DB_NAME`        | `glad_labs_staging`                      | Database name            |
| `STRAPI_STAGING_DB_USER`        | PostgreSQL username                      | From Railway dashboard   |
| `STRAPI_STAGING_DB_PASSWORD`    | PostgreSQL password                      | From Railway dashboard   |
| `STRAPI_STAGING_ADMIN_PASSWORD` | Strapi admin password                    | Generate secure password |
| `STRAPI_STAGING_ADMIN_EMAIL`    | `admin@staging.glad-labs.com`            | Admin email              |
| `STRAPI_STAGING_JWT_SECRET`     | Generate with: `openssl rand -base64 32` | JWT secret for tokens    |
| `STRAPI_STAGING_API_TOKEN`      | Generate in Strapi admin panel           | API access token         |
| `STRAPI_STAGING_TRANSFER_TOKEN` | For Strapi migrations                    | Generate in admin panel  |

#### Production (Environment: `production`)

| Secret Name                  | Value                                    | Notes                    |
| ---------------------------- | ---------------------------------------- | ------------------------ |
| `STRAPI_PROD_DB_HOST`        | PostgreSQL host for production           | Production database      |
| `STRAPI_PROD_DB_PORT`        | `5432`                                   | Standard PostgreSQL port |
| `STRAPI_PROD_DB_NAME`        | `glad_labs_production`                   | Database name            |
| `STRAPI_PROD_DB_USER`        | PostgreSQL username                      | From Railway dashboard   |
| `STRAPI_PROD_DB_PASSWORD`    | PostgreSQL password                      | From Railway dashboard   |
| `STRAPI_PROD_ADMIN_PASSWORD` | Strapi admin password                    | Generate secure password |
| `STRAPI_PROD_ADMIN_EMAIL`    | `admin@glad-labs.com`                    | Production admin email   |
| `STRAPI_PROD_JWT_SECRET`     | Generate with: `openssl rand -base64 32` | JWT secret for tokens    |
| `STRAPI_PROD_API_TOKEN`      | Generate in Strapi admin panel           | API access token         |
| `STRAPI_PROD_TRANSFER_TOKEN` | For Strapi migrations                    | Generate in admin panel  |

---

### **Component 2: Co-Founder Agent (FastAPI)**

#### Staging (Environment: `staging`)

| Secret Name                           | Value                     | Notes                            |
| ------------------------------------- | ------------------------- | -------------------------------- |
| `COFOUNDER_STAGING_OPENAI_API_KEY`    | OpenAI API key            | From OpenAI dashboard            |
| `COFOUNDER_STAGING_ANTHROPIC_API_KEY` | Anthropic API key         | From Anthropic dashboard         |
| `COFOUNDER_STAGING_REDIS_HOST`        | Redis host                | e.g., `redis.railway.app`        |
| `COFOUNDER_STAGING_REDIS_PORT`        | `6379`                    | Redis default port               |
| `COFOUNDER_STAGING_REDIS_PASSWORD`    | Redis password            | From Railway dashboard           |
| `COFOUNDER_STAGING_MEMORY_DB_URL`     | PostgreSQL URL for memory | `postgresql://user:pass@host/db` |
| `COFOUNDER_STAGING_MCP_SERVER_TOKEN`  | MCP authentication token  | Generate: `openssl rand -hex 32` |
| `COFOUNDER_STAGING_LOG_LEVEL`         | `debug`                   | For staging                      |
| `COFOUNDER_STAGING_SENTRY_DSN`        | Sentry error tracking     | From Sentry dashboard (optional) |

#### Production (Environment: `production`)

| Secret Name                        | Value                     | Notes                            |
| ---------------------------------- | ------------------------- | -------------------------------- |
| `COFOUNDER_PROD_OPENAI_API_KEY`    | OpenAI API key            | Production key                   |
| `COFOUNDER_PROD_ANTHROPIC_API_KEY` | Anthropic API key         | Production key                   |
| `COFOUNDER_PROD_REDIS_HOST`        | Redis host                | Production Redis                 |
| `COFOUNDER_PROD_REDIS_PORT`        | `6379`                    | Redis default port               |
| `COFOUNDER_PROD_REDIS_PASSWORD`    | Redis password            | From Railway dashboard           |
| `COFOUNDER_PROD_MEMORY_DB_URL`     | PostgreSQL URL for memory | Production database              |
| `COFOUNDER_PROD_MCP_SERVER_TOKEN`  | MCP authentication token  | Generate: `openssl rand -hex 32` |
| `COFOUNDER_PROD_LOG_LEVEL`         | `info`                    | For production                   |
| `COFOUNDER_PROD_SENTRY_DSN`        | Sentry error tracking     | From Sentry dashboard            |

---

### **Component 3: Public Site (Next.js)**

#### Staging (Environment: `staging`)

| Secret Name                         | Value                                    | Notes                  |
| ----------------------------------- | ---------------------------------------- | ---------------------- |
| `PUBLIC_SITE_STAGING_STRAPI_URL`    | `https://staging-cms.railway.app`        | Strapi API URL         |
| `PUBLIC_SITE_STAGING_COFOUNDER_URL` | `https://staging-agent.railway.app:8000` | Co-founder Agent URL   |
| `PUBLIC_SITE_STAGING_GA_ID`         | Google Analytics ID                      | Staging GA4 property   |
| `PUBLIC_SITE_STAGING_POSTHOG_KEY`   | PostHog analytics key                    | Optional analytics     |
| `PUBLIC_SITE_STAGING_SENTRY_DSN`    | Sentry error tracking                    | Optional monitoring    |
| `PUBLIC_SITE_STAGING_VERCEL_TOKEN`  | Vercel API token                         | For Vercel deployments |

#### Production (Environment: `production`)

| Secret Name                      | Value                            | Notes                   |
| -------------------------------- | -------------------------------- | ----------------------- |
| `PUBLIC_SITE_PROD_STRAPI_URL`    | `https://cms.railway.app`        | Production Strapi API   |
| `PUBLIC_SITE_PROD_COFOUNDER_URL` | `https://agent.railway.app:8000` | Production Agent URL    |
| `PUBLIC_SITE_PROD_GA_ID`         | Google Analytics ID              | Production GA4 property |
| `PUBLIC_SITE_PROD_POSTHOG_KEY`   | PostHog analytics key            | Production analytics    |
| `PUBLIC_SITE_PROD_SENTRY_DSN`    | Sentry error tracking            | Production monitoring   |
| `PUBLIC_SITE_PROD_VERCEL_TOKEN`  | Vercel API token                 | For Vercel deployments  |

---

### **Component 4: Oversight Hub (React)**

#### Staging (Environment: `staging`)

| Secret Name                       | Value                                    | Notes                  |
| --------------------------------- | ---------------------------------------- | ---------------------- |
| `OVERSIGHT_STAGING_STRAPI_URL`    | `https://staging-cms.railway.app`        | Strapi API URL         |
| `OVERSIGHT_STAGING_COFOUNDER_URL` | `https://staging-agent.railway.app:8000` | Co-founder Agent URL   |
| `OVERSIGHT_STAGING_AUTH_SECRET`   | Generate: `openssl rand -base64 32`      | NextAuth.js secret     |
| `OVERSIGHT_STAGING_SENTRY_DSN`    | Sentry error tracking                    | Optional               |
| `OVERSIGHT_STAGING_VERCEL_TOKEN`  | Vercel API token                         | For Vercel deployments |

#### Production (Environment: `production`)

| Secret Name                    | Value                               | Notes                  |
| ------------------------------ | ----------------------------------- | ---------------------- |
| `OVERSIGHT_PROD_STRAPI_URL`    | `https://cms.railway.app`           | Production Strapi API  |
| `OVERSIGHT_PROD_COFOUNDER_URL` | `https://agent.railway.app:8000`    | Production Agent URL   |
| `OVERSIGHT_PROD_AUTH_SECRET`   | Generate: `openssl rand -base64 32` | NextAuth.js secret     |
| `OVERSIGHT_PROD_SENTRY_DSN`    | Sentry error tracking               | Production             |
| `OVERSIGHT_PROD_VERCEL_TOKEN`  | Vercel API token                    | For Vercel deployments |

---

## 🚀 Using Secrets in GitHub Actions

### Example: Reference Secrets by Environment

```yaml
name: Deploy to Staging

on:
  push:
    branches: [dev]

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: staging # 👈 THIS tells GitHub which environment's secrets to use

    steps:
      - uses: actions/checkout@v4

      # GitHub Actions automatically provides all secrets from the "staging" environment
      - name: Deploy Strapi
        run: |
          export DATABASE_HOST=${{ secrets.STRAPI_STAGING_DB_HOST }}
          export DATABASE_USER=${{ secrets.STRAPI_STAGING_DB_USER }}
          export DATABASE_PASSWORD=${{ secrets.STRAPI_STAGING_DB_PASSWORD }}
          # ... deployment commands

      - name: Deploy Co-Founder Agent
        run: |
          export OPENAI_API_KEY=${{ secrets.COFOUNDER_STAGING_OPENAI_API_KEY }}
          export REDIS_HOST=${{ secrets.COFOUNDER_STAGING_REDIS_HOST }}
          # ... deployment commands
```

### How It Works

1. **Specify Environment**: `environment: staging` in workflow
2. **GitHub Automatically Provides Secrets**: All secrets from that environment are available
3. **Reference in Steps**: Use `${{ secrets.SECRET_NAME }}`
4. **GitHub Actions Masks**: Secrets are automatically hidden in logs

---

## 📋 Quick Setup Checklist

### In GitHub Settings → Environments

#### Staging Environment

- [ ] Create environment named `staging`
- [ ] Set deployment branch to `dev`
- [ ] Add all `STRAPI_STAGING_*` secrets
- [ ] Add all `COFOUNDER_STAGING_*` secrets
- [ ] Add all `PUBLIC_SITE_STAGING_*` secrets
- [ ] Add all `OVERSIGHT_STAGING_*` secrets

#### Production Environment

- [ ] Create environment named `production`
- [ ] Set deployment branch to `main`
- [ ] **Enable: Require reviewers** ⚠️
- [ ] Add all `STRAPI_PROD_*` secrets
- [ ] Add all `COFOUNDER_PROD_*` secrets
- [ ] Add all `PUBLIC_SITE_PROD_*` secrets
- [ ] Add all `OVERSIGHT_PROD_*` secrets

---

## 🔐 Shared Secrets (Repository Level)

Some secrets don't change by environment. Add these at **Settings → Secrets and variables → Actions** (repository level):

| Secret Name               | Value                    | Usage                                     |
| ------------------------- | ------------------------ | ----------------------------------------- |
| `RAILWAY_TOKEN`           | Railway CLI token        | Authenticate with Railway in any workflow |
| `VERCEL_TOKEN`            | Vercel API token         | Authenticate with Vercel globally         |
| `GITHUB_TOKEN`            | Auto-provided by GitHub  | For GitHub API calls (always available)   |
| `GCP_PROJECT_ID`          | Your GCP project ID      | Cloud Functions deployment                |
| `GCP_SERVICE_ACCOUNT_KEY` | GCP service account JSON | Cloud Functions authentication            |

---

## 🔄 Updated Workflow Examples

### Deploy Staging Workflow

```yaml
name: Deploy to Staging

on:
  push:
    branches: [dev]

jobs:
  deploy-staging:
    runs-on: ubuntu-latest
    environment: staging # 👈 Use staging secrets

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'

      - name: 🚀 Deploy Strapi to Railway
        run: |
          npx railway link --project ${{ secrets.RAILWAY_STAGING_PROJECT }}
          npx railway up
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
          DATABASE_HOST: ${{ secrets.STRAPI_STAGING_DB_HOST }}
          DATABASE_USER: ${{ secrets.STRAPI_STAGING_DB_USER }}
          DATABASE_PASSWORD: ${{ secrets.STRAPI_STAGING_DB_PASSWORD }}

      - name: 🚀 Deploy Public Site to Vercel
        run: |
          npx vercel --prod --token=${{ secrets.VERCEL_TOKEN }}
        env:
          NEXT_PUBLIC_STRAPI_URL: ${{ secrets.PUBLIC_SITE_STAGING_STRAPI_URL }}
          NEXT_PUBLIC_AGENT_URL: ${{ secrets.PUBLIC_SITE_STAGING_COFOUNDER_URL }}
```

### Deploy Production Workflow

```yaml
name: Deploy to Production

on:
  push:
    branches: [main]

jobs:
  deploy-production:
    runs-on: ubuntu-latest
    environment: production # 👈 Use production secrets

    # This will pause and require manual approval
    concurrency:
      group: production-deployment
      cancel-in-progress: false

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'

      - name: 🚀 Deploy Strapi to Railway
        run: |
          npx railway link --project ${{ secrets.RAILWAY_PROD_PROJECT }}
          npx railway up
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
          DATABASE_HOST: ${{ secrets.STRAPI_PROD_DB_HOST }}
          DATABASE_USER: ${{ secrets.STRAPI_PROD_DB_USER }}
          DATABASE_PASSWORD: ${{ secrets.STRAPI_PROD_DB_PASSWORD }}

      - name: 🚀 Deploy Public Site to Vercel
        run: |
          npx vercel --prod --token=${{ secrets.VERCEL_TOKEN }}
        env:
          NEXT_PUBLIC_STRAPI_URL: ${{ secrets.PUBLIC_SITE_PROD_STRAPI_URL }}
          NEXT_PUBLIC_AGENT_URL: ${{ secrets.PUBLIC_SITE_PROD_COFOUNDER_URL }}
```

---

## 🛡️ Security Best Practices

### ✅ DO

- ✅ Use strong, randomly generated passwords/tokens
- ✅ Rotate secrets periodically
- ✅ Use separate credentials per environment
- ✅ Enable "Require reviewers" for production deployments
- ✅ Use GitHub Environments for automatic secret management
- ✅ Mask sensitive values in logs
- ✅ Document which service each secret is for

### ❌ DON'T

- ❌ Commit secrets to the repository
- ❌ Use same credentials across environments
- ❌ Log or print secret values
- ❌ Store secrets in `.env` files in git
- ❌ Share secrets outside secure channels
- ❌ Hardcode API keys in code
- ❌ Commit `.env.production` or `.env.staging` with real values

---

## 📚 Reference: Secret Generation Commands

### Generate secure random strings for secrets:

```bash
# Generate 32-character base64 string (good for tokens/secrets)
openssl rand -base64 32

# Generate 64-character hex string (for strong secrets)
openssl rand -hex 32

# Generate UUID (for unique IDs)
openssl rand -hex 16 | tr -d '\n' | cut -c1-36

# On Windows PowerShell:
[Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
```

---

## 🔄 Environment Variable Mapping

### How to reference secrets in your code:

**In GitHub Actions:**

```yaml
env:
  DATABASE_PASSWORD: ${{ secrets.STRAPI_STAGING_DB_PASSWORD }}
```

**In Node.js:**

```javascript
const dbPassword = process.env.DATABASE_PASSWORD;
```

**In Python:**

```python
import os
db_password = os.getenv('DATABASE_PASSWORD')
```

**In .env files (for local development):**

```bash
DATABASE_PASSWORD=your_local_password_here
```

---

## ✅ Verification Checklist

After setting up GitHub Environments and Secrets:

- [ ] Run staging workflow on dev branch → validates secrets work
- [ ] Run production workflow on main branch → validates secrets work
- [ ] Check GitHub Actions logs → secrets properly masked
- [ ] Verify deployments succeed with correct configuration
- [ ] Test each component communicates with correct endpoints
- [ ] Confirm no secrets appear in logs or error messages

---

## 📞 Troubleshooting

### Problem: "Secret not found" error in workflow

**Solution:**

1. Check environment name matches in workflow: `environment: staging`
2. Verify secret exists in that environment
3. Check secret name spelling matches exactly

### Problem: Wrong secrets used for environment

**Solution:**

1. Confirm workflow specifies correct `environment:`
2. Manually check GitHub Settings → Environments
3. Verify branch filters match (dev → staging, main → production)

### Problem: Secret appears in logs

**Solution:**

1. GitHub automatically masks known secrets
2. If custom masking needed, use: `::add-mask::{value}`
3. Never log secret variables directly

---

## 📝 Document Control

| Field            | Value             |
| ---------------- | ----------------- |
| **Version**      | 1.0               |
| **Last Updated** | October 24, 2025  |
| **Status**       | Production Ready  |
| **Author**       | GitHub Copilot    |
| **Next Review**  | December 24, 2025 |

---

## 🔗 Related Documentation

- [GitHub Docs: Environments](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment)
- [GitHub Docs: Secrets](https://docs.github.com/en/actions/security-guides/encrypted-secrets)
- [Deployment Guide](./docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md)
- [Branch Variables Guide](./docs/07-BRANCH_SPECIFIC_VARIABLES.md)

---

**✅ Ready to set up? Go to GitHub Settings → Environments and start adding secrets!**
