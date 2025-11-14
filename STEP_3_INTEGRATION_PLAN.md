# Step 3: Integration Plan

**File to Modify**: `/src/cofounder_agent/services/content_router_service.py`  
**Function to Replace**: `process_content_generation_task()` (around line 384)  
**Expected Time**: ~30 minutes

---

## Current Code (Before)

**Location**: `/src/cofounder_agent/services/content_router_service.py`

```python
async def process_content_generation_task(task_id: str) -> Dict[str, Any]:
    """Process a content generation task - CURRENT VERSION (PLACEHOLDER)"""

    try:
        # Get the task
        task = self.task_store.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        # Update status to processing
        self.task_store.update_task_status(task_id, "processing")

        # ❌ PLACEHOLDER: Generate dummy content
        task.content = "Lorem ipsum dolor sit amet..."
        task.excerpt = "This is placeholder content"
        task.status = "completed"
        task.completed_at = datetime.utcnow()

        # Save task
        self.task_store.update_task(task.to_dict())

        return {"status": "completed", "task_id": task_id}

    except Exception as e:
        logger.error(f"Error processing task {task_id}: {e}")
        if task_id:
            self.task_store.update_task_status(task_id, "failed", error=str(e))
        raise
```

---

## New Code (After)

Replace the entire function with:

```python
async def process_content_generation_task(task_id: str) -> Dict[str, Any]:
    """
    Process a content generation task using 6-stage orchestrator.

    Phase 5: REAL CONTENT GENERATION WITH HUMAN APPROVAL GATE

    Pipeline:
    1. Research (10%)
    2. Creative Draft (25%)
    3. QA Review Loop (45%)
    4. Image Selection (60%)
    5. Formatting (75%)
    6. Awaiting Human Approval (100%) ⏳ STOPS HERE

    Returns: status="awaiting_approval" - MANDATORY human decision required
    """

    try:
        # Get the task
        task = self.task_store.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        # ✅ IMPORT ORCHESTRATOR
        from src.cofounder_agent.services.content_orchestrator import get_content_orchestrator

        logger.info(f"🚀 Phase 5 Pipeline: Starting orchestrator for task {task_id}")

        # ✅ GET ORCHESTRATOR INSTANCE
        orchestrator = get_content_orchestrator(self.task_store)

        # ✅ RUN 6-STAGE PIPELINE
        # Returns: status="awaiting_approval" (STOPS HERE - No auto-publishing!)
        result = await orchestrator.run(
            topic=task.topic,
            keywords=task.tags if task.tags else [task.topic],
            style=task.style or "educational",
            tone=task.tone or "professional",
            task_id=task_id,
            metadata={
                "request_type": task.request_type,
                "publish_mode": task.publish_mode,
            }
        )

        logger.info(f"✅ Orchestrator complete for {task_id}: status={result['status']}")

        # ✅ CRITICAL: Pipeline returns status="awaiting_approval"
        # NOTHING PUBLISHES UNTIL HUMAN APPROVES!
        return result

    except Exception as e:
        logger.error(f"❌ Error processing task {task_id}: {e}", exc_info=True)
        if task_id:
            self.task_store.update_task_status(task_id, "failed", error=str(e))
        raise
```

---

## Key Changes Explained

### 1. Import Orchestrator

```python
from src.cofounder_agent.services.content_orchestrator import get_content_orchestrator
```

**Why**: Need access to the 6-stage pipeline orchestrator

### 2. Get Orchestrator Instance

```python
orchestrator = get_content_orchestrator(self.task_store)
```

**Why**: Get singleton instance (creates on first call, reuses after)

### 3. Call Orchestrator with Task Data

```python
result = await orchestrator.run(
    topic=task.topic,
    keywords=task.tags if task.tags else [task.topic],
    style=task.style or "educational",
    tone=task.tone or "professional",
    task_id=task_id,
    metadata={...}
)
```

**Why**: Pass task data to orchestrator; it returns status="awaiting_approval"

### 4. Return Result Directly

```python
return result
```

**Why**: Result already contains all needed fields (content, qa_feedback, etc.)
**Critical**: Task is now in "awaiting_approval" status - NOT published!

---

## What This Changes

### Before (Old Behavior):

```
POST /api/tasks
  ↓
process_content_generation_task()
  ↓
Generate dummy content
  ↓
Auto-publish to Strapi
  ↓
NO HUMAN REVIEW
```

### After (New Behavior with Phase 5):

```
POST /api/tasks
  ↓
process_content_generation_task()
  ↓
Orchestrator runs 6 stages:
  1. Research
  2. Creative Draft
  3. QA Review Loop
  4. Image Selection
  5. Formatting
  ↓
Status = "awaiting_approval" ⏳
  ↓
STOPS HERE - awaits human decision
  ↓
Human approves/rejects via:
  POST /api/tasks/{task_id}/approve
  ↓
If approved: Publish to Strapi
If rejected: Mark as rejected (no publish)
```

---

## Testing the Integration

### Test 1: Verify Pipeline Runs

```bash
# Start services
npm run dev

# Create a task
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "AI in Healthcare",
    "tags": ["AI", "healthcare", "medical"],
    "style": "professional",
    "tone": "informative"
  }'

# Response should include:
# "task_id": "task_1732..._abc123",
# "status": "processing" (task created and queued)
```

### Test 2: Monitor Progress

```bash
# Check task status (pipeline running)
curl http://localhost:8000/api/tasks/task_1732..._abc123

# Response should show:
# "progress": {
#   "stage": "research",      # or "creative", "qa", "images", "formatting"
#   "percentage": 10,         # or 25, 45, 60, 75, 90, 100
#   "message": "📚 Gathering information..."
# }
```

### Test 3: Verify Human Approval Gate

```bash
# After pipeline completes (wait for 100% progress)
curl http://localhost:8000/api/tasks/task_1732..._abc123

# Response should show:
# "status": "awaiting_approval",       ✅ **STOPS HERE**
# "approval_status": "awaiting_review",✅ **REQUIRES HUMAN DECISION**
# "content": "## AI in Healthcare...",
# "qa_feedback": "87/100 - Well researched...",
# "quality_score": 87,
# "next_action": "POST /api/tasks/task_1732..._abc123/approve with human decision"

# ❌ NOT published to Strapi yet
# ❌ Requires human approval
```

### Test 4: View in Oversight Hub

```
1. Go to http://localhost:3001
2. Navigate to Approval Queue (after Step 5 UI is created)
3. Should see task in "awaiting_approval" status
4. Can see content preview and QA feedback
5. Can click Approve or Reject
```

---

## Before vs After Comparison

| Aspect                 | Before                   | After                       |
| ---------------------- | ------------------------ | --------------------------- |
| **Content Generation** | Dummy text (Lorem ipsum) | Real 6-stage pipeline       |
| **Quality Review**     | None                     | QA agent with feedback loop |
| **Image Selection**    | None                     | Featured images selected    |
| **Human Review**       | None                     | ✅ **MANDATORY**            |
| **Auto-Publishing**    | ❌ Yes (problematic)     | ✅ **No** (awaits approval) |
| **Publishing Gate**    | None                     | ⏳ Approval endpoint        |
| **Status Tracking**    | Basic                    | Detailed progress %         |

---

## Files Modified

- **Original File**: `/src/cofounder_agent/services/content_router_service.py`
- **Method**: `process_content_generation_task()`
- **Lines**: ~384-410 (approximately 25 lines)
- **Change Type**: Replace entire function body

---

## After This Step

✅ Step 1: Schema extended  
✅ Step 2: Orchestrator created  
✅ Step 3: Orchestrator integrated (THIS STEP)  
⏳ Step 4: Approval endpoint modified  
⏳ Step 5: Oversight Hub UI created  
⏳ Step 6: End-to-end testing

**Next**: Step 4 - Modify the approval endpoint to handle human decisions

---

## Implementation Checklist for Step 3

- [ ] Open `/src/cofounder_agent/services/content_router_service.py`
- [ ] Find `process_content_generation_task()` function (around line 384)
- [ ] Replace entire function with new code (above)
- [ ] Verify no syntax errors: `python -m py_compile src/cofounder_agent/services/content_router_service.py`
- [ ] Test local: Create a task and verify status goes to "awaiting_approval"
- [ ] Confirm orchestrator logs show all 6 stages
- [ ] Verify task data includes qa_feedback and quality_score
- [ ] Commit: `git add . && git commit -m "Step 3: Integrate ContentOrchestrator into pipeline"`

**Ready to start?** Let's do it!
