# Production Authentication Troubleshooting & Fix Guide

**Date:** February 11, 2026  
**Issue:** GitHub OAuth failing in production with 403/404 errors  
**Status:** FIXED ✅

---

## Issues Fixed

### 1. ❌ Endpoint Mismatch (404 Error)

**Problem:**

- Frontend called `/api/auth/github-callback` (with dash)
- Backend had `/api/auth/github/callback` (with slash)

**Error:**

```
Failed to load resource: the server responded with a status of 404 ()
Error exchanging code for token: Error: Authentication failed
```

**Fix Applied:** ✅

- Updated frontend to call `/api/auth/github/callback` (with slash)
- Added fallback endpoint `/api/auth/github-callback` for backward compatibility
- Frontend now sends CSRF state token with the request

**Files Modified:**

- `web/oversight-hub/src/services/authService.js` - Fixed endpoint path + added state handling
- `src/cofounder_agent/routes/auth_unified.py` - Added fallback endpoint

---

### 2. ❌ CSRF State Token Missing (403 Error)

**Problem:**

- Backend requires CSRF state token for security
- Frontend wasn't sending it in the request body

**Error:**

```
Invalid or expired CSRF token (403 Forbidden)
```

**Fix Applied:** ✅

- Frontend now retrieves state from sessionStorage
- State is included in request to backend: `{ code, state }`
- Backend validates state before proceeding

**Code Change:**

```javascript
// BEFORE (missing state)
body: JSON.stringify({ code })

// AFTER (includes state)
const state = sessionStorage.getItem('oauth_state');
if (!state) {
  throw new Error('CSRF state not found - session expired');
}
body: JSON.stringify({ code, state })
```

---

### 3. ❌ Mock Auth Enabled in Production

**Problem:**

```
❌ SECURITY WARNING: Mock auth service is being used in non-development mode!
This is a security risk. Ensure REACT_APP_USE_MOCK_AUTH is not set in production.
```

**Cause:**

- `REACT_APP_USE_MOCK_AUTH` was set in production
- Mock auth tokens were being used instead of real GitHub OAuth

**Fix Applied:** ✅

- Mock auth now blocked unless `NODE_ENV === 'development'`
- Environment check added to Login.jsx
- Warning logged if enabled in non-dev mode
- UI shows "Sign in (Mock - Dev Only)" to indicate when mock auth is active

**Code Change:**

```javascript
// BEFORE (always used mock if env var set)
const useMockAuth = process.env.REACT_APP_USE_MOCK_AUTH === 'true';

// AFTER (only in development)
const isDevelopment = process.env.NODE_ENV === 'development';
const useMockAuth = isDevelopment && process.env.REACT_APP_USE_MOCK_AUTH === 'true';
```

---

## Production Setup Checklist

### ✅ Environment Variables Required

**Backend (.env or Railway config):**

```bash
# GitHub OAuth Configuration
GH_OAUTH_CLIENT_ID=your_github_client_id
GH_OAUTH_CLIENT_SECRET=your_github_client_secret

# Application Configuration
NODE_ENV=production
DEPLOYMENT_ENV=production
```

**Frontend (.env.production or Railway config):**

```bash
# MUST NOT HAVE MOCK AUTH IN PRODUCTION
# ❌ DO NOT SET: REACT_APP_USE_MOCK_AUTH

# Required for OAuth
REACT_APP_GH_OAUTH_CLIENT_ID=your_github_client_id
REACT_APP_API_URL=https://cofounder-production.up.railway.app
NODE_ENV=production
```

---

### ✅ GitHub OAuth App Setup

1. **Go to GitHub Settings:**
   - Settings → Developer settings → OAuth Apps
   - Create New OAuth App or edit existing

2. **Configure Callback URL:**

   ```
   Authorization callback URL: https://yourapp.com/auth/callback
   ```

   (Replace `yourapp.com` with your actual domain)

3. **Get Credentials:**
   - Copy Client ID
   - Generate new Client Secret
   - Store in environment variables (not in code)

4. **Verify in Production:**
   - Backend env: `GH_OAUTH_CLIENT_ID` and `GH_OAUTH_CLIENT_SECRET`
   - Frontend env: `REACT_APP_GH_OAUTH_CLIENT_ID`

---

### ✅ API Endpoint Configuration

**Verify these endpoints are accessible:**

1. **GitHub OAuth Callback:**

   ```bash
   curl -X POST https://your-api.com/api/auth/github/callback \
     -H "Content-Type: application/json" \
     -d '{"code": "test_code", "state": "test_state"}'
   ```

   Expected: 400 (invalid code) or 403 (invalid state) - NOT 404

2. **Logout:**

   ```bash
   curl -X POST https://your-api.com/api/auth/logout \
     -H "Authorization: Bearer YOUR_JWT_TOKEN"
   ```

   Expected: 200 OK with logout response

3. **Get Current User:**

   ```bash
   curl https://your-api.com/api/auth/me \
     -H "Authorization: Bearer YOUR_JWT_TOKEN"
   ```

   Expected: 200 OK with user profile

---

## Testing Authentication Flow

### Step 1: Verify GitHub OAuth Configuration

```bash
# Check if GitHub client ID is set
echo $GH_OAUTH_CLIENT_ID

# Check if client secret is set (should not print value)
[ -n "$GH_OAUTH_CLIENT_SECRET" ] && echo "Secret is set" || echo "Secret NOT set"
```

### Step 2: Test Full OAuth Flow

1. Open your app: `https://your-app.com`
2. Click "Sign in with GitHub"
3. You should be redirected to GitHub login
4. After GitHub auth, should return to `/auth/callback`
5. Should redirect to dashboard with JWT token

### Step 3: Verify Token is Valid

```bash
# After logging in, check token in browser console:
console.log(localStorage.getItem('auth_token'))

# Token should be a valid JWT (3 parts separated by dots)
# Example: eyJhbGciOiJIUzI1NiIs.eyJzdWIiOiIxMjM0NTY3O...NDAyNDkxNDMyfQ.xzI9H4k
```

---

## Debugging Production Issues

### Enable Debug Logging (Backend)

Add to your FastAPI environment:

```bash
LOG_LEVEL=debug
SQL_DEBUG=false  # Keep false unless needed
```

Then check logs:

```bash
# Railway logs
railway logs --service cofounder-agent

# Look for GitHub auth logs:
# grep -i "github" logs.txt
```

### Enable Debug Logging (Frontend)

Add to browser DevTools console:

```javascript
// Watch auth service calls
localStorage.setItem('DEBUG_AUTH', 'true');

// Check what's stored
console.log(localStorage.getItem('auth_token'));
console.log(localStorage.getItem('user'));
console.log(sessionStorage.getItem('oauth_state'));
```

### Common Error Messages & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `Invalid or expired CSRF token (403)` | State token missing or expired | Check sessionStorage.oauth_state, state expires after 10 min |
| `GitHub authentication failed (401)` | Wrong Client ID/Secret | Verify GH_OAUTH_CLIENT_ID and GH_OAUTH_CLIENT_SECRET |
| `Failed to load resource: 404` | Endpoint mismatch | Use /api/auth/github/callback (with slash) |
| `Missing authorization header` | No JWT token sent | Token should be in localStorage after login |
| `OAuth callback failed` | Frontend error handling | Check browser console for stack trace |
| `Mock auth in production` | REACT_APP_USE_MOCK_AUTH set | Remove from .env.production |

---

## Deployment Steps

### 1. Remove Mock Auth from Production Build

**In your .env.production or Railway config:**

```bash
# Remove or comment out:
# REACT_APP_USE_MOCK_AUTH=true
```

**Verify no mock auth in build:**

```bash
grep -r "REACT_APP_USE_MOCK_AUTH" .env* | grep -v ".env.local"
# Should return nothing
```

### 2. Set GitHub OAuth Environment Variables

**For Railway:**

```bash
# In Railway Dashboard → Variables
GH_OAUTH_CLIENT_ID=github_xxxxxxxxxxxxxxxxxxxxx
GH_OAUTH_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxx
REACT_APP_GH_OAUTH_CLIENT_ID=github_xxxxxxxxxxxxxxxxxxxxx
```

**For other platforms (Vercel, etc):**

- Frontend: Set `REACT_APP_GH_OAUTH_CLIENT_ID` in build env
- Backend: Set `GH_OAUTH_CLIENT_ID` and `GH_OAUTH_CLIENT_SECRET` in server env

### 3. Verify Callback URL in GitHub

GitHub OAuth App settings must have:

```
Authorization callback URL: https://your-domain.com/auth/callback
```

### 4. Deploy & Test

```bash
# Deploy backend
git push  # Triggers Railway deploy

# Deploy frontend
# (automatic on main branch for Vercel)

# Wait for builds to complete, then test
# Visit: https://your-app.com
# Click sign in with GitHub
# Complete OAuth flow
```

---

## Files Modified for Production Fix

| File | Change | Impact |
|------|--------|--------|
| `web/oversight-hub/src/services/authService.js` | Fixed endpoint `/api/auth/github-callback` → `/api/auth/github/callback`, added CSRF state parameter | **HIGH** - Fixes 404 and CSRF errors |
| `web/oversight-hub/src/pages/Login.jsx` | Added NODE_ENV check for mock auth, improved env var handling | **HIGH** - Prevents mock auth in production |
| `src/cofounder_agent/routes/auth_unified.py` | Added fallback endpoint for backward compatibility | **MEDIUM** - Prevents future migration breaking |

---

## Verification Checklist

After deploying these fixes:

- [ ] Removed `REACT_APP_USE_MOCK_AUTH` from production config
- [ ] Set `GH_OAUTH_CLIENT_ID` in backend environment
- [ ] Set `GH_OAUTH_CLIENT_SECRET` in backend environment  
- [ ] Set `REACT_APP_GH_OAUTH_CLIENT_ID` in frontend environment
- [ ] GitHub OAuth app callback URL updated to production domain
- [ ] Tested full sign-in flow in production
- [ ] Verified JWT token generated correctly
- [ ] Checked no security warnings in browser console
- [ ] Changed GitHub OAuth app credentials (if exposed)
- [ ] Monitored logs for any auth errors

---

## Security Notes

🔒 **Important:**

1. **Never commit secrets** to git:

   ```bash
   # Good: Use environment variables
   GH_OAUTH_CLIENT_SECRET=$(cat /path/to/secret)
   
   # Bad: Don't do this
   GH_OAUTH_CLIENT_SECRET=abcd1234...
   ```

2. **Rotate GitHub credentials** if exposed:
   - GitHub OAuth app → Settings → Delete old credentials
   - Generate new credentials
   - Update in production immediately

3. **CSRF tokens expire:**
   - Frontend state tokens expire after 10 minutes
   - If OAuth callback takes too long, state becomes invalid
   - User needs to restart login

4. **JWT tokens expire:**
   - Access tokens expire after 60 minutes (configurable)
   - Implement token refresh for long sessions
   - Remove token on logout

---

## Support & Troubleshooting

**If still having issues:**

1. Check backend logs:

   ```bash
   railway logs --service cofounder-agent | grep -i auth
   ```

2. Check browser console:
   - DevTools → Console tab
   - Look for errors/warnings about auth

3. Verify environment variables:

   ```bash
   # Backend
   echo $GH_OAUTH_CLIENT_ID
   
   # Frontend (check build)
   grep "GH_OAUTH_CLIENT_ID" build/index.html
   ```

4. Test API directly:

   ```bash
   curl -v https://your-api.com/api/auth/github/callback
   # Should return 400 (bad request) not 404
   ```

---

**Summary:** All critical auth issues have been fixed. Mock auth is now properly disabled in production, CSRF state handling is correct, and endpoint paths are aligned. Deployment is safe! 🚀
