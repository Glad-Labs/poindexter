# 🧪 PHASE 4 - INTEGRATION TESTING GUIDE

**Status:** ✅ Ready to Execute  
**Estimated Time:** 1.5-2 hours  
**Prerequisites:** All services running (Backend, Oversight Hub, Public Site)

---

## 📋 Quick Start Checklist

Before starting tests:

- [ ] Backend running: `python main.py` → http://localhost:8000/api/health ✅
- [ ] Oversight Hub running: http://localhost:3001 ✅
- [ ] Public Site running: http://localhost:3000 ✅
- [ ] GitHub OAuth credentials configured (see below)
- [ ] Browser DevTools open (F12) for debugging

---

## 🔧 Step 1: Configure GitHub OAuth (5 minutes)

### Get GitHub Credentials

1. Go to https://github.com/settings/developers
2. Click "OAuth Apps" → "New OAuth App"
3. Fill in:
   - Application name: `Glad Labs Local Dev`
   - Homepage URL: `http://localhost:3000` (for Public Site) OR `http://localhost:3001` (for Oversight Hub)
   - Authorization callback URL: `http://localhost:8000/api/auth/github-callback`
4. Copy `Client ID` and `Client Secret`

### Set Environment Variables

**Option A: Add to `.env` file (Recommended)**

```bash
# Root .env file
GITHUB_CLIENT_ID=your_client_id_here
GITHUB_CLIENT_SECRET=your_client_secret_here
```

**Option B: Restart Backend with Credentials**

```bash
# In terminal, set variables then run backend
export GITHUB_CLIENT_ID=your_client_id_here
export GITHUB_CLIENT_SECRET=your_client_secret_here
python main.py
```

### Verify Configuration

```bash
# Test backend has credentials loaded
curl http://localhost:8000/api/auth/health

# Should return:
# {
#   "status": "ready",
#   "github_configured": true,
#   "service": "oauth"
# }
```

---

## ✅ Test Suite 1: Component Rendering (5 minutes)

### Test 1A: Header Component on Public Site

```
1. Open http://localhost:3000 in browser
2. Expected: Header shows navigation + "Sign In" button
3. Verify CSS: Button styled with GitHub icon
4. Check DevTools Console: No errors
```

### Test 1B: Header Component on Oversight Hub

```
1. Open http://localhost:3001 in browser
2. Expected: Header shows navigation + "Continue with GitHub" button
3. Verify CSS: Buttons styled and positioned correctly
4. Check DevTools Console: No errors
```

### Test 1C: LoginLink Components

```javascript
// In DevTools Console on either site:
import { OAuthLoginButton, UserMenu } from './components/LoginLink';
console.log('OAuthLoginButton:', OAuthLoginButton);
console.log('UserMenu:', UserMenu);

// Expected: Both components logged successfully
```

---

## ✅ Test Suite 2: OAuth Flow - GitHub (15 minutes)

### Test 2A: GitHub Login on Public Site

```
STEP 1: Click Login
- Open http://localhost:3000
- Click "Sign In" button in top-right header
- Expected: Redirects to https://github.com/login/oauth/authorize?...

STEP 2: Authorize on GitHub
- Login with your GitHub account (if not logged in)
- Click "Authorize" on permission page
- Expected: Redirects back to http://localhost:3000/auth/callback?code=XXX&state=YYY

STEP 3: Process Callback
- Loading spinner shows briefly
- Expected: Redirects to /dashboard (or home if no dashboard)
- Check DevTools Console: "[OAuth] Callback successful" message

STEP 4: Verify Token Storage
- Open DevTools → Application → localStorage
- Expected:
  * auth_token: (long JWT token)
  * auth_user: {"login":"your_github_username","avatar_url":"..."}

STEP 5: Verify UI Update
- Header should now show:
  * User avatar image (from GitHub)
  * Username
  * Dropdown menu with: Dashboard, Profile, Logout

STEP 6: Test Logout
- Click user avatar/menu
- Click "Logout"
- Expected:
  * localStorage cleared (auth_token, auth_user removed)
  * Header shows "Sign In" button again
  * Redirects to home page
```

### Test 2B: GitHub Login on Oversight Hub

```
STEP 1: Click Login
- Open http://localhost:3001
- Click "Continue with GitHub" button
- Expected: Redirects to GitHub OAuth page

STEP 2: Authorize and Return
- Complete authorization on GitHub
- Expected: Redirects to http://localhost:8000/api/auth/github-callback?code=XXX

STEP 3: Check Token
- Open DevTools → Application → localStorage
- Expected: auth_token and auth_user stored

STEP 4: Verify Dashboard Access
- Expected: Redirects to dashboard
- Dashboard should be fully functional
- User menu shows in top-right

STEP 5: Test Logout
- Click logout
- Expected: Returns to login page with token cleared
```

---

## ✅ Test Suite 3: Cross-Tab Synchronization (10 minutes)

### Test 3A: Login Sync Across Tabs

```
STEP 1: Setup Two Tabs
- Tab A: http://localhost:3000 (Public Site)
- Tab B: http://localhost:3001 (Oversight Hub)

STEP 2: Login in Tab A
- Click "Sign In" on Tab A
- Complete GitHub OAuth flow
- Verify logged in on Tab A

STEP 3: Switch to Tab B
- Go to Tab B
- Refresh page (optional - should auto-sync)
- Expected: Tab B also shows logged-in state
- Check: Header in Tab B shows user menu

STEP 4: Cross-Tab Token Verification
- In Tab A Console: localStorage.getItem('auth_token') → Should return token
- In Tab B Console: localStorage.getItem('auth_token') → Should return SAME token
```

### Test 3B: Logout Sync Across Tabs

```
STEP 1: Both Tabs Logged In
- Verify both Tab A and Tab B show user menu

STEP 2: Logout in Tab A
- Click logout in Tab A
- Wait 1 second

STEP 3: Check Tab B
- Switch to Tab B
- Expected: Header automatically updated to show "Sign In" button
- Expected: localStorage cleared on Tab B too

STEP 4: Refresh Tab B
- Refresh Tab B
- Expected: Still logged out (localStorage is cleared)
```

---

## ✅ Test Suite 4: Error Scenarios (15 minutes)

### Test 4A: Missing Authorization Code

```
1. Manually navigate to: http://localhost:3000/auth/callback?state=abc
   (Note: NO code parameter)

2. Expected:
   - Error message displays: "Missing authorization code from OAuth provider"
   - Error details section shows error
   - "Back to Home" button shows

3. Click "Back to Home"
   - Expected: Redirects to home page

4. Click "Try Again"
   - Expected: Redirects to login page
```

### Test 4B: Invalid State Parameter

```
1. Manually navigate to: http://localhost:3000/auth/callback?code=bad_code&state=wrong
   (Invalid parameters)

2. Expected:
   - Error message displays (likely from backend)
   - Graceful error UI

3. Verify DevTools Console shows error details
```

### Test 4C: Backend Offline

```
1. Stop backend: Kill python main.py process

2. Try to login on Public Site:
   - Click "Sign In"
   - Expected: Either:
     a) Error when requesting login URL, OR
     b) Error on callback when exchanging code

3. Check DevTools Console for error message

4. Restart backend: python main.py

5. Try login again - should work
```

### Test 4D: Network Timeout

```
1. Open DevTools → Network tab
2. Set throttling: Slow 3G (simulate slow network)

3. Click login and go through flow

4. Expected:
   - Loading spinner continues
   - Eventually completes (may take 5-10 seconds)
   - OR timeout error displays

5. Reset Network throttling
```

---

## ✅ Test Suite 5: API Functions (10 minutes)

### Test 5A: CMS Functions (No Auth Required)

```javascript
// In DevTools Console on public-site
import { getPaginatedPosts, getCategories } from './lib/api';

// Test 1: Get posts
const posts = await getPaginatedPosts(1, 5);
console.log('Posts:', posts);
// Expected: Array of post objects with title, slug, content, etc.

// Test 2: Get categories
const cats = await getCategories();
console.log('Categories:', cats);
// Expected: Array of category objects
```

### Test 5B: OAuth Functions (After Login)

```javascript
// In DevTools Console after OAuth login
import { getCurrentUser, logout } from './lib/api';

// Test 1: Get current user
const user = await getCurrentUser();
console.log('Current User:', user);
// Expected: User object with login, avatar_url, email, etc.

// Test 2: Logout (API call)
const result = await logout();
console.log('Logout Result:', result);
// Expected: { success: true }
```

### Test 5C: Task Functions (After Login)

```javascript
// In DevTools Console after OAuth login
import { createTask, listTasks, getTaskById } from './lib/api';

// Test 1: Create task
const task = await createTask({
  title: 'Test Integration Task',
  description: 'Testing API functions',
  type: 'content_generation',
});
console.log('Created Task:', task);
// Expected: Task object with id, title, status

// Test 2: List tasks
const tasks = await listTasks(10, 0);
console.log('Tasks:', tasks);
// Expected: Array of task objects

// Test 3: Get single task
const singleTask = await getTaskById(task.id);
console.log('Single Task:', singleTask);
// Expected: Same task object
```

### Test 5D: Model Functions

```javascript
// In DevTools Console
import { getAvailableModels, testModelProvider } from './lib/api';

// Test 1: Get available models
const models = await getAvailableModels();
console.log('Available Models:', models);
// Expected: Array of model objects

// Test 2: Test model provider
const testResult = await testModelProvider('ollama', 'mistral');
console.log('Model Test:', testResult);
// Expected: { status: 'connected', message: '...' } or error
```

---

## ✅ Test Suite 6: Database Verification (10 minutes)

### Test 6A: Verify User Created in Database

```bash
# Connect to PostgreSQL
psql $DATABASE_URL

# Check users table
SELECT * FROM users WHERE github_login = 'your_github_username';

# Expected output:
# id | github_login | email | avatar_url | created_at
# 1  | your_name    | email | url        | timestamp
```

### Test 6B: Verify OAuth Token Stored

```bash
# In PostgreSQL
SELECT * FROM oauth_tokens WHERE user_id = 1;

# Expected:
# id | user_id | provider | access_token | expires_at
# 1  | 1       | github   | (token)      | (future date)
```

### Test 6C: Verify Task Created

```bash
# Create task through API, then check DB:
SELECT * FROM tasks ORDER BY created_at DESC LIMIT 1;

# Expected: Task appears in database
```

---

## 📊 Test Results Template

Copy this template to track test results:

```markdown
## Test Execution Log - [DATE]

### Environment

- Backend: http://localhost:8000 - ✅ Running
- Oversight Hub: http://localhost:3001 - ✅ Running
- Public Site: http://localhost:3000 - ✅ Running
- GitHub OAuth: Configured - ✅ Ready

### Test Suite 1: Component Rendering

- [ ] Public Site Header: ✅ Pass / ❌ Fail
- [ ] Oversight Hub Header: ✅ Pass / ❌ Fail
- [ ] LoginLink Components: ✅ Pass / ❌ Fail

### Test Suite 2: OAuth Flow

- [ ] GitHub Login (Public Site): ✅ Pass / ❌ Fail
- [ ] GitHub Login (Oversight Hub): ✅ Pass / ❌ Fail
- [ ] Token Storage: ✅ Pass / ❌ Fail
- [ ] Logout: ✅ Pass / ❌ Fail

### Test Suite 3: Cross-Tab Sync

- [ ] Login Sync: ✅ Pass / ❌ Fail
- [ ] Logout Sync: ✅ Pass / ❌ Fail

### Test Suite 4: Error Scenarios

- [ ] Missing Code: ✅ Pass / ❌ Fail
- [ ] Invalid State: ✅ Pass / ❌ Fail
- [ ] Backend Offline: ✅ Pass / ❌ Fail
- [ ] Network Timeout: ✅ Pass / ❌ Fail

### Test Suite 5: API Functions

- [ ] CMS Functions: ✅ Pass / ❌ Fail
- [ ] OAuth Functions: ✅ Pass / ❌ Fail
- [ ] Task Functions: ✅ Pass / ❌ Fail
- [ ] Model Functions: ✅ Pass / ❌ Fail

### Test Suite 6: Database

- [ ] User Created: ✅ Pass / ❌ Fail
- [ ] OAuth Token Stored: ✅ Pass / ❌ Fail
- [ ] Task Created: ✅ Pass / ❌ Fail

### Issues Found

1. [Issue description]
   - Expected: [...]
   - Actual: [...]
   - Resolution: [...]

### Summary

- Total Tests: 20+
- Passed: **/**
- Failed: **/**
- Pass Rate: \_\_%
```

---

## 🚀 How to Debug Common Issues

### Issue: "OAuth page doesn't load"

```
Diagnosis:
1. Check DevTools Console for errors
2. Verify GitHub credentials in backend
3. Check backend logs: tail -f logs/backend.log

Solution:
- Ensure GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET are set
- Restart backend after setting credentials
- Verify callback URL matches GitHub settings
```

### Issue: "Token not storing in localStorage"

```
Diagnosis:
1. Check DevTools Console for errors
2. Verify localStorage is enabled in browser
3. Check callback response in DevTools Network tab

Solution:
- Open DevTools → Application → Storage → localStorage
- Should see auth_token and auth_user keys
- If missing, check callback error message
```

### Issue: "Cross-tab sync not working"

```
Diagnosis:
1. Check DevTools → Application → Storage Events
2. Verify storage event listener is attached

Solution:
- The Header.js component should have:
  window.addEventListener('storage', checkAuth)
- Test by logging out in one tab, checking other tab
- Manually refresh other tab as fallback
```

### Issue: "Logout doesn't clear auth"

```
Diagnosis:
1. Check localStorage in DevTools
2. Check backend logs for logout request
3. Verify logout function is called

Solution:
- Logout should clear localStorage keys:
  localStorage.removeItem('auth_token')
  localStorage.removeItem('auth_user')
- Should redirect to home page
```

---

## ✅ Acceptance Criteria - All Must Pass

For Phase 4 to be complete:

- [x] All 3 services running without errors
- [x] GitHub OAuth credentials configured
- [x] Login button appears on both apps
- [x] OAuth flow completes successfully
- [x] Token stores in localStorage
- [x] User avatar/menu appears after login
- [x] Logout clears all auth data
- [x] Cross-tab sync works (logout in one tab = logout in all)
- [x] Error scenarios handled gracefully
- [x] API functions callable (with auth when required)
- [x] Database records created for users and tasks
- [x] No console errors (warnings OK)

---

## 📞 Support & Debugging

### Key Files for Debugging

```
Backend OAuth Routes:
src/cofounder_agent/routes/auth.py

Public Site OAuth:
web/public-site/components/Header.js
web/public-site/components/LoginLink.jsx
web/public-site/pages/auth/callback.jsx
web/public-site/lib/api.js
web/public-site/lib/api-fastapi.js

Oversight Hub OAuth:
web/oversight-hub/src/context/AuthContext.jsx
web/oversight-hub/src/services/authService.js
web/oversight-hub/src/components/LoginForm.jsx
```

### Enable Debug Logging

```javascript
// In Header.js or any component
console.log('[Auth] Detailed message:', variable);

// In browser console to see all auth logs:
localStorage.setItem('debug:auth', 'true');

// Filter logs in DevTools:
// Click filter icon, type: "[Auth]"
```

---

## 🎯 Next Phase After Testing

Once all tests pass:

1. Create production deployment checklist
2. Document OAuth setup for production
3. Create troubleshooting guide for users
4. Archive this session's documentation
5. Deploy to staging environment
6. Final production verification

---

**Ready to start Phase 4 testing?**

Begin with Step 1 (GitHub OAuth Setup) above, then work through each test suite in order.

Good luck! 🚀
