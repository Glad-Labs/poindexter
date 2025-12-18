# 🚀 OAuth Quick Start Guide - Get GitHub OAuth Working in 15 Minutes

**Status:** OAuth infrastructure is complete ✅ | Routes registered ✅ | Ready to test ✅

**What you have:** Complete OAuth system ready to go  
**What you need:** GitHub OAuth credentials (5 minutes)  
**What you'll get:** User authentication via GitHub OAuth with JWT tokens

---

## ⚡ The 15-Minute Setup

### Step 1: Create GitHub OAuth App (5 minutes)

1. Go to: https://github.com/settings/developers
2. Click **"New OAuth App"**
3. Fill in:
   - **Application name:** `Glad Labs Dev`
   - **Homepage URL:** `http://localhost:8000`
   - **Authorization callback URL:** `http://localhost:8000/api/auth/github/callback`
4. Click **"Register application"**
5. On the app page, you'll see:
   - **Client ID** (copy this)
   - **Client Secret** (generate and copy this)

### Step 2: Add to .env.local (2 minutes)

Open `c:\Users\mattm\glad-labs-website\.env.local`

Find this section (around line 35):

```bash
# ==================================
# OAUTH CONFIGURATION (GitHub)
# ==================================
GITHUB_CLIENT_ID=your_github_client_id_here
GITHUB_CLIENT_SECRET=your_github_client_secret_here
```

Replace with your actual values:

```bash
GITHUB_CLIENT_ID=abc123xyz789def456
GITHUB_CLIENT_SECRET=ghu_abcXYZ789def456ghu_123ABC
```

**Save the file.**

### Step 3: Verify Setup (2 minutes)

From `c:\Users\mattm\glad-labs-website\src\cofounder_agent`, create a test script:

**File: `test_oauth_setup.py`**

```python
#!/usr/bin/env python
"""Quick test to verify GitHub OAuth is configured"""

import os
from dotenv import load_dotenv

# Load .env.local
load_dotenv(os.path.join(os.path.dirname(__file__), '../../.env.local'))

print("\n✓ OAuth Configuration Check")
print("=" * 60)

checks = {
    "GITHUB_CLIENT_ID": os.getenv("GITHUB_CLIENT_ID"),
    "GITHUB_CLIENT_SECRET": os.getenv("GITHUB_CLIENT_SECRET"),
    "BACKEND_URL": os.getenv("BACKEND_URL"),
    "FRONTEND_URL": os.getenv("FRONTEND_URL"),
}

all_good = True
for key, value in checks.items():
    if value and value != "your_github_client_id_here":
        print(f"✅ {key}: Configured")
    else:
        print(f"❌ {key}: Missing or placeholder")
        all_good = False

print("=" * 60)
if all_good:
    print("\n✅ OAuth is configured! Ready to test.\n")
else:
    print("\n❌ Please update .env.local with your GitHub OAuth credentials.\n")
```

Run it:

```bash
cd src\cofounder_agent
python test_oauth_setup.py
```

Expected output:

```
✓ OAuth Configuration Check
============================================================
✅ GITHUB_CLIENT_ID: Configured
✅ GITHUB_CLIENT_SECRET: Configured
✅ BACKEND_URL: Configured
✅ FRONTEND_URL: Configured
============================================================

✅ OAuth is configured! Ready to test.
```

### Step 4: Start Backend (2 minutes)

```bash
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python -m uvicorn main:app --reload
```

Wait for:

```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete
```

### Step 5: Quick Test (4 minutes)

Open a new terminal in your project:

**Test 1: Check OAuth providers available**

```bash
curl http://localhost:8000/api/auth/providers
```

Expected response:

```json
{
  "providers": ["github"]
}
```

**Test 2: Get login redirect**

```bash
curl -i http://localhost:8000/api/auth/github/login
```

Expected: `307 Temporary Redirect` with GitHub authorization URL

**Test 3: Full OAuth flow**

Manual test (best way to verify):

1. Visit: http://localhost:8000/api/auth/github/login
2. You'll be redirected to GitHub to authorize
3. Click **"Authorize"**
4. GitHub redirects back with JWT token in URL
5. Check database for your user:

```bash
psql -U postgres -d glad_labs_dev -c "SELECT * FROM users;"
psql -U postgres -d glad_labs_dev -c "SELECT * FROM oauth_accounts;"
```

---

## 🎯 What Just Happened

### User Perspective

```
I visit the app
    ↓
Click "Sign in with GitHub"
    ↓
Redirected to GitHub
    ↓
I authorize the app
    ↓
Redirected back to app with user profile
    ↓
I'm logged in! 🎉
```

### Technical Perspective

```
Frontend → GET /api/auth/github/login
    ↓ [CSRF state token generated]
    → Redirects to GitHub

GitHub ← User authorizes
    ↓ Redirects back with code

Backend → GET /api/auth/github/callback?code=XXX&state=YYY
    ↓
    → Validates CSRF state
    ↓
    → Exchanges code for GitHub access token
    ↓
    → Fetches user profile from GitHub API
    ↓
    → Creates user in database (if new)
    ↓
    → Creates OAuthAccount record (links provider)
    ↓
    → Generates JWT token
    ↓
    → Returns JWT to frontend

Frontend → Stores JWT token
    ↓
    → Uses token in Authorization header for all requests

Backend → Validates JWT token on every request
    ↓
    → Authorizes user for protected endpoints
```

---

## 📋 System Architecture (Quick Overview)

### 4 Core OAuth Files

1. **`oauth_provider.py`** - Abstract base class (all providers inherit this)
2. **`github_oauth.py`** - GitHub OAuth implementation
3. **`oauth_manager.py`** - Factory pattern (manages all providers)
4. **`oauth_routes.py`** - REST endpoints (completely provider-agnostic)

### Key Design: Perfect Modularity

Routes don't care which provider is used:

```python
# In oauth_routes.py
@router.get("/{provider}/login")
async def login(provider: str):
    # Works with ANY provider (github, google, facebook, etc.)
    oauth = OAuthManager.get_provider(provider)
    return oauth.get_authorization_url(...)
```

This means:

- ✅ Add Google OAuth = 1 file + 1 line registration
- ✅ Add Facebook = 1 file + 1 line registration
- ✅ Routes never change
- ✅ Perfect architecture for expansion

---

## 🧪 Integration Tests (See Full Guide)

Detailed test suite in `OAUTH_INTEGRATION_TEST_GUIDE.md`:

```
Test 1: Provider Registry Check
Test 2: GitHub Login Redirect
Test 3: Full OAuth Flow
Test 4: Get Current User (JWT)
Test 5: Logout
Test 6: Multiple OAuth Accounts
```

---

## ⚙️ What's Ready

### ✅ Already Done (No Action Needed)

- [x] OAuth infrastructure built (4 files)
- [x] Routes registered in main.py
- [x] Database models created (User + OAuthAccount)
- [x] Token functions (JWTTokenManager) ready
- [x] Environment template created

### 🔄 Ready for You (Minor Setup)

- [ ] Create GitHub OAuth app (5 min)
- [ ] Add credentials to .env.local (2 min)
- [ ] Start backend (2 min)
- [ ] Run tests (4 min)

### ⏳ Ready After Testing

- [ ] Demonstrate modularity (Google OAuth template provided)
- [ ] Frontend integration (next phase)
- [ ] Production deployment (after integration)

---

## 🐛 If Something Goes Wrong

### "GitHub redirects to wrong URL"

**Fix:** Verify in GitHub OAuth app settings:

- Callback URL should be exactly: `http://localhost:8000/api/auth/github/callback`
- Homepage URL should be: `http://localhost:8000`

### "Invalid state parameter error"

**Fix:** CSRF protection activated. This is expected:

- State token must match between request and callback
- If you copy-paste callback URL, the state won't match
- Use the automatic redirect (Test 1 will generate correct state)

### "User not created in database"

**Check:**

```bash
# View all database logs
tail -f logs/application.log | grep oauth

# Check if user exists
psql -U postgres -d glad_labs_dev -c "SELECT * FROM users;"

# Check OAuth links
psql -U postgres -d glad_labs_dev -c "SELECT * FROM oauth_accounts;"
```

### "500 error on callback"

**Check backend logs:**

```bash
# In the terminal where backend is running, look for error messages
# Common causes:
# 1. Invalid GitHub Client Secret
# 2. Callback URL doesn't match exactly
# 3. JWT_SECRET not set in .env
```

---

## ✨ Next Steps

### After Step 5 (Quick Test) Succeeds

**Immediate (Now):**

```bash
# 1. Run full integration test suite
python -m pytest tests/test_e2e_fixed.py -v

# 2. Check frontend integration
cd web/oversight-hub
npm start  # Or use VS Code task
```

**Short-term (1 hour):**

```bash
# Demonstrate modularity: Add Google OAuth
# See: src/cofounder_agent/services/google_oauth_template.py
# (Template shows adding new provider = 1 file + 1 line!)
```

**Medium-term (2 hours):**

```bash
# 1. Initialize roles in database
# 2. Assign OAuth users to VIEWER role
# 3. Test RBAC (role-based access control)
```

**Longer-term (Next session):**

```bash
# 1. Frontend OAuth integration (Oversight Hub + Public Site)
# 2. Production deployment
# 3. Add more providers (Google, Facebook, etc.)
```

---

## 📚 Additional Resources

- **Full integration guide:** `OAUTH_INTEGRATION_TEST_GUIDE.md`
- **Architecture details:** `docs/02-ARCHITECTURE_AND_DESIGN.md`
- **Setup guide:** `docs/01-SETUP_AND_OVERVIEW.md`
- **Google OAuth template:** `src/cofounder_agent/services/google_oauth_template.py`

---

## 🎉 Summary

You now have:

✅ **Complete OAuth system** - Ready to authenticate users via GitHub  
✅ **Perfect modularity** - Easy to add more providers (Google, Facebook, etc.)  
✅ **JWT token management** - Secure, stateless authentication  
✅ **Database integration** - Users automatically created and linked to providers  
✅ **Production-ready architecture** - Clean, extensible, well-documented

**Time to get started:** 15 minutes  
**Result:** GitHub OAuth authentication working end-to-end 🚀

---

**Ready? Let's go!**

```bash
# 1. Create GitHub OAuth app (5 min) →
# 2. Update .env.local (2 min) →
# 3. Start backend (2 min) →
# 4. Run tests (4 min) →
# 5. Done! ✅
```

Questions? See `OAUTH_INTEGRATION_TEST_GUIDE.md` for detailed troubleshooting.
