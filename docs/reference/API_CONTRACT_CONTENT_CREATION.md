# API Contract: Content Creation Workflow

**Version:** 1.0  
**Last Updated:** October 22, 2025  
**Status:** Ready for Implementation

---

## Overview

This document defines the HTTP API contract between:

- **Client:** Oversight Hub (React dashboard)
- **Server:** Cofounder Agent (FastAPI on Railway)
- **Backend:** Strapi CMS (Railway)

---

## Base URL

```
Development: http://localhost:8000/api/v1
Production: https://[cofounder-agent-railway-url]/api/v1
```

---

## Endpoints

### 1. Create Blog Post (Async Generation)

**Endpoint:** `POST /api/v1/content/create-blog-post`

**Description:** Generate a new blog post using AI. This is an async operation - it returns immediately with a task ID, and the client polls for status.

**Request:**

```json
{
  "topic": "How to optimize AI costs in production",
  "style": "technical",
  "tone": "professional",
  "target_length": 1500,
  "tags": ["AI", "cost-optimization", "production"],
  "categories": ["Technical Guides"],
  "featured_image_prompt": "Abstract visualization of cost optimization dashboard",
  "publish_mode": "draft",
  "target_strapi_environment": "production"
}
```

**Request Fields:**

| Field                       | Type          | Required | Default        | Description                                                               |
| --------------------------- | ------------- | -------- | -------------- | ------------------------------------------------------------------------- |
| `topic`                     | string        | ✅       | —              | Blog post topic/title                                                     |
| `style`                     | enum          | ❌       | `informative`  | `technical`, `narrative`, `listicle`, `educational`, `thought-leadership` |
| `tone`                      | enum          | ❌       | `professional` | `professional`, `casual`, `academic`, `inspirational`                     |
| `target_length`             | integer       | ❌       | 1500           | Target word count (200-5000)                                              |
| `tags`                      | array[string] | ❌       | []             | Content tags for Strapi                                                   |
| `categories`                | array[string] | ❌       | []             | Strapi collection categories                                              |
| `featured_image_prompt`     | string        | ❌       | Auto-generated | DALL-E 3 prompt for featured image (optional)                             |
| `publish_mode`              | enum          | ❌       | `draft`        | `draft`, `publish`                                                        |
| `target_strapi_environment` | enum          | ❌       | `production`   | `production`, `staging`                                                   |

**Response (201 Created):**

```json
{
  "task_id": "blog_20251022_a7f3e9c1",
  "status": "pending",
  "topic": "How to optimize AI costs in production",
  "created_at": "2025-10-22T14:30:45.123Z",
  "polling_url": "/api/v1/content/tasks/blog_20251022_a7f3e9c1",
  "estimated_completion": "2025-10-22T14:35:45Z"
}
```

**Response Fields:**

| Field                  | Type     | Description                                                  |
| ---------------------- | -------- | ------------------------------------------------------------ |
| `task_id`              | string   | Unique task identifier (use for polling status)              |
| `status`               | enum     | `pending`, `generating`, `publishing`, `completed`, `failed` |
| `created_at`           | ISO 8601 | When the task was created                                    |
| `polling_url`          | string   | Endpoint to check task status                                |
| `estimated_completion` | ISO 8601 | Estimated time task will complete                            |

**Error Responses:**

```json
{
  "error": "validation_error",
  "details": {
    "target_length": "Must be between 200 and 5000 words"
  }
}
```

---

### 2. Get Task Status

**Endpoint:** `GET /api/v1/content/tasks/{task_id}`

**Description:** Poll this endpoint to check generation status. Call every 2-5 seconds during generation.

**Response (200 OK) - Generating:**

```json
{
  "task_id": "blog_20251022_a7f3e9c1",
  "status": "generating",
  "progress": {
    "stage": "content_generation",
    "percentage": 45,
    "message": "Generating blog content..."
  },
  "created_at": "2025-10-22T14:30:45.123Z"
}
```

**Response (200 OK) - Completed:**

```json
{
  "task_id": "blog_20251022_a7f3e9c1",
  "status": "completed",
  "result": {
    "title": "How to optimize AI costs in production",
    "content": "# How to optimize AI costs in production\n\n## Introduction\n...",
    "summary": "A comprehensive guide on reducing AI API costs in production environments",
    "word_count": 1547,
    "featured_image_url": "https://cdn.example.com/images/cost-optimization-dash.png",
    "strapi_post_id": 42,
    "strapi_url": "https://glad-labs-website-production.up.railway.app/blog/how-to-optimize-ai-costs",
    "publish_status": "published",
    "published_at": "2025-10-22T14:35:12.456Z"
  },
  "created_at": "2025-10-22T14:30:45.123Z",
  "completed_at": "2025-10-22T14:35:15.789Z"
}
```

**Response (200 OK) - Failed:**

```json
{
  "task_id": "blog_20251022_a7f3e9c1",
  "status": "failed",
  "error": {
    "code": "generation_failed",
    "message": "AI generation failed after 3 retries",
    "details": "Gemini API rate limit exceeded. Please try again in 5 minutes."
  },
  "created_at": "2025-10-22T14:30:45.123Z",
  "failed_at": "2025-10-22T14:35:15.789Z"
}
```

---

### 3. List Recent Blog Drafts

**Endpoint:** `GET /api/v1/content/drafts`

**Description:** Get list of recently generated blog post drafts (not yet published).

**Query Parameters:**

| Param    | Type    | Default | Description                              |
| -------- | ------- | ------- | ---------------------------------------- |
| `limit`  | integer | 20      | Max results (1-100)                      |
| `offset` | integer | 0       | Pagination offset                        |
| `status` | enum    | `draft` | Filter by `draft`, `scheduled`, `failed` |

**Response (200 OK):**

```json
{
  "drafts": [
    {
      "draft_id": "draft_20251022_a7f3e9c1",
      "title": "How to optimize AI costs in production",
      "created_at": "2025-10-22T14:30:45.123Z",
      "status": "draft",
      "word_count": 1547,
      "summary": "A comprehensive guide on reducing AI API costs...",
      "can_edit": true,
      "can_publish": true,
      "can_delete": true
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

---

### 4. Publish Blog Post Draft

**Endpoint:** `POST /api/v1/content/drafts/{draft_id}/publish`

**Description:** Publish a draft to Strapi immediately.

**Request:**

```json
{
  "target_strapi_environment": "production",
  "scheduled_for": null
}
```

**Request Fields:**

| Field                       | Type     | Required | Description                                             |
| --------------------------- | -------- | -------- | ------------------------------------------------------- |
| `target_strapi_environment` | enum     | ✅       | `production` or `staging`                               |
| `scheduled_for`             | ISO 8601 | ❌       | Future date to schedule publishing (omit for immediate) |

**Response (200 OK):**

```json
{
  "draft_id": "draft_20251022_a7f3e9c1",
  "strapi_post_id": 42,
  "published_url": "https://glad-labs-website-production.up.railway.app/blog/how-to-optimize-ai-costs",
  "published_at": "2025-10-22T14:40:00Z",
  "status": "published"
}
```

---

### 5. Delete Blog Draft

**Endpoint:** `DELETE /api/v1/content/drafts/{draft_id}`

**Description:** Delete a blog draft (cannot delete published posts through this API).

**Response (200 OK):**

```json
{
  "draft_id": "draft_20251022_a7f3e9c1",
  "deleted": true,
  "message": "Draft deleted successfully"
}
```

---

## Authentication

All endpoints require authentication via API key header:

```
Authorization: Bearer {API_KEY}
```

API keys are environment variables:

- Development: `COFOUNDER_AGENT_API_KEY`
- Production: Set in Railway environment

---

## Error Handling

All errors follow this format:

```json
{
  "error": "error_code",
  "message": "Human-readable error message",
  "details": {
    "field": "Additional context"
  },
  "request_id": "req_12345"
}
```

**Common Error Codes:**

| Code                | HTTP | Meaning                         |
| ------------------- | ---- | ------------------------------- |
| `validation_error`  | 400  | Invalid request parameters      |
| `auth_failed`       | 401  | Missing or invalid API key      |
| `not_found`         | 404  | Task/draft not found            |
| `conflict`          | 409  | Draft already published         |
| `rate_limited`      | 429  | Too many requests (retry later) |
| `generation_failed` | 500  | AI generation error             |
| `strapi_error`      | 502  | Strapi CMS connection error     |

---

## Rate Limiting

- **Limit:** 10 requests per minute per API key
- **Headers:**
  - `X-RateLimit-Limit: 10`
  - `X-RateLimit-Remaining: 7`
  - `X-RateLimit-Reset: 1729612345`

---

## Polling Strategy (Recommended for Oversight Hub)

```javascript
async function pollTaskStatus(taskId) {
  const maxAttempts = 120; // 10 minutes max
  let attempts = 0;

  while (attempts < maxAttempts) {
    const response = await fetch(`/api/v1/content/tasks/${taskId}`);
    const task = await response.json();

    if (task.status === 'completed' || task.status === 'failed') {
      return task;
    }

    // Wait 5 seconds before next poll
    await new Promise((r) => setTimeout(r, 5000));
    attempts++;
  }

  throw new Error('Task polling timeout');
}
```

---

## Implementation Checklist

- [ ] Create `/api/v1/content/create-blog-post` endpoint
- [ ] Add task status tracking (Firestore or Redis)
- [ ] Implement blog content generation (MCP + Gemini)
- [ ] Create Strapi integration (publish/draft)
- [ ] Add `/api/v1/content/tasks/{id}` endpoint
- [ ] Add `/api/v1/content/drafts` listing endpoint
- [ ] Add `/api/v1/content/drafts/{id}/publish` endpoint
- [ ] Add `/api/v1/content/drafts/{id}` delete endpoint
- [ ] Implement rate limiting middleware
- [ ] Add authentication/authorization
- [ ] Add comprehensive error handling
- [ ] Test full workflow end-to-end
