# 📋 API Contract Reference

**Version:** 1.0
**Base URL:** `https://api.glad-labs.com` (or `http://localhost:8000` local)  
**Status:** ✅ Production Ready

---

## 📌 Overview

Complete documentation of all REST API endpoints for the Glad Labs Co-Founder system. This reference includes request/response formats, status codes, and example usage.

### API Principles

- **RESTful:** Standard HTTP methods and status codes
- **JSON:** All requests/responses in JSON format
- **Versioning:** No version prefix — all endpoints at `/api/{resource}`
- **Authentication:** Bearer token in Authorization header
- **Rate Limiting:** 100 requests/minute per API key
- **Pagination:** Limit/offset for list endpoints

---

## 🔐 Authentication

### GitHub OAuth + JWT

All protected requests require a JWT token obtained via GitHub OAuth:

```bash
curl -H "Authorization: Bearer <JWT_TOKEN>" \
     https://api.glad-labs.com/api/tasks
```

### Authentication Flow

1. User initiates GitHub OAuth via `GET /api/auth/login`
2. GitHub redirects back with auth code to `/api/auth/callback`
3. Backend validates and issues a JWT token
4. Client includes JWT in `Authorization: Bearer <token>` header

### Development Mode

When `DEVELOPMENT_MODE=true`, send `Authorization: Bearer dev-token` to bypass OAuth.

### Token Format

- **Type:** JWT Bearer token
- **Signing:** HS256 with `JWT_SECRET_KEY`
- **Revocation:** Token blocklist checked on each request

---

## 📚 Core Endpoints

### System Health

#### GET `/api/health`

Check if system is operational.

**Request:**

```bash
curl https://api.glad-labs.com/api/health
```

**Response (200 OK):**

```json
{
  "status": "healthy",
  "service": "glad-labs-cofounder",
  "version": "0.1.0",
  "timestamp": "2026-03-26T10:30:00Z",
  "components": {
    "database": "connected",
    "task_executor": "running"
  }
}
```

**Status Codes:**

- `200 OK` - System healthy
- `503 Service Unavailable` - System degraded

---

#### GET `/api/metrics`

System performance metrics.

**Request:**

```bash
curl https://api.glad-labs.com/api/metrics
```

**Response (200 OK):**

```json
{
  "uptime_seconds": 86400,
  "requests_total": 50000,
  "requests_failed": 25,
  "avg_response_time_ms": 145,
  "p95_response_time_ms": 250,
  "p99_response_time_ms": 500,
  "error_rate_percent": 0.05,
  "active_tasks": 12,
  "memory_usage_percent": 45,
  "database_connections": 8
}
```

---

### Task Management

#### POST `/api/tasks`

Create a new task for agent execution. Routes to the appropriate handler based on `task_type`.

**Request:**

```bash
curl -X POST https://api.glad-labs.com/api/tasks \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "blog_post",
    "topic": "AI Trends in Healthcare 2025",
    "style": "technical",
    "tone": "professional",
    "target_length": 2000,
    "generate_featured_image": true,
    "category": "technology",
    "target_audience": "Healthcare professionals",
    "primary_keyword": "AI healthcare",
    "tags": ["AI", "Healthcare"],
    "models_by_phase": {
      "research": "ultra_cheap",
      "draft": "premium",
      "assess": "cheap"
    },
    "quality_preference": "balanced",
    "metadata": {}
  }'
```

**Required Fields:**

- `topic` (string, 3-200 chars) - Task topic/subject/query

**Common Optional Fields:**

- `task_type` - One of: `blog_post` (default), `social_media`, `email`, `newsletter`, `business_analytics`, `data_retrieval`, `market_research`, `financial_analysis`
- `category` (string, default: "general") - Content category
- `target_audience` (string, default: "General") - Target audience
- `primary_keyword` (string) - Primary SEO keyword
- `models_by_phase` (object) - Per-phase model selection (research, outline, draft, assess, refine, finalize)
- `model_selections` (object) - DEPRECATED alias for `models_by_phase`
- `quality_preference` - One of: `fast`, `balanced` (default), `quality`
- `description` (string, max 1000 chars) - Human-written task description
- `metadata` (object) - Additional metadata

**Content Task Fields (blog_post, social_media, email, newsletter):**

- `style` - One of: `technical`, `narrative` (default), `listicle`, `educational`, `thought-leadership`
- `tone` - One of: `professional` (default), `casual`, `academic`, `inspirational`
- `target_length` (int, 200-5000, default: 1500) - Target word count (blog_post)
- `generate_featured_image` (bool, default: true) - Search for featured image (blog_post)
- `tags` (array, max 10) - Tags for categorization
- `platforms` (array) - Target platforms (social_media only)

**Analytics Task Fields (business_analytics):**

- `metrics` (array) - Metrics to analyze (revenue, churn, conversion_rate, etc.)
- `time_period` (string) - Analysis period (last_month, last_quarter, ytd, custom)
- `business_context` (object) - Industry, size, goals context

**Response (201 Created):**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "task_type": "blog_post",
  "topic": "AI Trends in Healthcare 2025",
  "status": "pending",
  "created_at": "2026-03-26T10:30:00+00:00",
  "message": "Blog post task created and queued"
}
```

**Status Codes:**

- `201 Created` - Task created successfully
- `400 Bad Request` - Unknown task_type
- `401 Unauthorized` - Missing/invalid authentication
- `422 Unprocessable Entity` - Validation error (e.g., topic too short)
- `429 Too Many Requests` - Rate limited (10/minute)

---

#### GET `/api/tasks/{task_id}`

Get details about a specific task. Returns a `UnifiedTaskResponse`.

**Request:**

```bash
curl https://api.glad-labs.com/api/tasks/550e8400-e29b-41d4-a716-446655440000 \
  -H "Authorization: Bearer YOUR_KEY"
```

**Response (200 OK):**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "task_name": "Blog Post: AI Trends in Healthcare 2025",
  "task_type": "blog_post",
  "status": "completed",
  "approval_status": "approved",
  "publish_status": "draft",
  "topic": "AI Trends in Healthcare 2025",
  "primary_keyword": "AI healthcare",
  "target_audience": "Healthcare professionals",
  "category": "technology",
  "style": "technical",
  "tone": "professional",
  "target_length": 2000,
  "quality_preference": "balanced",
  "models_by_phase": {
    "research": "ultra_cheap",
    "draft": "premium",
    "assess": "cheap"
  },
  "estimated_cost": 0.0125,
  "cost_breakdown": {
    "research": 0.001,
    "draft": 0.005,
    "assess": 0.003,
    "total": 0.0125
  },
  "content": "# AI Trends in Healthcare 2025\n\nContent here...",
  "excerpt": "Exploring how AI is transforming healthcare...",
  "featured_image_url": "https://images.pexels.com/...",
  "quality_score": 94.2,
  "seo_title": "AI Trends in Healthcare 2025 | Your Blog",
  "seo_description": "Discover how AI is revolutionizing healthcare",
  "seo_keywords": ["AI", "healthcare", "future"],
  "created_at": "2026-03-26T10:30:00Z",
  "updated_at": "2026-03-26T10:35:45Z",
  "started_at": "2026-03-26T10:31:00Z",
  "completed_at": "2026-03-26T10:35:00Z",
  "agent_id": "content-agent",
  "error_message": null,
  "error_details": null,
  "metadata": {}
}
```

**Status Codes:**

- `200 OK` - Task found
- `401 Unauthorized` - Missing/invalid authentication
- `403 Forbidden` - User does not own this task
- `404 Not Found` - Task doesn't exist

---

#### GET `/api/tasks`

List all tasks with filtering and pagination.

**Request:**

```bash
curl "https://api.glad-labs.com/api/tasks?status=completed&limit=10&offset=0" \
  -H "Authorization: Bearer YOUR_KEY"
```

**Query Parameters:**

- `status` - Filter by status (queued, pending, running, completed, failed)
- `category` - Filter by category
- `search` - Keyword search across task name, topic, and category (trigram-indexed, max 200 chars)
- `limit` - Results per page (default: 20, max: 100)
- `offset` - Pagination offset (default: 0)

**Response (200 OK):**

```json
{
  "tasks": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "task_name": "Blog Post: AI Trends in Healthcare 2025",
      "task_type": "blog_post",
      "status": "completed",
      "topic": "AI Trends in Healthcare 2025",
      "category": "technology",
      "created_at": "2026-03-26T10:30:00Z",
      "updated_at": "2026-03-26T10:35:45Z"
    },
    {
      "id": "660f9500-f39c-52e5-b827-557766551111",
      "task_name": "Market Research: Competitor Pricing",
      "task_type": "market_research",
      "status": "pending",
      "topic": "Competitor Pricing Strategy",
      "category": "general",
      "created_at": "2026-03-26T09:15:00Z",
      "updated_at": "2026-03-26T09:15:00Z"
    }
  ],
  "total": 245,
  "offset": 0,
  "limit": 10
}
```

---

#### PUT `/api/tasks/{task_id}/status`

Update task status with enterprise-level transition validation and audit trail.

**Request:**

```bash
curl -X PUT https://api.glad-labs.com/api/tasks/550e8400-e29b-41d4-a716-446655440000/status \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "awaiting_approval",
    "reason": "Content generation completed",
    "metadata": {"quality_score": 8.5}
  }'
```

**Request Body:**

- `status` (string, required) - Target status
- `updated_by` (string, optional) - User/system identifier
- `reason` (string, optional) - Change reason for audit trail
- `metadata` (object, optional) - Additional metadata

**Valid Status Transitions:**

```
pending -> in_progress, failed, cancelled
in_progress -> awaiting_approval, failed, on_hold, cancelled
awaiting_approval -> approved, rejected, in_progress, cancelled
approved -> published, on_hold, cancelled
published -> on_hold
failed -> pending, cancelled
on_hold -> in_progress, cancelled
rejected -> in_progress, cancelled
cancelled -> (terminal — no transitions)
```

**Response (200 OK):**

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "old_status": "in_progress",
  "new_status": "awaiting_approval",
  "timestamp": "2026-03-26T10:40:00Z",
  "updated_by": "user@example.com",
  "message": "Status updated successfully: in_progress -> awaiting_approval"
}
```

**Status Codes:**

- `200 OK` - Status updated
- `400 Bad Request` - Invalid task ID format
- `401 Unauthorized` - Missing/invalid authentication
- `403 Forbidden` - User does not own this task
- `404 Not Found` - Task doesn't exist
- `409 Conflict` - Invalid status transition
- `422 Unprocessable Entity` - Invalid status value

---

#### DELETE `/api/tasks/{task_id}`

Soft-delete a task (marks as cancelled, preserves audit trail).

**Request:**

```bash
curl -X DELETE https://api.glad-labs.com/api/tasks/550e8400-e29b-41d4-a716-446655440000 \
  -H "Authorization: Bearer YOUR_KEY"
```

**Response (204 No Content):**

```
(empty response)
```

**Status Codes:**

- `204 No Content` - Task deleted (soft)
- `401 Unauthorized` - Missing/invalid authentication
- `403 Forbidden` - User does not own this task
- `404 Not Found` - Task doesn't exist

---

### Agent Management

#### GET `/api/agents/status`

Get status of all agents.

**Request:**

```bash
curl https://api.glad-labs.com/api/agents/status \
  -H "Authorization: Bearer YOUR_KEY"
```

**Response (200 OK):**

```json
{
  "agents": {
    "content": {
      "status": "active",
      "tasks_completed": 1250,
      "tasks_failed": 2,
      "avg_execution_time_ms": 8500,
      "last_activity": "2025-11-14T10:35:00Z"
    },
    "financial": {
      "status": "active",
      "tasks_completed": 340,
      "tasks_failed": 0,
      "avg_execution_time_ms": 1200,
      "last_activity": "2025-11-14T10:32:00Z"
    },
    "market_insight": {
      "status": "active",
      "tasks_completed": 180,
      "tasks_failed": 1,
      "avg_execution_time_ms": 5600,
      "last_activity": "2025-11-14T10:30:00Z"
    },
    "compliance": {
      "status": "active",
      "tasks_completed": 95,
      "tasks_failed": 0,
      "avg_execution_time_ms": 2100,
      "last_activity": "2025-11-14T10:28:00Z"
    }
  }
}
```

---

#### GET `/api/agents/{agent_name}/status`

Get status of specific agent.

**Request:**

```bash
curl https://api.glad-labs.com/api/agents/content/status \
  -H "Authorization: Bearer YOUR_KEY"
```

**Response (200 OK):**

```json
{
  "agent": "content",
  "status": "active",
  "tasks_completed": 1250,
  "tasks_failed": 2,
  "avg_execution_time_ms": 8500,
  "current_task": {
    "id": "task_xyz789",
    "progress_percent": 75,
    "elapsed_ms": 6375,
    "estimated_remaining_ms": 2125
  },
  "last_activity": "2025-11-14T10:35:00Z",
  "health": {
    "cpu_percent": 45,
    "memory_mb": 512,
    "uptime_seconds": 86400
  }
}
```

---

### Model Management

#### GET `/api/models`

List available AI models and providers.

**Request:**

```bash
curl https://api.glad-labs.com/api/models \
  -H "Authorization: Bearer YOUR_KEY"
```

**Response (200 OK):**

```json
{
  "models": {
    "ollama": {
      "provider": "Ollama",
      "status": "connected",
      "models": ["mistral", "llama3.2", "phi"],
      "current": "mistral",
      "cost_per_1k_tokens": 0
    },
    "anthropic": {
      "provider": "Anthropic",
      "status": "connected",
      "models": ["claude-opus", "claude-sonnet", "claude-haiku"],
      "current": "claude-opus",
      "cost_per_1k_tokens": 0.015
    },
    "openai": {
      "provider": "OpenAI",
      "status": "connected",
      "models": ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"],
      "current": "gpt-4",
      "cost_per_1k_tokens": 0.03
    },
    "google": {
      "provider": "Google",
      "status": "connected",
      "models": ["gemini-pro", "gemini-pro-vision"],
      "current": "gemini-pro",
      "cost_per_1k_tokens": 0.0005
    }
  },
  "fallback_chain": ["ollama:mistral", "claude-opus", "gpt-4", "gemini-pro"],
  "current_provider": "ollama"
}
```

---

#### POST `/api/models/test`

Test connection to specific model provider.

**Request:**

```bash
curl -X POST https://api.glad-labs.com/api/models/test \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "ollama",
    "model": "mistral"
  }'
```

**Response (200 OK):**

```json
{
  "provider": "ollama",
  "model": "mistral",
  "status": "connected",
  "latency_ms": 145,
  "version": "2.0.0",
  "test_prompt": "Hello",
  "test_response": "Hi there! How can I help you today?",
  "timestamp": "2025-11-14T10:40:00Z"
}
```

---

### Content Generation

#### POST `/api/content/generate-blog-post`

Generate complete blog post with images, SEO, and publishing.

**Request:**

```bash
curl -X POST https://api.glad-labs.com/api/content/generate-blog-post \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "AI in Business 2025",
    "length": 2500,
    "style": "professional",
    "include_images": true,
    "seo_focus": "AI business applications",
    "publish_to_cms": true,
    "category": "Technology",
    "tags": ["AI", "business", "automation"],
    "author": "Content Bot"
  }'
```

**Response (201 Created):**

```json
{
  "task_id": "task_abc123",
  "status": "in_progress",
  "stage": "research",
  "progress_percent": 10,
  "estimated_completion_ms": 450000,
  "created_at": "2025-11-14T10:40:00Z"
}
```

**Polling for Completion:**

```bash
curl https://api.glad-labs.com/api/tasks/task_abc123 \
  -H "Authorization: Bearer YOUR_KEY"
```

**Final Response (when status = "completed"):**

```json
{
  "id": "task_abc123",
  "status": "completed",
  "result": {
    "title": "AI in Business 2025: Complete Guide",
    "content": "# AI in Business 2025\n\nArtificial Intelligence...",
    "excerpt": "Discover how AI is transforming business...",
    "images": [
      {
        "url": "https://cdn.glad-labs.com/img/ai-business.jpg",
        "alt": "AI and business",
        "caption": "AI transforming business landscape"
      }
    ],
    "seo": {
      "title": "AI in Business 2025: Complete Guide",
      "description": "Expert guide to AI applications in business...",
      "keywords": ["AI", "business", "automation", "2025"],
      "slug": "ai-business-2025"
    },
    "cms_id": "post_xyz789",
    "cms_url": "https://cms.glad-labs.com/posts/ai-business-2025",
    "quality_metrics": {
      "readability_score": 8.5,
      "seo_score": 9.2,
      "originality_score": 8.8
    }
  }
}
```

---

## ⚠️ Error Responses

All errors follow standard format:

```json
{
  "error": "Error code",
  "message": "Human-readable message",
  "details": "Additional context if available",
  "timestamp": "2025-11-14T10:40:00Z"
}
```

### Common Status Codes

| Code  | Meaning             | Example                  |
| ----- | ------------------- | ------------------------ |
| `200` | OK                  | Request successful       |
| `201` | Created             | Resource created         |
| `400` | Bad Request         | Invalid parameters       |
| `401` | Unauthorized        | Missing/invalid auth     |
| `403` | Forbidden           | Insufficient permissions |
| `404` | Not Found           | Resource doesn't exist   |
| `422` | Validation Error    | Invalid data format      |
| `429` | Rate Limited        | Too many requests        |
| `500` | Server Error        | Internal error           |
| `503` | Service Unavailable | System down              |

### Example Error Response

```json
{
  "error": "VALIDATION_ERROR",
  "message": "Topic is required",
  "details": {
    "field": "topic",
    "reason": "Field is required but not provided"
  },
  "timestamp": "2025-11-14T10:40:00Z"
}
```

---

## 📖 Pagination

List endpoints support pagination:

**Parameters:**

- `limit` - Results per page (default: 20, max: 100)
- `offset` - Number of results to skip (default: 0)

**Response (Task list example):**

```json
{
  "tasks": [...],
  "total": 542,
  "offset": 0,
  "limit": 20
}
```

> **Note:** The task list endpoint uses flat pagination fields (`total`, `offset`, `limit`) rather than a nested `pagination` object. Other list endpoints may follow a similar pattern.

---

## 🔄 Polling vs Webhooks

### Polling (Current)

```bash
# Create task
TASK_ID=$(curl -s -X POST https://api.glad-labs.com/api/tasks \
  -H "Authorization: Bearer KEY" \
  -H "Content-Type: application/json" \
  -d '{"topic": "AI Trends 2025", "task_type": "blog_post"}' | jq -r '.id')

# Poll for completion
while true; do
  STATUS=$(curl -s https://api.glad-labs.com/api/tasks/$TASK_ID \
    -H "Authorization: Bearer KEY" | jq -r '.status')

  if [ "$STATUS" = "completed" ]; then
    echo "Task done!"
    break
  fi

  sleep 5
done
```

### WebSocket Real-Time Progress (Current)

Connect to `/api/workflow-progress/ws/{execution_id}` for real-time progress updates instead of polling.

### Webhooks (Phase 6+)

```bash
# Create task with webhook (not yet implemented)
curl -X POST https://api.glad-labs.com/api/tasks \
  -H "Authorization: Bearer KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "AI Trends 2025",
    "task_type": "blog_post",
    "webhook_url": "https://yourapp.com/webhooks/task-complete"
  }'
```

---

## Related Documentation

- **Development:** [Development Workflow](../development/workflow.md)
- **Architecture:** [System Design](./system-design.md)

---

**Last Updated:** March 26, 2026
**Version:** 1.0 Production
**Next Version:** 2.0 (Phase 6)
**Status:** ✅ Complete Reference
