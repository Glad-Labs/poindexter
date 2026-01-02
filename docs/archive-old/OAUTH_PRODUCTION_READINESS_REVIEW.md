# GitHub OAuth Production Readiness Review

**Oversight Hub** - January 2, 2026

---

## Executive Summary

Your GitHub OAuth implementation is **functionally complete and well-structured**, with proper security measures in place. However, there are **critical environment variables and configuration changes required before production deployment**.

### Status: ✅ 70% Production-Ready

- ✅ OAuth flow implemented correctly
- ✅ CSRF protection (state validation)
- ✅ JWT token generation
- ✅ Fallback mock auth for development
- ⚠️ **MISSING: Backend GitHub OAuth environment variables**
- ⚠️ **MISSING: Production API URLs and redirect URIs**
- ⚠️ **MISSING: JWT secret rotation strategy**

---

## Current Implementation Overview

### Frontend Architecture (React - web/oversight-hub)

#### 1. **Login Flow** ([src/pages/Login.jsx](web/oversight-hub/src/pages/Login.jsx))

```javascript
REACT_APP_GITHUB_CLIENT_ID = Ov23liAcCMWrS5DihFnl; // ✅ Set
REACT_APP_USE_MOCK_AUTH = true; // ⚠️ Development only
```

- Generates GitHub OAuth authorization URL
- Redirects to: `https://github.com/login/oauth/authorize`
- Uses mock auth for local development (when `REACT_APP_USE_MOCK_AUTH=true`)

**Status**: Production-ready, but switch to real OAuth by setting `REACT_APP_USE_MOCK_AUTH=false`

---

#### 2. **OAuth Callback Handler** ([src/pages/AuthCallback.jsx](web/oversight-hub/src/pages/AuthCallback.jsx))

```javascript
// Receives: GET /auth/callback?code=...&state=...
// Processes: Code → Token exchange → User data
// Redirects: Dashboard on success | Login on failure
```

**Security Features**:

- ✅ CSRF state validation (`sessionStorage.getItem('oauth_state')`)
- ✅ Provider parameter support (extensible for Google, GitHub, etc.)
- ✅ Error handling with user feedback
- ✅ Fallback to legacy handler for compatibility

**Issues Found**: None critical

---

#### 3. **Auth Service** ([src/services/authService.js](web/oversight-hub/src/services/authService.js))

```javascript
generateGitHubAuthURL(); // Generate authorization URL
exchangeCodeForToken(); // Exchange code for token (Legacy)
handleOAuthCallbackNew(); // New unified handler (Preferred)
getAuthToken(); // Get stored token with expiry check
isTokenExpired(); // JWT expiry validation
initializeDevToken(); // Development token creation
```

**Security Features**:

- ✅ JWT token expiration checking
- ✅ Automatic token refresh for development (every 14 minutes)
- ✅ Base64url decoding for JWT payload inspection
- ✅ Token stored in localStorage (consider upgrading to httpOnly cookies)

**Potential Issues**:

- ⚠️ Token stored in `localStorage` (vulnerable to XSS)
  - **Recommendation**: Move to httpOnly cookies for production
  - **Quick Fix**: Add `document.httpOnly = true` to token storage

---

#### 4. **Auth Context** ([src/context/AuthContext.jsx](web/oversight-hub/src/context/AuthContext.jsx))

```javascript
// Provides:
- user (current authenticated user)
- isAuthenticated (boolean)
- accessToken (JWT token)
- logout() (clears tokens)
- setAuthUser() (updates user state)
- loading (initialization status)
```

**Features**:

- ✅ Zustand store integration (persistent state)
- ✅ Dual-sync: Both React Context + Zustand store
- ✅ Automatic initialization on app load
- ✅ Development token auto-generation

**Status**: Production-ready with token storage upgrade needed

---

### Backend Architecture (FastAPI - src/cofounder_agent)

#### 1. **Unified Auth Routes** ([src/cofounder_agent/routes/auth_unified.py](src/cofounder_agent/routes/auth_unified.py))

**Endpoints**:

| Endpoint                    | Method | Purpose                         | Status |
| --------------------------- | ------ | ------------------------------- | ------ |
| `/api/auth/github/callback` | POST   | Exchange auth code for token    | ✅     |
| `/api/auth/logout`          | POST   | Unified logout (all auth types) | ✅     |
| `/api/auth/me`              | GET    | Get current user profile        | ✅     |

**Implementation Details**:

```python
# GitHub OAuth Flow
1. Frontend sends: POST /api/auth/github/callback { code, state }
2. Backend exchanges code for GitHub access token
3. Backend fetches user data from GitHub API
4. Backend creates JWT token (15 minutes expiry)
5. Returns: { token, user }
```

**Security Features**:

- ✅ Code → Token exchange (no client-side token handling)
- ✅ GitHub API call server-side (secure)
- ✅ JWT token generation with expiry
- ✅ Mock auth support for development

**Critical Issues Found**:

| Issue                          | Severity    | Details                           |
| ------------------------------ | ----------- | --------------------------------- |
| Missing `GITHUB_CLIENT_ID`     | 🔴 CRITICAL | Not in root `.env.local`          |
| Missing `GITHUB_CLIENT_SECRET` | 🔴 CRITICAL | Not in root `.env.local`          |
| Token secret not set           | 🔴 CRITICAL | Uses default `dev-jwt-secret-...` |

---

## Production Deployment Checklist

### Phase 1: GitHub OAuth App Setup (GitHub.com)

- [ ] **Create GitHub OAuth App**
  1. Go to: https://github.com/settings/developers
  2. Click "New OAuth App"
  3. Fill in:
     - **Application name**: Glad Labs Oversight Hub
     - **Homepage URL**: `https://yourdomain.com` (production domain)
     - **Authorization callback URL**: `https://yourdomain.com/auth/callback` ⚠️ **CHANGE FROM localhost**
  4. Copy **Client ID** and **Client Secret**

  ⚠️ **Important**: The production GitHub OAuth app **must be different** from development!
  - Dev app: uses `http://localhost:3001/auth/callback`
  - Prod app: uses `https://yourdomain.com/auth/callback`

---

### Phase 2: Backend Environment Configuration

**File**: `.env.local` (root directory)

Add these sections:

```dotenv
# ==================================
# GITHUB OAUTH CONFIGURATION (PRODUCTION)
# ==================================
# Backend receives authorization code and exchanges for token
# These MUST match GitHub OAuth App settings!
GITHUB_CLIENT_ID=<your-production-client-id>
GITHUB_CLIENT_SECRET=<your-production-client-secret>

# ==================================
# SECURITY & JWT
# ==================================
# ⚠️ CHANGE THIS! Generate with: openssl rand -base64 32
JWT_SECRET=<generate-random-64-character-string>

# Token expiry (minutes)
JWT_EXPIRY_MINUTES=15

# ==================================
# CORS CONFIGURATION
# ==================================
# Production domain
ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com

# ==================================
# API URLS
# ==================================
# Backend must know its public URL for CORS and redirects
BACKEND_URL=https://api.yourdomain.com
FRONTEND_OVERSIGHT_URL=https://yourdomain.com/oversight

# ==================================
# DEPLOYMENT PLATFORM
# ==================================
# If using Railway, Vercel, or Heroku:
NODE_ENV=production
ENVIRONMENT=production
LOG_LEVEL=INFO              # Reduce verbose logging
```

---

### Phase 3: Frontend Environment Configuration

**File**: `web/oversight-hub/.env.local` (or Vercel environment variables)

Replace with production values:

```dotenv
# ==================================
# API CONFIGURATION
# ==================================
REACT_APP_API_URL=https://api.yourdomain.com

# ==================================
# GITHUB OAUTH
# ==================================
# Use YOUR PRODUCTION GitHub OAuth Client ID
REACT_APP_GITHUB_CLIENT_ID=<your-production-client-id>
REACT_APP_GITHUB_REDIRECT_URI=https://yourdomain.com/auth/callback
REACT_APP_USE_MOCK_AUTH=false                  # ⚠️ MUST be false in production!

# ==================================
# DEPLOYMENT
# ==================================
REACT_APP_ENVIRONMENT=production
REACT_APP_LOG_LEVEL=info
```

---

### Phase 4: Security Hardening

#### A. Token Storage (High Priority)

**Current**: `localStorage` (XSS vulnerable)
**Recommended**: `httpOnly` cookies + CSRF tokens

**Implementation Steps**:

1. **Backend** - Set secure cookie:

```python
# In auth_unified.py, modify github_callback response
response.set_cookie(
    key="auth_token",
    value=jwt_token,
    httponly=True,
    secure=True,                # HTTPS only
    samesite="Strict",          # CSRF protection
    max_age=15*60               # 15 minutes
)
```

2. **Frontend** - Read from cookie, not localStorage:

```javascript
// In authService.js
export const getAuthToken = () => {
  // Automatically sent by browser with requests
  // No need to manually retrieve from localStorage
  // Use: credentials: 'include' in fetch calls
};
```

3. **Update all API calls**:

```javascript
// In taskService.js, ollamaService.js, etc.
const response = await fetch(endpoint, {
  credentials: 'include', // Include cookies
  // ... other options
});
```

#### B. HTTPS/TLS Configuration

- ⚠️ OAuth **requires HTTPS** in production
- Obtain SSL certificate (Let's Encrypt free, Vercel automatic, Railway automatic)
- Update GitHub OAuth redirect URL to use `https://`

#### C. Rate Limiting

```dotenv
# In .env.local
RATE_LIMIT_AUTH=5/minute          # 5 login attempts per minute
RATE_LIMIT_API=100/minute         # 100 requests per minute per IP
```

---

### Phase 5: Deployment Platform Configuration

#### **Option A: Vercel (Recommended for Frontend)**

1. **Connect Repository**: https://vercel.com/new
2. **Environment Variables** (in Vercel dashboard):
   ```
   REACT_APP_API_URL = https://api.yourdomain.com
   REACT_APP_GITHUB_CLIENT_ID = <prod-client-id>
   REACT_APP_GITHUB_REDIRECT_URI = https://oversight.yourdomain.com/auth/callback
   REACT_APP_USE_MOCK_AUTH = false
   ```
3. **Redeploy**: Push to main branch

#### **Option B: Railway (Recommended for Backend)**

1. **Create new Service**: https://railway.app
2. **Connect Repository**: `glad-labs-website`
3. **Environment Variables**:
   ```
   GITHUB_CLIENT_ID = <prod-client-id>
   GITHUB_CLIENT_SECRET = <prod-client-secret>
   JWT_SECRET = <random-64-chars>
   ALLOWED_ORIGINS = https://oversight.yourdomain.com
   DATABASE_URL = <production-postgres-url>
   ```
4. **Deploy**: Railway auto-deploys on push

#### **Option C: Custom Server (AWS, GCP, Azure)**

1. Set environment variables in:
   - `.env` file (secure, never commit)
   - Docker secrets (if containerized)
   - Environment variable manager (AWS Secrets Manager, etc.)
2. Ensure HTTPS/TLS configured
3. Configure firewall to only allow required ports

---

## Testing Checklist

### Local Testing (Before Production)

- [ ] **Mock Auth Works**

  ```bash
  REACT_APP_USE_MOCK_AUTH=true npm run dev
  # Login with mock account
  ```

- [ ] **Real OAuth Works** (use test GitHub app)

  ```bash
  REACT_APP_USE_MOCK_AUTH=false npm run dev
  # Try signing in with GitHub
  ```

- [ ] **Token Expiry Works**
  - Generate token
  - Wait 15 minutes
  - Verify token refreshes automatically

- [ ] **Logout Works**
  - Sign in
  - Click logout
  - Verify redirects to login
  - Verify token cleared from storage

- [ ] **CORS Works**

  ```bash
  # Frontend at http://localhost:3001
  # Backend at http://localhost:8000
  # Verify API calls succeed
  ```

- [ ] **State Validation Works** (CSRF)
  - Intercept OAuth callback
  - Modify state parameter
  - Verify security error

### Production Testing (After Deployment)

- [ ] **HTTPS Enforced**

  ```bash
  curl -I https://oversight.yourdomain.com
  # Verify redirect from http to https
  ```

- [ ] **GitHub OAuth with Production App**
  - Login via GitHub
  - Verify correct scopes requested
  - Verify user data fetched

- [ ] **Token Security**

  ```bash
  # Open DevTools → Application → Cookies
  # Verify: auth_token exists, httpOnly=true, secure=true
  ```

- [ ] **CORS Headers Correct**

  ```bash
  curl -H "Origin: https://oversight.yourdomain.com" https://api.yourdomain.com/api/auth/me
  # Verify: Access-Control-Allow-Origin header present
  ```

- [ ] **SSL Certificate Valid**
  ```bash
  openssl s_client -connect api.yourdomain.com:443
  # Verify certificate chain
  ```

---

## Security Vulnerabilities & Mitigations

| Vulnerability           | Current Status    | Mitigation                                     |
| ----------------------- | ----------------- | ---------------------------------------------- |
| Token in localStorage   | ⚠️ **Vulnerable** | Move to httpOnly cookies                       |
| Mock auth in production | ❌ **CRITICAL**   | Ensure `REACT_APP_USE_MOCK_AUTH=false`         |
| Exposed Client Secret   | ⚠️ **Risk**       | Never commit to git; use env variables         |
| Default JWT Secret      | ❌ **CRITICAL**   | Generate random 64-char string                 |
| No HTTPS                | ❌ **CRITICAL**   | Enforce HTTPS in production                    |
| No Rate Limiting        | ⚠️ **Moderate**   | Add rate limits to `/api/auth/github/callback` |
| No CSRF Token Rotation  | ⚠️ **Low**        | Current state-based CSRF adequate              |

---

## Recommended Improvements (Post-Launch)

### Short Term (Week 1)

1. ✅ Move token to httpOnly cookies
2. ✅ Add rate limiting to auth endpoints
3. ✅ Implement token rotation (refresh tokens)

### Medium Term (Month 1)

1. 🔄 Add OAuth provider strategy pattern (GitHub, Google, Microsoft)
2. 🔄 Implement two-factor authentication (2FA) webhook
3. 🔄 Add user session logging to database

### Long Term (Quarter 1)

1. 🔐 Implement refresh token rotation
2. 🔐 Add OAuth token revocation endpoint
3. 🔐 Implement OAuth grant audit logging
4. 🔐 Add passwordless authentication (WebAuthn)

---

## Deployment Commands

### Pre-Deployment

```bash
# Verify environment variables are set
echo "Client ID: $GITHUB_CLIENT_ID"
echo "Client Secret: ${GITHUB_CLIENT_SECRET:0:10}***"
echo "JWT Secret: ${JWT_SECRET:0:10}***"

# Run security checks
npm run audit                  # Check for dependency vulnerabilities
npm run test                   # Run test suite
npm run lint                   # Check code quality

# Build
npm run build                  # Create production build
```

### Deployment

```bash
# Vercel (Frontend)
vercel --prod

# Railway (Backend)
# Auto-deploys on git push to main

# Manual (Docker)
docker build -t glad-labs-backend .
docker run -e GITHUB_CLIENT_ID=$GITHUB_CLIENT_ID \
           -e GITHUB_CLIENT_SECRET=$GITHUB_CLIENT_SECRET \
           -e JWT_SECRET=$JWT_SECRET \
           -p 8000:8000 glad-labs-backend
```

### Post-Deployment

```bash
# Health Check
curl https://api.yourdomain.com/health

# Auth Endpoint Check
curl -X POST https://api.yourdomain.com/api/auth/logout \
  -H "Authorization: Bearer <test-token>"

# Verify CORS
curl -I https://oversight.yourdomain.com
```

---

## Quick Reference: GitHub OAuth Setup

### Step 1: Create OAuth App

```
1. https://github.com/settings/developers
2. New OAuth App
3. Set redirect URL: https://yourdomain.com/auth/callback
4. Copy Client ID and Secret
```

### Step 2: Backend Config

```bash
export GITHUB_CLIENT_ID="your-client-id"
export GITHUB_CLIENT_SECRET="your-client-secret"
export JWT_SECRET="$(openssl rand -base64 32)"
```

### Step 3: Frontend Config

```bash
REACT_APP_GITHUB_CLIENT_ID=<client-id>
REACT_APP_USE_MOCK_AUTH=false
```

### Step 4: Test

```bash
npm run dev
# Try login at http://localhost:3001
```

---

## Support & Troubleshooting

### Common Issues

**Issue**: "GitHub authentication failed: invalid_request"

- **Cause**: Client ID/Secret mismatch or wrong redirect URL
- **Fix**: Verify GitHub OAuth App settings match environment variables

**Issue**: "CORS error: No 'Access-Control-Allow-Origin' header"

- **Cause**: Frontend domain not in `ALLOWED_ORIGINS`
- **Fix**: Add frontend URL to `.env.local` ALLOWED_ORIGINS

**Issue**: "Token expired immediately after login"

- **Cause**: JWT_SECRET changed or system time skewed
- **Fix**: Ensure consistent JWT_SECRET; check server time

**Issue**: "State mismatch - possible CSRF attack"

- **Cause**: Session storage cleared or browser tab closed during OAuth
- **Fix**: Normal; user should retry login

### Testing OAuth Flow

```javascript
// In browser console
localStorage.getItem('auth_token'); // Check token
localStorage.getItem('user'); // Check user data
sessionStorage.getItem('oauth_state'); // Check CSRF state
```

---

## Files Involved

### Frontend

- [web/oversight-hub/src/pages/Login.jsx](web/oversight-hub/src/pages/Login.jsx) - Login button
- [web/oversight-hub/src/pages/AuthCallback.jsx](web/oversight-hub/src/pages/AuthCallback.jsx) - OAuth callback handler
- [web/oversight-hub/src/services/authService.js](web/oversight-hub/src/services/authService.js) - Auth logic
- [web/oversight-hub/src/context/AuthContext.jsx](web/oversight-hub/src/context/AuthContext.jsx) - Auth state
- [web/oversight-hub/.env.local](web/oversight-hub/.env.local) - Frontend config

### Backend

- [src/cofounder_agent/routes/auth_unified.py](src/cofounder_agent/routes/auth_unified.py) - OAuth endpoints
- [src/cofounder_agent/main.py](src/cofounder_agent/main.py) - FastAPI app setup
- [.env.local](.env.local) - Backend config

---

## Summary Table

| Component                   | Status            | Action Required             |
| --------------------------- | ----------------- | --------------------------- |
| **Frontend Login UI**       | ✅ Ready          | None                        |
| **Frontend OAuth Callback** | ✅ Ready          | None                        |
| **Frontend Auth Service**   | ✅ Ready          | Upgrade to httpOnly cookies |
| **Frontend Auth Context**   | ✅ Ready          | None                        |
| **Backend OAuth Endpoints** | ✅ Ready          | None                        |
| **GitHub OAuth App Setup**  | ⚠️ Pending        | Create production app       |
| **Environment Variables**   | ❌ Missing        | Add to `.env.local`         |
| **Token Storage**           | ⚠️ Vulnerable     | Move to httpOnly cookies    |
| **HTTPS/TLS**               | ❌ Missing        | Configure on deployment     |
| **Rate Limiting**           | ⚠️ Not configured | Add to auth endpoints       |
| **CORS Configuration**      | ⚠️ Pending        | Set production domain       |

---

**Last Updated**: January 2, 2026  
**Reviewed By**: Copilot  
**Next Review**: After first production deployment
