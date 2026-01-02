# Service Layer Integration Checklist

**Date:** January 1, 2026  
**Purpose:** Step-by-step checklist for integrating ServiceRegistry as unified backend for both manual task creation and natural language chat

---

## Architecture Overview

Both the **manual task creation** (CreateTaskModal) and **natural language chat** (NaturalLanguageInput) will use the new service layer as their common backend:

```
CreateTaskModal (Manual)                    NaturalLanguageInput (Chat)
         ↓                                          ↓
  taskService.js                        nlp_intent_recognizer.py
         ↓                                          ↓
     SERVICE LAYER (unified backend)
         ↓
  ServiceRegistry (TaskService)
         ↓
  Task Actions: create_task, list_tasks, get_task, update_task_status
         ↓
  PostgreSQL
```

**Benefits:**

- ✅ Both paths use same TaskService implementation
- ✅ LLMs can call same service actions via API
- ✅ Single source of truth for task operations
- ✅ Backward compatible with existing /api/tasks endpoint
- ✅ Zero duplication between manual and NLP paths

---

## Pre-Integration Verification

### ✅ Existing Pipeline Verification

Before proceeding, verify your existing pipeline is working:

```bash
# 1. Check backend is running
curl http://localhost:8000/health

# 2. Check task creation endpoint
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer YOUR_JWT" \
  -H "Content-Type: application/json" \
  -d '{"task_name": "Test", "topic": "Test", "category": "blog_post"}'

# Expected: 201 response with task ID

# 3. Check task retrieval
curl http://localhost:8000/api/tasks \
  -H "Authorization: Bearer YOUR_JWT"

# Expected: 200 response with task list

# 4. Check Oversight Hub loads
# Open http://localhost:3001 in browser
# Verify Task Management page loads
# Verify "Create Task" button visible
```

---

## Integration Steps

### Step 1: Review Service Foundation Files

**Files Already Created:**

- ✅ `src/cofounder_agent/services/service_base.py` (ServiceBase, ServiceRegistry, ServiceAction)
- ✅ `src/cofounder_agent/services/task_service_example.py` (Example TaskService)
- ✅ `src/cofounder_agent/routes/services_registry_routes.py` (API routes)

**Action:** Review these files to understand the pattern

```bash
# Check files exist
ls -la src/cofounder_agent/services/service_base.py
ls -la src/cofounder_agent/services/task_service_example.py
ls -la src/cofounder_agent/routes/services_registry_routes.py
```

### Step 2: Update main.py - Add Imports

**File:** `src/cofounder_agent/main.py`

**Action:** Add these imports near the top (after existing imports):

```python
# Add to imports section
from services.service_base import get_service_registry
from services.task_service_example import TaskService
from routes.services_registry_routes import router as services_router
```

**Verification:**

```bash
python -c "from src.cofounder_agent.services.service_base import get_service_registry; print('✓ Imports successful')"
```

### Step 3: Update main.py - Initialize Registry

**File:** `src/cofounder_agent/main.py`

**Action:** Add initialization code after app creation, before route registration:

```python
# Create FastAPI app
app = FastAPI(
    title="Co-Founder Agent API",
    version="1.0.0",
    ...
)

# Existing middleware setup
# ... (all your existing middleware code)

# NEW: Initialize Service Registry
try:
    registry = get_service_registry()
    logger.info("✓ ServiceRegistry initialized")
except Exception as e:
    logger.error(f"✗ Failed to initialize ServiceRegistry: {e}")
    # Service registry failure is not critical
    registry = None
```

**Verification:**

```bash
cd src/cofounder_agent && python -c "from main import app; print('✓ App imports successfully')"
```

### Step 4: Update main.py - Register Services

**File:** `src/cofounder_agent/main.py`

**Action:** Add service registration code after registry initialization:

```python
# Register core services if registry is available
if registry:
    try:
        # Register TaskService
        task_service = TaskService(registry)
        registry.register(task_service)
        logger.info("✓ TaskService registered")

        # Register additional services here as they're refactored
        # registry.register(ContentService(registry))
        # registry.register(PublishingService(registry))

    except Exception as e:
        logger.error(f"✗ Failed to register services: {e}")
```

**Verification:**

```bash
# Check that services registered without errors
cd src/cofounder_agent && python main.py --log-level debug 2>&1 | grep -i "service"
```

### Step 5: Update main.py - Include Registry Routes

**File:** `src/cofounder_agent/main.py`

**Action:** Add route registration after all existing routes:

```python
# Include existing routes (unchanged)
app.include_router(task_routes.router, prefix="/api/tasks", tags=["Tasks"])
app.include_router(content_routes.router, prefix="/api/content", tags=["Content"])
# ... all other existing routes

# NEW: Include service registry routes
if registry:
    try:
        app.include_router(services_router, prefix="/api/services", tags=["Services"])
        logger.info("✓ Service registry routes included")
    except Exception as e:
        logger.error(f"✗ Failed to include service routes: {e}")
```

**Verification:**

```bash
# Check routes are registered
curl http://localhost:8000/openapi.json | grep -i "services"
```

### Step 6: Test Existing Endpoints Still Work

**Action:** Run smoke tests on existing endpoints

```bash
# Test task creation (existing endpoint)
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer YOUR_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "task_name": "Test After Integration",
    "topic": "Testing integration",
    "category": "blog_post"
  }'

# Expected: 201 with task ID

# Test task list (existing endpoint)
curl http://localhost:8000/api/tasks \
  -H "Authorization: Bearer YOUR_JWT"

# Expected: 200 with tasks array
```

### Step 7: Test New ServiceRegistry Endpoints

**Action:** Test the new service discovery endpoints

```bash
# Get available services
curl http://localhost:8000/api/services \
  -H "Authorization: Bearer YOUR_JWT"

# Expected: 200 with list of services

# Get complete registry schema
curl http://localhost:8000/api/services/registry \
  -H "Authorization: Bearer YOUR_JWT"

# Expected: 200 with service definitions and actions

# Create task via service action (should be same as /api/tasks)
curl -X POST http://localhost:8000/api/services/tasks/actions/create_task \
  -H "Authorization: Bearer YOUR_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "params": {
      "task_name": "Via Service Registry",
      "topic": "Test via service action",
      "category": "blog_post"
    }
  }'

# Expected: 200 with ActionResult containing task data
```

### Step 8: Test Frontend Still Works

**Action:** Verify UI task creation pipeline unchanged

```
1. Open Oversight Hub (http://localhost:3001)
2. Navigate to Task Management
3. Click "Create Task" button
4. Fill in form with:
   - Task Name: "UI Integration Test"
   - Topic: "Testing after service layer integration"
   - Category: "blog_post"
5. Click "Create Task"
6. Verify success message
7. Verify task appears in list
8. Verify task has correct status and fields
```

### Step 9: Check Logs for Errors

**Action:** Monitor backend logs for any errors

```bash
# Check for error messages
cd src/cofounder_agent && python main.py 2>&1 | grep -i "error"

# Expected: No new errors related to ServiceRegistry

# Check for warning messages
cd src/cofounder_agent && python main.py 2>&1 | grep -i "warn"

# Expected: No warnings related to services
```

### Step 10: Verify Database Integrity

**Action:** Check that tasks created via both paths use same database

```bash
# Connect to PostgreSQL
psql -U postgres -d glad_labs

# Check tasks table
SELECT id, task_name, category, status, created_at
FROM tasks
ORDER BY created_at DESC
LIMIT 5;

# Expected: Tasks from both UI and service registry paths
```

---

## Rollback Procedure

If anything breaks, rollback is simple:

### 1. Revert main.py changes

```bash
git checkout src/cofounder_agent/main.py
```

### 2. Restart backend

```bash
poetry run uvicorn main:app --reload
```

### 3. Verify original endpoints work

```bash
curl http://localhost:8000/api/tasks
```

**Important:** The three service layer files (service_base.py, task_service_example.py, services_registry_routes.py) can be left in place - they won't affect anything if not referenced in main.py.

---

## Success Criteria Checklist

✅ **Task Creation Works**

- [ ] UI "Create Task" button works
- [ ] Task appears in list after creation
- [ ] Task has correct fields in database

✅ **Existing Endpoints Work**

- [ ] POST /api/tasks returns 201
- [ ] GET /api/tasks returns 200
- [ ] PATCH /api/tasks/{id} works
- [ ] All filters and pagination work

✅ **New Endpoints Work**

- [ ] GET /api/services returns service list
- [ ] GET /api/services/registry returns full schema
- [ ] POST /api/services/tasks/actions/create_task creates tasks
- [ ] No errors in logs

✅ **Database Integrity**

- [ ] Tasks created via UI in database
- [ ] Tasks created via service action in database
- [ ] All fields match (task_name, topic, category, etc.)
- [ ] Timestamps are correct

✅ **No Breaking Changes**

- [ ] No new errors in logs
- [ ] No changes to frontend
- [ ] No changes to existing routes
- [ ] Task approval/rejection workflow unchanged

---

## Common Issues & Solutions

### Issue: "ServiceRegistry already registered" error

**Cause:** Registry initialized multiple times  
**Solution:** Ensure `get_service_registry()` is called once in main.py startup

### Issue: "Service not found" when calling action

**Cause:** Service not registered before call  
**Solution:** Verify service registered in registry initialization section before routes

### Issue: 404 on /api/services endpoints

**Cause:** services_registry_routes not included  
**Solution:** Verify router included: `app.include_router(services_router, prefix="/api/services")`

### Issue: 500 error when creating task via service

**Cause:** Database service not properly initialized in TaskService  
**Solution:** Check that DatabaseService passed to TaskService and available via registry

### Issue: Task doesn't appear in list after creation

**Cause:** Different code paths not using same database connection  
**Solution:** Verify both paths use same DatabaseService instance via registry

---

## Timeline

| Phase     | Action                                        | Time        |
| --------- | --------------------------------------------- | ----------- |
| Review    | Read service_base.py, task_service_example.py | 15 min      |
| Update    | Modify main.py (5 small additions)            | 10 min      |
| Test      | Run smoke tests and manual tests              | 15 min      |
| Verify    | Check logs, database, endpoints               | 10 min      |
| **Total** |                                               | **~50 min** |

---

## Notes

- **Your frontend doesn't need any changes** - CreateTaskModal stays as-is
- **Both old and new endpoints coexist** - No conflicts
- **Rollback is easy** - Just revert main.py
- **Zero risk to users** - Existing pipeline unchanged
- **Start small** - Only register TaskService, add others later

---

## Support

If you encounter issues during integration:

1. **Check logs** - `cd src/cofounder_agent && python main.py`
2. **Review SERVICE_LAYER_BACKWARD_COMPATIBILITY.md** - Detailed architecture
3. **Review service_base.py** - Understand ServiceBase pattern
4. **Test endpoints individually** - Isolate the issue

---

## Next Steps After Integration

Once integrated successfully:

1. **Register additional services** (ModelRouter, ContentService, etc.)
2. **Create LLM integration** (allow LLMs to call service actions)
3. **Add service composition examples** (chain multiple service actions)
4. **Document service patterns** (help team migrate existing services)

---

**Ready to integrate?** Start with Step 1 and work through each step methodically.
