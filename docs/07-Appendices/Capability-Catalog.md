# Capability Catalog

This document catalogs all registered capabilities available for task composition and automatic routing. Capabilities enable intent-based task execution where natural language requests are matched against available agent skills.

## Registered Capabilities

### Content Generation Capability

**ID:** `content_generation`  
**Agent:** `content_agent`  
**Description:** Generate, refine, and optimize written content

**Input Schema:**

```json
{
  "topic": {
    "type": "string",
    "required": true,
    "description": "Content topic"
  },
  "length": {
    "type": "enum",
    "values": ["short", "medium", "long"],
    "required": false
  },
  "style": {
    "type": "string",
    "required": false,
    "description": "Writing style"
  },
  "target_audience": { "type": "string", "required": false },
  "tone": { "type": "string", "required": false }
}
```

**Output Schema:**

```json
{
  "content": { "type": "string" },
  "metadata": {
    "word_count": "integer",
    "estimated_read_time_minutes": "integer",
    "quality_score": "float"
  }
}
```

**Related Services:**

- `research` - Gather background information
- `draft` - Generate initial content
- `assess` - Quality evaluation
- `refine` - Content improvement

---

### Image Generation Capability

**ID:** `image_generation`  
**Agent:** `image_agent`  
**Description:** Generate, select, and manipulate images

**Input Schema:**

```json
{
  "description": {
    "type": "string",
    "required": true,
    "description": "Image description"
  },
  "style": {
    "type": "enum",
    "values": ["photorealistic", "illustration", "abstract", "sketch"],
    "required": false
  },
  "size": {
    "type": "enum",
    "values": ["small", "medium", "large"],
    "required": false
  },
  "provider": {
    "type": "enum",
    "values": ["pexels", "dalle", "midjourney"],
    "required": false
  }
}
```

**Output Schema:**

```json
{
  "image_urls": { "type": "array", "items": "string" },
  "metadata": {
    "selected_url": "string",
    "alt_text": "string",
    "attribution": "string"
  },
  "provider_used": "string"
}
```

**Related Services:**

- `search_stock_photos` - Find appropriate stock images
- `generate_dall_e` - Generate custom images
- `optimize_alt_text` - Create accessible descriptions

---

### Research Capability

**ID:** `research`  
**Agent:** `content_agent`  
**Description:** Gather, synthesize, and analyze information

**Input Schema:**

```json
{
  "query": {
    "type": "string",
    "required": true,
    "description": "Research query"
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
    "values": ["academic", "news", "industry", "general"],
    "required": false
  }
}
```

**Output Schema:**

```json
{
  "summary": "string",
  "key_points": { "type": "array", "items": "string" },
  "sources": {
    "type": "array",
    "items": {
      "url": "string",
      "title": "string",
      "source_type": "string"
    }
  },
  "confidence_score": "float"
}
```

**Related Services:**

- `web_search` - Search the internet
- `academic_search` - Search scholarly articles
- `news_aggregation` - Gather recent news

---

### Quality Evaluation Capability

**ID:** `quality_evaluation`  
**Agent:** `content_agent`  
**Description:** Assess content quality, correctness, and compliance

**Input Schema:**

```json
{
  "content": {
    "type": "string",
    "required": true,
    "description": "Content to evaluate"
  },
  "criteria": {
    "type": "array",
    "items": "string",
    "values": [
      "grammar",
      "factuality",
      "tone",
      "engagement",
      "seo",
      "compliance"
    ],
    "required": false
  },
  "threshold": {
    "type": "float",
    "minimum": 0,
    "maximum": 1,
    "required": false,
    "default": 0.8
  }
}
```

**Output Schema:**

```json
{
  "overall_score": "float",
  "assessment": {
    "grammar": { "score": "float", "feedback": "string" },
    "factuality": { "score": "float", "feedback": "string" },
    "tone": { "score": "float", "feedback": "string" },
    "engagement": { "score": "float", "feedback": "string" },
    "seo": { "score": "float", "feedback": "string" },
    "compliance": { "score": "float", "feedback": "string" }
  },
  "pass": "boolean",
  "improvements": { "type": "array", "items": "string" }
}
```

**Related Services:**

- `grammar_check` - Syntax and grammar analysis
- `factuality_check` - Verify claims against sources
- `tone_analysis` - Check writing tone compliance
- `seo_analysis` - SEO quality assessment

---

### Publishing Capability

**ID:** `publishing`  
**Agent:** `publishing_agent`  
**Description:** Publish content to multiple channels

**Input Schema:**

```json
{
  "content": {
    "type": "string",
    "required": true,
    "description": "Content to publish"
  },
  "title": { "type": "string", "required": true },
  "channels": {
    "type": "array",
    "items": "string",
    "values": ["blog", "social_media", "email", "api"],
    "required": true
  },
  "metadata": {
    "keywords": { "type": "array", "items": "string" },
    "featured_image": "string",
    "publish_date": "string"
  }
}
```

**Output Schema:**

```json
{
  "publication_ids": {
    "type": "object",
    "properties": {
      "blog": "string",
      "social_media": { "type": "array", "items": "string" },
      "email": "string"
    }
  },
  "urls": { "type": "array", "items": "string" },
  "published_at": "string",
  "status": "string"
}
```

**Related Services:**

- `publish_to_blog` - CMS/blog publishing
- `schedule_social` - Social media posting
- `send_email` - Email distribution
- `update_analytics` - Track metrics

---

### Market Analysis Capability

**ID:** `market_analysis`  
**Agent:** `market_insight_agent`  
**Description:** Analyze markets, competitors, and trends

**Input Schema:**

```json
{
  "market": {
    "type": "string",
    "required": true,
    "description": "Market/industry name"
  },
  "focus": {
    "type": "array",
    "items": "string",
    "values": ["competitors", "trends", "opportunities", "threats", "pricing"],
    "required": false
  }
}
```

**Output Schema:**

```json
{
  "market_overview": "string",
  "key_players": { "type": "array", "items": "string" },
  "trends": { "type": "array", "items": "string" },
  "opportunities": { "type": "array", "items": "string" },
  "threats": { "type": "array", "items": "string" },
  "recommendations": { "type": "array", "items": "string" }
}
```

**Related Services:**

- `competitor_intelligence` - Analyze competitors
- `trend_detection` - Identify market trends
- `opportunity_assessment` - Find business opportunities

---

## Capability-to-Service Mapping

| Capability           | Primary Services                        | Models                     |
| -------------------- | --------------------------------------- | -------------------------- |
| `content_generation` | research, draft, assess, refine         | Claude 3.5, GPT-4          |
| `image_generation`   | search_stock, generate_custom, optimize | Pexels, DALL-E, Midjourney |
| `research`           | web_search, academic, news              | Claude, GPT-4              |
| `quality_evaluation` | grammar, factuality, tone, seo          | Claude 3.5                 |
| `publishing`         | cms_publish, social_schedule, email     | Native implementations     |
| `market_analysis`    | competitor, trends, opportunities       | Claude, GPT-4              |

## Capability Discovery via API

**List all capabilities:**

```bash
curl -X GET "http://localhost:8000/api/capabilities" \
  -H "Authorization: Bearer dev-token"
```

**Get specific capability schema:**

```bash
curl -X GET "http://localhost:8000/api/capabilities/content_generation" \
  -H "Authorization: Bearer dev-token"
```

## Intent-to-Capability Matching

The system matches natural language intents to capabilities using semantic understanding:

**Request:**

```json
{
  "intent": "Create a blog post about AI trends with research, quality review, and publication"
}
```

**System Response:**

```json
{
  "matched_capabilities": [
    {
      "capability_id": "research",
      "confidence": 0.95,
      "reason": "Explicitly requests research phase"
    },
    {
      "capability_id": "content_generation",
      "confidence": 0.92,
      "reason": "Blog post creation mentioned"
    },
    {
      "capability_id": "quality_evaluation",
      "confidence": 0.88,
      "reason": "Quality review requested"
    },
    {
      "capability_id": "publishing",
      "confidence": 0.85,
      "reason": "Publication step mentioned"
    }
  ],
  "recommended_workflow": [
    "research",
    "content_generation",
    "quality_evaluation",
    "publishing"
  ]
}
```

## Key Implementation Files

- [src/cofounder_agent/services/capability_registry.py](../../src/cofounder_agent/services/capability_registry.py)
- [src/cofounder_agent/services/task_planning_service.py](../../src/cofounder_agent/services/task_planning_service.py)
- [src/cofounder_agent/routes/capability_tasks_routes.py](../../src/cofounder_agent/routes/capability_tasks_routes.py)
- [src/cofounder_agent/agents/](../../src/cofounder_agent/agents/) - Agent implementations

## Notes

- Capabilities are provider-agnostic abstractions over agent services
- Multiple agents can implement the same capability with different quality/cost tradeoffs
- Semantic intent matching enables end-users to specify high-level goals without technical details
- Capability composition is validated to ensure successful data flow between phases
- New capabilities can be registered by extending the CapabilityRegistry
