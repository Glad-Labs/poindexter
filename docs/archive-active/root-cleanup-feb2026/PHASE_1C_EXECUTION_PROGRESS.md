# Phase 1C Implementation - Execution Guide

**Status:** In Progress  
**Current File:** task_executor.py (1,191 lines, 14 exception blocks)  
**Effort:** 8.5 hours total, starting with Tier 1 (highest impact)

---

## Strategy: Parallelizable Work Breakdown

Instead of one person doing all 68 files, this work can be parallelized:

### Team Assignment Pattern

The 68 service files can be divided by file size/complexity:

**Small files (< 300 lines, 2-4 exceptions each) - 1 hour per file → Dev A handles 20+ files**
**Medium files (300-600 lines, 5-8 exceptions each) - 1.5 hours per file → Dev B handles 8-10 files**
**Large files (600+ lines, 10+ exceptions each) - 2-3 hours per file → Dev C handles 2-3 files**

This parallelization cuts the 8.5 hour task down to 3-4 hours of wall-clock time with team of 3.

---

## Tier 1 Implementation (Highest Impact - Doing Now)

### File: src/cofounder_agent/services/task_executor.py

**Scope:** 14 `except Exception` blocks to replace  
**Risk Level:** HIGH (critical execution path)  
**Precedence:** Do this first for testing before rolling to other files

#### Exception Locations in task_executor.py

| Line | Context | Type | Template |
| --- | --- | --- | --- |
| 176 | Task processing loop error | Service | Template 1 |
| 196 | Database update failure | Database | Template 2 |
| 217 | Task polling loop error | Service | Template 1 |
| 250 | Debug task dump | Service | Template 1 |
| 283 | execute_task method error | Service | Template 1 |
| 415 | Task publish to CMS | Service | Template 1 |
| 437 | Fallback error handling | Service | Template 1 |
| 440 | Main error handler | Service | Template 1 |
| 672 | Fallback generation error | Service | Template 1 |
| 682 | Quality check error | Service | Template 1 |
| 729 | Critique loop error | Service | Template 1 |
| 906 | Refinement step error | Service | Template 1 |
| 1019 | Dynamic module import error | Service | Template 1 |
| 1175 | Final catch-all error | Service | Template 1 |

#### Import Addition for task_executor.py

```python
# Add to imports at top of file (after existing imports)
from .error_handler import (
    ServiceError,
    DatabaseError,
)
```

#### Implementation for Each Exception

Let's document the FIRST exception as a template:

**Exception #1 (Line 176) - Task Processing Loop:**

**Current Code:**

```python
                        except Exception as e:
                            logger.error(
                                f"❌ [TASK_EXEC_LOOP] Error processing task {task_id}: {str(e)}",
                                exc_info=True,
                            )
```

**Why:** This is the main task processing in the event loop. When a task fails here, it needs to be logged with context and the task marked as failed (which is done right after).

**New Code:**

```python
                        except Exception as e:
                            logger.error(
                                "Error processing task in event loop",
                                exc_info=True,
                            )
                            raise ServiceError(
                                message=f"Failed to process task {task_id}",
                                details={
                                    "task_id": str(task_id),
                                    "error_type": type(e).__name__,
                                },
                                cause=e,
                            )
```

**Note:** This error is caught AGAIN at line 196 when trying to update the database, so the double-logging is intentional - one log for the processing error, another for the update failure.

---

**Exception #2 (Line 196) - Database Update Failure:**

**Current Code:**

```python
                            except Exception as update_err:
                                logger.error(
                                    f"❌ [TASK_EXEC_LOOP] Failed to update task status: {str(update_err)}"
                                )
```

**Why:** This is specifically a database operation failure. Distinguishing this helps with debugging - we know the task processing failed AND we couldn't persist the failure state.

**New Code:**

```python
                            except Exception as update_err:
                                logger.error(
                                    "Failed to update task status in database",
                                    exc_info=True,
                                )
                                raise DatabaseError(
                                    message=f"Failed to persist task failure state for task {task_id}",
                                    details={
                                        "task_id": str(task_id),
                                        "intended_status": "failed",
                                        "original_error": str(e) if 'e' in locals() else "unknown",
                                    },
                                    cause=update_err,
                                )
```

---

## Implementation Checklist for task_executor.py

```
IMPORT ADDITIONS:
[ ] Add ServiceError import
[ ] Add DatabaseError import

EXCEPTION BLOCK REPLACEMENTS (in order of appearance):
[ ] Line 176 - Task processing loop (use ServiceError)
[ ] Line 196 - Database update (use DatabaseError)
[ ] Line 217 - Task polling loop (use ServiceError)
[ ] Line 250 - Debug task dump (use ServiceError or skip non-critical)
[ ] Line 283 - _execute_task method (use ServiceError)
[ ] Line 415 - Task publish to CMS (use ServiceError)
[ ] Line 437 - Fallback error (use ServiceError)
[ ] Line 440 - Main error handler (use ServiceError)
[ ] Line 672 - Fallback generation (use ServiceError)
[ ] Line 682 - Quality check (use ServiceError)
[ ] Line 729 - Critique loop (use ServiceError)
[ ] Line 906 - Refinement step (use ServiceError)
[ ] Line 1019 - Module import (use ServiceError)
[ ] Line 1175 - Final catch-all (use ServiceError)

VERIFICATION:
[ ] All exception blocks have proper error message context
[ ] All exceptions include relevant details (task_id, etc.)
[ ] Original logging preserved (or improved)
[ ] Tests still pass: pytest tests/services/test_task_executor.py -v
[ ] Type checking passes: mypy src/cofounder_agent/services/task_executor.py
[ ] No breaking changes to existing error handling
[ ] Request context can be added later if needed
```

---

## Quick Reference: Error Selection

When you see `except Exception as e:`, ask yourself:

1. **What operation am I protecting?**
   - Database operation → DatabaseError
   - External API call → ServiceError (or TimeoutError if async timeout)
   - Internal orchestration → ServiceError or OrchestratorError
   - Validation → ValidationError
   - Authentication → UnauthorizedError
   - Resource not found → NotFoundError
   - Duplicate/conflict → ConflictError

2. **What context do I need to debug this?**
   - Task ID? Add to details
   - User ID? Add to details
   - Resource ID? Add to details
   - Operation name? Include in message

3. **Is this recoverable?**
   - If yes, might want retry logic (decorator instead)
   - If no, raise the typed error

---

## Team Execution Template

For developers implementing other files, follow this pattern:

```bash
# 1. Open the file in VSCode
code src/cofounder_agent/services/[filename].py

# 2. Search for exception blocks
# Ctrl+F: except Exception

# 3. For each match:
#    a) Read surrounding context (what operation?)
#    b) Determine error type from table above
#    c) Apply template + context
#    d) Preserve logging

# 4. Run tests
pytest tests/services/test_[filename].py -v

# 5. Submit for review
# Create a commit with message:
# "refactor: standardize error handling in [filename]"
```

---

## Success Metrics

After Phase 1C completion:

✅ All 312 generic exceptions → typed exceptions  
✅ All exceptions include operation context  
✅ All exceptions have relevant details dict  
✅ All tests passing  
✅ Consistent error responses across all endpoints  
✅ Better production debugging with proper error codes

---

## After This File

Once task_executor.py is done and tests pass:

1. Apply same pattern to unified_orchestrator.py (47 exceptions, Tier 1)
2. Then database_service.py and delegates (39 exceptions, Tier 1)
3. Then Team parallelizes on Tier 2 files (20+ smaller files)

Estimated timeline with proper parallelization:

- Tier 1 (3 files, high impact): 4-5 hours with 1 person
- Tier 2-3 (65 files, distributed): 3-4 hours with team of 3
- **Total: 8.5 hours as estimated** ✓
