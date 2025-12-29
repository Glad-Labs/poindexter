# 📚 API Documentation Guide

## Overview

The Glad Labs AI Co-Founder exposes a comprehensive REST API with **50+ authenticated endpoints** organized across 17 route modules. API documentation is automatically generated using OpenAPI/Swagger and ReDoc specifications.

## 🚀 Accessing API Documentation

### Interactive Swagger UI

**URL**: [http://localhost:8000/api/docs](http://localhost:8000/api/docs)

The Swagger UI provides:

- **Interactive API Explorer**: Try endpoints directly from your browser
- **Request/Response Examples**: See live request and response formats
- **Parameter Documentation**: Understand required fields, data types, and constraints
- **Authentication Testing**: Test endpoints with your JWT token
- **Schema Validation**: Visual validation of request/response schemas

### ReDoc Documentation

**URL**: [http://localhost:8000/api/redoc](http://localhost:8000/api/redoc)

ReDoc provides:

- **Beautiful Documentation**: Organized, searchable API reference
- **Schema Details**: Complete schema definitions for all models
- **Code Examples**: Language-specific request examples
- **Better for Reading**: Optimized for reference documentation

### OpenAPI Specification

**URL**: [http://localhost:8000/api/openapi.json](http://localhost:8000/api/openapi.json)

Raw OpenAPI JSON schema for programmatic access. Useful for:

- Code generation tools
- API client libraries
- CI/CD pipeline integration
- Third-party API documentation tools

## 📋 API Organization

### Authentication (7 endpoints)

**Prefix**: `/api/auth`
**Module**: `routes/auth_unified.py`

- `POST /api/auth/github/callback` - GitHub OAuth authentication
- `POST /api/auth/logout` - Logout for all authentication types
- `GET /api/auth/me` - Get current authenticated user profile

### Tasks & Execution (25+ endpoints)

**Prefix**: `/api/tasks` and `/api/subtasks`
**Modules**: `routes/task_routes.py`, `routes/subtask_routes.py`, `routes/bulk_task_routes.py`

- `POST /api/tasks` - Create new task
- `GET /api/tasks/{task_id}` - Get task details
- `PUT /api/tasks/{task_id}` - Update task
- `DELETE /api/tasks/{task_id}` - Delete task
- `GET /api/tasks/pending` - Get pending tasks
- `POST /api/subtasks` - Create independent subtask
- `POST /api/tasks/bulk` - Create multiple tasks at once
- And more...

### Content Management (15+ endpoints)

**Prefix**: `/api/content`
**Modules**: `routes/content_routes.py`, `routes/cms_routes.py`

- `POST /api/content/generate` - Generate AI content
- `GET /api/content/{content_id}` - Retrieve content
- `PUT /api/content/{content_id}` - Update content
- `POST /api/content/evaluate` - Quality evaluation
- `POST /api/content/publish` - Multi-channel publishing
- `GET /api/cms/pages` - List CMS pages
- `POST /api/cms/pages` - Create CMS page
- And more...

### Models & AI Integration (10+ endpoints)

**Prefix**: `/api/models`
**Modules**: `routes/models.py`

- `GET /api/models` - List available models
- `GET /api/models/providers` - List model providers
- `POST /api/models/switch` - Switch active model
- `POST /api/chat` - Chat with AI model
- And more...

### Settings & Configuration (5+ endpoints)

**Prefix**: `/api/settings`
**Module**: `routes/settings_routes.py`

- `GET /api/settings` - Get all settings
- `PUT /api/settings` - Update settings
- `GET /api/settings/{setting_name}` - Get specific setting
- And more...

### Metrics & Monitoring (8+ endpoints)

**Prefix**: `/api/metrics`
**Module**: `routes/metrics_routes.py`

- `GET /api/health` - Unified health check
- `GET /api/metrics` - Task metrics
- `GET /api/metrics/performance` - Performance analytics
- `POST /api/metrics/reset` - Reset metrics
- And more...

### Additional Endpoints

- **Workflows**: `GET /api/workflows` - Workflow history and persistence
- **Agents**: `GET /api/agents` - Agent management and monitoring
- **Social Media**: `POST /api/social/publish` - Multi-platform publishing
- **Webhooks**: `POST /api/webhooks` - Event handling
- **Command Queue**: `POST /api/commands` - Command processing
- **Ollama Integration**: `GET /api/ollama/health` - Local LLM health

## 🔐 Authentication

### JWT Token Authentication

Most endpoints require authentication via JWT tokens:

```bash
curl -X GET http://localhost:8000/api/tasks \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
```

### Obtaining Tokens

#### Via GitHub OAuth

```bash
POST /api/auth/github/callback
Content-Type: application/json

{
  "code": "github_authorization_code",
  "state": "random_state_for_csrf_protection"
}
```

#### Response

```json
{
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "user": {
    "username": "octocat",
    "email": "user@example.com",
    "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
    "user_id": "1",
    "auth_provider": "github"
  }
}
```

### Token Expiration

Tokens are valid for the duration of your session. To logout:

```bash
POST /api/auth/logout
Authorization: Bearer <your_token>
```

## 📊 Response Formats

### Success Response (200 OK)

```json
{
  "status": "success",
  "data": { "task_id": "123", "status": "pending" },
  "timestamp": "2025-12-07T10:30:00Z"
}
```

### Error Response (4xx/5xx)

```json
{
  "detail": "Task not found",
  "status_code": 404,
  "request_id": "req_123456"
}
```

### Health Check Response

```json
{
  "status": "healthy",
  "service": "cofounder-agent",
  "version": "3.0.1",
  "timestamp": "2025-12-07T10:30:00Z",
  "components": {
    "database": "healthy"
  }
}
```

## 🧪 Testing the API

### Using Swagger UI

1. Open [http://localhost:8000/api/docs](http://localhost:8000/api/docs)
2. Click **"Authorize"** button and enter your JWT token
3. Click **"Try it out"** on any endpoint
4. Enter parameters and click **"Execute"**

### Using cURL

```bash
# Get health status (no auth required)
curl http://localhost:8000/api/health

# Create a task (requires auth)
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Write blog post",
    "description": "Create a blog post about AI"
  }'
```

### Using Python Requests

```python
import requests

headers = {
    "Authorization": "Bearer YOUR_TOKEN",
    "Content-Type": "application/json"
}

# Get pending tasks
response = requests.get(
    "http://localhost:8000/api/tasks/pending",
    headers=headers
)
tasks = response.json()

# Create new task
new_task = {
    "title": "Analyze market trends",
    "description": "Research competitor pricing"
}
response = requests.post(
    "http://localhost:8000/api/tasks",
    json=new_task,
    headers=headers
)
task_id = response.json()["task_id"]
```

### Using JavaScript/Fetch

```javascript
const token = 'YOUR_JWT_TOKEN';

// Get metrics
const metricsResponse = await fetch('http://localhost:8000/api/metrics', {
  method: 'GET',
  headers: {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  },
});
const metrics = await metricsResponse.json();

// Create task
const taskResponse = await fetch('http://localhost:8000/api/tasks', {
  method: 'POST',
  headers: {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    title: 'Generate social media content',
    priority: 'high',
  }),
});
const task = await taskResponse.json();
```

## 🔧 Endpoint Categories

### Health & Status (3 endpoints)

- `GET /api/health` - Unified health check with component status
- `GET /status` - Legacy status endpoint
- `GET /metrics/health` - Legacy health metrics

### Task Management (20+ endpoints)

Complete task lifecycle: create, list, get, update, delete, execute, monitor

### Content Operations (15+ endpoints)

Content creation, evaluation, publishing, and quality scoring

### Model Management (10+ endpoints)

List models, switch providers, chat interface

### System Configuration (8+ endpoints)

Settings, metrics, workflows, logging, debugging

## 📈 Performance Considerations

### Rate Limiting

API is protected with rate limiting to prevent abuse:

- Default: 100 requests per minute per IP
- Authenticated users: 300 requests per minute
- Response: 429 Too Many Requests when limit exceeded

### Pagination

List endpoints support pagination:

```bash
GET /api/tasks?skip=0&limit=10
```

### Async Processing

Long-running operations use background tasks:

```json
{
  "task_id": "task_123",
  "status": "processing",
  "status_url": "/api/tasks/task_123/status"
}
```

## 🐛 Debugging & Troubleshooting

### Enable Debug Logging

```bash
export LOG_LEVEL=DEBUG
python -m uvicorn main:app --reload
```

### Check Startup Status

```bash
GET /api/debug/startup
```

Response includes:

- Startup completion status
- Any initialization errors
- Service availability
- Environment configuration

### API Request Logging

All requests are logged with:

- Request method and path
- Response status code
- Execution time
- User authentication status
- Error details (if applicable)

Check logs in: `src/cofounder_agent/server.log`

## 📚 Schema Documentation

### Key Schemas

#### Task

```json
{
  "task_id": "task_123",
  "title": "string",
  "description": "string",
  "status": "pending|running|completed|failed",
  "priority": "low|normal|high|critical",
  "created_at": "2025-12-07T10:30:00Z",
  "updated_at": "2025-12-07T10:30:00Z",
  "assigned_to": "string|null",
  "result": "object|null"
}
```

#### Content

```json
{
  "content_id": "content_123",
  "title": "string",
  "body": "string",
  "type": "article|social_post|email|video_script",
  "status": "draft|published|archived",
  "quality_score": 0.0-100.0,
  "created_at": "2025-12-07T10:30:00Z",
  "published_at": "2025-12-07T10:30:00Z|null"
}
```

## 🔗 API Integration Examples

See `/docs/INTEGRATION_EXAMPLE_QA_BRIDGE.md` for complete integration examples.

## 📞 Support

- **Issues**: Create a GitHub issue
- **Email**: support@gladlabs.io
- **Documentation**: [Full Docs Hub](./00-README.md)
- **Architecture**: [System Architecture](./02-ARCHITECTURE_AND_DESIGN.md)

---

**Last Updated**: December 7, 2025  
**API Version**: 3.0.1  
**Status**: ✅ Production Ready
