# Environment Configuration - Action Items

**Priority**: 🔴 CRITICAL - Complete before next deployment  
**Estimated Time**: 30-45 minutes

---

## Action 1: Update .env.local with Missing Frontend Variables

**File**: `.env.local` (at project root)

**Changes Required**:

Add these two variables after the existing `NEXT_PUBLIC_COFOUNDER_AGENT_URL` line:

```env
# Frontend - Public Site (Next.js)
NEXT_PUBLIC_FASTAPI_URL=http://localhost:8000
NEXT_PUBLIC_SITE_URL=http://localhost:3000
```

**Why**: The public site code references `NEXT_PUBLIC_FASTAPI_URL` in multiple files but it's not defined. Currently falls back to hardcoded localhost, which breaks in production.

**Verification**: After adding, run:

```bash
grep -n "NEXT_PUBLIC_FASTAPI_URL" .env.local
```

Should return the line number where it's defined.

---

## Action 2: Fix Production Deployment Workflow

**File**: `.github/workflows/deploy-production-with-environments.yml`

### Fix 2A: Add DATABASE_URL to Backend Deployment

**Location**: Around line 103 (in the Co-Founder Agent deployment section)

**Change**:

```yaml
# BEFORE:
env:
  RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
  RAILWAY_PROJECT_ID: ${{ secrets.RAILWAY_PROD_PROJECT_ID }}
  RAILWAY_SERVICE_ID: cofounder-agent-prod
  OPENAI_API_KEY: ${{ secrets.COFOUNDER_PROD_OPENAI_API_KEY }}

# AFTER:
env:
  RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
  RAILWAY_PROJECT_ID: ${{ secrets.RAILWAY_PROD_PROJECT_ID }}
  RAILWAY_SERVICE_ID: cofounder-agent-prod
  DATABASE_URL: ${{ secrets.DATABASE_PROD_URL }}           # ← ADD THIS
  OPENAI_API_KEY: ${{ secrets.COFOUNDER_PROD_OPENAI_API_KEY }}
```

### Fix 2B: Add JWT Secret to Backend Deployment

**Location**: Same section (Co-Founder Agent)

**Add after SENTRY_DSN**:

```yaml
SENTRY_DSN: ${{ secrets.COFOUNDER_PROD_SENTRY_DSN }}
JWT_SECRET: ${{ secrets.COFOUNDER_PROD_JWT_SECRET }} # ← ADD THIS
ENVIRONMENT: production
```

### Fix 2C: Remove Strapi References from Public Site

**Location**: Lines 125-130 (Public Site deployment)

**REMOVE these lines**:

```yaml
NEXT_PUBLIC_STRAPI_URL: ${{ secrets.PUBLIC_SITE_PROD_STRAPI_URL }}
```

**KEEP these lines**:

```yaml
VERCEL_TOKEN: ${{ secrets.VERCEL_TOKEN }}
VERCEL_PROJECT_ID: ${{ secrets.PUBLIC_SITE_PROD_PROJECT_ID }}
VERCEL_ORG_ID: ${{ secrets.VERCEL_ORG_ID }}
NEXT_PUBLIC_COFOUNDER_URL: ${{ secrets.PUBLIC_SITE_PROD_COFOUNDER_URL }} # Keep
NEXT_PUBLIC_GA_ID: ${{ secrets.PUBLIC_SITE_PROD_GA_ID }}
SENTRY_DSN: ${{ secrets.PUBLIC_SITE_PROD_SENTRY_DSN }}
```

### Fix 2D: Add Missing Frontend Variables to Public Site

**Location**: Same Public Site section

**ADD these lines**:

```yaml
NEXT_PUBLIC_FASTAPI_URL: ${{ secrets.PUBLIC_SITE_PROD_FASTAPI_URL }} # ← ADD
NEXT_PUBLIC_SITE_URL: ${{ secrets.PUBLIC_SITE_PROD_SITE_URL }} # ← ADD
```

### Fix 2E: Remove Strapi References from Oversight Hub

**Location**: Lines 145-150 (Oversight Hub deployment)

**REMOVE**:

```yaml
REACT_APP_STRAPI_URL: ${{ secrets.OVERSIGHT_PROD_STRAPI_URL }}
```

**FINAL CONFIG FOR OVERSIGHT HUB** should be:

```yaml
VERCEL_TOKEN: ${{ secrets.VERCEL_TOKEN }}
VERCEL_PROJECT_ID: ${{ secrets.OVERSIGHT_PROD_PROJECT_ID }}
VERCEL_ORG_ID: ${{ secrets.VERCEL_ORG_ID }}
REACT_APP_AGENT_URL: ${{ secrets.OVERSIGHT_PROD_COFOUNDER_URL }}
REACT_APP_AUTH_SECRET: ${{ secrets.OVERSIGHT_PROD_AUTH_SECRET }}
SENTRY_DSN: ${{ secrets.OVERSIGHT_PROD_SENTRY_DSN }}
```

---

## Action 3: Fix Staging Deployment Workflow

**File**: `.github/workflows/deploy-staging-with-environments.yml`

Apply the same fixes as Action 2, but for staging:

### Fix 3A: Add DATABASE_URL to Backend (Staging)

```yaml
DATABASE_URL: ${{ secrets.DATABASE_STAGING_URL }} # or DATABASE_PROD_URL if shared
```

### Fix 3B: Add JWT Secret to Backend (Staging)

```yaml
JWT_SECRET: ${{ secrets.COFOUNDER_STAGING_JWT_SECRET }}
```

### Fix 3C: Remove Strapi from Public Site (Staging)

Remove:

```yaml
NEXT_PUBLIC_STRAPI_URL: ${{ secrets.PUBLIC_SITE_STAGING_STRAPI_URL }}
```

### Fix 3D: Add Missing Frontend Variables (Staging)

Add:

```yaml
NEXT_PUBLIC_FASTAPI_URL: ${{ secrets.PUBLIC_SITE_STAGING_FASTAPI_URL }}
NEXT_PUBLIC_SITE_URL: ${{ secrets.PUBLIC_SITE_STAGING_SITE_URL }}
```

### Fix 3E: Remove Strapi from Oversight Hub (Staging)

Remove:

```yaml
REACT_APP_STRAPI_URL: ${{ secrets.OVERSIGHT_STAGING_STRAPI_URL }}
```

---

## Action 4: Create GitHub Organization Secrets

**Location**: GitHub repository → Settings → Secrets and variables → Actions (or Environments)

### Create in "production" Environment:

| Secret Name                    | Value                                 | Notes                               |
| ------------------------------ | ------------------------------------- | ----------------------------------- |
| `DATABASE_PROD_URL`            | `postgresql://user:pass@host:5432/db` | From Railway PostgreSQL             |
| `COFOUNDER_PROD_JWT_SECRET`    | `<random-64-char-string>`             | Generate: `openssl rand -base64 32` |
| `RAILWAY_TOKEN`                | Your Railway API token                | Railway → Account → API Tokens      |
| `VERCEL_TOKEN`                 | Your Vercel auth token                | Vercel → Settings → Tokens          |
| `VERCEL_ORG_ID`                | Your Vercel org ID                    | Vercel → Settings → General         |
| `RAILWAY_PROD_PROJECT_ID`      | Your Railway project ID               | Railway → Project Settings          |
| `PUBLIC_SITE_PROD_PROJECT_ID`  | Vercel project ID                     | Vercel → Project Settings           |
| `OVERSIGHT_PROD_PROJECT_ID`    | Vercel project ID                     | Vercel → Project Settings           |
| `PUBLIC_SITE_PROD_FASTAPI_URL` | `https://agent-prod.railway.app`      | Your production agent URL           |
| `PUBLIC_SITE_PROD_SITE_URL`    | `https://glad-labs.com`               | Your production domain              |
| `OVERSIGHT_PROD_COFOUNDER_URL` | `https://agent-prod.railway.app`      | Same as agent URL                   |

### Optional - For Production Features:

| Secret Name                        | Value                     | When Needed                  |
| ---------------------------------- | ------------------------- | ---------------------------- |
| `COFOUNDER_PROD_OPENAI_API_KEY`    | OpenAI API key            | If using ChatGPT as fallback |
| `COFOUNDER_PROD_ANTHROPIC_API_KEY` | Anthropic API key         | If using Claude as fallback  |
| `COFOUNDER_PROD_GOOGLE_API_KEY`    | Google API key            | If using Gemini as fallback  |
| `COFOUNDER_PROD_SENTRY_DSN`        | Sentry error tracking URL | If enabling error tracking   |
| `PUBLIC_SITE_PROD_SENTRY_DSN`      | Sentry error tracking URL | If enabling error tracking   |
| `COFOUNDER_PROD_REDIS_HOST`        | Redis server host         | If enabling Redis caching    |
| `COFOUNDER_PROD_REDIS_PASSWORD`    | Redis password            | If enabling Redis caching    |

### Do NOT Create These (Deprecated):

- ❌ `PUBLIC_SITE_PROD_STRAPI_URL` - CMS service removed
- ❌ `OVERSIGHT_PROD_STRAPI_URL` - CMS service removed
- ❌ `PUBLIC_SITE_STAGING_STRAPI_URL` - CMS service removed
- ❌ `OVERSIGHT_STAGING_STRAPI_URL` - CMS service removed

---

## Action 5: Create Staging Environment Secrets (if deploying to staging)

**Location**: GitHub repository → Settings → Environments → staging

| Secret Name                       | Value                               | Notes                        |
| --------------------------------- | ----------------------------------- | ---------------------------- |
| `DATABASE_STAGING_URL`            | Staging database URL                | From Railway or your hosting |
| `COFOUNDER_STAGING_JWT_SECRET`    | Staging JWT secret                  | Different from production    |
| `RAILWAY_STAGING_PROJECT_ID`      | Staging Railway project ID          | Different from production    |
| `PUBLIC_SITE_STAGING_PROJECT_ID`  | Staging Vercel project              | Different from production    |
| `OVERSIGHT_STAGING_PROJECT_ID`    | Staging Vercel project              | Different from production    |
| `PUBLIC_SITE_STAGING_FASTAPI_URL` | `https://agent-staging.railway.app` | Staging agent URL            |
| `PUBLIC_SITE_STAGING_SITE_URL`    | `https://staging.glad-labs.com`     | Staging domain               |
| `OVERSIGHT_STAGING_COFOUNDER_URL` | `https://agent-staging.railway.app` | Staging agent URL            |

---

## Action 6: Update Vercel Project Environment Variables

**Location**: Vercel Dashboard → Project → Settings → Environment Variables

For **Public Site** project, add:

```env
# Production
NEXT_PUBLIC_FASTAPI_URL=https://agent-prod.railway.app
NEXT_PUBLIC_SITE_URL=https://glad-labs.com

# Staging
NEXT_PUBLIC_FASTAPI_URL=https://agent-staging.railway.app
NEXT_PUBLIC_SITE_URL=https://staging.glad-labs.com
```

For **Oversight Hub** project, add:

```env
# Production
REACT_APP_AGENT_URL=https://agent-prod.railway.app

# Staging
REACT_APP_AGENT_URL=https://agent-staging.railway.app
```

---

## Action 7: Verify All Configurations

### Test Development Setup:

```bash
# Navigate to project root
cd /path/to/glad-labs-website

# Check .env.local has all variables
echo "=== Checking .env.local ==="
grep "NEXT_PUBLIC_FASTAPI_URL" .env.local
grep "NEXT_PUBLIC_SITE_URL" .env.local
grep "DATABASE_URL" .env.local
grep "JWT_SECRET" .env.local

# Should all return the defined values
```

### Test Backend Reads Variables:

```bash
# Start backend
cd src/cofounder_agent
python -c "import os; from dotenv import load_dotenv; load_dotenv('../../.env.local'); print(f'DB: {os.getenv(\"DATABASE_URL\")}'); print(f'JWT: {os.getenv(\"JWT_SECRET\")}')"

# Should output your configured values
```

### Test Frontend Sees Variables:

```bash
# In web/public-site directory
echo $NEXT_PUBLIC_FASTAPI_URL
# Should output: http://localhost:8000
```

---

## Action 8: Commit Changes

```bash
# Stage all changes
git add .env.local
git add .github/workflows/deploy-production-with-environments.yml
git add .github/workflows/deploy-staging-with-environments.yml

# Commit with descriptive message
git commit -m "fix: update environment configuration for production deployment

- Add missing NEXT_PUBLIC_FASTAPI_URL and NEXT_PUBLIC_SITE_URL to .env.local
- Add DATABASE_URL and JWT_SECRET to production workflows
- Remove deprecated Strapi CMS references from all workflows
- Update frontend variable names to match code expectations
- Add frontend URL variables to staging deployment"

# Push to feature branch for review
git push origin fix/env-configuration
```

---

## Verification Checklist

### Before Committing:

- [ ] `.env.local` has `NEXT_PUBLIC_FASTAPI_URL=http://localhost:8000`
- [ ] `.env.local` has `NEXT_PUBLIC_SITE_URL=http://localhost:3000`
- [ ] `deploy-production-with-environments.yml` has `DATABASE_URL` secret
- [ ] `deploy-production-with-environments.yml` has `JWT_SECRET` secret
- [ ] `deploy-staging-with-environments.yml` has `DATABASE_URL` secret
- [ ] `deploy-staging-with-environments.yml` has `JWT_SECRET` secret
- [ ] All Strapi CMS references removed from workflows
- [ ] No `PUBLIC_SITE_PROD_STRAPI_URL` in public site deployment
- [ ] No `REACT_APP_STRAPI_URL` in oversight hub deployment

### Before Production Deployment:

- [ ] GitHub secrets created for production environment
- [ ] GitHub secrets created for staging environment
- [ ] Vercel environment variables configured
- [ ] Railway environment variables set
- [ ] Test deployment to staging completes successfully
- [ ] All services (Agent, Public Site, Oversight Hub) accessible in staging
- [ ] Health checks pass

---

## Timeline

| Step                       | Time       | Who                  |
| -------------------------- | ---------- | -------------------- |
| Actions 1-3: Code changes  | 10 min     | Developer            |
| Action 4-5: GitHub secrets | 10 min     | GitHub admin         |
| Action 6: Vercel config    | 5 min      | Vercel project owner |
| Action 7: Verification     | 10 min     | Developer            |
| Action 8: Commit & push    | 5 min      | Developer            |
| **Total**                  | **40 min** |                      |

---

## FAQ

**Q: What if I don't know my Railway project ID?**  
A: Run `railway link` in the project directory, or find it in Railway Dashboard → Project → Settings.

**Q: Can I use the same database for staging and production?**  
A: Not recommended. Create separate databases. But if necessary, use `DATABASE_STAGING_URL: ${{ secrets.DATABASE_PROD_URL }}`.

**Q: What if I'm still using Ollama in production?**  
A: Don't set `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GOOGLE_API_KEY` in production. Backend will use Ollama by default.

**Q: Do I need to restart services after updating .env.local?**  
A: Yes. Stop and restart your dev server (`npm run dev`).

**Q: What happens if a secret isn't defined in GitHub?**  
A: The deployment will fail with "Undefined secrets" error. Check GitHub Actions logs for which secrets are missing.

---

**Document Version**: 1.0  
**Last Updated**: January 11, 2026
