# 🏗️ Glad Labs: Task Pipeline & Real-Time Architecture Proposal

**Date:** November 3, 2025  
**Status:** ⏳ AWAITING YOUR APPROVAL  
**Decision Required By:** Before Implementation  
**Effort Estimate:** 4-6 weeks (development, testing, deployment)

---

## 🎯 Executive Summary

This proposal outlines a **complete overhaul** of the content generation pipeline to:

1. **Preserve self-assessment core value** - Keep existing multi-step quality evaluation and scoring
2. **Implement real-time visibility** - Stream verbose logs to Oversight Hub as tasks progress
3. **Use MCP for agent orchestration** - Leverage Model Context Protocol for flexible tool calling
4. **Add Redis for task state management** - Track live progress, enable task cancellation, priority queues
5. **Optional queue system** - RabbitMQ for future scaling (deferred unless you want complexity now)

**Bottom Line:** Your system is 90% ready. The missing piece is connecting PostgreSQL tasks to the content generation pipeline with real-time status updates. This proposal fixes that while preserving your self-critique loops.

---

## ❌ Current Problems

### Problem 1: Disconnected Pipeline

```
Oversight Hub → POST /api/tasks → PostgreSQL ✅
    BUT
PostgreSQL Tasks ❌ → Content Generation (independent system)
Content Generation ❌ → Strapi Publishing
```

**Impact:** Tasks created from Oversight Hub are orphaned. They never generate content.

### Problem 2: No Real-Time Status

- Frontend polls `/api/tasks/{id}` every 2-5 seconds (inefficient)
- No streaming updates on what's happening (generate? validating? publishing?)
- User sees "pending" → "completed" with no visibility into intermediate steps
- Backend logs are in 4 separate systems (FastAPI, Ollama, Strapi, database)

### Problem 3: Self-Assessment Logic Underutilized

- Excellent self-critique pipeline exists (`AIContentGenerator._validate_content()`)
- Quality scores (0-10) and issue tracking working perfectly
- But frontend doesn't show this verbose feedback during generation
- User doesn't see "Initial score: 6.2/10, issues: missing examples, needs stronger conclusion"

### Problem 4: Scaling Complexity

- No task prioritization system
- No task cancellation
- No retry logic for failed generations
- Direct function calls (not queue-based)

---

## ✅ Your Existing Assets

### Self-Assessment System (CORE VALUE - PRESERVE)

```python
# src/cofounder_agent/services/ai_content_generator.py
class AIContentGenerator:
    def _validate_content(self):
        # Returns: quality_score (0-10), issues[], feedback
        # Checks: length, structure, headings, examples, CTA, tone, etc.

    async def generate_blog_post():
        # Generation loop with validation
        # Refinement attempts (up to 3)
        # Returns full metrics including validation_results
```

**Usage in generation:**

1. Generate draft → Validate (score: 6.5/10)
2. Issues found → Refinement loop
3. Validate again (score: 8.2/10) → Accept
4. Track all attempts in metrics

**Current Metrics Tracked:**

- `validation_results`: List of each attempt with score, issues, passed
- `final_quality_score`: 0-10
- `generation_attempts`: How many tries?
- `refinement_attempts`: How many refinements?
- `model_used`: Which AI model?
- `generation_time_seconds`: Total time

### MCP Infrastructure (ALREADY BUILT)

```python
# src/mcp/mcp_orchestrator.py
# src/mcp/client_manager.py
# src/mcp/base_server.py

# Already implements:
✅ Tool registration system
✅ Server discovery
✅ Tool calling with arguments
✅ Resource management
✅ Error handling
```

### Content Generation System (WORKING)

```python
# src/cofounder_agent/routes/content.py - POST /api/content/blog-posts
# Already does:
✅ Generate content with self-checking
✅ Search featured images
✅ Publish to Strapi
✅ Background task processing
✅ Full metrics tracking
```

### PostgreSQL Schema (READY)

```sql
-- tasks table with all needed columns:
id, task_name, topic, primary_keyword, target_audience,
category, status, agent_id, user_id, metadata,
created_at, updated_at, started_at, completed_at,
task_metadata, result
```

---

## 🎯 Proposed Solution Architecture

### Option A: MCP + Redis (RECOMMENDED)

```
┌─────────────────────────────────────────────────┐
│         OVERSIGHT HUB (React)                   │
│  - WebSocket connection to backend              │
│  - Real-time progress stream                    │
│  - Verbose logs: API, Ollama, FastAPI, Strapi  │
│  - Quality scores & issues displayed live       │
└────────────────┬────────────────────────────────┘
                 │ WebSocket
                 ▼
┌─────────────────────────────────────────────────┐
│     FASTAPI ORCHESTRATOR (Main)                 │
│  ┌───────────────────────────────────────────┐ │
│  │ Task Router (POST /api/tasks)             │ │
│  │ - Validate request                        │ │
│  │ - Create PostgreSQL task record           │ │
│  │ - Add to Redis queue (priority)           │ │
│  │ - Trigger background worker               │ │
│  │ - Return task_id immediately              │ │
│  └───────────────────────────────────────────┘ │
│                                                 │
│  ┌───────────────────────────────────────────┐ │
│  │ Task Status Endpoint (GET /api/tasks/{id})│ │
│  │ - Fetch from PostgreSQL                   │ │
│  │ - Return: status, progress %, stage, logs │ │
│  │ - WebSocket push on updates               │ │
│  └───────────────────────────────────────────┘ │
└────────────────┬────────────────────────────────┘
                 │
        ┌────────┴────────┐
        ▼                 ▼
┌──────────────────┐  ┌──────────────────┐
│   REDIS QUEUE    │  │   PostgreSQL DB  │
│  (Task State)    │  │  (Persistence)   │
│                  │  │                  │
│ - Priority Q     │  │ - Tasks table    │
│ - Task metadata  │  │ - Results        │
│ - Status updates │  │ - Logs           │
└──────────────────┘  └──────────────────┘
        ▲
        │
┌───────┴──────────────────────────────────┐
│  BACKGROUND WORKER (FastAPI Task)        │
│                                          │
│  for each task in redis_queue:           │
│    ├─→ Stage 1: Generate Content         │
│    │   - Call AIContentGenerator          │
│    │   - Capture ALL validation results  │
│    │   - Update Redis: progress 0% → 25% │
│    │   - Stream logs to WebSocket        │
│    │                                     │
│    ├─→ Stage 2: Search Images            │
│    │   - Get featured image URL          │
│    │   - Update Redis: progress 25% → 50%│
│    │   - Stream logs to WebSocket        │
│    │                                     │
│    ├─→ Stage 3: Publish to Strapi        │
│    │   - Create post in CMS              │
│    │   - Get Strapi post ID              │
│    │   - Update Redis: progress 50% → 75%│
│    │   - Stream logs to WebSocket        │
│    │                                     │
│    └─→ Stage 4: Finalize                 │
│        - Update PostgreSQL with results  │
│        - Clear Redis entry               │
│        - Update Redis: progress 100%     │
│        - Stream COMPLETE log             │
│                                          │
└──────────────────────────────────────────┘
```

### Redis Structure (Proposed)

```python
# Task Queue (Priority)
tasks:queue:high     # High priority tasks
tasks:queue:normal   # Standard priority (default)
tasks:queue:low      # Low priority

# Task Progress (Live Status)
tasks:progress:{task_id}
{
    "status": "in_progress",
    "stage": "generating_content",
    "progress_percent": 25,
    "current_step": "Initial draft generation - Ollama:mistral",
    "started_at": "2025-11-03T10:00:00Z"
}

# Task Logs (Real-Time Stream)
tasks:logs:{task_id}  # Redis stream with all logs
tasks:events:{task_id}  # Events (stage change, quality score, etc.)

# Task Result (Cached)
tasks:result:{task_id}
{
    "content": "...",
    "quality_score": 8.5,
    "model_used": "Ollama - mistral",
    "strapi_post_id": "123",
    "metadata": {...},
    "completed_at": "2025-11-03T10:02:30Z"
}
```

### MCP Enhancements (Proposed)

#### 1. Add MCP Tool: CreateTaskWithMCP

```python
# src/mcp/servers/task_server.py (NEW)

class TaskMCPServer(BaseMCPServer):
    """MCP server for task management"""

    @register_tool("create_task")
    async def create_task(self,
        topic: str,
        style: str,
        tone: str,
        target_length: int,
        tags: List[str]) -> Dict:
        """Create content generation task via MCP"""
        # → Calls task_routes.create_task()
        # → Returns task_id + queue position

    @register_tool("get_task_status")
    async def get_task_status(self, task_id: str) -> Dict:
        """Get live task status with progress"""
        # → Fetches from Redis
        # → Returns: progress %, stage, logs, quality score

    @register_tool("cancel_task")
    async def cancel_task(self, task_id: str) -> Dict:
        """Cancel running task"""
        # → Removes from Redis queue
        # → Updates PostgreSQL status = "cancelled"
```

#### 2. Add MCP Tool: StreamLogs

```python
class TaskMCPServer:
    @register_tool("get_task_logs")
    async def get_task_logs(self, task_id: str) -> Dict:
        """Get all logs for a task"""
        # Returns:
        # - FastAPI logs
        # - Ollama inference logs
        # - Validation feedback
        # - Strapi API logs
        # - Quality scores at each stage
```

---

## 📊 Implementation Plan

### Phase 1: Redis Foundation (Week 1-2)

**Goal:** Task state management with persistence

**Tasks:**

1. Add redis-py to requirements.txt
2. Create `services/redis_service.py`
   - Connection pooling
   - Queue operations (push, pop, list)
   - Progress tracking (get, set, increment)
   - Log streaming (append, read)
3. Create `services/task_queue_manager.py`
   - Task priority handling
   - Queue monitoring
   - Metrics (queue size, processing speed)
4. Update `main.py` lifespan
   - Initialize Redis on startup
   - Health check endpoint
5. Create `/api/health/redis` endpoint

**Deliverable:** Redis running, can queue and retrieve tasks

**Estimated Effort:** 15-20 hours

---

### Phase 2: PostgreSQL ↔ Redis Bridge (Week 2-3)

**Goal:** Task creation triggers content generation

**Tasks:**

1. Update `task_routes.py`
   - After POST /api/tasks creates record
   - Push task to Redis queue
   - Return task_id + queue position
2. Create `background_worker.py`
   - Listen to Redis queue
   - For each task:
     - Update PostgreSQL status = "in_progress"
     - Update Redis progress = 0%
     - Call `AIContentGenerator.generate_blog_post()`
     - Capture every validation result
     - Push logs to Redis stream at each step
     - Update progress (25%, 50%, 75%, 100%)
     - Update PostgreSQL with final result

3. Create `task_processor.py`
   - Orchestrates entire pipeline
   - Error handling + retry logic
   - Strapi integration
   - Database updates

**Pseudo-code:**

```python
# background_worker.py
async def process_task_queue():
    while True:
        task_id = await redis.pop_from_queue()
        if not task_id:
            await asyncio.sleep(1)
            continue

        try:
            # Get task data from PostgreSQL
            task = await db.get_task(task_id)

            # STAGE 1: Generate
            await redis.set_progress(task_id, 0, "Generating content...")
            logger.info(f"[Stage 1] Starting content generation for {task_id}")

            generator = AIContentGenerator()
            content, model, metrics = await generator.generate_blog_post(
                topic=task.topic,
                style=task.metadata.get("style", "professional"),
                tone=task.metadata.get("tone", "professional"),
                target_length=task.metadata.get("length", 1000),
                tags=task.metadata.get("tags", [])
            )

            # Capture ALL validation results
            validation_details = {
                "attempts": metrics["validation_results"],
                "final_score": metrics["final_quality_score"],
                "model": model,
                "refinements": metrics["refinement_attempts"]
            }

            await redis.push_log(task_id, {
                "type": "generation_complete",
                "quality_score": validation_details["final_score"],
                "issues": validation_details["attempts"][-1].get("issues", [])
            })

            await redis.set_progress(task_id, 25, "Content generated ✓")

            # STAGE 2: Images
            await redis.set_progress(task_id, 25, "Searching for images...")
            logger.info(f"[Stage 2] Searching images for {task_id}")

            image_service = FeaturedImageService()
            image_url = await image_service.search_featured_image(task.topic)

            await redis.push_log(task_id, {
                "type": "image_found",
                "url": image_url
            })

            await redis.set_progress(task_id, 50, "Image found ✓")

            # STAGE 3: Strapi Publishing
            await redis.set_progress(task_id, 50, "Publishing to CMS...")
            logger.info(f"[Stage 3] Publishing to Strapi for {task_id}")

            strapi = StrapiPublishingService()
            result = await strapi.publish_blog_post(
                title=content_title,
                content=content,
                featured_image=image_url,
                tags=task.metadata.get("tags", [])
            )

            await redis.push_log(task_id, {
                "type": "published",
                "strapi_id": result["id"],
                "url": result["url"]
            })

            await redis.set_progress(task_id, 75, "Published to Strapi ✓")

            # STAGE 4: Finalize
            await redis.set_progress(task_id, 100, "Complete!")

            await db.update_task(task_id, {
                "status": "completed",
                "result": {
                    "content": content,
                    "quality_score": validation_details["final_score"],
                    "strapi_id": result["id"],
                    "validation": validation_details,
                    "completed_at": datetime.utcnow().isoformat()
                }
            })

            await redis.delete_progress(task_id)  # Clean up
            logger.info(f"✓ Task {task_id} completed successfully")

        except Exception as e:
            logger.error(f"✗ Task {task_id} failed: {e}")
            await db.update_task(task_id, {
                "status": "failed",
                "error": str(e)
            })
            await redis.push_log(task_id, {
                "type": "error",
                "error": str(e)
            })
```

**Deliverable:** Oversight Hub → /api/tasks → Redis Queue → Background Worker → Strapi

**Estimated Effort:** 20-25 hours

---

### Phase 3: WebSocket Real-Time Status (Week 3)

**Goal:** Frontend sees verbose updates as task runs

**Tasks:**

1. Create `services/websocket_manager.py`
   - Manages WebSocket connections per user
   - Broadcasts progress updates
   - Pushes log entries in real-time
   - Handles disconnection cleanup

2. Create `routes/ws_routes.py`
   - `GET /ws/tasks/{task_id}`
   - Authenticate user
   - Stream updates until task complete
   - Format: JSON line-delimited

3. Update Oversight Hub frontend
   - Connect WebSocket on task creation
   - Display progress bar (0-100%)
   - Display current stage with emoji
   - Verbose logs panel (scrollable)
   - Quality scores in real-time

**WebSocket Message Format:**

```json
// Progress update
{
  "type": "progress",
  "task_id": "abc-123",
  "percent": 25,
  "stage": "generating_content",
  "message": "Initial draft generated - score 6.5/10"
}

// Log entry
{
  "type": "log",
  "task_id": "abc-123",
  "level": "info",
  "source": "ollama",
  "message": "Model: mistral:latest, Prompt tokens: 245, Response tokens: 1,234"
}

// Quality update
{
  "type": "quality",
  "task_id": "abc-123",
  "score": 6.5,
  "issues": ["Missing examples", "Weak conclusion"],
  "feedback": "✗ Content needs improvement (6.5/10, threshold: 7.0)"
}

// Complete
{
  "type": "complete",
  "task_id": "abc-123",
  "status": "success",
  "strapi_id": "post-456",
  "final_score": 8.2
}
```

**Frontend Component:**

```jsx
<TaskProgress taskId={taskId}>
  Progress: 25% Stage: Generating content (Ollama:mistral) Quality Score: 6.5/10
  Issues Found: - Missing practical examples - Weak call-to-action [Real-time
  logs...] [2025-11-03 10:00:15] Ollama: Model loaded [2025-11-03 10:00:20]
  FastAPI: Generation started [2025-11-03 10:01:15] Validator: Quality check
  complete (score: 6.5) [2025-11-03 10:01:16] Refinement: Attempting improvement
  ...
</TaskProgress>
```

**Deliverable:** Real-time visibility into task execution

**Estimated Effort:** 15-20 hours

---

### Phase 4: MCP Integration (Week 4)

**Goal:** Use MCP for agent orchestration + tool flexibility

**Tasks:**

1. Create `src/mcp/servers/task_server.py`
   - Implement TaskMCPServer class
   - Register tools: create_task, get_task_status, cancel_task
   - Integrate with Redis + PostgreSQL

2. Update `mcp_integration.py`
   - MCPEnhancedCoFounder can now:
     - Create tasks via MCP
     - Get task status
     - See progress in real-time
     - Cancel if needed

3. Test MCP tool integration
   - Verify tool calling
   - Verify data flow

**Deliverable:** MCP servers expose task management

**Estimated Effort:** 10-15 hours

---

### Phase 5: Polish & Testing (Week 5-6)

**Goal:** Production-ready system

**Tasks:**

1. Error handling + retry logic
   - Ollama timeouts?
   - Strapi connection failures?
   - Redis down?
   - Graceful degradation

2. Task cancellation
   - Remove from Redis queue
   - Update status in PostgreSQL
   - Stop background worker gracefully

3. Metrics & monitoring
   - Queue size endpoint
   - Processing speed (avg time per task)
   - Success/failure rates
   - Peak hours analysis

4. Testing
   - Unit tests for Redis operations
   - Integration tests (full pipeline)
   - Load tests (multiple concurrent tasks)
   - WebSocket connection tests

5. Documentation
   - Architecture diagrams
   - API endpoint docs
   - Troubleshooting guide
   - Deployment instructions

**Deliverable:** Production-ready system

**Estimated Effort:** 20-30 hours

---

## 🚀 Why Option A (MCP + Redis)?

| Aspect                        | Option A            | Option B          | Option C              |
| ----------------------------- | ------------------- | ----------------- | --------------------- |
| **Complexity**                | Medium              | Low               | High                  |
| **Scalability**               | ⭐⭐⭐⭐            | ⭐⭐              | ⭐⭐⭐⭐⭐            |
| **Real-time Visibility**      | ✅ (WebSocket)      | ❌ (Polling only) | ✅ (Both)             |
| **Task Prioritization**       | ✅                  | ❌                | ✅                    |
| **Cost**                      | $0 (you have Redis) | $0                | $50-100/mo (RabbitMQ) |
| **Effort**                    | 4-6 weeks           | 1-2 weeks         | 8-10 weeks            |
| **Preserves Self-Assessment** | ✅                  | ✅                | ✅                    |
| **Cloud-Ready**               | ✅                  | ✅                | ✅                    |
| **Best For**                  | Your current setup  | Ultra-simple      | Enterprise scale      |

---

## 🤔 Should You Add RabbitMQ Now?

### NO. Here's why:

1. **You already have Redis deployed** on Railway
2. **Single developer** - no need for distributed message broker complexity
3. **Current volume** - likely <100 tasks/day
4. **Redis does everything you need:**
   - Task queuing
   - Priority handling
   - State management
   - Streaming logs
   - Easy to monitor

### WHEN to add RabbitMQ:

- [ ] Multiple backend servers (horizontal scaling)
- [ ] Multiple teams working on different agents
- [ ] > 10,000 tasks/day
- [ ] Need guaranteed message delivery guarantees
- [ ] Routing between different worker types

**Recommendation:** Skip RabbitMQ now. If you hit scaling limits later (unlikely in year 1), migrate then.

---

## 📋 What Stays the Same

### ✅ Preserve Completely

1. **Self-assessment system** (`AIContentGenerator._validate_content()`)
   - All quality scoring remains
   - All issue tracking remains
   - All refinement loops remain
   - We just expose them to frontend

2. **Existing routes**
   - `/api/content/blog-posts` still works
   - `/api/models/*` still works
   - `/api/agents/*` still works
   - Add to, don't replace

3. **MCP infrastructure**
   - All existing servers remain
   - Add TaskMCPServer alongside them
   - No breaking changes

4. **Strapi integration**
   - No changes to publishing
   - Same CMS API calls
   - Same data structures

5. **PostgreSQL schema**
   - Tasks table already perfect
   - Just add new status values
   - Add new columns? Nope, metadata JSONB handles everything

---

## ⚠️ Breaking Changes

### NONE!

All changes are **additive**:

- New Redis service (alongside PostgreSQL)
- New background worker (new process, doesn't affect existing)
- New WebSocket endpoint (new, doesn't affect REST)
- New task routes (new, don't touch existing content routes)

**Backward compatibility:** 100%

---

## 📊 Success Metrics

After implementation, you'll have:

✅ **Oversight Hub Workflow Works End-to-End**

```
1. Click "Generate Blog Post"
2. See real-time progress (25%, 50%, 75%, 100%)
3. Watch quality scores tick up (6.2 → 7.1 → 8.5)
4. See all logs: Ollama inference, validation checks, Strapi API calls
5. Post auto-publishes to Strapi when complete
6. Task card shows "✓ Published" with link to post
```

✅ **Full Visibility into AI Processing**

```
Real-time logs show:
- Ollama model loading
- Token counts (prompt + response)
- Quality validation at each step
- Refinement attempts with feedback
- Image search results
- Strapi API calls
- All timestamps
```

✅ **Measurable Quality Improvements**

```
- Track quality scores per attempt
- See which refinements helped
- Identify patterns (what topics need more refinement?)
- Benchmark: average time per task, success rate
```

✅ **Operational Control**

```
- Cancel running tasks
- Prioritize urgent content
- Retry failed tasks
- View queue depth in real-time
```

---

## 🛠️ Technical Notes

### Database Transactions

- Keep PostgreSQL as source of truth
- Redis is cache layer only
- If Redis crashes, data recovers from PostgreSQL
- Task recovery: Check status in DB, resume from where it left off

### Error Scenarios

```
Ollama times out?
  → Fallback to HuggingFace/Gemini
  → Log the failure
  → Continue to stage 2

Strapi API fails?
  → Retry 3 times with exponential backoff
  → If all fail: mark task "failed"
  → User can retry from Oversight Hub

Redis connection lost?
  → Use in-memory fallback queue
  → When Redis recovers, flush memory to persistent storage
```

### Load Handling

```
Tasks arrive faster than they complete?
  → Queue fills up (normal, by design)
  → Show "X tasks in queue, estimated wait: 5 min"
  → Allow user to set priority
  → Process high-priority first

All workers fail?
  → Supervisor process restarts them
  → Alert team
  → Oldest tasks resume first
```

---

## 💰 Cost Analysis

### Current Monthly (estimated)

- Railway PostgreSQL: $15
- Railway FastAPI: $25
- Vercel Frontend: $20
- **Total: $60/month**

### After Implementation

- Railway PostgreSQL: $15 (no change)
- Railway FastAPI: $25 (maybe $30 with more processing)
- Railway Redis: $10 (you already have it)
- Vercel Frontend: $20 (no change)
- **Total: $65-70/month** ✅

### RabbitMQ Comparison

- Would add: $50-100/month
- Not recommended unless you scale

---

## 🎬 Next Steps (If Approved)

1. **You approve this plan** ✓
2. I create GitHub issue with detailed tasks
3. Break Phase 1 into 5-6 day sprints
4. Start with Redis service
5. Incrementally add each layer
6. Test at each phase
7. Deploy to production when complete

---

## ❓ Questions for You

Before I proceed, please clarify:

1. **Verbose Logging Preferences**
   - How much detail? (timestamp for every step?)
   - Should logs persist in PostgreSQL for audit trail?
   - Archive old logs automatically?

2. **Task Prioritization**
   - Simple (high/normal/low) or complex (numeric score)?
   - Can users change priority after task starts?

3. **Error Recovery**
   - How many retries before giving up?
   - Should failed tasks auto-retry or wait for user action?

4. **UI Preferences**
   - Live logs panel scrollable?
   - Auto-scroll to bottom or stay where user scrolled?
   - Show raw API requests or human-readable summaries?

5. **Real-time vs Batch**
   - Must progress updates be instant (WebSocket)?
   - Or polling every 2-5 seconds acceptable?

6. **Monitoring/Alerting**
   - Should you get Slack alerts if task fails?
   - Monitor queue depth on Oversight Hub?

7. **Timeline Flexibility**
   - Can you wait 4-6 weeks for production?
   - Or need MVP sooner (skip some polish)?

---

## 📝 Decision Template

**Please approve/revise this plan:**

```
Architecture: [MCP + Redis ✅ / Modify Phase X / Use Option C instead]

Redis Deployment: [Yes, I have it on Railway ✅ / Need to set up first]

Real-time Priority: [WebSocket NOW / Polling OK for now / Defer to later]

Task Prioritization: [Simple (high/normal/low) / Complex / Defer]

Testing Rigor: [Comprehensive / Balanced / MVP-ready]

Timeline: [4-6 weeks is fine / Need faster / Can wait longer]

Additional Questions/Constraints: [...]
```

---

## 📚 References

- Current self-assessment code: `src/cofounder_agent/services/ai_content_generator.py`
- MCP infrastructure: `src/mcp/mcp_orchestrator.py`
- Existing PostgreSQL schema: `.env` + `src/cofounder_agent/routes/task_routes.py`
- Redis already deployed on Railway (verified in your staging setup)

---

**Status:** ⏳ AWAITING YOUR APPROVAL

Once approved, I'll create detailed sprint plans and start implementation.
