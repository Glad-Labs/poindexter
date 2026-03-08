# 📋 API Contract Reference

**Version:** 2.0 (Phase 6+)  
**Current:** 1.0 in production  
**Base URL:** `https://api.glad-labs.com` (or `http://localhost:8000` local)  
**Status:** ✅ Production Ready

---

## 📌 Overview

Complete documentation of all REST API endpoints for the Glad Labs Co-Founder system. This reference includes request/response formats, status codes, and example usage.

### API Principles

- **RESTful:** Standard HTTP methods and status codes
- **JSON:** All requests/responses in JSON format
- **Versioning:** URL-based versioning (`/api/v1/`, `/api/v2/`)
- **Authentication:** Bearer token in Authorization header
- **Rate Limiting:** 1000 requests/minute per API key
- **Pagination:** Limit/offset for list endpoints

---

## 🔐 Authentication

### API Key

All requests require authentication:

```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
     https://api.glad-labs.com/api/tasks
```

### Getting an API Key

1. Log in to Oversight Hub
2. Go to Settings → API Keys
3. Click "Create New Key"
4. Copy the key immediately (it won't be shown again)
5. Use in requests

### Token Format

- **Type:** Bearer token
- **Length:** 32+ characters
- **Rotation:** Rotate every 90 days
- **Revocation:** Immediate via UI

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
  "timestamp": "2025-11-14T10:30:00Z",
  "version": "1.0.0",
  "services": {
    "database": "connected",
    "cache": "connected",
    "agents": "ready",
    "models": "ready"
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

Create a new task for agent execution.

**Request:**

```bash
curl -X POST https://api.glad-labs.com/api/tasks \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Generate Blog Post",
    "description": "Write SEO-optimized blog post about AI",
    "type": "content_generation",
    "parameters": {
      "topic": "AI Trends 2025",
      "length": 2000,
      "style": "professional",
      "include_images": true
    }
  }'
```

**Response (201 Created):**

```json
{
  "id": "task_abc123xyz",
  "title": "Generate Blog Post",
  "status": "pending",
  "type": "content_generation",
  "created_at": "2025-11-14T10:30:00Z",
  "created_by": "user_123",
  "parameters": {
    "topic": "AI Trends 2025",
    "length": 2000,
    "style": "professional",
    "include_images": true
  }
}
```

**Status Codes:**

- `201 Created` - Task created successfully
- `400 Bad Request` - Invalid parameters
- `401 Unauthorized` - Missing/invalid authentication
- `422 Unprocessable Entity` - Validation error

---

#### GET `/api/tasks/{task_id}`

Get details about a specific task.

**Request:**

```bash
curl https://api.glad-labs.com/api/tasks/task_abc123xyz \
  -H "Authorization: Bearer YOUR_KEY"
```

**Response (200 OK):**

```json
{
  "id": "task_abc123xyz",
  "title": "Generate Blog Post",
  "status": "completed",
  "type": "content_generation",
  "created_at": "2025-11-14T10:30:00Z",
  "started_at": "2025-11-14T10:31:00Z",
  "completed_at": "2025-11-14T10:35:00Z",
  "assigned_agents": ["content_agent", "publishing_agent"],
  "parameters": {
    "topic": "AI Trends 2025",
    "length": 2000,
    "style": "professional",
    "include_images": true
  },
  "result": {
    "content": "# AI Trends 2025\n\nArtificial Intelligence...",
    "images": ["img1.jpg", "img2.jpg"],
    "seo": {
      "title": "AI Trends 2025 - Expert Guide",
      "description": "Comprehensive guide to AI trends...",
      "keywords": ["AI", "trends", "2025", "machine learning"]
    },
    "quality_score": 4.8
  },
  "errors": null
}
```

**Status Codes:**

- `200 OK` - Task found
- `401 Unauthorized` - Missing/invalid authentication
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

- `status` - Filter by status (pending, in_progress, completed, failed)
- `type` - Filter by type (content_generation, financial_analysis, etc.)
- `limit` - Results per page (default: 20, max: 100)
- `offset` - Pagination offset (default: 0)
- `sort` - Sort field (created_at, status, type)
- `order` - Sort order (asc, desc)

**Response (200 OK):**

```json
{
  "data": [
    {
      "id": "task_abc123xyz",
      "title": "Generate Blog Post",
      "status": "completed",
      "type": "content_generation",
      "created_at": "2025-11-14T10:30:00Z"
    },
    {
      "id": "task_def456uvw",
      "title": "Analyze Market",
      "status": "in_progress",
      "type": "market_analysis",
      "created_at": "2025-11-14T09:15:00Z"
    }
  ],
  "pagination": {
    "limit": 10,
    "offset": 0,
    "total": 245,
    "pages": 25
  }
}
```

---

#### PUT `/api/tasks/{task_id}`

Update a task (only pending tasks).

**Request:**

```bash
curl -X PUT https://api.glad-labs.com/api/tasks/task_abc123xyz \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "paused",
    "priority": "high"
  }'
```

**Response (200 OK):**

```json
{
  "id": "task_abc123xyz",
  "status": "paused",
  "priority": "high",
  "updated_at": "2025-11-14T10:40:00Z"
}
```

---

#### DELETE `/api/tasks/{task_id}`

Cancel a pending task.

**Request:**

```bash
curl -X DELETE https://api.glad-labs.com/api/tasks/task_abc123xyz \
  -H "Authorization: Bearer YOUR_KEY"
```

**Response (204 No Content):**

```
(empty response)
```

**Status Codes:**

- `204 No Content` - Task deleted
- `400 Bad Request` - Cannot delete completed task
- `401 Unauthorized` - Missing/invalid authentication
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

**Response:**

```json
{
  "data": [...],
  "pagination": {
    "limit": 20,
    "offset": 0,
    "total": 542,
    "pages": 28,
    "current_page": 1,
    "has_next": true,
    "has_prev": false
  }
}
```

---

## 🔄 Polling vs Webhooks

### Polling (Current)

```bash
# Create task
TASK_ID=$(curl -X POST https://api.glad-labs.com/api/tasks \
  -H "Authorization: Bearer KEY" \
  -d '...' | jq -r '.id')

# Poll for completion
while true; do
  STATUS=$(curl https://api.glad-labs.com/api/tasks/$TASK_ID \
    -H "Authorization: Bearer KEY" | jq -r '.status')

  if [ "$STATUS" = "completed" ]; then
    echo "Task done!"
    break
  fi

  sleep 5
done
```

### Webhooks (Phase 6+)

```bash
# Create task with webhook
curl -X POST https://api.glad-labs.com/api/tasks \
  -H "Authorization: Bearer KEY" \
  -d '{
    "title": "...",
    "webhook_url": "https://yourapp.com/webhooks/task-complete"
  }'
```

---

## 📚 Related Documentation

- **Development:** [Development Workflow](../04-DEVELOPMENT_WORKFLOW.md)
- **Architecture:** [System Architecture](../02-ARCHITECTURE_AND_DESIGN.md)
- **Authentication:** [Auth Systems](../reference/AUTH_SYSTEMS.md)
- **Rate Limiting:** [Rate Limiting Policy](../reference/RATE_LIMITING.md)

---

**Last Updated:** November 14, 2025  
**Version:** 1.0 Production  
**Next Version:** 2.0 (Phase 6)  
**Status:** ✅ Complete Reference
