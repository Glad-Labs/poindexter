# Glad Labs - Execution Plan (Oct 25, 2025)

**Status:** Starting Implementation  
**Branch:** feat/bugs  
**Timeline:** 2-3 Weeks to MVP

---

## 📋 Immediate Tasks (This Session - Critical Path)

### 1. Fix Firestore Dependency ✅ START HERE

- [x] Create new `useTasks.js` hook (API-based)
- [ ] Remove Firebase imports from Oversight Hub
- [ ] Update environment variables
- [ ] Test hook in isolation

### 2. Complete Login Flow (Full JWT Integration)

- [ ] Create login endpoint in FastAPI backend
- [ ] Create registration endpoint
- [ ] Implement JWT token generation
- [ ] Add token refresh logic
- [ ] Wire LoginForm to backend
- [ ] Test login/logout cycle

### 3. Fix SQLAlchemy Issue (Enable Database)

- [ ] Fix `metadata` column naming conflict
- [ ] Test Task model CRUD operations
- [ ] Verify database migrations work
- [ ] Test with PostgreSQL locally

### 4. Test Authentication (E2E Validation)

- [ ] User can register
- [ ] User can login
- [ ] JWT token stored in localStorage
- [ ] Token sent with API requests
- [ ] Token refresh works
- [ ] Logout clears tokens

---

## 🎯 Short-term Tasks (Next 1-2 Weeks)

### 5. Implement Task Delegation

- [ ] Create `TaskCreationModal.jsx` component
- [ ] Wire to backend POST /api/tasks endpoint
- [ ] Show task in dashboard after creation
- [ ] Poll for task status updates

### 6. Connect Agents to Tasks

- [ ] Update agent to process tasks from database
- [ ] Implement task status updates (queued → processing → complete)
- [ ] Handle task failures gracefully
- [ ] Store results in database

### 7. Add Metrics Display

- [ ] Create `MetricsDisplay.jsx` component
- [ ] Implement GET /api/metrics endpoint
- [ ] Calculate metrics from task data
- [ ] Update dashboard with metrics

### 8. Error Handling

- [ ] Add error boundaries to React
- [ ] Implement API error responses
- [ ] Show user-friendly error messages
- [ ] Log errors for debugging

---

## 📊 Files to Create/Modify

### Backend (Python/FastAPI)

**New Files:**

- `src/cofounder_agent/routes/auth_routes.py` - Login/register endpoints
- `src/cofounder_agent/routes/tasks.py` - Task management
- `src/cofounder_agent/routes/metrics.py` - Metrics aggregation
- `src/cofounder_agent/services/auth_service.py` - JWT token logic

**Modified Files:**

- `src/cofounder_agent/main.py` - Register new routes
- `src/cofounder_agent/models.py` - Fix Task model metadata field
- `src/cofounder_agent/database.py` - Ensure PostgreSQL connection

### Frontend (React)

**New Files:**

- `web/oversight-hub/src/hooks/useTasks.js` - API data fetching (replace Firebase)
- `web/oversight-hub/src/components/TaskCreationModal.jsx` - Create tasks
- `web/oversight-hub/src/components/MetricsDisplay.jsx` - Show metrics
- `web/oversight-hub/src/services/cofounderAgentClient.js` - API client
- `web/oversight-hub/src/components/ErrorBoundary.jsx` - Error handling

**Modified Files:**

- `web/oversight-hub/src/store/useStore.js` - Add auth state
- `web/oversight-hub/src/components/LoginForm.jsx` - Connect to API
- `web/oversight-hub/src/App.jsx` - Add route guards

---

## 🧪 Testing Strategy

### Unit Tests

```bash
# Frontend
npm run test:frontend -- --testPathPattern="useTasks|LoginForm"

# Backend
npm run test:python -- tests/test_auth.py tests/test_tasks.py
```

### Integration Tests

```bash
# Test full auth flow
npm run test:python -- tests/test_auth_integration.py

# Test task creation flow
npm run test:python -- tests/test_task_flow.py
```

### Manual Testing (Step-by-Step)

1. Start all services: `npm run dev`
2. Navigate to http://localhost:3001
3. Register new account
4. Login with account
5. Create task
6. Watch task status update
7. Check metrics

---

## ⏱️ Timeline

**Today (Oct 25):**

- Immediate task 1-2: Fix Firestore & JWT integration
- 4-6 hours

**Tomorrow (Oct 26):**

- Immediate task 3-4: Fix database & test auth
- 4-6 hours

**Next 2 Days (Oct 27-28):**

- Short-term task 5-8: Task delegation & metrics
- 8-12 hours

**Week 2:**

- Testing & bug fixes
- Real-time updates (WebSocket)
- Performance optimization

---

## ✅ Success Criteria

### Immediate (Critical) - MUST COMPLETE

- [x] User can register ← Start here
- [x] User can login with JWT
- [x] Tasks stored in PostgreSQL
- [x] Auth flow end-to-end works

### Short-term (Important)

- [ ] User can create task
- [ ] Task appears in dashboard
- [ ] Task status updates
- [ ] Metrics calculated and displayed

### Medium-term (Valuable)

- [ ] WebSocket real-time updates
- [ ] Performance optimized
- [ ] Advanced features working
- [ ] Analytics dashboard

---

## 🚀 Getting Started

**Start with:** Section 1 - Fix Firestore Dependency

**Expected outcome:**

- useTasks.js works with API instead of Firebase
- No more Firebase errors in console
- Tasks fetched from backend

**Time estimate:** 30 minutes
