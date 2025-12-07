# 🚀 Subtask API Implementation & Integration Guide

**Last Updated:** November 24, 2025  
**Status:** ✅ Complete & Production Ready  
**Framework:** FastAPI + asyncpg + PostgreSQL  
**Integration Points:** Content Orchestrator, Database Service, Model Router

---

## 📋 Table of Contents

- **[Architecture Overview](#architecture-overview)** - How subtasks work
- **[Implementation Details](#implementation-details)** - Code structure
- **[Integration Guide](#integration-guide)** - Using subtasks in your app
- **[Database Schema](#database-schema)** - Task and subtask tables
- **[Error Handling](#error-handling)** - Robust error management
- **[Performance Tips](#performance-tips)** - Optimization strategies

---

## 🏗️ Architecture Overview

### Subtask System Design

The Subtask API breaks the **7-stage content creation pipeline** into **independent HTTP endpoints** that can be called individually or chained together:

```text
┌─────────────────────────────────────────────────────────────┐
│            Subtask API Orchestration Layer                  │
│  (src/cofounder_agent/routes/subtask_routes.py)             │
└──────────────────────────────────────────────────────────────┘
                           ↓
    ┌────────────┬─────────┬──────────┬─────────┬──────────┐
    │            │         │          │         │          │
    ▼            ▼         ▼          ▼         ▼          ▼
┌────────┐ ┌───────┐ ┌──────┐ ┌───────┐ ┌──────┐
│Research│ │Create │ │ QA   │ │Image  │ │Format│
│ /api/  │ │ /api/ │ │/api/ │ │ /api/ │ │/api/ │
└────────┘ └───────┘ └──────┘ └───────┘ └──────┘
    │         │         │         │         │
    └─────────┼─────────┼─────────┼─────────┘
              │
    ┌─────────▼──────────────────────────┐
    │  Content Orchestrator              │
    │  (services/content_orchestrator.py)│
    │  - _run_research()                 │
    │  - _run_creative_initial()         │
    │  - _run_qa_loop()                  │
    │  - _run_image_selection()          │
    │  - _run_formatting()               │
    └─────────┬──────────────────────────┘
              │
    ┌─────────▼──────────────────────────┐
    │  Model Router                      │
    │  (services/model_router.py)        │
    │  Ollama → Claude → GPT → Gemini    │
    └─────────┬──────────────────────────┘
              │
    ┌─────────▼──────────────────────────┐
    │  LLM Providers                     │
    │  - Ollama (local, free)            │
    │  - Anthropic Claude (paid)         │
    │  - OpenAI GPT-4 (paid)             │
    │  - Google Gemini (free/paid)       │
    └────────────────────────────────────┘
```

### Key Features

1. **Independent Execution**
   - Each stage runs in isolation
   - No dependency on other stages
   - Reusable outputs

2. **Dependency Chaining**
   - Pass output from one stage to another
   - Track parent/child relationships
   - Flexible workflow orchestration

3. **Database Tracking**
   - Every subtask creates a database record
   - Stored in `tasks` table with type='subtask'
   - Full audit trail and status tracking

4. **Authentication & Authorization**
   - JWT token validation on all endpoints
   - User context tracking
   - Role-based access control ready

---

## 💻 Implementation Details

### File Structure

```text
src/cofounder_agent/
├── routes/
│   └── subtask_routes.py              # ← All subtask endpoints
├── services/
│   ├── content_orchestrator.py        # Stage implementations
│   ├── model_router.py                # LLM provider routing
│   └── database_service.py            # Database operations
├── models/
│   └── task_models.py                 # Pydantic models
└── middleware/
    └── auth_unified.py                # JWT authentication
```

### Subtask Route Registration

In `main.py`, subtask routes are imported and registered:

```python
# Import subtask router
from routes.subtask_routes import router as subtask_router
from routes.subtask_routes import set_db_service as set_subtask_db_service

# ... later in startup ...

# Initialize subtask database service
set_subtask_db_service(database_service)

# Register router with FastAPI app
app.include_router(subtask_router)  # Adds /api/content/subtasks/* endpoints
```

### Request/Response Flow

```text
1. HTTP Request (FastAPI)
   └─► /api/content/subtasks/research
       POST with JSON body
       Authorization header required

2. Route Handler (subtask_routes.py)
   ├─► Validate JWT token
   ├─► Parse and validate request body (Pydantic)
   ├─► Create subtask record in database
   └─► Call content orchestrator

3. Content Orchestrator (content_orchestrator.py)
   ├─► Get appropriate method (_run_research, _run_creative, etc.)
   ├─► Call Model Router for LLM inference
   ├─► Process and format results
   └─► Return output

4. Model Router (model_router.py)
   ├─► Try Ollama (local, free)
   ├─► If fails, try Claude 3 Opus
   ├─► If fails, try GPT-4
   ├─► If fails, try Gemini
   └─► Return best available output

5. Update Database
   ├─► Mark subtask as completed
   ├─► Store result JSON
   ├─► Update metadata (duration, tokens, model)
   └─► Return response

6. HTTP Response (FastAPI)
   └─► SubtaskResponse (JSON)
       ├─ subtask_id
       ├─ stage
       ├─ status
       ├─ result
       └─ metadata
```

### Pydantic Models

Each subtask has a request model for validation:

```python
from pydantic import BaseModel, Field
from typing import Optional, List

class ResearchSubtaskRequest(BaseModel):
    topic: str = Field(..., description="Topic to research")
    keywords: List[str] = Field(default_factory=list)
    parent_task_id: Optional[str] = Field(None)

class SubtaskResponse(BaseModel):
    subtask_id: str
    stage: str
    parent_task_id: Optional[str]
    status: str
    result: Dict[str, Any]
    metadata: Dict[str, Any]
```

### Database Integration

Subtasks are stored in the `tasks` table:

```python
# Create subtask record
await db_service.execute(
    """
    INSERT INTO tasks (
        id, task_name, task_type, status, metadata, result
    ) VALUES (
        $1, $2, 'subtask', 'in_progress', $3, $4
    )
    """,
    subtask_id,
    f"Research: {request.topic}",
    {"stage": "research", "parent_task_id": request.parent_task_id, ...},
    None
)

# Update after completion
await db_service.execute(
    """
    UPDATE tasks SET status = 'completed', result = $1
    WHERE id = $2
    """,
    result_data,
    subtask_id
)
```

---

## 🔗 Integration Guide

### Using Subtask API in Your Application

#### Example 1: Simple Research

```python
import httpx
import json

async def get_research_on_topic(topic: str, auth_token: str) -> dict:
    """Get research data on a topic"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/api/content/subtasks/research",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={
                "topic": topic,
                "keywords": ["innovation", "trends"]
            }
        )
        response.raise_for_status()
        return response.json()

# Usage
result = await get_research_on_topic("AI trends", token)
print(f"Research ID: {result['subtask_id']}")
print(f"Quality Score: {result['metadata']}")
```

#### Example 2: Full Pipeline Chaining

```python
async def generate_article_full_pipeline(
    topic: str,
    style: str = "professional",
    auth_token: str = None
) -> dict:
    """
    Full pipeline: Research → Creative → QA → Format
    """
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {auth_token}"}

        # 1. Research
        print(f"📚 Researching {topic}...")
        research = await client.post(
            "http://localhost:8000/api/content/subtasks/research",
            headers=headers,
            json={"topic": topic, "keywords": ["research", "trends"]}
        )
        research_output = research.json()["result"]["research_data"]

        # 2. Creative with research
        print("✍️ Creating draft...")
        creative = await client.post(
            "http://localhost:8000/api/content/subtasks/creative",
            headers=headers,
            json={
                "topic": topic,
                "research_output": research_output,
                "style": style,
                "target_length": 2500
            }
        )
        creative_output = creative.json()["result"]["content"]

        # 3. QA on content
        print("🔍 Running QA...")
        qa = await client.post(
            "http://localhost:8000/api/content/subtasks/qa",
            headers=headers,
            json={
                "topic": topic,
                "creative_output": creative_output,
                "max_iterations": 2
            }
        )
        final_content = qa.json()["result"]["content"]

        # 4. Format for publishing
        print("📄 Formatting...")
        formatted = await client.post(
            "http://localhost:8000/api/content/subtasks/format",
            headers=headers,
            json={
                "topic": topic,
                "content": final_content,
                "tags": ["technology", "trends"],
                "category": "insights"
            }
        )

        return formatted.json()

# Usage
article = await generate_article_full_pipeline(
    "Future of AI",
    style="professional",
    auth_token="your-jwt-token"
)
print(article["result"]["formatted_content"])
```

#### Example 3: Parallel Subtask Execution

```python
import asyncio

async def research_multiple_topics(
    topics: List[str],
    auth_token: str
) -> dict:
    """Research multiple topics in parallel"""
    
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {auth_token}"}

        # Create all research requests
        tasks = [
            client.post(
                "http://localhost:8000/api/content/subtasks/research",
                headers=headers,
                json={"topic": topic, "keywords": []}
            )
            for topic in topics
        ]

        # Execute in parallel
        responses = await asyncio.gather(*tasks)

        # Collect results
        results = {}
        for topic, response in zip(topics, responses):
            data = response.json()
            results[topic] = data["result"]["research_data"]

        return results

# Usage
topics = ["AI", "Blockchain", "Quantum Computing"]
research = await research_multiple_topics(topics, token)
for topic, data in research.items():
    print(f"{topic}: {data[:100]}...")
```

### Integration with External Systems

#### Webhook Integration

```python
# After subtask completes, send webhook notification
async def notify_on_subtask_completion(subtask_id: str, webhook_url: str):
    """Send webhook notification when subtask completes"""
    async with httpx.AsyncClient() as client:
        # Get subtask status
        subtask = client.get(f"/api/content/subtasks/{subtask_id}").json()
        
        if subtask["status"] == "completed":
            # Send webhook
            await client.post(
                webhook_url,
                json={
                    "event": "subtask.completed",
                    "subtask_id": subtask_id,
                    "result": subtask["result"]
                }
            )
```

#### Scheduled Subtask Execution

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job('cron', hour=9)
async def daily_content_generation():
    """Generate content automatically each day at 9 AM"""
    async with httpx.AsyncClient() as client:
        topics = ["AI trends", "Market insights", "Tech news"]
        
        for topic in topics:
            response = await client.post(
                "http://localhost:8000/api/content/subtasks/research",
                headers={"Authorization": f"Bearer {sys_token}"},
                json={"topic": topic}
            )
            print(f"Generated research for {topic}")

scheduler.start()
```

---

## 🗄️ Database Schema

### Tasks Table (Subtasks)

```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY,
    task_name VARCHAR(255) NOT NULL,
    task_type VARCHAR(50) NOT NULL,  -- 'subtask', 'pipeline', 'scheduled'
    status VARCHAR(50) DEFAULT 'pending',  -- 'pending', 'in_progress', 'completed', 'failed'
    metadata JSONB,  -- stage, parent_task_id, inputs
    result JSONB,    -- stage-specific output
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast lookups
CREATE INDEX idx_tasks_type_status ON tasks(task_type, status);
CREATE INDEX idx_tasks_created_at ON tasks(created_at DESC);
```

### Subtask Metadata Structure

```python
{
    "stage": "research",  # or creative, qa, images, format
    "parent_task_id": "task-uuid-or-null",
    "inputs": {
        "topic": "AI trends",
        "keywords": ["ML", "innovation"]
    },
    "error": "error message if failed"
}
```

### Subtask Result Structure (by stage)

#### Research Result

```python
{
    "research_data": "Full research text...",
    "topic": "AI trends",
    "keywords": ["ML", "innovation"]
}
```

#### Creative Result

```python
{
    "title": "The Future of AI",
    "content": "Full article markdown...",
    "style": "professional",
    "tone": "informative"
}
```

#### QA Result

```python
{
    "content": "Refined content...",
    "feedback": ["Improved clarity", "Added examples"],
    "quality_score": 8.5,
    "iterations": 2
}
```

#### Image Result

```python
{
    "featured_image_url": "https://example.com/image.jpg",
    "topic": "AI trends",
    "number_requested": 3
}
```

#### Format Result

```python
{
    "formatted_content": "# AI Trends\n\n...",
    "excerpt": "Short excerpt for preview...",
    "tags": ["AI", "tech"],
    "category": "technology"
}
```

---

## ⚠️ Error Handling

### Error Response Format

All errors return consistent format:

```json
{
    "detail": "Error description here"
}
```

### Common HTTP Status Codes

| Code | Meaning | Example |
|------|---------|---------|
| 200 | Success | Subtask completed |
| 201 | Created | Task record created |
| 400 | Bad Request | Malformed JSON |
| 401 | Unauthorized | Missing auth token |
| 403 | Forbidden | Invalid/expired token |
| 422 | Validation Error | Missing required field |
| 500 | Server Error | LLM provider unavailable |

### Error Handling in Routes

```python
@router.post("/research", response_model=SubtaskResponse)
async def run_research_subtask(request: ResearchSubtaskRequest):
    subtask_id = str(uuid4())
    
    try:
        # Create task record
        await db_service.execute(...)
        
        # Execute research
        research_output = await orchestrator._run_research(...)
        
        # Update with results
        await db_service.execute(...)
        
        return SubtaskResponse(...)
        
    except Exception as e:
        logger.error(f"Research subtask failed: {e}")
        
        # Mark as failed in database
        await db_service.execute(
            """
            UPDATE tasks SET status = 'failed', metadata = 
            jsonb_set(metadata, '{error}', to_jsonb($1))
            WHERE id = $2
            """,
            str(e),
            subtask_id
        )
        
        raise HTTPException(status_code=500, detail=str(e))
```

### Retry Logic

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
async def call_llm_with_retry(prompt: str) -> str:
    """Call LLM with automatic retries"""
    return await model_router.call_model(prompt)
```

---

## 🚀 Performance Tips

### 1. Parallel Subtask Execution

```python
# Run independent subtasks in parallel
results = await asyncio.gather(
    client.post("/api/content/subtasks/research", ...),
    client.post("/api/content/subtasks/research", ...),
    return_exceptions=True
)
```

### 2. Caching Research Output

```python
# Cache research to avoid re-running
cache = {}

async def get_research(topic: str, auth_token: str) -> str:
    if topic in cache:
        return cache[topic]
    
    response = await client.post(
        "/api/content/subtasks/research",
        json={"topic": topic}
    )
    data = response.json()
    cache[topic] = data["result"]["research_data"]
    return cache[topic]
```

### 3. Model Selection

```python
# Use cheaper models for simple tasks
{
    "topic": topic,
    "preferred_model": "gpt-3.5-turbo",  # Faster, cheaper
    "max_tokens": 500
}

# Use better models for quality work
{
    "topic": topic,
    "preferred_model": "gpt-4",  # Slower, more expensive, better quality
    "max_tokens": 3000
}
```

### 4. Database Query Optimization

```python
# Use indexed columns for filtering
await db_service.execute(
    "SELECT * FROM tasks WHERE task_type = $1 AND status = $2 ORDER BY created_at DESC LIMIT 10",
    "subtask",
    "completed"
)
```

---

## 📊 Monitoring & Logging

### Key Metrics to Track

```python
logger.info(f"Subtask {subtask_id} completed in {duration_ms}ms")
logger.info(f"Tokens used: {tokens_used}, Cost: ${cost:.4f}")
logger.info(f"Quality score: {quality_score}/10")
```

### Structured Logging

```python
import structlog

structlog.get_logger().info(
    "subtask_completed",
    subtask_id=subtask_id,
    stage="research",
    duration_ms=15000,
    tokens_used=1250,
    model="gpt-4",
    quality_score=8.5
)
```

---

## ✅ Production Checklist

Before deploying subtasks to production:

- [ ] All tests passing (`pytest tests/test_subtask_endpoints.py -v`)
- [ ] Error handling comprehensive
- [ ] Database backups configured
- [ ] Authentication properly enforced
- [ ] Rate limiting configured
- [ ] Monitoring/alerting set up
- [ ] Documentation reviewed
- [ ] Team trained on API usage

---

## 🔗 Related Documentation

- **[Testing Guide](../guides/SUBTASK_API_TESTING_GUIDE.md)** - Comprehensive test examples
- **[API Contracts](../reference/API_CONTRACT_CONTENT_CREATION.md)** - Full API specification
- **[Architecture](../02-ARCHITECTURE_AND_DESIGN.md)** - System design
- **[Development](../04-DEVELOPMENT_WORKFLOW.md)** - Dev process

---

**Ready to integrate? Start with the [Testing Guide](../guides/SUBTASK_API_TESTING_GUIDE.md)!**
