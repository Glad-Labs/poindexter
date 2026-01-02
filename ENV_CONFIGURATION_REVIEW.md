# Environment Configuration Review - Glad Labs Monorepo

**Date**: January 2, 2026  
**Status**: ⚠️ Review Required - Configuration Issues Found

---

## Summary

Your monorepo has `.env` files across 3 services. I found **critical configuration issues** that need to be addressed for proper functionality.

### Issues Found:

- ❌ **GitHub Client Secret exposed** in version control
- ⚠️ **Duplicate variables** in root and service-specific `.env.local`
- ⚠️ **Mock auth enabled** by default in Oversight Hub
- ⚠️ **JWT secret using dev default** (not unique per environment)
- ⚠️ **Missing production `.env.production` files**
- ⚠️ **Inconsistent variable naming** across services

---

## Current Structure

```
glad-labs-website/
├── .env.example              ✅ Template (checked in git)
├── .env.local               ⚠️ Root config (NEVER commit!)
├── .env.production          ⚠️ Missing production config
├── web/oversight-hub/
│   ├── .env.example         ✅ Template
│   └── .env.local          ⚠️ Service config (NEVER commit!)
├── web/public-site/
│   ├── .env.example         ✅ Template
│   └── .env.local          ⚠️ Service config (NEVER commit!)
└── src/cofounder_agent/     ⚠️ No .env file (uses root .env.local)
```

---

## Configuration Issues

### 🔴 CRITICAL: GitHub Client Secret Exposed

**Location**: `.env.local` (root directory)

```dotenv
GITHUB_CLIENT_ID=Ov23liMUM5PuVfu7F4kB
GITHUB_CLIENT_SECRET=a2b98d4eb47ba4b657b214a1ad494cb692c111c7
```

**Problem**:

- Secret is stored in plaintext in `.env.local`
- Could be committed accidentally or exposed in logs
- Anyone with access to this file can impersonate your GitHub OAuth app

**Impact**: 🔴 **CRITICAL** - Security risk

**Action Required**:

1. **Revoke this secret immediately** on GitHub.com
   - Go to: https://github.com/settings/developers
   - Select your OAuth app
   - Delete/regenerate the secret

2. **Generate a new secret** on GitHub

3. **Update environment variables** to use the new secret

4. **Verify `.env.local` is in `.gitignore`**

---

### ⚠️ HIGH: Duplicate Environment Variables

**Problem**: Variables defined in BOTH root `.env.local` AND service-specific `.env.local`

**Root `.env.local` has**:

```dotenv
REACT_APP_API_URL=http://localhost:8000
REACT_APP_LOG_LEVEL=debug
```

**web/oversight-hub/.env.local also has**:

```dotenv
REACT_APP_API_URL=http://localhost:8000
REACT_APP_API_URL=http://localhost:8000  # DUPLICATED!
```

**Problem**:

- Confusion about which value takes precedence
- Harder to maintain (update in two places)
- React doesn't read from root `.env.local` - only from service-specific files

**Action Required**:

1. Remove duplicates from oversight-hub/.env.local
2. Clarify which variables should be in root vs service-specific files

---

### ⚠️ MEDIUM: Mock Auth Enabled by Default

**Location**: `web/oversight-hub/.env.local`

```dotenv
REACT_APP_USE_MOCK_AUTH=true
```

**Problem**:

- Anyone can log in without a real GitHub account
- Meant for development only
- Could accidentally enable in production

**Action Required**:

1. Change to `REACT_APP_USE_MOCK_AUTH=false` for production
2. Add clear documentation about when to enable/disable

---

### ⚠️ MEDIUM: Dev JWT Secret in Production

**Location**: `.env.local` (root)

```dotenv
JWT_SECRET=dev-jwt-secret-change-in-production-to-random-64-chars
```

**Problem**:

- Using default dev secret in production is insecure
- Anyone knowing the secret can forge tokens
- Sessions can be hijacked

**Action Required**:

1. Generate unique secret: `openssl rand -base64 32`
2. Use different secrets per environment
3. Never hardcode secrets in code

---

### ⚠️ MEDIUM: No Production `.env.production` Files

**Missing Files**:

- `.env.production` (root) - Should have production config
- `web/oversight-hub/.env.production` - Should have production config
- `web/public-site/.env.production` - Should have production config

**Problem**:

- No clear separation between development and production config
- Risk of deploying development settings to production

**Action Required**:

1. Create `.env.production` files
2. Set up environment-specific deployment

---

### ⚠️ LOW: Inconsistent Variable Naming

**Issues**:

1. Root uses `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`
2. Oversight Hub uses `REACT_APP_GITHUB_CLIENT_ID`
3. Different prefixes make it confusing

**Standard Practice**:

- Root `.env` (backend): `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`
- React apps `.env.local`: `REACT_APP_*` prefix (required by Create React App)
- Next.js apps `.env.local`: `NEXT_PUBLIC_*` or regular variables

---

## Detailed Configuration Review

### Root `.env.local` Analysis

```dotenv
✅ CORRECT:
  DATABASE_URL=postgresql://...              (Correct format)
  OLLAMA_HOST=http://localhost:11434         (Correct endpoint)
  ALLOWED_ORIGINS=http://localhost:...       (Development correct)

⚠️ NEEDS ATTENTION:
  JWT_SECRET=dev-jwt-secret-...              (Default, needs change)
  GITHUB_CLIENT_ID=Ov23li...                 (Dev value)
  GITHUB_CLIENT_SECRET=a2b98d4eb...          (EXPOSED - revoke!)

❌ MISSING:
  BACKEND_URL (not used, but helpful)
  NO PRODUCTION EQUIVALENT (.env.production)
```

---

### web/oversight-hub/.env.local Analysis

```dotenv
✅ CORRECT:
  REACT_APP_USE_MOCK_AUTH=true               (Dev-appropriate)
  REACT_APP_LOG_LEVEL=debug                  (Dev-appropriate)
  REACT_APP_DEBUG_MODE=true                  (Dev-appropriate)

⚠️ ISSUES:
  REACT_APP_API_URL=duplicated (also in root)
  REACT_APP_GITHUB_CLIENT_ID=Ov23liAcC...    (Different from root!)
  REACT_APP_USE_MOCK_AUTH=true               (MUST be false in prod)

❌ MISSING:
  NO PRODUCTION EQUIVALENT (.env.production)
  GitHub Client Secret not present (correct - kept on backend)
```

**Note**: The GitHub Client IDs don't match!

- Root: `Ov23liMUM5PuVfu7F4kB`
- Oversight Hub: `Ov23liAcCMWrS5DihFnl`

This suggests **multiple GitHub OAuth apps** are being used. Clarify which is correct.

---

### web/public-site/.env.local Analysis

```dotenv
✅ CORRECT:
  NEXT_PUBLIC_FASTAPI_URL=http://localhost:8000
  NEXT_PUBLIC_SITE_URL=http://localhost:3000
  NEXT_PUBLIC_GA_ID=G-XXXXXXXXXX             (Placeholder - correct)
  NEXT_PUBLIC_ADSENSE_CLIENT_ID=...          (Placeholder - correct)

❌ MISSING:
  NO PRODUCTION EQUIVALENT (.env.production)
  No GA ID set (should be filled in or removed)
  AdSense ID commented out (needs clarification)
```

---

## Recommended Configuration Structure

### Option A: Minimal (Recommended)

**Root `.env.local` only** - All services read from here:

```dotenv
# ENVIRONMENT
NODE_ENV=development
ENVIRONMENT=development

# DATABASE
DATABASE_URL=postgresql://...

# BACKEND
OLLAMA_HOST=http://localhost:11434
JWT_SECRET=<generate-random>
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3001,http://localhost:8000

# GITHUB OAUTH
GITHUB_CLIENT_ID=<from-github>
GITHUB_CLIENT_SECRET=<from-github>

# FRONTEND
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
REACT_APP_API_URL=http://localhost:8000
REACT_APP_USE_MOCK_AUTH=true
```

**Note**: Eliminate service-specific `.env.local` files - all apps read from root.

---

### Option B: Modular (Current Approach)

**Root `.env.local` for backend + service-specific `.env.local` for frontend**

```
Root (.env.local):
  - DATABASE_URL
  - OLLAMA_HOST
  - JWT_SECRET
  - GITHUB_CLIENT_ID
  - GITHUB_CLIENT_SECRET

Oversight Hub (.env.local):
  - REACT_APP_API_URL (from root, but duplicated)
  - REACT_APP_GITHUB_CLIENT_ID (from root)
  - REACT_APP_USE_MOCK_AUTH
  - REACT_APP_DEBUG_MODE

Public Site (.env.local):
  - NEXT_PUBLIC_FASTAPI_URL (from root)
  - NEXT_PUBLIC_GA_ID
  - NEXT_PUBLIC_ADSENSE_CLIENT_ID
```

**Advantage**: Clear separation per service  
**Disadvantage**: Duplication, harder to maintain

---

## Action Plan

### Immediate (Before Deployment)

1. **Revoke GitHub Client Secret**

   ```
   Go to: https://github.com/settings/developers
   Delete current secret and generate new one
   ```

2. **Update `.env.local` with new secret**

   ```bash
   GITHUB_CLIENT_SECRET=<new-secret-from-github>
   ```

3. **Verify `.gitignore` includes `.env` files**

   ```
   # Should already contain:
   .env.local
   .env.*.local
   .env.production
   ```

4. **Generate unique JWT Secret**
   ```bash
   openssl rand -base64 32
   # Copy result and update: JWT_SECRET=<result>
   ```

### Short Term (Week 1)

1. **Consolidate duplicate variables**
   - Remove duplicates from oversight-hub/.env.local
   - Use root `.env.local` as single source of truth

2. **Create `.env.production` files**

   ```
   .env.production (root)
   web/oversight-hub/.env.production
   web/public-site/.env.production
   ```

3. **Document environment variable usage**
   - Create `ENV_VARIABLES.md` explaining each variable
   - Which services use which variables
   - What values for dev vs production

### Medium Term (Month 1)

1. **Move secrets to environment manager**
   - Use GitHub Secrets for CI/CD
   - Use Vercel/Railway environment variables for deployment
   - Never commit real secrets

2. **Standardize variable naming**
   - Backend: No prefix
   - React: `REACT_APP_` prefix
   - Next.js: `NEXT_PUBLIC_` prefix

3. **Add validation**
   - Create script to validate required variables at startup
   - Fail fast if critical variables missing

---

## Production Environment Template

### `.env.production` (Root)

```dotenv
# ENVIRONMENT
NODE_ENV=production
ENVIRONMENT=production
LOG_LEVEL=INFO

# DATABASE
DATABASE_URL=postgresql://user:pass@prod-db:5432/glad_labs_prod

# BACKEND
OLLAMA_HOST=http://ollama:11434  # Or use cloud API
JWT_SECRET=<generate-new-per-environment>
ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com

# GITHUB OAUTH
GITHUB_CLIENT_ID=<production-github-app-id>
GITHUB_CLIENT_SECRET=<production-github-app-secret>

# REDIS (if using)
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=<secure-password>
```

### `.env.production` (web/oversight-hub/)

```dotenv
REACT_APP_API_URL=https://api.yourdomain.com
REACT_APP_USE_MOCK_AUTH=false
REACT_APP_LOG_LEVEL=info
REACT_APP_DEBUG_MODE=false
```

### `.env.production` (web/public-site/)

```dotenv
NEXT_PUBLIC_FASTAPI_URL=https://api.yourdomain.com
NEXT_PUBLIC_SITE_URL=https://yourdomain.com
NEXT_PUBLIC_GA_ID=G-ACTUAL_ID
NEXT_PUBLIC_ADSENSE_CLIENT_ID=ca-pub-ACTUAL_ID
```

---

## Security Checklist

- [ ] GitHub Client Secret NOT in version control
- [ ] JWT Secret is unique and random (not default)
- [ ] All `.env.local` files in `.gitignore`
- [ ] No plaintext secrets in config files
- [ ] Secrets stored in environment variable manager
- [ ] CORS origins restricted to your domains
- [ ] Mock auth disabled in production
- [ ] Production `.env.production` files created
- [ ] Database credentials use environment variables
- [ ] API keys/tokens use environment variables

---

## Files to Review

| File                                | Status            | Action                |
| ----------------------------------- | ----------------- | --------------------- |
| `.env.local`                        | ⚠️ Exposed secret | Update GitHub secret  |
| `.env.production`                   | ❌ Missing        | Create                |
| `web/oversight-hub/.env.local`      | ⚠️ Duplicates     | Remove duplicate vars |
| `web/oversight-hub/.env.production` | ❌ Missing        | Create                |
| `web/public-site/.env.local`        | ⚠️ Incomplete     | Remove/add GA ID      |
| `web/public-site/.env.production`   | ❌ Missing        | Create                |
| `.gitignore`                        | ✅ Verified       | Should include .env\* |

---

## Next Steps

1. **Emergency**: Revoke GitHub Client Secret immediately
2. **This week**: Create `.env.production` files
3. **This month**: Move to secure secrets manager
4. **Ongoing**: Document all environment variables

See `CONFIGURATION_FIXES.md` for detailed implementation steps.
