# Task Executor.py - Exact Code Changes (Diff Format)

**File:** src/cofounder_agent/services/task_executor.py  
**Applied to:** Lines 728-905  
**Total Changes:** 5 major, 1 minor  

---

## Change 1: Import QualityAssessment (Line 29 area)

**Add after existing UnifiedQualityService import:**

```python
from .quality_service import UnifiedQualityService, QualityAssessment
```

---

## Change 2: Fix Quality Assessment Attribute Access (Lines 728-733)

```diff
-        quality_score = quality_result.get("score", 0)
-        approved = quality_result.get("approved", False)
-
-        logger.info(f"   Quality Score: {quality_score}/100")
-        logger.info(f"   Approved: {approved}")
-        logger.debug(f"   Quality result keys: {list(quality_result.keys())}")
+        # Handle both QualityAssessment objects and fallback dicts
+        if isinstance(quality_result, QualityAssessment):
+            quality_score = quality_result.overall_score  # 0-100
+            approved = quality_result.passing  # boolean
+            feedback_text = quality_result.feedback
+            suggestions_list = quality_result.suggestions
+            needs_refine = quality_result.needs_refinement
+        else:
+            # Fallback for dict (line 721)
+            quality_score = quality_result.get("score", 0)
+            approved = quality_result.get("approved", False)
+            feedback_text = quality_result.get("feedback", "")
+            suggestions_list = quality_result.get("suggestions", [])
+            needs_refine = quality_result.get("needs_refinement", False)
+
+        logger.info(f"   Quality Score: {quality_score}/100")
+        logger.info(f"   Approved: {approved}")
+        if isinstance(quality_result, QualityAssessment):
+            logger.debug(f"   Quality dimensions: clarity={quality_result.dimensions.clarity:.0f}, "
+                         f"readability={quality_result.dimensions.readability:.0f}")
```

---

## Change 3: Fix critique_result Reference (Line 739)

```diff
-        logger.debug(f"   Feedback: {critique_result.get('feedback')}")
+        logger.debug(f"   Feedback: {feedback_text}")
```

---

## Change 4: Fix critique_result and critique_loop (Lines 742-843)

**BEFORE:**

```python
        if critique_result.get("needs_refinement") and self.orchestrator:
            logger.info(
                f"🔄 [TASK_EXECUTE] Attempting refinement based on critique feedback..."
            )
            logger.info(
                f"   Original content length: {len(generated_content) if generated_content else 0} chars"
            )
            try:
                # Check if orchestrator supports modern process_request
                if hasattr(self.orchestrator, "process_request") and not hasattr(
                    self.orchestrator, "process_command_async"
                ):
                    # UnifiedOrchestrator
                    refinement_result = await self.orchestrator.process_request(
                        user_input=f"Refine content about '{topic}' based on feedback: {critique_result.get('feedback')}",
                        context={
                            "original_content": generated_content,
                            "feedback": critique_result.get("feedback"),
                            "task_id": str(task_id),
                            "model_selections": model_selections,
                        },
                    )
                else:
                    # Legacy Orchestrator or basic Orchestrator
                    # Try legacy signature first if it has process_request
                    if hasattr(self.orchestrator, "process_request"):
                        refinement_result = await self.orchestrator.process_request(
                            user_request=f"Refine content based on feedback: {topic}",
                            user_id="system_task_executor",
                            business_metrics={
                                "original_content": generated_content,
                                "feedback": critique_result.get("feedback"),
                                "suggestions": critique_result.get("suggestions"),
                                "topic": topic,
                                "model_selections": model_selections,
                            },
                        )
                    else:
                        # Fallback to process_command_async
                        refinement_result = await self.orchestrator.process_command_async(
                            command=f"Refine content about '{topic}' based on feedback: {critique_result.get('feedback')}",
                            context={"original_content": generated_content},
                        )

                logger.info(
                    f"   Refinement completed, result type: {type(refinement_result).__name__}"
                )

                # Extract content from refinement result
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
                    elif (
                        isinstance(refinement_result.get("output"), dict)
                        and "content" in refinement_result["output"]
                    ):
                        refined_content = refinement_result["output"]["content"]
                    else:
                        # Fallback: If refinement didn't return content, keep original
                        logger.warning(f"⚠️  Refinement result missing expected content fields")
                        refined_content = None
                elif isinstance(refinement_result, str):
                    # If result is just a string, use it as content
                    refined_content = refinement_result
                    logger.info(
                        f"   Refinement returned string content ({len(refinement_result)} chars)"
                    )
                else:
                    # Unknown format
                    logger.warning(
                        f"⚠️  Unexpected refinement result type: {type(refinement_result).__name__}"
                    )
                    refined_content = None

                if refined_content and len(str(refined_content).strip()) > 50:
                    # Use refined content
                    generated_content = (
                        str(refined_content)
                        if not isinstance(refined_content, str)
                        else refined_content
                    )
                    logger.info(f"   ✅ Using refined content ({len(generated_content)} chars)")

                    # Re-critique refined content
                    critique_result = await self.critique_loop.critique(
                        content=generated_content,
                        context={
                            "topic": topic,
                            "keywords": primary_keyword,
                        },
                    )

                    quality_score = critique_result.get("quality_score", 0)
                    approved = critique_result.get("approved", False)
                    logger.info(f"   Refined Quality Score: {quality_score}/100")
                else:
                    logger.warning(
                        f"   ⚠️  Refined content too short ({len(str(refined_content).strip()) if refined_content else 0} chars), keeping original"
                    )

            except Exception as refine_err:
                logger.error(
                    f"❌ [TASK_EXECUTE] Refinement failed: {refine_err}", exc_info=True
                )
                logger.warning(
                    f"   Keeping original content ({len(generated_content) if generated_content else 0} chars)"
                )

            logger.info(
                f"🔄 Refinement complete: approved={approved}, score={quality_score}/100, content_len={len(generated_content) if generated_content else 0}"
            )
```

**AFTER:**

```python
        if needs_refine and self.orchestrator:
            logger.info(
                f"🔄 [TASK_EXECUTE] Attempting refinement based on feedback..."
            )
            logger.info(
                f"   Original content length: {len(generated_content) if generated_content else 0} chars"
            )
            try:
                # Check if orchestrator supports modern process_request
                if hasattr(self.orchestrator, "process_request") and not hasattr(
                    self.orchestrator, "process_command_async"
                ):
                    # UnifiedOrchestrator
                    refinement_result = await self.orchestrator.process_request(
                        user_input=f"Refine content about '{topic}' based on feedback: {feedback_text}",
                        context={
                            "original_content": generated_content,
                            "feedback": feedback_text,
                            "suggestions": suggestions_list,
                            "task_id": str(task_id),
                            "model_selections": model_selections,
                        },
                    )
                else:
                    # Legacy Orchestrator or basic Orchestrator
                    # Try legacy signature first if it has process_request
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
                            },
                        )
                    else:
                        # Fallback to process_command_async
                        refinement_result = await self.orchestrator.process_command_async(
                            command=f"Refine content about '{topic}' based on feedback: {feedback_text}",
                            context={"original_content": generated_content},
                        )

                logger.info(
                    f"   Refinement completed, result type: {type(refinement_result).__name__}"
                )

                # Extract content from refinement result
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
                    elif (
                        isinstance(refinement_result.get("output"), dict)
                        and "content" in refinement_result["output"]
                    ):
                        refined_content = refinement_result["output"]["content"]
                    else:
                        # Fallback: If refinement didn't return content, keep original
                        logger.warning(f"⚠️  Refinement result missing expected content fields")
                        refined_content = None
                elif isinstance(refinement_result, str):
                    # If result is just a string, use it as content
                    refined_content = refinement_result
                    logger.info(
                        f"   Refinement returned string content ({len(refinement_result)} chars)"
                    )
                else:
                    # Unknown format
                    logger.warning(
                        f"⚠️  Unexpected refinement result type: {type(refinement_result).__name__}"
                    )
                    refined_content = None

                if refined_content and len(str(refined_content).strip()) > 50:
                    # Use refined content
                    generated_content = (
                        str(refined_content)
                        if not isinstance(refined_content, str)
                        else refined_content
                    )
                    logger.info(f"   ✅ Using refined content ({len(generated_content)} chars)")

                    # RE-EVALUATE REFINED CONTENT (using quality_service, not critique_loop)
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
                        },
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
                    logger.warning(
                        f"   ⚠️  Refined content too short ({len(str(refined_content).strip()) if refined_content else 0} chars), keeping original"
                    )

            except Exception as refine_err:
                logger.error(
                    f"❌ [TASK_EXECUTE] Refinement failed: {refine_err}", exc_info=True
                )
                logger.warning(
                    f"   Keeping original content ({len(generated_content) if generated_content else 0} chars)"
                )

            logger.info(
                f"🔄 Refinement complete: approved={approved}, score={quality_score}/100, content_len={len(generated_content) if generated_content else 0}"
            )
```

---

## Change 5: Add Missing Phase 2 Metrics Recording (After line 850)

**Insert before "===== Validate Content Generation =====" comment:**

```python
        # ===== Record Phase 2 Metrics =====
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

## Change 6: Fix critique_result in Final Result Dict (Lines 904-905)

```diff
-            "critique_feedback": critique_result.get("feedback", ""),
-            "critique_suggestions": critique_result.get("suggestions", []),
+            "critique_feedback": feedback_text,
+            "critique_suggestions": suggestions_list,
```

---

## Complete Diff Summary

| Line Range | Type | Change | Complexity |
|-----------|------|--------|------------|
| 29 | Import | Add `QualityAssessment` | Trivial |
| 728-733 | Logic | Replace dict access with object attributes | Medium |
| 739 | Ref | Replace `critique_result` with `feedback_text` | Trivial |
| 742-843 | Logic | Use `needs_refine`, `feedback_text`, replace critique_loop | Complex |
| ~850 | Insert | Add Phase 2 metrics recording | Trivial |
| 904-905 | Ref | Replace `critique_result` with extracted vars | Trivial |

**Total Lines Changed:** ~130 (mostly in refinement logic)  
**Files Modified:** 1  
**Breaking Changes:** 0 (backward compatible)  
**Risk Level:** Low (all changes isolated, well-tested patterns)

---

## Verification Steps

After applying all changes:

```bash
# 1. Check syntax
python -m py_compile src/cofounder_agent/services/task_executor.py

# 2. Check for undefined variables
grep -n "critique_result\[^_\]" src/cofounder_agent/services/task_executor.py
# Should return 0 results (except in comments)

# 3. Check for get() on QualityAssessment
grep -n "quality_result\.get" src/cofounder_agent/services/task_executor.py
# Should only appear in fallback/error handling paths

# 4. Check imports
grep "from .quality_service import" src/cofounder_agent/services/task_executor.py
# Should show: UnifiedQualityService, QualityAssessment

# 5. Verify Phase 2 metrics
grep -n "record_phase_end.*quality_assessment" src/cofounder_agent/services/task_executor.py
# Should find one match around line 850+
```

---

**Status: All changes are ready for sequential implementation.**
