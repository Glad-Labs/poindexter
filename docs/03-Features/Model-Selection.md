# Model Selection

Workflow executions can capture a caller-selected model and persist that choice for traceability and analytics. Model selection enables users to specify which LLM provider/model should execute workflow phases.

## How It Works

1. Client includes a `model` key in workflow template input payload
2. Template execution extracts the selected model from `task_input`
3. Custom workflow execution receives `selected_model` and injects it into execution inputs
4. Execution persistence stores `selected_model` in workflow execution records
5. Automatic fallback applies if primary model is unavailable

## Request Examples

### Template Execution with Model Selection

**Request:**

```bash
curl -X POST http://localhost:8000/api/workflows/execute/blog_post \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "The Future of AI",
    "keywords": ["artificial intelligence", "machine learning"],
    "target_audience": "Technical professionals",
    "model": "claude-3-5-sonnet"
  }'
```

**Response:**

```json
{
  "execution_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_id": "550e8400-e29b-41d4-a716-446655440001",
  "selected_model": "claude-3-5-sonnet",
  "fallback_chain": ["gpt-4-turbo", "gemini-1.5-pro"],
  "status": "running",
  "current_phase": "research",
  "progress_percent": 0,
  "created_at": "2026-03-08T14:35:00Z"
}
```

### Custom Workflow Execution with Model Selection

**Request:**

```bash
curl -X POST "http://localhost:8000/api/workflows/custom/wf-550e8400/execute" \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "input_data": {
      "topic": "Market Analysis",
      "depth": "comprehensive"
    },
    "model": "gpt-4-turbo"
  }'
```

**Response:**

```json
{
  "execution_id": "exec-550e8400-e29b-41d4",
  "workflow_id": "wf-550e8400",
  "selected_model": "gpt-4-turbo",
  "status": "running",
  "progress_percent": 0,
  "created_at": "2026-03-08T14:36:00Z"
}
```

## Available Models

**Cost Tiers:**

- **Ultra-cheap** (local, zero API cost): `ollama/mistral`, `ollama/llama2`, `ollama/neural-chat`
- **Cheap** (low API cost): `gemini-1.5-flash`
- **Balanced** (standard cost): `claude-3-5-sonnet`, `gpt-4-turbo`
- **Premium** (high cost): `claude-3-opus`, `gpt-4o`
- **Ultra-premium** (multi-model ensemble): Multi-step verification

**Available Providers:**

- **Anthropic Claude**: `claude-3-5-sonnet`, `claude-3-opus`, `claude-3-haiku`
- **OpenAI**: `gpt-4-turbo`, `gpt-4o`, `gpt-3.5-turbo`
- **Google Gemini**: `gemini-1.5-pro`, `gemini-1.5-flash`
- **Ollama (local)**: `ollama/mistral`, `ollama/llama2`, `ollama/dolphin-mixtral`

## Automatic Fallback Chain

If selected model is unavailable, system automatically tries:

```plaintext
Ollama (local) → Anthropic → OpenAI → Google Gemini → Echo/Mock
```

**Fallback Example:**

- Request selection: `claude-3-5-sonnet`
- Check API key: ✓ ANTHROPIC_API_KEY available
- Check service: ✗ Rate limited
- Fallback to: `gpt-4-turbo` (OpenAI)
- Check service: ✓ Available
- Execution: Uses OpenAI with fallback logged

## Analytics and Cost Tracking

**Query Cost by Model:**

```bash
curl -X GET "http://localhost:8000/api/analytics/model-usage?range=7d" \
  -H "Authorization: Bearer dev-token"
```

**Response:**

```json
{
  "period": "7d",
  "total_executions": 247,
  "by_model": {
    "claude-3-5-sonnet": {
      "count": 150,
      "percentage": 60.7,
      "total_cost": 7.50,
      "avg_execution_time_ms": 4234
    },
    "gpt-4-turbo": {
      "count": 75,
      "percentage": 30.4,
      "total_cost": 5.25,
      "avg_execution_time_ms": 3891
    },
    "ollama/mistral": {
      "count": 22,
      "percentage": 8.9,
      "total_cost": 0.00,
      "avg_execution_time_ms": 2156
    }
  },
  "total_cost": 12.75
}
```

## Key Implementation Files

- [src/cofounder_agent/services/template_execution_service.py](../../src/cofounder_agent/services/template_execution_service.py) (model extraction)
- [src/cofounder_agent/services/custom_workflows_service.py](../../src/cofounder_agent/services/custom_workflows_service.py) (model handling)
- [src/cofounder_agent/services/model_router.py](../../src/cofounder_agent/services/model_router.py) (provider routing)
- Database: `workflow_executions.selected_model` column for audit trail

## Notes

- Model selection parameter is optional; default routing applies when not provided
- Selected model is persisted to PostgreSQL for auditability and cost analysis
- Automatic fallback prevents workflow failures due to temporary API outages
- Development mode supports local Ollama by default for zero-cost testing
- Cost tracking enables budget management and ROI optimization
- Failed models do not count toward cost calculations
