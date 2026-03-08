# Service Registry

The service registry provides dynamic discovery of available agents, actions, and their capabilities for intelligent composition and routing.

## Primary Endpoints

- `GET /api/services/registry` - List all available services
- `GET /api/services/list` - Alias for registry list
- `GET /api/services/{service_name}` - Get service details
- `GET /api/services/{service_name}/actions` - List service actions
- `GET /api/services/{service_name}/actions/{action_name}` - Get action schema
- `GET /api/agents/registry` - List available agents

## Request/Response Examples

### List Available Services

**Request:**

```bash
curl -X GET "http://localhost:8000/api/services/registry" \
  -H "Authorization: Bearer dev-token"
```

**Response:**

```json
{
  "services": [
    {
      "name": "content_generation",
      "description": "Content generation and refinement",
      "actions": ["research", "draft", "assess", "refine"],
      "input_schema": {
        "topic": { "type": "string", "required": true },
        "style": { "type": "string", "required": false }
      },
      "output_schema": {
        "content": { "type": "string" },
        "metadata": { "type": "object" }
      }
    },
    {
      "name": "market_analysis",
      "description": "Market research and trend analysis",
      "actions": ["research", "analyze", "forecast"],
      "input_schema": {
        "market": { "type": "string", "required": true }
      },
      "output_schema": {
        "analysis": { "type": "object" },
        "trends": { "type": "array" }
      }
    }
  ],
  "total_count": 8
}
```

### Get Service Actions

**Request:**

```bash
curl -X GET "http://localhost:8000/api/services/content_generation/actions" \
  -H "Authorization: Bearer dev-token"
```

**Response:**

```json
{
  "service": "content_generation",
  "actions": [
    {
      "name": "research",
      "description": "Gather research on topic",
      "parameters": {
        "topic": { "type": "string", "required": true },
        "depth": { "type": "enum", "values": ["shallow", "deep"] }
      },
      "returns": {
        "summary": { "type": "string" },
        "sources": { "type": "array" }
      }
    },
    {
      "name": "draft",
      "description": "Create content draft",
      "parameters": {
        "research": { "type": "object", "required": true },
        "tone": { "type": "string" }
      },
      "returns": {
        "content": { "type": "string" },
        "word_count": { "type": "integer" }
      }
    }
  ]
}
```

### Get Specific Action Schema

**Request:**

```bash
curl -X GET "http://localhost:8000/api/services/content_generation/actions/research" \
  -H "Authorization: Bearer dev-token"
```

**Response:**

```json
{
  "service": "content_generation",
  "action": "research",
  "description": "Gather background research on a topic with web search and synthesis",
  "parameters": {
    "topic": {
      "type": "string",
      "required": true,
      "description": "Research topic"
    },
    "depth": {
      "type": "enum",
      "values": ["shallow", "moderate", "deep"],
      "required": false,
      "default": "moderate"
    },
    "source_types": {
      "type": "array",
      "items": "string",
      "required": false
    }
  },
  "returns": {
    "summary": {
      "type": "string",
      "description": "Synthesized research summary"
    },
    "sources": {
      "type": "array",
      "items": { "url": "string", "title": "string" }
    },
    "key_points": {
      "type": "array",
      "items": "string"
    },
    "confidence_score": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    }
  }
}
```

## What It Enables

- LLM/agent discovery of callable services and action schemas
- Dynamic frontend generation of service-action forms
- Registry-backed orchestration and capability-based task routing
- Intelligent composition of multiple services into workflows

## Key Implementation Files

- [src/cofounder_agent/routes/service_registry_routes.py](../../src/cofounder_agent/routes/service_registry_routes.py)
- [src/cofounder_agent/services/service_registry.py](../../src/cofounder_agent/services/service_registry.py)
- [src/cofounder_agent/services/service_base.py](../../src/cofounder_agent/services/service_base.py)
- [src/cofounder_agent/routes/agent_registry_routes.py](../../src/cofounder_agent/routes/agent_registry_routes.py)

## Notes

- The registry schema includes per-action parameter and response schema metadata
- Service routes return structured 404s listing available services when lookup fails
- Registry enables frontend UI generation without explicit component coding
- Service discovery supports dynamic capability composition for LLMs
