# Capability System - Quick Start Guide

Get started with the capability-based task system in under 5 minutes.

---

## 1. List Available Capabilities

```bash
curl http://localhost:8000/api/capabilities | jq '.'
```

**Response:**

```json
{
  "capabilities": [
    {
      "name": "research",
      "description": "Research a topic and gather information",
      "tags": ["research"],
      "input_schema": {
        "parameters": [
          {
            "name": "topic",
            "type": "string",
            "required": true
          }
        ]
      }
    },
    // ... more capabilities
  ],
  "total": 7
}
```

---

## 2. Create a Task (Chain Capabilities Together)

```bash
curl -X POST http://localhost:8000/api/tasks/capability \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Content Creation Workflow",
    "description": "Research → Generate → Critique → Publish",
    "steps": [
      {
        "capability_name": "research",
        "inputs": {"topic": "AI trends", "depth": "deep"},
        "output_key": "research_data",
        "order": 0
      },
      {
        "capability_name": "generate_content",
        "inputs": {
          "topic": "AI trends",
          "style": "professional",
          "length": "medium"
        },
        "output_key": "draft",
        "order": 1
      },
      {
        "capability_name": "critique",
        "inputs": {"content": "$draft"},
        "output_key": "feedback",
        "order": 2
      },
      {
        "capability_name": "publish",
        "inputs": {
          "content": "$draft",
          "platform": "blog"
        },
        "output_key": "published",
        "order": 3
      }
    ],
    "tags": ["content", "automated"]
  }'
```

**Save the returned task ID:**

```json
{
  "id": "task_abc123xyz",
  "name": "Content Creation Workflow",
  "owner_id": "user_123",
  "created_at": "2024-02-12T10:30:00"
}
```

---

## 3. Execute the Task

```bash
TASK_ID="task_abc123xyz"

curl -X POST http://localhost:8000/api/tasks/capability/$TASK_ID/execute \
  -H "Authorization: Bearer <your_jwt_token>"
```

**Response (Execution Started):**

```json
{
  "execution_id": "exec_xyz789",
  "task_id": "task_abc123xyz",
  "status": "running"
}
```

---

## 4. Check Execution Results

```bash
EXEC_ID="exec_xyz789"

curl http://localhost:8000/api/tasks/capability/$TASK_ID/executions/$EXEC_ID
```

**Response (Full Results):**

```json
{
  "execution_id": "exec_xyz789",
  "task_id": "task_abc123xyz",
  "status": "completed",
  "step_results": [
    {
      "step_index": 0,
      "capability_name": "research",
      "output_key": "research_data",
      "output": {
        "topic": "AI trends",
        "findings": "...",
        "sources": ["source1", "source2"]
      },
      "duration_ms": 1250,
      "status": "completed"
    },
    // ... more steps ...
  ],
  "final_outputs": {
    "research_data": {...},
    "draft": {...},
    "feedback": {...},
    "published": {...}
  },
  "total_duration_ms": 6500,
  "progress_percent": 100,
  "started_at": "2024-02-12T10:31:00",
  "completed_at": "2024-02-12T10:31:06.5"
}
```

---

## Key Concepts in 30 Seconds

### Capability

A single reusable function/method with defined inputs and outputs

- Example: `research`, `generate_content`, `critique`, `publish`

### Task

A sequence of capabilities where:

- Each capability produces outputs
- Outputs can feed into next capability's inputs
- Use `$output_key` references to pass data forward

### Step

One capability execution in a task

- Runs in sequence by default
- Has inputs, capability_name, and output_key

### Variable References

Pass data between steps using references:

```json
{
  "capability_name": "step_2",
  "inputs": {
    "data": "$step_1_output"  // Reference to previous step's output_key
  }
}
```

---

## Common Patterns

### Pattern 1: Linear Pipeline

Research → Generate → Critique → Publish

### Pattern 2: Branch & Merge

```
       ├→ Generate Text
Input→┤
       ├→ Select Images
         └→ Combine → Publish
```

### Pattern 3: Conditional (coming Phase 2)

```
Generate → Critique → if score > 8? 
                        No: Regenerate
                        Yes: Publish
```

---

## Manage Tasks

### List Your Tasks

```bash
curl http://localhost:8000/api/tasks/capability?skip=0&limit=50
```

### Get Task Details

```bash
curl http://localhost:8000/api/tasks/capability/task_abc123xyz
```

### Update Task

```bash
curl -X PUT http://localhost:8000/api/tasks/capability/task_abc123xyz \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Updated Name",
    "steps": [...]
  }'
```

### Delete Task

```bash
curl -X DELETE http://localhost:8000/api/tasks/capability/task_abc123xyz
```

### View Execution History

```bash
curl http://localhost:8000/api/tasks/capability/task_abc123xyz/executions?limit=10
```

---

## Create Your Own Capability

### Option A: Simple Function

```python
from services.capability_registry import get_registry, InputSchema, OutputSchema, ParameterSchema, ParameterType

async def my_awesome_capability(param1: str) -> dict:
    """Do something cool."""
    result = await some_processing(param1)
    return {"output": result}

# Register it
registry = get_registry()
registry.register_function(
    func=my_awesome_capability,
    name="my_awesome_capability",
    description="Do something cool",
    input_schema=InputSchema(parameters=[
        ParameterSchema(
            name="param1",
            type=ParameterType.STRING,
            description="Input parameter",
            required=True,
        ),
    ]),
    output_schema=OutputSchema(description="Results"),
    tags=["custom"],
    cost_tier="balanced",
)
```

### Option B: Class-Based (With Dependencies)

```python
from services.capability_registry import Capability, CapabilityMetadata

class MyCapability(Capability):
    def __init__(self, service):
        self.service = service
    
    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="my_capability",
            description="Description",
            tags=["tag"],
        )
    
    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(parameters=[...])
    
    @property
    def output_schema(self) -> OutputSchema:
        return OutputSchema(...)
    
    async def execute(self, **inputs) -> dict:
        result = await self.service.process(inputs['param'])
        return result

# Register
registry = get_registry()
registry.register(MyCapability(my_service))
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Capability not found | Check spelling, list all capabilities with `GET /api/capabilities` |
| Variable reference not working | Use exact format `$output_key`, must match previous step's output_key |
| Execution fails at step 2 | Check step 2's inputs are valid for that capability |
| Input validation error | Check input types match capability's input_schema |
| Database error | Run migration: `cd src/cofounder_agent && alembic upgrade head` |

---

## Complete Example: Content Creation

**Step 1: Create the task**

```bash
curl -X POST http://localhost:8000/api/tasks/capability \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Blog Post Generator",
    "steps": [
      {"capability_name": "research", "inputs": {"topic": "Python 3.12"}, "output_key": "research", "order": 0},
      {"capability_name": "generate_content", "inputs": {"topic": "Python 3.12"}, "output_key": "content", "order": 1},
      {"capability_name": "critique", "inputs": {"content": "$content"}, "output_key": "feedback", "order": 2},
      {"capability_name": "select_images", "inputs": {"topic": "Python", "count": 3}, "output_key": "images", "order": 3},
      {"capability_name": "publish", "inputs": {"content": "$content", "platform": "blog"}, "output_key": "published", "order": 4}
    ],
    "tags": ["blog", "python"]
  }' | jq -r '.id'
```

**Step 2: Save the task ID and execute**

```bash
TASK_ID="<returned_task_id>"
curl -X POST http://localhost:8000/api/tasks/capability/$TASK_ID/execute | jq -r '.execution_id'
```

**Step 3: Check results (poll until completed)**

```bash
EXEC_ID="<returned_execution_id>"
curl http://localhost:8000/api/tasks/capability/$TASK_ID/executions/$EXEC_ID | jq '.status'
# When status is "completed", view final_outputs:
curl http://localhost:8000/api/tasks/capability/$TASK_ID/executions/$EXEC_ID | jq '.final_outputs'
```

---

## API Endpoints Reference

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/capabilities` | GET | List all capabilities |
| `/api/capabilities/{name}` | GET | Get capability details |
| `/api/tasks/capability` | POST | Create task |
| `/api/tasks/capability` | GET | List tasks (pagination) |
| `/api/tasks/capability/{id}` | GET | Get task details |
| `/api/tasks/capability/{id}` | PUT | Update task |
| `/api/tasks/capability/{id}` | DELETE | Delete task |
| `/api/tasks/capability/{id}/execute` | POST | Execute task |
| `/api/tasks/capability/{id}/executions/{exec_id}` | GET | Get execution result |
| `/api/tasks/capability/{id}/executions` | GET | List execution history |

---

## Need More Info?

- **Full Documentation:** `docs/CAPABILITY_BASED_TASK_SYSTEM.md`
- **Architecture Details:** `docs/CAPABILITIES_PHASE_1_SUMMARY.md`
- **Example Code:** `services/capability_examples.py`
- **Source Code:** `services/capability_*.py`, `routes/capability_tasks_routes.py`

---

**Ready to build? Start with Step 1 above!** 🚀
