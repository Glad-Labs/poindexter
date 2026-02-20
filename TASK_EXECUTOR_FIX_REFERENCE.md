# Task Executor Fix - Quick Implementation Reference

**Created:** February 19, 2026  
**File:** src/cofounder_agent/services/task_executor.py  
**Total Errors:** 11+  
**Complexity:** Medium (data structure refactoring, variable naming)

---

## Error Summary Table

| Line(s) | Error Type | Issue | Fix Priority |
|---------|-----------|-------|--------------|
| 728-729 | Object/Dict mismatch | `.get("score")` on QualityAssessment object | P1 CRITICAL |
| 739 | Undefined variable | `critique_result` doesn't exist | P1 CRITICAL |
| 742, 756, 759, 773, 774, 782 | Undefined variable | Multiple `critique_result` uses | P1 CRITICAL |
| 834 | Uninitialized object | `self.critique_loop` never created | P1 CRITICAL |
| 842-843 | Cascading error | Uses undefined `critique_result` | P1 CRITICAL |
| 699 | Missing cleanup | No `record_phase_end()` for Phase 2 | P2 HIGH |
| 904-905 | Undefined variable | `critique_result` in result dict | P1 CRITICAL |

---

## Import Check (Line 29)

```python
# CURRENT: ✅ Already imported
from .quality_service import UnifiedQualityService

# ADD IF MISSING (before _execute_task):
from models import QualityAssessment  # For isinstance() check
```

---

## Fix 1: Lines 728-733 (Object Attribute Access)

**Location:** Phase 2 Quality Assessment section

```python
# BEFORE (BROKEN):
quality_score = quality_result.get("score", 0)
approved = quality_result.get("approved", False)

logger.info(f"   Quality Score: {quality_score}/100")
logger.info(f"   Approved: {approved}")
logger.debug(f"   Quality result keys: {list(quality_result.keys())}")

# AFTER (FIXED):
# Handle both QualityAssessment objects and fallback dicts
if isinstance(quality_result, QualityAssessment):
    quality_score = quality_result.overall_score  # 0-100
    approved = quality_result.passing  # boolean
    feedback_text = quality_result.feedback
    suggestions_list = quality_result.suggestions
    needs_refine = quality_result.needs_refinement
else:
    # Fallback for dict (line 721)
    quality_score = quality_result.get("score", 0)
    approved = quality_result.get("approved", False)
    feedback_text = quality_result.get("feedback", "")
    suggestions_list = quality_result.get("suggestions", [])
    needs_refine = quality_result.get("needs_refinement", False)

logger.info(f"   Quality Score: {quality_score}/100")
logger.info(f"   Approved: {approved}")
if isinstance(quality_result, QualityAssessment):
    logger.debug(f"   Quality dimensions: clarity={quality_result.dimensions.clarity:.0f}, "
                 f"readability={quality_result.dimensions.readability:.0f}")
```

---

## Fix 2: Line 739 (Variable Reference Error)

**Location:** Right after line 737

```python
# BEFORE (BROKEN):
logger.debug(f"   Feedback: {critique_result.get('feedback')}")

# AFTER (FIXED):
logger.debug(f"   Feedback: {feedback_text}")
```

---

## Fix 3: Lines 742-843 (Refinement Block Rewrite)

**Location:** Entire refinement conditional (lines 742-850)

```python
# BEFORE (BROKEN):
if critique_result.get("needs_refinement") and self.orchestrator:
    # ... refinement logic ...
    critique_result = await self.critique_loop.critique(...)  # ❌ CRASHES
    quality_score = critique_result.get("quality_score", 0)
    approved = critique_result.get("approved", False)

# AFTER (FIXED):
if needs_refine and self.orchestrator:
    logger.info(f"🔄 [TASK_EXECUTE] Attempting refinement based on feedback...")
    logger.info(f"   Original content length: {len(generated_content) if generated_content else 0} chars")
    try:
        # Use orchestrator to refine
        if hasattr(self.orchestrator, "process_request") and not hasattr(self.orchestrator, "process_command_async"):
            refinement_result = await self.orchestrator.process_request(
                user_input=f"Refine content about '{topic}' based on feedback: {feedback_text}",
                context={
                    "original_content": generated_content,
                    "feedback": feedback_text,
                    "suggestions": suggestions_list,
                    "task_id": str(task_id),
                    "model_selections": model_selections,
                }
            )
        else:
            if hasattr(self.orchestrator, "process_request"):
                refinement_result = await self.orchestrator.process_request(
                    user_request=f"Refine content based on feedback: {topic}",
                    user_id="system_task_executor",
                    business_metrics={
                        "original_content": generated_content,
                        "feedback": feedback_text,
                        "suggestions": suggestions_list,
                        "topic": topic,
                        "model_selections": model_selections,
                    }
                )
            else:
                refinement_result = await self.orchestrator.process_command_async(
                    command=f"Refine content about '{topic}' based on feedback: {feedback_text}",
                    context={"original_content": generated_content}
                )
        
        # Extract refined content
        refined_content = None
        if isinstance(refinement_result, dict):
            logger.debug(f"   Refinement result keys: {list(refinement_result.keys())}")
            if "content" in refinement_result:
                refined_content = refinement_result["content"]
            elif "output" in refinement_result:
                refined_content = refinement_result["output"]
            elif "response" in refinement_result:
                refined_content = refinement_result["response"]
            elif "final_formatting" in refinement_result:
                refined_content = refinement_result["final_formatting"]
            elif isinstance(refinement_result.get("output"), dict) and "content" in refinement_result["output"]:
                refined_content = refinement_result["output"]["content"]
        elif isinstance(refinement_result, str):
            refined_content = refinement_result
            logger.info(f"   Refinement returned string content ({len(refinement_result)} chars)")
        
        if refined_content and len(str(refined_content).strip()) > 50:
            generated_content = str(refined_content) if not isinstance(refined_content, str) else refined_content
            logger.info(f"   ✅ Using refined content ({len(generated_content)} chars)")
            
            # RE-EVALUATE REFINED CONTENT USING QUALITY SERVICE (NOT critique_loop)
            quality_result = await self.quality_service.evaluate(
                content=generated_content,
                context={
                    "topic": topic,
                    "keywords": primary_keyword,
                    "target_audience": target_audience,
                    "category": category,
                    "style": style,
                    "tone": tone,
                    "target_length": target_length,
                }
            )
            
            # Extract new quality scores
            if isinstance(quality_result, QualityAssessment):
                quality_score = quality_result.overall_score
                approved = quality_result.passing
                feedback_text = quality_result.feedback
                suggestions_list = quality_result.suggestions
                needs_refine = quality_result.needs_refinement
            else:
                quality_score = quality_result.get("score", 0)
                approved = quality_result.get("approved", False)
                feedback_text = quality_result.get("feedback", "")
                suggestions_list = quality_result.get("suggestions", [])
                needs_refine = quality_result.get("needs_refinement", False)
            
            logger.info(f"   Refined Quality Score: {quality_score}/100")
        else:
            logger.warning(f"   ⚠️  Refined content too short, keeping original")
    
    except Exception as refine_err:
        logger.error(f"❌ [TASK_EXECUTE] Refinement failed: {refine_err}", exc_info=True)
        logger.warning(f"   Keeping original content ({len(generated_content) if generated_content else 0} chars)")
    
    logger.info(f"🔄 Refinement complete: approved={approved}, score={quality_score}/100")
```

---

## Fix 4: Add Missing Phase 2 Metrics (After line 850)

**Location:** After the entire quality assessment block, before "Validate Content Generation" section

```python
# ADD THIS:
# Record Phase 2 completion
logger.debug(f"📊 [METRICS] Recording Phase 2 completion...")
task_metrics.record_phase_end(
    "quality_assessment", 
    phase_2_start, 
    status="success",
    error=None
)
logger.info(f"✅ [TASK_EXECUTE] PHASE 2 Complete: Quality assessment recorded")
```

---

## Fix 5: Update Final Result Dict (Lines 904-905)

**Location:** Building the result dict (around line 900-910)

```python
# BEFORE (BROKEN):
"critique_feedback": critique_result.get("feedback", ""),
"critique_suggestions": critique_result.get("suggestions", []),

# AFTER (FIXED):
"critique_feedback": feedback_text,
"critique_suggestions": suggestions_list,
```

---

## Testing Checklist

After implementing fixes:

- [ ] Task executor starts without import errors
- [ ] First task executes Phase 1 successfully
- [ ] Phase 2 quality assessment completes without crashes
- [ ] Quality scores are 0-100 range
- [ ] Approved is true/false boolean
- [ ] Phase 2 metrics are recorded (check logs)
- [ ] Final result dict contains all required fields
- [ ] Content is stored in database correctly
- [ ] Metrics saved to admin_logs table

---

## Quick Debug Command

```bash
# Check logs while running
tail -f /var/log/glad-labs/task-executor.log | grep -E "\[TASK_SINGLE\]|\[TASK_EXECUTE\]|\[METRICS\]"

# Or in Python terminal:
# import asyncio
# from services.task_executor import TaskExecutor
# executor = TaskExecutor(database_service, orchestrator)
# await executor._execute_task(test_task_dict)
```

---

## Expected Log Output After Fix

```
🎬 [TASK_EXECUTE] PRODUCTION PIPELINE: task-id
📝 [TASK_EXECUTE] PHASE 1: Generating content via orchestrator...
✅ [TASK_EXECUTE] PHASE 1 Complete: Generated 5234 chars
🔍 [TASK_EXECUTE] PHASE 2: Validating content quality...
   Quality Score: 82/100
   Approved: True
📊 [METRICS] Recording Phase 2 completion...
✅ [TASK_EXECUTE] PHASE 2 Complete: Quality assessment recorded
✅ [TASK_EXECUTE] Content approved
```

---

## Variables After Fix

| Variable | Type | Scope | Purpose |
|----------|------|-------|---------|
| `quality_result` | QualityAssessment | Phase 2 | Assessment object from quality_service |
| `quality_score` | float | Phase 2+ | 0-100 score extracted from assessment |
| `approved` | bool | Phase 2+ | Pass/fail decision |
| `feedback_text` | str | Phase 2+ | Human-readable feedback |
| `suggestions_list` | List[str] | Phase 2+ | Improvement suggestions |
| `needs_refine` | bool | Phase 2+ | Should attempt refinement |
| `phase_2_start` | float | Phase 2 | Start time for metrics |
| `task_metrics` | TaskMetrics | Entire func | Metrics collector |

---

**Status: Ready for implementation. All fixes are independent and can be applied sequentially.**
