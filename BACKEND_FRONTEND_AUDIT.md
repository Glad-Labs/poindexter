# Backend-Frontend Audit: Disconnects & Implementation Gaps

**Last Updated:** December 2025  
**Status:** 🔴 CRITICAL GAPS IDENTIFIED  
**Severity:** High - UI expects endpoints and data structures that partially exist or are stubbed

---

## Executive Summary

The Glad Labs system has significant disconnects between what the **React Oversight Hub** expects and what the **FastAPI backend** actually provides. This document catalogs all issues found during a comprehensive audit of both systems.

### Key Findings

- ✅ **Task Management endpoints** exist but have incomplete implementations
- ❌ **Analytics/KPI endpoints** are completely missing
- ❌ **Workflow history endpoint** exists but frontend calls wrong endpoint path
- ⚠️ **Model selection** endpoints exist but return mock/stubbed data in places
- ❌ **Image generation endpoint** exists but may not work end-to-end
- ⚠️ **Data consistency issues** between multiple endpoints returning similar data

---

## 1. CRITICAL GAPS - Missing Endpoints

### 1.1 ❌ Analytics/KPI Dashboard - MISSING COMPLETELY

**Frontend Expectation:**

```javascript
// web/oversight-hub/src/components/pages/ExecutiveDashboard.jsx:36
fetch(`http://localhost:8000/api/analytics/kpis?range=${timeRange}`);
```

**Backend Status:** ⛔ **NOT IMPLEMENTED**

**Issues:**

- Frontend calls `/api/analytics/kpis` endpoint
- Backend has no `/api/analytics` router at all
- No `analytics_routes.py` file exists
- ExecutiveDashboard will fail with 404 when trying to load KPIs

**Impact:** Dashboard cannot display any metrics, KPIs, or analytics

**What's needed:**

```python
# Create: src/cofounder_agent/routes/analytics_routes.py
@router.get("/kpis")
async def get_kpis(range: str = "7d"):
    """Return KPI data for dashboard: task completion rate, avg cost, etc."""
    # Should aggregate from tasks table and return:
    # - total_tasks_created
    # - completed_tasks
    # - failed_tasks
    # - avg_completion_time
    # - total_cost
    # - avg_cost_per_task
```

**Workaround:** Frontend can use existing `/api/tasks?limit=100` endpoint to calculate KPIs locally

---

### 1.2 ❌ Workflow History - Wrong Endpoint Path

**Frontend Call:**

```javascript
// web/oversight-hub/src/components/pages/ExecutionHub.jsx:43
fetch('http://localhost:8000/api/workflow/history', {
```

**Backend Endpoint:**

```python
# src/cofounder_agent/routes/workflow_history.py:64
@router.get("/history", response_model=WorkflowHistoryResponse)
# Router prefix: "/api/workflow-history" (with hyphen)
```

**Issue:**

- Frontend expects: `/api/workflow/history` (with slash)
- Backend provides: `/api/workflow-history/history` (with hyphen and duplicate)

**Impact:** ExecutionHub cannot load workflow history

**Fix Options:**

1. Update frontend to use `/api/workflow-history/history`
2. Update backend router prefix from `/api/workflow-history` to `/api/workflow`
3. Add route alias for backward compatibility

---

## 2. PARTIAL IMPLEMENTATIONS - Endpoints Exist But Incomplete

### 2.1 ⚠️ Task Status Endpoint - Inconsistent Data Structure

**Frontend Calls:**

```javascript
// TaskManagement.jsx:92 - Fetch full task with content
fetch(`http://localhost:8000/api/content/tasks/${taskId}`);

// TaskManagement.jsx:170 - List tasks
fetch(`http://localhost:8000/api/tasks?limit=100&offset=0`);
```

**Backend Provides:**

```python
# /api/content/tasks/{task_id} - content_routes.py
# Returns TaskStatusResponse with nested 'result' object

# /api/tasks - task_routes.py
# Returns TaskListResponse with 'tasks' array
```

**Issues:**

- Two different endpoints return different structures
- Frontend has to handle both patterns in different components
- Content fields nested inconsistently (sometimes in `result`, sometimes top-level)
- No clear contract between endpoints

**Example Mismatch:**

```javascript
// Frontend expects from /api/content/tasks/{id}:
{
  task_id: "uuid",
  status: "completed",
  result: {
    content: "...",
    title: "...",
    featured_image_url: "..."
  }
}

// Frontend expects from /api/tasks:
{
  tasks: [
    {
      id: "uuid",
      status: "completed",
      content: "...", // Top-level, not in result!
      excerpt: "...",
      task_metadata: { ... }
    }
  ]
}
```

**Recommendation:** Unify both endpoints to return same structure or clearly document which endpoint to use for which scenario

---

### 2.2 ⚠️ Task Creation - Model Selection Parameters Not Integrated

**Frontend Sends:**

```javascript
// CreateTaskModal.jsx
{
  task_type: "blog_post",
  topic: "...",
  models_by_phase: {
    research: "llama2",
    outline: "claude",
    draft: "gpt4",
    assess: "claude",
    refine: "gpt4",
    finalize: "claude"
  },
  quality_preference: "balanced"
}
```

**Backend Expects:**

```python
# CreateBlogPostRequest schema
{
  topic: str,
  style: ContentStyle,
  tone: ContentTone,
  target_length: int,
  generate_featured_image: bool,
  # ✅ These fields exist:
  models_by_phase: Optional[Dict[str, str]],
  quality_preference: Optional[str]
}
```

**Issues:**

- Backend schema accepts these fields ✅
- But task execution in `process_content_generation_task` may not actually use them ❓
- No validation that specified models are available
- Cost estimation is done but unclear if used downstream

**Missing:**

- Validation that specified models exist
- Task execution actually routing to specified models
- Clear error if model selection fails

---

### 2.3 ⚠️ Media/Image Generation - Endpoint Exists But Functionality Unclear

**Frontend Calls:**

```javascript
// ResultPreviewPanel.jsx:205
fetch('http://localhost:8000/api/media/generate-image', {
  method: 'POST',
  body: JSON.stringify({
    prompt: '...',
    style: 'professional',
    format: 'landscape',
  }),
});

// CreateTaskModal.jsx:281
fetch('http://localhost:8000/api/media/generate-image');
```

**Backend Status:**

```python
# src/cofounder_agent/routes/media_routes.py exists
# Handlers for:
# - Pexels image search (FREE)
# - SDXL image generation (if GPU available)
# - Cloudinary upload (if configured)
```

**Issues:**

- Endpoint exists but implementation details unknown
- Frontend doesn't check if endpoint is available before calling
- Error handling for failures not clear
- Response format might not match what frontend expects

**What Works:**

- ✅ Pexels search endpoint (free, most reliable)
- ⚠️ SDXL local generation (requires GPU, may timeout)
- ⚠️ Cloudinary upload (requires env vars)

---

## 3. MOCKED/STUBBED FUNCTIONALITY

### 3.1 🎭 Model Selection Panel - Mocked Ollama Data

**File:** `web/oversight-hub/src/components/ModelSelectionPanel.jsx`

**Issue:**

```javascript
const fetchAvailableModels = async () => {
  try {
    // Tries to fetch real Ollama models
    const ollamaResponse = await fetch('http://localhost:11434/api/tags');
    // ...
  } catch (err) {
    // FALLBACK: Returns hardcoded/mocked data
    setPhaseModels(getDefaultPhaseModels());
  }
};

const getDefaultPhaseModels = () => {
  // Hardcoded model list that may not match actual Ollama instance
  return {
    research: 'llama2:13b',
    outline: 'mistral',
    draft: 'neural-chat',
    // ... etc
  };
};
```

**Problems:**

- If Ollama is down, silently falls back to hardcoded models
- Frontend doesn't know if actual vs mocked data
- User might select model that doesn't exist locally
- No error indicator shown to user

**Fix:**

```javascript
// Should show warning if using fallback
if (usingFallbackModels) {
  console.warn('⚠️ Using default models - Ollama not responding');
  // Show banner to user
}
```

---

### 3.2 🎭 Model Selection - Cost Calculations Are Mocked

**File:** `web/oversight-hub/src/components/ModelSelectionPanel.jsx:334-360`

```javascript
const getModelCost = (modelId) => {
  // Hardcoded cost estimates per phase
  const costs = {
    'llama2': { research: 0.0001, outline: 0.0001, ... },
    'mistral': { research: 0.0002, outline: 0.0002, ... },
    // etc
  }
  return costs[modelId]?.[phase] || 0.0001
}
```

**Issue:**

- Costs are completely hardcoded in frontend
- No communication with backend for actual costs
- Backend might estimate different costs
- Display might not match actual charges

---

### 3.3 🎭 LangGraph Blog Creation - WebSocket Mock

**File:** `src/cofounder_agent/routes/content_routes.py:1042+`

```python
@content_router.websocket("/langgraph/ws/blog-posts/{request_id}")
async def websocket_blog_creation(websocket: WebSocket, request_id: str):
    # ...
    phases = [
        {"node": "research", "progress": 15},
        {"node": "outline", "progress": 30},
        # ...
    ]

    for phase in phases:
        await websocket.send_json({...})
        await asyncio.sleep(1)  # ← HARDCODED 1-SECOND DELAYS!
```

**Issues:**

- Progress percentages are hardcoded (15%, 30%, 50%, 70%, 100%)
- Sleep times are hardcoded (1 second per phase = 5 seconds total)
- Doesn't actually track real pipeline progress
- Frontend gets false progress information

**Comment in code:**

```python
# Mock streaming (in production, fetch from database and stream)
# For now, simulate phases
```

**This is a TODO that hasn't been completed.**

---

## 4. INCOMPLETE IMPLEMENTATIONS - Methods Exist But Don't Fully Work

### 4.1 ⚠️ Task Approval/Publishing Pipeline

**What Exists:**

- ✅ `/api/content/tasks/{task_id}/approve` endpoint
- ✅ Approval status tracking
- ✅ Database updates

**What's Incomplete:**

- ❓ Frontend submits approval request
- ❓ Content actually publishes to external CMS
- ❓ Featured image metadata handling
- ❓ Category/tag matching logic

**Endpoint Behavior (from code review):**

```python
@content_router.post("/tasks/{task_id}/approve")
async def approve_and_publish_task(task_id: str, request: ApprovalRequest):
    # 1. Get task from database ✅
    # 2. Check approval status ✅
    # 3. If approved:
    #    - Mark as 'approved' in DB ✅
    #    - Generate metadata using UnifiedMetadataService ✅
    #    - Create post in CMS database ✅
    #    - Return post_id and URL ✅
    # 4. If rejected:
    #    - Mark as 'rejected' in DB ✅
    #    - Store feedback ✅
```

**However:**

- External CMS integration (Strapi) is commented/incomplete
- Featured image handling has multiple fallback paths (confusing)
- Category/tag matching logic is mentioned but not shown
- Error handling for CMS publishing failure could be better

---

### 4.2 ⚠️ Bulk Task Operations

**File:** `web/oversight-hub/src/components/tasks/TaskManagement.jsx:1070+`

```javascript
// Calls bulk operation endpoint
fetch(`http://localhost:8000/api/tasks/${task.id}`, {
  method: 'PATCH',
  body: JSON.stringify({ status: 'running' }),
});
```

**Backend Endpoint:**

```python
# src/cofounder_agent/routes/bulk_task_routes.py
@router.post("/bulk", response_model=BulkTaskResponse)
async def bulk_task_operations(request: BulkTaskRequest):
```

**Mismatch:**

- Frontend calls single task PATCH `/api/tasks/{id}`
- Backend has bulk POST `/api/tasks/bulk`
- Unclear which operation the frontend is trying to do

---

## 5. DATA CONSISTENCY ISSUES

### 5.1 Task Fields - Multiple Representations

**Problem:** Same data represented differently across endpoints

```javascript
// From /api/tasks:
{
  id: "uuid",
  task_name: "...",
  content: "...",
  task_metadata: { content: "...", ... } // DUPLICATE!
}

// From /api/content/tasks/{id}:
{
  task_id: "uuid",
  status: "...",
  result: {
    title: "...",
    content: "...",
    task_metadata: { ... }
  }
}

// From /api/tasks/{id}:
{
  id: "uuid",
  task_name: "...",
  task_metadata: { content: "..." }
  // No 'content' at top level!
}
```

**Issues:**

- Frontend doesn't know which field to use for content
- Code duplication in task_routes.py to merge normalized fields into task_metadata
- Comment in code: `# IMPORTANT: Merge normalized columns back into task_metadata for UI compatibility`
- This is a band-aid, not a real solution

---

### 5.2 Task Status Values - Inconsistent Naming

**From content_routes.py:**

```python
status values: "pending", "generating", "completed", "failed", "awaiting_approval", "approved", "rejected", "published"
```

**From task_routes.py:**

```python
status values: "pending", "running", "completed", "failed"
```

**Frontend expects:** Various statuses in different components

**Problem:** No central enum for valid statuses

---

## 6. AUTHENTICATION & AUTHORIZATION GAPS

### 6.1 ❌ Auth Token Handling Inconsistent

**Issue:**

- Some endpoints require auth token (content_routes)
- Others accept but don't require it (task_routes)
- Frontend sometimes passes token, sometimes doesn't
- No clear indication which endpoints require auth

**Example:**

```python
# content_routes.py - Gets token but may not require it
token = getAuthToken();
const headers = { 'Content-Type': 'application/json' };
if (token) {
  headers['Authorization'] = `Bearer ${token}`;
}

# Backend - Some routes have @Depends(get_current_user), some don't
```

---

## 7. SUMMARY TABLE: Status of All API Integrations

| Endpoint                                       | Backend Status              | Frontend Use         | Issues                                                |
| ---------------------------------------------- | --------------------------- | -------------------- | ----------------------------------------------------- |
| `POST /api/tasks`                              | ✅ Implemented              | ✅ Used              | None known                                            |
| `GET /api/tasks`                               | ✅ Implemented              | ✅ Used              | Returns different structure than `/api/content/tasks` |
| `GET /api/tasks/{id}`                          | ✅ Implemented              | ✅ Used              | Data structure differs from content endpoint          |
| `PATCH /api/tasks/{id}`                        | ✅ Implemented              | ✅ Used              | Unclear what bulk operation is for                    |
| `GET /api/analytics/kpis`                      | ❌ MISSING                  | ❌ Dashboard expects | **CRITICAL**                                          |
| `GET /api/workflow/history`                    | ✅ Available but wrong path | ❌ Broken            | ExecutionHub gets 404                                 |
| `POST /api/content/tasks`                      | ✅ Implemented              | ✅ Used              | Model selection params may not be used                |
| `GET /api/content/tasks/{id}`                  | ✅ Implemented              | ✅ Used              | Result structure nests data confusingly               |
| `GET /api/content/tasks`                       | ✅ Implemented              | ⚠️ Not used          | Lists tasks differently than `/api/tasks`             |
| `POST /api/content/tasks/{id}/approve`         | ✅ Implemented              | ✅ Used              | External CMS publishing incomplete                    |
| `DELETE /api/content/tasks/{id}`               | ✅ Implemented              | ✅ Used              | Works but no confirmation in UI                       |
| `POST /api/media/generate-image`               | ✅ Implemented              | ✅ Used              | Reliability unclear, no fallback shown                |
| `GET /api/ollama/models`                       | ✅ Implemented              | ✅ Used              | Frontend has hardcoded fallback                       |
| `POST /api/content/langgraph/blog-posts`       | ⚠️ Stub                     | ⚠️ Experimental      | WebSocket sends mock progress                         |
| `WS /api/content/langgraph/ws/blog-posts/{id}` | 🎭 Mocked                   | ⚠️ Experimental      | Hardcoded progress, not real                          |

---

## 8. RECOMMENDED FIXES (Priority Order)

### 🔴 CRITICAL - Fix Immediately

1. **Create `/api/analytics/kpis` endpoint**
   - Status: Missing entirely
   - Impact: Dashboard non-functional
   - Effort: 2-3 hours

2. **Fix `/api/workflow/history` endpoint path**
   - Status: Wrong prefix
   - Impact: ExecutionHub broken
   - Effort: 15 minutes

### 🟠 HIGH - Fix This Week

3. **Unify task response structures**
   - Choose one canonical structure for all task endpoints
   - Remove duplication in task_metadata
   - Update all endpoints to return same format
   - Effort: 4-6 hours

4. **Complete LangGraph WebSocket streaming**
   - Replace mock progress with real task progress
   - Query database instead of returning hardcoded values
   - Effort: 3-4 hours

5. **Fix model selection validation**
   - Validate selected models exist before task creation
   - Return clear error if model unavailable
   - Effort: 2 hours

### 🟡 MEDIUM - Fix This Sprint

6. **Standardize task status values**
   - Create enum with all possible statuses
   - Use across all endpoints
   - Document in API schema
   - Effort: 1-2 hours

7. **Complete external CMS integration**
   - Finish Strapi or other external CMS publishing
   - Handle featured image metadata properly
   - Effort: 4-6 hours

8. **Improve image generation reliability**
   - Add fallbacks and error handling
   - Show status to user
   - Effort: 2-3 hours

### 🟢 LOW - Nice to Have

9. Document which endpoints require authentication
10. Add endpoint deprecation warnings
11. Create API version strategy

---

## 9. Test Cases to Validate Fixes

```javascript
// Test 1: Analytics KPI endpoint
GET /api/analytics/kpis?range=7d
// Should return: total_tasks, completed_tasks, failed_tasks, avg_cost, etc.

// Test 2: Workflow history
GET /api/workflow-history/history
// Should return: list of workflow executions with timeline

// Test 3: Task unified response
GET /api/tasks?limit=1
GET /api/content/tasks?limit=1
// Both should return same structure for task content

// Test 4: LangGraph real progress
POST /api/content/langgraph/blog-posts
WS /api/content/langgraph/ws/blog-posts/{request_id}
// Should show real progress, not mocked values

// Test 5: Model validation
POST /api/content/tasks {
  "models_by_phase": {
    "research": "invalid_model"
  }
}
// Should reject with 400 Bad Request
```

---

## 10. Reference Map: What Calls What

### Frontend → Backend Calls

```
ExecutiveDashboard.jsx
  → GET /api/analytics/kpis ❌ MISSING
  → GET /api/tasks ✅ Works

TaskManagement.jsx
  → GET /api/content/tasks/{id} ✅ Works
  → GET /api/tasks ✅ Works (duplicate endpoint!)
  → DELETE /api/content/tasks/{id} ✅ Works
  → PATCH /api/tasks/{id} ✅ Works
  → POST /api/content/tasks/{id}/approve ✅ Works

ResultPreviewPanel.jsx
  → POST /api/media/generate-image ✅ Works
  → POST /api/content/tasks/{id}/approve ✅ Works

CreateTaskModal.jsx
  → POST /api/content/tasks ✅ Works
  → POST /api/media/generate-image ✅ Works
  → GET /api/ollama/models ✅ Works

ExecutionHub.jsx
  → GET /api/workflow/history ❌ Wrong path (should be /api/workflow-history/history)

LangGraphTest.jsx (experimental)
  → POST /api/content/langgraph/blog-posts ⚠️ Stub
  → WS /api/content/langgraph/ws/blog-posts/ ⚠️ Mocked
```

---

## Conclusion

The Glad Labs system is **functionally working** for basic task creation and management, but has several **critical gaps**:

1. **Missing endpoints** prevent dashboard from displaying metrics
2. **Inconsistent data structures** between endpoints create confusion
3. **Mocked functionality** gives false sense of working features
4. **Poor error handling** makes failures silent

The fixes above, when implemented, will create a **unified, predictable, well-tested API** that the frontend can reliably build on.

---

**Next Step:** Pick one critical issue and create a detailed implementation plan.
