# End-to-End Testing Guide - Phase 1 Complete

**Last Updated:** October 25, 2025  
**Status:** ✅ Ready for Testing  
**Duration:** ~15-20 minutes for full cycle

---

## 📋 Overview

This guide walks through a complete end-to-end test of the GLAD Labs authentication and task creation system.

**Testing Path:**

1. Start all services ✅
2. Access login page
3. Create test user (or use existing)
4. Login with credentials
5. Verify Zustand state + localStorage
6. Create a blog post task
7. Monitor real-time polling
8. View metrics auto-update
9. Verify task completion
10. Test logout

---

## 🚀 Phase 1: Start Services

### Terminal 1: Backend (FastAPI + SQLAlchemy)

```powershell
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python -m uvicorn main:app --reload --port 8000
```

**Expected Output:**

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete
```

**Verification:**

- Open http://localhost:8000/docs
- Should see Swagger UI with all endpoints
- Look for: `/api/auth/login`, `/api/tasks/*`, `/api/tasks/metrics/aggregated`

### Terminal 2: Strapi CMS

```powershell
cd c:\Users\mattm\glad-labs-website\cms\strapi-main
npm run develop
```

**Expected Output:**

```
[strapi]: Starting Strapi...
Server is running at: http://localhost:1337/admin
```

**Verification:**

- Open http://localhost:1337/admin
- Strapi admin panel should load

### Terminal 3: Oversight Hub (React)

```powershell
cd c:\Users\mattm\glad-labs-website\web\oversight-hub
npm start
```

**Expected Output:**

```
webpack compiled with X warnings
Compiled successfully!
Local:   http://localhost:3000
```

**Verification:**

- Open http://localhost:3000 (or actual port from terminal)
- Should redirect to http://localhost:3000/login (or http://localhost:3001/login)

---

## 🔐 Phase 2: Authentication Test

### Step 1: Navigate to Login

Open http://localhost:3000 (or actual port)

**Expected Behavior:**

- Redirects to `/login` route
- LoginForm component displays with:
  - Email input field
  - Password input field
  - "Sign In" button
  - "Remember me" checkbox
  - Error alerts area (empty)

### Step 2: Create Test User (via API)

**Option A: Using curl/PowerShell**

```powershell
$body = @{
    email = "test@example.com"
    password = "TestPassword123!"
} | ConvertTo-Json

curl -X POST "http://localhost:8000/api/auth/register" `
  -H "Content-Type: application/json" `
  -d $body
```

**Expected Response (201 Created):**

```json
{
  "id": "uuid-here",
  "email": "test@example.com",
  "message": "User created successfully"
}
```

**Option B: Use existing demo user**

If you have a known test user already in the database, use those credentials:

- Email: `demo@example.com`
- Password: `Demo123!`

### Step 3: Login with Credentials

1. Enter email: `test@example.com`
2. Enter password: `TestPassword123!`
3. Check "Remember me" (optional)
4. Click "Sign In" button

**Expected Behavior:**

- Loading spinner appears
- Button becomes disabled
- Request sent to `/api/auth/login`
- No error message

### Step 4: Handle 2FA (if enabled)

**If 2FA is required:**

- Dialog appears: "Two-Factor Authentication"
- Prompt for TOTP code (6 digits)
- If you don't have 2FA set up, error: "2FA not enabled"

**If 2FA is not required:**

- Proceeds directly to Step 5

### Step 5: Verify Success Page

**Expected Display:**

- Checkmark icon with "Sign in successful"
- "Redirecting to dashboard in 2 seconds..."
- Auto-redirects to http://localhost:3000 (dashboard)

---

## 💾 Phase 3: Verify State & Storage

### Check Browser Storage

**Step 1: Open DevTools**

- Press `F12` to open DevTools
- Go to "Application" tab

**Step 2: Check localStorage**

Navigate to: **Application → Storage → Local Storage → http://localhost:3000**

**Expected Keys:**

- `oversight-hub-storage` - Should contain:
  ```json
  {
    "state": {
      "user": {
        "id": "uuid",
        "email": "test@example.com",
        "role": "user"
      },
      "accessToken": "eyJ0eXAiOiJKV1QiLCJhbGc...",
      "refreshToken": "eyJ0eXAiOiJKV1QiLCJhbGc...",
      "isAuthenticated": true,
      "metrics": {
        "totalTasks": 0,
        "completedTasks": 0,
        "failedTasks": 0,
        "successRate": 0,
        "avgExecutionTime": 0,
        "totalCost": 0
      }
    }
  }
  ```

### Check Zustand Store (DevTools)

**Option A: Use Redux DevTools**

If Redux DevTools extension installed (optional):

- Actions logged and playback available

**Option B: Manual Console Check**

Open DevTools **Console** tab and run:

```javascript
// Import and check store
localStorage.getItem('oversight-hub-storage');

// Parse to view
JSON.parse(localStorage.getItem('oversight-hub-storage')).state;
```

**Expected Output:**

```javascript
{
  user: { id: "...", email: "test@example.com", ... },
  accessToken: "eyJ0eXAi...",
  refreshToken: "eyJ0eXAi...",
  isAuthenticated: true,
  metrics: { totalTasks: 0, ... },
  ...
}
```

---

## 📊 Phase 4: Dashboard & Metrics

### Step 1: Dashboard Loads

**Expected Components:**

- Header: "Dashboard" with welcome message
- "Create Task" button (primary)
- MetricsDisplay section with 6 metric cards:
  - Total Tasks (0)
  - Completed (0)
  - Failed (0)
  - Success Rate (0%)
  - Avg Time (0m)
  - Total Cost ($0)
- Recent Tasks section (empty initially)

### Step 2: Verify Metrics Auto-Refresh

**Action:** Wait 30 seconds

**Expected Behavior:**

- Metrics should refresh automatically every 30 seconds
- Refresh icon shows briefly during fetch
- Last update timestamp updates
- No errors in console

---

## ✏️ Phase 5: Create Task

### Step 1: Click "Create Task" Button

**Expected Display:**

- TaskCreationModal dialog opens
- 3-step stepper:
  1. Create Task (form)
  2. Execution (in progress)
  3. Complete (result)

### Step 2: Fill Task Form

**Form Inputs:**

- **Topic** (required): `"How to Build AI Agents with FastAPI"`
- **Primary Keyword** (optional): `"FastAPI, AI Agents"`
- **Target Audience** (optional): `"Developers, AI Enthusiasts"`
- **Category** (optional): `"Technology"`

### Step 3: Submit Form

Click "Create" button

**Expected Behavior:**

- Step changes to "Execution (in progress)"
- Progress bar appears at 10%
- Stepper shows step 2 active
- Button disabled during submission

### Step 4: Monitor Polling

**Expected Behavior:**

- Poll updates every 5 seconds
- Progress bar increases: 10% → 50% → 90%
- Status shows: "pending" → "in_progress" → "completed"
- Task ID displayed (UUID)

### Step 5: Task Completes

**Expected Result:**

- Progress reaches 100%
- Step changes to "Complete (result)"
- Success icon displays
- Result shown (content preview)
- "Done" button appears

**Result Content Should Contain:**

- Blog post title
- Generated content
- Metadata (author, date, etc.)

---

## 📈 Phase 6: Verify Metrics Update

### Step 1: Close Modal

Click "Done" or close modal

### Step 2: Check Dashboard

**Expected Changes:**

- **Total Tasks**: 0 → 1
- **Completed**: 0 → 1
- **Failed**: 0 (unchanged)
- **Success Rate**: 0% → 100%
- **Recent Tasks**: Now shows the created task

### Step 3: Monitor Auto-Refresh

**Action:** Wait and watch for 2-3 refresh cycles (60 seconds)

**Expected Behavior:**

- Metrics remain consistent
- Refresh icon flashes every 30 seconds
- Last update timestamp increases
- No errors in console

---

## 🔄 Phase 7: Create Multiple Tasks (Optional)

### Repeat Task Creation (x2-3)

1. Click "Create Task" again
2. Fill different topic
3. Submit and monitor
4. Wait for completion

**Expected Metrics After 3 Tasks:**

- **Total Tasks**: 3
- **Completed**: 3
- **Success Rate**: 100%
- **Total Cost**: $0.03 (3 × $0.01)

---

## 🚪 Phase 8: Test Logout (Optional)

### Step 1: Find Logout Button

**Location:** Usually in header/sidebar

Click logout button (if visible)

### Step 2: Verify Logout Flow

**Expected Behavior:**

- Zustand store cleared:
  - `accessToken` → null
  - `isAuthenticated` → false
  - `user` → null
- localStorage cleared
- Redirects to `/login`
- Cannot access `/` (dashboard) without authenticating

### Step 3: Try Direct Navigation to Dashboard

Try accessing http://localhost:3000 directly

**Expected Behavior:**

- Should redirect to `/login`
- LoginForm displays

---

## 🐛 Phase 9: Error Handling Tests (Optional)

### Test 1: Wrong Password

1. Try login with correct email, wrong password
2. **Expected:** Error message "Invalid credentials"
3. Form remains on login step

### Test 2: Wrong Email

1. Try login with non-existent email
2. **Expected:** Error message "User not found"

### Test 3: Network Error

1. Stop backend service (Ctrl+C in Terminal 1)
2. Try to create a task
3. **Expected:** Error message "Failed to connect to API"
4. Restart backend, retry should work

### Test 4: Invalid Task Data

1. Try to submit task without required fields
2. **Expected:** Form validation error
3. Can't proceed until required fields filled

---

## 📋 Test Checklist

Use this checklist to verify all components work:

- [ ] **Services Started**
  - [ ] Backend running on port 8000
  - [ ] Strapi running on port 1337
  - [ ] Frontend running on port 3000

- [ ] **Authentication**
  - [ ] Login page loads
  - [ ] Can login with credentials
  - [ ] Tokens stored in localStorage
  - [ ] Zustand store updated
  - [ ] Redirects to dashboard after login

- [ ] **Dashboard**
  - [ ] Dashboard loads without errors
  - [ ] MetricsDisplay shows 6 metric cards
  - [ ] Metrics auto-refresh every 30 seconds
  - [ ] "Create Task" button functional

- [ ] **Task Creation**
  - [ ] Modal opens
  - [ ] Form validates required fields
  - [ ] Submitting creates task
  - [ ] Progress bar shows polling
  - [ ] Task completes with result

- [ ] **Metrics Update**
  - [ ] Total tasks increment
  - [ ] Completed tasks increment
  - [ ] Success rate calculates correctly
  - [ ] Recent tasks list updates

- [ ] **Multiple Tasks**
  - [ ] Can create 3+ tasks
  - [ ] All appear in Recent Tasks
  - [ ] Metrics aggregate correctly
  - [ ] Cost tracking accurate

- [ ] **Error Handling**
  - [ ] Wrong credentials show error
  - [ ] Network errors handled gracefully
  - [ ] Form validation prevents invalid submission

---

## 📊 Performance Metrics to Monitor

While testing, monitor these in DevTools:

**Network Tab:**

- Initial load: ~200-400ms
- Login API call: ~100-300ms
- Create task API call: ~200-500ms
- Poll task status: ~50-150ms

**Performance Tab:**

- Dashboard load: <1s
- Task creation: <2s total

**Console:**

- No errors or warnings
- No memory leaks
- Proper cleanup on unmount

---

## 🎯 Success Criteria

**✅ All tests pass if:**

1. ✅ Can login with valid credentials
2. ✅ Tokens stored in Zustand + localStorage
3. ✅ Dashboard loads with metrics
4. ✅ Can create tasks without errors
5. ✅ Real-time polling updates progress
6. ✅ Metrics update automatically after task completes
7. ✅ Multiple tasks tracked correctly
8. ✅ No console errors
9. ✅ Logout clears state and redirects

**If any fail:** Review the [Troubleshooting](#troubleshooting) section below

---

## 🔧 Troubleshooting

### Issue: "Cannot POST /api/auth/login"

**Cause:** Backend not running

**Solution:**

- Check Terminal 1 - should show "Application startup complete"
- Verify http://localhost:8000/docs loads
- Restart backend if needed

### Issue: "Redirected to login after successful login"

**Cause:** Tokens not being stored properly

**Solution:**

1. Check browser console for errors
2. Verify Zustand store is initialized:
   ```javascript
   localStorage.getItem('oversight-hub-storage');
   ```
3. Check that `isAuthenticated` is `true`

### Issue: Metrics not updating

**Cause:** API endpoint not responding

**Solution:**

1. Check http://localhost:8000/docs
2. Try endpoint manually: `GET /api/tasks/metrics/aggregated`
3. Add header: `Authorization: Bearer {your_token}`
4. Verify backend database has tasks

### Issue: Task creation fails

**Cause:** API endpoint error or database issue

**Solution:**

1. Check backend console for error messages
2. Verify task_routes.py is loaded (see /docs)
3. Try creating via curl:
   ```powershell
   curl -X POST "http://localhost:8000/api/tasks" `
     -H "Authorization: Bearer {token}" `
     -H "Content-Type: application/json" `
     -d '{"task_name": "Test", "topic": "Test Topic"}'
   ```

### Issue: Modal closes but task doesn't appear

**Cause:** Polling didn't complete or error during creation

**Solution:**

1. Check browser console for errors
2. Check backend logs for database errors
3. Try creating task again
4. Manually refresh page

### Issue: "useNavigate is not a function"

**Cause:** LoginForm not wrapped in Router context

**Solution:**

- Verify AppRoutes.jsx is imported in App.jsx
- Ensure LoginForm renders inside <Router>

---

## 📞 Quick Command Reference

### Start All Services (PowerShell)

```powershell
# Terminal 1: Backend
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent; python -m uvicorn main:app --reload --port 8000

# Terminal 2: Strapi
cd c:\Users\mattm\glad-labs-website\cms\strapi-main; npm run develop

# Terminal 3: Frontend
cd c:\Users\mattm\glad-labs-website\web\oversight-hub; npm start
```

### Test Endpoints (curl/PowerShell)

**Login:**

```powershell
curl -X POST "http://localhost:8000/api/auth/login" `
  -H "Content-Type: application/json" `
  -d '{"email":"test@example.com","password":"TestPassword123!"}'
```

**Get Metrics:**

```powershell
curl -X GET "http://localhost:8000/api/tasks/metrics/aggregated" `
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

**Create Task:**

```powershell
curl -X POST "http://localhost:8000/api/tasks" `
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" `
  -H "Content-Type: application/json" `
  -d '{
    "task_name": "Blog Post",
    "topic": "AI Agents",
    "primary_keyword": "FastAPI",
    "target_audience": "Developers",
    "category": "Tech"
  }'
```

---

## ✅ Next Steps After Testing

If all tests pass:

1. **✅ Phase 1 Complete** - Authentication system working
2. **→ Phase 2** - Add logout functionality (15 min)
3. **→ Phase 3** - Add error boundaries (30 min)
4. **→ Phase 4** - Add user notifications (20 min)
5. **→ Production Ready** - Deploy to staging

If issues found:

1. Document error with screenshot
2. Check [Troubleshooting](#troubleshooting) section
3. Review related code in components
4. Check backend logs for errors
5. File issue with details

---

**Testing Guide Created:** October 25, 2025  
**Version:** 1.0  
**Status:** Ready for Use
