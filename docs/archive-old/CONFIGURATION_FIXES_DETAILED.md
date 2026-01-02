# Environment Configuration Fixes - Implementation Guide

**Priority**: 🔴 **CRITICAL** (GitHub secret exposed)  
**Estimated Time**: 30-45 minutes

---

## STEP 1: Revoke Exposed GitHub Client Secret (URGENT - 5 min)

Your GitHub Client Secret is exposed in `.env.local`:

```
GITHUB_CLIENT_SECRET=a2b98d4eb47ba4b657b214a1ad494cb692c111c7
```

### Action:

1. **Go to GitHub OAuth App Settings**
   - URL: https://github.com/settings/developers
   - Click on your OAuth app (look for "Glad Labs" or similar)

2. **Delete the exposed secret**
   - Scroll to "Client secrets"
   - Find the secret: `a2b98d4eb47ba4...`
   - Click the trash icon to delete it

3. **Generate a new secret**
   - Click "Generate a new client secret"
   - Copy the new secret

4. **Update your `.env.local`**

   ```bash
   # Replace:
   GITHUB_CLIENT_SECRET=a2b98d4eb47ba4b657b214a1ad494cb692c111c7

   # With:
   GITHUB_CLIENT_SECRET=<new-secret-from-github>
   ```

5. **Update root `.env.local`**
   ```bash
   nano .env.local
   # Find: GITHUB_CLIENT_SECRET=
   # Replace with new secret
   # Save: Ctrl+O, Enter, Ctrl+X
   ```

---

## STEP 2: Generate Unique JWT Secret (3 min)

Your JWT secret is using the default development value:

```
JWT_SECRET=dev-jwt-secret-change-in-production-to-random-64-chars
```

### Action:

1. **Generate random secret**

   ```bash
   openssl rand -base64 32
   ```

   Output example:

   ```
   aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890+/=
   ```

2. **Update `.env.local` (root)**

   ```bash
   # Replace:
   JWT_SECRET=dev-jwt-secret-change-in-production-to-random-64-chars

   # With:
   JWT_SECRET=<paste-output-from-above>
   ```

3. **Verify it's updated**
   ```bash
   grep JWT_SECRET .env.local
   ```

---

## STEP 3: Fix Oversight Hub Configuration (5 min)

Your oversight-hub has issues:

1. **Duplicate `REACT_APP_API_URL`** - appears twice
2. **Mock auth enabled** - should be false for production testing
3. **Different GitHub Client ID** than root (clarify which is correct)

### Action:

**File**: `web/oversight-hub/.env.local`

**Remove this line** (it's duplicated):

```dotenv
REACT_APP_API_URL=http://localhost:8000
```

Your file should have only ONE:

```dotenv
REACT_APP_API_URL=http://localhost:8000
REACT_APP_API_TIMEOUT=10000
```

**Note about GitHub Client IDs**:

- Root has: `Ov23liMUM5PuVfu7F4kB`
- Oversight Hub has: `Ov23liAcCMWrS5DihFnl`

**Choose ONE GitHub app and update both places to match:**

Option A: Use the root one (`Ov23liMUM5PuVfu7F4kB`)

```bash
# Update oversight-hub/.env.local:
REACT_APP_GITHUB_CLIENT_ID=Ov23liMUM5PuVfu7F4kB
```

Option B: Use the oversight hub one (`Ov23liAcCMWrS5DihFnl`)

```bash
# Update root/.env.local:
GITHUB_CLIENT_ID=Ov23liAcCMWrS5DihFnl
```

**Recommendation**: Use the root one (simpler, more secure)

---

## STEP 4: Create Production Configuration Files (10 min)

Create `.env.production` files for production deployment.

### File 1: `.env.production` (Root)

**Create file**: `c:\Users\mattm\glad-labs-website\.env.production`

```dotenv
# ==================================
# PRODUCTION ENVIRONMENT
# ==================================
NODE_ENV=production
ENVIRONMENT=production
LOG_LEVEL=INFO
DEBUG=false

# ==================================
# PORTS
# ==================================
COFOUNDER_AGENT_PORT=8000
PUBLIC_SITE_PORT=3000
OVERSIGHT_HUB_PORT=3001
POSTGRES_PORT=5432

# ==================================
# DATABASE (Update with your production DB)
# ==================================
DATABASE_URL=postgresql://user:password@prod-db-host:5432/glad_labs_prod
DATABASE_HOST=prod-db-host
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_prod
DATABASE_USER=prod-user
DATABASE_PASSWORD=<secure-password>

# ==================================
# AI MODEL SELECTION
# ==================================
# Use Ollama in production or cloud API
OLLAMA_HOST=http://ollama:11434
OLLAMA_MODEL=mistral:latest

# ==================================
# SECURITY & AUTHENTICATION
# ==================================
# Generate new secret: openssl rand -base64 32
JWT_SECRET=<generate-new-per-environment>
JWT_EXPIRY_MINUTES=15

# CORS: Restrict to your production domains
ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com,https://oversight.yourdomain.com

# ==================================
# GITHUB OAUTH (Production App)
# ==================================
# Use DIFFERENT GitHub app for production!
# Go to: https://github.com/settings/developers
# Create new OAuth app with production redirect URL
GITHUB_CLIENT_ID=<production-github-client-id>
GITHUB_CLIENT_SECRET=<production-github-client-secret>
GITHUB_REDIRECT_URI=https://oversight.yourdomain.com/auth/callback

# ==================================
# RATE LIMITING
# ==================================
RATE_LIMIT_PER_MINUTE=100
API_TIMEOUT=30000
API_RETRY_ATTEMPTS=2

# ==================================
# REDIS (Optional, for production caching)
# ==================================
# REDIS_HOST=redis-host
# REDIS_PORT=6379
# REDIS_PASSWORD=<secure-password>
# REDIS_DB=0

# ==================================
# OBSERVABILITY
# ==================================
# Optional: Enable for production monitoring
# SENTRY_DSN=https://your-sentry-key@sentry.io/your-project-id

# ==================================
# FEATURE FLAGS
# ==================================
ENABLE_DEBUG_LOGS=false
ENABLE_ANALYTICS=true
ENABLE_ERROR_REPORTING=true
ENABLE_MCP_SERVER=true
ENABLE_MEMORY_SYSTEM=true

# ==================================
# OTHER
# ==================================
# Replace with your Pexels API key if used
PEXELS_API_KEY=<your-pexels-api-key>

# ==================================
# FRONTEND CONFIGURATION
# ==================================
NEXT_PUBLIC_API_BASE_URL=https://api.yourdomain.com
NEXT_PUBLIC_COFOUNDER_AGENT_URL=https://api.yourdomain.com
REACT_APP_API_URL=https://api.yourdomain.com
REACT_APP_LOG_LEVEL=info
```

### File 2: `.env.production` (web/oversight-hub/)

**Create file**: `c:\Users\mattm\glad-labs-website\web\oversight-hub\.env.production`

```dotenv
# ==================================
# Oversight Hub - Production Configuration
# ==================================

# ==================================
# API Configuration
# ==================================
REACT_APP_API_URL=https://api.yourdomain.com

# ==================================
# GitHub OAuth Configuration
# ==================================
# Use SAME production GitHub app as root
REACT_APP_GITHUB_CLIENT_ID=<production-github-client-id>
REACT_APP_GITHUB_REDIRECT_URI=https://oversight.yourdomain.com/auth/callback

# ==================================
# Authentication
# ==================================
# MUST BE FALSE IN PRODUCTION!
REACT_APP_USE_MOCK_AUTH=false

# ==================================
# API Configuration
# ==================================
REACT_APP_API_TIMEOUT=30000

# ==================================
# Feature Flags
# ==================================
REACT_APP_ENABLE_DARK_MODE=true
REACT_APP_ENABLE_REAL_TIME_UPDATES=true
REACT_APP_ENABLE_ANALYTICS=true

# ==================================
# UI Configuration
# ==================================
REACT_APP_THEME_COLOR=blue
REACT_APP_AUTO_REFRESH_INTERVAL=5000

# ==================================
# Logging & Monitoring
# ==================================
REACT_APP_SENTRY_DSN=https://your-sentry-key@sentry.io/your-project-id
REACT_APP_LOG_LEVEL=info

# ==================================
# Production Settings
# ==================================
REACT_APP_DEBUG_MODE=false
```

### File 3: `.env.production` (web/public-site/)

**Create file**: `c:\Users\mattm\glad-labs-website\web\public-site\.env.production`

```dotenv
# ==================================
# Public Site - Production Configuration
# ==================================

# ==================================
# API Configuration
# ==================================
NEXT_PUBLIC_FASTAPI_URL=https://api.yourdomain.com
NEXT_PUBLIC_SITE_URL=https://yourdomain.com

# ==================================
# Analytics (Optional)
# ==================================
# Get this from Google Analytics 4
NEXT_PUBLIC_GA_ID=G-YOUR_GA_ID

# ==================================
# Google AdSense (Optional)
# ==================================
# Get this from Google AdSense after approval
NEXT_PUBLIC_ADSENSE_CLIENT_ID=ca-pub-YOUR_ADSENSE_ID
```

---

## STEP 5: Remove Duplicate Variables (3 min)

Your `web/oversight-hub/.env.local` has duplicates.

**Current content has**:

```dotenv
# Line 9
REACT_APP_API_URL=http://localhost:8000

# Line 32 (DUPLICATE!)
REACT_APP_API_URL=http://localhost:8000
```

**Fix**: Delete the duplicate (line 32)

**Updated file should look like**:

```dotenv
# ==================================
# Oversight Hub - Development Configuration
# ==================================

# ==================================
# Cofounder Agent API Configuration
# ==================================
REACT_APP_API_URL=http://localhost:8000

# ==================================
# GitHub OAuth Configuration
# ==================================
REACT_APP_GITHUB_CLIENT_ID=Ov23liMUM5PuVfu7F4kB  # Match root value
REACT_APP_GITHUB_REDIRECT_URI=http://localhost:3001/auth/callback

# ==================================
# Mock Authentication
# ==================================
REACT_APP_USE_MOCK_AUTH=true

# ==================================
# Feature Flags
# ==================================
REACT_APP_ENABLE_DARK_MODE=true
REACT_APP_ENABLE_REAL_TIME_UPDATES=true
REACT_APP_ENABLE_ANALYTICS=true

# ==================================
# UI Configuration
# ==================================
REACT_APP_THEME_COLOR=blue
REACT_APP_AUTO_REFRESH_INTERVAL=5000

# ==================================
# Logging & Monitoring
# ==================================
REACT_APP_SENTRY_DSN=
REACT_APP_LOG_LEVEL=debug

# ==================================
# Development
# ==================================
REACT_APP_DEBUG_MODE=true
REACT_APP_API_TIMEOUT=10000
```

---

## STEP 6: Verify `.gitignore` (Already Done) ✅

Your `.gitignore` already correctly includes:

```ignore
.env
.env.local
.env.*.local
.env.production
.env.development
.env.test
.env.staging
```

No changes needed! ✅

---

## STEP 7: Verify Configuration (5 min)

**Run these commands to verify your setup**:

```bash
# Check root .env.local
echo "=== Root .env.local ==="
grep -E "JWT_SECRET|GITHUB_CLIENT" .env.local

# Check oversight-hub config
echo "=== Oversight Hub .env.local ==="
grep -E "REACT_APP_API_URL|REACT_APP_GITHUB" web/oversight-hub/.env.local

# Check public-site config
echo "=== Public Site .env.local ==="
grep -E "NEXT_PUBLIC_FASTAPI_URL" web/public-site/.env.local

# Verify .gitignore
echo "=== .gitignore check ==="
grep ".env" .gitignore
```

Expected output:

```
=== Root .env.local ===
JWT_SECRET=<your-generated-secret>
GITHUB_CLIENT_ID=Ov23liMUM5PuVfu7F4kB
GITHUB_CLIENT_SECRET=<new-secret>

=== Oversight Hub .env.local ===
REACT_APP_API_URL=http://localhost:8000
REACT_APP_GITHUB_CLIENT_ID=Ov23liMUM5PuVfu7F4kB

=== Public Site .env.local ===
NEXT_PUBLIC_FASTAPI_URL=http://localhost:8000

=== .gitignore check ===
.env
.env.local
.env.*.local
(etc.)
```

---

## STEP 8: Test Your Setup

**Test in development**:

```bash
# Start all services
npm run dev

# Check backend starts without errors
curl http://localhost:8000/health

# Check frontend loads
open http://localhost:3001

# Check mock auth works (REACT_APP_USE_MOCK_AUTH=true)
# Click login button - should show "Sign in (Mock)"
```

**Test OAuth**:

```bash
# Create test GitHub OAuth app (if not done)
# Go to: https://github.com/settings/developers
# New OAuth app with:
#   Redirect URL: http://localhost:3001/auth/callback
#   Use the Client ID from your .env.local

# Set REACT_APP_USE_MOCK_AUTH=false in oversight-hub/.env.local
npm run dev

# Click login - should show "Sign in with GitHub"
# Should redirect to GitHub.com
```

---

## STEP 9: Prepare for Production Deployment

**Before deploying to production**:

1. **Update `.env.production` files with real values**
   - Replace `yourdomain.com` with your actual domain
   - Replace database credentials
   - Replace GitHub OAuth app credentials (PRODUCTION app)
   - Replace JWT secret (generate new: `openssl rand -base64 32`)
   - Replace API keys (Pexels, Google Analytics, etc.)

2. **Create GitHub OAuth app for production**
   - Go to: https://github.com/settings/developers
   - New OAuth app with production redirect URL
   - Copy Client ID and Client Secret
   - Add to `.env.production`

3. **Set up secrets in deployment platform**
   - **Vercel** (for web/oversight-hub and web/public-site)
     - Go to Project Settings → Environment Variables
     - Add all variables from `.env.production` files
   - **Railway** (for backend)
     - Go to Service Settings → Variables
     - Add all variables from root `.env.production`

4. **Verify secrets are NOT in code**
   ```bash
   git status
   # Should NOT show .env.local or .env.production changes
   ```

---

## Summary of Changes

| File                                | Change                             | Priority    |
| ----------------------------------- | ---------------------------------- | ----------- |
| `.env.local` (root)                 | Update GitHub secret               | 🔴 CRITICAL |
| `.env.local` (root)                 | Update JWT secret                  | ⚠️ HIGH     |
| `.env.local` (root)                 | Decide on GitHub Client ID         | ⚠️ MEDIUM   |
| `web/oversight-hub/.env.local`      | Remove duplicate REACT_APP_API_URL | ⚠️ MEDIUM   |
| `web/oversight-hub/.env.local`      | Match GitHub Client ID with root   | ⚠️ MEDIUM   |
| `.env.production` (root)            | Create                             | ⚠️ MEDIUM   |
| `web/oversight-hub/.env.production` | Create                             | ⚠️ MEDIUM   |
| `web/public-site/.env.production`   | Create                             | ⚠️ MEDIUM   |

---

## Verification Checklist

- [ ] GitHub Client Secret revoked
- [ ] New GitHub Client Secret generated and added to `.env.local`
- [ ] JWT secret replaced with random 64-char string
- [ ] Duplicate `REACT_APP_API_URL` removed from oversight-hub/.env.local
- [ ] GitHub Client IDs match across root and oversight-hub
- [ ] `.env.production` files created
- [ ] Production values filled in (domain, database, GitHub app)
- [ ] `.env.local` and `.env.production` are in `.gitignore`
- [ ] Services start without errors
- [ ] OAuth flow works in development

---

## Files to Backup (Optional)

```bash
# Backup current config before making changes
cp .env.local .env.local.backup
cp web/oversight-hub/.env.local web/oversight-hub/.env.local.backup
cp web/public-site/.env.local web/public-site/.env.local.backup
```

---

**Estimated Total Time**: 30-45 minutes

**After Completion**: Your monorepo will be properly configured with:
✅ Secure secrets management  
✅ Production-ready configuration  
✅ No exposed credentials  
✅ Clear separation of environments

---

**Next Steps After This Guide**:

1. Follow the Glad Labs copilot instructions for deployment
2. Review: OAUTH_PRODUCTION_READINESS_REVIEW.md
3. Deploy to Vercel (frontend) + Railway (backend)
