# Week 2 Quick Start & Testing Guide

## Prerequisites

### 1. Environment Setup

```bash
# Backend: Navigate to backend directory
cd src/cofounder_agent

# Ensure database connection
export DATABASE_URL="postgresql://user:password@localhost/glad_labs_dev"
export JWT_SECRET="your-jwt-secret"

# Frontend: Navigate to oversight hub
cd web/oversight-hub
npm install  # if not done
```

### 2. Database Check

```bash
# Verify PostgreSQL running
psql -h localhost -U postgres -c "\l"  # List databases

# Check glad_labs_dev exists
psql -h localhost -U postgres -d glad_labs_dev -c "\dt"  # List tables

# Verify cost_logs table has indexes
psql -h localhost -U postgres -d glad_labs_dev -c "\d cost_logs"
```

---

## Running Services

### Option A: Terminal Sessions (Manual)

**Terminal 1 - Backend:**

```bash
cd /c/Users/mattm/glad-labs-website/src/cofounder_agent
python main.py
# Watch for: "Starting server on port 8001"
```

**Terminal 2 - Frontend:**

```bash
cd /c/Users/mattm/glad-labs-website/web/oversight-hub
npm start
# Watch for: "Compiled successfully!"
# Opens http://localhost:3000
```

### Option B: VS Code Tasks

Use the predefined tasks:

1. "Start Co-founder Agent" - Starts backend
2. "Start Oversight Hub" - Starts frontend
3. "Start All Services" - Starts both

---

## Testing Workflow

### Step 1: Verify Backend Service

```bash
# Test endpoint availability
curl -v http://localhost:8001/api/health

# Test cost metrics endpoint (requires JWT token)
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  http://localhost:8001/api/metrics/costs

# Should return JSON with costs, budget, tasks
```

### Step 2: Verify Database Connection

```bash
# Check if cost_logs table has data
psql -h localhost -U postgres -d glad_labs_dev -c \
  "SELECT COUNT(*) as total_costs FROM cost_logs;"

# Should show count > 0 if Week 1 tasks were run
```

### Step 3: Access Dashboard

1. Open http://localhost:3000
2. Log in with test credentials
3. Navigate to "Dashboard" menu
4. Look for "Cost Metrics Dashboard" card
5. Click to open cost dashboard

### Step 4: Verify Data Display

Check all sections load:

- ✅ Budget Overview card (shows budget, spent, remaining)
- ✅ Costs by Phase table (research, outline, draft, assess, refine, finalize)
- ✅ Costs by Model table (ollama, gpt-3.5, gpt-4, claude)
- ✅ Cost History table (daily costs for past 7 days)
- ✅ Summary card (total spent, remaining, projected)

### Step 5: Test Alert Threshold

To test budget alerts:

1. Manually set `monthly_budget` lower in frontend code OR
2. Use API endpoint with low budget:
   ```bash
   curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     "http://localhost:8001/api/metrics/costs/budget?monthly_budget=20"
   ```
3. Should show "warning" status if current spend is high

---

## Running Tests

### Backend Tests

```bash
# Navigate to backend
cd src/cofounder_agent

# Run Week 2 validation tests
python -m pytest tests/test_week2_cost_analytics.py -v

# Expected output:
# test_cost_aggregation_service_methods PASSED
# test_metrics_routes_endpoints PASSED
# test_frontend_client_methods PASSED
# test_dashboard_component PASSED
# test_database_methods PASSED
# test_response_models PASSED
# test_integration PASSED
```

### Manual API Tests

```bash
# Get phase breakdown
curl -H "Authorization: Bearer TOKEN" \
  "http://localhost:8001/api/metrics/costs/breakdown/phase?period=week"

# Get model breakdown
curl -H "Authorization: Bearer TOKEN" \
  "http://localhost:8001/api/metrics/costs/breakdown/model?period=month"

# Get cost history
curl -H "Authorization: Bearer TOKEN" \
  "http://localhost:8001/api/metrics/costs/history?period=week"

# Get budget status
curl -H "Authorization: Bearer TOKEN" \
  "http://localhost:8001/api/metrics/costs/budget?monthly_budget=150"
```

---

## Common Issues & Fixes

### Issue 1: "Database connection refused"

**Cause:** PostgreSQL not running or wrong DATABASE_URL

**Fix:**

```bash
# Check if PostgreSQL is running
psql -h localhost -U postgres -c "SELECT 1;"

# If not running, start it:
# Windows: net start postgresql-x64-XX
# macOS: brew services start postgresql
# Linux: sudo systemctl start postgresql
```

### Issue 2: "401 Unauthorized" on API calls

**Cause:** Missing or invalid JWT token

**Fix:**

```bash
# Get token from frontend login
# Use token in header:
curl -H "Authorization: Bearer YOUR_JWT" http://localhost:8001/api/metrics/costs

# Or test without token if auth is disabled in dev mode
```

### Issue 3: Tables show "No data"

**Cause:** cost_logs table is empty or Week 1 tasks weren't run

**Fix:**

```bash
# Insert test data:
psql -h localhost -U postgres -d glad_labs_dev << EOF
INSERT INTO cost_logs
  (task_id, phase, model, provider, cost_usd, success)
VALUES
  ('task-1', 'draft', 'gpt-4', 'openai', 0.50, true),
  ('task-2', 'research', 'ollama', 'local', 0.00, true),
  ('task-3', 'outline', 'gpt-3.5', 'openai', 0.10, true);
EOF

# Refresh dashboard to see data
```

### Issue 4: "Module not found" error

**Cause:** Python dependencies not installed

**Fix:**

```bash
cd src/cofounder_agent
pip install -r requirements.txt
# Then restart: python main.py
```

---

## Data Flow Verification

### Confirm End-to-End

1. **Database Layer:**

   ```bash
   psql -d glad_labs_dev -c "SELECT COUNT(*) FROM cost_logs;"
   # Should show count
   ```

2. **Service Layer:**

   ```python
   # In Python shell
   from services.cost_aggregation_service import CostAggregationService
   from services.database_service import DatabaseService

   db = DatabaseService()
   service = CostAggregationService(db)
   result = await service.get_summary()
   print(result)
   # Should show costs summary
   ```

3. **API Layer:**

   ```bash
   curl -H "Authorization: Bearer TOKEN" \
     http://localhost:8001/api/metrics/costs
   # Should return JSON structure
   ```

4. **Frontend Layer:**
   - Open browser console (F12)
   - Check Network tab
   - Click refresh on Cost Metrics Dashboard
   - Should see requests to:
     - `/api/metrics/costs`
     - `/api/metrics/costs/breakdown/phase`
     - `/api/metrics/costs/breakdown/model`
     - `/api/metrics/costs/history`
     - `/api/metrics/costs/budget`

---

## Performance Baseline

Expected performance on typical hardware:

| Operation                      | Typical Time |
| ------------------------------ | ------------ |
| Get summary                    | 50-100ms     |
| Get phase breakdown            | 50-150ms     |
| Get model breakdown            | 50-150ms     |
| Get history (7 days)           | 50-150ms     |
| Get history (30 days)          | 100-200ms    |
| Get budget status              | 30-50ms      |
| Dashboard load (all 5 APIs)    | 200-400ms    |
| Auto-refresh (60 sec interval) | < 500ms      |

If slower, check:

- Database indexes: `EXPLAIN ANALYZE` on cost_logs queries
- Network latency: Check DevTools Network tab
- Database connection pool size

---

## Code Locations Quick Reference

### Backend Files

```
src/cofounder_agent/
├── services/
│   └── cost_aggregation_service.py        ← Cost calculations
├── routes/
│   └── metrics_routes.py                  ← API endpoints
└── tests/
    └── test_week2_cost_analytics.py       ← Validation tests
```

### Frontend Files

```
web/oversight-hub/src/
├── components/
│   └── CostMetricsDashboard.jsx           ← Dashboard component
└── services/
    └── cofounderAgentClient.js            ← API client methods
```

---

## What to Test

### Functional Tests

- [ ] Phase breakdown table shows all 6 phases (if tasks exist for them)
- [ ] Model breakdown table shows only models used
- [ ] History table shows last 7 days of costs
- [ ] Budget card color-codes correctly:
  - Green: < 50% budget
  - Yellow: 50-80% budget
  - Orange: 80-100% budget
  - Red: > 100% budget
- [ ] Summary card calculates correctly:
  - Spent = sum of all costs
  - Remaining = budget - spent
  - Projected = spent + (daily_avg \* days_remaining)

### Edge Cases

- [ ] Dashboard shows message when no cost data exists
- [ ] Tables handle zero division errors
- [ ] Buttons don't break with null data
- [ ] Auto-refresh doesn't duplicate data
- [ ] Budget status handles negative remaining correctly

### Integration Tests

- [ ] Create new task → Cost logged → Appears in dashboard (60 sec lag)
- [ ] Change budget slider → Alerts update immediately
- [ ] Switch between week/month view → Data changes correctly
- [ ] Refresh page → Data reloads from API
- [ ] Close/reopen dashboard → No memory leaks

---

## Next Steps (Week 3)

After Week 2 is fully tested:

1. **Smart Model Selection** - Auto-choose models based on cost/quality
2. **Learning System** - Track which models get best ratings
3. **Optimization Recommendations** - "Use Ollama more for research"
4. **Monthly Summary Reports** - Email summary each month
5. **Advanced Analytics** - ROI tracking, trend analysis

---

## Support Files

- Full implementation guide: `WEEK_2_IMPLEMENTATION_COMPLETE.md`
- API endpoint details: Run tests with `-v` flag
- Code examples: See docstrings in cost_aggregation_service.py

---
