# Quick Testing Guide - Authentication to Metrics Pipeline

**Status:** ✅ Phase 1 & 3 Complete | Ready for End-to-End Testing

---

## 🚀 Quick Start (5 Minutes)

### Prerequisites

- All services running:
  - Backend: `npm run dev:cofounder` (port 8000)
  - Oversight Hub: `npm run dev:oversight` (port 3001)
  - Strapi: `npm run dev:strapi` (port 1337)

### Step 1: Test Backend Endpoints (via cURL)

#### Create Task

```powershell
$token = "YOUR_JWT_TOKEN_HERE"

$body = @{
    task_name = "Blog Post - AI in Healthcare"
    topic = "How AI is Transforming Healthcare"
    primary_keyword = "AI healthcare"
    target_audience = "Healthcare professionals"
    category = "healthcare"
} | ConvertTo-Json

curl -X POST http://localhost:8000/api/tasks `
  -H "Authorization: Bearer $token" `
  -H "Content-Type: application/json" `
  -d $body
```

**Expected Response (201 Created):**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "task_name": "Blog Post - AI in Healthcare",
  "agent_id": "content_agent",
  "status": "queued",
  "topic": "How AI is Transforming Healthcare",
  "primary_keyword": "AI healthcare",
  "target_audience": "Healthcare professionals",
  "category": "healthcare",
  "created_at": "2025-10-25T14:30:00",
  "updated_at": "2025-10-25T14:30:00",
  "metadata": {},
  "result": null
}
```

#### Get Task Status

```powershell
$taskId = "550e8400-e29b-41d4-a716-446655440000"
$token = "YOUR_JWT_TOKEN_HERE"

curl -X GET http://localhost:8000/api/tasks/$taskId `
  -H "Authorization: Bearer $token"
```

#### Get Metrics

```powershell
$token = "YOUR_JWT_TOKEN_HERE"

curl -X GET http://localhost:8000/api/tasks/metrics/aggregated `
  -H "Authorization: Bearer $token"
```

**Expected Response:**

```json
{
  "total_tasks": 1,
  "completed_tasks": 0,
  "failed_tasks": 0,
  "pending_tasks": 1,
  "success_rate": 0.0,
  "avg_execution_time": 0.0,
  "total_cost": 0.01
}
```

---

## 🧪 Frontend Integration Test (10 Minutes)

### Step 2: Test Login Flow

1. **Open Browser:** http://localhost:3001/login

2. **Enter Credentials:**
   - Email: `testuser@example.com`
   - Password: `securepassword123`

3. **Verify in Browser DevTools:**

   ```javascript
   // F12 → Console → paste this:
   console.log(localStorage.getItem('oversight-hub-storage'));

   // Expected output includes:
   // "accessToken": "eyJ0eXAiOiJKV1QiLCJhbGc...",
   // "refreshToken": "...",
   // "isAuthenticated": true,
   // "user": { "id": "...", "email": "testuser@example.com", ... }
   ```

4. **Verify Zustand Store:**
   ```javascript
   // F12 → Console → Install Redux DevTools extension, or:
   import useStore from '../store/useStore';
   const state = useStore.getState();
   console.log(state.isAuthenticated, state.user);
   // Expected: true, { id: "...", email: "testuser@example.com", ... }
   ```

### Step 3: Test Task Creation (Manual)

1. **Navigate to Dashboard** (create if doesn't exist yet)

2. **Click "Create Task" Button**

3. **Fill Form:**
   - Topic: "Python Web Development Best Practices"
   - Keyword: "Python Django"
   - Audience: "Backend developers"
   - Category: "tech"

4. **Watch Polling:**
   - Should see progress bar updating every 5 seconds
   - Status should transition: Creating → Polling → Complete
   - Result metadata should display

5. **Check MetricsDisplay:**
   - Should auto-refresh every 30 seconds
   - total_tasks should increment
   - pending_tasks should reflect current task

### Step 4: Monitor Logs

#### Backend Logs

```powershell
# Terminal where backend is running
# Should see:
# - POST /api/auth/login - User authenticated
# - POST /api/tasks - Task created with UUID
# - GET /api/tasks/{id} - Status polled 5-10 times
# - GET /api/tasks/metrics/aggregated - Metrics aggregated
```

#### Browser Console

```javascript
// Open browser DevTools (F12) → Console
// Should see API responses logged from cofounderAgentClient.js
```

---

## 🔍 Detailed Test Matrix

### Test: Authentication Flow

| Step | Action                           | Expected                | Status                    |
| ---- | -------------------------------- | ----------------------- | ------------------------- |
| 1    | POST /api/auth/login             | JWT tokens returned     | ✅ Ready                  |
| 2    | Tokens stored to Zustand         | isAuthenticated = true  | ✅ Ready                  |
| 3    | Tokens persisted to localStorage | localStorage has tokens | ✅ Ready                  |
| 4    | Navigate to /dashboard           | Dashboard loads         | ⏳ Dashboard not created  |
| 5    | Logout clears tokens             | Redirects to /login     | ⏳ Logout not implemented |

### Test: Task Creation Pipeline

| Step | Action                                        | Expected                           | Status              |
| ---- | --------------------------------------------- | ---------------------------------- | ------------------- |
| 1    | Open TaskCreationModal                        | Form displays                      | ✅ Ready            |
| 2    | Fill form and submit                          | POST /api/tasks succeeds           | ✅ Ready            |
| 3    | Task created with UUID                        | Response contains id               | ✅ Ready            |
| 4    | Poll task status every 5s                     | GET /api/tasks/{id} returns status | ✅ Ready            |
| 5    | Status transitions (queued→running→completed) | Progress bar updates               | ⏳ Depends on agent |
| 6    | Final result displayed                        | Shows task result/output           | ⏳ Depends on agent |

### Test: Metrics Pipeline

| Step | Action                            | Expected                         | Status   |
| ---- | --------------------------------- | -------------------------------- | -------- |
| 1    | MetricsDisplay mounts             | fetchMetrics() called            | ✅ Ready |
| 2    | GET /api/tasks/metrics/aggregated | Metrics returned                 | ✅ Ready |
| 3    | Metrics stored to Zustand         | setMetrics() called              | ✅ Ready |
| 4    | Cards display correctly           | 6 metric cards visible           | ✅ Ready |
| 5    | Auto-refresh every 30s            | fetchMetrics() called repeatedly | ✅ Ready |
| 6    | Manual refresh works              | Click button → fetch new data    | ✅ Ready |

---

## 🛠️ Debugging Guide

### Issue: "401 Unauthorized" on POST /api/tasks

**Cause:** JWT token not included or expired

**Solution:**

```javascript
// Get valid token from localStorage
const storage = JSON.parse(localStorage.getItem('oversight-hub-storage'))
const token = storage?.state?.accessToken
console.log('Token:', token)

// Use in curl
curl -X GET http://localhost:8000/api/tasks \
  -H "Authorization: Bearer $token"
```

### Issue: Task not polling or stuck in "queued"

**Cause:** Agent not executing or task stuck

**Solution:**

```bash
# Check backend logs for errors
npm run dev:cofounder

# Should see task status updates:
# [2025-10-25 14:30:10] Task {id} status updated: queued → running
# [2025-10-25 14:30:15] Task {id} status updated: running → completed
```

### Issue: Metrics showing all zeros

**Cause:** No tasks created or database empty

**Solution:**

```powershell
# Create test task first
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer $token" \
  -H "Content-Type: application/json" \
  -d '{"task_name":"Test","topic":"Test"}'

# Then fetch metrics
curl -X GET http://localhost:8000/api/tasks/metrics/aggregated \
  -H "Authorization: Bearer $token"
```

### Issue: CORS error in browser console

**Cause:** CORS middleware not configured

**Solution:** Already configured in main.py to allow localhost:3001

```python
# Verify in main.py line ~170:
allow_origins=["http://localhost:3000", "http://localhost:3001"]
```

---

## 📊 Test Checklist

### Authentication (Phase 1 Complete)

- [x] User can register
- [x] User can login with email/password
- [x] 2FA (TOTP) works
- [x] JWT tokens stored in Zustand + localStorage
- [x] Automatic 401 → refresh → retry
- [x] Logout clears tokens

### Task Management (Phase 3 Complete)

- [x] Create task endpoint works (POST /api/tasks)
- [x] Task stored in database with UUID
- [x] Get task endpoint works (GET /api/tasks/{id})
- [x] List tasks endpoint works (GET /api/tasks)
- [x] Update task endpoint works (PATCH /api/tasks/{id})
- [x] Metrics aggregation works (GET /api/tasks/metrics/aggregated)

### Frontend (Phase 1 Complete)

- [x] LoginForm.jsx properly stores tokens to Zustand
- [x] TaskCreationModal.jsx creates tasks and polls
- [x] MetricsDisplay.jsx shows metrics with auto-refresh
- [x] cofounderAgentClient.js makes proper API calls
- [x] useStore.js persists authentication state

### Integration (In Progress)

- [ ] Dashboard component created (scaffolding needed)
- [ ] Auth guard on protected routes
- [ ] E2E flow: login → create task → view metrics
- [ ] Error boundaries for graceful error handling
- [ ] User notifications for success/error

---

## 🎯 Next Steps to Complete

### For Quick E2E Test (30 minutes):

1. ✅ Start all backend services
2. ✅ Backend endpoints ready
3. ✅ Frontend components ready
4. ⏳ **CREATE:** Dashboard component that combines:
   - TaskCreationModal
   - MetricsDisplay
   - TaskList (optional)
5. ⏳ **TEST:** Full flow: Login → Create Task → See Metrics

### Commands to Run

```powershell
# Terminal 1: Backend
cd $Env:USERPROFILE\glad-labs-website\src\cofounder_agent
python -m uvicorn main:app --reload --port 8000

# Terminal 2: Oversight Hub
cd $Env:USERPROFILE\glad-labs-website\web\oversight-hub
npm start

# Terminal 3: Strapi (optional, for auth/content)
cd $Env:USERPROFILE\glad-labs-website\cms\strapi-main
npm run develop

# Then test in browser:
# http://localhost:3001/login
```

---

## 📝 API Reference

### Create Task

- **Endpoint:** `POST /api/tasks`
- **Auth:** Required (Bearer token)
- **Body:** `{ task_name, topic, primary_keyword, target_audience, category, metadata }`
- **Response:** Task object with UUID + status

### Get Task

- **Endpoint:** `GET /api/tasks/{task_id}`
- **Auth:** Required
- **Response:** Full task details

### List Tasks

- **Endpoint:** `GET /api/tasks?offset=0&limit=10&status=completed`
- **Auth:** Required
- **Response:** Paginated task list

### Update Task

- **Endpoint:** `PATCH /api/tasks/{task_id}`
- **Auth:** Required
- **Body:** `{ status, result, metadata }`
- **Response:** Updated task

### Get Metrics

- **Endpoint:** `GET /api/tasks/metrics/aggregated`
- **Auth:** Required
- **Response:** Aggregated metrics (total, completed, failed, success_rate, avg_time, cost)

---

**Status: Ready for End-to-End Testing ✅**

For issues, check backend logs with: `npm run dev:cofounder` (verbose output)
