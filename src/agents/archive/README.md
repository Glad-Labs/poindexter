# Legacy Code Archive

**Status:** DEPRECATED - Use `src/cofounder_agent/` instead

This directory contains legacy implementations that have been consolidated into the unified codebase.
All functionality has been merged into `/src/cofounder_agent/services/` with improvements.

## Migration Mapping

### ✅ PexelsClient → ImageService

- **Legacy Path:** `src/agents/content_agent/services/pexels_client.py`
- **New Path:** `src/cofounder_agent/services/image_service.py`
- **Migration:** All Pexels functionality merged into unified ImageService with additional features
- **Status:** FULLY CONSOLIDATED

### ✅ ImageGenClient → ImageService

- **Legacy Path:** `src/agents/content_agent/services/image_gen_client.py`
- **New Path:** `src/cofounder_agent/services/image_service.py`
- **Migration:** SDXL image generation now part of unified ImageService
- **Status:** FULLY CONSOLIDATED

### ✅ ImageAgent → ImageService

- **Legacy Path:** `src/agents/content_agent/agents/image_agent.py`
- **New Path:** `src/cofounder_agent/services/image_service.py`
- **Migration:** Orchestration logic integrated into unified ImageService
- **Status:** FULLY CONSOLIDATED

### ✅ QAAgent → ContentQualityService

- **Legacy Path:** `src/agents/content_agent/agents/qa_agent.py`
- **New Path:** `src/cofounder_agent/services/content_quality_service.py`
- **Migration:** Binary approval + LLM feedback merged with 7-criteria evaluation
- **New Features:** Hybrid scoring, pattern-based fallback, PostgreSQL persistence
- **Status:** FULLY CONSOLIDATED

### ✅ QualityEvaluator → ContentQualityService

- **Legacy Path:** `src/cofounder_agent/services/quality_evaluator.py` (temporary)
- **New Path:** `src/cofounder_agent/services/content_quality_service.py`
- **Migration:** Complete rewrite with improved architecture
- **Status:** FULLY CONSOLIDATED

### ✅ UnifiedQualityOrchestrator → ContentQualityService

- **Legacy Path:** `src/cofounder_agent/services/unified_quality_orchestrator.py` (temporary)
- **New Path:** `src/cofounder_agent/services/content_quality_service.py`
- **Migration:** Orchestration logic integrated into single service
- **Status:** FULLY CONSOLIDATED

### ✅ LLMClient → ModelRouter (or removed)

- **Legacy Path:** `src/agents/content_agent/services/llm_client.py`
- **New Path:** `src/cofounder_agent/services/model_router.py` OR use directly
- **Migration:** Use ModelRouter for LLM operations
- **Status:** PARTIALLY MIGRATED (legacy still available for compatibility)

### ✅ Content Pipeline Orchestration

- **Legacy Path:** `src/agents/content_agent/orchestrator.py`
- **New Path:** `src/cofounder_agent/services/content_router_service.py`
- **Migration:** 7-stage unified pipeline with PostgreSQL persistence
- **Status:** FULLY CONSOLIDATED

### ✅ SEO Generation

- **Legacy Path:** Embedded in `src/agents/content_agent/agents/creative_agent.py`
- **New Path:** `src/cofounder_agent/services/seo_content_generator.py`
- **Migration:** Moved to dedicated service for reusability
- **Status:** FULLY CONSOLIDATED

## Database Persistence

All unified services use PostgreSQL (glad_labs_dev) for persistence:

### Tables

- `content_tasks` - Track generation tasks
- `quality_evaluations` - Store 7-criteria scores
- `quality_improvement_logs` - Track refinements
- `orchestrator_training_data` - Capture training examples
- `posts` - Store generated content
- `authors` - Content creators (AI + human)
- `categories` - Content categorization
- `tags` - Content tagging

### Connection

- Uses `DatabaseService` from `src/cofounder_agent/services/database_service.py`
- Async/await pattern (asyncpg driver)
- Connection pooling for performance

## Migration Steps

If you have code depending on legacy implementations:

### 1. Replace Pexels Client

```python
# OLD
from src.agents.content_agent.services.pexels_client import PexelsClient
client = PexelsClient()
result = await client.search_and_download(query, path)

# NEW
from src.cofounder_agent.services.image_service import get_image_service
service = get_image_service()
featured_image = await service.search_featured_image(topic=query, keywords=[])
```

### 2. Replace QA Agent

```python
# OLD
from src.agents.content_agent.agents.qa_agent import QAAgent
qa_agent = QAAgent(llm_client)
approved, feedback = await qa_agent.run(post, content)

# NEW
from src.cofounder_agent.services.content_quality_service import get_content_quality_service
quality_service = get_content_quality_service(model_router=model_router)
result = await quality_service.evaluate(
    content=content,
    context={'topic': topic},
    method=EvaluationMethod.LLM_BASED  # or PATTERN_BASED or HYBRID
)
approved, feedback = result.to_approval_tuple()  # For compatibility
```

### 3. Replace Content Pipeline

```python
# OLD
from src.agents.content_agent.orchestrator import ContentOrchestrator
orchestrator = ContentOrchestrator()
result = await orchestrator.run(task)

# NEW
from src.cofounder_agent.services.content_router_service import process_content_generation_task
result = await process_content_generation_task(
    topic=topic,
    style=style,
    tone=tone,
    target_length=target_length,
    database_service=db
)
```

## Why This Consolidation?

1. **Single Source of Truth** - No duplicate implementations
2. **Better Maintenance** - Changes in one place affect all uses
3. **PostgreSQL Persistence** - All data goes to same database (glad_labs_dev)
4. **Unified Architecture** - Consistent async/await patterns
5. **Zero Breaking Changes** - API layer unchanged, backend consolidated
6. **Reduced Code** - ~50% less code, ~100% more functionality
7. **Cost Savings** - Pexels (FREE) instead of DALL-E ($0.02/image)

## Legacy Agent Stack Status

The legacy `src/agents/content_agent/` remains for reference but should not be used for new development:

- ❌ Don't use: ResearchAgent, CreativeAgent, ImageAgent, PublishingAgent, QAAgent
- ✅ Use instead: Consolidated services in `src/cofounder_agent/`

## Cleanup Timeline

- **Immediate:** Don't use legacy code for new features
- **1-2 weeks:** Update existing code to use new services
- **1 month:** Archive legacy agents folder completely

## Questions?

See consolidated documentation:

- `CODEBASE_DUPLICATION_ANALYSIS.md` - Full analysis
- `COMPLETE_IMPLEMENTATION_GUIDE.md` - Integration guide
- `IMPLEMENTATION_COMPLETE_SUMMARY.md` - Architecture overview
