# Quick Reference: LangGraph Integration Points

**Status: Ready for Implementation**

---

## System Health Check ✅

| Component           | Status     | Details                                 |
| ------------------- | ---------- | --------------------------------------- |
| LangGraph Pipeline  | ✅ Working | 6 nodes executing, quality loop working |
| Database            | ✅ Working | AsyncPG, slug uniqueness fixed          |
| Backend             | ✅ Working | 57+ services, all initialized           |
| Frontend            | ✅ Working | React build successful                  |
| Quality Assessment  | ✅ Working | 7-criteria, parameters corrected        |
| WebSocket Streaming | ✅ Working | Real-time progress updates              |

---

## Your Two Workflows - The Map

### Workflow A: Predetermined Tasks

```
User Input (3 modes)
├─ Detailed: topic + keywords + audience + tone + word_count
├─ Minimal: just topic (defaults fill rest)
└─ Template: select template (e.g., "blog_detailed"), override fields

         ↓

Create Task Endpoint (NEW)
POST /api/content/tasks/with-execution

         ↓

LangGraph 6-Node Pipeline
research → outline → draft → assess → refine (loop) → finalize

         ↓

Database: Create post + save quality metrics
```

### Workflow B: Natural Language Agent

```
User Input (chat)
"Create a 1500-word SEO blog about climate change"

         ↓

Extract Parameters (NEW Service)
NLP extraction → {topic, audience, word_count, seo_focus, ...}

         ↓

LangGraph 6-Node Pipeline
With extracted parameters

         ↓

Auto-Approval (NEW Service)
IF quality_score >= 0.85:
  └─ Auto-publish, else require approval
```

---

## Files: What to Create, What to Modify

### CREATE (3 Files - ~400 LOC)

```
1. services/parameter_extractor.py (150 LOC)
   └─ Class ParameterExtractor
      └─ async extract(request_text) → Dict[topic, audience, tone, ...]

2. services/task_templates.py (100 LOC)
   └─ Class TaskTemplates
      └─ TEMPLATES dict with presets
      └─ Methods: get_template(), list_templates(), apply_template()

3. web/oversight-hub/src/services/templateService.js (50 LOC)
   └─ Fetch templates from backend
   └─ Apply template defaults in UI
```

### MODIFY (5 Files - ~300 LOC)

```
1. routes/content_routes.py (+100 LOC)
   ADD: POST /api/content/tasks/with-execution
   └─ Create task → Trigger pipeline in background

   ADD: GET /api/content/templates
   └─ Return list of available templates

   ADD: POST /api/content/extract-parameters
   └─ Extract params from natural language request

2. routes/orchestrator_routes.py (+50 LOC)
   MODIFY: POST /api/orchestrator/process
   └─ For content requests: extract params → call LangGraph

3. services/langgraph_orchestrator.py (+20 LOC)
   ADD: task_id parameter support
   └─ Update task_metadata during pipeline execution

4. TaskCreationModal.jsx (+150 LOC)
   ADD: Input mode selector (detailed/minimal/template)
   ADD: Template dropdown
   MODIFY: Form submission → call new endpoint

5. OrchestratorPage.jsx (+100 LOC)
   ADD: Natural language chat UI
   MODIFY: Connect chat to parameter extraction + pipeline
```

---

## Implementation Order (Recommended)

**Phase 1: Backend Services (2 hours)**

1. Create `parameter_extractor.py`
2. Create `task_templates.py`
3. Test both services independently

**Phase 2: Routes (1.5 hours)**

1. Add `/api/content/tasks/with-execution` endpoint
2. Add `/api/content/templates` endpoint
3. Modify `/api/orchestrator/process` for extraction

**Phase 3: Frontend (2 hours)**

1. Enhance TaskCreationModal
2. Enhance OrchestratorPage
3. Add template service

**Phase 4: Integration Testing (1.5 hours)**

1. End-to-end tests
2. Error handling
3. Performance verification

**Total: 7 hours**

---

## Code Templates

### Parameter Extraction Service

```python
# Extract from: "Create a 2000-word SEO blog about Python for beginners"
# Returns: {
#   "topic": "Python",
#   "audience": "beginners",
#   "word_count": 2000,
#   "seo_focus": True,
#   "tone": "educational",
#   "keywords": ["Python", "programming"]
# }
```

### Task Template System

```python
# User selects: "blog_detailed"
# System provides:
# {
#   "word_count": 1500,
#   "tone": "informative",
#   "seo_focus": True,
#   "max_refinements": 3
# }
# User can override any field
```

### New Route Pattern

```python
@router.post("/api/content/tasks/with-execution")
async def create_task_with_execution(request):
    # 1. Create task in DB
    task_id = await db.create_task(...)

    # 2. Start pipeline in background
    async def run():
        result = await langgraph.execute_content_pipeline(...)
        # Pipeline saves to database automatically

    background_tasks.add_task(run)

    # 3. Return 202 Accepted with task_id
    return {"task_id": task_id, "status": "executing"}
```

---

## Testing Checklist

After implementation:

- [ ] Create task with detailed inputs → executes pipeline
- [ ] Create task with minimal inputs → uses defaults
- [ ] Create task from template → applies defaults
- [ ] Extract parameters from chat input → correct structure
- [ ] Pipeline auto-saves to database
- [ ] Quality score calculated correctly
- [ ] Auto-approval works at threshold
- [ ] WebSocket streams real-time progress
- [ ] Error handling graceful (connection lost, LLM timeout, etc.)
- [ ] Backward compatible (old endpoints still work)

---

## Database Queries (for verification)

```sql
-- Check tasks created
SELECT id, topic, status, stage FROM tasks
ORDER BY created_at DESC LIMIT 10;

-- Check quality assessments
SELECT task_id, quality_score, passed_quality FROM quality_evaluations
ORDER BY created_at DESC LIMIT 10;

-- Check posts created by pipeline
SELECT id, slug, title, created_at FROM posts
ORDER BY created_at DESC LIMIT 10;

-- Verify slug uniqueness
SELECT slug, COUNT(*) FROM posts GROUP BY slug HAVING COUNT(*) > 1;
-- Should return: (empty) - no duplicates
```

---

## API Endpoint Summary

### Current Working Endpoints

```
POST   /api/content/langgraph/generate      Pipeline execution (test)
GET    /api/tasks                           List all tasks
GET    /api/tasks/{task_id}                 Get task details
POST   /api/orchestrator/process            Natural language routing
POST   /api/chat                            Chat interface
GET    /api/quality/statistics              Quality metrics
```

### New Endpoints to Add

```
POST   /api/content/tasks/with-execution    Create task + execute
GET    /api/content/templates               List templates
POST   /api/content/extract-parameters      Extract from NLP
```

---

## Success Criteria

✅ **Workflow A Complete When:**

- User can select input mode (detailed/minimal/template)
- Task creation auto-executes pipeline
- Real-time progress visible
- Auto-publishes at quality threshold

✅ **Workflow B Complete When:**

- Chat interface accepts natural language
- Parameters extracted automatically
- Pipeline executes with extracted params
- Auto-approval works
- Real-time progress shown in chat

✅ **System Health When:**

- No slug duplicates (UUID suffix)
- Quality assessment correct
- Database persistence reliable
- All 6 pipeline phases working
- Error handling graceful
- Backward compatible

---

## Key Files Locations

**Backend:**

- Main entry: `/src/cofounder_agent/main.py`
- Routes: `/src/cofounder_agent/routes/`
- Services: `/src/cofounder_agent/services/`
- LangGraph: `/src/cofounder_agent/services/langgraph_graphs/`
- Database: `/src/cofounder_agent/services/database_service.py`

**Frontend:**

- Pages: `/web/oversight-hub/src/pages/`
- Components: `/web/oversight-hub/src/components/`
- Services: `/web/oversight-hub/src/services/`

**Documentation:**

- Full details: `INTEGRATION_ROADMAP_COMPLETE.md`
- Architecture: `LANGGRAPH_INTEGRATION_ANALYSIS.md`
- Summary: `NEXT_STEPS_SUMMARY.md`
- Quick ref: `QUICK_REFERENCE_LANGGRAPH_INTEGRATION.md` (this file)

---

## Common Questions

**Q: Will this break existing functionality?**
A: No. All changes are additive. Old endpoints remain unchanged.

**Q: How long will implementation take?**
A: 7-10 hours for complete integration + testing.

**Q: Do I need to modify the LangGraph pipeline?**
A: No. Pipeline is complete and working. Just add connectors.

**Q: What about backward compatibility?**
A: Keep old endpoints working. Gradual migration possible.

**Q: Can I test before full implementation?**
A: Yes. Test each component independently, then integrate.

---

## Ready to Proceed?

✅ Analysis complete  
✅ Architecture mapped  
✅ Implementation plan detailed  
✅ Code templates provided

**Next action:** Start Phase 1 or request modifications to plan?
