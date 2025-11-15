# OAuth Integration Testing Guide

**Status:** ✅ Ready for Integration Testing  
**Backend Progress:** 85/100 (Infrastructure Complete)  
**Date:** November 14, 2025

---

## 📋 Pre-Flight Checklist

Before running tests, verify all infrastructure is in place:

### ✅ Files Created (Verify Exist)

```bash
✅ src/cofounder_agent/services/oauth_provider.py      (Abstract interface)
✅ src/cofounder_agent/services/github_oauth.py        (GitHub OAuth)
✅ src/cofounder_agent/services/oauth_manager.py       (Provider factory)
✅ src/cofounder_agent/routes/oauth_routes.py          (OAuth endpoints)
✅ src/cofounder_agent/models.py                       (Updated with OAuthAccount)
✅ src/cofounder_agent/services/database_service.py    (Updated with OAuth methods)
```

**Verify files exist:**

```bash
ls -la src/cofounder_agent/services/oauth*.py
ls -la src/cofounder_agent/routes/oauth_routes.py
```

### ✅ Dependencies Installed

```bash
# Verify httpx is installed (used for OAuth API calls)
python -c "import httpx; print(f'httpx version: {httpx.__version__}')"

# Verify jose/jwt is installed (used for token management)
python -c "from jose import jwt; print('jose installed')"

# Verify FastAPI is installed
python -c "import fastapi; print(f'FastAPI version: {fastapi.__version__}')"
```

### ✅ Environment Variables Set

Check `.env.local` has all required variables:

```bash
# Check GitHub OAuth credentials exist
grep "GITHUB_CLIENT_ID" .env.local
grep "GITHUB_CLIENT_SECRET" .env.local
grep "BACKEND_URL" .env.local
grep "FRONTEND_URL" .env.local
grep "JWT_SECRET" .env.local
```

### ✅ Database Ready

```bash
# Verify PostgreSQL is running
psql $DATABASE_URL -c "SELECT version();"

# Check OAuthAccount table exists
psql $DATABASE_URL -c "\dt oauth_accounts;"

# Check users table exists
psql $DATABASE_URL -c "\dt users;"
```

---

## 🚀 Setup Steps (Quick Start)

### Step 1: Get GitHub OAuth Credentials (5 min)

1. Go to **https://github.com/settings/developers**
2. Click **"New OAuth App"**
3. Fill in:
   - **Application name:** `Glad Labs Dev`
   - **Homepage URL:** `http://localhost:8000`
   - **Authorization callback URL:** `http://localhost:8000/api/auth/github/callback`
4. Click **Create OAuth App**
5. You'll see:
   - **Client ID** (copy this)
   - **Client Secret** (click "Generate new client secret", copy this)

### Step 2: Add to Environment (2 min)

Edit `.env.local` and replace these values:

```bash
GITHUB_CLIENT_ID=<paste_your_client_id>
GITHUB_CLIENT_SECRET=<paste_your_client_secret>
```

**Example (DO NOT USE - for reference):**

```
GITHUB_CLIENT_ID=Ov23liXxxxxxxxxxxxx
GITHUB_CLIENT_SECRET=1a2b3c4d5e6f7g8h9i0jxxxxxxxx
```

### Step 3: Verify Setup (2 min)

```bash
# Verify environment variables are loaded
python -c "
import os
from dotenv import load_dotenv
load_dotenv('.env.local')
print(f'GITHUB_CLIENT_ID: {os.getenv(\"GITHUB_CLIENT_ID\", \"NOT SET\")}')
print(f'GITHUB_CLIENT_SECRET: {os.getenv(\"GITHUB_CLIENT_SECRET\", \"NOT SET\")}')
print(f'BACKEND_URL: {os.getenv(\"BACKEND_URL\", \"NOT SET\")}')
print(f'FRONTEND_URL: {os.getenv(\"FRONTEND_URL\", \"NOT SET\")}')
print(f'JWT_SECRET: {os.getenv(\"JWT_SECRET\", \"NOT SET\")}')
"
```

### Step 4: Start Backend (2 min)

```bash
cd src/cofounder_agent
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Expected output:

```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete
```

---

## 🧪 Test Scenarios

### Test 1: OAuth Provider Registry (5 min)

**Objective:** Verify OAuthManager can list available providers

**Command:**

```bash
curl http://localhost:8000/api/auth/providers
```

**Expected Response:**

```json
{
  "providers": ["github"]
}
```

**What It Tests:**

- ✅ oauth_manager.py is working
- ✅ Route registration successful
- ✅ No import errors

---

### Test 2: GitHub Login Redirect (5 min)

**Objective:** Verify login endpoint generates correct GitHub OAuth URL

**Command:**

```bash
curl -i http://localhost:8000/api/auth/github/login
```

**Expected Response:**

```
HTTP/1.1 307 Temporary Redirect
Location: https://github.com/login/oauth/authorize?client_id=Ov23li...&scope=read:user,user:email&state=...
```

**What It Tests:**

- ✅ CSRF state token generation (state parameter)
- ✅ GitHub OAuth URL construction
- ✅ Route registration

**Troubleshooting:**

- If you get `GITHUB_CLIENT_ID not found`: Check .env.local
- If redirect URL is wrong: Check oauth_manager.py GitHub URLs

---

### Test 3: GitHub Callback (Manual - 10 min)

**Objective:** Complete full OAuth flow and receive JWT token

**Steps:**

1. **Get Authorization Code:**
   - Visit: `http://localhost:8000/api/auth/github/login`
   - GitHub will ask for authorization
   - Click "Authorize <app>"
   - GitHub redirects to: `http://localhost:8000/api/auth/github/callback?code=XXXX&state=YYYY`
   - Copy the `code` parameter

2. **Simulate Callback (if redirect doesn't work):**

   ```bash
   # Note: Real callback happens automatically when you authorize
   # But if testing manual flow:
   curl -i "http://localhost:8000/api/auth/github/callback?code=YOUR_CODE&state=YOUR_STATE"
   ```

3. **Expected Response:**

   ```json
   {
     "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
     "token_type": "bearer",
     "expires_in": 3600
   }
   ```

4. **Verify User Created:**
   ```bash
   # Check database for new user
   psql $DATABASE_URL -c "SELECT * FROM users ORDER BY created_at DESC LIMIT 1;"
   psql $DATABASE_URL -c "SELECT * FROM oauth_accounts ORDER BY created_at DESC LIMIT 1;"
   ```

---

### Test 4: Get Current User (5 min)

**Objective:** Verify JWT token works and returns user info

**Command:**

```bash
# Replace YOUR_TOKEN with the JWT from Test 3
JWT_TOKEN="YOUR_TOKEN_HERE"
curl -H "Authorization: Bearer $JWT_TOKEN" http://localhost:8000/api/auth/me
```

**Expected Response:**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "your.github.email@example.com",
  "username": "your_github_username",
  "oauth_accounts": [
    {
      "provider": "github",
      "provider_user_id": "123456",
      "created_at": "2025-11-14T12:00:00Z"
    }
  ]
}
```

**What It Tests:**

- ✅ JWT token validation (JWTTokenManager.verify_token)
- ✅ Token decoding
- ✅ User data retrieval
- ✅ OAuth account relationship

**Troubleshooting:**

- If you get `401 Unauthorized`: JWT token invalid/expired
- If you get `404 Not Found`: Route not registered
- If you get `500 Error`: Check backend logs

---

### Test 5: Logout (3 min)

**Objective:** Verify logout endpoint works

**Command:**

```bash
JWT_TOKEN="YOUR_TOKEN_HERE"
curl -X POST -H "Authorization: Bearer $JWT_TOKEN" http://localhost:8000/api/auth/logout
```

**Expected Response:**

```json
{
  "message": "Successfully logged out"
}
```

---

### Test 6: Multiple OAuth Accounts (10 min)

**Objective:** Verify one user can link multiple OAuth providers

**Steps:**

1. **Login with GitHub** (complete Test 3)
   - Get JWT token
   - User created

2. **Simulate adding Google OAuth:**
   - For now, use the GitHub token from step 1
   - Call GET /api/auth/me
   - See oauth_accounts only has GitHub

3. **After Google OAuth is implemented:**
   - Login with Google
   - Same email address
   - User should auto-link to existing account
   - oauth_accounts should have both GitHub and Google

---

## 📊 Testing Results Template

Use this template to document your test results:

```markdown
# OAuth Integration Test Results

**Date:** [YYYY-MM-DD HH:MM:SS]
**Tester:** [Your Name]
**Environment:** [local/staging/production]

## Pre-Flight Checklist

- [ ] All files exist
- [ ] All dependencies installed
- [ ] Environment variables set
- [ ] Database ready
- [ ] Backend started

## Test Results

### Test 1: OAuth Provider Registry

- Status: ✅ PASS / ❌ FAIL
- Response: [paste response]
- Notes: [any issues]

### Test 2: GitHub Login Redirect

- Status: ✅ PASS / ❌ FAIL
- Redirect URL: [paste URL]
- State Token Generated: ✅ YES / ❌ NO
- Notes: [any issues]

### Test 3: GitHub Callback

- Status: ✅ PASS / ❌ FAIL
- JWT Token Received: ✅ YES / ❌ NO
- User Created: ✅ YES / ❌ NO
- OAuthAccount Created: ✅ YES / ❌ NO
- Notes: [any issues]

### Test 4: Get Current User

- Status: ✅ PASS / ❌ FAIL
- User Data Returned: ✅ YES / ❌ NO
- OAuth Accounts Listed: ✅ YES / ❌ NO
- Notes: [any issues]

### Test 5: Logout

- Status: ✅ PASS / ❌ FAIL
- Logout Message: [paste message]
- Notes: [any issues]

### Test 6: Multiple OAuth Accounts

- Status: ✅ PASS / ❌ FAIL
- Notes: [any issues]

## Summary

- Total Tests: 6
- Passed: [X]/6
- Failed: [Y]/6
- Blockers: [list any]

## Next Steps

- [ ] Fix any failures
- [ ] Demonstrate modularity (add Google OAuth)
- [ ] Load testing
- [ ] Production deployment
```

---

## 🐛 Common Issues & Troubleshooting

### Issue 1: "GITHUB_CLIENT_ID not found"

**Cause:** Environment variables not loaded

**Fix:**

```bash
# Verify .env.local exists and has credentials
cat .env.local | grep GITHUB_CLIENT

# Restart backend (environment loaded on startup)
# Stop: Ctrl+C
# Start: python -m uvicorn main:app --reload
```

---

### Issue 2: GitHub Redirects to Wrong URL

**Cause:** Callback URL in GitHub app settings doesn't match code

**Fix:**

1. Go to https://github.com/settings/developers
2. Edit your OAuth app
3. Set **Authorization callback URL** to: `http://localhost:8000/api/auth/github/callback`
4. Restart backend

---

### Issue 3: "Invalid state parameter"

**Cause:** CSRF state token doesn't match

**Fix:**

- This is a security feature
- Refresh the login page and try again
- Tokens expire after 10 minutes

---

### Issue 4: 500 Error on Callback

**Check Logs:**

```bash
# Backend logs will show:
ERROR: ... [traceback will show exact issue]
```

**Common causes:**

- Database connection failed → Check PostgreSQL
- OAuthAccount creation failed → Check database_service.py
- JWT token creation failed → Check JWT_SECRET in .env.local

---

### Issue 5: User Not Created in Database

**Debug:**

```bash
# Check if oauth_accounts table has entries
psql $DATABASE_URL -c "SELECT * FROM oauth_accounts;"

# Check if users table has entries
psql $DATABASE_URL -c "SELECT * FROM users WHERE created_at > NOW() - INTERVAL '5 minutes';"

# Check database_service logs
# Look for get_or_create_oauth_user() calls
```

---

## 📈 Performance Metrics to Check

During testing, monitor:

```bash
# Backend response time
curl -w "@curl-format.txt" -o /dev/null -s http://localhost:8000/api/auth/providers

# Database query time (enable slow query log)
psql $DATABASE_URL -c "SET log_min_duration_statement = 1000;"

# Memory usage
ps aux | grep uvicorn
```

---

## ✅ Next Steps After Testing

### If All Tests Pass ✅

1. **Demonstrate Modularity:** Add Google OAuth (1 file + 1 line)
2. **Frontend Integration:** Build login UI
3. **Production Deployment:** Deploy with real GitHub app

### If Any Test Fails ❌

1. **Check Logs:** Backend logs show exact error
2. **Review Code:** Files may need updates
3. **Debug:** Follow troubleshooting guide
4. **Retry:** Re-run test after fix

---

## 📞 Quick Reference

### Essential URLs

- Backend: http://localhost:8000
- GitHub OAuth: https://github.com/settings/developers
- API Docs: http://localhost:8000/docs
- Health Check: http://localhost:8000/api/health

### Database Commands

```bash
# List all users
psql $DATABASE_URL -c "SELECT id, email, username, created_at FROM users;"

# List all OAuth accounts
psql $DATABASE_URL -c "SELECT user_id, provider, provider_user_id, created_at FROM oauth_accounts;"

# Count recent users
psql $DATABASE_URL -c "SELECT COUNT(*) FROM users WHERE created_at > NOW() - INTERVAL '1 hour';"
```

### Backend Commands

```bash
# Start backend
cd src/cofounder_agent && python -m uvicorn main:app --reload

# Stop backend
# Ctrl+C

# Check if running
ps aux | grep uvicorn
```

---

**🎉 Ready to Test! Follow the steps above and let me know results.**
