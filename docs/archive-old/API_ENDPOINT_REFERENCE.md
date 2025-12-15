# API Endpoint Reference Guide

**Complete catalog of all 97+ backend endpoints across 17 route modules**

---

## Table of Contents

1. [Task Management](#task-management)
2. [Content Management](#content-management)
3. [Chat & Messaging](#chat--messaging)
4. [Agents](#agents)
5. [Intelligent Orchestrator](#intelligent-orchestrator)
6. [Social Publishing](#social-publishing)
7. [Metrics & Analytics](#metrics--analytics)
8. [Ollama Models](#ollama-models)
9. [Settings](#settings)
10. [Workflow History](#workflow-history)
11. [Subtasks](#subtasks)
12. [Command Queue](#command-queue)
13. [CMS Routes](#cms-routes)
14. [Bulk Operations](#bulk-operations)
15. [Webhooks](#webhooks)
16. [Authentication](#authentication)
17. [Models Metadata](#models-metadata)

---

## Task Management

**Route Module:** `task_routes.py`  
**Base Path:** `/api/tasks`  
**Auth Required:** All endpoints

### POST /api/tasks

Create a new task

```
Method: POST
Path: /api/tasks
Auth: ✅ Required (Bearer token)
Content-Type: application/json

Request Body:
{
  "task_name": "Blog Post: AI Trends",
  "topic": "artificial intelligence",
  "primary_keyword": "AI trends 2025",
  "target_audience": "Tech professionals",
  "category": "Technology",
  "task_metadata": {
    "style": "professional",
    "tone": "informative"
  }
}

Response: 201 Created
{
  "id": "uuid-string",
  "task_name": "Blog Post: AI Trends",
  "status": "pending",
  "created_at": "2024-12-09T15:00:00Z",
  ...
}

Error: 400/401/500
```

### GET /api/tasks

List all tasks with pagination

```
Method: GET
Path: /api/tasks?limit=20&offset=0&status=pending
Auth: ✅ Required

Query Parameters:
- limit: int (default: 100, max: 100)
- offset: int (default: 0)
- status: string (pending|in_progress|completed|failed) [optional]
- sort_by: string (created_at|updated_at|status) [optional]

Response: 200 OK
{
  "tasks": [
    {
      "id": "uuid",
      "task_name": "string",
      "status": "string",
      "created_at": "ISO8601",
      ...
    }
  ],
  "total": 89,
  "offset": 0,
  "limit": 20
}
```

### GET /api/tasks/{task_id}

Get single task details

```
Method: GET
Path: /api/tasks/{task_id}
Auth: ✅ Required

URL Parameters:
- task_id: UUID (string)

Response: 200 OK
{
  "id": "uuid-string",
  "task_name": "Blog Post: AI Trends",
  "status": "in_progress",
  "created_at": "ISO8601",
  "updated_at": "ISO8601",
  "task_metadata": {
    "content": "Article content...",
    "quality_score": 85,
    ...
  }
}

Error: 404 Not Found
```

### PATCH /api/tasks/{task_id}

Update task status

```
Method: PATCH
Path: /api/tasks/{task_id}
Auth: ✅ Required
Content-Type: application/json

Request Body:
{
  "status": "completed",
  "task_metadata": {
    "quality_score": 92,
    "content": "Updated content..."
  }
}

Response: 200 OK
{ ... updated task object ... }

Error: 404/400/500
```

### GET /api/tasks/metrics/summary

Get aggregated task metrics

```
Method: GET
Path: /api/tasks/metrics/summary
Auth: ✅ Required

Response: 200 OK
{
  "total_tasks": 89,
  "completed": 48,
  "in_progress": 0,
  "failed": 22,
  "pending": 19,
  "completion_rate": 0.54,
  "avg_completion_time": 3600,
  "success_rate": 0.69
}
```

### POST /api/tasks/intent

Process task intent

```
Method: POST
Path: /api/tasks/intent
Auth: ✅ Required

Request Body:
{
  "task_id": "uuid",
  "intent": "create_blog_post",
  "parameters": { ... }
}

Response: 200 OK
{ ... intent processing result ... }
```

### POST /api/tasks/confirm-intent

Confirm task intent processing

```
Method: POST
Path: /api/tasks/confirm-intent
Auth: ✅ Required

Request Body:
{
  "task_id": "uuid",
  "confirmed": true
}

Response: 200 OK
{ ... confirmation result ... }
```

---

## Content Management

**Route Module:** `content_routes.py`  
**Base Path:** `/api/content`  
**Auth Required:** All endpoints

### POST /api/content

Create new content

```
Method: POST
Path: /api/content
Auth: ✅ Required

Request Body:
{
  "title": "Article Title",
  "content": "Article content...",
  "featured_image_url": "https://...",
  "seo_title": "SEO Title",
  "seo_description": "SEO description"
}

Response: 201 Created
{ ... content object with id ... }
```

### GET /api/content

List content items

```
Method: GET
Path: /api/content?limit=20&offset=0
Auth: ✅ Required

Query Parameters:
- limit: int (default: 100)
- offset: int (default: 0)
- status: string (optional)

Response: 200 OK
{
  "items": [ ... ],
  "total": X,
  "offset": Y,
  "limit": Z
}
```

### GET /api/content/{item_id}

Get content details

```
Method: GET
Path: /api/content/{item_id}
Auth: ✅ Required

Response: 200 OK
{ ... content object ... }
```

### POST /api/content/{item_id}

Update content

```
Method: POST
Path: /api/content/{item_id}
Auth: ✅ Required

Request Body:
{ ... updated fields ... }

Response: 200 OK
{ ... updated content object ... }
```

### DELETE /api/content/{item_id}

Delete content

```
Method: DELETE
Path: /api/content/{item_id}
Auth: ✅ Required

Response: 204 No Content
```

### POST /api/content/approve

Approve content for publishing

```
Method: POST
Path: /api/content/approve
Auth: ✅ Required

Request Body:
{
  "content_id": "uuid",
  "approved": true,
  "feedback": "Looks great!"
}

Response: 200 OK
{ ... approved content object ... }
```

---

## Chat & Messaging

**Route Module:** `chat_routes.py`  
**Base Path:** `/api/chat`  
**Auth Required:** Most endpoints

### POST /api/chat

Send chat message

```
Method: POST
Path: /api/chat
Auth: ✅ Required

Request Body:
{
  "conversation_id": "uuid or null for new",
  "message": "What is AI?",
  "model": "claude-3-sonnet"
}

Response: 200 OK
{
  "conversation_id": "uuid",
  "message_id": "uuid",
  "response": "AI is artificial intelligence...",
  "model": "claude-3-sonnet",
  "tokens_used": 250
}
```

### GET /api/chat/history/{conversation_id}

Get conversation history

```
Method: GET
Path: /api/chat/history/{conversation_id}
Auth: ✅ Required

Response: 200 OK
{
  "conversation_id": "uuid",
  "messages": [
    {
      "id": "uuid",
      "role": "user|assistant|system",
      "content": "Message text",
      "timestamp": "ISO8601"
    }
  ]
}
```

### DELETE /api/chat/history/{conversation_id}

Clear conversation history

```
Method: DELETE
Path: /api/chat/history/{conversation_id}
Auth: ✅ Required

Response: 204 No Content
```

### GET /api/chat/models

Get available chat models

```
Method: GET
Path: /api/chat/models
Auth: ✅ Required

Response: 200 OK
{
  "models": [
    {
      "id": "claude-3-sonnet",
      "name": "Claude 3 Sonnet",
      "provider": "anthropic",
      "capabilities": [ ... ]
    }
  ]
}
```

---

## Agents

**Route Module:** `agents_routes.py`  
**Base Path:** `/api/agents`  
**Auth Required:** All endpoints

### GET /api/agents/status

Get all agents status

```
Method: GET
Path: /api/agents/status
Auth: ✅ Required

Response: 200 OK
{
  "agents": [
    {
      "name": "ContentAgent",
      "status": "active|inactive|error",
      "uptime": 3600,
      "tasks_completed": 45
    }
  ]
}
```

### GET /api/agents/{agent_name}/status

Get specific agent status

```
Method: GET
Path: /api/agents/{agent_name}/status
Auth: ✅ Required

Response: 200 OK
{ ... agent status details ... }
```

### POST /api/agents/{agent_name}/command

Send command to agent

```
Method: POST
Path: /api/agents/{agent_name}/command
Auth: ✅ Required

Request Body:
{
  "command": "start|stop|restart|execute",
  "parameters": { ... }
}

Response: 200 OK
{ ... command execution result ... }
```

### GET /api/agents/logs

Get agent execution logs

```
Method: GET
Path: /api/agents/logs?agent={name}&limit=100
Auth: ✅ Required

Response: 200 OK
{
  "logs": [
    {
      "timestamp": "ISO8601",
      "agent": "string",
      "level": "INFO|WARNING|ERROR",
      "message": "string"
    }
  ]
}
```

### GET /api/agents/memory/stats

Get memory statistics

```
Method: GET
Path: /api/agents/memory/stats
Auth: ✅ Required

Response: 200 OK
{
  "total_memory": 8589934592,
  "used_memory": 2147483648,
  "available_memory": 6442450944,
  "memory_usage_percent": 25
}
```

### GET /api/agents/health

Get agent health status

```
Method: GET
Path: /api/agents/health
Auth: ✅ Required

Response: 200 OK
{
  "status": "healthy|degraded|unhealthy",
  "uptime": 86400,
  "error_rate": 0.02,
  "response_time_ms": 45
}
```

---

## Intelligent Orchestrator

**Route Module:** `intelligent_orchestrator_routes.py`  
**Base Path:** `/api/orchestrator`  
**Auth Required:** All endpoints

### POST /api/orchestrator/process

Process task through orchestrator

```
Method: POST
Path: /api/orchestrator/process
Auth: ✅ Required

Request Body:
{
  "task_id": "uuid",
  "workflow_type": "content_creation|analysis|optimization",
  "parameters": { ... }
}

Response: 200 OK
{
  "execution_id": "uuid",
  "task_id": "uuid",
  "status": "processing|completed|failed",
  "result": { ... }
}
```

### GET /api/orchestrator/status/{task_id}

Get orchestration status

```
Method: GET
Path: /api/orchestrator/status/{task_id}
Auth: ✅ Required

Response: 200 OK
{
  "task_id": "uuid",
  "status": "processing|approved|rejected|completed",
  "progress": 75,
  "current_step": "quality_assessment"
}
```

### GET /api/orchestrator/approval/{task_id}

Get approval status

```
Method: GET
Path: /api/orchestrator/approval/{task_id}
Auth: ✅ Required

Response: 200 OK
{
  "task_id": "uuid",
  "requires_approval": true,
  "approval_status": "pending|approved|rejected",
  "feedback": "Needs revision"
}
```

### POST /api/orchestrator/approve/{task_id}

Approve orchestrated task

```
Method: POST
Path: /api/orchestrator/approve/{task_id}
Auth: ✅ Required

Request Body:
{
  "approved": true,
  "feedback": "Looks good!",
  "notes": "Optional notes"
}

Response: 200 OK
{ ... approval result ... }
```

### GET /api/orchestrator/history

Get orchestration history

```
Method: GET
Path: /api/orchestrator/history?limit=50
Auth: ✅ Required

Response: 200 OK
{
  "executions": [
    {
      "id": "uuid",
      "task_id": "uuid",
      "completed_at": "ISO8601",
      "result": "success|failure",
      "duration_ms": 5000
    }
  ]
}
```

### POST /api/orchestrator/training-data/export

Export training data

```
Method: POST
Path: /api/orchestrator/training-data/export
Auth: ✅ Required

Response: 200 OK
{
  "file_url": "https://...",
  "format": "json|csv",
  "record_count": 1000
}
```

### POST /api/orchestrator/training-data/upload-model

Upload trained model

```
Method: POST
Path: /api/orchestrator/training-data/upload-model
Auth: ✅ Required (multipart/form-data)

Response: 200 OK
{ ... model upload result ... }
```

### GET /api/orchestrator/learning-patterns

Get learned patterns

```
Method: GET
Path: /api/orchestrator/learning-patterns
Auth: ✅ Required

Response: 200 OK
{
  "patterns": [
    {
      "id": "pattern_1",
      "description": "High-performing content uses 800+ words",
      "confidence": 0.92,
      "applicable_domains": [ ... ]
    }
  ]
}
```

### GET /api/orchestrator/business-metrics-analysis

Business metrics analysis

```
Method: GET
Path: /api/orchestrator/business-metrics-analysis
Auth: ✅ Required

Response: 200 OK
{
  "roi": 3.5,
  "cost_per_output": 0.25,
  "throughput": 50,
  "quality_score": 0.87
}
```

### GET /api/orchestrator/tools

Get available orchestration tools

```
Method: GET
Path: /api/orchestrator/tools
Auth: ✅ Required

Response: 200 OK
{
  "tools": [
    {
      "name": "content_analyzer",
      "description": "Analyzes content quality",
      "inputs": [ ... ],
      "outputs": [ ... ]
    }
  ]
}
```

---

## Social Publishing

**Route Module:** `social_routes.py`  
**Base Path:** `/api/social`  
**Auth Required:** All endpoints

### GET /api/social/platforms

Get connected social platforms

```
Method: GET
Path: /api/social/platforms
Auth: ✅ Required

Response: 200 OK
{
  "platforms": [
    {
      "name": "twitter",
      "connected": true,
      "account": "@username",
      "followers": 10000
    }
  ]
}
```

### POST /api/social/connect

Connect new social platform

```
Method: POST
Path: /api/social/connect
Auth: ✅ Required

Request Body:
{
  "platform": "twitter|linkedin|instagram",
  "credentials": { ... }
}

Response: 200 OK
{ ... connection result ... }
```

### GET /api/social/posts

Get scheduled posts

```
Method: GET
Path: /api/social/posts?platform=twitter&limit=20
Auth: ✅ Required

Response: 200 OK
{
  "posts": [
    {
      "id": "uuid",
      "platform": "twitter",
      "content": "Post content",
      "scheduled_at": "ISO8601",
      "status": "scheduled|published|failed"
    }
  ]
}
```

### POST /api/social/posts

Create new social post

```
Method: POST
Path: /api/social/posts
Auth: ✅ Required

Request Body:
{
  "platforms": [ "twitter", "linkedin" ],
  "content": "Post content",
  "images": [ ... ],
  "scheduled_at": "ISO8601 or null for immediate",
  "hashtags": [ "#ai", "#tech" ]
}

Response: 201 Created
{ ... created post object ... }
```

### DELETE /api/social/posts/{post_id}

Delete post

```
Method: DELETE
Path: /api/social/posts/{post_id}
Auth: ✅ Required

Response: 204 No Content
```

### GET /api/social/posts/{post_id}/analytics

Get post analytics

```
Method: GET
Path: /api/social/posts/{post_id}/analytics
Auth: ✅ Required

Response: 200 OK
{
  "post_id": "uuid",
  "impressions": 5000,
  "engagements": 250,
  "likes": 150,
  "shares": 50,
  "comments": 50,
  "engagement_rate": 0.05
}
```

### POST /api/social/generate

Generate social post from content

```
Method: POST
Path: /api/social/generate
Auth: ✅ Required

Request Body:
{
  "content": "Blog post content",
  "platforms": [ "twitter", "linkedin" ],
  "style": "professional|casual|promotional"
}

Response: 200 OK
{
  "twitter": "Tweet text (280 chars max)",
  "linkedin": "LinkedIn post text"
}
```

### GET /api/social/trending

Get trending topics

```
Method: GET
Path: /api/social/trending?platform=twitter
Auth: ✅ Required

Response: 200 OK
{
  "trending": [
    {
      "topic": "#AI",
      "mentions": 50000,
      "trend_velocity": "rising"
    }
  ]
}
```

### POST /api/social/cross-post

Cross-post to multiple platforms

```
Method: POST
Path: /api/social/cross-post
Auth: ✅ Required

Request Body:
{
  "post_id": "uuid",
  "platforms": [ "twitter", "linkedin", "instagram" ]
}

Response: 200 OK
{ ... cross-posting result ... }
```

---

## Metrics & Analytics

**Route Module:** `metrics_routes.py`  
**Base Path:** `/api/metrics`  
**Auth Required:** All endpoints

### GET /api/metrics/usage

Get usage metrics

```
Method: GET
Path: /api/metrics/usage?period=day|week|month
Auth: ✅ Required

Response: 200 OK
{
  "period": "day",
  "api_calls": 5000,
  "tokens_used": 1000000,
  "active_users": 50,
  "error_rate": 0.02
}
```

### GET /api/metrics/costs

Get cost analysis

```
Method: GET
Path: /api/metrics/costs
Auth: ✅ Required

Response: 200 OK
{
  "total_cost": 150.50,
  "breakdown": {
    "api_calls": 50.00,
    "storage": 30.00,
    "compute": 70.50
  },
  "cost_per_task": 1.69
}
```

### GET /api/metrics

Get all metrics

```
Method: GET
Path: /api/metrics
Auth: ✅ Required

Response: 200 OK
{
  "usage": { ... },
  "costs": { ... },
  "performance": { ... }
}
```

### GET /api/metrics/summary

Get metrics summary

```
Method: GET
Path: /api/metrics/summary
Auth: ✅ Required

Response: 200 OK
{
  "summary": {
    "total_tasks": 89,
    "completion_rate": 0.54,
    "avg_quality_score": 87.5,
    "total_cost": 150.50
  }
}
```

### POST /api/metrics/track-usage

Track custom usage

```
Method: POST
Path: /api/metrics/track-usage
Auth: ✅ Required

Request Body:
{
  "metric_name": "content_generation",
  "value": 100,
  "unit": "tokens|requests|duration",
  "tags": { ... }
}

Response: 200 OK
{ ... tracking result ... }
```

---

## Ollama Models

**Route Module:** `ollama_routes.py`  
**Base Path:** `/api/ollama`  
**Auth Required:** Varies (see below)

### GET /api/ollama/health

Check Ollama server health

```
Method: GET
Path: /api/ollama/health
Auth: ❌ Not required

Response: 200 OK
{
  "status": "healthy|degraded|offline",
  "uptime": 86400,
  "memory_usage": 2147483648
}
```

### GET /api/ollama/models

List available Ollama models

```
Method: GET
Path: /api/ollama/models
Auth: ❌ Not required

Response: 200 OK
{
  "models": [
    {
      "name": "mistral",
      "size": "7b",
      "parameters": 7000000000,
      "quantization": "4bit"
    }
  ]
}
```

### POST /api/ollama/warmup

Warm up model in memory

```
Method: POST
Path: /api/ollama/warmup
Auth: ❌ Not required

Request Body:
{
  "model": "mistral"
}

Response: 200 OK
{
  "status": "warming|loaded|ready",
  "progress": 100
}
```

### GET /api/ollama/status

Get current model status

```
Method: GET
Path: /api/ollama/status
Auth: ❌ Not required

Response: 200 OK
{
  "active_model": "mistral",
  "memory_allocated": 4294967296,
  "requests_per_second": 5
}
```

### POST /api/ollama/select-model

Select active model

```
Method: POST
Path: /api/ollama/select-model
Auth: ✅ Required

Request Body:
{
  "model": "mistral"
}

Response: 200 OK
{ ... model selection result ... }
```

---

## Settings

**Route Module:** `settings_routes.py`  
**Base Path:** `/api/settings`  
**Auth Required:** All endpoints

### GET /api/settings/general

Get general settings

```
Method: GET
Path: /api/settings/general
Auth: ✅ Required

Response: 200 OK
{
  "app_name": "Glad Labs",
  "language": "en",
  "timezone": "UTC"
}
```

### GET /api/settings/system

Get system settings

```
Method: GET
Path: /api/settings/system
Auth: ✅ Required

Response: 200 OK
{
  "debug_mode": false,
  "log_level": "INFO",
  "max_workers": 4
}
```

### POST /api/settings/create

Create new setting

```
Method: POST
Path: /api/settings/create
Auth: ✅ Required

Request Body:
{
  "key": "setting_name",
  "value": "setting_value",
  "type": "string|int|bool|json"
}

Response: 201 Created
{ ... created setting ... }
```

### PUT /api/settings/{setting_id}

Update setting

```
Method: PUT
Path: /api/settings/{setting_id}
Auth: ✅ Required

Request Body:
{ "value": "new_value" }

Response: 200 OK
{ ... updated setting ... }
```

### DELETE /api/settings/{setting_id}

Delete setting

```
Method: DELETE
Path: /api/settings/{setting_id}
Auth: ✅ Required

Response: 204 No Content
```

### PUT /api/settings/theme

Update theme

```
Method: PUT
Path: /api/settings/theme
Auth: ✅ Required

Request Body:
{
  "theme": "light|dark|auto",
  "accent_color": "#FF0000"
}

Response: 200 OK
{ ... updated theme ... }
```

### DELETE /api/settings/theme

Reset theme

```
Method: DELETE
Path: /api/settings/theme
Auth: ✅ Required

Response: 204 No Content
```

### GET /api/settings/api-keys

Get API keys

```
Method: GET
Path: /api/settings/api-keys
Auth: ✅ Required

Response: 200 OK
{
  "keys": [
    {
      "id": "uuid",
      "name": "Production API Key",
      "partial": "sk_live_****",
      "created_at": "ISO8601"
    }
  ]
}
```

### POST /api/settings/webhooks

Configure webhooks

```
Method: POST
Path: /api/settings/webhooks
Auth: ✅ Required

Request Body:
{
  "url": "https://webhook.example.com",
  "events": [ "task.completed", "task.failed" ],
  "active": true
}

Response: 201 Created
{ ... webhook configuration ... }
```

### GET /api/settings/integrations

Get integrations

```
Method: GET
Path: /api/settings/integrations
Auth: ✅ Required

Response: 200 OK
{
  "integrations": [
    {
      "name": "stripe",
      "status": "connected|pending|error",
      "configured_at": "ISO8601"
    }
  ]
}
```

---

## Workflow History

**Route Module:** `workflow_history.py`  
**Base Path:** `/api/workflow`  
**Auth Required:** All endpoints

### GET /api/workflow/history

Get execution history

```
Method: GET
Path: /api/workflow/history?limit=50
Auth: ✅ Required

Response: 200 OK
{
  "executions": [
    {
      "id": "uuid",
      "workflow_name": "content_creation",
      "status": "success|failure",
      "started_at": "ISO8601",
      "completed_at": "ISO8601"
    }
  ]
}
```

### GET /api/workflow/{execution_id}/details

Get execution details

```
Method: GET
Path: /api/workflow/{execution_id}/details
Auth: ✅ Required

Response: 200 OK
{
  "id": "uuid",
  "steps": [
    {
      "step_name": "research",
      "status": "completed",
      "duration_ms": 5000,
      "output": { ... }
    }
  ]
}
```

### GET /api/workflow/statistics

Get workflow statistics

```
Method: GET
Path: /api/workflow/statistics
Auth: ✅ Required

Response: 200 OK
{
  "total_executions": 1000,
  "success_rate": 0.92,
  "avg_duration_ms": 3600,
  "workflows": [ ... ]
}
```

### GET /api/workflow/performance-metrics

Get performance metrics

```
Method: GET
Path: /api/workflow/performance-metrics
Auth: ✅ Required

Response: 200 OK
{
  "throughput": 50,
  "avg_latency_ms": 100,
  "peak_latency_ms": 5000,
  "bottleneck": "database_query"
}
```

### GET /api/workflow/{workflow_id}/history

Get specific workflow history

```
Method: GET
Path: /api/workflow/{workflow_id}/history
Auth: ✅ Required

Response: 200 OK
{
  "workflow_id": "uuid",
  "executions": [ ... ]
}
```

---

## Subtasks

**Route Module:** `subtask_routes.py`  
**Base Path:** `/api/subtasks`  
**Auth Required:** All endpoints

### POST /api/subtasks/research

Execute research subtask

```
Method: POST
Path: /api/subtasks/research
Auth: ✅ Required

Request Body:
{
  "topic": "AI trends 2025",
  "depth": "deep|moderate|shallow",
  "sources": [ "academic", "news", "blogs" ]
}

Response: 200 OK
{
  "status": "completed|processing",
  "findings": "Research findings...",
  "sources": [ ... ]
}
```

### POST /api/subtasks/creative

Execute creative subtask

```
Method: POST
Path: /api/subtasks/creative
Auth: ✅ Required

Request Body:
{
  "topic": "Blog post about AI",
  "style": "professional|casual|humorous",
  "length": "short|medium|long"
}

Response: 200 OK
{
  "status": "completed",
  "content": "Generated creative content..."
}
```

### POST /api/subtasks/qa

Execute QA subtask

```
Method: POST
Path: /api/subtasks/qa
Auth: ✅ Required

Request Body:
{
  "content": "Content to QA",
  "checks": [ "grammar", "factuality", "formatting" ]
}

Response: 200 OK
{
  "status": "completed",
  "issues": [ ... ],
  "quality_score": 92
}
```

### POST /api/subtasks/images

Process image subtask

```
Method: POST
Path: /api/subtasks/images
Auth: ✅ Required

Request Body:
{
  "prompt": "AI robot image",
  "style": "photorealistic|illustration|3d",
  "count": 1
}

Response: 200 OK
{
  "status": "completed",
  "images": [
    {
      "url": "https://...",
      "prompt": "AI robot image"
    }
  ]
}
```

### POST /api/subtasks/format

Format content subtask

```
Method: POST
Path: /api/subtasks/format
Auth: ✅ Required

Request Body:
{
  "content": "Raw content",
  "format": "markdown|html|rst",
  "style": "blog|documentation|email"
}

Response: 200 OK
{
  "status": "completed",
  "formatted_content": "Formatted content..."
}
```

---

## Command Queue

**Route Module:** `command_queue_routes.py`  
**Base Path:** `/api/commands`  
**Auth Required:** All endpoints

### POST /api/commands

Queue new command

```
Method: POST
Path: /api/commands
Auth: ✅ Required

Request Body:
{
  "command": "execute_task",
  "parameters": { ... },
  "priority": "high|normal|low"
}

Response: 201 Created
{
  "command_id": "uuid",
  "status": "queued"
}
```

### GET /api/commands/{command_id}

Get command status

```
Method: GET
Path: /api/commands/{command_id}
Auth: ✅ Required

Response: 200 OK
{
  "command_id": "uuid",
  "status": "queued|processing|completed|failed",
  "result": { ... },
  "created_at": "ISO8601",
  "completed_at": "ISO8601"
}
```

### GET /api/commands

List commands

```
Method: GET
Path: /api/commands?limit=50&status=processing
Auth: ✅ Required

Response: 200 OK
{
  "commands": [ ... ],
  "total": X
}
```

### POST /api/commands/{command_id}/complete

Mark command complete

```
Method: POST
Path: /api/commands/{command_id}/complete
Auth: ✅ Required

Request Body:
{
  "result": { ... }
}

Response: 200 OK
{ ... updated command ... }
```

### POST /api/commands/{command_id}/fail

Mark command failed

```
Method: POST
Path: /api/commands/{command_id}/fail
Auth: ✅ Required

Request Body:
{
  "error": "Error message",
  "retry": true
}

Response: 200 OK
{ ... updated command ... }
```

### POST /api/commands/{command_id}/cancel

Cancel command

```
Method: POST
Path: /api/commands/{command_id}/cancel
Auth: ✅ Required

Response: 204 No Content
```

### GET /api/commands/stats/queue-stats

Get queue statistics

```
Method: GET
Path: /api/commands/stats/queue-stats
Auth: ✅ Required

Response: 200 OK
{
  "queued": 10,
  "processing": 3,
  "completed": 500,
  "failed": 5,
  "avg_processing_time_ms": 2000
}
```

### POST /api/commands/cleanup/clear-old

Clean old commands

```
Method: POST
Path: /api/commands/cleanup/clear-old
Auth: ✅ Required

Request Body:
{
  "older_than_days": 7
}

Response: 200 OK
{
  "deleted": 100
}
```

---

## CMS Routes

**Route Module:** `cms_routes.py`  
**Base Path:** `/api`  
**Auth Required:** None (public endpoints)

### GET /api/posts

Get blog posts

```
Method: GET
Path: /api/posts?skip=0&limit=10
Auth: ❌ Not required

Response: 200 OK
{
  "posts": [
    {
      "id": "uuid",
      "title": "Post title",
      "slug": "post-slug",
      "content": "Post content",
      "published_at": "ISO8601"
    }
  ]
}
```

### GET /api/posts/{slug}

Get single post by slug

```
Method: GET
Path: /api/posts/{slug}
Auth: ❌ Not required

Response: 200 OK
{ ... post object ... }
```

### GET /api/categories

Get post categories

```
Method: GET
Path: /api/categories
Auth: ❌ Not required

Response: 200 OK
{
  "categories": [
    { "id": "uuid", "name": "Technology" }
  ]
}
```

### GET /api/tags

Get post tags

```
Method: GET
Path: /api/tags
Auth: ❌ Not required

Response: 200 OK
{
  "tags": [
    { "id": "uuid", "name": "AI" }
  ]
}
```

### GET /api/cms/status

Get CMS health status

```
Method: GET
Path: /api/cms/status
Auth: ❌ Not required

Response: 200 OK
{
  "status": "healthy|degraded|offline",
  "posts_count": 45,
  "last_sync": "ISO8601"
}
```

---

## Bulk Operations

**Route Module:** `bulk_task_routes.py`  
**Base Path:** `/api/bulk`  
**Auth Required:** All endpoints

### POST /api/bulk

Perform bulk operations

```
Method: POST
Path: /api/bulk
Auth: ✅ Required

Request Body:
{
  "operation": "update_status|delete|export|reassign",
  "task_ids": [ "uuid1", "uuid2", "uuid3" ],
  "parameters": {
    "new_status": "completed",
    "export_format": "csv|json"
  }
}

Response: 200 OK
{
  "operation_id": "uuid",
  "status": "completed|processing",
  "affected": 3,
  "result": { ... }
}
```

---

## Webhooks

**Route Module:** `webhooks.py`  
**Base Path:** `/api/webhooks`  
**Auth Required:** Varies

### POST /api/webhooks/

Handle incoming webhooks

```
Method: POST
Path: /api/webhooks/
Auth: ✅ Token-based (X-Webhook-Token header)

Request Body:
{
  "event": "task.completed|task.failed|deployment.success",
  "timestamp": "ISO8601",
  "data": { ... }
}

Response: 200 OK
{
  "acknowledged": true
}
```

---

## Authentication

**Route Module:** `auth_unified.py`  
**Base Path:** `/api/auth`  
**Auth Required:** Varies (see below)

### POST /api/auth/github/callback

GitHub OAuth callback

```
Method: POST
Path: /api/auth/github/callback
Auth: ❌ Not required

Request Body:
{
  "code": "github_oauth_code"
}

Response: 200 OK
{
  "access_token": "jwt_token",
  "user": { ... }
}
```

### POST /api/auth/logout

User logout

```
Method: POST
Path: /api/auth/logout
Auth: ✅ Required

Response: 200 OK
{
  "message": "Logged out successfully"
}
```

### GET /api/auth/me

Get current user info

```
Method: GET
Path: /api/auth/me
Auth: ✅ Required

Response: 200 OK
{
  "user_id": "uuid",
  "email": "user@example.com",
  "name": "User Name",
  "avatar_url": "https://..."
}
```

---

## Models Metadata

**Route Module:** `models.py`  
**Base Path:** `/api/models`  
**Auth Required:** None

### GET /api/models

Get available models

```
Method: GET
Path: /api/models
Auth: ❌ Not required

Response: 200 OK
{
  "models": [
    {
      "id": "claude-3-sonnet",
      "name": "Claude 3 Sonnet",
      "provider": "anthropic",
      "context_window": 200000,
      "pricing": { ... }
    }
  ]
}
```

### GET /api/models/{model_name}

Get model details

```
Method: GET
Path: /api/models/{model_name}
Auth: ❌ Not required

Response: 200 OK
{ ... model details ... }
```

### GET /api/models/list

Get models list

```
Method: GET
Path: /api/models/list
Auth: ❌ Not required

Response: 200 OK
{ ... models list ... }
```

### GET /api/models/{model_name}/info

Get model info

```
Method: GET
Path: /api/models/{model_name}/info
Auth: ❌ Not required

Response: 200 OK
{ ... model info ... }
```

### GET /api/models-list

Alternate models endpoint

```
Method: GET
Path: /api/models-list
Auth: ❌ Not required

Response: 200 OK
{ ... models list ... }
```

---

## Common Response Patterns

### Success Response

```json
{
  "data": { ... },
  "status": "success",
  "timestamp": "ISO8601"
}
```

### Error Response

```json
{
  "detail": "Error message",
  "status": 400,
  "type": "validation_error",
  "fields": {
    "field_name": "error details"
  }
}
```

### Paginated Response

```json
{
  "items": [ ... ],
  "total": 100,
  "offset": 0,
  "limit": 20,
  "has_more": true
}
```

---

## Authentication Header Format

```
Authorization: Bearer {jwt_token}

Token Structure:
- Type: JWT (JSON Web Token)
- Algorithm: HS256
- Secret: development-secret-key-change-in-production
- Expiration: 15 minutes
```

---

## Common Query Parameters

| Parameter | Type      | Purpose                     |
| --------- | --------- | --------------------------- |
| `limit`   | int       | Max results (default: 100)  |
| `offset`  | int       | Skip N results (default: 0) |
| `sort_by` | string    | Sort column                 |
| `order`   | asc\|desc | Sort order                  |
| `filter`  | string    | Filter criteria             |
| `search`  | string    | Search text                 |

---

**Last Updated:** 2024-12-09  
**Total Endpoints:** 97+  
**Coverage:** 100% of implemented functionality
