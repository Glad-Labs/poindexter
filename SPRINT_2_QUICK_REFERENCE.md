# Sprint 2 Quick Reference Guide
**For:** Implementation Planning  
**Date:** February 19, 2026

---

## ROUTES TO REFACTOR (Return 202 ACCEPTED)

### Top 3 Priority Routes

| # | Route | File | Current Status Code | Change | Effort |
|---|-------|------|-------------------|--------|--------|
| 1 | `POST /api/tasks` | task_routes.py:164 | `201 Created` | → `202 ACCEPTED` | 2h |
| 2 | `POST /api/workflows/execute/{template}` | workflow_routes.py:244 | `200 OK` | → `202 ACCEPTED` | 2h |
| 3 | `POST /api/workflows/custom/{id}/execute` | custom_workflows_routes.py | `200 OK` | → `202 ACCEPTED` | 1h |

---

## BACKGROUND EXECUTION (Already Working)

```
TaskExecutor Details:
  - File: src/cofounder_agent/services/task_executor.py
  - Polling Interval: 5 seconds ✅
  - Max Tasks/Cycle: 10 ✅
  - State Management: pending → in_progress → awaiting_approval/failed ✅
  - Database: PostgreSQL (asyncpg) ✅
  
Patterns Already Used:
  - asyncio.create_task() ✅
  - Non-blocking async/await ✅
  - WebSocket progress broadcast (Phase 4) ✅
  - Database status tracking ✅
```

---

## DATABASE SCHEMA (Ready to Use)

**Main Table:** `content_tasks`
- Columns: id, task_id, task_name, task_type, status, task_metadata, result, created_at
- Status Values: pending, in_progress, awaiting_approval, approved, published, failed
- Persistence: Fast asyncpg queries ✅

**Audit Table:** `task_status_history`
- Tracks all status transitions
- Auto-populated by TaskExecutor
- Ready for Sprint 2 ✅

**Execution Table:** `workflow_executions`
- Stores complete execution records (Sprint 1 complete)
- Used for result retrieval after completion
- Ready for Sprint 2 ✅

---

## RESPONSE PATTERNS

### Current (POST /api/tasks)
```json
{
  "id": "uuid",
  "task_id": "uuid",
  "status": "pending",
  "message": "...created and queued"
}
```
Status Code: `201 Created`

### Target (POST /api/tasks - Post Sprint 2)
```json
{
  "execution_id": "uuid",
  "status": "pending",
  "message": "Task execution queued",
  "estimated_duration_seconds": 180
}
```
Status Code: `202 ACCEPTED`
Headers: 
- `Location: /api/tasks/{task_id}/status`
- `Retry-After: 5`

---

## POLLING PATTERN (For Clients)

```
1. POST /api/tasks → 202 ACCEPTED + execution_id
2. GET /api/tasks/{execution_id}/status → {status: "in_progress", progress: 45}
3. Repeat step 2 every 3-5 seconds (respecting Retry-After header)
4. When status = "awaiting_approval|failed", show results
```

**Status Query Endpoints:**
- `GET /api/tasks/{task_id}` - Full task details
- `GET /api/workflows/executions/{execution_id}` - Full execution details
- Both return status + results when complete

---

## KEY FUNCTIONS TO REFERENCE

| Function | File | Purpose | Important Details |
|----------|------|---------|-------------------|
| `create_task()` | task_routes.py:164 | Main entry point | Change status code to 202 |
| `_process_loop()` | task_executor.py:139 | Background polling | 5s interval, 10 tasks/cycle |
| `_process_single_task()` | task_executor.py:224 | Process one task | Updates status in DB |
| `_execute_task()` | task_executor.py:450+ | Actual execution | 900s timeout, calls orchestrator |
| `execute_template()` | template_execution_service.py:182 | Template execution | Move to async, return 202 |
| `get_pending_tasks()` | tasks_db.py:78 | Poll database | Fast asyncpg query |

---

## CONFIGURATION REVIEW

**Current Timeouts:**
- Task execution: 900 seconds (15 min)
- Blog post template: ~900 seconds (15 min)
- Newsletter template: ~1200 seconds (20 min) ⚠️

**Action:** Verify timeout >= template duration

---

## TESTING CHECKLIST

```
✅ Unit Tests:
  - create_task() returns 202
  - execute_template() returns 202
  - Status query endpoints respond with accurate data

✅ Integration Tests:
  - Create task → TaskExecutor picks it up within 5s
  - Task processes completely
  - Status updates available via GET
  - WebSocket broadcasts progress

✅ Manual Testing:
  - POST /api/tasks → 202 + task_id
  - Poll GET /api/tasks/{id} → see status changes
  - Check oversigh-hub UI still works
  - Monitor background logs for errors

✅ Load Testing:
  - 10 concurrent tasks
  - All complete successfully
  - No orphaned tasks
```

---

## BLOCKERS & RISKS

**🟢 No Major Blockers** - All prerequisites complete

**⚠️ Minor Considerations:**
1. **Breaking Change:** 202 status code requires client update
   - Solution: Oversight Hub already handles async patterns
   
2. **Timeout Mismatch:** Newsletter template (20m) vs executor timeout (15m)
   - Solution: Increase timeout or document limitation
   
3. **Error Recovery:** No retry logic on LLM failures
   - Solution: Document as future improvement (Sprint 6)

---

## FILES TO CHANGE

**Primary Changes (Must Modify):**
- [ ] `src/cofounder_agent/routes/task_routes.py` - create_task() → 202
- [ ] `src/cofounder_agent/routes/workflow_routes.py` - execute_workflow_template() → 202
- [ ] `src/cofounder_agent/routes/custom_workflows_routes.py` - execute_workflow() → 202

**Secondary Enhancements (Optional):**
- [ ] Add `GET /api/tasks/{id}/status` lightweight endpoint
- [ ] Add `GET /api/workflows/{id}/status` lightweight endpoint
- [ ] Update documentation/API changelog

**No Changes Required:**
- ✅ task_executor.py (already functional)
- ✅ database_service.py (already async)
- ✅ websocket_routes.py (already working)

---

## SUCCESS METRICS (Post-Sprint 2)

| Metric | Target | Verification |
|--------|--------|---------------|
| API response time | < 100ms | `curl -w "%{time_total}"` |
| Background execution time | Unchanged (3-5 min) | Task creation → completion logs |
| Task completion rate | 100% | All pending tasks → awaiting_approval |
| Error recovery | No orphaned tasks | Database audit trail |
| Client compatibility | No regressions | Oversight Hub UI works normally |

---

## TOTAL SPRINT 2 EFFORT

```
Task 2.1: Refactor routes to 202        4-5 hours
Task 2.2: Verify TaskExecutor           3-4 hours  
Task 2.3: Enhance status endpoints      3   hours
─────────────────────────────────────────────────
TOTAL SPRINT 2 EFFORT:                  10-12 hours (slightly under estimated 16h)
```

---

## NEXT STEPS

1. **Review This Report** - Share with team/stakeholder
2. **Clarify Questions** - Any blockers or unknowns?
3. **Plan Task Breakdown** - Create GitHub issues for each task
4. **Set Timeline** - Weeks 3-4 (March 3-16, 2026)
5. **Begin Implementation** - Task 2.1 first (routes refactor)

---

**Questions?** Refer to SPRINT_2_RESEARCH_REPORT.md for detailed context.

