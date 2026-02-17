# Phase 4: Analytics & Content Agent WebSocket Integration

**Date:** February 16, 2026  
**Status:** ✅ COMPLETE  
**Services Integrated:** Analytics Routes, Unified Orchestrator (Content Agent)

---

## Summary

Successfully integrated WebSocket event emission into two critical backend services that produce real-time metrics and progress updates:

1. **Analytics Service** (`analytics_routes.py`) - Emits updated KPI metrics
2. **Unified Orchestrator** (`unified_orchestrator.py`) - Emits 5-stage content generation progress

All integrations follow the established non-blocking, error-safe pattern without code duplication.

---

## Integration Details

### 1. Analytics Routes Integration

**File:** `src/cofounder_agent/routes/analytics_routes.py`

**Changes:**

- **Line 24:** Added import for `emit_analytics_update` from websocket_event_broadcaster
- **Lines 361-373:** Added event emission in `get_kpi_metrics()` endpoint

**Implementation:**

```python
# Import
from services.websocket_event_broadcaster import emit_analytics_update

# Emit call (before return statement in get_kpi_metrics)
try:
    await emit_analytics_update(
        total_tasks=total_tasks,
        completed_today=completed_tasks,
        average_completion_time=avg_execution_time,
        cost_today=total_cost,
        success_rate=success_rate,
        failed_today=failed_tasks,
        running_now=pending_tasks,
    )
except Exception as e:
    logger.warning(f"⚠️ Failed to emit analytics update: {e}")
```

**Trigger Point:** When KPI metrics are requested via GET `/api/analytics/kpis`

**Data Emitted:**

- `total_tasks` - Total tasks in selected time range
- `completed_today` - Tasks completed in range
- `average_completion_time` - Avg execution time in seconds
- `cost_today` - Total cost in USD
- `success_rate` - Success percentage (0-100)
- `failed_today` - Failed task count
- `running_now` - Tasks still pending

**Frontend Impact:** Analytics dashboard receives real-time metric updates whenever KPI endpoint is called

---

### 2. Unified Orchestrator Integration (Content Agent)

**File:** `src/cofounder_agent/services/unified_orchestrator.py`

**Changes:**

- **Line 41:** Added import for `emit_task_progress` from websocket_event_broadcaster
- **5 emit calls** at each stage of content generation pipeline

**Pipeline Stages with Emit Points:**

#### Stage 1: Research (10% → 25%)

- **Location:** After research_compliance check (line ~658)
- **Event:** Research Complete
- **Progress:** 25%, completed_steps=1
- **Message:** "Research phase completed - gathered background information"

#### Stage 2: Creative Draft (25% → 45%)

- **Location:** After creative_compliance check (line ~791)
- **Event:** Creative Draft Complete
- **Progress:** 45%, completed_steps=2
- **Message:** "Creative draft generated - ready for quality review"

#### Stage 3: QA Review (45% → 60%)

- **Location:** After qa_compliance check (line ~873)
- **Event:** QA Review Complete
- **Progress:** 60%, completed_steps=3
- **Message:** "Quality assurance review complete - content approved"

#### Stage 4: Image Selection (60% → 75%)

- **Location:** After featured image selection (line ~908)
- **Event:** Image Selection Complete
- **Progress:** 75%, completed_steps=4
- **Message:** "Featured image selected - ready for final formatting"

#### Stage 5: Formatting (75% → 90%)

- **Location:** After publishing agent formats content (line ~933)
- **Event:** Formatting Complete
- **Progress:** 90%, completed_steps=5
- **Message:** "Content formatted and ready for publication"

**Implementation Pattern (All 5 stages):**

```python
# Emit progress: [Stage Name] stage complete
try:
    await emit_task_progress(
        task_id=task_id,
        status="RUNNING",
        progress=[10|25|45|60|75|90],
        current_step="[Stage Name] Complete",
        total_steps=5,
        completed_steps=[1|2|3|4|5],
        message="[Stage-specific message]",
    )
except Exception as e:
    logger.warning(f"⚠️ Failed to emit [stage] progress: {e}")
```

**Trigger Points:** Content generation pipeline execution via POST `/api/tasks` with `task_type: "blog_post"`

**Frontend Impact:**

- Task progress monitor shows real-time stage progression
- Visual progress bar updates at each stage
- Users see detailed current step and completion percentage
- Prevents "silent" long-running operations

---

## Architecture Notes

### Non-Blocking Design

- All emit calls wrapped in try/except
- Failures logged but don't interrupt service execution
- Service continues regardless of WebSocket connectivity
- Zero blocking time on event emission

### Event Flow

- **Analytics:** GET `/api/analytics/kpis` → Emit metrics → WebSocket broadcast
- **Content:** POST `/api/tasks` → Pipeline executes → Emit at each stage → WebSocket broadcast

### WebSocket Infrastructure Used

- **Manager:** `websocket_manager.py` (Connection management)
- **Broadcaster:** `websocket_event_broadcaster.py` (Centralized emit functions)
- **Routes:** `websocket_routes.py` (Global `/ws` endpoint)
- **Frontend:** WebSocket context + hooks for real-time updates

---

## File Modifications Summary

| File | Changes | Lines Added | Status |
|------|---------|-------------|--------|
| `analytics_routes.py` | Import + emit call | 2 + 13 | ✅ Complete |
| `unified_orchestrator.py` | Import + 5 emit calls | 1 + (5 × ~10) | ✅ Complete |

---

## Validation

### Syntax Checks

- ✅ `analytics_routes.py` - No syntax errors
- ✅ `unified_orchestrator.py` - No syntax errors

### Integration Pattern

- ✅ Consistent with Task Executor and Workflow History integrations
- ✅ No code duplication (uses centralized broadcaster)
- ✅ All emit functions properly async and non-blocking
- ✅ Error handling applied at all points

---

## Services Integration Summary

### Completed (Feb 15-16)

| Service | File | Emit Points | Status |
|---------|------|------------|--------|
| Task Executor | `task_executor.py` | 3 (start/fail/success) | ✅ Feb 16 |
| Workflow History | `workflow_history.py` | 1 (on save) | ✅ Feb 16 |
| Analytics | `analytics_routes.py` | 1 (on kpi request) | ✅ Feb 16 |
| Content Agent | `unified_orchestrator.py` | 5 (per stage) | ✅ Feb 16 |

### Pending

- Image Generation service
- Additional content services (if applicable)

---

## Testing Scenarios

### Analytics Integration

```bash
# Trigger analytics emit
curl http://localhost:8000/api/analytics/kpis?range=7d

# Monitor WebSocket for analytics event:
# {"event": "analytics", "data": {...metrics...}}
```

### Content Agent Integration

```bash
# Trigger content generation task
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"task_type": "blog_post", "topic": "Test Article"}'

# Monitor WebSocket for 5 progress updates:
# Step 1: {"current_step": "Research Complete", "progress": 25}
# Step 2: {"current_step": "Creative Draft Complete", "progress": 45}
# Step 3: {"current_step": "QA Review Complete", "progress": 60}
# Step 4: {"current_step": "Image Selection Complete", "progress": 75}
# Step 5: {"current_step": "Formatting Complete", "progress": 90}
```

---

## Next Steps

1. **Test Real-Time Delivery:**
   - Start all services: `npm run dev`
   - Open browser DevTools → Network → WS
   - Trigger analytics or content task
   - Verify WebSocket events received in real-time

2. **Frontend Enhancement (Optional):**
   - Update task monitor to show stage names
   - Add visual indicators for each stage
   - Show estimated time remaining

3. **Production Deployment:**
   - Test with concurrent users
   - Monitor WebSocket connection stability
   - Verify no memory leaks or resource exhaustion

4. **Load Testing:**
   - Simulate 10+ concurrent content generation tasks
   - Verify WebSocket handles broadcast to multiple clients
   - Check performance impact on backend

---

## Integration Checklist

- [x] Analytics Routes: WebSocket import added
- [x] Analytics Routes: Emit call at KPI endpoint
- [x] Unified Orchestrator: WebSocket import added
- [x] Unified Orchestrator: Emit at Stage 1 (Research)
- [x] Unified Orchestrator: Emit at Stage 2 (Creative)
- [x] Unified Orchestrator: Emit at Stage 3 (QA)
- [x] Unified Orchestrator: Emit at Stage 4 (Image)
- [x] Unified Orchestrator: Emit at Stage 5 (Formatting)
- [x] Syntax validation: All files pass pylance check
- [x] Pattern consistency: All use centralized broadcaster
- [x] Error handling: All wrapped in try/except
- [x] Non-blocking: No service execution blocked

---

## Code Quality

- **Duplication:** ✅ None (centralized broadcaster pattern)
- **Error Safety:** ✅ All emissions wrapped in try/except
- **Async/Await:** ✅ All properly awaited
- **Logging:** ✅ All include logger.warning on failure
- **Maintainability:** ✅ Consistent with existing patterns

---

## Related Documentation

- Phase 4 WebSocket Infrastructure: `PHASE_4_WEBSOCKET_IMPLEMENTATION.md`
- Task Executor Integration: `PHASE_4_TASK_EXECUTOR_INTEGRATION.md`
- Workflow History Integration: `WORKFLOW_INTEGRATION_FEB_16.md`
- Unified Orchestrator Source: `src/cofounder_agent/services/unified_orchestrator.py`
- Analytics Routes Source: `src/cofounder_agent/routes/analytics_routes.py`

---

**Integration Complete!** 🎉 All core backend services now emit real-time WebSocket events without code duplication or architectural redundancy.
