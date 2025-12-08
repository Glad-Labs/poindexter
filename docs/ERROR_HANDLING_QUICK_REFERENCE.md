# Error Handling Quick Reference Card

## Import Statements

```python
# Exception classes
from services.error_handler import (
    ValidationError,
    NotFoundError,
    UnauthorizedError,
    ForbiddenError,
    ConflictError,
    StateError,
    DatabaseError,
    ServiceError,
    TimeoutError,
    AppError,
)

# Recovery patterns
from services.error_handler import (
    CircuitBreaker,
    retry_with_backoff,
)

# Utilities
from services.error_handler import (
    validate_string_field,
    validate_integer_field,
    validate_enum_field,
    ErrorContext,
    ErrorCategory,
    log_error_context,
)
```

## Exception Quick Reference

| Exception           | Status | When to Use                    |
| ------------------- | ------ | ------------------------------ |
| `ValidationError`   | 400    | Input validation failed        |
| `UnauthorizedError` | 401    | Authentication required/failed |
| `ForbiddenError`    | 403    | User lacks permission          |
| `NotFoundError`     | 404    | Resource doesn't exist         |
| `ConflictError`     | 409    | Resource already exists        |
| `StateError`        | 422    | Invalid state transition       |
| `DatabaseError`     | 500    | Database operation failed      |
| `ServiceError`      | 500    | Service operation failed       |
| `TimeoutError`      | 504    | Operation exceeded timeout     |

## Common Patterns

### 1. Input Validation

```python
# String validation
topic = validate_string_field(request.topic, "topic", min_length=3)

# Integer validation
limit = validate_integer_field(request.limit, "limit", min_value=1, max_value=100)

# Enum validation
status = validate_enum_field(request.status, "status", TaskStatus)
```

### 2. Resource Lookup

```python
task = await db.get_task(task_id)
if not task:
    raise NotFoundError(
        "Task not found",
        resource_type="Task",
        resource_id=task_id
    )
```

### 3. State Transitions

```python
if task.status != "pending":
    raise StateError(
        "Cannot execute task in this state",
        current_state=task.status,
        requested_action="execute"
    )
```

### 4. Duplicate Checks

```python
existing = await db.find_by_name(name)
if existing:
    raise ConflictError(
        "Resource already exists",
        details={"name": name}
    )
```

### 5. Retry Logic

```python
@retry_with_backoff(max_retries=3, initial_delay=1.0)
async def risky_operation():
    return await external_service.call()
```

### 6. Circuit Breaker

```python
breaker = CircuitBreaker("service_name", failure_threshold=5)

async def call_service():
    return await breaker.call_async(service.method)
```

### 7. Database Operations

```python
try:
    await db.insert_task(task_data)
except asyncpg.UniqueViolationError:
    raise ConflictError("Task name must be unique")
except asyncpg.ForeignKeyViolationError:
    raise ValidationError("Invalid referenced user")
except Exception as e:
    raise DatabaseError("Insert failed", cause=e)
```

## Standard Response Format

```json
{
  "error_code": "ERROR_CODE",
  "message": "Human readable message",
  "details": { "optional": "context" },
  "request_id": "uuid-for-tracing"
}
```

## Error Codes Reference

**Validation (400)**

- VALIDATION_ERROR
- INVALID_INPUT
- INVALID_PARAMETER
- MISSING_REQUIRED_FIELD
- CONSTRAINT_VIOLATION

**Authentication (401)**

- UNAUTHORIZED
- TOKEN_INVALID

**Authorization (403)**

- FORBIDDEN
- PERMISSION_DENIED

**Not Found (404)**

- NOT_FOUND
- RESOURCE_NOT_FOUND
- TASK_NOT_FOUND
- USER_NOT_FOUND

**Conflict (409)**

- CONFLICT
- ALREADY_EXISTS

**Invalid State (422)**

- STATE_ERROR
- INVALID_STATE

**Server Errors (500)**

- INTERNAL_ERROR
- DATABASE_ERROR
- SERVICE_ERROR
- EXTERNAL_SERVICE_ERROR

**Timeout (504)**

- TIMEOUT_ERROR

**Unavailable (503)**

- SERVICE_UNAVAILABLE

## Error Context Tracking

```python
context = ErrorContext(
    category=ErrorCategory.EXTERNAL_SERVICE,
    service="openai",
    operation="generate_text",
    request_id=request_id,
    user_id=user_id,
    metadata={"model": "gpt-4"}
)

try:
    result = await openai.generate(prompt)
except Exception as e:
    context.error = e
    log_error_context(context)  # Logs + sends to Sentry
```

## Testing Errors

```python
# Test validation error
response = client.post("/api/tasks", json={"topic": "ab"})
assert response.status_code == 400
assert response.json()["error_code"] == "VALIDATION_ERROR"

# Test not found
response = client.get("/api/tasks/nonexistent")
assert response.status_code == 404
assert response.json()["error_code"] == "NOT_FOUND"

# Test conflict
response = client.post("/api/tasks", json={"name": "duplicate"})
assert response.status_code == 409
assert response.json()["error_code"] == "CONFLICT"
```

## Best Practices Checklist

- ✅ Use specific exception classes, not generic Exception
- ✅ Include context in error details (field, constraint, value)
- ✅ Use validation helpers for input safety
- ✅ Add circuit breaker for external service calls
- ✅ Use retry decorator for transient failures
- ✅ Log errors with full context (request_id, user_id)
- ✅ Include cause exception for debugging
- ✅ Test error paths in unit tests
- ✅ Return meaningful error messages
- ✅ Track error metrics in production

## Debugging Tips

**View error logs:**

```bash
grep "ERROR" logs/cofounder-agent.log | tail -20
```

**Check request ID correlation:**

```bash
grep "request-123" logs/cofounder-agent.log
# Find all events for this request
```

**Monitor circuit breakers:**

```python
status = breaker.get_status()
print(f"State: {status['state']}")
print(f"Failures: {status['failure_count']}/{status['threshold']}")
```

**View Sentry errors:**

- Open Sentry dashboard
- Filter by tag: `request_id=<uuid>`
- View error grouping and metrics

## Common Mistakes to Avoid

❌ **Don't:** `raise Exception("Error message")`  
✅ **Do:** `raise ValidationError("Invalid input", field="name")`

❌ **Don't:** `raise ServiceError("API failed")`  
✅ **Do:** `raise ServiceError("OpenAI API failed", cause=original_exception)`

❌ **Don't:** Manual retry loops  
✅ **Do:** `@retry_with_backoff(max_retries=3)`

❌ **Don't:** Direct external service calls without protection  
✅ **Do:** Wrap with `CircuitBreaker`

❌ **Don't:** Generic status 500 for all errors  
✅ **Do:** Use specific error codes and status codes

---

**For complete guide, see: `docs/ERROR_HANDLING_GUIDE.md`**
