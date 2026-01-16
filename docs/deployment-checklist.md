# Task Status Management System - Deployment Checklist

**Project:** Glad Labs - Task Status Management  
**Phase:** 5 (Complete)  
**Version:** 1.0.0  
**Status:** ✅ Ready for Production

---

## Pre-Deployment Verification

### Backend Setup

- [ ] Python 3.12+ installed
- [ ] PostgreSQL running on local machine or configured in `.env.local`
- [ ] Database credentials set in `DATABASE_URL`
- [ ] Poetry environment activated
- [ ] All Python dependencies installed: `poetry install`

### Frontend Setup

- [ ] Node.js 18+ installed
- [ ] npm or yarn available
- [ ] React 17+ installed in `web/oversight-hub/`
- [ ] All npm dependencies installed: `npm install`

### Configuration

- [ ] `.env.local` file exists at project root with:
  ```
  DATABASE_URL=postgresql://user:pass@localhost:5432/glad_labs
  ```
- [ ] Auth token system ready (for localStorage)
- [ ] CORS headers configured in FastAPI (`main.py`)

---

## Backend Deployment Checklist

### 1. Database Migration

```bash
cd src/cofounder_agent

# Option A: Using poetry
poetry run alembic upgrade head

# Option B: Manual SQL
psql -U postgres -d glad_labs < ../../docs/migrations/001_create_task_status_history.sql
```

**Verify:**

- [ ] No SQL errors
- [ ] Table `task_status_history` created
- [ ] Indexes created
- [ ] Foreign keys working

### 2. Verify Database Schema

```bash
# Connect to PostgreSQL
psql -U postgres -d glad_labs

# Run verification
SELECT * FROM information_schema.tables WHERE table_name = 'task_status_history';
SELECT * FROM information_schema.columns WHERE table_name = 'task_status_history';
```

**Expected columns:**

- [ ] id (UUID)
- [ ] task_id (VARCHAR)
- [ ] old_status (VARCHAR)
- [ ] new_status (VARCHAR)
- [ ] reason (TEXT)
- [ ] timestamp (TIMESTAMP)
- [ ] metadata (JSONB)
- [ ] created_at (TIMESTAMP)

### 3. Verify Backend Services

**Start FastAPI:**

```bash
cd src/cofounder_agent
poetry run uvicorn main:app --reload --port 8000
```

**Health check:**

```bash
curl -X GET http://localhost:8000/health
```

**Expected response:**

```json
{ "status": "ok" }
```

**Verify endpoints exist:**

- [ ] `PUT /api/tasks/{task_id}/status/validated`
- [ ] `GET /api/tasks/{task_id}/status-history`
- [ ] `GET /api/tasks/{task_id}/status-history/failures`

### 4. Run Backend Tests

```bash
npm run test:python
```

**Expected:**

- [ ] All 37 tests pass
- [ ] No linting errors
- [ ] Coverage > 90%

### 5. Test API Endpoints

**Test 1: Update status**

```bash
curl -X PUT http://localhost:8000/api/tasks/test-123/status/validated \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{
    "new_status": "approved",
    "reason": "Test update",
    "user_id": "user-123"
  }'
```

**Test 2: Get history**

```bash
curl -X GET "http://localhost:8000/api/tasks/test-123/status-history?limit=50" \
  -H "Authorization: Bearer test-token"
```

**Test 3: Get failures**

```bash
curl -X GET "http://localhost:8000/api/tasks/test-123/status-history/failures?limit=50" \
  -H "Authorization: Bearer test-token"
```

---

## Frontend Deployment Checklist

### 1. Verify Component Files Exist

```bash
cd web/oversight-hub/src/components/tasks/

# Check files
ls -la Status*.jsx
ls -la Status*.css
```

**Files to verify:**

- [ ] StatusAuditTrail.jsx (161 lines)
- [ ] StatusAuditTrail.css (350 lines)
- [ ] StatusTimeline.jsx (195 lines)
- [ ] StatusTimeline.css (330 lines)
- [ ] ValidationFailureUI.jsx (220 lines)
- [ ] ValidationFailureUI.css (380 lines)
- [ ] StatusDashboardMetrics.jsx (210 lines)
- [ ] StatusDashboardMetrics.css (320 lines)
- [ ] StatusComponents.js (13 lines)

### 2. Verify Imports

```bash
# Check barrel export
grep -l "export.*Status" StatusComponents.js
```

**Expected exports:**

- [ ] StatusAuditTrail
- [ ] StatusTimeline
- [ ] ValidationFailureUI
- [ ] StatusDashboardMetrics

### 3. Test Component Imports

```bash
cd web/oversight-hub

# Create test file
cat > test-import.jsx << 'EOF'
import { StatusAuditTrail, StatusTimeline, ValidationFailureUI, StatusDashboardMetrics } from './src/components/tasks/StatusComponents';
console.log('All imports successful');
EOF

# Test with Node
node -r @babel/register test-import.jsx
```

**Expected:**

- [ ] No import errors
- [ ] All components importable

### 4. Build Frontend

```bash
cd web/oversight-hub
npm run build
```

**Expected:**

- [ ] Build completes successfully
- [ ] No errors
- [ ] Build output in `build/` or `.next/` directory

### 5. Test in Development

```bash
cd web/oversight-hub
npm start
```

**Expected:**

- [ ] Server starts on port 3001
- [ ] No console errors
- [ ] CSS loads properly

### 6. Verify Component Rendering

**Create test component:**

```jsx
import { StatusAuditTrail } from './components/tasks/StatusComponents';

export function TestStatus() {
  return <StatusAuditTrail taskId="test-123" limit={50} />;
}
```

**In browser:**

- [ ] Component renders without error
- [ ] Loading state appears first
- [ ] Network request made to `/api/tasks/test-123/status-history`

### 7. Test with Mock Data

```jsx
import StatusTimeline from './StatusTimeline';

const mockHistory = [
  {
    old_status: 'pending',
    new_status: 'in_progress',
    timestamp: new Date().toISOString(),
  },
  {
    old_status: 'in_progress',
    new_status: 'approved',
    timestamp: new Date().toISOString(),
  },
];

export function TestTimeline() {
  return (
    <StatusTimeline currentStatus="approved" statusHistory={mockHistory} />
  );
}
```

**Expected:**

- [ ] Timeline renders
- [ ] All states show
- [ ] Duration calculated
- [ ] Styling applied

---

## Integration Testing

### 1. Full Stack Test

**Setup:**

- [ ] Backend running on port 8000
- [ ] Frontend running on port 3001
- [ ] Database connected
- [ ] Auth token configured

**Test Flow:**

1. **Create task with status**

   ```bash
   curl -X POST http://localhost:8000/api/tasks \
     -H "Authorization: Bearer test-token" \
     -d '{ "id": "task-001", "status": "pending", "title": "Test Task" }'
   ```

2. **Update status via API**

   ```bash
   curl -X PUT http://localhost:8000/api/tasks/task-001/status/validated \
     -H "Authorization: Bearer test-token" \
     -d '{ "new_status": "in_progress", "reason": "Started" }'
   ```

3. **View in frontend**
   - Open browser to `http://localhost:3001`
   - Navigate to task detail
   - Verify StatusAuditTrail shows new entry
   - Verify StatusTimeline shows transition

4. **Test error case**
   - Try invalid transition
   - Verify error appears
   - Check ValidationFailureUI shows failure

### 2. API Response Validation

**Status History Response:**

```javascript
✓ Has task_id field
✓ Has history_count field
✓ Has history array
✓ Each entry has: id, old_status, new_status, reason, timestamp, metadata
✓ Timestamps are valid ISO format
✓ Metadata is valid JSON
```

**Failures Response:**

```javascript
✓ Has task_id field
✓ Has failure_count field
✓ Has failures array
✓ Each failure has: old_status, new_status, reason, timestamp, metadata
```

### 3. Component Integration

- [ ] StatusAuditTrail displays fetched history
- [ ] StatusTimeline shows current state correctly
- [ ] ValidationFailureUI shows errors if any
- [ ] StatusDashboardMetrics calculates metrics
- [ ] All components handle loading state
- [ ] All components handle error state
- [ ] All components handle empty state

---

## Performance Testing

### Backend Performance

**Test query performance:**

```bash
# Time the query
time curl -X GET "http://localhost:8000/api/tasks/test-123/status-history?limit=50" \
  -H "Authorization: Bearer test-token"
```

**Expected:**

- [ ] Response time < 200ms for limit=50
- [ ] Response time < 500ms for limit=1000

**Test concurrent requests:**

```bash
# Use Apache Bench
ab -n 100 -c 10 -H "Authorization: Bearer test-token" \
  http://localhost:8000/api/tasks/test-123/status-history
```

**Expected:**

- [ ] No errors
- [ ] Average response time < 300ms
- [ ] Failed requests = 0

### Frontend Performance

**Test component rendering:**

- [ ] StatusTimeline renders < 100ms
- [ ] StatusAuditTrail renders < 150ms
- [ ] StatusDashboardMetrics renders < 200ms
- [ ] No layout shifts
- [ ] CSS animations smooth

---

## Security Checklist

### Authentication

- [ ] Auth token required for all endpoints
- [ ] Bearer token validation working
- [ ] Invalid tokens rejected
- [ ] Token expiration handled

### Input Validation

- [ ] Request body validated
- [ ] Status values validated
- [ ] Task IDs validated
- [ ] SQL injection protected
- [ ] XSS protection enabled

### Data Protection

- [ ] Sensitive data not logged
- [ ] Error messages don't expose internals
- [ ] CORS properly configured
- [ ] HTTPS enforced in production

### Database Security

- [ ] Foreign keys enforced
- [ ] Unique constraints working
- [ ] Transactions atomic
- [ ] Data integrity maintained

---

## Deployment Steps

### Production Backend Deployment

1. **Setup environment:**

   ```bash
   export DATABASE_URL="postgresql://prod_user:pass@prod.db:5432/glad_labs_prod"
   export ENVIRONMENT="production"
   export LOG_LEVEL="info"
   ```

2. **Run migration:**

   ```bash
   poetry run alembic upgrade head
   ```

3. **Verify migration:**

   ```bash
   psql $DATABASE_URL -c "SELECT * FROM task_status_history LIMIT 1;"
   ```

4. **Start service:**

   ```bash
   poetry run uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
   ```

5. **Health check:**
   ```bash
   curl https://api.gladlabs.com/health
   ```

### Production Frontend Deployment

1. **Build:**

   ```bash
   cd web/oversight-hub
   npm run build
   ```

2. **Deploy to hosting:**

   ```bash
   # To Vercel
   npm run deploy

   # Or manually
   cp -r build/* /var/www/oversight-hub/
   ```

3. **Verify deployment:**
   ```bash
   curl https://oversight.gladlabs.com
   ```

---

## Monitoring & Logs

### Backend Logging

**Check logs:**

```bash
# Development
tail -f cofounder_agent.log

# Production
journalctl -u glad-labs-backend -f
```

**Expected log entries:**

- [ ] Server startup message
- [ ] Endpoint requests
- [ ] Database queries (if SQL_DEBUG=true)
- [ ] Error messages with context

### Frontend Monitoring

**Browser Console:**

- [ ] No JavaScript errors
- [ ] No console warnings
- [ ] API calls visible in Network tab
- [ ] Response times logged

**Error Tracking:**

- [ ] Sentry integration configured
- [ ] Errors sent to Sentry
- [ ] User sessions tracked

---

## Post-Deployment Verification

### Smoke Tests

1. **Backend alive:**

   ```bash
   curl -X GET https://api.gladlabs.com/health
   ```

2. **Database accessible:**

   ```bash
   psql $DATABASE_URL -c "SELECT COUNT(*) FROM task_status_history;"
   ```

3. **Frontend loads:**

   ```bash
   curl -X GET https://oversight.gladlabs.com | grep StatusAuditTrail
   ```

4. **API working:**
   ```bash
   curl -X GET "https://api.gladlabs.com/api/tasks/test/status-history" \
     -H "Authorization: Bearer $TOKEN"
   ```

### Functional Tests

- [ ] Create new task
- [ ] Update status successfully
- [ ] View audit trail
- [ ] Check validation failures
- [ ] View dashboard metrics
- [ ] Test error handling
- [ ] Verify responsive design

---

## Rollback Plan

**If deployment fails:**

1. **Check logs for errors**
2. **Rollback database migration**
   ```bash
   poetry run alembic downgrade -1
   ```
3. **Rollback frontend**
   ```bash
   # Revert to previous deployment
   # Vercel: Deploy from previous commit
   # Manual: Restore previous build
   ```
4. **Verify rollback successful**
5. **Fix issue and redeploy**

---

## Documentation Updates

After deployment, update:

- [ ] API documentation with live endpoints
- [ ] Component documentation with screenshots
- [ ] Deployment guide with production URLs
- [ ] Status page with system health
- [ ] Release notes with new features

---

## Sign-Off

**Deployment Checklist Completed By:** ******\_\_\_\_******  
**Date:** ******\_\_\_\_******  
**Time:** ******\_\_\_\_******

**Backend Status:** ☐ Ready ☐ Deployed  
**Frontend Status:** ☐ Ready ☐ Deployed  
**Database:** ☐ Ready ☐ Migrated  
**Tests:** ☐ Passing ☐ Verified

**Production URL (Backend):** **********\_**********  
**Production URL (Frontend):** **********\_**********

**Notes:**

---

## Support Contacts

**Backend Issues:** backend-team@gladlabs.com  
**Frontend Issues:** frontend-team@gladlabs.com  
**Database Issues:** dba-team@gladlabs.com  
**General Support:** support@gladlabs.com

---

**Project:** Glad Labs - Task Status Management System  
**Version:** 1.0.0  
**Status:** ✅ Ready for Deployment
