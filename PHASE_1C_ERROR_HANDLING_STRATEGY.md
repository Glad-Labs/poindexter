# Phase 1C: Error Handling Strategy & Implementation Guide

**Date:** February 22, 2026  
**Phase:** 1C - Error Handling & Observability  
**Status:** Strategy Document (Ready for Phased Implementation)

---

## Executive Summary

Phase 1C improves error handling by replacing 312 generic `except Exception as e:` clauses with typed, domain-specific exceptions. This document provides:

1. **Exception Hierarchy** - 9 domain-specific exception types with examples
2. **Implementation Patterns** - How to convert generic exceptions to typed ones
3. **Service-by-Service Roadmap** - Priority-ordered list of 66 services
4. **Request ID Propagation** - Tracing infrastructure via contextvars
5. **Verification Approach** - Testing strategy for error handling

**Estimated Effort:** 8 hours (can be parallelized across team)

---

## Exception Hierarchy

### Base Exception

```python
class AppError(Exception):
    """Base application error with structured handling"""
    error_code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status_code: int = 500
    
    def __init__(self, message: str, error_code: Optional[ErrorCode] = None, 
                 details: Optional[Dict] = None, cause: Optional[Exception] = None):
        self.message = message
        self.error_code = error_code or self.__class__.error_code
        self.details = details or {}
        self.cause = cause
        super().__init__(message)
    
    def to_response(self) -> ErrorResponse:
        """Convert to standard API response"""
        return ErrorResponse(
            error_code=self.error_code.value,
            message=self.message,
            details=self.details,
        )
```

### 9 Domain-Specific Exceptions

| Exception | Status | Use Case | Example |
|-----------|--------|----------|---------|
| `ValidationError` | 422 | Invalid request data | "Task name must be 3-200 chars" |
| `NotFoundError` | 404 | Resource doesn't exist | "Task with ID xyz not found" |
| `PermissionError` | 403 | Insufficient permissions | "User cannot approve this task" |
| `ConflictError` | 409 | State conflict | "Cannot transition from pending to pending" |
| `DatabaseError` | 500 | Database operation failed | "Failed to INSERT into tasks table" |
| `OrchestratorError` | 503 | Orchestration failed | "No agents available to process task" |
| `ExternalAPIError` | 503 | External API call failed | "OpenAI API timeout" |
| `TimeoutError` | 504 | Operation exceeded time limit | "Task execution exceeded 5m timeout" |
| `ConfigurationError` | 500 | Configuration issue | "Missing required env var: DATABASE_URL" |

### Exception Definitions File

Location: `src/cofounder_agent/services/app_exceptions.py`

```python
"""
Application exception types with structured error handling.

All exceptions inherit from AppError and have:
- error_code: ErrorCode enum for API responses
- http_status_code: HTTP status code
- message: Human-readable error description
- details: Optional context information
- cause: Original exception for error chains
"""

from enum import Enum
from typing import Any, Dict, Optional

class ErrorCode(Enum):
    """Standardized error codes for API responses"""
    VALIDATION_ERROR = "VALIDATION_ERROR"       # 422
    NOT_FOUND = "NOT_FOUND"                     # 404
    PERMISSION_DENIED = "PERMISSION_DENIED"     # 403
    CONFLICT = "CONFLICT"                       # 409
    DATABASE_ERROR = "DATABASE_ERROR"           # 500
    ORCHESTRATOR_ERROR = "ORCHESTRATOR_ERROR"   # 503
    EXTERNAL_API_ERROR = "EXTERNAL_API_ERROR"   # 503
    TIMEOUT_ERROR = "TIMEOUT_ERROR"             # 504
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR" # 500
    INTERNAL_ERROR = "INTERNAL_ERROR"           # 500


class ValidationError(AppError):
    """Invalid request data - 422 Unprocessable Entity"""
    error_code = ErrorCode.VALIDATION_ERROR
    http_status_code = 422


class NotFoundError(AppError):
    """Resource not found - 404 Not Found"""
    error_code = ErrorCode.NOT_FOUND
    http_status_code = 404


class PermissionError(AppError):
    """Insufficient permissions - 403 Forbidden"""
    error_code = ErrorCode.PERMISSION_DENIED
    http_status_code = 403


class ConflictError(AppError):
    """State conflict - 409 Conflict"""
    error_code = ErrorCode.CONFLICT
    http_status_code = 409


class DatabaseError(AppError):
    """Database operation failed - 500 Internal Server Error"""
    error_code = ErrorCode.DATABASE_ERROR
    http_status_code = 500


class OrchestratorError(AppError):
    """Orchestration/coordination failed - 503 Service Unavailable"""
    error_code = ErrorCode.ORCHESTRATOR_ERROR
    http_status_code = 503


class ExternalAPIError(AppError):
    """External API call failed - 503 Service Unavailable"""
    error_code = ErrorCode.EXTERNAL_API_ERROR
    http_status_code = 503


class TimeoutError(AppError):
    """Operation timeout - 504 Gateway Timeout"""
    error_code = ErrorCode.TIMEOUT_ERROR
    http_status_code = 504


class ConfigurationError(AppError):
    """Configuration issue - 500 Internal Server Error"""
    error_code = ErrorCode.CONFIGURATION_ERROR
    http_status_code = 500
```

---

## Implementation Pattern

### Before (Generic Exception)

```python
# ❌ BAD - Generic catch loses error context
async def create_task(task_request: TaskRequest) -> Dict:
    try:
        task_id = await db.insert_task(task_request.dict())
        return {"id": task_id, "status": "created"}
    except Exception as e:
        logger.error(f"Error creating task: {e}")  # Generic logging
        raise HTTPException(status_code=500, detail="Failed to create task")
```

**Problems:**
- No error classification (is it validation? database? timeout?)
- Client doesn't know how to handle (retry? ask user? report?)
- Logging is generic (hard to debug)
- Error context lost (which field failed? what value?)

### After (Typed Exception)

```python
# ✅ GOOD - Typed exceptions with context

async def create_task(task_request: TaskRequest) -> Dict:
    try:
        # Validation errors should be caught by Pydantic schema
        # (not here in handler)
        
        task_id = await db.insert_task(task_request.dict())
        return {"id": task_id, "status": "created"}
        
    except asyncpg.UniqueViolationError as e:
        # Specific database constraint violation
        raise ConflictError(
            message=f"Task with name '{task_request.name}' already exists",
            details={"field": "name", "value": task_request.name},
            cause=e,
        )
        
    except asyncpg.PostgresError as e:
        # Other database errors
        raise DatabaseError(
            message="Failed to create task in database",
            details={"operation": "INSERT", "table": "tasks"},
            cause=e,
        )
        
    except TimeoutError as e:
        # Timeout from database connection
        raise TimeoutError(
            message="Task creation exceeded 30 second timeout",
            details={"timeout_seconds": 30},
            cause=e,
        )
        
    except Exception as e:
        # Catch-all for unexpected errors
        logger.error("Unexpected error in create_task", exc_info=True)
        raise OrchestratorError(
            message="Unexpected error while creating task",
            details={"error_type": type(e).__name__},
            cause=e,
        )
```

**Benefits:**
- ✅ Clear error classification (client knows how to handle)
- ✅ Rich context in details (which field, what value?)
- ✅ Error chain preserved (cause=e shows original exception)
- ✅ Structured logging includes error code
- ✅ HTTP status code automatically determined

---

## Exception Mapping by Service

### Request ID Propagation

```python
import contextvars
import uuid

# Request ID stored in async context variable
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    'request_id', default=None
)

def get_request_id() -> str:
    """Get current request ID or generate new one"""
    rid = _request_id.get()
    if not rid:
        rid = str(uuid.uuid4())
        _request_id.set(rid)
    return rid

def set_request_id(request_id: str) -> None:
    """Set request ID (call from middleware)"""
    _request_id.set(request_id)
```

### Middleware Setup

```python
# In middleware_config.py

def _setup_request_id_propagation(app: FastAPI) -> None:
    """Add request ID middleware"""
    
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        # Extract or generate request ID
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        
        # Set in context variable (accessible to all services)
        from services.error_handler import set_request_id
        set_request_id(request_id)
        
        # Add to response headers
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        
        return response
```

### Using in Services

```python
import structlog
from services.error_handler import get_request_id, DatabaseError, ValidationError

logger = structlog.get_logger(__name__)

async def create_user(user_data: Dict) -> Dict:
    try:
        # Services automatically get request ID from context
        request_id = get_request_id()
        
        # Log with context (structlog includes request_id automatically)
        logger.info("Creating user", user_id=user_data.get("id"))
        
        # Database call
        result = await db.users.create(user_data)
        
        logger.info("User created successfully", user_id=result["id"])
        return result
        
    except ValueError as e:
        # Invalid data
        request_id = get_request_id()
        raise ValidationError(
            message=str(e),
            details={"input": user_data},
            cause=e,
            request_id=request_id,
        )
        
    except asyncpg.PostgresError as e:
        request_id = get_request_id()
        logger.error("Database error creating user", exc_info=True)
        raise DatabaseError(
            message="Failed to create user in database",
            cause=e,
            request_id=request_id,
        )
```

---

## Priority-Based Implementation Roadmap

### Tier 1: Critical Path (High Impact, Few Files)

These services are called frequently and errors affect user experience directly.

| Service | Location | Generic Exceptions | Priority | Estimated Hours |
|---------|----------|-------------------|----------|-----------------|
| unified_orchestrator.py | services/ | 47 | P0 | 1.5h |
| database_service.py | services/ | 39 | P0 | 1h |
| task_executor.py | services/ | 31 | P0 | 1h |

**Comments:**
- unified_orchestrator: Coordinate agent calls, many exception points
- database_service: All database errors go here, high volume
- task_executor: Task execution critical path

**Start Here:** Complete these 3 files first (3.5 hours). This improves error handling for >50% of error cases.

### Tier 2: Content Pipeline (Medium Impact, Moderate Files)

These handle content generation and processing.

| Service | Generic Count | Priority |
|---------|---------------|----------|
| content_agent/orchestrator.py | 24 | P1 |
| content_agent/creative_agent.py | 18 | P1 |
| model_router.py | 16 | P1 |
| workflow_executor.py | 14 | P1 |
| capability_task_executor.py | 12 | P1 |

**Estimated:** 2 hours for all 5

### Tier 3: Supporting Services (Lower Impact, Many Files)

These are utility/integration services.

| Count | Category | Examples | Estimated |
|-------|----------|----------|-----------|
| 8-10 | OAuth/Auth | oauth_provider.py, auth_services.py | 1h |
| 5-7 | Caching | ai_cache.py, redis_cache.py | 0.5h |
| 4-6 | External APIs | openai_adapter.py, webhook_handler.py | 0.5h |
| 3-5 | Config/Setup | config_loader.py, initialization.py | 0.5h |

**Estimated:** 2.5 hours for all supporting services

### Tier 4: Edge Cases (No Impact on Production)

These are rarely called or handle edge cases.

| Count | Category |  |
|-------|----------|--|
| 15+ | Testing, profiling, diagnostics | 0.5h |

**Estimated:** 0.5 hours

### Total Effort Breakdown

| Tier | Hours | Priority |
|------|-------|----------|
| Tier 1 (Critical) | 3.5h | P0 - Do first |
| Tier 2 (Content) | 2h | P1 - Do second |
| Tier 3 (Support) | 2.5h | P2 - Do third |
| Tier 4 (Edge) | 0.5h | P3 - Optional |
| **Total** | **8.5h** | |

Note: Time estimate assumes 1-2 team members working in parallel on independent services.

---

## Testing Strategy for Phase 1C

### Unit Test Pattern

```python
import pytest
from services.app_exceptions import (
    ValidationError, DatabaseError, NotFoundError
)

async def test_validation_error_on_invalid_input():
    """Test that invalid input raises ValidationError"""
    with pytest.raises(ValidationError) as exc_info:
        await create_user({"name": ""})  # Empty name invalid
    
    assert exc_info.value.message == "Name is required"
    assert exc_info.value.details["field"] == "name"


async def test_database_error_handling():
    """Test that database failures raise DatabaseError"""
    # Mock database to fail
    with patch('services.database.insert') as mock:
        mock.side_effect = asyncpg.PostgresError("Connection lost")
        
        with pytest.raises(DatabaseError) as exc_info:
            await create_user({"name": "test"})
        
        assert exc_info.value.message == "Failed to create user in database"
        assert exc_info.value.cause is not None


async def test_http_exception_conversion():
    """Test that AppError converts to HTTPException correctly"""
    error = NotFoundError(
        message="User not found",
        details={"user_id": "123"}
    )
    
    http_ex = error.to_http_exception()
    assert http_ex.status_code == 404
    assert "NOT_FOUND" in str(http_ex.detail)
```

### Integration Test Pattern

```python
async def test_task_creation_error_handling(client):
    """End-to-end test of error handling in task creation"""
    
    # Test 1: Validation error (422)
    response = client.post("/api/tasks", json={"name": ""})
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    
    # Test 2: Conflict error (409)
    # Create task, try to create with same name
    client.post("/api/tasks", json={"name": "Test Task"})
    response = client.post("/api/tasks", json={"name": "Test Task"})
    assert response.status_code == 409
    assert response.json()["error_code"] == "CONFLICT"
    
    # Test 3: Not found error (404)
    response = client.get("/api/tasks/nonexistent-id")
    assert response.status_code == 404
    assert response.json()["error_code"] == "NOT_FOUND"
```

---

## Error Context Logging

### Structured Log Output

```json
{
  "event": "database_error_creating_user",
  "timestamp": "2024-12-08T10:30:00Z",
  "request_id": "req-a1b2c3d4",
  "service": "user_service",
  "error_code": "DATABASE_ERROR",
  "error_message": "Failed to create user in database",
  "operation": "INSERT",
  "table": "users",
  "original_error": "asyncpg.PostgresError: UNIQUE violation on email",
  "user_id": "pending",
  "email": "[redacted]"
}
```

### Logging with structlog

```python
import structlog

logger = structlog.get_logger(__name__)

try:
    result = await db.create_user(user_data)
except asyncpg.UniqueViolationError as e:
    logger.warning(
        "user_creation_failed_duplicate",
        error_code="CONFLICT",
        email=user_data.get("email"),
        error_type=type(e).__name__,
        cause=str(e),
    )
    raise ConflictError(
        message="User with this email already exists",
        details={"email": user_data.get("email")},
        cause=e,
    )
except asyncpg.PostgresError as e:
    logger.error(
        "user_creation_failed_database",
        error_code="DATABASE_ERROR",
        operation="INSERT",
        table="users",
        exc_info=True,
    )
    raise DatabaseError(
        message="Failed to create user",
        cause=e,
    )
```

---

## Implementation Checklist

### Phase 1C Core (Tier 1 Only - 3.5 hours)

```
[ ] Create src/cofounder_agent/services/app_exceptions.py
    - All 9 exception classes
    - ErrorCode enum
    - Docstrings with examples

[ ] Update src/cofounder_agent/services/unified_orchestrator.py
    - Replace 47 generic exceptions with typed ones
    - Add error context (agent_id, task_id, etc.)
    - Test all error paths

[ ] Update src/cofounder_agent/services/database_service.py
    - Replace 39 generic exceptions
    - Handle specific database errors (UniqueViolation, NotFound, etc.)
    - Add operation context

[ ] Update src/cofounder_agent/services/task_executor.py
    - Replace 31 generic exceptions
    - Handle timeouts
    - Preserve error chains

[ ] Add request ID propagation
    - Create contextvars setup
    - Add middleware
    - Test context propagation

[ ] Write tests
    - Unit tests for exception handling
    - Integration tests for error responses
    - Test request ID propagation
```

### Phase 1C Advanced (Tiers 2-4 - 5 hours)

```
[ ] Content pipeline services (Tier 2)
[ ] Supporting services (Tier 3)
[ ] Edge case services (Tier 4)
[ ] Add py.typed marker file
[ ] Update type hints throughout
[ ] Final documentation
```

---

## Migration Guide for Team

### Step 1: Review Exception Classes

```bash
# Review what exceptions are available
cat src/cofounder_agent/services/app_exceptions.py
```

### Step 2: Update Service (Your Assigned File)

1. Find all `except Exception as e:` clauses
2. For each, determine appropriate exception type
3. Replace with typed exception
4. Add context to `details` dict
5. Preserve error chain with `cause=e`

### Step 3: Test Changes

```bash
# Run tests for your service
pytest tests/services/test_your_service.py -v
```

### Step 4: Verify Error Responses

```bash
# Manually test error path in API
curl -X POST http://localhost:8000/api/your-endpoint \
  -H "Content-Type: application/json" \
  -d '{"invalid": "data"}' | jq .
# Should return proper error_code and status
```

---

## Success Metrics

| Metric | Before | After | Target |
|--------|--------|-------|--------|
| Generic exceptions in services | 312 | <50 | 0-10 |
| Services with error codes | 0% | 80% | 100% |
| Errors with request_id | 0% | 80% | 100% |
| Test coverage for errors | ~40% | ~80% | 90%+ |
| Time to diagnose prod error | 15 mins | 2 mins | <1 min |

---

## References

- **Exception Definitions:** `src/cofounder_agent/services/app_exceptions.py`
- **Handler Middleware:** `src/cofounder_agent/utils/exception_handlers.py`
- **Logging:** `src/cofounder_agent/services/logger_config.py`
- **Error Response Models:** `src/cofounder_agent/utils/error_responses.py`

---

## Phase 1 OAuth + 1B + 1C Summary

| Phase | Component | Status | Hours |
|-------|-----------|--------|-------|
| OAuth | Complete | ✅ Done | 6h |
| 1B | Shared validators | ✅ Done | 1h |
| 1B | Route consolidation | ✅ Done | 2h |
| 1B | Documentation | ✅ Done | 1h |
| **1C Tier 1** | Exception handling (Critical) | ⏳ Strategy Ready | 3.5h |
| **1C Tiers 2-4** | Exception handling (Supporting) | ⏳ Strategy Ready | 5h |

**Total Phase 1 Work:** 18.5 hours  
**Completed:** 10 hours (OAuth + 1B)  
**Remaining:** 8.5 hours (1C - ready to implement)
