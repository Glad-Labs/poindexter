# Workflows System

The workflow system provides template-based execution and lifecycle control for multi-phase tasks.

## What It Covers

- Template execution for common content flows (blog, social, email, newsletter, market analysis)
- Execution tracking and status retrieval
- Workflow control (pause, resume, cancel)
- Integration with progress broadcasting and persistence
- Real-time progress updates via WebSocket

## Primary Endpoints

- `POST /api/workflows/execute/{template_name}` - Start template execution
- `GET /api/workflows/templates` - List available templates
- `GET /api/workflows/status/{execution_id}` - Get workflow status
- `GET /api/workflows/executions` - List all executions
- `POST /api/workflows/pause/{workflow_id}` - Pause workflow
- `POST /api/workflows/resume/{workflow_id}` - Resume workflow
- `POST /api/workflows/cancel/{workflow_id}` - Cancel workflow

## Request/Response Examples

### Execute Template

**Request:**

```bash
curl -X POST http://localhost:8000/api/workflows/execute/blog_post \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "The Future of AI in Content Creation",
    "keywords": ["artificial intelligence", "content generation", "SEO"],
    "target_audience": "Marketing professionals",
    "tone": "Informative and engaging"
  }'
```

**Response (202 Accepted):**

```json
{
  "execution_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_id": "550e8400-e29b-41d4-a716-446655440001",
  "template": "blog_post",
  "status": "running",
  "phases": [
    "research",
    "draft",
    "assess",
    "refine",
    "finalize",
    "image_selection",
    "publish"
  ],
  "phase_results": {
    "research": {
      "status": "completed",
      "duration_ms": 12345,
      "output": {
        "summary": "AI-generated research summary...",
        "sources": ["source1", "source2"]
      }
    }
  },
  "progress_percent": 20,
  "current_phase": "draft",
  "error_message": null,
  "created_at": "2026-03-08T14:30:00Z"
}
```

### Poll Workflow Status

**Request:**

```bash
curl -X GET "http://localhost:8000/api/workflows/status/550e8400-e29b-41d4-a716-446655440000" \
  -H "Authorization: Bearer dev-token"
```

**Response:**

```json
{
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "current_phase": "publish",
  "phases_executed": ["research", "draft", "assess", "refine", "finalize", "image_selection"],
  "progress_percent": 100,
  "results": {
    "research": {
      "status": "completed",
      "duration_ms": 12345,
      "error": null,
      "output": {"summary": "...", "sources": [...]}
    },
    "draft": {
      "status": "completed",
      "duration_ms": 8234,
      "output": {"content": "...", "word_count": 1250}
    },
    "assess": {
      "status": "completed",
      "quality_score": 0.92,
      "output": {"feedback": "...", "issues": []}
    },
    "refine": {
      "status": "completed",
      "duration_ms": 5678,
      "output": {"refined_content": "..."}
    }
  },
  "final_output": {
    "title": "The Future of AI in Content Creation",
    "content": "...",
    "seo_metadata": {
      "meta_description": "...",
      "keywords": ["artificial intelligence", "content generation"]
    },
    "image_urls": ["image1.jpg", "image2.jpg"],
    "publish_status": "published"
  },
  "started_at": "2026-03-08T14:30:00Z",
  "completed_at": "2026-03-08T14:50:23Z",
  "duration_ms": 1223000
}
```

### Pause/Resume/Cancel Workflow

**Pause:**

```bash
curl -X POST "http://localhost:8000/api/workflows/pause/550e8400-e29b-41d4-a716-446655440000" \
  -H "Authorization: Bearer dev-token"
```

**Resume:**

```bash
curl -X POST "http://localhost:8000/api/workflows/resume/550e8400-e29b-41d4-a716-446655440000" \
  -H "Authorization: Bearer dev-token"
```

**Cancel:**

```bash
curl -X POST "http://localhost:8000/api/workflows/cancel/550e8400-e29b-41d4-a716-446655440000" \
  -H "Authorization: Bearer dev-token"
```

All control endpoints return the updated workflow status.

## Key Implementation Files

- [src/cofounder_agent/routes/workflow_routes.py](../../src/cofounder_agent/routes/workflow_routes.py)
- [src/cofounder_agent/services/template_execution_service.py](../../src/cofounder_agent/services/template_execution_service.py)
- [src/cofounder_agent/services/workflow_executor.py](../../src/cofounder_agent/services/workflow_executor.py)
- [src/cofounder_agent/services/workflow_engine.py](../../src/cofounder_agent/services/workflow_engine.py)

## Template Definitions

Available templates and their phases:

- **blog_post** - `research → draft → assess → refine → finalize → image_selection → publish`
- **social_media** - `research → draft → assess → refine → image_selection → publish`
- **email** - `research → draft → assess → personalization → publish`
- **newsletter** - `research → curate → draft → assess → image_selection → publish`
- **market_analysis** - `research → analyze → assess → finalize → publish`

See [Workflow-Templates-Matrix.md](../07-Appendices/Workflow-Templates-Matrix.md) for detailed phase compositions.

## Notes

- Template execution is asynchronous and returns HTTP 202 (Accepted) with execution_id
- Poll status endpoint repeatedly or use WebSocket for real-time updates (see [WebSocket-Real-Time.md](WebSocket-Real-Time.md))
- Quality threshold (0.0-1.0) can be customized when executing templates
- Workflows can be paused mid-execution and resumed later
- All phase outputs are captured for audit and refinement purposes
