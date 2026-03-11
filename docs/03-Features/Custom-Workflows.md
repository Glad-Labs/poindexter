# Custom Workflows

Custom workflows allow users to create, list, update, delete, and execute workflow definitions with user-scoped access control.

## Primary Endpoints

- `POST /api/workflows/custom` - Create workflow
- `GET /api/workflows/custom` - List user workflows
- `GET /api/workflows/custom/{workflow_id}` - Get workflow details
- `PUT /api/workflows/custom/{workflow_id}` - Update workflow
- `DELETE /api/workflows/custom/{workflow_id}` - Delete workflow
- `POST /api/workflows/custom/{workflow_id}/execute` - Execute workflow

## Request/Response Examples

### Create Custom Workflow

**Request:**

```bash
curl -X POST http://localhost:8000/api/workflows/custom \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Custom Content Pipeline",
    "description": "Research → Draft → QA → Publish",
    "phases": [
      {
        "index": 0,
        "name": "research",
        "user_inputs": {"topic": "AI Trends 2026"},
        "input_mapping": {}
      },
      {
        "index": 1,
        "name": "draft",
        "user_inputs": {},
        "input_mapping": {"research_summary": "research.summary"}
      },
      {
        "index": 2,
        "name": "assess",
        "user_inputs": {"quality_threshold": 0.85},
        "input_mapping": {"content": "draft.content"}
      }
    ],
    "tags": ["content", "automation"]
  }'
```

**Response:**

```json
{
  "id": "wf-550e8400-e29b-41d4",
  "name": "My Custom Content Pipeline",
  "description": "Research → Draft → QA → Publish",
  "owner_id": "dev-user-123",
  "created_at": "2026-03-08T14:35:00Z",
  "updated_at": "2026-03-08T14:35:00Z",
  "phases": [...],
  "tags": ["content", "automation"],
  "is_template": false
}
```

### List User Workflows

**Request:**

```bash
curl -X GET "http://localhost:8000/api/workflows/custom?limit=10&offset=0" \
  -H "Authorization: Bearer dev-token"
```

**Response:**

```json
{
  "workflows": [
    {
      "id": "wf-550e8400-e29b-41d4",
      "name": "My Custom Content Pipeline",
      "description": "Research → Draft → QA → Publish",
      "owner_id": "dev-user-123",
      "created_at": "2026-03-08T14:35:00Z",
      "updated_at": "2026-03-08T14:35:00Z",
      "tags": ["content", "automation"],
      "is_template": false
    }
  ],
  "total_count": 1,
  "limit": 10,
  "offset": 0,
  "has_next": false
}
```

### Execute Custom Workflow

**Request:**

```bash
curl -X POST "http://localhost:8000/api/workflows/custom/wf-550e8400-e29b-41d4/execute" \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "input_data": {
      "topic": "AI Trends 2026",
      "audience": "Tech Decision Makers"
    },
    "model_tier": "standard"
  }'
```

**Response:**

```json
{
  "execution_id": "exec-550e8400-e29b-41d4",
  "workflow_id": "wf-550e8400-e29b-41d4",
  "status": "running",
  "current_phase": "research",
  "progress_percent": 0,
  "phase_results": {},
  "created_at": "2026-03-08T14:36:00Z"
}
```

### Update Workflow

**Request:**

```bash
curl -X PUT "http://localhost:8000/api/workflows/custom/wf-550e8400-e29b-41d4" \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Updated Content Pipeline",
    "description": "New description",
    "phases": [...],
    "tags": ["content", "updated"]
  }'
```

## Available Phases Discovery

**Request:**

```bash
curl -X GET "http://localhost:8000/api/workflows/available-phases" \
  -H "Authorization: Bearer dev-token"
```

**Response:**

```json
{
  "phases": [
    {
      "name": "research",
      "description": "Gather background research",
      "input_schema": { "topic": "string" },
      "output_schema": { "summary": "string", "sources": ["string"] }
    },
    {
      "name": "draft",
      "description": "Generate content draft",
      "input_schema": { "research_summary": "string" },
      "output_schema": { "content": "string", "word_count": "integer" }
    }
  ],
  "total_count": 12
}
```

## Key Implementation Files

- [src/cofounder_agent/routes/custom_workflows_routes.py](../../src/cofounder_agent/routes/custom_workflows_routes.py)
- [src/cofounder_agent/services/custom_workflows_service.py](../../src/cofounder_agent/services/custom_workflows_service.py)
- [src/cofounder_agent/schemas/custom_workflow_schemas.py](../../src/cofounder_agent/schemas/custom_workflow_schemas.py)

## Notes

- User identity is resolved from JWT bearer token or development token in local workflows
- All workflows are scoped to owner_id for multi-tenant access control
- Input mapping defines how previous phase outputs feed into subsequent phases
- Workflows can be tagged for organization and discovery
- Template execution internally uses the custom workflow execution path for consistency
