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
    "model_tier": "standard"
  }'
```

**Response:**

```json
{
  "execution_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_id": "550e8400-e29b-41d4-a716-446655440001",
  "selected_tier": "standard",
  "fallback_chain": ["budget", "free"],
  "status": "running",
  "current_phase": "research",
  "progress_percent": 0,
  "created_at": "2026-03-10T14:35:00Z"
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
    "model_tier": "premium"
  }'
```

**Response:**

```json
{
  "execution_id": "exec-550e8400-e29b-41d4",
  "workflow_id": "wf-550e8400",
  "selected_tier": "premium",
  "status": "running",
  "progress_percent": 0,
  "created_at": "2026-03-10T14:36:00Z"
}
```

## Cost Tiers (ALWAYS Use Tiers, NOT Model Names)

**Available Tiers:**

- **`free`** (local, zero API cost): Ollama models (mistral, llama2, neural-chat)
- **`budget`** (low API cost): GPT-3.5 Turbo, Claude Instant, Gemini Flash
- **`standard`** (mid-tier cost): Claude Haiku, balanced quality/cost
- **`premium`** (high-capability): Claude Opus, Gemini Pro
- **`flagship`** (most capable): GPT-4 Turbo, top-tier models for critical tasks

**Key Principle:** Always specify a cost tier (free/budget/standard/premium/flagship), never hardcode model names. The router automatically selects the best available model within that tier based on API availability, rate limits, and fallback chains.

## Automatic Fallback Chain

If primary tier is unavailable, system automatically cascades through:

```plaintext
Free (Ollama) → Budget (Cheapest APIs) → Standard → Premium → Flagship → Echo/Mock
```

**Fallback Example:**

- Request tier: `premium`
- Check API key: ✓ ANTHROPIC_API_KEY available
- Check service: ✗ Rate limited
- Fallback to: `standard` tier
- Check service: ✓ Available
- Execution: Uses standard tier model (e.g., Claude Haiku) with fallback logged

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
  "by_tier": {
    "premium": {
      "count": 150,
      "percentage": 60.7,
      "total_cost": 7.5,
      "avg_execution_time_ms": 4234,
      "actual_models": ["Claude Opus", "GPT-4 Turbo"]
    },
    "standard": {
      "count": 75,
      "percentage": 30.4,
      "total_cost": 2.25,
      "avg_execution_time_ms": 3891,
      "actual_models": ["Claude Haiku", "GPT-3.5 Turbo"]
    },
    "free": {
      "count": 22,
      "percentage": 8.9,
      "total_cost": 0.0,
      "avg_execution_time_ms": 2156,
      "actual_models": ["Ollama Mistral", "Ollama Llama2"]
    }
  },
  "total_cost": 9.75,
  "cost_savings": 34.2
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
