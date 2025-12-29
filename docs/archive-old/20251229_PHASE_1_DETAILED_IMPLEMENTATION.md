# Phase 1 Critical Path - Detailed Implementation Plan

**Date:** December 27, 2025  
**Status:** Analysis Complete - Ready for Implementation  
**Source:** Systematic review of existing codebase

---

## Overview

After thorough codebase analysis, the good news is that **much of the infrastructure already exists**. The issue is not missing code but rather the analytics endpoint not using existing methods properly. Here's what we actually need to do:

---

## Issue #1: Analytics KPI Endpoint Returns Mock Data ✅ READY

### Current State

- **File:** [analytics_routes.py](src/cofounder_agent/routes/analytics_routes.py#L130-L138)
- **Problem:** Uses empty mock data instead of querying database
- **Root Cause:** Simply returns `tasks = []` without calling database

### What Already Exists

✅ `DatabaseService.get_all_tasks()` - fetches from content_tasks  
✅ `DatabaseService.get_pending_tasks()` - filters by status  
✅ `DatabaseService.get_task_counts()` - aggregates by status  
✅ `DatabaseService.get_task_costs()` - calculates costs per task  
✅ `DatabaseService.get_tasks_paginated()` - with date/status filtering

### What Needs to Be Done

**1A. Add Date Range Query Method to DatabaseService**

**Location:** After `get_queued_tasks()` in [database_service.py](src/cofounder_agent/services/database_service.py#L830+)

**What to Add:**

```python
async def get_tasks_by_date_range(
    self,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    status: Optional[str] = None,
    limit: int = 10000
) -> List[Dict[str, Any]]:
    """
    Get tasks from content_tasks within date range.

    Args:
        start_date: Start of date range (UTC)
        end_date: End of date range (UTC) - defaults to now
        status: Filter by status (optional)
        limit: Maximum results

    Returns:
        List of task dicts
    """
```

**Why Needed:**

- Analytics endpoint needs to filter tasks by date range (1d, 7d, 30d, 90d, all)
- `get_tasks_paginated()` exists but doesn't support date range filtering
- This will be a simple addition using WHERE clause with `created_at BETWEEN`

**Estimated Effort:** 30 minutes

---

**1B. Update analytics_routes.py to Use Real Queries**

**Location:** [analytics_routes.py](src/cofounder_agent/routes/analytics_routes.py#L130-L138)

**Current Code:**

```python
# Use mock data for now - database query causing issues
# TODO: Fix database schema and implement proper query
logger.debug(f"  ℹ️  Using mock task data for analytics")
tasks = []  # Empty list - will be populated with mock data below
```

**Change To:**

```python
# Query tasks for date range from content_tasks
tasks = await db.get_tasks_by_date_range(
    start_date=start_time,
    end_date=now,
    limit=10000
)
logger.debug(f"  ✅ Retrieved {len(tasks)} tasks from database")
```

**Estimated Effort:** 15 minutes

---

### Phase 1.1 Verification

**Test Endpoint:**

```bash
curl "http://localhost:8000/api/analytics/kpis?range=30d" \
  -H "Authorization: Bearer dev-token-test" | python -m json.tool
```

**Expected Result:** ✅ HTTP 200 with real task metrics (instead of all zeros)

**Success Criteria:**

- [ ] Returns HTTP 200
- [ ] `total_tasks` is > 0 (if tasks exist in database)
- [ ] `completed_tasks` reflects actual completed tasks
- [ ] Time-series data populates for charts
- [ ] Dashboard loads without "Failed to fetch" error

---

## Issue #2: Task Status Not Properly Updated ✅ READY

### Current State

- **File:** [database_service.py](src/cofounder_agent/services/database_service.py#L650)
- **Status:** ✅ Already Implemented!
- **Method:** `async def update_task_status()`

### What Already Exists

✅ `update_task_status(task_id, status, result)` - updates status in content_tasks  
✅ Uses proper timestamp (`updated_at = NOW()`)  
✅ Returns updated task  
✅ Logs success/failure

### Verification Needed

Check that task routes are using this method:

**Files to Check:**

- [task_routes.py](src/cofounder_agent/routes/task_routes.py) - confirm update_task_status is called
- [content_routes.py](src/cofounder_agent/routes/content_routes.py) - confirm status updates

**Questions:**

- [ ] Are task status updates being called from task execution?
- [ ] Are status transitions validated (pending → processing → completed)?
- [ ] Is there a lifecycle management system?

**Estimated Investigation:** 1 hour

---

## Issue #3: Cost Calculations Hardcoded ⚠️ NEEDS WORK

### Current State

- **File:** [main.py](src/cofounder_agent/main.py#L1074, #L1086)
- **Problem:** Cost hardcoded to 0.03 (blog) and 0.02 (image)
- **File Location:**

```python
# Line 1074
cost = 0.03  # Placeholder

# Line 1086
cost = 0.02  # Placeholder
```

### What Already Exists

⚠️ No dedicated cost calculation service found
❓ Need to check if model_router provides pricing
❓ Need to check task_metadata storage for cost tracking

### Work Required

**3A. Create Cost Calculation Service (NEW)**

**Location:** Create [services/cost_calculator.py](src/cofounder_agent/services/cost_calculator.py)

**Features:**

- Get cost from model_router for specific model/tokens
- Calculate cost breakdown by pipeline phase
- Store in task metadata
- Track total project costs

**Estimated Effort:** 2-3 hours

**3B. Update main.py to Use Real Costs**

Replace hardcoded values:

```python
# OLD
cost = 0.03  # Placeholder

# NEW
from services.cost_calculator import CostCalculator
cost_calc = CostCalculator(model_router)
cost = await cost_calc.calculate_task_cost(
    model=model_used,
    tokens_used=token_count,
    task_type=task_type
)
```

**Estimated Effort:** 1 hour

---

## Issue #4: Database Query Methods Missing ✅ PARTIAL

### Current State

- Code was calling `db.query()` which doesn't exist
- This was the cascade failure blocking analytics

### What Already Exists

✅ `get_all_tasks()` - generic task fetch  
✅ `get_pending_tasks()` - status filter  
✅ `get_task_counts()` - count by status  
✅ `get_tasks_paginated()` - pagination + filtering  
✅ `get_queued_tasks()` - specific status  
✅ `update_task_status()` - update status

### What's Still Missing

❌ `get_tasks_by_date_range()` - needed for analytics (Issue #1A above)

**Note:** No general `.query()` method needed - all specific methods exist. The analytics endpoint just needs to use `get_tasks_by_date_range()` once created.

---

## Issue #5: Orchestrator Endpoints - Training Data Export

### Current State

- **File:** [orchestrator_routes.py](src/cofounder_agent/routes/orchestrator_routes.py#L235-L243)
- **Problem:** Returns empty data
- **Status:** Can be implemented using existing task queries

### Implementation Path

**5A. Add Export Helper to DatabaseService**

**New Method:**

```python
async def get_completed_tasks_for_export(
    self,
    min_quality_score: float = 0.7,
    limit: int = 10000
) -> List[Dict[str, Any]]:
    """Get completed tasks for training data export"""
    # Query content_tasks WHERE status = 'completed' AND quality_score >= min_quality_score
```

**Estimated Effort:** 1 hour

**5B. Update orchestrator_routes.py**

Replace stub with real implementation:

- Call new export method
- Format as JSONL/CSV
- Generate download URL or return data

**Estimated Effort:** 2-3 hours

---

## Issue #6: Settings Persistence

### Current State

- **File:** [settings_routes.py](src/cofounder_agent/routes/settings_routes.py)
- **Problem:** All endpoints return mock data
- **Status:** Partially ready (database infrastructure exists)

### What Already Exists

✅ `add_log_entry()` - pattern for storing data  
✅ `add_financial_entry()` - pattern for JSONB storage  
✅ `get_financial_summary()` - pattern for querying

### What's Missing

❌ `settings` table schema  
❌ DatabaseService methods for CRUD settings

### Implementation Path

**6A. Create Settings Table (SQL Migration)**

```sql
CREATE TABLE IF NOT EXISTS settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    key VARCHAR(255) NOT NULL,
    value JSONB,
    category VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, key)
);
```

**Estimated Effort:** 30 minutes

**6B. Add DatabaseService Methods**

```python
async def get_setting(self, key: str, user_id: Optional[str] = None) -> Optional[Dict]
async def set_setting(self, key: str, value: Any, user_id: Optional[str] = None) -> Dict
async def get_all_settings(self, user_id: Optional[str] = None) -> List[Dict]
async def delete_setting(self, key: str, user_id: Optional[str] = None) -> bool
```

**Estimated Effort:** 2-3 hours

**6C. Update settings_routes.py**

Replace all mock returns with database calls

**Estimated Effort:** 2-3 hours

---

## Summary of Work Items

### 🔴 CRITICAL PATH (Phase 1) - Total: 12-16 hours

| Item                                               | Status    | Effort | Blocker |
| -------------------------------------------------- | --------- | ------ | ------- |
| Add `get_tasks_by_date_range()` to DatabaseService | ✅ Ready  | 0.5h   | YES     |
| Update analytics_routes.py to use real queries     | ✅ Ready  | 0.25h  | YES     |
| Verify task status updates working                 | ⚠️ Check  | 1h     | NO      |
| Create cost calculation service                    | ⚠️ Needed | 2-3h   | NO      |
| Update main.py to use real costs                   | ⚠️ Needed | 1h     | NO      |
| Add training export helper                         | ⚠️ Needed | 1h     | NO      |
| Update orchestrator export endpoint                | ⚠️ Needed | 2-3h   | NO      |
| Create settings table + migrations                 | ⚠️ Needed | 0.5h   | NO      |
| Add DatabaseService settings methods               | ⚠️ Needed | 2-3h   | NO      |
| Update settings_routes.py endpoints                | ⚠️ Needed | 2-3h   | NO      |

### 🟠 HIGH PRIORITY (Phase 2) - Dependency

- Model upload endpoint
- Learning patterns extraction
- MCP tool discovery
- LLM-based quality evaluation

---

## No Duplicates - Existing Code Inventory

✅ **DO NOT CREATE** - These already exist:

- Task CRUD endpoints
- Task status update method
- Task query methods (generic)
- Pagination system
- Log storage pattern
- Financial tracking pattern

⚠️ **NEED TO CREATE** - These are missing:

- Date range query for analytics
- Cost calculation service
- Settings storage system
- Training data export helper

---

## Recommended Execution Order

### Day 1 (3-4 hours)

1. Add `get_tasks_by_date_range()` to DatabaseService (30 min)
2. Update analytics_routes.py to use real queries (15 min)
3. Test analytics endpoint (15 min)
4. ✅ Dashboard now shows real metrics

### Day 2 (4-5 hours)

5. Create cost calculator service (2-3 hours)
6. Update main.py to use real costs (1 hour)
7. Test cost tracking (30 min)

### Day 3 (4-5 hours)

8. Create settings table + migrations (30 min)
9. Add DatabaseService settings methods (2-3 hours)
10. Update settings_routes.py (1-2 hours)

### Days 4-5 (3-4 hours)

11. Add training data export helper (1 hour)
12. Update orchestrator endpoints (2-3 hours)

---

## Testing Checklist

After each implementation:

```bash
# Test analytics
curl "http://localhost:8000/api/analytics/kpis?range=30d" | python -m json.tool

# Check dashboard loads
curl "http://localhost:3001" | grep -i "kpi\|metric"

# Test settings
curl -X POST "http://localhost:8000/api/settings" \
  -H "Content-Type: application/json" \
  -d '{"key": "test", "value": "test_value"}'

# Check database
psql -U postgres -d glad_labs_dev -c "SELECT COUNT(*) FROM content_tasks;"
```

---

## Risk Mitigation

| Risk                                | Mitigation                                     |
| ----------------------------------- | ---------------------------------------------- |
| Database migration fails            | Test on staging first; have rollback ready     |
| Cascade failure if analytics breaks | Add try/catch; return partial data             |
| Cost calculation takes too long     | Cache results; make async                      |
| Settings not persisting             | Check database connection; verify table exists |

---

## Verification That We're Not Duplicating

**Confirmed EXISTING:**

- [x] `DatabaseService.get_all_tasks()` - line 336
- [x] `DatabaseService.get_pending_tasks()` - line 313
- [x] `DatabaseService.get_task_counts()` - line 803
- [x] `DatabaseService.update_task_status()` - line 650
- [x] `DatabaseService.get_tasks_paginated()` - line 759
- [x] Task route CRUD - task_routes.py

**Confirmed MISSING:**

- [ ] `DatabaseService.get_tasks_by_date_range()` - NEEDS TO BE ADDED
- [ ] Cost calculation service - NEEDS TO BE CREATED
- [ ] Settings CRUD in DatabaseService - NEEDS TO BE ADDED
- [ ] Settings table schema - NEEDS SQL MIGRATION

---

_Plan Created: 2025-12-27_  
_Status: Ready for Implementation_  
_Duplication Check: Complete_
