# Unified Task Endpoints - Official Reference

**Last Updated:** January 17, 2026  
**Status:** ✅ CONSOLIDATED - Single `/api/tasks` endpoint for all content types

---

## Endpoint Overview

**Single unified endpoint for ALL task types:**

```
POST /api/tasks       - Create a new task
GET /api/tasks        - List all tasks
GET /api/tasks/{id}   - Get task details and results
DELETE /api/tasks/{id} - Delete a task
```

---

## Complete API Reference

### 1. Create Task (Any Type)

**Endpoint:**

```
POST /api/tasks
```

**Supported Task Types:**

- `blog_post` - Blog content generation
- `social_media` - Multi-platform social content
- `email` - Email campaign generation
- `newsletter` - Newsletter content
- `business_analytics` - Business metrics analysis
- `data_retrieval` - Data extraction
- `market_research` - Market intelligence
- `financial_analysis` - Financial analysis

**Request (Blog Post Example):**

```json
{
  "task_type": "blog_post",
  "topic": "AI and Machine Learning",
  "style": "technical",
  "tone": "professional",
  "target_length": 1500,
  "generate_featured_image": true,
  "tags": ["ai", "machine-learning"],
  "quality_preference": "balanced",
  "models_by_phase": {
    "research": "ollama/mistral",
    "creative": "ollama/mistral",
    "qa": "ollama/mistral"
  }
}
```

**Response (201 Created):**

```json
{
  "id": "6d5af4d2-5095-4149-b2f3-cf006cb3fde9",
  "task_type": "blog_post",
  "status": "pending",
  "created_at": "2026-01-17T07:20:57.317843+00:00",
  "message": "Blog post task created and queued"
}
```

**Processing Pipeline:**
The task will automatically proceed through:

1. Research phase (gather information)
2. Creative phase (generate content)
3. QA phase (critique and refine)
4. Image phase (fetch featured image)
5. Format phase (SEO optimization)

---

### 2. List Tasks

**Endpoint:**

```
GET /api/tasks?limit=20&offset=0&status=completed
```

**Query Parameters:**

- `limit` (int, default 20): Number of results (1-100)
- `offset` (int, default 0): Pagination offset
- `status` (string, optional): Filter by status
  - Values: `pending`, `generating`, `completed`, `failed`, `awaiting_approval`, `approved`, `published`
- `task_type` (string, optional): Filter by type
  - Values: `blog_post`, `social_media`, `email`, `newsletter`, etc.

**Response (200 OK):**

```json
{
  "tasks": [
    {
      "id": "75",
      "task_id": "6d5af4d2-5095-4149-b2f3-cf006cb3fde9",
      "task_type": "blog_post",
      "topic": "AI and Machine Learning",
      "status": "completed",
      "approval_status": "pending_human_review",
      "content": "## AI and Machine Learning...",
      "featured_image_url": "https://...",
      "quality_score": 6.36,
      "seo_title": "AI and Machine Learning",
      "created_at": "2026-01-17T07:20:57.317843+00:00"
    }
  ],
  "total": 127,
  "limit": 20,
  "offset": 0
}
```

---

### 3. Get Task Details

**Endpoint:**

```
GET /api/tasks/{task_id}
```

**Path Parameters:**

- `task_id` (string, required): Task UUID or numeric ID

**Response (200 OK):**

```json
{
  "id": "6d5af4d2-5095-4149-b2f3-cf006cb3fde9",
  "task_type": "blog_post",
  "status": "completed",
  "content": "## Full blog content...",
  "featured_image_url": "https://images.pexels.com/...",
  "quality_score": 6.36,
  "seo_title": "AI and Machine Learning",
  "seo_description": "Comprehensive guide to AI and ML",
  "seo_keywords": ["ai", "machine-learning", "algorithms"],
  "created_at": "2026-01-17T07:20:57.317843+00:00"
}
```

---

### 4. Update Task Status (Approval Workflow)

**Endpoint:**

```
PUT /api/tasks/{task_id}/status/validated
```

**Path Parameters:**

- `task_id` (string, required): Task ID

**Request Body:**

```json
{
  "status": "approved",
  "updated_by": "user@example.com",
  "reason": "Reviewed and approved",
  "metadata": {
    "feedback": "Great content, minor edits needed",
    "reviewer_notes": "..."
  }
}
```

**Valid Status Transitions:**

- `awaiting_approval` → `approved` (Approve)
- `awaiting_approval` → `rejected` (Reject)
- `approved` → `published` (Publish)
- `rejected` → `awaiting_approval` (Re-submit)

**Response (200 OK):**

```json
{
  "success": true,
  "task_id": "6d5af4d2-5095-4149-b2f3-cf006cb3fde9",
  "message": "Status changed: awaiting_approval → approved",
  "updated_by": "user@example.com",
  "timestamp": "2026-01-17T02:06:50.311571+00:00"
}
```

---

### 5. Delete Task

**Endpoint:**

```
DELETE /api/tasks/{task_id}
```

**Response (204 No Content):**

```
Task successfully deleted
```

---

## Task Status State Machine

```
pending
  ↓
generating
  ├─ awaiting_approval (content ready, needs human review)
  │  ├─ approved (human approved)
  │  │  └─ published (live on CMS)
  │  └─ rejected (human rejected)
  │
  └─ completed (task finished without approval)
```

---

## Quality & Cost Tracking

Each completed task includes:

- `quality_score` (1-10): Content quality evaluation
- `estimated_cost`: Estimated AI cost
- `cost_breakdown`: Cost by phase
- `model_used`: Which AI model generated content
- `featured_image_url`: Auto-sourced from Pexels

---

## Backend Implementation

**File:** `src/cofounder_agent/routes/task_routes.py`  
**Prefix:** `/api/tasks`  
**Handler:** Unified task router routes to appropriate handler based on `task_type`

**Router Logic:**

```python
@router.post("")
async def create_task(request: UnifiedTaskRequest):
    if request.task_type == "blog_post":
        return await _handle_blog_post_creation(request, ...)
    elif request.task_type == "social_media":
        return await _handle_social_media_creation(request, ...)
    # ... other types
```

---

## Frontend Integration

**Service:** `web/oversight-hub/src/services/cofounderAgentClient.js`  
**Function:** `getTasks(limit, offset)`  
**Endpoint Called:** `GET /api/tasks?limit={limit}&offset={offset}`

---

## Troubleshooting

### Issue: Tasks not progressing past "pending"

**Cause:** Background task executor not running  
**Solution:** Check backend logs for asyncio task execution errors

### Issue: Content generation is slow

**Cause:** Using expensive models (GPT-4, Claude Opus) for research phase  
**Solution:** Set `quality_preference: "fast"` or specify cheaper models in `models_by_phase`

---

## See Also

- [Task Status State Machine](TASK_STATUS_STATE_MACHINE.md)
- [Content Generation Pipeline](docs/05-AI_AGENTS_AND_INTEGRATION.md)
- [Database Schema](docs/reference/)
