# Environment Configuration - Quick Reference

**Status**: 🔴 3 CRITICAL issues found  
**Fix Time**: ~40 minutes

---

## Critical Issues Found

| Issue                               | Severity    | Location       | Impact                                 |
| ----------------------------------- | ----------- | -------------- | -------------------------------------- |
| Missing `NEXT_PUBLIC_FASTAPI_URL`   | 🔴 CRITICAL | `.env.local`   | Frontend can't reach API in production |
| Missing `DATABASE_URL` in workflows | 🔴 CRITICAL | GitHub Actions | Deployment fails - no DB connection    |
| Missing `JWT_SECRET` in workflows   | 🔴 CRITICAL | GitHub Actions | Authentication fails in production     |
| Strapi CMS still referenced         | 🔴 CRITICAL | Workflows      | Build fails on undefined secrets       |
| Wrong frontend variable names       | 🟡 HIGH     | Workflows      | Analytics/URLs don't work              |

---

## What Needs to Be Added

### .env.local (Local Development)

```env
# Add these two lines:
NEXT_PUBLIC_FASTAPI_URL=http://localhost:8000
NEXT_PUBLIC_SITE_URL=http://localhost:3000
```

### GitHub Secrets (Production)

```yaml
# MUST CREATE (for deployment):
DATABASE_PROD_URL = postgresql://...
COFOUNDER_PROD_JWT_SECRET = <random-64-char>
RAILWAY_TOKEN = <your-railway-token>
VERCEL_TOKEN = <your-vercel-token>
VERCEL_ORG_ID = <your-org-id>

# MUST SET (URLs):
PUBLIC_SITE_PROD_FASTAPI_URL = https://agent-prod.railway.app
PUBLIC_SITE_PROD_SITE_URL = https://glad-labs.com
OVERSIGHT_PROD_COFOUNDER_URL = https://agent-prod.railway.app

# REMOVE (CMS is gone):
PUBLIC_SITE_PROD_STRAPI_URL ❌
OVERSIGHT_PROD_STRAPI_URL ❌
```

### Workflows (Both Staging & Production)

**Remove**:

```yaml
NEXT_PUBLIC_STRAPI_URL: ${{ secrets... }}  ❌
REACT_APP_STRAPI_URL: ${{ secrets... }}    ❌
```

**Add**:

```yaml
DATABASE_URL: ${{ secrets.DATABASE_PROD_URL }}
JWT_SECRET: ${{ secrets.COFOUNDER_PROD_JWT_SECRET }}
NEXT_PUBLIC_FASTAPI_URL: ${{ secrets... }}
NEXT_PUBLIC_SITE_URL: ${{ secrets... }}
```

---

## Files to Modify

1. **`.env.local`** - Add 2 variables
2. **`.github/workflows/deploy-production-with-environments.yml`** - Fix 5 sections
3. **`.github/workflows/deploy-staging-with-environments.yml`** - Fix 5 sections
4. **GitHub Settings** - Create environment secrets
5. **Vercel Dashboard** - Add environment variables

---

## Current Environment Variable Status

### ✅ Properly Configured

- Database connection
- JWT security
- CORS settings
- AI model selection (Ollama)
- Rate limiting
- Port configuration

### ❌ Missing or Wrong

- `NEXT_PUBLIC_FASTAPI_URL` not in `.env.local`
- `NEXT_PUBLIC_SITE_URL` not in `.env.local`
- `DATABASE_URL` not passed to production workflows
- `JWT_SECRET` not passed to production workflows
- Strapi URLs still in workflows (service removed)

### ⚠️ Optional (Not Blocking)

- LLM API keys (using Ollama, fallbacks optional)
- OAuth credentials (not configured)
- Error tracking (Sentry)
- Analytics (Google Analytics)
- Email service (not used)

---

## Backend Environment Variables Used

### Required (Currently OK ✅)

- `DATABASE_URL` - PostgreSQL connection
- `JWT_SECRET` - Token signing
- `OLLAMA_HOST` - Local AI server
- `ENVIRONMENT` - Dev/staging/prod mode

### Optional (Not Set)

- `OPENAI_API_KEY` - ChatGPT
- `ANTHROPIC_API_KEY` - Claude
- `GOOGLE_API_KEY` - Gemini
- `SERPER_API_KEY` - Web search
- `TWITTER_BEARER_TOKEN` - Twitter
- `PEXELS_API_KEY` - Images
- `REDIS_URL` - Caching (prod only)
- `SENTRY_DSN` - Error tracking (prod only)

---

## Frontend Environment Variables Used

### Public Site (Next.js)

**Currently Used**:

- `NEXT_PUBLIC_FASTAPI_URL` ❌ MISSING
- `NEXT_PUBLIC_SITE_URL` ❌ MISSING
- `NEXT_PUBLIC_GA_ID` - Optional

**Currently Set**:

- `NEXT_PUBLIC_API_BASE_URL` ✅
- `NEXT_PUBLIC_COFOUNDER_AGENT_URL` ✅

### Oversight Hub (React)

**Currently Used**:

- `REACT_APP_API_URL` ✅
- `REACT_APP_LOG_LEVEL` ✅
- `REACT_APP_AGENT_URL` - From workflows

---

## Deployment Secrets Needed

### Railway (Backend Deployment)

```
RAILWAY_TOKEN              # For CLI auth
RAILWAY_PROD_PROJECT_ID    # Which project
DATABASE_PROD_URL          # Where to connect
JWT_SECRET                 # Auth key
(Optional: OpenAI, Anthropic, Google keys for LLM fallbacks)
```

### Vercel (Frontend Deployment)

```
VERCEL_TOKEN              # For CLI auth
VERCEL_ORG_ID             # Which org
NEXT_PUBLIC_FASTAPI_URL   # Backend URL
NEXT_PUBLIC_SITE_URL      # Website URL
(Optional: GA_ID for analytics)
```

---

## Quick Diagnosis Commands

```bash
# Check if .env.local has required variables
grep "DATABASE_URL" .env.local && echo "✅ DB URL set" || echo "❌ DB URL missing"
grep "JWT_SECRET" .env.local && echo "✅ JWT secret set" || echo "❌ JWT secret missing"
grep "NEXT_PUBLIC_FASTAPI_URL" .env.local && echo "✅ Frontend API set" || echo "❌ Frontend API missing"

# Check workflows for Strapi references
grep -r "STRAPI" .github/workflows/ && echo "❌ Strapi refs still in workflows" || echo "✅ Strapi removed from workflows"

# Count secrets in production workflows
echo "Production workflow secrets:"
grep -c "secrets\." .github/workflows/deploy-production-with-environments.yml
```

---

## Priority Order

1. **RIGHT NOW**: Add `NEXT_PUBLIC_FASTAPI_URL` and `NEXT_PUBLIC_SITE_URL` to `.env.local` (2 lines)
2. **BEFORE TESTING**: Remove Strapi from all 3 workflow files (10 lines total)
3. **BEFORE PRODUCTION**: Add DATABASE_URL and JWT_SECRET to workflows (4 lines)
4. **BEFORE DEPLOYING**: Create GitHub secrets and Vercel environment variables

---

## Links to Detailed Docs

- **Full Audit**: See `ENV_CONFIGURATION_AUDIT.md` for complete analysis
- **Action Steps**: See `ENV_CONFIGURATION_ACTION_ITEMS.md` for detailed fixes
- **Setup Guide**: See `docs/01-SETUP_AND_OVERVIEW.md` for initial setup

---

## Key Takeaways

**The good news**: Your local development setup is 95% correct. You just need to add 2 missing variables to `.env.local`.

**The bad news**: Your production workflows have critical missing configurations that will cause deployment failures. They reference removed services (Strapi) and missing secrets.

**The urgent fixes** (30 minutes):

1. Add 2 variables to `.env.local`
2. Remove Strapi references from workflows
3. Add DATABASE_URL and JWT_SECRET to workflows
4. Create GitHub secrets

**After these fixes**, deployment to production should work correctly.

---

**Last Updated**: January 11, 2026  
**Status**: Ready for implementation
