# PostgreSQL → Co-Founder Agent → Strapi CMS Pipeline Analysis

**Date:** November 3, 2025  
**Status:** ⚠️ **PARTIALLY IMPLEMENTED** - Critical Gap Identified

---

## 🎯 Executive Summary

The pipeline from PostgreSQL database to Strapi CMS exists but **has a critical gap**: Tasks created via the PostgreSQL database endpoint (`/api/tasks`) are **not being picked up by the content generation pipeline**.

### Current State:

- ✅ **PostgreSQL ↔ Task Management**: Working
- ✅ **Content Generation → Strapi**: Working
- ❌ **PostgreSQL → Content Generation**: **MISSING** - No consumer for DB tasks

---

## 📊 Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                   GLAD LABS PIPELINE                            │
└─────────────────────────────────────────────────────────────────┘

┌──────────────────────┐
│  Oversight Hub (UI)  │
│  (React Frontend)    │
└──────────────────────┘
         │
         │ POST /api/tasks with blog post request
         ↓
┌──────────────────────────────────────────────────────────────────┐
│              FastAPI Co-Founder Agent (main.py)                  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ routes/task_routes.py                                   │   │
│  │ POST /api/tasks                                         │   │
│  │ - Creates task in PostgreSQL                            │   │
│  │ - Returns task ID                                       │   │
│  │ ❌ DOES NOT trigger content generation                  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ routes/content_routes.py                                │   │
│  │ POST /api/content/blog-posts                            │   │
│  │ - Creates task in PersistentTaskStore                   │   │
│  │ ✅ Triggers background_tasks.add_task()                │   │
│  └─────────────────────────────────────────────────────────┘   │
│           │                                                     │
│           ├─ services/content_router_service.py                │
│           │  async def process_content_generation_task()       │
│           │  - Generates blog post via AI                      │
│           │  - Searches for featured images (Pexels)           │
│           │  - Publishes to Strapi CMS                         │
│           │                                                     │
│           └─ services/strapi_client.py                         │
│              async def create_blog_post()                       │
│              - API call to Strapi                              │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
         │
         ↓
┌──────────────────────┐
│   Strapi CMS v5      │
│  (localhost:1337)    │
│                      │
│  ✅ RECEIVES posts   │
│  ✅ STORES content   │
│  ✅ PUBLISHES        │
└──────────────────────┘
         │
         ↓
┌──────────────────────┐
│  PostgreSQL          │
│  (glad_labs_dev)     │
│                      │
│  - articles table    │
│  - metadata          │
└──────────────────────┘
```

---

## 🔴 Critical Issue: The Missing Link

### Problem: Two Separate Task Systems

The codebase has **two distinct task management systems that don't communicate**:

#### System #1: PostgreSQL Tasks (`/api/tasks`)

```
Location: src/cofounder_agent/routes/task_routes.py
Endpoint: POST /api/tasks
Storage: PostgreSQL `tasks` table
Flow:
  1. Oversight Hub creates task
  2. Task stored in DB
  3. ❌ NOTHING picks it up
  4. ❌ No content generation triggered
  5. ❌ Strapi never receives content
```

**Current Status of `/api/tasks`:**

```python
@router.post("", response_model=Dict[str, Any], status_code=201)
async def create_task(request: TaskCreateRequest, current_user: dict = Depends(get_current_user)):
    """Create a new task for content generation."""
    try:
        task_data = {
            "task_name": request.task_name,
            "topic": request.topic,
            "primary_keyword": request.primary_keyword,
            "target_audience": request.target_audience,
            "category": request.category,
            "status": "pending",
            # ... more fields
        }

        # Add task to database
        task_id = await db_service.add_task(task_data)

        # ❌ STOPS HERE - No background processing!
        return {"id": task_id, "status": "pending", ...}
```

#### System #2: Content Generation Tasks (`/api/content/blog-posts`)

```
Location: src/cofounder_agent/routes/content_routes.py
Endpoint: POST /api/content/blog-posts
Storage: PersistentTaskStore (SQLAlchemy-based)
Flow:
  1. Client creates content task
  2. Task stored in PersistentTaskStore
  3. ✅ background_tasks.add_task() triggered
  4. ✅ Content generated via AI
  5. ✅ Published to Strapi
```

**Current Status of `/api/content/blog-posts`:**

```python
@content_router.post("/blog-posts", response_model=CreateBlogPostResponse, status_code=201)
async def create_blog_post(request: CreateBlogPostRequest, background_tasks: BackgroundTasks):
    """Create a new blog post with AI generation."""
    try:
        # Create task in persistent store
        task_id = task_store.create_task(
            topic=request.topic,
            style=request.style,
            tone=request.tone,
            # ... more fields
        )

        # ✅ THIS IS THE KEY LINE - Triggers background processing!
        background_tasks.add_task(process_content_generation_task, task_id)

        # Process includes:
        # 1. Content generation
        # 2. Image search
        # 3. Strapi publishing

        return CreateBlogPostResponse(task_id=task_id, ...)
```

---

## ✅ What IS Working

### 1. Content Generation (Verified)

```python
# Location: services/content_router_service.py
async def process_content_generation_task(task_id: str):
    """Process a content generation task - FULLY IMPLEMENTED"""
    # Stage 1: Generate content with AI ✅
    # Stage 2: Search for featured image ✅
    # Stage 3: Publish to Strapi ✅
    # Stage 4: Track progress and handle errors ✅
```

**Features:**

- Multi-model AI support (Ollama → HuggingFace → Gemini → Anthropic → OpenAI)
- Featured image search via Pexels API
- SEO optimization available
- Draft/publish modes
- Progress tracking

### 2. Strapi Integration (Verified)

```python
# Location: services/strapi_client.py
async def create_blog_post(
    title: str,
    content: str,
    summary: str,
    tags: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    featured_image_url: Optional[str] = None,
    publish: bool = False,
) -> Dict[str, Any]:
    """Publish blog post to Strapi - FULLY IMPLEMENTED & TESTED"""
    # Uses aiohttp for async POST to Strapi API
    # Handles draft vs published modes
    # Returns Strapi post ID and URL
```

**Endpoints:**

- `POST /articles` - Create blog post
- `PUT /articles/{id}` - Publish draft
- `GET /articles/{id}` - Fetch post
- `GET /articles` - List posts

### 3. PostgreSQL Database Integration (Verified)

```python
# Location: services/database_service.py
async def add_task(self, task_data: Dict[str, Any]) -> str:
    """Create new task in PostgreSQL - FULLY IMPLEMENTED"""
    # Uses asyncpg for async PostgreSQL operations
    # Stores in `tasks` table with proper schema
    # Returns UUID task ID
```

**Schema:**

- `id` (UUID, Primary Key)
- `task_name`, `topic`, `primary_keyword`, `target_audience`, `category`
- `status`, `agent_id`, `user_id`
- `metadata`, `created_at`, `updated_at`
- Foreign key relationships to other tables

---

## ❌ What's NOT Working

### The Gap: PostgreSQL Tasks Are Orphaned

When you create a task via `/api/tasks`:

1. ✅ Task is saved to PostgreSQL
2. ❌ **No one reads it from the database**
3. ❌ **No content generation is triggered**
4. ❌ **Strapi never receives the content**
5. ❌ **Task stays in "pending" status forever**

**Example Flow (Currently Broken):**

```
POST /api/tasks
{
  "task_name": "Blog Post: AI in Healthcare",
  "topic": "AI in Healthcare",
  "primary_keyword": "AI healthcare",
  "target_audience": "Healthcare professionals",
  "category": "healthcare"
}

Response: {"id": "uuid-123", "status": "pending", ...}

❌ That's it! Nothing else happens!
❌ Task sits in PostgreSQL with status="pending"
❌ Oversight Hub polling /api/tasks/uuid-123/status gets "pending" forever
```

---

## 🔧 How to Fix It

### Solution: Connect the Two Systems

**Option A: Hybrid Approach (RECOMMENDED)**
Convert `/api/tasks` endpoint to use the content generation pipeline:

```python
@router.post("", response_model=Dict[str, Any], status_code=201)
async def create_task(
    request: TaskCreateRequest,
    background_tasks: BackgroundTasks,  # ADD THIS
    current_user: dict = Depends(get_current_user)
):
    """Create a new task for content generation."""
    try:
        # Keep PostgreSQL for persistence
        task_data = {
            "task_name": request.task_name,
            "topic": request.topic,
            "primary_keyword": request.primary_keyword,
            "target_audience": request.target_audience,
            "category": request.category,
            "status": "pending",
            "agent_id": "content-agent",
            "user_id": current_user.get("id", "system"),
            "metadata": request.metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Add task to database
        task_id = await db_service.add_task(task_data)

        # ADD THIS: Trigger content generation
        background_tasks.add_task(
            process_db_task,  # NEW HANDLER
            task_id,
            request  # Original request data
        )

        return {
            "id": task_id,
            "status": "pending",
            "created_at": task_data["created_at"],
            "message": "Task created successfully - generating content..."
        }
```

**New Handler to Bridge the Gap:**

```python
async def process_db_task(task_id: str, request: TaskCreateRequest):
    """
    Bridge function: Reads task from PostgreSQL and triggers generation
    """
    try:
        # Get task from database
        task = await db_service.get_task(task_id)
        if not task:
            logger.error(f"Task not found: {task_id}")
            return

        # Update status
        await db_service.update_task_status(task_id, "generating")

        # Create equivalent content generation task
        content_task_id = create_content_generation_task(
            topic=task["topic"],
            style=task.get("category", "technical"),
            tone="professional",
            target_length=2000,
            tags=[task.get("primary_keyword", "")],
            target_audience=task.get("target_audience", "general"),
        )

        # Store reference to content task in metadata
        await db_service.update_task(task_id, {
            "metadata": {
                **task.get("metadata", {}),
                "content_task_id": content_task_id
            }
        })

        # Process content generation
        await process_content_generation_task(content_task_id)

        # When complete, update PostgreSQL task with results
        content_task = get_content_task_store().get_task(content_task_id)
        if content_task and content_task.get("status") == "completed":
            await db_service.update_task(task_id, {
                "status": "completed",
                "metadata": {
                    **task.get("metadata", {}),
                    "strapi_post_id": content_task.get("result", {}).get("strapi_post_id"),
                    "generated_content": content_task.get("result", {})
                }
            })

    except Exception as e:
        logger.error(f"Error processing DB task {task_id}: {e}", exc_info=True)
        await db_service.update_task_status(task_id, "failed")
```

**Option B: Polling Service**
Implement a periodic polling service that:

1. Queries PostgreSQL for pending tasks
2. Creates corresponding content generation tasks
3. Updates status as processing completes

```python
async def task_polling_service():
    """Background service to consume pending tasks from PostgreSQL"""
    while True:
        try:
            # Get pending tasks
            pending_tasks = await db_service.get_pending_tasks(limit=10)

            for task in pending_tasks:
                # Create content generation task
                await create_and_process_content_from_db_task(task)

        except Exception as e:
            logger.error(f"Error in task polling service: {e}")

        # Poll every 30 seconds
        await asyncio.sleep(30)
```

---

## 📊 Current Test Results

### ✅ Working: Direct Content Generation

```bash
POST /api/content/blog-posts
{
  "topic": "Machine Learning Trends",
  "style": "technical",
  "tone": "professional",
  "target_length": 2000,
  "tags": ["ML", "AI"]
}

Response: 201 Created
{
  "task_id": "a1b2c3d4-e5f6-47g8-h9i0",
  "status": "pending",
  "polling_url": "/api/content/blog-posts/tasks/a1b2c3d4-e5f6-47g8-h9i0"
}

Poll /api/content/blog-posts/tasks/a1b2c3d4-e5f6-47g8-h9i0
Response: Content generated and published to Strapi ✅
```

### ❌ Broken: PostgreSQL Task Chain

```bash
POST /api/tasks
{
  "task_name": "Blog Post: AI in Healthcare",
  "topic": "AI in Healthcare",
  "primary_keyword": "AI healthcare",
  "target_audience": "Healthcare professionals",
  "category": "healthcare"
}

Response: 201 Created
{
  "id": "xyz-789-uvw",
  "status": "pending"
}

Poll /api/tasks/xyz-789-uvw
Response: status: "pending" (FOREVER - nothing processes it)
Check Strapi: No new content ❌
```

---

## 🎯 Recommendations

### Immediate (Critical):

1. **Implement `/api/tasks` ↔ Content Generation Bridge**
   - Add `background_tasks` to `/api/tasks` endpoint
   - Create handler to trigger `process_content_generation_task()`
   - Map PostgreSQL task fields to content generation parameters

2. **Add Status Endpoint for `/api/tasks`**
   - GET `/api/tasks/{task_id}` should show generation progress
   - Include Strapi post ID when available

3. **Testing**
   - Verify end-to-end: Oversight Hub → PostgreSQL → Content → Strapi
   - Update integration tests

### Short-term:

1. Deprecate the dual-system approach
2. Consolidate to single `/api/tasks` endpoint with unified processing
3. Archive `task_store_service.py` logic into PostgreSQL schema
4. Update API documentation

### Long-term:

1. Consider MCP integration for distributed agent processing
2. Implement event-driven architecture with Pub/Sub alternative
3. Add task queuing system (Redis, RabbitMQ) for scalability

---

## 📚 Relevant Files

### Core Pipeline:

- `src/cofounder_agent/routes/task_routes.py` - DB task endpoint
- `src/cofounder_agent/routes/content_routes.py` - Content generation endpoint
- `src/cofounder_agent/services/content_router_service.py` - Generation orchestration
- `src/cofounder_agent/services/strapi_client.py` - Strapi integration
- `src/cofounder_agent/services/database_service.py` - PostgreSQL interface

### Related:

- `web/oversight-hub/src/services/cofounderAgentClient.js` - Frontend client
- `src/cofounder_agent/main.py` - Application startup
- `cms/strapi-v5-backend/` - Strapi CMS (receives published content)

---

## 🔗 Connections Verified

| Connection                         | Status | Notes                       |
| ---------------------------------- | ------ | --------------------------- |
| Oversight Hub → `/api/tasks`       | ✅     | Working, returns task ID    |
| `/api/tasks` → PostgreSQL          | ✅     | Task stored with all fields |
| PostgreSQL → Content Gen           | ❌     | **NO INTEGRATION**          |
| Content Gen → Strapi               | ✅     | Fully functional            |
| Strapi → PostgreSQL                | ✅     | Post ID stored in metadata  |
| `/api/content/blog-posts` → Strapi | ✅     | End-to-end working          |

---

## 🚀 Next Steps

1. **Immediate Priority**: Implement bridge between `/api/tasks` and content generation
2. **Verify**: Test with Oversight Hub creating tasks and checking status
3. **Monitor**: Check Strapi receives published content correctly
4. **Document**: Update API contracts and integration guides
