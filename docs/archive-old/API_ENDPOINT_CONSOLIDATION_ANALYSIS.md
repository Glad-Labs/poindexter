# API Endpoint Consolidation Analysis

**Date:** January 15, 2026  
**Goal:** Identify and consolidate duplicate task creation endpoints

---

## Current Endpoint Landscape

### Task Creation Endpoints (PRIMARY DUPLICATION)

```
POST /api/tasks                    (task_routes.py)
  - Prefix: /api/tasks
  - Function: create_task()
  - Input: TaskCreateRequest
  - Fields: task_name, topic, primary_keyword, target_audience, category, metadata
  - Purpose: Generic task creation

POST /api/content/tasks            (content_routes.py)
  - Prefix: /api/content/tasks
  - Function: create_content_task()
  - Input: CreateBlogPostRequest
  - Fields: task_type, topic, style, tone, target_length, tags, generate_featured_image, models_by_phase
  - Purpose: Content-specific generation (blog, social, email, newsletter)
```

### Task Status/Retrieval Endpoints

```
GET  /api/tasks                    (task_routes.py) - List all tasks
GET  /api/tasks/{task_id}          (task_routes.py) - Get task by ID
GET  /api/tasks/{task_id}/status   (task_routes.py) - Get task status

GET  /api/content/tasks            (content_routes.py) - List content tasks
GET  /api/content/tasks/{task_id}  (content_routes.py) - Get content task
```

### Task Actions

```
POST /api/tasks/{task_id}/approve  (task_routes.py) - Approve for publishing
POST /api/tasks/{task_id}/publish  (task_routes.py) - Publish task
POST /api/tasks/{task_id}/reject   (task_routes.py) - Reject task

POST /api/tasks/bulk               (bulk_task_routes.py) - Bulk operations (cancel, pause, resume)
POST /api/tasks/intent             (task_routes.py) - Parse natural language intent
POST /api/tasks/confirm-intent     (task_routes.py) - Confirm parsed intent
```

### Subtask Execution (Bypass Orchestrator)

```
POST /api/subtasks/research        (subtask_routes.py) - Run research phase directly
POST /api/subtasks/creative        (subtask_routes.py) - Run creative phase directly
POST /api/subtasks/qa              (subtask_routes.py) - Run QA phase directly
POST /api/subtasks/images          (subtask_routes.py) - Run image search directly
POST /api/subtasks/format          (subtask_routes.py) - Run formatting directly
```

### Cost Estimation

```
POST /api/models/estimate-full-task (model_selection_routes.py)
  - Estimates cost for a full task
```

---

## The Duplication Problem

### Issue: Two ways to create the same thing

**POST /api/tasks** (Generic, task-focused):

```json
{
  "task_name": "Blog Post - AI Trends",
  "topic": "AI Trends 2025",
  "category": "technology"
}
```

**POST /api/content/tasks** (Content-focused, richer):

```json
{
  "task_type": "blog_post",
  "topic": "AI Trends 2025",
  "style": "narrative",
  "tone": "professional",
  "target_length": 2000,
  "generate_featured_image": true,
  "models_by_phase": {
    "research": "claude-3.5-sonnet",
    "creative": "claude-3-opus"
  }
}
```

**Problem:**

1. ❌ Both routes create the same task in the database
2. ❌ Unclear which to use
3. ❌ Different response formats
4. ❌ Different field names and validation
5. ❌ Both end up calling ContentRouterService anyway
6. ❌ Code duplication in handlers

---

## User's Vision vs. Current State

### Your Goal:

```
POST /api/tasks (single unified endpoint)
  - Accepts: task_type, topic, and type-specific fields
  - Parser: Determines task type (blog_post, social_media, image, etc.)
  - Router: Sends to appropriate orchestrator
  - Future: Easy to add new task types
```

### Current State:

```
❌ Two creation endpoints (/api/tasks and /api/content/tasks)
❌ Subtask endpoints bypass orchestrator completely
❌ No unified "task type" dispatcher
❌ Content-specific fields only in content_routes
```

---

## Recommended Consolidation

### Phase 1: Unify Task Creation

**New consolidated endpoint:**

```python
POST /api/tasks (UNIFIED - replaces both)
```

**New unified request schema:**

```python
class UnifiedTaskRequest(BaseModel):
    task_type: str  # "blog_post", "social_media", "image", "email", etc.

    # Common fields
    topic: str

    # Content-specific optional fields
    style: Optional[str] = "narrative"           # For blog posts
    tone: Optional[str] = "professional"         # For blog posts
    target_length: Optional[int] = 2000          # For blog posts
    generate_featured_image: Optional[bool] = False

    # For social media
    platforms: Optional[List[str]] = None        # ["twitter", "linkedin"]

    # For image generation
    image_count: Optional[int] = None
    image_style: Optional[str] = None

    # Common optional
    tags: Optional[List[str]] = None
    models_by_phase: Optional[Dict[str, str]] = None
    quality_preference: Optional[str] = "balanced"
```

**New unified handler:**

```python
@router.post("", response_model=UnifiedTaskResponse, status_code=201)
async def create_task(
    request: UnifiedTaskRequest,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Unified task creation endpoint - routes to appropriate handler"""

    # Route based on task_type
    if request.task_type == "blog_post":
        return await _handle_blog_post(request, current_user, db_service)
    elif request.task_type == "social_media":
        return await _handle_social_media(request, current_user, db_service)
    elif request.task_type == "image_generation":
        return await _handle_image_generation(request, current_user, db_service)
    else:
        raise HTTPException(400, f"Unknown task_type: {request.task_type}")
```

---

### Phase 2: Eliminate Subtask Bypass Endpoints

**Current subtask endpoints (problematic):**

```
POST /api/subtasks/research   ← Bypasses orchestrator
POST /api/subtasks/creative   ← Bypasses orchestrator
POST /api/subtasks/qa         ← Bypasses orchestrator
```

**Problem:** These let users run individual phases, skipping the full orchestration.

**Options:**

1. **Remove entirely** - Users should only use /api/tasks
2. **Move to admin-only** - Keep for debugging but require special auth
3. **Deprecate slowly** - Warn users in docs, plan removal

**Recommendation:** Remove subtask endpoints or move to `/api/admin/subtasks` with stricter auth.

---

### Phase 3: Keep Content/Retrieval Endpoints Separate

**These are fine as-is:**

```
GET  /api/tasks                    ✓ List all tasks
GET  /api/tasks/{task_id}          ✓ Get task details
GET  /api/tasks/{task_id}/status   ✓ Get task status

POST /api/tasks/{task_id}/approve  ✓ Approval workflow
POST /api/tasks/{task_id}/publish  ✓ Publishing
POST /api/tasks/{task_id}/reject   ✓ Rejection
```

These are action endpoints, not duplicates.

---

### Phase 4: Define Explicit Task Types

**Registry of supported task types:**

```python
class TaskType(str, Enum):
    BLOG_POST = "blog_post"
    SOCIAL_MEDIA = "social_media"
    EMAIL = "email"
    NEWSLETTER = "newsletter"
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"
    # Add more as orchestrator supports them
```

Each type has:

- Specific request schema
- Specific orchestrator handler
- Specific cost calculation
- Specific response format

---

## Implementation Roadmap

### Step 1: Create UnifiedTaskRequest schema

**Files:** `schemas/unified_task_request.py`

- Base fields: task_type, topic
- Blog post fields: style, tone, target_length, featured_image
- Social media fields: platforms
- Image fields: count, style
- Common: tags, models_by_phase, quality_preference

### Step 2: Create unified create_task handler

**Files:** `routes/task_routes.py`

- Parse task_type
- Route to type-specific handler
- Log all attempts
- Unified response format

### Step 3: Create type-specific handlers

**Files:** `routes/task_routes.py` or new `routes/task_handlers.py`

```python
async def _handle_blog_post(request, user, db)
async def _handle_social_media(request, user, db)
async def _handle_image_generation(request, user, db)
# etc.
```

### Step 4: Deprecate /api/content/tasks

**Action:** Add deprecation warning

```python
@deprecated(version="2.0", reason="Use /api/tasks instead with task_type parameter")
@content_router.post("/tasks")
async def create_content_task(...):
    """DEPRECATED: Use POST /api/tasks with task_type='blog_post' instead"""
```

### Step 5: Remove/restrict subtask endpoints

**Action:** Move to admin or remove entirely

- Remove from public API
- Move to `/api/admin/subtasks` if needed for debugging
- Require special auth token

---

## Example Unified API Usage

### Before (Current - Confusing)

```bash
# Using /api/tasks
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer JWT" \
  -d '{
    "task_name": "Blog Post",
    "topic": "AI Trends"
  }'

# OR using /api/content/tasks (different endpoint!)
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Authorization: Bearer JWT" \
  -d '{
    "task_type": "blog_post",
    "topic": "AI Trends",
    "style": "narrative"
  }'
```

### After (Proposed - Clear)

```bash
# ONE unified endpoint, handles all task types
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer JWT" \
  -d '{
    "task_type": "blog_post",
    "topic": "AI Trends",
    "style": "narrative",
    "tone": "professional",
    "target_length": 2000
  }'

# Same endpoint, different task type
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer JWT" \
  -d '{
    "task_type": "social_media",
    "topic": "AI Trends",
    "platforms": ["twitter", "linkedin"]
  }'

# Same endpoint, extensible for future types
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer JWT" \
  -d '{
    "task_type": "video_generation",
    "topic": "AI Trends",
    "duration": 60,
    "style": "educational"
  }'
```

---

## Benefits of Consolidation

### For Users:

- ✅ Single endpoint to learn
- ✅ Clear `task_type` field
- ✅ Consistent request format
- ✅ Easy to discover supported types

### For Developers:

- ✅ Single code path to maintain
- ✅ Shared validation logic
- ✅ Easier to add new task types
- ✅ Clear routing logic
- ✅ Better testability

### For Orchestrator:

- ✅ Single entry point
- ✅ Easy to add new handlers
- ✅ Can introspect task_type
- ✅ Future: ML-based routing options

---

## Questions for You

1. **Task type inference:** Should the system infer task type from request fields, or require explicit `task_type` parameter?
   - Current: Requires explicit (safer)
   - Alternative: Infer from fields (guessing game)

2. **Subtask endpoints:** Should `/api/subtasks/*` be removed entirely or kept for advanced users?

3. **Backward compatibility:** How long to support `/api/content/tasks` before removal?

4. **New task types:** What's your roadmap for new task types beyond blog/social/email?

---

## Recommended Next Steps

1. **Review unified schema** - Does it match your vision?
2. **Create UnifiedTaskRequest** - Start building
3. **Update create_task handler** - Implement routing
4. **Test with existing content** - Ensure it still works
5. **Deprecate /api/content/tasks** - Add warning
6. **Document task types** - Clear API docs
7. **Plan subtask removal** - Timeline?

This consolidation aligns with your vision of a single flexible endpoint that routes different task types to appropriate handlers.
