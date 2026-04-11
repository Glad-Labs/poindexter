# Poindexter API Reference

**Base URL:** `http://localhost:8002` (local worker — the only supported deployment today)
**Status:** Alpha. Surface area is broad but not contractually stable across releases.
**Last Updated:** 2026-04-11

---

## Overview

Reference for the REST API exposed by the Poindexter worker. This
document covers request/response formats, authentication, status
codes, and example usage for the routes you're most likely to call
from client code.

> **Stability warning.** The API is not yet considered stable. Route
> shapes change between releases, especially around task metadata.
> Pin to a specific commit until the first tagged release.

### API Principles

- **RESTful:** Standard HTTP methods and status codes
- **JSON:** All requests/responses in JSON format
- **Versioning:** URL-based versioning (`/api/v1/`, `/api/v2/`)
- **Authentication:** Bearer token in Authorization header
- **Rate Limiting:** 10 requests/minute on authentication and task creation endpoints
- **Pagination:** Limit/offset for list endpoints

---

## 🔐 Authentication

### JWT Bearer Token

All requests to protected endpoints require a JWT Bearer token in the Authorization header:

```bash
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     http://localhost:8000/api/tasks
```

### Token Sources

1. **API Token** (Production): Set `API_TOKEN` environment variable; include as `Authorization: Bearer <token>` header
2. **Dev Token** (Development): Use `Bearer dev-token` for local development without OAuth setup

### Development Bypass

For testing without GitHub OAuth credentials:

```bash
curl -H "Authorization: Bearer dev-token" \
     http://localhost:8000/api/tasks
```

### Protected Routes

The following routes require authentication:

- `/api/tasks` - Task management
- `/api/workflows` - Workflow execution
- `/api/agents` - Agent control
- `/api/custom-workflows` - Custom workflow management
- Plus 15+ additional admin and system routes

### Development Mode

Set `DEVELOPMENT_MODE=true` in `.env.local` to allow unauthenticated access with a mock user for testing.

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

### Tasks

#### GET `/api/tasks`

List all tasks (paginated).

**Request:**

```bash
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     http://localhost:8000/api/tasks?limit=20&offset=0
```

**Response (200 OK):**

```json
{
  "tasks": [
    {
      "id": "task_abc123",
      "title": "Blog Post Generation",
      "status": "in_progress",
      "created_at": "2025-11-14T10:30:00Z",
      "updated_at": "2025-11-14T10:35:00Z"
    }
  ],
  "pagination": {
    "limit": 20,
    "offset": 0,
    "total": 542,
    "pages": 28,
    "current_page": 1
  }
}
```

#### POST `/api/tasks`

Create a new task.

**Request:**

```bash
curl -X POST https://api.glad-labs.com/api/tasks \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Generate Blog Post",
    "description": "Create a comprehensive blog post about AI in business",
    "task_type": "blog_post",
    "priority": "medium"
  }'
```

**Response (201 Created):**

```json
{
  "id": "task_abc123",
  "title": "Generate Blog Post",
  "description": "Create a comprehensive blog post about AI in business",
  "status": "pending",
  "created_at": "2025-11-14T10:30:00Z"
}
```

#### GET `/api/tasks/{task_id}`

Get task details.

#### POST `/api/tasks/{task_id}/execute`

Execute a task.

#### GET `/api/tasks/{task_id}/executions/{execution_id}`

Get execution result.

---

---

### Error Responses

#### Validation Error

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

#### Not Found

```json
{
  "error": "NOT_FOUND",
  "message": "Task not found",
  "task_id": "task_xyz789",
  "timestamp": "2025-11-14T10:40:00Z"
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

- **Development:** [Development Workflow](../04-Development/Development-Workflow.md)
- **Architecture:** [System Architecture](../02-Architecture/System-Design.md)
- **Authentication:** [Auth Systems](../reference/AUTH_SYSTEMS.md)
- **Rate Limiting:** [Rate Limiting Policy](../reference/RATE_LIMITING.md)

---

**Last Updated:** March 21, 2026
**Version:** 1.0 Production
**Next Version:** 2.0 (Phase 6)
**Status:** ✅ Complete Reference
