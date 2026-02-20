# Sprint 4 Implementation Plan: Image Generation & Approval Workflow

**Status:** STARTING IMPLEMENTATION  
**Date:** February 19, 2026  
**Duration:** Weeks 7-8 (estimated 16 hours)  
**Team:** Solo Developer (MVP Mode)

---

## Executive Summary

Sprint 4 adds image selection and human approval gates to the content generation pipeline. Users can now see featured images with content, and tasks won't publish without explicit human approval.

**Current Status:**

- Task 4.1 (Image): 70% complete - ImageService implemented, integrated into orchestrator
- Task 4.2 (Approval): 20% complete - Status tracking in place, logic missing
- Task 4.3 (UI): 0% complete - Component needs to be built

**Expected Outcome:**

```
User creates task
  ↓ (Task executes 90% pipeline)
Featured image selected via Pexels API
  ↓ (Image Stage 4 complete)
Task status: "awaiting_approval"
  ↓ (UnifiedOrchestrator returns result)
Content appears in ApprovalQueue UI
  ↓ (Approval component shows task for review)
Human reviews and clicks Approve/Reject
  ↓ (API confirms approval)
Task transitions to "approved" or "failed"
  ↓ (Publishing agent runs or task is abandoned)
Content published to CMS or rejected ✅
```

---

## Task Breakdown

### Task 4.1: Image Selection/Generation ✅ (Already ~70% Complete)

**Current Status:** WORKING

- ImageService.py: Fully implemented (838 lines)
  - `search_featured_image()`: Async search via Pexels API
  - `generate_image()`: SDXL generation fallback
  - Caching and optimization built-in

- unified_orchestrator.py Stage 4 (Lines 892-909): INTEGRATED
  - Calls image_service.search_featured_image()
  - Handles failures gracefully
  - Stores URL in featured_image_url
  - Emits progress updates

- task_executor.py: HANDLING IMAGES
  - Extracts featured_image_url from result
  - Stores in task metadata (lines 321-322)

**What's Working:**

- Pexels API integration ($0/month - unlimited)
- SDXL fallback (local GPU, $0)
- graceful degradation if images unavail able
- Progress tracking (75% complete stage)

**Verification Needed:**

- [ ] Test image selection with real topic
- [ ] Verify image URL stored in database
- [ ] Confirm Pexels API key configured
- [ ] Test fallback behavior (invalid topic → no image)

**Implementation Status:** Ready for testing in 4.3 approval workflow

---

### Task 4.2: Approval Queue Before Publishing ⏳ (IN PROGRESS)

**Current Status:** PARTIALLY COMPLETE

- "awaiting_approval" status: IMPLEMENTED
- Task status transitions: IMPLEMENTED
- Approval endpoints: MISSING
- Publishing logic: MISSING

**What Needs to be Built:**

#### 4.2.1: Approval Routes

File: `src/cofounder_agent/routes/approval_routes.py` (NEW)

Routes to create:

```
POST /api/tasks/{task_id}/approve
  ├─ Body: { "approved": true, "feedback": "..." }
  ├─ Returns: { "status": "approved", "message": "..." }
  └─ Updates: task.status → "approved"

POST /api/tasks/{task_id}/reject
  ├─ Body: { "feedback": "Needs revision...", "reason": "..." }
  ├─ Returns: { "status": "failed", "message": "..." }
  └─ Updates: task.status → "failed"

GET /api/tasks/pending-approval
  ├─ Query: ?user_id=X&limit=10&offset=0
  ├─ Returns: [ { task_id, topic, content, featured_image_url, ... }, ...]
  └─ Filters: status == "awaiting_approval"
```

#### 4.2.2: Database Update

File: `src/cofounder_agent/services/database_service.py`

Methods to add:

```python
async def get_pending_approvals(self, user_id: str = None, limit: int = 20) -> List[Dict]:
    """Fetch tasks awaiting approval"""
    # Filter: status = 'awaiting_approval'
    # Order: created_at DESC (newest first)
    
async def approve_task(self, task_id: str, feedback: str = None) -> bool:
    """Mark task as approved, update status"""
    # Update: status = 'approved', approval_date = now()
    # Store: feedback in metadata
    
async def reject_task(self, task_id: str, feedback: str) -> bool:
    """Mark task as failed/rejected"""
    # Update: status = 'failed', rejection_date = now()
    # Store: rejection reason in metadata
```

#### 4.2.3: Publishing Logic

File: `src/cofounder_agent/services/unified_orchestrator.py`

Change: Stage 5 (Publishing) should only run if status == "approved"

Current: Always publishes if reaches this stage
Future: Check task.status == "approved" before publishing

```python
# STAGE 5: PUBLISHING
if task.status != "approved":
    logger.warning(f"Task {task_id} not approved, skipping publishing")
    return ExecutionResult(...)  # Return pending approval
```

---

### Task 4.3: Approval Queue UI ⏳ (IN PROGRESS)

**Component to Build:** `web/oversight-hub/src/components/ApprovalQueue.jsx`

**Features:**

1. **Pending Tasks List**
   - Shows all tasks with status="awaiting_approval"
   - Columns: Topic, Date Created, Quality Score, Actions
   - Sortable by date (newest first)
   - Pagination (10-20 per page)

2. **Task Preview Modal**
   - Shows full content + featured image
   - Displays quality score and feedback from QA agent
   - Shows metadata (word count, writing style used, etc.)

3. **Approval Controls**
   - **Approve Button**: One-click to approve
   - **Reject Button**: Opens form for feedback
   - **Edit Button**: Open content editor (optional for Sprint 5)

4. **UI Layout**

   ```
   ┌─────────────────────────────────────┐
   │ ✅ Approval Queue (5 pending)       │
   ├─────────────────────────────────────┤
   │                                       │
   │ Topic              │ Date   │ Score  │ Actions │
   ├────────────────────┼────────┼────────┼─────────┤
   │ AI in Healthcare   │ 2 hrs  │ 8.2/10 │ [→] [✓] │
   │ (Featured: image)  │ ago    │        │ [✗] [*] │
   │                    │        │        │         │
   │ Q4 Revenue Report  │ 1 day  │ 7.8/10 │ [→] [✓] │
   │ (No image)         │ ago    │        │ [✗] [*] │
   │                    │        │        │         │
   │ [Load More...]     │        │        │         │
   └─────────────────────────────────────┘
   
   Legend:
   [→] = Preview
   [✓] = Approve
   [✗] = Reject
   [*] = Edit
   ```

5. **Status Indicators**
   - ✅ Approved: Green, hidden from queue
   - ⏳ Awaiting Approval: Yellow/Orange, visible
   - ❌ Rejected: Red, archive visible
   - 🔄 Revisions: Gray, back in queue

---

## Implementation Priority

**Phase 1 (Task 4.2 - Routes):** 4 hours

- [ ] Create approval_routes.py
- [ ] Implement POST /approve and /reject
- [ ] Add database methods
- [ ] Test endpoints

**Phase 2 (Task 4.3 - UI):** 6 hours

- [ ] Create ApprovalQueue.jsx component
- [ ] Fetch pending tasks
- [ ] Implement task preview modal
- [ ] Add approve/reject functionality
- [ ] Style with Tailwind CSS

**Phase 3 (Validation & Testing):** 3 hours

- [ ] Verify image selection works
- [ ] Test end-to-end: create task → approve → publish
- [ ] Test rejection flow (rejected → appears with failure message)
- [ ] Update sprint tracking

**Phase 4 (Polish):** 3 hours

- [ ] Add error handling
- [ ] Loading states
- [ ] Confirmation dialogs
- [ ] Final UI polish

---

## Database Schema Notes

**No new tables needed!** Existing `tasks` table has:

- `id`: Task ID (PRIMARY KEY)
- `status`: VARCHAR - already supports "awaiting_approval", "approved", "failed"
- `metadata`: JSONB - can store approval feedback
- `created_at`, `updated_at`: Already tracked

Fields to store in metadata:

```json
{
  "approval_feedback": "String feedback from approver",
  "approval_date": "ISO timestamp",
  "approved_by": "User ID (optional)",
  "rejection_reason": "String if rejected",
  "featured_image_url": "Pexels URL",
  "featured_image_data": "Image metadata"
}
```

---

## Testing Strategy

### Unit Tests

- [ ] approval_routes.py - test approve/reject endpoints
- [ ] database_service.py - test get_pending_approvals
- [ ] unified_orchestrator.py - test publishing gate

### Integration Tests

- [ ] Create task → awaiting_approval status ✅
- [ ] Approve task → status = "approved"
- [ ] Reject task → status = "failed"
- [ ] Approved task can be published
- [ ] Failed task blocked from publishing

### E2E Tests (Approval Queue)

- [ ] ApprovalQueue fetches pending tasks
- [ ] Preview modal shows content + image
- [ ] Approve button updates status
- [ ] Reject opens form + saves feedback
- [ ] Missing images handled gracefully

### Manual Testing

1. Create blog post task
2. Wait for completion (should see "awaiting_approval")
3. Open Approval Queue
4. Click preview → verify content visible
5. Click approve → verify status changes
6. Check database: status = "approved"
7. Verify Task detail page shows "Approved by You"

---

## Success Criteria

**Task 4.1 (Image Selection):**

- [x] ImageService exists and functional
- [x] Stage 4 integrated in orchestrator
- [ ] Featured images appear in task metadata (needs verification)
- [ ] Graceful fallback when image unavailable

**Task 4.2 (Approval Routes):**

- [ ] POST /tasks/{id}/approve returns 200 OK
- [ ] POST /tasks/{id}/reject returns 200 OK  
- [ ] GET /tasks/pending-approval returns task list
- [ ] Task status updates correctly in database
- [ ] Approval feedback stored in metadata

**Task 4.3 (Approval UI):**

- [ ] ApprovalQueue component renders
- [ ] Shows all pending tasks
- [ ] Preview modal displays content + image
- [ ] Approve/Reject buttons work
- [ ] Real-time status updates (WebSocket optional)

**Overall Sprint 4:**

- ✅ Complete content generation pipeline (research → image → approval)
- ✅ Human approval gate enforced before publishing
- ✅ Approval queue visible in dashboard
- ✅ Zero breaking changes to existing features

---

## Risk Mitigation

**Risk: Image generation fails for some topics**

- Mitigation: Graceful fallback, publish without image if needed
- Already: Pexels search returns None → skip image

**Risk: Approval UI confusion**

- Mitigation: Simple 2-button UI (Approve/Reject), no complex workflows
- Design: Large obvious buttons, single action per click

**Risk: Approval logic breaks publishing**

- Mitigation: Keep publishing logic simple, test thoroughly
- Change: Just add `if status != "approved": return early`

**Risk: Database migration if schema changes**

- Mitigation: NO schema changes, use existing metadata JSONB field
- Safe: All changes backward-compatible

---

## Next Steps (Sprint 5+)

- [ ] Image gallery (user selects from multiple images)
- [ ] Image editing (crop, filter, resize)
- [ ] Approval templates (standard feedback messages)
- [ ] Batch approvals (multi-select + approve all)
- [ ] Approval notifications (email, Slack, Discord)
- [ ] Revision workflows (send back for edits)
- [ ] Publishing schedules (publish at specific time)

---

## References

- ImageService: `src/cofounder_agent/services/image_service.py` (838 lines)
- Orchestrator Stage 4: `src/cofounder_agent/services/unified_orchestrator.py` (lines 892-909)
- Status Enum: `src/cofounder_agent/schemas/task_schemas.py`
- Task Executor: `src/cofounder_agent/services/task_executor.py` (1110 lines)
- WebSocket: For real-time approval updates (optional enhancement)
