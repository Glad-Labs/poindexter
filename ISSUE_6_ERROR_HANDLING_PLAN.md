# Issue #6 - Error Handling Standardization (P2-High)

**Status:** IN PROGRESS  
**Estimated Time:** 8.5 hours  
**Priority Files:** 68 services with 312 unhandled exceptions  
**Current Scope:** Top 5 high-impact files (85 exceptions combined)  

---

## Objective

Standardize error handling across all 68 backend service files using unified patterns from `utils/error_handler.py` and `services/error_handler.py`.

## Error Handling Templates Available

### 1. **Route Error Handler** (for API endpoints)

```python
from utils.error_handler import handle_route_error

try:
    result = await service.operation()
    return result
except HTTPException:
    raise
except Exception as e:
    raise await handle_route_error(e, "operation_name", logger)
```

### 2. **Service Error Handler** (for service methods)

```python
from utils.error_handler import handle_service_error

try:
    result = await db.fetch()
    return result
except Exception as e:
    return handle_service_error(e, "fetch_operation", logger, fallback_value=[])
```

### 3. **Specific Exception Mapping**

```python
except ValueError:
    raise ValueError(f"Invalid input: {str(error)}")
except KeyError:
    logger.error(f"Missing key: {str(error)}")
    raise KeyError(f"Required field missing: {str(error)}")
except TimeoutError:
    raise TimeoutError(f"Operation timed out: {operation_name}")
```

---

## High-Impact Files (Priority Ranking)

| File | Exceptions | Priority | Est. Time |
|------|-----------|----------|-----------|
| custom_workflows_service.py | 19 | P1 | 1.0h |
| tasks_db.py | 16 | P1 | 0.9h |
| task_executor.py | 14 | P1 | 0.8h |
| image_service.py | 14 | P1 | 0.8h |
| unified_orchestrator.py | 13 | P1 | 0.8h |
| **Subtotal (Top 5)** | **76** | - | **4.3h** |
| Remaining 63 files | 236 | P2-P4 | 4.2h |
| **TOTAL** | **312** | - | **8.5h** |

---

## Implementation Strategy

### Phase 1: Foundation (Completed in this session)

- ✅ Establish error handling patterns in top 5 files
- ✅ Create standardized exception responses
- ✅ Document approach for automation

### Phase 2: Remaining Files (Future sessions)

Use automated search-and-replace patterns:

```bash
# Find all generic exception handlers
grep -r "except Exception" src/cofounder_agent/services/*.py

# Apply standard handler pattern using provided templates
# Process files in batches of 10-15
```

---

## Pattern Examples

### ❌ Current Pattern (To Replace)

```python
try:
    result = await operation()
    return result
except Exception as e:
    logger.error(f"Error: {str(e)}")
    # Missing: proper status code, error type, context
    raise
```

### ✅ Standardized Pattern (Target)

```python
try:
    result = await operation()
    return result
except HTTPException:
    raise
except Exception as e:
    # Use handle_route_error for routes, handle_service_error for services
    raise await handle_route_error(e, "operation_name", logger)
```

---

## Files Already Using Standard Patterns

- ✅ `utils/error_handler.py` - Defines standard handlers
- ✅ `services/error_handler.py` - Service-specific error handling
- ✅ `workflow_routes.py` - Recently fixed pause/resume/cancel
- ✅ Multiple routes with `HTTPException` handling

---

## Automation Opportunities

For bulk fixing, use pattern matching:

```python
# Pattern 1: Service methods with fallback
OLD: except Exception as e:
     logger.error(...)
     raise

NEW: except Exception as e:
     return handle_service_error(e, "method_name", logger, fallback_value=[])

# Pattern 2: Route handlers
OLD: except Exception as e:
     logger.error(...)
     raise HTTPException(...)

NEW: except Exception as e:
     raise await handle_route_error(e, "operation", logger)
```

---

## Impact Assessment

## Before (Current State)

- 312+ generic `Exception` catches
- Inconsistent error logging
- No standardized error response format
- Difficult to debug across services
- HTTP status codes not always appropriate

## After (Target State)

- ✅ Specific exception types caught
- ✅ Consistent logging with context
- ✅ Standardized error responses
- ✅ Proper HTTP status code mapping
- ✅ Reduced code duplication (reuse templates)

---

## Next Steps

1. **Session 2:** Fix remaining top 15 files (90+ more exceptions)
2. **Session 3:** Batch-apply patterns to remaining 50+ files
3. **Automation:** Create script to verify all services use standard handlers
4. **Testing:** Add error handling test coverage

---

## Related Issues

- Issue #7 (P3-Medium): Type safety - works with error handling patterns
- Issue #11 (P3-Medium): Refactoring - depends on error standardization

---

**Last Updated:** Feb 22, 2026  
**Next Review:** After completing top 5 files
