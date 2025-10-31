# 🧪 Testing Guide - Authentication Strobing Fix

**Date:** October 31, 2025  
**Status:** Ready for manual testing  
**Changes to Test:** Auth strobing fix, npm workspace setup, verbose logging

---

## ✅ Pre-Testing Verification

Before testing, verify these files were successfully updated:

```powershell
# Check files exist
Test-Path "src/cofounder_agent/package.json"
Test-Path "web/oversight-hub/src/context/AuthContext.jsx"
Test-Path "web/oversight-hub/src/routes/Dashboard.jsx"
Test-Path "web/oversight-hub/STROBING_FIX.md"

# Check key changes in AuthContext
Select-String "setStoreUser\|setStoreIsAuthenticated\|storeLogout" `
  web/oversight-hub/src/context/AuthContext.jsx | Measure-Object
# Should show 15+ matches (multiple uses in function)

# Check Dashboard is simplified
Select-String "useEffect.*isAuthenticated" web/oversight-hub/src/routes/Dashboard.jsx
# Should return NO matches (effect was removed)
```

---

## 🧬 Test 1: Co-Founder Agent Startup Verification

### What We're Testing

- ✅ npm workspace setup working for Python project
- ✅ Verbose logging displaying all startup steps
- ✅ All 5 initialization steps complete successfully

### Steps

**1. Start Co-Founder Agent:**

```powershell
npm run dev:cofounder
```

**2. Expected Output (Watch for these lines):**

```
[TIMESTAMP] INFO: ======================================================================
[TIMESTAMP] INFO: 🚀 GLAD LABS AI CO-FOUNDER AGENT - STARTUP SEQUENCE
[TIMESTAMP] INFO: ======================================================================
[TIMESTAMP] INFO: ⏰ Startup time: [DATE/TIME]
[TIMESTAMP] INFO: 📍 Python version: 3.12.x
[TIMESTAMP] INFO: 📁 Working directory: C:\Users\mattm\glad-labs-website\src\cofounder_agent

[TIMESTAMP] INFO: 📥 [STEP 1/5] Loading FastAPI application...
[TIMESTAMP] INFO: ✅ FastAPI app loaded successfully

[TIMESTAMP] INFO: 📥 [STEP 2/5] Importing uvicorn server...
[TIMESTAMP] INFO: ✅ Uvicorn imported successfully

[TIMESTAMP] INFO: 📥 [STEP 3/5] Checking environment variables...
[TIMESTAMP] INFO: ✅ Environment: development

[TIMESTAMP] INFO: 📡 SERVER CONFIGURATION
[TIMESTAMP] INFO: 🌐 Host: 0.0.0.0
[TIMESTAMP] INFO: 🔌 Port: 8000
[TIMESTAMP] INFO: ✨ Server initialization complete!
[TIMESTAMP] INFO: 🎯 Access the server at: http://localhost:8000
```

**3. Verify Startup:**

- ✅ All 5 steps show with status indicators
- ✅ No errors or warnings in output
- ✅ Server accessible at http://localhost:8000

**4. Test API:**

```powershell
# In new terminal:
curl http://localhost:8000/docs
# Should show Swagger UI

curl http://localhost:8000/api/health
# Should return: {"status": "healthy"}
```

**5. Pass/Fail:**

- ✅ PASS: All 5 steps show, server runs, API responds
- ❌ FAIL: Any step missing, server won't start, API errors

---

## 🔐 Test 2: Authentication Strobing Fix

### What We're Testing

- ✅ No more flashing between dashboard and login
- ✅ Auth state consistent across components
- ✅ Smooth login/logout flow

### Setup

**1. Ensure Oversight Hub is running:**

```powershell
npm run dev:oversight
# Should start on port 3001
```

**2. Clear browser state (important!):**

```javascript
// Open browser DevTools (F12) → Console → paste:
localStorage.clear();
sessionStorage.clear();
location.reload();
```

### Test Flow A: First Login (No Cached Auth)

**1. Navigate to App:**

- URL: http://localhost:3001
- Expected: Redirects to /login
- Observe: Single redirect, smooth transition to login page

**2. Open Browser Console:**

- Press F12 → Console tab
- Look for logs starting with 🔐 [AuthContext]
- Expected:
  ```
  🔐 [AuthContext] Starting authentication initialization...
  🔍 [AuthContext] No cached session, verifying with backend...
  (or "✅ [AuthContext] Found stored user and token, using cached session")
  ```

**3. Click "Sign in (Mock)":**

- Shows "Authenticating..." briefly
- Logs should show: 👤 [AuthContext] Setting user: dev-user
- Should load dashboard smoothly
- ❌ NOT Expected: "Redirecting to login..." message
- ❌ NOT Expected: Flashing back to login page

**4. Verify Dashboard Loads:**

- Dashboard displays without strobing
- Can see tasks, settings buttons, etc.
- No redirects happening
- Browser console shows no errors

**Pass Criteria:**

- ✅ Single redirect to /login
- ✅ Login page loads smoothly
- ✅ Click sign in → single redirect to dashboard
- ✅ Dashboard displays without any strobing
- ✅ No 401/403 errors in Network tab
- ✅ Auth logs show proper initialization

**Fail Criteria:**

- ❌ Multiple redirects (strobing)
- ❌ "Redirecting to login..." message visible
- ❌ Flashing between pages
- ❌ 401/403 errors in Network tab
- ❌ Console errors related to auth

### Test Flow B: Cached Auth (Page Reload)

**1. After logging in, reload page:**

```
Press F12 (keep console open)
Press Ctrl+R (reload)
```

**2. Expected Behavior:**

- Console shows: ✅ [AuthContext] Found stored user and token, using cached session
- Dashboard loads IMMEDIATELY (no redirect to login)
- No loading spinner
- Auth logs show re-hydration from localStorage

**3. Pass Criteria:**

- ✅ Dashboard loads immediately (no /login redirect)
- ✅ Auth logs show cached session used
- ✅ Smooth transition, no strobing

**4. Fail Criteria:**

- ❌ Redirects to /login on reload
- ❌ Brief flash to login then back to dashboard
- ❌ Auth logs show "No cached session" when localStorage has user

### Test Flow C: Logout

**1. While on dashboard:**

- Click user menu (top right) → Logout
- Expected: Clean redirect to /login page

**2. Observe Console:**

- Should see: 🚪 [AuthContext] Logging out...
- localStorage should be cleared

**3. Pass Criteria:**

- ✅ Single redirect to /login
- ✅ No more dashboard visible
- ✅ Logout logs appear
- ✅ localStorage cleared

**4. Fail Criteria:**

- ❌ Hangs on dashboard
- ❌ Multiple redirects during logout
- ❌ Logout logs don't appear

### Test Flow D: Login Again After Logout

**1. From /login page, click "Sign in (Mock)" again:**

**2. Expected:**

- Same smooth flow as Test Flow A
- Dashboard loads without strobing
- Fresh auth logs show initialization

**3. Pass Criteria:**

- ✅ Same smooth behavior as first login
- ✅ No strobing or flashing

---

## 🔄 Test 3: Full System Integration

### What We're Testing

- ✅ All services start together without conflicts
- ✅ Frontend can communicate with backend (if applicable)

### Steps

**1. Stop all running services:**

```powershell
# Stop all npm processes
taskkill /IM node.exe /F 2>&1 | out-null

# Wait 2 seconds
Start-Sleep -Seconds 2
```

**2. Start everything:**

```powershell
npm run dev
# Starts:
# Terminal 1: Co-Founder Agent (8000)
# Terminal 2: Strapi CMS (1337)
# Terminal 3: Public Site (3000)
# Terminal 4: Oversight Hub (3001)
```

**3. Wait for all services to be ready (~30-45 seconds):**

- Watch for "ready" messages in each terminal
- Check ports:
  ```powershell
  netstat -ano | findstr ":8000|:1337|:3000|:3001"
  ```

**4. Test Each Service:**

```powershell
# Co-Founder Agent
curl http://localhost:8000/api/health

# Strapi (might show login)
curl http://localhost:1337/

# Public Site
curl http://localhost:3000/

# Oversight Hub
curl http://localhost:3001/
```

**5. Test Frontend in Browser:**

- Public Site: http://localhost:3000
  - Should load homepage
  - No console errors

- Oversight Hub: http://localhost:3001
  - Should redirect to /login
  - Follow login test flow (Test Flow A)

**Pass Criteria:**

- ✅ All 4 services start in separate terminals
- ✅ All ports listening (netstat shows :8000, :1337, :3000, :3001)
- ✅ All HTTP endpoints respond
- ✅ Frontend auth works without strobing
- ✅ No CORS errors in console

**Fail Criteria:**

- ❌ Any service fails to start
- ❌ Port conflicts
- ❌ HTTP endpoints return 500 errors
- ❌ CORS errors in browser console
- ❌ Auth strobing returns

---

## 📊 Browser Console Monitoring

### Key Logs to Watch For

**Initialization (app startup):**

```
🔐 [AuthContext] Starting authentication initialization...
```

**Session found:**

```
✅ [AuthContext] Found stored user and token, using cached session
```

**No session (needs backend verify):**

```
🔍 [AuthContext] No cached session, verifying with backend...
```

**User set in context:**

```
👤 [AuthContext] Setting user: dev-user
```

**Logout:**

```
🚪 [AuthContext] Logging out...
```

### Checking Console Logs in Browser

**1. Open Outlook Hub:**

- http://localhost:3001

**2. Open DevTools:**

- Press F12

**3. Go to Console tab:**

- Should show auth logs with 🔐 emoji

**4. If no logs appear:**

- Check that console.log() is enabled in console
- Check for "Log levels" filter - make sure "Verbose" is selected
- Hard refresh: Ctrl+Shift+R

---

## 🐛 Troubleshooting

### Problem: "Something is already running on port 3001"

```powershell
# Kill existing process
taskkill /IM node.exe /F

# Or find specific process
lsof -i :3001
```

### Problem: Strobing still happening

1. Clear browser cache: Ctrl+Shift+Delete
2. Clear localStorage: `localStorage.clear()` in console
3. Hard refresh: Ctrl+Shift+R
4. Check that AuthContext imports are working:
   ```javascript
   // In browser console:
   useAuth(); // Should show user object
   ```

### Problem: No auth logs in console

1. Make sure console.log level is set to "All"
2. Open DevTools BEFORE loading page
3. Check that AuthContext.jsx has console.log calls
4. Verify file was saved correctly

### Problem: "Redirecting to login..." message still shows

1. This line should be removed from Dashboard.jsx
2. Check file has been updated correctly
3. Hard refresh browser: Ctrl+Shift+R
4. Rebuild if needed: `npm run build --workspace=web/oversight-hub`

### Problem: Co-Founder Agent won't start

1. Check Python is available: `python --version`
2. Check requirements installed: `pip list | findstr -i fastapi uvicorn`
3. Check port 8000 is free: `netstat -ano | findstr :8000`
4. Check error logs: Look for specific error message in terminal

---

## 📋 Testing Checklist

### Startup Tests

- [ ] Co-Founder Agent starts with 5-step verbose logging
- [ ] All 5 steps show with ✅ status
- [ ] Co-Founder Agent API responds at http://localhost:8000/api/health

### Auth Strobing Tests

- [ ] First login: Single redirect to /login, no strobing
- [ ] After sign in: Single redirect to dashboard, no flashing
- [ ] Page reload: Dashboard loads immediately from cache
- [ ] Logout: Clean single redirect to /login
- [ ] Re-login: Same smooth flow as first login

### Integration Tests

- [ ] All 4 services start together via `npm run dev`
- [ ] All ports listening (8000, 1337, 3000, 3001)
- [ ] Frontend loads without console errors
- [ ] Auth logs show proper initialization
- [ ] No CORS errors during flow

### Network Tests

- [ ] No 401/403 errors in Network tab during auth flow
- [ ] No failed requests to backend
- [ ] localStorage updated on login/logout
- [ ] API calls succeed where applicable

---

## ✅ Success Criteria Summary

**Strobing Fix is SUCCESS if:**

1. ✅ No flashing between dashboard and login
2. ✅ Login flows smoothly with single redirect
3. ✅ Dashboard loads immediately on cached sessions
4. ✅ Logout is clean with single redirect
5. ✅ No "Redirecting to login..." messages on dashboard
6. ✅ Console logs show proper auth initialization
7. ✅ Can repeatedly login/logout without issues

**If ALL checks pass: 🎉 SESSION FIXES COMPLETE AND VERIFIED!**

---

## 📝 Report Results

After testing, update this section:

```
## Test Results - [Your Name] - [Date/Time]

### Co-Founder Agent Startup
- Startup verbose logging: PASS / FAIL
- All 5 steps shown: PASS / FAIL
- API responds: PASS / FAIL

### Auth Strobing Fix
- First login smooth: PASS / FAIL
- Page reload cached: PASS / FAIL
- Logout clean: PASS / FAIL
- Re-login works: PASS / FAIL

### Full Integration
- All services start: PASS / FAIL
- All ports listening: PASS / FAIL
- No console errors: PASS / FAIL

### Overall
- STATUS: COMPLETE ✅ / ISSUES FOUND ⚠️
- Notes: [Any issues or observations]
```

---

**Ready to test!** 🚀

Navigate to http://localhost:3001 and follow the test flows above.
