# GLAD LABS: QUICK REFERENCE CARD

**One-page guide to the content generation pipeline**

---

## THE 6-STAGE PIPELINE (Where everything happens)

```
📍 Location: src/cofounder_agent/services/content_router_service.py
📍 Function: process_content_generation_task()
📍 Status: ✅ ACTIVE (production)
```

### Stage Flow with Logs

```
┌─────────────────────────────────────────────────────────────────┐
│  🔍 STAGE 1a: RESEARCH                                          │
│     Input:  topic, style, tone                                  │
│     Output: research_text (~450 chars)                          │
│     Time:   ~5-10 seconds                                       │
├─────────────────────────────────────────────────────────────────┤
│  ✍️ STAGE 1b: CREATE DRAFT                                      │
│     Input:  research_text, topic, style, tone, target_length   │
│     Output: content_text (~2000 words)                          │
│     Time:   ~10-20 seconds                                      │
├─────────────────────────────────────────────────────────────────┤
│  📋 STAGE 2a: QUALITY EVALUATION                                │
│     Scores: clarity, accuracy, completeness, relevance,         │
│              seo_quality, readability, engagement              │
│     Output: overall_score (0-10), passing (≥7.0)               │
│     Time:   ~5-10 seconds                                       │
├─────────────────────────────────────────────────────────────────┤
│  💡 STAGE 2b: REFINE DRAFT (conditional)                        │
│     Condition: Only if quality_score < 7.0                     │
│     Input:  draft, feedback, suggestions                        │
│     Output: improved content_text                               │
│     Time:   ~10-20 seconds (if needed)                          │
├─────────────────────────────────────────────────────────────────┤
│  🖼️ STAGE 3: FEATURED IMAGE SEARCH                              │
│     API:    Pexels API integration                              │
│     Output: featured_image_url, photographer, source           │
│     Time:   ~2-5 seconds                                        │
├─────────────────────────────────────────────────────────────────┤
│  📊 STAGE 4: SEO METADATA                                       │
│     Output: seo_title (≤60 chars)                               │
│             seo_description (≤160 chars)                        │
│             seo_keywords (≤10 keywords)                         │
│     Time:   ~3-5 seconds                                        │
├─────────────────────────────────────────────────────────────────┤
│  📝 STAGE 5: CREATE POST                                        │
│     Database: PostgreSQL posts table                            │
│     Status:   Always "draft" (human review required)            │
│     Output:   post_id, post_slug                                │
│     Time:    ~1-2 seconds                                       │
├─────────────────────────────────────────────────────────────────┤
│  🎓 STAGE 6: CAPTURE TRAINING DATA                              │
│     Database: quality_evaluations, orchestrator_training_data  │
│     Purpose:  Improve future content generation                │
│     Time:     ~1-2 seconds                                      │
└─────────────────────────────────────────────────────────────────┘

TOTAL TIME: ~40-80 seconds (depends on if refinement needed)
```

---

## ACTIVE CODE (DON'T DELETE)

```
✅ CORE PIPELINE
   └─ services/content_router_service.py → THE MAIN PIPELINE

✅ AGENTS
   └─ src/agents/content_agent/
      ├─ core.py
      ├─ quality_agent.py
      └─ [agent methods]

✅ SERVICES
   ├─ database_service.py        (PostgreSQL)
   ├─ quality_service.py         (Quality evaluation)
   ├─ image_service.py           (Pexels integration)
   ├─ model_router.py            (LLM selection)
   ├─ unified_orchestrator.py    (Task coordination)
   ├─ cost_calculator.py         (Cost estimation)
   └─ [other services in services/]

✅ ROUTES
   ├─ routes/content_routes.py   (REST API)
   ├─ routes/task_routes.py
   └─ [all other routes]

✅ UTILITIES
   ├─ utils/route_utils.py
   ├─ utils/startup_manager.py
   └─ [all utilities]
```

---

## DEPRECATED CODE (SAFE TO ARCHIVE)

```
🗑️ orchestrator_logic.py
   ├─ Status: 0 imports in active code
   ├─ Replaced by: services/unified_orchestrator.py
   ├─ Size: ~800 lines
   └─ Action: ARCHIVE (safe)

⚠️ src/mcp/mcp_orchestrator.py
   ├─ Status: Test-only (not in production pipeline)
   ├─ Files: test_mcp.py, demo.py only
   ├─ Size: ~400 lines
   └─ Action: ARCHIVE (optional)
```

---

## API ENDPOINTS

### Create Content Task

```http
POST /api/content/tasks
Content-Type: application/json

{
  "topic": "How to Train Your AI",
  "task_type": "blog_post",
  "style": "narrative",
  "tone": "professional",
  "target_length": 2000,
  "tags": ["AI", "Training"],
  "generate_featured_image": true
}

Response 201:
{
  "task_id": "abc-123-def",
  "status": "pending",
  "polling_url": "/api/content/tasks/abc-123-def"
}
```

### Check Task Status

```http
GET /api/content/tasks/abc-123-def

Response 200:
{
  "task_id": "abc-123-def",
  "status": "completed",
  "result": {
    "title": "How to Train Your AI",
    "content": "...",
    "featured_image_url": "https://images.pexels.com/...",
    "quality_score": 8.2,
    "seo_keywords": ["AI training", "machine learning"]
  }
}
```

### Task Status Lifecycle

```
pending
   ↓ (execution starts)
generating (optional)
   ↓ (all 6 stages complete)
completed
   ├→ pending_human_review (awaiting approval)
   ├→ approved (human approved)
   └→ published (went live)

OR
   ↓ (error during execution)
failed
```

---

## KEY QUALITY DIMENSIONS

Scored 0-10, passing if average ≥ 7.0:

| Dimension    | What It Measures                      |
| ------------ | ------------------------------------- |
| Clarity      | Is content easy to understand?        |
| Accuracy     | Are facts correct and well-supported? |
| Completeness | Does it cover topic thoroughly?       |
| Relevance    | Does it match user request?           |
| SEO Quality  | Optimized for search engines?         |
| Readability  | Grammar, flow, structure?             |
| Engagement   | Will readers find it interesting?     |

---

## CONFIGURATION (.env.local)

```env
# Content generation
TARGET_CONTENT_LENGTH=2000
QUALITY_THRESHOLD=7.0
MAX_REFINEMENT_LOOPS=3

# Image service
PEXELS_API_KEY=xxxx
IMAGE_SEARCH_ENABLED=true

# SEO
SEO_SERVICE_ENABLED=true

# Model selection
PREFERRED_MODEL=claude
QUALITY_MODEL=claude-fast
```

---

## DEBUGGING

### View Real-time Pipeline Logs

```bash
# Terminal 1: Start backend
npm run dev:cofounder

# Terminal 2: Create a task and watch logs
# Look for emoji markers: 🔍 ✍️ 📋 💡 🖼️ 📊 📝 🎓
```

### Test the Pipeline

```bash
# Full integration test
npm run test:python

# Check for import errors
grep -r "from orchestrator_logic" src/
# Expected: 0 results
```

### Verify Components

```bash
# Health check endpoint
curl http://localhost:8000/health

# Check database
curl http://localhost:8000/health/db

# Check models available
curl http://localhost:8000/api/models/available
```

---

## CLEANUP (ARCHIVAL)

### Automated Cleanup

```bash
python scripts/cleanup_deprecated_code.py
```

This will:

1. ✓ Verify no imports of deprecated files
2. ✓ Move files to archive/
3. ✓ Run tests
4. ✓ Create cleanup log

---

## DOCUMENTS IN THIS PACKAGE

| Document                            | Purpose                           | Audience   |
| ----------------------------------- | --------------------------------- | ---------- |
| CODE_ANALYSIS_PACKAGE_README.md     | Overview of all documents         | Everyone   |
| ACTIVE_VS_DEPRECATED_AUDIT.md       | What code is used vs deprecated   | Architects |
| CONTENT_PIPELINE_DEVELOPER_GUIDE.md | How to understand/modify pipeline | Developers |
| scripts/cleanup_deprecated_code.py  | Automate code archival            | DevOps     |

---

## COMMON TASKS

### Add a new quality dimension

1. Update QualityEvaluationResult model
2. Add scoring logic in quality_agent.py
3. Update Stage 2a in content_router_service.py
4. Store in PostgreSQL quality_evaluations
5. Run tests: `npm run test:python`

### Change quality threshold

1. Edit: `QUALITY_THRESHOLD = 7.0` in content_router_service.py
2. Change to: `QUALITY_THRESHOLD = 8.0` (example)
3. Run tests
4. Update documentation

### Disable featured image search

1. Set `generate_featured_image=false` in API request
   OR
2. Set `IMAGE_SEARCH_ENABLED=false` in .env.local
3. Stage 3 will be skipped in pipeline

### Use different model for quality evaluation

1. Update .env.local: `QUALITY_MODEL=gpt4`
2. Modify model_router.py if needed
3. Run tests

---

## PERFORMANCE

| Stage        | Time   | Depends On                 |
| ------------ | ------ | -------------------------- |
| 1a. Research | 5-10s  | Model speed                |
| 1b. Draft    | 10-20s | Model speed, target_length |
| 2a. Quality  | 5-10s  | Model speed                |
| 2b. Refine   | 10-20s | If quality < 7.0           |
| 3. Image     | 2-5s   | Pexels API                 |
| 4. SEO       | 3-5s   | Model speed                |
| 5. Post      | 1-2s   | DB speed                   |
| 6. Training  | 1-2s   | DB speed                   |

**Total:** 40-80 seconds typical  
**Slowest:** Stage 1b (content creation)  
**Optimization:** Can batch stages 4-6 in parallel

---

## QUICK REFERENCE: FILE LOCATIONS

```
The Pipeline:
  src/cofounder_agent/services/content_router_service.py

The API:
  src/cofounder_agent/routes/content_routes.py

The Agents:
  src/agents/content_agent/
  src/agents/image_agent/

The Database:
  PostgreSQL (connection string in .env.local)

The Logs:
  Terminal output when running: npm run dev:cofounder

The Tests:
  tests/test_full_stack_integration.py
  tests/test_e2e_comprehensive.py

The Cleanup:
  scripts/cleanup_deprecated_code.py
```

---

**For complete information, see the included documentation files.**

_Last Updated: December 22, 2025_
