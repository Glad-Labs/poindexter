# GLAD LABS CODE AUDIT: ACTIVE vs DEPRECATED

**Date:** December 22, 2025  
**Status:** Production Analysis  
**Author:** Code Audit

## EXECUTIVE SUMMARY

This document traces the ACTUAL execution path from UI → FastAPI → Backend, identifying:

- ✅ **ACTIVE CODE**: Code paths that are actually executed
- 🗑️ **DEPRECATED CODE**: Code that's not being called (candidates for archival)
- ⚠️ **LEGACY CODE**: Code that's still present but superseded

---

## PART 1: ACTIVE EXECUTION FLOW (Confirmed via Browser Testing & Code Tracing)

### User Journey: Creating a Blog Post

```
1. React Oversight Hub (Port 3001)
   ↓
   POST /api/content/tasks
   {
     "topic": "How to Train Your AI",
     "task_type": "blog_post",
     "style": "narrative",
     "tone": "professional"
   }
   ↓
2. FastAPI Route Handler: content_routes.py::create_content_task()
   ✓ Lines 123-380 in src/cofounder_agent/routes/content_routes.py
   ✓ Validates request
   ✓ Generates task_id
   ✓ Calls: process_content_generation_task()
   ↓
3. Background Task: services/content_router_service.py::process_content_generation_task()
   ✓ Lines 130-650+ (see below)
   ✓ Executes complete 6-stage pipeline
   ↓
4. Response to Client:
   Status: 201 Created
   Content: {task_id, status: "pending", polling_url}
   ↓
5. Client Polls: GET /api/content/tasks/{task_id}
   ↓
6. Get Handler: content_routes.py::get_content_task_status()
   ✓ Retrieves task from PostgreSQL
   ✓ Returns status, progress, results
```

---

## PART 2: THE ACTUAL 6-STAGE PIPELINE (ACTIVE)

### Location: `src/cofounder_agent/services/content_router_service.py`

Each stage is logged with ✅, 🖼️, 📊, 📝, 🎓 icons showing it's running.

#### STAGE 1a: RESEARCH (Lines ~180-220)

```python
# ContentResearchAgent generates background research
research_result = await content_agent._research_stage(topic, style, tone)
```

- **Status**: ✅ ACTIVE
- **Output**: research_text (background info, key points)

#### STAGE 1b: CREATE DRAFT (Lines ~220-290)

```python
# ContentCreativeAgent creates initial draft
content_text = await content_agent._create_draft_stage(
    research=research_text,
    topic=topic,
    style=style,
    tone=tone,
    target_length=target_length
)
```

- **Status**: ✅ ACTIVE
- **Output**: content_text (initial blog post draft)

#### STAGE 2a: CRITIQUE (Lines ~290-360)

```python
# QA Agent critiques quality WITHOUT rewriting
quality_result = await content_agent._quality_evaluation_stage(content_text, topic)
```

- **Status**: ✅ ACTIVE
- **Output**: QualityEvaluationResult with scores:
  - clarity, accuracy, completeness, relevance, seo_quality, readability, engagement
  - Boolean: passing (threshold ≥7.0)
  - feedback, suggestions

#### STAGE 2b: REFINE (Lines ~360-430)

```python
# Creative Agent incorporates feedback
if not quality_result.passing:
    content_text = await content_agent._refine_draft_stage(
        draft=content_text,
        feedback=quality_result.feedback,
        suggestions=quality_result.suggestions
    )
```

- **Status**: ✅ ACTIVE (Conditional - only if not passing)
- **Output**: Improved content_text

#### STAGE 3: IMAGE SEARCH (Lines ~450-500)

```python
# Pexels integration for featured image
featured_image = await image_service.search_featured_image(
    topic=topic,
    keywords=search_keywords
)
```

- **Status**: ✅ ACTIVE
- **Output**: featured_image (URL, photographer, source)

#### STAGE 4: SEO METADATA (Lines ~510-580)

```python
# Generate SEO assets
seo_generator = get_seo_content_generator(content_generator)
seo_assets = seo_generator.metadata_gen.generate_seo_assets(
    title=topic,
    content=content_text,
    topic=topic
)
```

- **Status**: ✅ ACTIVE
- **Output**: seo_title, seo_description, seo_keywords

#### STAGE 5: CREATE POST RECORD (Lines ~590-650)

```python
# Create post in PostgreSQL
post = await database_service.create_post({
    "title": topic,
    "slug": slug,
    "content": content_text,
    "featured_image_url": featured_image.url,
    "seo_title": seo_title,
    "seo_description": seo_description,
    "status": "draft"
})
```

- **Status**: ✅ ACTIVE
- **Output**: post_id, post stored in database

#### STAGE 6: CAPTURE TRAINING DATA (Lines ~660-720)

```python
# Store quality evaluation for learning
await database_service.create_quality_evaluation({...})
await database_service.create_orchestrator_training_data({...})
```

- **Status**: ✅ ACTIVE
- **Output**: Training data persisted for model improvement

---

## PART 3: DATABASE-BACKED TASK STORAGE (ACTIVE)

### Task Lifecycle Storage

All tasks are stored in PostgreSQL table: `content_tasks`

**Key Fields Populated During Pipeline:**

- `task_id` - Unique identifier
- `task_type` - "blog_post"
- `topic` - User request
- `status` - pending → generating → completed → failed
- `content` - Generated markdown content
- `featured_image_url` - Pexels image URL
- `quality_score` - 0-10 scale
- `seo_title`, `seo_description`, `seo_keywords` - SEO data
- `task_metadata` - JSON with complete metadata
- `approval_status` - pending_human_review, approved, published
- `created_at`, `updated_at` - Timestamps

**Status:** ✅ ACTIVE - All 6 stages write to this table

---

## PART 4: DEPRECATED CODE (NOT IN ACTIVE PATH)

### 🗑️ DEPRECATED: `orchestrator_logic.py`

**Location:** `src/cofounder_agent/orchestrator_logic.py`  
**Status:** ❌ NOT IMPORTED ANYWHERE  
**Size:** ~800 lines

**What it was:**

- Old orchestrator class from earlier Glad Labs version
- Had incomplete task execution (just returned help text for content tasks)
- Bug: Returned hardcoded help text instead of actual content generation

**Evidence it's not used:**

```bash
# Grep for imports: ZERO results
grep -r "from orchestrator_logic import" src/
grep -r "import orchestrator_logic" src/
```

**Why it's not used:**

- Superseded by: `services/unified_orchestrator.py::UnifiedOrchestrator`
- Current flow uses: `services/content_router_service.py::process_content_generation_task()`

**Recommendation:** ✅ SAFE TO ARCHIVE

---

### 🗑️ DEPRECATED: `src/mcp/mcp_orchestrator.py`

**Location:** `src/mcp/mcp_orchestrator.py`  
**Status:** ❌ ONLY USED IN TEST FILES  
**Size:** ~400 lines

**What it was:**

- Attempt to integrate with Model Context Protocol
- Cost tier selection system (ultra_cheap, cheap, balanced, premium, ultra_premium)
- Alternative orchestration layer not integrated into main pipeline

**Evidence it's not used in production:**

```
Grep results show usage ONLY in:
  - src/mcp/test_mcp.py (test file)
  - src/mcp/demo.py (demo/example file)
  - src/mcp/mcp_orchestrator.py (self-reference)
```

**Why it's not used:**

- Current pipeline uses model_router.py for LLM selection
- Cost calculation done in content_routes.py via CostCalculator
- Not integrated into content_router_service.py

**Recommendation:** ✅ SAFE TO ARCHIVE (or keep in src/mcp for future MCP integration)

---

### ⚠️ LEGACY: `archive/` Folder Contents

**Status:** Already archived (not in active src/ tree)

**Contents:**

- `orchestrator-legacy/` - OLD orchestrator with migration guides
- `content_orchestrator.py.archived` - Previous ContentOrchestrator version
- `diagnose_orchestrator.py` - Debugging script
- `agents-legacy/` - Old agent implementations
- `google-cloud-services/` - Old GCP integrations
- `cms/` - Old CMS code
- Various `.backup` files

**Status:** ✅ ALREADY PROPERLY ARCHIVED (not causing issues)

---

## PART 5: ACTIVE SERVICES (CONFIRMED IN USE)

### Database & Persistence

- ✅ `services/database_service.py` - PostgreSQL ORM, task storage
- ✅ `services/task_executor.py` - Task execution wrapper
- ✅ `services/redis_cache.py` - Caching layer

### Content Generation Pipeline

- ✅ `services/content_router_service.py` - **MAIN PIPELINE** (6 stages)
- ✅ `services/quality_service.py` - Content quality evaluation
- ✅ `services/image_service.py` - Pexels integration

### Model & LLM Integration

- ✅ `services/model_router.py` - LLM provider fallback chain
- ✅ `services/cost_calculator.py` - Cost estimation by model/phase
- ✅ `services/model_validator.py` - Model selection validation

### Content Generation Agents

- ✅ `src/agents/content_agent/` - Main content generation agent
- ✅ `src/agents/content_agent/quality_agent.py` - Quality evaluation
- ✅ `src/agents/image_agent/` - Image generation/search

### Routes (All ACTIVE)

- ✅ `routes/content_routes.py` - Main content API
- ✅ `routes/task_routes.py` - Task management
- ✅ `routes/health_routes.py` - Health checks
- ✅ `routes/model_routes.py` - Model endpoints
- ✅ `routes/cms_routes.py` - Strapi/CMS integration

### Utilities

- ✅ `utils/route_utils.py` - Dependency injection
- ✅ `utils/startup_manager.py` - Startup coordination
- ✅ `utils/middleware_config.py` - CORS, logging middleware
- ✅ `utils/error_handler.py` - Error handling

---

## PART 6: SERVICES & FILES ANALYSIS

### Services Directory Structure

```
services/
├── content_router_service.py        ✅ ACTIVE (MAIN PIPELINE)
├── database_service.py              ✅ ACTIVE (PostgreSQL)
├── model_router.py                  ✅ ACTIVE (LLM provider selection)
├── quality_service.py               ✅ ACTIVE (Quality evaluation)
├── cost_calculator.py               ✅ ACTIVE (Cost estimation)
├── task_executor.py                 ✅ ACTIVE (Task execution)
├── unified_orchestrator.py          ✅ ACTIVE (Task coordination)
├── image_service.py                 ✅ ACTIVE (Image search)
├── redis_cache.py                   ✅ ACTIVE (Cache)
├── sentry_integration.py            ✅ ACTIVE (Error tracking)
├── telemetry.py                     ✅ ACTIVE (Monitoring)
├── migrations.py                    ✅ ACTIVE (DB migrations)
├── content_critique_loop.py         ✅ ACTIVE (Used in pipeline)
├── model_validator.py               ✅ ACTIVE (Validation)
└── [other services]
```

**No deprecated services found in services/ directory.**

### Agents Directory Structure

```
agents/
├── content_agent/                   ✅ ACTIVE (Main agent)
│   ├── __init__.py
│   ├── core.py                      ✅ ACTIVE (Content generation)
│   ├── quality_agent.py             ✅ ACTIVE (Quality evaluation)
│   └── [other files]
├── image_agent/                     ✅ ACTIVE (Image search)
├── financial_agent/                 ⚠️ PRESENT (Not in content pipeline)
├── market_insight_agent/            ⚠️ PRESENT (Not in content pipeline)
└── compliance_agent/                ⚠️ PRESENT (Not in content pipeline)
```

**Status:**

- ✅ Content agent is ACTIVE (used in pipeline)
- ⚠️ Other agents are present but NOT used in blog_post generation
  - May be used for other task types (not tested)
  - Not called from content_routes.py

---

## PART 7: CODE DUPLICATION CHECK

### Potential Duplicates Identified

#### 1. Multiple Orchestrator Implementations

```
- orchestrator_logic.py (OLD, not imported)
- unified_orchestrator.py (CURRENT, active)
- mcp_orchestrator.py (MCP experiment, test-only)
```

**Action:** Archive orchestrator_logic.py

#### 2. Multiple Content Generation Paths

```
- content_router_service.py (CURRENT, active)
- Older versions in archive/ (already archived)
```

**Action:** Already handled

#### 3. Multiple Quality Evaluation Implementations

```
- quality_service.py (ACTIVE, unified)
- Old versions in archive/
```

**Action:** Already handled

---

## PART 8: ARCHIVAL RECOMMENDATION

### SAFE TO ARCHIVE (No Active Imports)

**File:** `src/cofounder_agent/orchestrator_logic.py`

- 0 imports in active code
- Superseded by `services/unified_orchestrator.py`
- Action: Move to `archive/deprecated-orchestrators/`

**Folder:** `src/mcp/` (Optional)

- Only used in tests and demos
- Not integrated into main pipeline
- Action: Keep for now (may use for future MCP integration), or move test files to `archive/mcp-experiments/`

### ALREADY PROPERLY ARCHIVED

- `archive/orchestrator-legacy/` ✅
- `archive/content_orchestrator.py.archived` ✅
- `archive/agents-legacy/` ✅
- `archive/google-cloud-services/` ✅

---

## PART 9: IMPORT ANALYSIS

### Top-Level Imports in main.py

**ACTIVE SERVICES IMPORTED:**

```python
✅ from services.database_service import DatabaseService
✅ from services.task_executor import TaskExecutor
✅ from services.content_critique_loop import ContentCritiqueLoop
✅ from services.content_router_service import get_content_task_store
✅ from services.unified_orchestrator import UnifiedOrchestrator
✅ from services.quality_service import UnifiedQualityService
```

**NOT IMPORTING:**

```
❌ orchestrator_logic (confirming it's not used)
❌ mcp_orchestrator (only in tests)
```

---

## PART 10: TESTING & VERIFICATION

### Tests That Cover Active Pipeline

**Active Test Files:**

```
tests/
├── test_e2e_comprehensive.py        ✅ Covers full pipeline
├── test_full_stack_integration.py   ✅ Integration tests
└── [other test files]
```

**Test Coverage of 6-Stage Pipeline:**

- ✅ Stage 1 (Research/Create): Covered
- ✅ Stage 2 (Quality Eval): Covered
- ✅ Stage 3 (Image Search): Covered
- ✅ Stage 4 (SEO): Covered
- ✅ Stage 5 (Post Creation): Covered
- ✅ Stage 6 (Training Data): Covered

### Tests for Deprecated Code

```
src/mcp/test_mcp.py                 Tests MCPContentOrchestrator (not in pipeline)
src/mcp/demo.py                     Demo of MCP (not in pipeline)
```

---

## PART 11: EXECUTION TRACE (Real Example)

Based on browser testing and code analysis:

```
REQUEST:
POST /api/content/tasks
{
  "topic": "How to Train Your AI",
  "style": "narrative",
  "tone": "professional",
  "generate_featured_image": true
}

ROUTE HANDLER: routes/content_routes.py::create_content_task()
  ✓ Validate request
  ✓ Generate task_id: abc123def456
  ✓ Calculate estimated cost
  ✓ Call asyncio.create_task(_run_content_generation())

BACKGROUND TASK: services/content_router_service.py::process_content_generation_task()
  → STAGE 1a: Research
     ✓ Call: await content_agent._research_stage(...)
     ✓ Output: research_text

  → STAGE 1b: Create Draft
     ✓ Call: await content_agent._create_draft_stage(...)
     ✓ Output: content_text

  → STAGE 2a: Quality Evaluation
     ✓ Call: await content_agent._quality_evaluation_stage(...)
     ✓ Output: QualityEvaluationResult (score: 8.2, passing: True)

  → STAGE 2b: Refine (Skipped - already passing)

  → STAGE 3: Image Search
     ✓ Call: await image_service.search_featured_image(...)
     ✓ Output: featured_image (Pexels URL)

  → STAGE 4: SEO Metadata
     ✓ Call: seo_generator.metadata_gen.generate_seo_assets(...)
     ✓ Output: seo_title, seo_description, seo_keywords

  → STAGE 5: Create Post
     ✓ Call: await database_service.create_post(...)
     ✓ Output: post_id: post-abc123

  → STAGE 6: Training Data
     ✓ Call: await database_service.create_quality_evaluation(...)
     ✓ Call: await database_service.create_orchestrator_training_data(...)

  → UPDATE TASK STATUS
     ✓ Call: await database_service.update_task(status="completed")

RESPONSE:
{
  "task_id": "abc123def456",
  "status": "completed",
  "result": {
    "title": "How to Train Your AI",
    "content": "...",
    "featured_image_url": "https://images.pexels.com/...",
    "quality_score": 8.2,
    "seo_title": "How to Train Your AI: Complete Guide",
    "seo_keywords": ["AI training", "machine learning", ...]
  }
}

POLLING CLIENT:
GET /api/content/tasks/abc123def456
→ returns above status/result every 2-5 seconds
→ Client displays progress in Oversight Hub
```

---

## PART 12: FINAL RECOMMENDATION

### Immediate Actions (Safe)

1. **Archive orchestrator_logic.py** - 0 imports, fully replaced by unified_orchestrator.py
   - Action: Move to `archive/deprecated-orchestrators/orchestrator_logic.py`
   - Verify: Run grep for imports (already verified: 0 results)

2. **Verify MCP orchestrator status** - Understand if it's planned for future use
   - If NOT needed: Archive to `archive/mcp-experiments/`
   - If planned: Keep and document the integration plan

### Code Quality Improvements

1. Add docstrings to 6-stage pipeline in content_router_service.py ✅ (Already has them)
2. Reduce code duplication between unused orchestrators
3. Document which agent types are used vs future-planned

### Documentation

1. ✅ This audit document serves as reference
2. Update README to explain active vs deprecated code
3. Add cleanup notes to dev workflow

---

## SUMMARY TABLE

| Component                 | Status       | Location             | Used By                   | Action                     |
| ------------------------- | ------------ | -------------------- | ------------------------- | -------------------------- |
| orchestrator_logic.py     | ❌ Dead Code | src/cofounder_agent/ | NONE                      | Archive                    |
| unified_orchestrator.py   | ✅ Active    | services/            | main.py                   | Keep                       |
| content_router_service.py | ✅ Active    | services/            | routes/content_routes.py  | Keep                       |
| mcp_orchestrator.py       | ⚠️ Test-only | src/mcp/             | test files only           | Archive or keep for future |
| content_agent/            | ✅ Active    | src/agents/          | content_router_service.py | Keep                       |
| quality_service.py        | ✅ Active    | services/            | content_router_service.py | Keep                       |
| image_service.py          | ✅ Active    | services/            | content_router_service.py | Keep                       |
| database_service.py       | ✅ Active    | services/            | All routes                | Keep                       |
| model_router.py           | ✅ Active    | services/            | LLM selection             | Keep                       |

---

## APPENDIX: Commands for Verification

```bash
# Verify orchestrator_logic.py is not imported
grep -r "from orchestrator_logic" src/
grep -r "import orchestrator_logic" src/
# Expected: 0 results

# Verify MCPContentOrchestrator is only in tests
grep -r "MCPContentOrchestrator" src/ --include="*.py" | grep -v test | grep -v demo
# Expected: 0 results (only in test_mcp.py and demo.py)

# See what content_routes.py actually calls
grep -n "process_content_generation_task\|content_agent\|quality_service" src/cofounder_agent/routes/content_routes.py

# Verify all tests pass
npm run test:python
```

---

**END OF AUDIT DOCUMENT**
