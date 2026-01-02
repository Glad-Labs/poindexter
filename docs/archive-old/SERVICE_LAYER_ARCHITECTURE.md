# Service Layer Architecture for LLM Integration

**Date:** January 1, 2026  
**Purpose:** Enable natural language workflows by providing modular, reusable services discoverable by LLMs

---

## Overview

This architecture transforms the FastAPI backend into a service tool layer that LLMs can:

1. **Discover** - Query available services and actions
2. **Understand** - Read action schemas to understand parameters and outputs
3. **Execute** - Call actions with natural language parameters
4. **Chain** - Compose multiple services into workflows
5. **Extend** - Add new services without modifying existing ones

---

## Core Concepts

### ServiceBase

Base class that all services inherit from. Provides:

- Standardized action interface (all services expose actions)
- Action registry (define available operations)
- Async execution (all actions are async)
- Error handling (consistent error codes)
- Service composition (call other services)

### ServiceAction

Definition of a single operation a service can perform:

- **name**: Unique identifier for the action
- **description**: What the action does (for LLM understanding)
- **input_schema**: JSON Schema defining required/optional parameters
- **output_schema**: JSON Schema defining the response structure
- **error_codes**: Possible error codes this action can return

### ServiceRegistry

Central catalog of all services:

- Registers services
- Executes actions across services
- Provides registry schema for LLM consumption
- Manages service discovery

### ActionResult

Standardized response format:

```python
{
    "action": "create_task",
    "status": "completed|failed",
    "data": {...},           # Result data if successful
    "error": "...",          # Error message if failed
    "error_code": "...",     # Standard error code
    "execution_time_ms": 45.2,
    "timestamp": "2026-01-01T18:00:00Z"
}
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────┐
│           LLM (Claude, GPT, etc.)                   │
│  - Interprets natural language                      │
│  - Queries service registry                         │
│  - Chains service calls                             │
└─────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────┐
│         API Layer (services_registry_routes.py)     │
│  GET  /api/services              - List services   │
│  GET  /api/services/registry     - Get all actions │
│  POST /api/services/{s}/actions/{a} - Execute     │
└─────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────┐
│      Service Registry (service_base.py)             │
│  - Maintains registry of all services              │
│  - Routes action calls to correct service          │
│  - Enables service-to-service composition          │
└─────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────┐
│    Service Layer (multiple ServiceBase subclasses)  │
│                                                      │
│  TaskService          ContentService               │
│  - create_task       - research                    │
│  - list_tasks        - draft                       │
│  - get_task          - critique                    │
│  - update_status     - refine                      │
│                                                      │
│  PublishingService   MetricsService               │
│  - publish_twitter   - track_cost                 │
│  - publish_linkedin  - get_metrics                │
│  - send_email        - analyze_performance        │
└─────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────┐
│      Database Layer (PostgreSQL)                    │
│  - Persistent storage                              │
│  - Transaction support                             │
└─────────────────────────────────────────────────────┘
```

---

## How LLMs Use This System

### Step 1: Discover Available Services

```bash
GET /api/services
Authorization: Bearer YOUR_JWT_TOKEN
```

Response:

```json
[
  {
    "name": "tasks",
    "version": "1.0.0",
    "description": "Manage content generation tasks",
    "actions_count": 4
  },
  {
    "name": "content",
    "version": "1.0.0",
    "description": "Generate and manage content",
    "actions_count": 5
  }
]
```

### Step 2: Query Complete Registry

```bash
GET /api/services/registry
Authorization: Bearer YOUR_JWT_TOKEN
```

Response: Complete schema with all service definitions and action inputs/outputs

### Step 3: Execute an Action

```bash
POST /api/services/tasks/actions/create_task
Authorization: Bearer YOUR_JWT_TOKEN
Content-Type: application/json

{
    "params": {
        "task_name": "Blog Post - AI Ethics",
        "topic": "Ethical considerations in AI",
        "category": "technology",
        "primary_keyword": "AI ethics"
    }
}
```

Response:

```json
{
  "action": "create_task",
  "status": "completed",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "pending",
    "created_at": "2026-01-01T18:00:00Z"
  },
  "execution_time_ms": 45.2,
  "timestamp": "2026-01-01T18:00:00Z"
}
```

### Step 4: Chain Multiple Services

LLM interprets natural language into workflow:

```
User: "Create a blog post about AI ethics and publish it to Twitter"
```

LLM breaks this into:

1. Call `tasks.create_task` with topic
2. Call `content.generate` with task_id
3. Call `publishing.publish_twitter` with content_id

---

## Creating a New Service

### Basic Template

```python
from services.service_base import ServiceBase, ServiceAction, JsonSchema

class MyService(ServiceBase):
    name = "my_service"
    version = "1.0.0"
    description = "Brief description"

    def get_actions(self) -> List[ServiceAction]:
        return [
            ServiceAction(
                name="my_action",
                description="What this action does",
                input_schema=JsonSchema(
                    type="object",
                    properties={
                        "param1": {"type": "string"},
                        "param2": {"type": "integer"},
                    },
                    required=["param1"]
                ),
                output_schema=JsonSchema(
                    type="object",
                    properties={
                        "result": {"type": "string"},
                    },
                    required=["result"]
                ),
                error_codes=["VALIDATION_ERROR", "EXECUTION_ERROR"]
            )
        ]

    async def action_my_action(self, param1: str, param2: int = 0) -> dict:
        """Implementation of my_action"""
        # Your logic here
        return {"result": f"Processed {param1}"}
```

### Register Service

```python
# In main.py or startup function
from services.service_base import get_service_registry
from services.my_service import MyService

registry = get_service_registry()
registry.register(MyService())
```

### Call Other Services (Composition)

```python
# Inside an action method
async def action_complex_workflow(self, task_id: str) -> dict:
    # Call another service
    result = await self.call_service(
        service_name="content",
        action_name="generate",
        params={"task_id": task_id}
    )

    if result.status == "completed":
        # Use the result
        content_id = result.data["id"]

        # Call another service
        publish_result = await self.call_service(
            service_name="publishing",
            action_name="publish_twitter",
            params={"content_id": content_id}
        )

        return {
            "task_id": task_id,
            "content_id": content_id,
            "published": publish_result.status == "completed"
        }
```

---

## Migration Strategy: Converting Existing Services

### Phase 1: Foundation (Week 1)

1. Create 5 core services using ServiceBase:
   - TaskService (task management)
   - ContentService (content generation)
   - PublishingService (publishing)
   - DatabaseService (data access)
   - MetricsService (analytics)

2. Implement service discovery endpoints
3. Add registry schema endpoint

### Phase 2: Expansion (Weeks 2-3)

4. Convert remaining 20 services to ServiceBase
5. Update all intra-service communication to use registry
6. Add service composition tests

### Phase 3: Integration (Weeks 4+)

7. Connect LLM to service registry
8. Implement workflow interpretation
9. Add workflow persistence and history

---

## Key Advantages

### For Developers

- ✅ **Modularity**: Each service is independent
- ✅ **Reusability**: Services can be used in any combination
- ✅ **Testability**: Each action can be tested in isolation
- ✅ **Documentation**: Auto-generated via schemas
- ✅ **Composability**: Services can call other services

### For LLMs

- ✅ **Discoverability**: Query `/api/services/registry` to see available tools
- ✅ **Type Safety**: JSON Schema ensures valid parameters
- ✅ **Clear Contracts**: Input/output schemas are explicit
- ✅ **Error Handling**: Standard error codes for error recovery
- ✅ **Composition**: Can chain services into workflows

### For End Users

- ✅ **Natural Language**: "Create a blog post and publish it"
- ✅ **Flexible Workflows**: LLM decides how to break it down
- ✅ **Error Recovery**: LLM can handle and retry failures
- ✅ **Progress Tracking**: Real-time updates on execution

---

## API Endpoints Reference

| Endpoint                                | Method | Purpose                      |
| --------------------------------------- | ------ | ---------------------------- |
| `/api/services`                         | GET    | List all registered services |
| `/api/services/registry`                | GET    | Get complete registry schema |
| `/api/services/{name}`                  | GET    | Get service details          |
| `/api/services/{name}/actions`          | GET    | List service actions         |
| `/api/services/{name}/actions/{action}` | POST   | Execute an action            |
| `/api/services/health`                  | GET    | Check registry health        |

---

## Example Workflows

### Workflow 1: Create and Publish Blog Post

```
LLM Instruction: "Write a blog post about AI ethics and share on Twitter and LinkedIn"

Step 1: Create Task
  POST /api/services/tasks/actions/create_task
  Params: {task_name: "Blog Post - AI Ethics", topic: "AI ethics"}
  Result: task_id = "123"

Step 2: Generate Content
  POST /api/services/content/actions/generate
  Params: {task_id: "123"}
  Result: content_id = "456"

Step 3: Publish to Twitter
  POST /api/services/publishing/actions/publish_twitter
  Params: {content_id: "456"}
  Result: twitter_url = "https://twitter.com/..."

Step 4: Publish to LinkedIn
  POST /api/services/publishing/actions/publish_linkedin
  Params: {content_id: "456"}
  Result: linkedin_url = "https://linkedin.com/..."

Final Result:
  {
    "task_id": "123",
    "content_id": "456",
    "twitter": "https://twitter.com/...",
    "linkedin": "https://linkedin.com/..."
  }
```

### Workflow 2: Analyze Performance and Optimize

```
LLM Instruction: "Show me metrics for my blog posts and suggest improvements"

Step 1: Get Metrics
  POST /api/services/metrics/actions/get_performance
  Params: {time_period: "last_30_days"}
  Result: metrics = {views: 1500, engagement: 0.08, ...}

Step 2: Analyze
  POST /api/services/metrics/actions/analyze
  Params: {metrics: {...}}
  Result: insights = ["Low engagement, improve CTA", "Good reach", ...]

Step 3: Suggest Improvements
  POST /api/services/content/actions/suggest_improvements
  Params: {metrics: {...}, insights: [...]}
  Result: recommendations = ["Add more examples", "Improve headlines", ...]

Final Result:
  {
    "metrics": {...},
    "insights": [...],
    "recommendations": [...]
  }
```

---

## Error Handling

All services follow standard error code pattern:

```json
{
  "action": "create_task",
  "status": "failed",
  "error": "Task name cannot be empty",
  "error_code": "VALIDATION_ERROR",
  "metadata": {
    "service": "tasks",
    "details": { "field": "task_name" }
  },
  "execution_time_ms": 12.3
}
```

Common error codes:

- `VALIDATION_ERROR` - Input parameters invalid
- `NOT_FOUND` - Resource not found
- `UNAUTHORIZED` - Authentication required
- `DATABASE_ERROR` - Database operation failed
- `SERVICE_NOT_FOUND` - Service doesn't exist
- `ACTION_NOT_FOUND` - Action doesn't exist
- `TIMEOUT` - Operation exceeded time limit

---

## Testing

```python
import pytest
from services.service_base import get_service_registry
from services.task_service_example import TaskService

@pytest.fixture
def task_service():
    registry = get_service_registry()
    service = TaskService(registry)
    registry.register(service)
    return service

@pytest.mark.asyncio
async def test_create_task(task_service):
    result = await task_service.execute_action(
        action_name="create_task",
        params={
            "task_name": "Test Task",
            "topic": "Test Topic"
        }
    )

    assert result.status == "completed"
    assert result.data["id"] is not None
    assert result.data["status"] == "pending"
```

---

## Next Steps

1. **Create Core Services** - TaskService, ContentService, PublishingService
2. **Register All Services** - Update main.py startup
3. **Add Test Suite** - Test all service actions
4. **Document Actions** - Add examples to each action
5. **Connect LLM** - Wire up language model to call services
6. **Build Workflow UI** - Show workflows being executed
7. **Add Persistence** - Save workflow history

---

## Related Files

- `services/service_base.py` - ServiceBase, ServiceRegistry, ServiceAction definitions
- `services/task_service_example.py` - Example service implementation
- `routes/services_registry_routes.py` - API endpoints for service discovery and execution
- `tests/test_service_layer.py` - Test suite for service layer

---

**Questions?** Refer to the TaskService example or create an issue with questions about extending the system.
