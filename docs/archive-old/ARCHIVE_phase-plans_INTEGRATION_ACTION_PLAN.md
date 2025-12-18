# 🎯 OAuth + PostgreSQL Integration Action Plan

**Current Status:** Backend 100% Ready | Frontend 0% Ready | Database Ready to Connect  
**Estimated Time:** 2-3 hours total  
**Difficulty:** Moderate (code examples provided)

---

## 📋 IMMEDIATE ACTION ITEMS (Do These First)

### Task 1: Verify PostgreSQL Connection ⏱️ 5 minutes

**Objective:** Confirm database is accessible

**Steps:**

1. Open terminal
2. Run: `psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "SELECT NOW();"`
3. Should see current timestamp (if successful)

**If it fails:**

- Install PostgreSQL: https://www.postgresql.org/download/
- Create database: `createdb -U postgres glad_labs_dev`
- See POSTGRESQL_SETUP_GUIDE.md for detailed setup

**Status:** ⏳ BLOCKED on user execution

---

### Task 2: Add GitHub OAuth Credentials ⏱️ 10 minutes

**Objective:** Register OAuth app with GitHub

**Steps:**

1. Go to: https://github.com/settings/developers
2. Click "New OAuth App"
3. Fill in:
   - Application name: `Glad Labs Development`
   - Homepage URL: `http://localhost:3000`
   - Authorization callback URL: `http://localhost:8000/api/auth/github/callback`
4. Copy `Client ID` and `Client Secret`
5. Update `.env.local`:
   ```bash
   GITHUB_CLIENT_ID=your_client_id_here
   GITHUB_CLIENT_SECRET=your_client_secret_here
   ```

**Status:** ⏳ BLOCKED on user execution (10 min)

---

### Task 3: Verify Backend OAuth Endpoints ⏱️ 10 minutes

**Objective:** Confirm backend OAuth API is working

**Prerequisites:**

- Backend running: `npm run dev:cofounder` or `cd src/cofounder_agent && python -m uvicorn main:app --reload`

**Test Commands:**

```bash
# Test 1: Check providers
curl http://localhost:8000/api/auth/providers
# Expected: {"providers": ["github"]}

# Test 2: Check health
curl http://localhost:8000/api/health
# Expected: {"status": "healthy", ...}

# Test 3: Verify OAuth routes exist
curl http://localhost:8000/api/auth/github/login
# Expected: 302 redirect to GitHub

# If any fail, check backend logs for errors
```

**Status:** ⏳ BLOCKED on user starting backend

---

## 🚀 FRONTEND INTEGRATION (Sequential Steps)

### Phase A: Oversight Hub (React) - 45 minutes

**File 1: Update AuthContext.jsx** ⏱️ 15 minutes

**Location:** `web/oversight-hub/src/context/AuthContext.jsx`

**What to do:** Replace Firebase auth with OAuth API calls

**Complete code provided in:** FRONTEND_OAUTH_INTEGRATION_GUIDE.md (Section 5.1)

**Key changes:**

- Remove Firebase imports
- Add axios or fetch for API calls
- Update context to use JWT from localStorage
- Add handleOAuthCallback() method
- Add logout() method

---

**File 2: Update LoginForm.jsx** ⏱️ 10 minutes

**Location:** `web/oversight-hub/src/components/LoginForm.jsx`

**What to do:** Add GitHub login button

**Complete code provided in:** FRONTEND_OAUTH_INTEGRATION_GUIDE.md (Section 5.2)

**Key changes:**

- Add GitHub login button
- handleGitHubLogin() redirects to backend
- `window.location.href = ${API_BASE_URL}/api/auth/github/login`

---

**File 3: Create OAuthCallback.jsx** ⏱️ 15 minutes

**Location:** `web/oversight-hub/src/pages/OAuthCallback.jsx` (CREATE NEW)

**What to do:** Handle OAuth callback from GitHub

**Complete code provided in:** FRONTEND_OAUTH_INTEGRATION_GUIDE.md (Section 5.3)

**Key changes:**

- Extract token from URL query params
- Call AuthContext.handleOAuthCallback()
- Redirect to dashboard on success

---

**File 4: Update API Client** ⏱️ 5 minutes

**Location:** `web/oversight-hub/src/services/apiClient.js`

**What to do:** Add JWT authentication header

**Complete code provided in:** FRONTEND_OAUTH_INTEGRATION_GUIDE.md (Section 5.4)

**Key changes:**

- Get JWT from localStorage
- Add Authorization header to all requests
- Handle 401 errors (redirect to login)

---

### Phase B: Public Site (Next.js) - 45 minutes

**File 1: Update API Client** ⏱️ 15 minutes

**Location:** `web/public-site/lib/api.js`

**What to do:** Add OAuth token handling

**Complete code provided in:** FRONTEND_OAUTH_INTEGRATION_GUIDE.md (Section 6.1)

**Key changes:**

- Create getAuthToken() function
- Add Authorization header to all requests
- Handle token storage in localStorage (browser) and cookies (SSR)

---

**File 2: Create LoginLink Component** ⏱️ 15 minutes

**Location:** `web/public-site/components/LoginLink.jsx` (CREATE NEW)

**What to do:** Create login button component

**Complete code provided in:** FRONTEND_OAUTH_INTEGRATION_GUIDE.md (Section 6.2)

**Key changes:**

- Client component (use "use client")
- Button redirects to backend OAuth
- Save redirect URL for post-login

---

**File 3: Create OAuth Callback Page** ⏱️ 15 minutes

**Location:** `web/public-site/pages/auth/callback.jsx` (CREATE NEW)

**What to do:** Handle OAuth callback

**Complete code provided in:** FRONTEND_OAUTH_INTEGRATION_GUIDE.md (Section 6.3)

**Key changes:**

- Extract token from query params
- Verify token with backend
- Redirect to saved URL or home

---

## 🧪 TESTING PHASE (30 minutes)

### Backend Verification ⏱️ 5 minutes

```bash
# Test all OAuth endpoints
curl -H "Content-Type: application/json" http://localhost:8000/api/auth/providers
curl http://localhost:8000/api/health
curl http://localhost:8000/api/models
```

### Oversight Hub Manual Test ⏱️ 10 minutes

1. Open http://localhost:3001
2. Look for GitHub login button
3. Click "Login with GitHub"
4. Redirect to GitHub authorization page
5. Authorize the application
6. Should redirect back to Oversight Hub with token
7. Verify you're logged in (should show user info)
8. Check localStorage for `accessToken`

### Public Site Manual Test ⏱️ 10 minutes

1. Open http://localhost:3000
2. Look for login link (in navbar or header)
3. Click login
4. Redirect to GitHub authorization
5. Authorize the application
6. Should redirect back to public site
7. Verify you're logged in
8. Check localStorage for `accessToken`

### Database Verification ⏱️ 5 minutes

```bash
# Check users table
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "SELECT * FROM users;"

# Check OAuth accounts
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "SELECT * FROM oauth_accounts;"

# Should see your GitHub user created in both tables
```

---

## 📝 CODE EXAMPLES PROVIDED

All code examples are in: **FRONTEND_OAUTH_INTEGRATION_GUIDE.md**

**Section 5: Oversight Hub Integration**

- 5.1 AuthContext.jsx update (full code)
- 5.2 LoginForm.jsx update (full code)
- 5.3 OAuthCallback.jsx creation (full code)
- 5.4 API client update (full code)

**Section 6: Public Site Integration**

- 6.1 API client update (full code)
- 6.2 LoginLink.jsx creation (full code)
- 6.3 OAuth callback page (full code)

**All code is copy-paste ready!**

---

## ⏱️ TIMELINE

```
Pre-Integration: 5 min → Database check
Integration Setup: 10 min → GitHub credentials
Backend Testing: 10 min → Verify endpoints
Frontend Integration: 90 min → Modify 6 files
Testing: 30 min → Manual verification

TOTAL: ~2.5 hours

Expected Completion: Before end of session
```

---

## 🎯 SUCCESS CRITERIA

When complete, you should have:

✅ Users can sign in with GitHub on Oversight Hub  
✅ Users can sign in with GitHub on Public Site  
✅ JWT token stored in localStorage  
✅ User data persisted in PostgreSQL  
✅ OAuth accounts linked in database  
✅ All endpoints responding correctly  
✅ No console errors in browser DevTools  
✅ Full OAuth flow working end-to-end

---

## 🔄 NEXT PHASE (After Integration Complete)

1. Add Google OAuth (template provided)
2. Implement refresh tokens
3. Add role-based access control
4. Deploy to staging
5. Deploy to production

---

## 📞 BLOCKERS & SOLUTIONS

**Blocker 1:** "Can't connect to PostgreSQL"

- **Solution:** Run `createdb -U postgres glad_labs_dev`
- **Reference:** POSTGRESQL_SETUP_GUIDE.md

**Blocker 2:** "GitHub OAuth says invalid redirect URI"

- **Solution:** Update GitHub app settings with exact callback URL
- **Reference:** Task 2 above

**Blocker 3:** "Frontend can't reach backend API"

- **Solution:** Verify CORS is enabled in backend main.py (line 330)
- **Reference:** FRONTEND_OAUTH_INTEGRATION_GUIDE.md → Troubleshooting

**Blocker 4:** "Token not working after OAuth"

- **Solution:** Check token is stored in localStorage
- **Reference:** FRONTEND_OAUTH_INTEGRATION_GUIDE.md → Security

---

## 🚀 LET'S BEGIN!

**Next step:** Start with Task 1 above (5 minutes to verify PostgreSQL)

**Documentation available:**

- POSTGRESQL_SETUP_GUIDE.md - Database setup
- FRONTEND_OAUTH_INTEGRATION_GUIDE.md - Frontend code
- OAUTH_QUICK_START_GUIDE.md - Quick reference
- OAUTH_INTEGRATION_TEST_GUIDE.md - Detailed testing

**Everything is ready. Let's integrate!** ✅
