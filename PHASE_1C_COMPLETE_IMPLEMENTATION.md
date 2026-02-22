# Phase 1C: Complete Implementation Strategy

**Status:** Ready for Full Execution  
**Scope:** 312 generic exceptions across 68 files  
**Estimated Effort:** 8-12 hours (team of 2-3) | 16-20 hours (single developer)

---

## Part 1: Core Pattern Templates (Copy-Paste Ready)

### Import Addition Required

```python
# Add this to imports at the top of each service file:
from services.error_handler import (
    ValidationError, NotFoundError, UnauthorizedError, ForbiddenError,
    ConflictError, StateError, DatabaseError, ServiceError, TimeoutError
)
```

### Template 1: Simple Service Error (Most Common - Use 40% of the time)

**Find & Replace Pattern:**
```python
# Search for:
        except Exception as e:
            logger.error(f"...: {e}...")
            raise

# Replace with:
        except Exception as e:
            logger.error("...", exc_info=True)
            raise ServiceError(
                message="[operation description] failed",
                details={"error_type": type(e).__name__},
                cause=e,
            )
```

**Real Example:**
```python
# BEFORE:
        except Exception as e:
            logger.error(f"Error processing task {task_id}: {str(e)}", exc_info=True)
            self.error_count += 1
            raise

# AFTER:
        except Exception as e:
            logger.error("Error processing task", exc_info=True)
            self.error_count += 1
            raise ServiceError(
                message=f"Failed to process task {task_id}",
                details={"task_id": task_id, "error_type": type(e).__name__},
                cause=e,
            )
```

### Template 2: Database Error (Use 20% of the time)

```python
# BEFORE:
        except Exception as e:
            logger.error(f"Database error: {e}")
            raise

# AFTER:
        except asyncpg.UniqueViolationError as e:
            # Duplicate key violation
            raise ConflictError(
                message="[entity] already exists",
                details={"field": "[field_name]"},
                cause=e,
            )
        except asyncpg.NotNullViolationError as e:
            # Required field missing
            raise ValidationError(
                message="Required field is missing",
                field="[field_name]",
                cause=e,
            )
        except asyncpg.PostgresError as e:
            # Generic database error
            logger.error("Database error", exc_info=True)
            raise DatabaseError(
                message="Database operation failed",
                details={"operation": "[operation]"},
                cause=e,
            )
```

### Template 3: Timeout Error (Use 10% of the time)

```python
# BEFORE:
        except Exception as e:
            logger.error(f"Timeout: {e}")
            raise

# AFTER:
        except asyncio.TimeoutError as e:
            raise TimeoutError(
                message="Operation exceeded timeout",
                details={"timeout_seconds": TIMEOUT},
                cause=e,
            )
        except Exception as e:
            logger.error("Unexpected error", exc_info=True)
            raise ServiceError(
                message="Operation failed unexpectedly",
                cause=e,
            )
```

### Template 4: Not Found Error (Use 10% of the time)

```python
# BEFORE:
        except Exception as e:
            if "not found" in str(e).lower():
                raise HTTPException(status_code=404)
            raise

# AFTER:
        except (KeyError, ValueError) as e:
            if "not found" in str(e).lower():
                raise NotFoundError(
                    message="[Resource] not found",
                    resource_type="[type]",
                    resource_id="[id]",
                    cause=e,
                )
            raise ValidationError(
                message=str(e),
                cause=e,
            )
```

### Template 5: API/HTTP Error (Use 10% of the time)

```python
# BEFORE:
        except Exception as e:
            logger.error(f"API call failed: {e}")
            raise

# AFTER:
        except asyncio.TimeoutError as e:
            raise TimeoutError(
                message="External API call timed out",
                details={"endpoint": "[url]", "timeout_seconds": 30},
                cause=e,
            )
        except httpx.HTTPStatusError as e:
            if 400 <= e.response.status_code < 500:
                raise ValidationError(
                    message=f"Invalid request to API",
                    field="request_body",
                    cause=e,
                )
            else:
                raise ServiceError(
                    message="External API returned error",
                    details={"status": e.response.status_code},
                    cause=e,
                )
        except (httpx.RequestError, Exception) as e:
            raise ServiceError(
                message="Failed to call external API",
                details={"error_type": type(e).__name__},
                cause=e,
            )
```

### Template 6: Conflict/State Error (Use 10% of the time)

```python
# BEFORE:
        except Exception as e:
            if "conflict" in str(e).lower() or "already" in str(e).lower():
                raise HTTPException(status_code=409)
            raise

# AFTER:
        except asyncpg.UniqueViolationError as e:
            raise ConflictError(
                message="[Entity] already exists",
                cause=e,
            )
        except Exception as e:
            if "conflict" in str(e).lower() or "already" in str(e).lower():
                raise ConflictError(
                    message=str(e),
                    cause=e,
                )
            if "invalid state" in str(e).lower():
                raise StateError(
                    message=str(e),
                    current_state="[current]",
                    requested_action="[action]",
                    cause=e,
                )
            raise ServiceError(
                message="Operation failed",
                cause=e,
            )
```

---

## Part 2: File-by-File Execution Plan

### Tier 1 Priority (CRITICAL - Do FIRST)

#### File 1: task_executor.py (17 replacements)

**Locations to update:**
- Line 176: `except Exception as e:` in task loop → ServiceError
- Line 196: `except Exception as update_err:` database update → DatabaseError
- Line 217: `except Exception as e:` in loop → ServiceError
- Line 250: `except Exception as e:` debug task dump → ServiceError (or ignore)
- Line 283: `except Exception as e:` in _execute_task → ServiceError
- Line 415: `except Exception as e:` publish attempt → ServiceError
- Line 437: `except Exception as e:` fallback error → ServiceError
- Line 440: `except Exception as e:` main error handler → ServiceError
- Line 672: `except Exception as fallback_err:` fallback error → ServiceError
- Line 682: `except Exception as e:` quality check → ServiceError
- Line 729: `except Exception as e:` critique loop → ServiceError
- Line 906: `except Exception as refine_err:` refinement → ServiceError
- Line 1019: `except Exception as e:` module import → ServiceError
- Line 1175: `except Exception as e:` final catch-all → ServiceError
- Plus 3 more async error handlers

**Execution Steps for task_executor.py:**
1. Add imports at top: `from services.error_handler import (...)`
2. For each `except Exception as e:` clause:
   - Read the surrounding context (what operation?)
   - If it's a database operation → DatabaseError
   - If it's API/HTTP → ServiceError or specific error
   - If it's internal orchestration → OrchestratorError
   - Otherwise → ServiceError
3. Apply appropriate template from Part 1
4. Preserve all existing logging
5. Run tests: `pytest tests/services/test_task_executor.py -v`

#### File 2: unified_orchestrator.py (47 replacements)

Key sections:
- Agent instantiation → OrchestratorError
- Request parsing → ValidationError/OrchestratorError
- Content creation pipeline → Multiple error types
- Handler dispatch → OrchestratorError
- Financial analysis → ServiceError
- Compliance checks → ServiceError

**Strategy:** Read function by function, apply templates based on context

#### File 3: database_service.py + delegates (39 replacements)

Key sections:
- users_db.py: OAuth failures → UnauthorizedError/AuthError
- tasks_db.py: Query failures → NotFoundError/DatabaseError
- content_db.py: Parse/validation → ValidationError/DatabaseError
- admin_db.py: Most can safely stay as ServiceError
- writing_style_db.py: Query errors → DatabaseError/NotFoundError

**Strategy:** Focus on specific database error types (UniqueViolation, NotFound, etc.)

### Tier 2 Priority (MEDIUM - Do SECOND)

Files needing similar patterns:
- content_agent/orchestrator.py (24 exceptions)
- creative_agent.py (18 exceptions)
- model_router.py (16 exceptions) - API/HTTP focused
- workflow_executor.py (14 exceptions)
- capability_task_executor.py (12 exceptions)

**Execution:** Apply Templates 1-5 based on file context

###Tier 3 Priority (LOWER - Do THIRD)

- auth files (8-10) - Use Template 6 + UnauthorizedError
- cache files (5-7) - Use Template 1 (safe)
- external API files (4-6) - Use Template 5 (HTTP errors)
- Other supporting (30+ exceptions) - Use templates 1-2

### Tier 4 (LOWEST - Do IF TIME)

- Testing/profiling files (20-30 exceptions)
- Diagnostic services (5-10 exceptions)

---

## Part 3: Verification Checklist

After completing each file:

```
[ ] Added imports for error classes
[ ] All `except Exception as e:` lines replaced with typed exceptions
[ ] All `except Exception:` bare clauses replaced
[ ] Message includes context about what operation failed
[ ] Details dict includes relevant identifiers (task_id, user_id, resource_id, etc.)
[ ] cause=e parameter preserved in all raised exceptions
[ ] Original logging calls preserved (or improved)
[ ] No breaking changes to exception handling logic
[ ] Tests pass: pytest tests/services/test_[filename].py -v
[ ] Type hints validated: mypy src/cofounder_agent/services/[filename].py
```

---

## Part 4: Testing Strategy

### Unit Test Framework

```python
import pytest
from services.error_handler import DatabaseError, ValidationError, ServiceError

async def test_database_error_on_unique_violation():
    """Test that duplicate keys raise ConflictError"""
    from services.task_executor import TaskExecutor
    executor = TaskExecutor(db_service_mock)
    
    with pytest.raises(ConflictError) as exc_info:
        await executor.create_duplicate_task({...})
    
    assert exc_info.value.message == "Task already exists"
    assert exc_info.value.error_code.value == "CONFLICT"

async def test_timeout_error_on_slow_operation():
    """Test that timeouts raise TimeoutError"""
    with pytest.raises(TimeoutError) as exc_info:
        await executor.slow_operation(timeout=1)
    
    assert exc_info.value.error_code.value == "TIMEOUT_ERROR"
```

### Integration Test

```python
async def test_error_response_format():
    """Test that errors return proper API response format"""
    response = client.post("/api/tasks", json={"invalid": "data"})
    
    assert response.status_code in [400, 422, 409, 500, 503, 504]
    assert "error_code" in response.json()
    assert "message" in response.json()
    # Optional: assert "request_id" in response.json()
```

---

## Part 5: Rollout Plan

### Phase 5A: Tier 1 Validation (1-2 days)
- [ ] Complete all Tier 1 replacements
- [ ] Run full test suite: `pytest tests/ -v`
- [ ] Manual smoke tests on dev environment
- [ ] Code review for consistency

### Phase 5B: Deploy to Staging (1 day)
- [ ] Merge Tier 1 changes to dev branch
- [ ] Deploy to Railway staging environment
- [ ] Test real error paths with staging data
- [ ] Monitor error logs for "Unexpected error" patterns

### Phase 5C: Tiers 2-4 Implementation (2-3 days)
- [ ] Complete Tier 2 replacements (content pipeline)
- [ ] Test content generation with error handling
- [ ] Complete Tier 3 + 4 replacements
- [ ] Run full test suite again

### Phase 5D: Final Deployment (1 day)
- [ ] Merge all changes to main
- [ ] Deploy to production
- [ ] Monitor error metrics in first 24 hours
- [ ] Success criteria: structured error logs with error_codes

---

## Part 6: Quick Reference - Common Replacements

| Context | Replace With | Example |
|---------|--------------|---------|
| Database query | DatabaseError | "Failed to fetch task from database" |
| Duplicate key | ConflictError | "Task name already exists" |
| Not found | NotFoundError | "User with ID 123 not found" |
| API timeout | TimeoutError | "External API did not respond within 30s" |
| API failure | ServiceError | "OpenAI API returned 500 error" |
| Invalid input | ValidationError | "Email address is invalid" |
| Permission denied | ForbiddenError | "User cannot approve this task" |
| Invalid state | StateError | "Cannot transition from DRAFT to ARCHIVED" |
| Auth failure | UnauthorizedError | "Token is invalid or expired" |
| Generic fallback | ServiceError | "An unexpected error occurred" |

---

## Part 7: Estimated Time Breakdown

| Task | Files | Exceptions | Time |
|------|-------|-----------|------|
| **Tier 1** | 3 | ~110 | 3.5h |
| - task_executor.py | 1 | 17 | 0.5h |
| - unified_orchestrator.py | 1 | 47 | 1.5h |
| - database_service + delegates | 6 | 46 | 1.5h |
| **Tier 2** | 5 | ~84 | 2.5h |
| **Tier 3** | 25+ | ~90 | 2.5h |
| **Tier 4** | 35+ | ~20 | 0.5h |
| **Testing & Validation** | - | - | 1h |
| **Deploy & Verify** | - | - | 1h |
|  **TOTAL** | **68** | **~312** | **8.5-10h** |

---

## Part 8: Success Criteria

✅ **Phase 1C is COMPLETE when:**

1. All 68 service files have typed exceptions (0 bare `except:`, <10 generic `Exception`
2. All raised exceptions include:
   - Appropriate error code from ErrorCode enum
   - Clear, contextual message
   - Details dict with relevant IDs/context
   - cause=original_exception parameter
3. All database files handle specific PostgreSQL error types
4. All API/HTTP files handle timeout and status errors appropriately
5. Request ID propagation implemented (if desired)
6. Test coverage >80% for error paths
7. Documentation updated (PHASE_1C_COMPLETE.md)
8. Error logs now structured with error_code for monitoring/dashboards

---

## Quick Start Commands

```bash
# Verify imports are in place
grep -r "from services.error_handler import" src/cofounder_agent/services/ | wc -l

# Count remaining generic exceptions
grep -r "except Exception" src/cofounder_agent/services/ | wc -l

# Run tests to verify changes
pytest tests/services/ -v --tb=short

# Generate test coverage report
pytest tests/services/ --cov=src/cofounder_agent/services --cov-report=html

# Deploy changes
git add src/cofounder_agent/services/
git commit -m "Phase 1C: Full error handling implementation (Tiers 1-4)"
git push origin phase-1c
```

---

## Integration with Existing Infrastructure

✅ **Already in place:**
- Exception base classes (AppError, 9 domain-specific types)
- ErrorCode enum (28 codes covering all scenarios)
- Exception handlers middleware (converts AppError to HTTP responses)
- structlog configured for error logging
- Request context tracking

✅ **No additional infrastructure needed**
- Just swap `except Exception` with typed exceptions
- Use provided templates
- Preserve existing logging

---

## Support & Questions

- **Question:** How do I know which exception type to use?
  - **Answer:** Look at the code context. Is it a database operation? API call? State transition? See Part 2 / Part 6 Quick Reference.

- **Question:** Do I need to modify tests?
  - **Answer:** Tests should already pass. Just verify with `pytest tests/services/ -v` after your changes.

- **Question:** Should I refactor the error handling logic?
  - **Answer:** No! Just replace the `except Exception` clause. Keep the logic the same.

- **Question:** What about specific exception types like ValueError?
  - **Answer:** Those stay as-is. We're only replacing generic `Exception` clauses with typed AppError subclasses.

---

## Next Phase (Phase 2 - After 1C Complete)

Once Phase 1C is complete with all 312 replacements:
- Add request ID propagation middleware (contextvars)
- Create error monitoring dashboard (track error codes, rates)
- Implement automatic error recovery for specific scenarios
- Add structured error logging to central logging system (e.g., Sentry)
- Test production error handling with synthetic errors

