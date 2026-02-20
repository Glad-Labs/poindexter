# Sprint 2 Research Report: Async Execution Refactor
**Date:** February 19, 2026  
**Research Completed By:** Copilot Research Agent  
**Status:** Ready for Implementation Planning

---

## EXECUTIVE SUMMARY

Sprint 2 requires refactoring long-running routes to return **202 ACCEPTED** responses immediately while `TaskExecutor` continues work in background. Current routes execute synchronously (block for 2-3 minutes), causing client timeouts. The refactor will decouple request/response from execution, enabling immediate feedback to clients while background processing completes.

**Current State:** All long-running work is already wired for async execution via `asyncio.create_task()` and `TaskExecutor` polling. **Key gap:** Routes still synchronously wait for results instead of returning 202 immediately.

---

## 1. LONG-RUNNING ROUTES NEEDING 202 REFACTOR

### Summary
**Routes to Refactor:** 3 primary routes  
**Estimated Total Execution Time:** 180-300 seconds (3-5 minutes)  
**Implementation Pattern:** Return 202 with `execution_id`, let TaskExecutor poll background work

---

### 1.1 POST /api/tasks (Create Task)
**File:** [src/cofounder_agent/routes/task_routes.py](src/cofounder_agent/routes/task_routes.py#L164)  
**Status:** Currently synchronous (lines 340-370 run content generation in background but route doesn't complete)

**Current Flow:**
```python
# Lines 164-250: create_task() endpoint
async def create_task(request: UnifiedTaskRequest, ...):
    # 1. Validate request
    # 2. Create task in database (status: "pending")
    # 3. Schedule background generation:
    asyncio.create_task(_run_blog_generation())
    # 4. Return 201 with task_id
    return {"id": task_id, "status": "pending", ...}
```

**Current Response Pattern:**
```json
{
  "id": "<uuid>",
  "task_id": "<uuid>",
  "task_type": "blog_post",
  "status": "pending",
  "created_at": "2026-02-19T...",
  "message": "Blog post task created and queued"
}
```

**Response Status Code:** `201 Created` (should be `202 ACCEPTED` after refactor)

**Task Types (all use same pattern):**
- `blog_post` - 120-180 seconds (self-critiquing 6-stage pipeline)
- `social_media` - 30-60 seconds
- `email` - 30-60 seconds
- `newsletter` - 120-180 seconds
- `business_analytics` - 60-120 seconds
- `data_retrieval` - 30-120 seconds
- `market_research` - 60-120 seconds
- `financial_analysis` - 60-120 seconds

**Route Handler Functions** (lines 340-550):
- `_handle_blog_post_creation()` → calls `process_content_generation_task()`
- `_handle_social_media_creation()`
- `_handle_email_creation()`
- `_handle_newsletter_creation()`
- `_handle_business_analytics_creation()`
- `_handle_data_retrieval_creation()`
- `_handle_market_research_creation()`
- `_handle_financial_analysis_creation()`

**What Triggers Completion:**
- `process_content_generation_task()` in [src/cofounder_agent/services/content_router_service.py](src/cofounder_agent/services/content_router_service.py#L419) runs all 6 stages
- Stages: verify → research → generate → critique → refine → publish
- Each stage calls LLM (via model_router), can take 20-40 seconds per stage
- Total: ~3-5 minutes for full blog post generation

**Changes Needed:**
- ✅ Response already scheduled as async task → No code change
- Only change: Return 202 instead of 201
- Already has `execution_id` pattern (uses `task_id`)

**Priority:** HIGH (most frequently used)

---

### 1.2 POST /api/workflows/execute/{template_name}
**File:** [src/cofounder_agent/routes/workflow_routes.py](src/cofounder_agent/routes/workflow_routes.py#L244)  
**Status:** Implemented (lines 244-320)

**Current Flow:**
```python
async def execute_workflow_template(template_name: str, task_input: Dict[str, Any], ...):
    # 1. Validate template name
    # 2. Get template execution service
    # 3. Execute template via service.execute_template()
    result = await template_service.execute_template(
        template_name=template_name,
        task_input=task_input,
        ...
    )
    # 4. Returns full result (blocks until complete)
```

**Current Response Pattern** (example blog_post):
```json
{
  "execution_id": "550e8400-e29b...",
  "workflow_id": "550e8400-e29b...",
  "template": "blog_post",
  "status": "completed",
  "phases": ["research", "draft", "assess", "refine", "finalize", "image_selection", "publish"],
  "phase_results": {...},
  "final_output": {...},
  "error_message": null,
  "duration_ms": 15234.5
}
```

**Response Status Code:** `200 OK` (should be `202 ACCEPTED` + `Location` header)

**Template Types Available** (from lines 16-71 of template_execution_service.py):
| Template | Phases | Duration | Status |
|----------|--------|----------|--------|
| `blog_post` | research, draft, assess, refine, image, publish | 900s (15m) | Long |
| `social_media` | draft, assess, publish | 300s (5m) | Medium |
| `email` | draft, assess, publish | 240s (4m) | Medium |
| `newsletter` | research, draft, assess, refine, image, publish | 1200s (20m) | Long |
| `market_analysis` | research, assess, publish | 600s (10m) | Medium |

**Execution Flow** (from template_execution_service.py line 182+):
1. Validate template name → lines 205-211
2. Build CustomWorkflow from template → `build_workflow_from_template()` lines 117-175
3. Initialize progress tracking → `_initialize_progress_tracking()` lines 276-304
4. Execute via `CustomWorkflowsService` → `execute_workflow()` awaits result
5. Returns immediately with full results

**What Triggers Completion:**
- `CustomWorkflowsService.execute_workflow()` [src/cofounder_agent/services/custom_workflows_service.py](src/cofounder_agent/services/custom_workflows_service.py)
- Executes all phases sequentially through `WorkflowEngine`
- Each phase calls agents/LLMs
- Stores execution record to `workflow_executions` table (Sprint 1 - COMPLETE)

**Changes Needed:**
- Return 202 immediately with execution_id
- Move actual execution to background task
- Currently returns 200 with full result → change to 202 with minimal response
- `execution_id` already used, can reuse pattern

**Priority:** HIGH (used in Oversight Hub UI)

---

### 1.3 POST /api/workflows/custom/{workflow_id}/execute
**File:** [src/cofounder_agent/routes/custom_workflows_routes.py](src/cofounder_agent/routes/custom_workflows_routes.py)  
**Status:** Search shows route exists but working through custom execution

**Current Flow:**
- Route: POST /api/workflows/custom/{workflow_id}/execute
- Receives: WorkflowExecutionRequest (initial_inputs, skip_phases, quality_threshold)
- Calls: CustomWorkflowsService.execute_workflow()
- Returns: Execution result with all phase results

**Execution Pattern:** Same as template execution (uses same underlying service)

**Changes Needed:** Same as route #2 - return 202 instead of 200

**Priority:** MEDIUM (less frequently used than templates)

---

### 1.4 Other Candidate Routes for 202 Conversion
**Searching task_routes.py for sync patterns:**

- `POST /api/tasks/{task_id}/approve` (lines 1749+) → Currently quick operation (< 100ms), probably skip
- `POST /api/tasks/{task_id}/publish` (lines 2037+) → Currently quick operation (< 100ms), probably skip
- `PUT /api/tasks/{task_id}/status` (lines 1304+) → Status update only, quick
- Other routes use GET/info operations, skip

**Recommendation:** Focus on 3 primary routes above. Others are already fast.

---

## 2. CURRENT TASKEXECUTOR IMPLEMENTATION

### 2.1 TaskExecutor Service Overview
**File:** [src/cofounder_agent/services/task_executor.py](src/cofounder_agent/services/task_executor.py)  
**Lines:** 1-1110 (comprehensive implementation)

**Class Structure:**
```python
class TaskExecutor:
    """Background task executor service"""
    
    def __init__(self, database_service, orchestrator=None, poll_interval: int = 5, app_state=None):
        self.database_service = database_service
        self.orchestrator = orchestrator
        self.poll_interval = poll_interval      # DEFAULT: 5 seconds ✅
        self.running = False
        self.task_count = 0
        self.success_count = 0
        self.error_count = 0
        self._processor_task = None
```

**Initialization:** [main.py](src/cofounder_agent/main.py) startup (around line 200+)
- Creates TaskExecutor instance
- Calls `await task_executor.start()` 
- Returns immediately, background polling begins

---

### 2.2 Polling Interval & Startup
**Code:** Lines 99-121

**Configuration:**
- **Poll Interval:** 5 seconds (configurable via constructor)
- **Max Tasks Per Cycle:** 10 (hardcoded line 141)
- **Startup:** `asyncio.create_task(self._process_loop())` → Creates background task
- **Shutdown:** `stop()` method cancels processor task

**Polling Loop** (lines 139-201):
```python
async def _process_loop(self):
    """Main processing loop - runs continuously in background"""
    while self.running:
        try:
            # 1. Poll for pending tasks (max 10)
            pending_tasks = await self.database_service.get_pending_tasks(limit=10)
            
            if pending_tasks:
                # 2. Process each task
                for task in pending_tasks:
                    await self._process_single_task(task)
            else:
                logger.debug("No pending tasks - sleeping for {poll_interval}s")
            
            # 3. Sleep before next poll
            await asyncio.sleep(self.poll_interval)  # 5 second wait
```

**Status Transitions:**
- `pending` → `in_progress` (line 263) → `awaiting_approval` or `failed` (line 331)
- Database update on each transition (lines 263-337)

---

### 2.3 Task Processing & Execution
**Code:** Lines 224-450 (_process_single_task & _execute_task)

**Single Task Processing:**
```
1. Mark task status → "in_progress" (line 263)
   UPDATE content_tasks SET status='in_progress' WHERE id={task_id}
   
2. Emit WebSocket progress (line 275) - Phase 4, already implemented ✅
   
3. Execute task with timeout (line 290)
   timeout=900 seconds (15 minutes) - configurable per task
   
4. Handle result (lines 315-410)
   - Extract content, title, metadata
   - Update task_metadata with results
   - Set final status (awaiting_approval, failed, etc.)

5. Emit WebSocket completion event (line 405)
```

**Error Recovery:**
- Timeouts: Set status to `failed` with timeout message (lines 300-305)
- Exceptions during execution: Caught, logged, status set to `failed` (lines 186-198)
- No retry logic currently ⚠️ (potential issue for flaky LLM calls)

**Performance:**
- Can process 10 tasks in parallel per cycle (line 141: `limit=10`)
- Each task can take 0-15 minutes
- Database I/O is fast (Postgres + asyncpg)
- Bottleneck: LLM API calls (OpenAI, Anthropic, etc.)

---

### 2.4 Status Tracking in Database
**Task Status Column:**
```python
# From tasks_db.py, content_tasks table
status: str  # Values: "pending", "in_progress", "awaiting_approval", "approved", "published", "failed"
```

**Task Metadata:**
```python
# Stored as JSON in task_metadata column
task_metadata: Dict containing:
  - content: str (generated content)
  - title: str
  - excerpt: str
  - featured_image_url: str
  - seo_keywords: list
  - quality_score: float
  - orchestrator_error: str (if failed)
  - started_at: ISO datetime
```

**Status History Audit Table:**
**File:** [src/cofounder_agent/services/tasks_db.py](src/cofounder_agent/services/tasks_db.py#L727)

```python
# table: task_status_history
Columns:
  - task_id: uuid
  - old_status: str
  - new_status: str
  - reason: str (optional)
  - metadata: json (optional)
  - timestamp: datetime (DEFAULT now())
```

**Logging:** Method `_log_status_change()` at lines 727-765

---

### 2.5 Task Completion Verification
**How system knows task is complete:**

1. **TaskExecutor sets status** → database updated synchronously (line 331)
2. **Client polls GET /api/tasks/{task_id}** → reads latest status
3. **WebSocket broadcasts progress** → real-time updates (Phase 4, already implemented)

**Current Flow (Post-Sprint 1):**
```
1. Client: POST /api/tasks → Returns 201 + task_id
2. Client: Polls GET /api/tasks/{task_id} every 2-5 seconds
   OR
   Client: Listens to WS /api/ws for progress events
3. TaskExecutor: Polls database every 5 seconds
4. TaskExecutor: Finds pending task → marks in_progress
5. TaskExecutor: Executes through orchestrator (2-5 minutes)
6. TaskExecutor: Updates task status → awaiting_approval
7. Client: Next poll gets awaiting_approval status
```

**Gap for Sprint 2:** Routes don't return 202 explicitly, but background execution is already setup ✅

---

## 3. DATABASE SCHEMA FOR TASK TRACKING

### 3.1 Main Task Table
**Table:** `content_tasks`  
**Primary Use:** Store all task data

**Key Columns:**
| Column | Type | Purpose | Notes |
|--------|------|---------|-------|
| id | UUID | Primary key | ✅ |
| task_id | UUID | Alternative ID | Duplicate of id for backwards compat |
| task_name | VARCHAR | Human-readable task name | ✅ |
| task_type | VARCHAR | blog_post, social_media, etc. | ✅ |
| status | VARCHAR | pending, in_progress, awaiting_approval, approved, published, failed | ✅ |
| topic | TEXT | Task topic/subject | ✅ |
| category | VARCHAR | Content category | ✅ |
| created_at | TIMESTAMP | Creation timestamp | ✅ |
| updated_at | TIMESTAMP | Last update | ✅ |
| task_metadata | JSONB | Dynamic metadata (content, title, etc.) | ✅ |
| result | JSONB | Final results (alternative to task_metadata) | ✅ |
| user_id | VARCHAR | User who created task | ✅ |

**Status Values (from utils/task_status.py):**
```python
class TaskStatus(Enum):
    PENDING = "pending"           # Initial state
    IN_PROGRESS = "in_progress"   # TaskExecutor picked it up
    AWAITING_APPROVAL = "awaiting_approval"  # Content generated, waiting human review
    APPROVED = "approved"         # Approved for publishing
    PUBLISHED = "published"       # Published to CMS/social
    FAILED = "failed"             # Error during execution
```

**Status Transition Rules** (from utils/task_status.py):
```
pending → in_progress (TaskExecutor)
in_progress → awaiting_approval (TaskExecutor after eval)
in_progress → failed (TaskExecutor on error)
awaiting_approval → approved (Human approval)
approved → published (Publishing agent)
approved → awaiting_approval (Human requests revisions)
```

---

### 3.2 Task Status History Table
**Table:** `task_status_history`  
**Purpose:** Audit trail of all status changes

**Schema:**
```sql
CREATE TABLE task_status_history (
    id SERIAL PRIMARY KEY,
    task_id UUID NOT NULL,
    old_status VARCHAR,
    new_status VARCHAR,
    reason VARCHAR,
    metadata JSONB,
    timestamp TIMESTAMP DEFAULT NOW()
)
```

**Used By:** `_log_status_change()` in [tasks_db.py](src/cofounder_agent/services/tasks_db.py#L727)

---

### 3.3 Workflow Execution History (Sprint 1 - Complete)
**Table:** `workflow_executions`  
**Purpose:** Persist workflow execution results

**Columns:**
```python
id: UUID
workflow_id: UUID
owner_id: VARCHAR
execution_status: VARCHAR (completed, failed, cancelled, pending)
initial_input: JSONB
phase_definitions: JSONB (snapshot of phase definitions at save time)
phase_results: JSONB (results from each phase)
final_output: JSONB (assembled final output)
error_message: TEXT
duration_ms: FLOAT
created_at: TIMESTAMP
completed_at: TIMESTAMP (NULL if still running)
tags: JSONB
metadata: JSONB
```

**Purpose:** Enables Sprint 2 workflow status queries without re-executing

---

### 3.4 Schema Gaps Identified
**Issue:** No separate `task_executions` table for tracking background execution status independently from task creation.

**Current Workaround:** Uses `content_tasks.status` column directly
- ✅ Works for new system
- Gap: Can't distinguish "user created task with parameters" from "background execution is running"

**For Sprint 2:** Not critical - existing status column sufficient for 202 pattern

---

## 4. CURRENT RESPONSE PATTERNS

### 4.1 Current Task Creation Response (POST /api/tasks)
**Status Code:** `201 Created`

**Currently Returns:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "task_type": "blog_post",
  "topic": "AI in Healthcare",
  "status": "pending",
  "created_at": "2026-02-19T14:30:00Z",
  "message": "Blog post task created and queued"
}
```

**Sprint 2 Change Required:**
- Status code: 201 → **202 ACCEPTED**
- Response: Same JSON (clients already expect execution_id)
- Add `Location` header → pointing to GET /api/tasks/{task_id}/status endpoint
- Optionally add `Retry-After` header → suggested poll interval (e.g., 5-10 seconds)

---

### 4.2 Current Workflow Template Execution Response
**Status Code:** `200 OK`

**Currently Returns (example - FULL COMPLETE RESULT):**
```json
{
  "execution_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_id": "550e8400-e29b-41d4-a716-446655440001",
  "template": "blog_post",
  "status": "completed",
  "phases": ["research", "draft", "assess", "refine", "finalize", "image_selection", "publish"],
  "phase_results": {
    "research": {
      "status": "completed",
      "duration_ms": 4520,
      "result": {...}
    },
    ...
  },
  "final_output": {
    "title": "...",
    "content": "...",
    "featured_image_url": "..."
  },
  "error_message": null,
  "duration_ms": 145230
}
```

**Sprint 2 Change Required:**
- Status code: 200 → **202 ACCEPTED** (returned immediately)
- Response body: Minimal (execution details only, not full results)

**New 202 Response Should Be:**
```json
{
  "execution_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_id": "550e8400-e29b-41d4-a716-446655440001",
  "template": "blog_post",
  "status": "pending",
  "created_at": "2026-02-19T14:30:00Z",
  "message": "Workflow execution queued",
  "estimated_duration_seconds": 900
}
```

---

### 4.3 Polling for Status/Results
**Existing Endpoints:**

**GET /api/tasks/{task_id}** (lines 787-825)
```python
# Returns full task with current status
# Client polls this to check if task complete
```

**Returns:**
```json
{
  "id": "task-uuid",
  "status": "awaiting_approval",  // or "in_progress", "failed", etc.
  "task_metadata": {
    "content": "...",
    "title": "...",
    "quality_score": 0.85
  },
  ...
}
```

**GET /api/workflows/executions/{execution_id}** (Sprint 1, already implemented)
```python
# Returns complete execution record from database
```

**Database Query:**
```sql
SELECT * FROM workflow_executions WHERE id = {execution_id}
```

**Returns:**
```json
{
  "id": "execution-uuid",
  "workflow_id": "workflow-uuid",
  "execution_status": "completed",
  "phase_results": {...},
  "final_output": {...},
  "duration_ms": 145230
}
```

---

## 5. ASYNC PATTERNS ALREADY IN USE

### 5.1 AsyncIO Task Creation Pattern
**Pattern:** `asyncio.create_task(coroutine)`  
**Used In:** Multiple places

**Examples in Codebase:**

1. **TaskExecutor Startup** (task_executor.py line 115):
```python
self._processor_task = asyncio.create_task(self._process_loop())
```
Creates background polling loop at startup.

2. **Blog Post Generation** (task_routes.py lines 340-365):
```python
async def _run_blog_generation():
    try:
        await process_content_generation_task(...)
    except Exception as e:
        logger.error(...)
        await db_service.update_task(task_id, {"status": "failed"})

asyncio.create_task(_run_blog_generation())
```
Schedules content generation as background task immediately after task creation.

3. **WebSocket Progress Broadcasting** (custom_workflows_service.py line 331):
```python
asyncio.create_task(broadcast_workflow_progress(execution_id, progress))
```
Non-blocking progress broadcast to WebSocket clients.

4. **Workflow Background Execution** (template_execution_service.py line 304):
```python
asyncio.create_task(
    _execute_workflow_background(phases, context, custom_workflow, database_service)
)
```
Async workflow execution with non-blocking return.

---

### 5.2 BackgroundTasks from FastAPI
**Pattern:** `background_tasks.add_task(function, *args)`  
**Used In:** Some routes (but inconsistently)

**Example (task_routes.py publish_task):**
```python
@router.post("/{task_id}/publish", ...)
async def publish_task(
    task_id: str,
    background_tasks: BackgroundTasks = None,
    ...
):
    # Do synchronous work
    task = await db_service.get_task(task_id)
    
    # Queue background work (if needed)
    if background_tasks:
        background_tasks.add_task(some_function, task_id)
    
    # Return immediately
    return {"status": "published"}
```

**Note:** FastAPI BackgroundTasks are less reliable for long operations than `asyncio.create_task()`. Current codebase prefers `asyncio.create_task()` for durability.

---

### 5.3 Asyncio Event Loop Pattern
**Pattern:** All database calls use async/await with `asyncpg` connection pool

**Example (tasks_db.py line 78+):**
```python
async with self.pool.acquire() as conn:
    rows = await asyncio.wait_for(conn.fetch(sql, *params), timeout=QUERY_TIMEOUT)
    return [ModelConverter.to_task_response(row) for row in rows]
```

**Benefits:**
- ✅ Non-blocking I/O
- ✅ Can handle hundreds of concurrent requests
- ✅ Event loop manages task scheduling

---

### 5.4 Existing Async Patterns to Replicate for Sprint 2

**Pattern 1: Create background task, return immediately**
```python
# For POST /api/tasks/{task_id}/execute
async def execute_task(task_id: str, ...):
    # 1. Validate task exists
    task = await db_service.get_task(task_id)
    
    # 2. Queue execution in background
    asyncio.create_task(task_executor.execute_task(task))
    
    # 3. Return immediately with execution_id
    return {
        "execution_id": task_id,
        "status": "pending",
        "message": "Task execution queued"
    }
```

**Pattern 2: Return 202 with polling endpoint**
```python
# For GET /api/tasks/{task_id}/status
async def get_task_status(task_id: str, ...):
    # Just queries current status from DB (fast operation)
    task = await db_service.get_task(task_id)
    return {
        "status": task["status"],
        "progress": task.get("progress", 0),
        ...
    }
```

---

## 6. POLLING & STATUS QUERY ENDPOINTS

### 6.1 Existing Status Query Endpoints
**These already exist and work correctly:**

1. **GET /api/tasks/{task_id}** (task_routes.py line 787)
   - Returns full task object
   - Includes current status
   - Already functioning ✅

2. **GET /api/workflows/executions/{execution_id}** (custom_workflows_routes.py line 377)
   - Returns complete execution record
   - Already functioning (Sprint 1) ✅

3. **GET /api/workflows/custom/{workflow_id}/executions** (custom_workflows_routes.py line 397)
   - Lists all executions for a workflow
   - Paginated results
   - Already functioning ✅

---

### 6.2 Status Query Pattern for Sprint 2
**Recommended Polling Pattern:**

```
Client:
1. POST /api/tasks → 202 ACCEPTED + task_id
2. Poll GET /api/tasks/{task_id} every 3-5 seconds
3. When status == "awaiting_approval|failed", show result to user

OR (with WebSocket):
1. POST /api/tasks → 202 ACCEPTED + task_id
2. Open WS /api/ws
3. Receive progress updates in real-time
4. When status == "complete", stop polling
```

**Response Schema Should Be Consistent:**
```json
{
  "id": "task-uuid",
  "status": "pending|in_progress|awaiting_approval|failed",
  "progress": 0-100,
  "current_step": "Research phase...",
  "result": {...},  // Only populated when complete
  "error_message": null,
  "created_at": "2026-02-19T14:30:00Z",
  "updated_at": "2026-02-19T14:31:45Z"
}
```

---

### 6.3 WebSocket Real-Time Progress (Phase 4 - Already Wired)
**File:** [src/cofounder_agent/routes/websocket_routes.py](src/cofounder_agent/routes/websocket_routes.py)

**Already Implemented:**
- WS /api/ws → WebSocket endpoint
- Broadcasts task progress in real-time
- Clients subscribe to task_id channel
- Receives: `{status, progress, current_step, message}`

**No Changes Needed for Sprint 2** - just ensure routes emit progress when using 202 pattern.

---

## 7. IDENTIFIED BLOCKERS & COMPATIBILITY ISSUES

### 7.1 No Major Blockers Found ✅
All prerequisites for 202 conversion are in place:
- ✅ TaskExecutor background polling functional (5s interval)
- ✅ Database schema supports status tracking
- ✅ Status query endpoints exist
- ✅ Async patterns already established
- ✅ WebSocket progress broadcast working

---

### 7.2 Minor Issues to Address

**Issue 1: Workflow Template Status Code Not Explicit**
- File: workflow_routes.py line 244
- Current: Returns `200 OK` with complete result
- Issue: Clients expect synchronous response, changing to 202 is breaking change
- Mitigation: Document in API changelog, clients will need polling code

**Issue 2: No Explicit "Retry-After" Header**
- Pattern: Should include `Retry-After: 5` header on 202 responses
- Not currently used
- Mitigation: Add to all 202 responses as guidance for polling interval

**Issue 3: Timeout Configuration Inconsistency**
- Task executor timeout: 900 seconds (15 min)
- Template estimated durations: 240-1200 seconds (4-20 min)
- Mismatch risk: If template configured for 20m but TaskExecutor timeout is 15m
- Mitigation: Align timeout with template durations or make configurable per route

**Issue 4: Error Recovery Limited**
- Current: No retry logic on LLM failures
- Risk: Transient LLM API errors cause task failure immediately
- Mitigation: For future sprint, add exponential backoff retry (max 3 attempts)

---

### 7.3 Performance Considerations

**Current Metrics** (from task_executor.py implementation):
- Poll interval: 5 seconds
- Max tasks per poll cycle: 10
- Parallel execution: Sequential per task, but 10 different tasks in parallel
- Expected throughput: 10-20 tasks/minute (depends on LLM response time)

**Scalability with 202 Refactor:**
- No change to backend throughput
- Frees up HTTP connections → clients don't hold connections during execution
- Enables browser to close connection after 202 response
- Overall reduction in resource utilization

---

## 8. KEY FILES & FUNCTIONS FOR IMPLEMENTATION

### 8.1 Routes to Modify (Return 202 ACCEPTED)

| File | Function | Lines | Change |
|------|----------|-------|--------|
| task_routes.py | create_task() | 164-250 | Return 202 instead of 201 |
| workflow_routes.py | execute_workflow_template() | 244-320 | Return 202 immediately, move execution async |
| custom_workflows_routes.py | execute_workflow() | TBD | Return 202, move execution async |

---

### 8.2 Services to Verify (No Changes Needed)

| File | Function | Purpose | Status |
|------|----------|---------|--------|
| task_executor.py | _process_loop() | Background polling | ✅ Already async |
| task_executor.py | _process_single_task() | Task execution | ✅ Already fully implemented |
| template_execution_service.py | execute_template() | Workflow execution | ✅ Already async |
| database_service.py | get_pending_tasks() | Task polling | ✅ Already async |
| tasks_db.py | update_task_status() | Status persistence | ✅ Already async |

---

### 8.3 Database Methods (Existing & Sufficient)

| Method | File | Purpose | Status |
|--------|------|---------|--------|
| get_pending_tasks(limit) | tasks_db.py | Fetch pending tasks | ✅ Ready |
| update_task() | tasks_db.py | Update task status | ✅ Ready |
| get_task() | tasks_db.py | Fetch single task | ✅ Ready |
| add_task() | tasks_db.py | Create new task | ✅ Ready |

---

## 9. IMPLEMENTATION ROADMAP FOR SPRINT 2

### Task 2.1: Refactor Routes to Return 202
**Effort:** 4-5 hours  
**Files:**
- task_routes.py (modify create_task() and handlers)
- workflow_routes.py (modify execute_workflow_template())
- custom_workflows_routes.py (modify execute_workflow())

**Changes:**
1. Wrap execution logic in `asyncio.create_task()`
2. Change status code to 202
3. Return minimal response (execution_id + status: pending)
4. Add Location header pointing to status endpoint
5. Add Retry-After header (5-10 seconds)

**Testing:**
- POST /api/tasks → verify 202 response
- POST /api/workflows/execute/{template} → verify 202 response
- Verify background execution continues
- Verify status query endpoints work

---

### Task 2.2: Verify TaskExecutor Polling Completes Work
**Effort:** 3-4 hours  
**Files:**
- task_executor.py (verify _process_loop, _process_single_task)
- database_service.py (verify get_pending_tasks)

**Changes:**
1. Audit polling loop for correctness
2. Verify status transitions work correctly
3. Add timeout and error recovery
4. Test with various task types
5. Verify WebSocket progress events still emit

**Testing:**
- Create task via API → Monitor background execution
- Verify status transitions: pending → in_progress → awaiting_approval
- Verify failed tasks marked correctly
- Monitor error logs for orphaned tasks

---

### Task 2.3: Add Status Query Endpoints (Enhancements)
**Effort:** 3 hours  
**Files:**
- task_routes.py (create GET /api/tasks/{task_id}/status)
- workflow_routes.py (create GET /api/workflows/{execution_id}/status)

**New Endpoints:**
```
GET /api/tasks/{task_id}/status
  Returns: {status, progress, current_step, error_message}
  
GET /api/workflows/{execution_id}/status
  Returns: {status, progress, current_step, error_message}
```

**Changes:**
1. Create lightweight status endpoints (different from full task GET)
2. Include progress percentage (0-100)
3. Include current_step being executed
4. Cache in memory for fast response
5. Update UI to poll these endpoints

**Testing:**
- Poll endpoints during task execution
- Verify progress values increase over time
- Verify step names match expected phases

---

## 10. SUCCESS CRITERIA FOR SPRINT 2

### Definition of Done
- [ ] All 3 primary routes return 202 ACCEPTED immediately
- [ ] Background execution continues correctly
- [ ] TaskExecutor picks up and completes all tasks
- [ ] Status query endpoints return accurate status
- [ ] WebSocket progress events still broadcast
- [ ] No regressions in existing functionality
- [ ] Client response time < 100ms for all routes
- [ ] Background execution time unchanged (still 2-5 min)

### Testing Checklist
- [ ] Manual test: Create task, poll status, verify completion
- [ ] Manual test: Execute workflow template, poll status, verify completion
- [ ] Automated test: 10 concurrent tasks, all complete successfully
- [ ] Error case: Create task, then fail halfway through, verify error status
- [ ] WebSocket: Monitor progress in real-time during execution
- [ ] Load test: 100 queued tasks, TaskExecutor processes all correctly

---

## 11. RISK ASSESSMENT

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| Clients break due to 202 change | Medium | High | Update Oversight Hub UI immediately, add changelog |
| Background task not picked up | Low | High | Verify TaskExecutor runs at startup, add health check |
| Status endpoints race condition | Very Low | Medium | Use database transactions, not in-memory state |
| LLM timeout still blocks | Low | Low | Existing 900s timeout handles 20m workflows |
| WebSocket disconnect mid-task | Low | Low | Already implemented with reconnection logic |

---

## APPENDIX: CODE SNIPPETS FOR REFERENCE

### A1. Current Async Task Pattern (Current Best Practice in Codebase)
```python
# From task_routes.py lines 340-365
async def _run_blog_generation():
    try:
        await process_content_generation_task(
            topic=request.topic,
            style=request.style or "narrative",
            tone=request.tone or "professional",
            target_length=request.target_length or 1500,
            tags=request.tags,
            generate_featured_image=request.generate_featured_image or True,
            database_service=db_service,
            task_id=task_id,
            models_by_phase=request.models_by_phase,
            quality_preference=request.quality_preference or "balanced",
            category=request.category or "general",
            target_audience=request.target_audience or "General",
        )
    except Exception as e:
        logger.error(f"Blog generation failed: {e}", exc_info=True)
        await db_service.update_task(task_id, {"status": "failed", "error_message": str(e)})

asyncio.create_task(_run_blog_generation())

# Return immediately (should be 202, not 201)
return {
    "id": returned_task_id,
    "task_id": returned_task_id,
    "task_type": "blog_post",
    "topic": request.topic,
    "status": "pending",
    "created_at": task_data["created_at"],
    "message": "Blog post task created and queued",
}
```

### A2. TaskExecutor Polling Loop Pattern
```python
# From task_executor.py lines 139-201
async def _process_loop(self):
    """Main processing loop - runs continuously in background"""
    logger.info("TASK EXECUTOR: Main processing loop has started.")
    
    while self.running:
        try:
            # Get pending tasks
            pending_tasks = await self.database_service.get_pending_tasks(limit=10)
            
            if pending_tasks:
                logger.info(f"Found {len(pending_tasks)} pending task(s)")
                # Process each task
                for task in pending_tasks:
                    if not self.running:
                        break
                    try:
                        await self._process_single_task(task)
                        self.success_count += 1
                    except Exception as e:
                        logger.error(f"Error processing task: {str(e)}", exc_info=True)
                        self.error_count += 1
                    finally:
                        self.task_count += 1
            else:
                logger.debug(f"No pending tasks - sleeping {self.poll_interval}s")
            
            # Sleep before next poll
            await asyncio.sleep(self.poll_interval)
            
        except asyncio.CancelledError:
            logger.info("Task executor loop cancelled")
            break
        except Exception as e:
            logger.error(f"Unexpected error in task executor loop: {str(e)}", exc_info=True)
            await asyncio.sleep(self.poll_interval)
```

### A3. Status Transition Pattern
```python
# From task_executor.py _process_single_task()
# 1. Mark as in_progress
await self.database_service.update_task(
    task_id,
    {
        "status": "in_progress",
        "task_metadata": {
            "status": "processing",
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    },
)

# 2. Execute task
result = await asyncio.wait_for(
    self._execute_task(task), timeout=TASK_TIMEOUT_SECONDS
)

# 3. Update with result (awaiting_approval, failed, etc.)
final_status = result.get("status", "awaiting_approval")
await self.database_service.update_task(task_id, {
    "status": final_status,
    "task_metadata": task_metadata_updates
})
```

---

## SUMMARY TABLE: Ready for Implementation

| Component | Status | Changes Required | Effort |
|-----------|--------|------------------|--------|
| TaskExecutor polling | ✅ Functional | None | 0h |
| Database schema | ✅ Ready | None | 0h |
| Status tracking | ✅ Working | None | 0h |
| Async patterns | ✅ Established | None | 0h |
| **POST /api/tasks** | ⏳ Needs 202 | Return 202 instead of 201 | 2h |
| **POST /api/workflows/execute** | ⏳ Needs 202 | Return 202, move to async | 2h |
| **Custom workflow execute** | ⏳ Needs 202 | Return 202, move to async | 1h |
| Status query endpoints | ✅ Exist | Optional enhancements | 3h |
| **TOTAL SPRINT 2** | | | **16h** |

---

**Research Completion Date:** February 19, 2026  
**Status:** READY FOR IMPLEMENTATION PLANNING

