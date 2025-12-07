# Data Pipeline Gaps & Feature Implementation Plan

**Status:** Analysis Complete  
**Date:** December 5, 2025  
**Scope:** Identifying missing features and recommending implementations

---

## 🎯 Overview

The codebase has **5 core pipelines** working well, but **7 significant gaps** in functionality that should be addressed:

```
Core Pipelines (✅ Working):
  1. Content Generation (7-agent with human approval)
  2. Task Management (background execution)
  3. Model Routing (Ollama → Claude → GPT → Gemini)
  4. Authentication (OAuth + JWT)
  5. CMS (PostgreSQL direct access)

Missing Features (❌ Gaps):
  1. Social Media Publishing
  2. Real-time WebSocket Updates
  3. Automatic Error Recovery
  4. Approval System RBAC & Audit Trail
  5. Detailed Analytics & Metrics
  6. Workflow Visualization
  7. Multi-tenant Support (if needed)
```

---

## 📋 Gap 1: Social Media Publishing

### Current State

**Routes exist but implementation missing:**

```python
# src/cofounder_agent/routes/social_routes.py
@app.post("/api/social/publish")
async def publish_to_social(request: PublishRequest):
    """
    ❌ Question: What happens here?
    - Posts to Twitter/X?
    - Posts to LinkedIn?
    - Posts to Facebook?
    - Nothing - just validates request?
    """
```

### What's Missing

```
Content Ready (PostgreSQL posts table)
    ↓
POST /api/social/publish
    ├─ No Twitter API integration
    ├─ No LinkedIn API integration
    ├─ No Facebook API integration
    ├─ No Instagram support
    ├─ No posting scheduler
    └─ No analytics collection
```

### Implementation Difficulty: **MEDIUM** ⏱️ 4-6 hours

### Recommended Implementation

**Step 1: Create Social Media Client Abstraction**

```python
# src/cofounder_agent/services/social_media_clients/base_client.py
class SocialMediaClient(ABC):
    @abstractmethod
    async def post(self, content: str, image_url: Optional[str]) -> Dict[str, Any]:
        """Post content to platform"""
        pass

    @abstractmethod
    async def schedule(self, content: str, scheduled_time: datetime) -> str:
        """Schedule content for later"""
        pass

# Create implementations:
# - TwitterClient (tweepy or httpx)
# - LinkedInClient (oauth integration)
# - FacebookClient (facebook-sdk)
```

**Step 2: Add OAuth Integrations**

```python
# Store OAuth tokens in PostgreSQL
CREATE TABLE social_oauth_tokens (
    id UUID PRIMARY KEY,
    user_id UUID,
    platform VARCHAR(50),  -- "twitter", "linkedin", "facebook"
    access_token TEXT,
    refresh_token TEXT,
    expires_at TIMESTAMP,
    created_at TIMESTAMP
)
```

**Step 3: Implement Posting**

```python
# routes/social_routes.py
@app.post("/api/social/publish")
async def publish_to_social(request: PublishRequest):
    """
    Publish content to configured social platforms

    Request:
        content_id: str  -- ID of post to publish
        platforms: List[str]  -- ["twitter", "linkedin", "facebook"]
        scheduled_time: Optional[datetime]

    Returns:
        {
            "post_id": str,
            "platforms": {
                "twitter": {"status": "published", "url": "..."},
                "linkedin": {"status": "published", "url": "..."},
                "facebook": {"status": "failed", "error": "..."}
            }
        }
    """
    pass
```

**Step 4: Add Analytics Feedback**

```python
# After posting, periodically fetch metrics
class SocialAnalyticsService:
    async def collect_metrics(self, social_post_id: str):
        """Collect likes, shares, comments, impressions"""
        pass

# Store in new table:
CREATE TABLE social_post_metrics (
    id UUID PRIMARY KEY,
    social_post_id VARCHAR(255),
    platform VARCHAR(50),
    likes INT,
    shares INT,
    comments INT,
    impressions INT,
    collected_at TIMESTAMP
)
```

### Priority: **MEDIUM** 🟡

- **Why:** Needed for amplifying content beyond website
- **When:** Phase 6 or later
- **Dependencies:** OAuth provider integrations

---

## 📋 Gap 2: Real-time WebSocket Updates

### Current State

**Frontend polls task status:**

```javascript
// Oversight Hub polls every 2-5 seconds
while (true) {
  GET / api / tasks / { taskId };
  // Returns status, progress, logs
  await sleep(2000); // Very inefficient
}
```

### What's Missing

```
Should be:
    WebSocket /ws/tasks/{taskId}
    ├─ Push status updates in real-time
    ├─ Stream log output as it happens
    ├─ Real-time progress notifications
    └─ No polling needed
```

### Implementation Difficulty: **MEDIUM** ⏱️ 3-4 hours

### Recommended Implementation

**Step 1: Add WebSocket Support to FastAPI**

```python
# src/cofounder_agent/routes/websocket_routes.py
from fastapi import WebSocket, WebSocketDisconnect

@app.websocket("/ws/tasks/{task_id}")
async def websocket_task_stream(websocket: WebSocket, task_id: str):
    """
    Stream task execution in real-time

    Messages sent to client:
    {
        "type": "status",
        "status": "processing",
        "stage": "research",
        "percentage": 25
    }

    {
        "type": "log",
        "level": "info",
        "message": "Starting research phase..."
    }

    {
        "type": "complete",
        "status": "completed",
        "result": {...}
    }
    """
    await websocket.accept()

    try:
        # Subscribe to task updates
        async for message in task_updates_stream(task_id):
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
```

**Step 2: Add Task Update Stream**

```python
# src/cofounder_agent/services/task_stream_service.py
class TaskStreamService:
    async def stream_task_updates(self, task_id: str):
        """Stream task updates as AsyncGenerator"""
        # Poll database in background
        # Yield updates when status changes
        pass

    async def post_update(self, task_id: str, update: Dict[str, Any]):
        """Called by task executor to post updates"""
        # Store in Redis for real-time delivery
        # Notify all connected clients
        pass
```

**Step 3: Update Task Executor**

```python
# When task status changes, call:
await TaskStreamService.post_update(
    task_id=task_id,
    update={
        "type": "status",
        "status": "processing",
        "stage": "creative",
        "percentage": 40
    }
)
```

**Step 4: Update Frontend**

```javascript
// Oversight Hub
useEffect(() => {
  const ws = new WebSocket(`ws://localhost:8000/ws/tasks/${taskId}`);

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    setTaskStatus(msg.status);
    setProgress(msg.percentage);
  };

  return () => ws.close();
}, [taskId]);
```

### Priority: **HIGH** 🔴

- **Why:** Much better UX, eliminates polling
- **When:** Phase 6
- **Dependencies:** asyncio + Redis (optional but recommended)

---

## 📋 Gap 3: Automatic Error Recovery & Retry Logic

### Current State

**No retry mechanism:**

```
Task Fails (LLM API timeout, network error, etc.)
    ↓
status = "failed"
    ↓
error_message = stored in DB
    ↓
❌ User must manually retry via API
```

### What's Missing

```
Should be:
Task Fails
    ↓
Automatic Retry (exponential backoff)
    ├─ Attempt 1: immediate
    ├─ Attempt 2: +5 seconds
    ├─ Attempt 3: +10 seconds
    ├─ Attempt 4: +20 seconds
    └─ Max 4 attempts
    ↓
Circuit Breaker (stop retrying if pattern detected)
    ├─ If LLM API consistently fails
    ├─ Switch to fallback model
    ├─ Send alert
    └─ Continue with degraded service
    ↓
Dead Letter Queue (give up after retries)
    ├─ Move task to DLQ
    ├─ Send alert to admin
    └─ Manual inspection required
```

### Implementation Difficulty: **MEDIUM** ⏱️ 4-5 hours

### Recommended Implementation

**Step 1: Add Retry Configuration to Task**

```python
# Update PostgreSQL tasks table
ALTER TABLE tasks ADD COLUMN (
    max_retries INT DEFAULT 3,
    retry_count INT DEFAULT 0,
    last_retry_at TIMESTAMP,
    next_retry_at TIMESTAMP,
    error_category VARCHAR(50)  -- "rate_limit", "network", "validation", etc.
)

# Also add circuit breaker table
CREATE TABLE circuit_breakers (
    id UUID PRIMARY KEY,
    service VARCHAR(100),  -- "ollama", "openai", "anthropic", etc.
    status VARCHAR(20),  -- "closed", "open", "half_open"
    failure_count INT DEFAULT 0,
    last_failure_at TIMESTAMP,
    reset_at TIMESTAMP
)
```

**Step 2: Create Retry Service**

```python
# src/cofounder_agent/services/retry_service.py
class RetryService:
    async def should_retry(self, task: Task, error: Exception) -> bool:
        """
        Determine if task should be retried

        Don't retry:
        - Validation errors (user's fault)
        - Missing API keys
        - Rate limiting (use exponential backoff instead)

        Do retry:
        - Network timeouts
        - LLM API transient failures
        - Database connection issues
        """
        error_type = classify_error(error)

        if task.retry_count >= task.max_retries:
            return False

        if error_type == "validation":
            return False

        if error_type == "rate_limit":
            # Use exponential backoff instead of immediate retry
            delay = 2 ** task.retry_count + random(0, 1)
            task.next_retry_at = now() + timedelta(seconds=delay)
            return True

        # Retry transient errors
        return error_type in ["network", "timeout", "temporary"]

    async def execute_with_retry(self, task: Task, handler):
        """Execute with automatic retry"""
        while task.retry_count <= task.max_retries:
            try:
                return await handler(task)
            except Exception as e:
                if await self.should_retry(task, e):
                    task.retry_count += 1
                    task.last_retry_at = now()
                    await self.db.update_task(task)

                    # Wait before retry (exponential backoff)
                    delay = 2 ** task.retry_count
                    await asyncio.sleep(delay)
                else:
                    # Retry exhausted or non-retryable error
                    task.status = "failed"
                    raise
```

**Step 3: Add Circuit Breaker**

```python
# src/cofounder_agent/services/circuit_breaker.py
class CircuitBreaker:
    """Stop retrying if service is consistently failing"""

    async def call(self, service_name: str, handler):
        """Call handler through circuit breaker"""
        breaker = await self.get_breaker(service_name)

        if breaker.status == "open":
            # Too many failures, stop trying
            raise CircuitBreakerOpen(f"{service_name} is currently unavailable")

        try:
            result = await handler()

            # Success, reset circuit
            await self.reset_breaker(service_name)
            return result

        except Exception as e:
            # Record failure
            await self.record_failure(service_name)

            # Check if should open circuit
            breaker = await self.get_breaker(service_name)
            if breaker.failure_count >= 5:
                breaker.status = "open"
                breaker.reset_at = now() + timedelta(minutes=5)
                await self.db.update_breaker(breaker)

            raise
```

**Step 4: Update Task Executor**

```python
# task_executor.py
class TaskExecutor:
    async def execute_task(self, task: Task):
        async def run_task():
            # The actual task logic
            return await self.orchestrator.run(task)

        # Wrap with retry and circuit breaker
        try:
            result = await self.retry_service.execute_with_retry(
                task,
                lambda t: self.circuit_breaker.call(
                    "orchestrator",
                    lambda: run_task()
                )
            )
        except Exception as e:
            # After retries exhausted, move to DLQ
            if task.retry_count >= task.max_retries:
                task.status = "dead_letter"
                await self.notify_admin(task, e)
```

### Priority: **HIGH** 🔴

- **Why:** Prevents cascading failures, improves reliability
- **When:** Phase 6 or sooner
- **Dependencies:** PostgreSQL updates, asyncio

---

## 📋 Gap 4: Approval System RBAC & Audit Trail

### Current State

**Gate exists but no security:**

```
Content Ready
    ↓
POST /api/content/tasks/{id}/approve?decision=approve
    ↓
❌ Issues:
    - Anyone can approve (no role check)
    - No audit trail
    - No reviewer comments
    - No rejection workflow
```

### What's Missing

```
Should be:
POST /api/content/tasks/{id}/approve
{
    "decision": "approve" | "reject",
    "reviewer_id": "...",
    "comments": "Fix grammar in paragraph 3",
    "quality_score": 8.5
}
    ↓
Check: Is reviewer an admin or content manager?
    ↓
If yes, update content status
    ↓
Log approval in audit table
    ↓
If rejected, put back in draft for revision
    ↓
Email notification sent to content creator
```

### Implementation Difficulty: **LOW** ⏱️ 2-3 hours

### Recommended Implementation

**Step 1: Create Approval Tables**

```sql
-- Approval audit trail
CREATE TABLE content_approvals (
    id UUID PRIMARY KEY,
    content_id UUID REFERENCES posts(id),
    reviewer_id UUID,
    decision VARCHAR(20),  -- "approve", "reject"
    comments TEXT,
    quality_score DECIMAL(3,2),  -- 0.0 - 10.0
    approved_at TIMESTAMP
)

-- User roles
CREATE TABLE user_roles (
    id UUID PRIMARY KEY,
    user_id UUID,
    role VARCHAR(50),  -- "admin", "editor", "reviewer", "author"
    created_at TIMESTAMP
)
```

**Step 2: Add RBAC Check**

```python
# src/cofounder_agent/services/rbac_service.py
class RBACService:
    APPROVAL_ROLES = {"admin", "editor", "content_manager"}

    async def can_approve(self, user_id: str) -> bool:
        """Check if user has approval permission"""
        roles = await self.db.get_user_roles(user_id)
        return any(role in self.APPROVAL_ROLES for role in roles)

    async def can_create_content(self, user_id: str) -> bool:
        """Check if user can create content"""
        roles = await self.db.get_user_roles(user_id)
        return any(role in {"author", "editor", "admin"} for role in roles)
```

**Step 3: Update Approval Endpoint**

```python
# routes/cms_routes.py
@app.post("/api/content/tasks/{task_id}/approve")
async def approve_content(
    task_id: str,
    request: ApprovalRequest,
    current_user = Depends(get_current_user)
):
    """
    Approve or reject content with audit trail

    Request:
    {
        "decision": "approve" | "reject",
        "comments": "Optional feedback",
        "quality_score": 8.5
    }
    """
    # 1. Check permission
    if not await rbac.can_approve(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    # 2. Get task
    task = await db.get_task(task_id)

    # 3. Record approval
    approval = ContentApproval(
        content_id=task.content_id,
        reviewer_id=current_user.id,
        decision=request.decision,
        comments=request.comments,
        quality_score=request.quality_score
    )
    await db.create_approval(approval)

    # 4. Update content status
    if request.decision == "approve":
        await db.update_task(task_id, {"status": "published"})
        # Send email to author
        await notify_author(task, "approved")
    else:
        await db.update_task(task_id, {"status": "draft"})
        # Send email with feedback
        await notify_author(task, "rejected", request.comments)

    # 5. Return audit record
    return approval
```

**Step 4: Add Audit Log Endpoint**

```python
@app.get("/api/content/{content_id}/approval-history")
async def get_approval_history(content_id: str):
    """Get approval audit trail for content"""
    approvals = await db.get_approvals(content_id)
    return [
        {
            "reviewer": approval.reviewer_id,
            "decision": approval.decision,
            "comments": approval.comments,
            "quality_score": approval.quality_score,
            "timestamp": approval.approved_at
        }
        for approval in approvals
    ]
```

### Priority: **MEDIUM** 🟡

- **Why:** Security and compliance requirement
- **When:** Phase 6
- **Dependencies:** User roles table, email service

---

## 📋 Gap 5: Detailed Analytics & Metrics

### Current State

**Basic metrics only:**

```
GET /api/metrics
→ Returns overall task counts, success rates
→ No per-agent breakdown
→ No pipeline stage timing
→ No model cost attribution
```

### What's Missing

```
Should be:
Per-Agent Metrics:
    ├─ Research Agent: avg execution time, quality score
    ├─ Creative Agent: draft generation speed, revision count
    ├─ QA Agent: feedback relevance, approval rate
    ├─ Image Agent: image selection success rate
    └─ Publishing Agent: publishing errors, edge cases

Per-Pipeline Metrics:
    ├─ Research stage: 10-15 seconds
    ├─ Creative stage: 20-30 seconds
    ├─ QA loop: 15-25 seconds
    ├─ Image stage: 8-12 seconds
    ├─ Publishing stage: 2-5 seconds
    └─ Total: 60-90 seconds per post

Cost Tracking:
    ├─ Cost per model provider
    ├─ Cost per task
    ├─ Cost attribution to agents
    └─ Monthly spend trends

Quality Metrics:
    ├─ QA approval rate (before human)
    ├─ Human approval rate (after QA)
    ├─ Content quality score trend
    └─ Revision count per stage
```

### Implementation Difficulty: **MEDIUM-HIGH** ⏱️ 6-8 hours

### Recommended Implementation

**Step 1: Add Metrics Schema**

```sql
-- Pipeline execution metrics
CREATE TABLE pipeline_metrics (
    id UUID PRIMARY KEY,
    task_id UUID,
    stage VARCHAR(50),  -- "research", "creative", "qa", "images", "publish"
    agent_name VARCHAR(100),
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    duration_ms INT,
    tokens_used INT,
    model_used VARCHAR(100),
    cost DECIMAL(10,4),
    success BOOLEAN,
    error_message TEXT
)

-- Agent-specific metrics
CREATE TABLE agent_metrics (
    id UUID PRIMARY KEY,
    agent_name VARCHAR(100),
    metric_name VARCHAR(100),  -- "execution_time", "quality_score", "error_rate"
    value DECIMAL(10,4),
    period_date DATE,
    count INT
)

-- Content quality metrics
CREATE TABLE quality_metrics (
    id UUID PRIMARY KEY,
    content_id UUID,
    qa_approval BOOLEAN,
    human_approval BOOLEAN,
    quality_score DECIMAL(3,2),
    revision_count INT,
    recorded_at TIMESTAMP
)
```

**Step 2: Create Analytics Service**

```python
# src/cofounder_agent/services/analytics_service.py
class AnalyticsService:
    async def record_stage_execution(
        self,
        task_id: str,
        stage: str,
        agent_name: str,
        duration_ms: int,
        tokens_used: int,
        model_used: str,
        cost: float,
        success: bool,
        error: Optional[str] = None
    ):
        """Record execution metrics for a pipeline stage"""
        metric = PipelineMetric(
            task_id=task_id,
            stage=stage,
            agent_name=agent_name,
            duration_ms=duration_ms,
            tokens_used=tokens_used,
            model_used=model_used,
            cost=cost,
            success=success,
            error_message=error
        )
        await self.db.create_metric(metric)

    async def get_agent_metrics(self, agent_name: str, days: int = 30):
        """Get performance metrics for an agent"""
        metrics = await self.db.get_agent_metrics(agent_name, days)

        return {
            "agent": agent_name,
            "period_days": days,
            "avg_execution_time_ms": metrics.avg_duration,
            "success_rate": metrics.success_count / metrics.total_count,
            "total_tokens": metrics.total_tokens,
            "total_cost": metrics.total_cost,
            "error_rate": metrics.error_count / metrics.total_count
        }

    async def get_pipeline_timing(self, days: int = 30):
        """Get average timing per pipeline stage"""
        stages = ["research", "creative", "qa", "images", "publish"]
        timing = {}

        for stage in stages:
            avg_time = await self.db.get_avg_stage_time(stage, days)
            timing[stage] = avg_time

        return {
            "period_days": days,
            "stage_timing_ms": timing,
            "total_avg_time_ms": sum(timing.values())
        }

    async def get_cost_breakdown(self, days: int = 30):
        """Get cost breakdown by model and agent"""
        return {
            "period_days": days,
            "by_model": await self.db.get_cost_by_model(days),
            "by_agent": await self.db.get_cost_by_agent(days),
            "by_task": await self.db.get_cost_by_task(days),
            "total_cost": await self.db.get_total_cost(days)
        }

    async def get_quality_metrics(self, days: int = 30):
        """Get quality metrics"""
        return {
            "period_days": days,
            "qa_approval_rate": await self.db.get_qa_approval_rate(days),
            "human_approval_rate": await self.db.get_human_approval_rate(days),
            "avg_quality_score": await self.db.get_avg_quality_score(days),
            "revision_count_avg": await self.db.get_avg_revisions(days)
        }
```

**Step 3: Update Orchestrator to Record Metrics**

```python
# When each stage completes, record metrics
import time

async def _run_research(self, topic: str, keywords: list):
    start = time.time()

    try:
        result = await research_agent.execute(topic, keywords)
        duration_ms = int((time.time() - start) * 1000)

        await self.analytics.record_stage_execution(
            task_id=self.current_task_id,
            stage="research",
            agent_name="research",
            duration_ms=duration_ms,
            tokens_used=result.tokens_used,
            model_used=result.model,
            cost=result.cost,
            success=True
        )

        return result
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)

        await self.analytics.record_stage_execution(
            task_id=self.current_task_id,
            stage="research",
            agent_name="research",
            duration_ms=duration_ms,
            tokens_used=0,
            model_used="unknown",
            cost=0,
            success=False,
            error=str(e)
        )
        raise
```

**Step 4: Create Analytics Endpoints**

```python
# routes/analytics_routes.py
@app.get("/api/analytics/agents/{agent_name}")
async def get_agent_metrics(agent_name: str, days: int = 30):
    """Get agent performance metrics"""
    return await analytics.get_agent_metrics(agent_name, days)

@app.get("/api/analytics/pipeline/timing")
async def get_pipeline_timing(days: int = 30):
    """Get average execution time per pipeline stage"""
    return await analytics.get_pipeline_timing(days)

@app.get("/api/analytics/costs")
async def get_cost_breakdown(days: int = 30):
    """Get cost breakdown"""
    return await analytics.get_cost_breakdown(days)

@app.get("/api/analytics/quality")
async def get_quality_metrics(days: int = 30):
    """Get quality metrics"""
    return await analytics.get_quality_metrics(days)

@app.get("/api/analytics/dashboard")
async def get_dashboard(days: int = 30):
    """Get complete analytics dashboard"""
    return {
        "agent_metrics": {agent: await analytics.get_agent_metrics(agent, days)
                         for agent in ["research", "creative", "qa", "image", "publish"]},
        "pipeline_timing": await analytics.get_pipeline_timing(days),
        "costs": await analytics.get_cost_breakdown(days),
        "quality": await analytics.get_quality_metrics(days)
    }
```

### Priority: **MEDIUM** 🟡

- **Why:** Essential for optimization and cost control
- **When:** Phase 6 or later
- **Dependencies:** PostgreSQL, metrics collection in orchestrator

---

## 📋 Gap 6: Workflow Visualization & History

### Current State

**Basic tracking only:**

```
POST /api/workflows/history
→ Records workflow execution
→ No timeline visualization
→ No error recovery recommendations
→ No ability to replay workflows
```

### What's Missing

```
Execution Timeline:
    10:00:00 - STARTED: "Generate blog post about AI"
    10:00:05 - STAGE: research (completed in 5s)
    10:00:25 - STAGE: creative (completed in 20s)
    10:00:45 - STAGE: qa (completed in 20s)
    10:00:55 - STAGE: images (completed in 10s)
    10:01:00 - STAGE: publish (completed in 5s)
    10:01:00 - COMPLETED: Content ready for approval

Error Recovery:
    If any stage fails, show:
    - "What went wrong"
    - "Likely cause"
    - "Recommended fix"
    - "Retry this stage" button

Replay/Retry:
    Ability to:
    - Retry from specific stage
    - Use different parameters
    - Skip approved stages
```

### Implementation Difficulty: **LOW-MEDIUM** ⏱️ 3-4 hours

### Recommended Implementation

**Already implemented in Phase 5:**

- `workflow_history.py` routes
- `services/workflow_history.py` service
- PostgreSQL persistence

**What to complete:**

1. Add visualization timeline formatting
2. Add error context and recommendations
3. Add replay/retry UI endpoints
4. Add stage-specific retry capability

---

## 📋 Gap 7: Multi-Tenant Support (If Needed)

### Current State

**No tenant isolation:**

```
All users can access all content
No account separation
No per-user billing
```

### What's Missing

```
If multi-tenant needed:
    - User/account filtering on all queries
    - Per-account API keys
    - Per-account rate limits
    - Separate databases per tenant (optional)
    - Per-account billing
```

### Priority: **LOW** 🟢 (Unless explicitly required)

- **Why:** Only needed if multiple users/organizations
- **When:** Future phases if needed
- **Dependencies:** User/account model

---

## 🚀 Implementation Priority Matrix

| Gap               | Priority  | Difficulty | Time  | Value  | Start Phase |
| ----------------- | --------- | ---------- | ----- | ------ | ----------- |
| Social Publishing | MEDIUM 🟡 | MEDIUM     | 4-6h  | HIGH   | Phase 6     |
| WebSocket Updates | HIGH 🔴   | MEDIUM     | 3-4h  | HIGH   | Phase 6     |
| Error Recovery    | HIGH 🔴   | MEDIUM     | 4-5h  | HIGH   | Phase 6     |
| Approval RBAC     | MEDIUM 🟡 | LOW        | 2-3h  | MEDIUM | Phase 6     |
| Analytics         | MEDIUM 🟡 | MEDIUM     | 6-8h  | HIGH   | Phase 6+    |
| Workflow Viz      | MEDIUM 🟡 | LOW        | 2-3h  | MEDIUM | Phase 6+    |
| Multi-Tenant      | LOW 🟢    | HIGH       | 8-10h | LOW    | Later       |

---

## 📝 Recommended Phase 6 Roadmap

```
Week 1: Error Recovery & WebSocket
    - Implement retry logic with circuit breaker
    - Add WebSocket support for real-time updates
    - Update frontend to use WebSocket

Week 2: Approval System & Analytics Foundation
    - Add RBAC and audit trail
    - Create analytics schema
    - Update orchestrator to record metrics

Week 3: Social Publishing
    - Create social media client abstraction
    - Add OAuth integrations
    - Implement posting and scheduling

Week 4: Polish & Testing
    - Complete analytics dashboard
    - Test all pipelines end-to-end
    - Documentation and cleanup
```

---

**Status:** Complete  
**Next Step:** Choose which gaps to address in Phase 6  
**Effort Estimate:** Total 24-30 hours for all gaps

Ready to prioritize? Choose 2-3 gaps to start with! 🚀
