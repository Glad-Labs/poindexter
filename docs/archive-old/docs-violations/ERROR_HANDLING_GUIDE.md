# Comprehensive Error Handling Guide

## Overview

The Glad Labs system implements a robust, unified error handling architecture across all backend services. This guide explains:

- **Error Classification**: How errors are categorized and reported
- **Exception Classes**: When and how to use each exception type
- **Recovery Patterns**: Retry logic and circuit breaker implementation
- **Error Responses**: Standardized API error response format
- **Best Practices**: How to integrate error handling into your code

## Architecture

The error handling system consists of:

### 1. **Error Classification** (`ErrorCode` enum)

Standard error codes for consistent client-side handling:

```python
class ErrorCode(str, Enum):
    # Validation errors (400)
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_INPUT = "INVALID_INPUT"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"

    # Authentication/Authorization errors (401/403)
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    TOKEN_INVALID = "TOKEN_INVALID"

    # Not found errors (404)
    NOT_FOUND = "NOT_FOUND"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"

    # Conflict/State errors (409/422)
    CONFLICT = "CONFLICT"
    STATE_ERROR = "STATE_ERROR"

    # Server errors (500)
    INTERNAL_ERROR = "INTERNAL_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    SERVICE_ERROR = "SERVICE_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
```

### 2. **Exception Classes** (Inherit from `AppError`)

#### Base Exception: `AppError`

```python
raise AppError(
    message="Something went wrong",
    error_code=ErrorCode.SERVICE_ERROR,
    details={"service": "ollama", "operation": "generate"}
)
```

#### Domain-Specific Exceptions

**ValidationError (400)**

```python
from services.error_handler import ValidationError

raise ValidationError(
    message="Invalid topic provided",
    field="topic",
    constraint="min_length=3",
    value=user_input
)
```

**NotFoundError (404)**

```python
from services.error_handler import NotFoundError

raise NotFoundError(
    message="Task not found",
    resource_type="Task",
    resource_id=task_id
)
```

**UnauthorizedError (401)**

```python
from services.error_handler import UnauthorizedError

raise UnauthorizedError(
    message="Invalid or expired token"
)
```

**ForbiddenError (403)**

```python
from services.error_handler import ForbiddenError

raise ForbiddenError(
    message="You do not have permission to access this resource"
)
```

**ConflictError (409)**

```python
from services.error_handler import ConflictError

raise ConflictError(
    message="Resource already exists",
    details={"resource": "workflow", "name": "marketing_tasks"}
)
```

**StateError (422)**

```python
from services.error_handler import StateError

raise StateError(
    message="Invalid state transition",
    current_state="in_progress",
    requested_action="delete"
)
```

**DatabaseError (500)**

```python
from services.error_handler import DatabaseError

try:
    await db.execute(query)
except Exception as e:
    raise DatabaseError(
        message="Database operation failed",
        cause=e,
        details={"operation": "insert", "table": "tasks"}
    )
```

**ServiceError (500)**

```python
from services.error_handler import ServiceError

raise ServiceError(
    message="External service failed",
    details={"service": "openai", "endpoint": "chat.completions"}
)
```

**TimeoutError (504)**

```python
from services.error_handler import TimeoutError

raise TimeoutError(
    message="Operation exceeded timeout",
    details={"timeout_seconds": 30, "operation": "api_call"}
)
```

## Recovery Patterns

### 1. Retry with Exponential Backoff

**Async Function Example:**

```python
from services.error_handler import retry_with_backoff

@retry_with_backoff(
    max_retries=3,
    initial_delay=1.0,
    max_delay=30.0,
    exponential_base=2.0
)
async def call_external_api():
    """Automatically retries on failure with exponential backoff"""
    response = await client.get("https://api.example.com/data")
    return response.json()
```

**Sync Function Example:**

```python
@retry_with_backoff(max_retries=5)
def fetch_data():
    """Works with both sync and async functions"""
    return requests.get("https://api.example.com/data").json()
```

**With Error Callback:**

```python
def log_retry_error(error, attempt, max_attempts):
    logger.warning(f"Attempt {attempt}/{max_attempts} failed: {error}")

@retry_with_backoff(
    max_retries=3,
    on_error_callback=log_retry_error
)
async def risky_operation():
    return await external_service.do_something()
```

### 2. Circuit Breaker Pattern

Protect against cascading failures when external services are down:

```python
from services.error_handler import CircuitBreaker

# Create circuit breaker for each external service
ollama_breaker = CircuitBreaker(
    name="ollama",
    failure_threshold=5,  # Open circuit after 5 failures
    recovery_timeout=60   # Attempt recovery after 60 seconds
)

# Use in async context
async def generate_text(prompt: str):
    try:
        return await ollama_breaker.call_async(
            ollama_client.generate,
            prompt=prompt
        )
    except HTTPException:
        # Circuit is open - service unavailable
        logger.error("Ollama circuit breaker is open")
        return "Service temporarily unavailable"

# Use in sync context
def process_data(data):
    try:
        return ollama_breaker.call(
            processor.process,
            data
        )
    except HTTPException:
        # Fallback to cached result or queue for retry
        return load_cached_result(data)

# Check circuit status
status = ollama_breaker.get_status()
print(f"Circuit state: {status['state']}")
print(f"Failure count: {status['failure_count']}/{status['threshold']}")
```

**Real-World Example:**

```python
# In services/openai_service.py
from services.error_handler import CircuitBreaker

class OpenAIService:
    def __init__(self):
        self.breaker = CircuitBreaker(
            name="openai",
            failure_threshold=10,
            recovery_timeout=120
        )

    async def generate_completion(self, prompt: str) -> str:
        try:
            return await self.breaker.call_async(
                self._call_openai,
                prompt
            )
        except HTTPException as e:
            # OpenAI is down - use fallback or local model
            return await self._fallback_generation(prompt)

    async def _call_openai(self, prompt: str) -> str:
        # Actual API call with retry logic
        async with httpx.AsyncClient() as client:
            response = await client.post(...)
            return response.json()["choices"][0]["text"]

    async def _fallback_generation(self, prompt: str) -> str:
        # Use Ollama as fallback
        return await ollama_client.generate(prompt)
```

## Error Context & Tracking

### ErrorContext Dataclass

Track errors with full context for debugging and monitoring:

```python
from services.error_handler import ErrorContext, ErrorCategory, log_error_context

context = ErrorContext(
    category=ErrorCategory.EXTERNAL_SERVICE,
    service="openai",
    operation="generate_text",
    attempt=1,
    max_attempts=3,
    request_id="req-12345",
    user_id="user-456",
    metadata={
        "model": "gpt-4",
        "tokens": 2048
    }
)

try:
    await openai_client.generate(prompt)
except Exception as e:
    context.error = e
    log_error_context(context)  # Logs to logger and Sentry
```

## Error Response Format

All API errors follow a standardized response format:

```json
{
  "error_code": "VALIDATION_ERROR",
  "message": "Invalid input parameter",
  "details": {
    "field": "topic",
    "constraint": "min_length=3",
    "value": "ab"
  },
  "request_id": "req-12345"
}
```

### Error Response Codes

| Status | Error Code          | Use Case                                |
| ------ | ------------------- | --------------------------------------- |
| 400    | VALIDATION_ERROR    | Input validation failed                 |
| 401    | UNAUTHORIZED        | Authentication required/failed          |
| 403    | FORBIDDEN           | User lacks permission                   |
| 404    | NOT_FOUND           | Resource doesn't exist                  |
| 409    | CONFLICT            | Resource already exists                 |
| 422    | STATE_ERROR         | Invalid state transition                |
| 500    | INTERNAL_ERROR      | Unhandled exception                     |
| 500    | DATABASE_ERROR      | Database operation failed               |
| 500    | SERVICE_ERROR       | Service operation failed                |
| 504    | TIMEOUT_ERROR       | Operation exceeded timeout              |
| 503    | SERVICE_UNAVAILABLE | External service down (circuit breaker) |

## Validation Helpers

### String Field Validation

```python
from services.error_handler import validate_string_field

# In your route handler
topic = validate_string_field(
    request.topic,
    field_name="topic",
    min_length=3,
    max_length=100
)

# Raises ValidationError if:
# - Not a string
# - Less than 3 characters
# - More than 100 characters
# - Is only whitespace
```

### Integer Field Validation

```python
from services.error_handler import validate_integer_field

num_results = validate_integer_field(
    request.limit,
    field_name="limit",
    min_value=1,
    max_value=100
)
```

### Enum Field Validation

```python
from services.error_handler import validate_enum_field
from enum import Enum

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"

status = validate_enum_field(
    request.status,
    field_name="status",
    enum_class=TaskStatus,
    case_insensitive=True  # "PENDING" matches "pending"
)
```

## Global Exception Handlers

The application has global exception handlers that catch all errors:

### Handled Exception Types

1. **AppError** - Application errors with error codes
2. **RequestValidationError** - Pydantic validation failures
3. **HTTPException** - FastAPI HTTP errors
4. **Exception** - All unhandled exceptions (logged + Sentry)

### Request ID Tracking

Every error response includes a unique `request_id` for tracing:

```python
# Client sends: Authorization header with token
# Response includes: X-Request-ID header

{
  "error_code": "UNAUTHORIZED",
  "message": "Invalid token",
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

Use this ID to correlate logs and Sentry events.

## Best Practices

### 1. Use Specific Exceptions

**❌ Don't:**

```python
raise Exception("Something went wrong")
```

**✅ Do:**

```python
raise ValidationError("Invalid topic", field="topic")
raise NotFoundError("Task not found", resource_type="Task", resource_id=task_id)
raise DatabaseError("Insert failed", cause=original_exception)
```

### 2. Include Context

**❌ Don't:**

```python
raise ServiceError("API call failed")
```

**✅ Do:**

```python
raise ServiceError(
    message="OpenAI API call failed",
    details={
        "service": "openai",
        "endpoint": "chat.completions",
        "status": response.status_code
    },
    cause=original_exception
)
```

### 3. Use Validation Helpers

**❌ Don't:**

```python
if not topic or len(topic) < 3:
    raise ValueError("Invalid topic")
```

**✅ Do:**

```python
topic = validate_string_field(topic, "topic", min_length=3)
```

### 4. Leverage Recovery Patterns

**❌ Don't:**

```python
for i in range(3):
    try:
        return await external_api.call()
    except Exception:
        if i < 2:
            await asyncio.sleep(2 ** i)
```

**✅ Do:**

```python
@retry_with_backoff(max_retries=3, initial_delay=1.0)
async def call_external_api():
    return await external_api.call()
```

### 5. Protect External Service Calls

**❌ Don't:**

```python
# Direct calls can cause cascading failures
for item in items:
    result = await slow_external_service.process(item)
```

**✅ Do:**

```python
# Use circuit breaker
for item in items:
    try:
        result = await service_breaker.call_async(
            slow_external_service.process,
            item
        )
    except HTTPException:
        # Circuit open - use cached result
        result = load_cached_result(item)
```

### 6. Log with Context

**❌ Don't:**

```python
logger.error("Task failed")
```

**✅ Do:**

```python
logger.error(
    "Task execution failed",
    extra={
        "request_id": request_id,
        "task_id": task_id,
        "service": "orchestrator"
    },
    exc_info=original_exception
)
```

## Integration Examples

### Example 1: Route Handler with Error Handling

```python
from fastapi import APIRouter, Depends
from services.error_handler import ValidationError, NotFoundError, StateError

router = APIRouter()

@router.post("/api/tasks/{task_id}/execute")
async def execute_task(
    task_id: str,
    request: ExecuteTaskRequest,
    db: DatabaseService = Depends(get_db)
):
    """Execute a task with comprehensive error handling"""

    # Validate input
    prompt = validate_string_field(
        request.prompt,
        field_name="prompt",
        min_length=1,
        max_length=5000
    )

    # Check resource exists
    task = await db.get_task(task_id)
    if not task:
        raise NotFoundError(
            "Task not found",
            resource_type="Task",
            resource_id=task_id
        )

    # Check state
    if task.status not in ["pending", "ready"]:
        raise StateError(
            message="Cannot execute task in current state",
            current_state=task.status,
            requested_action="execute"
        )

    # Execute with retry
    @retry_with_backoff(max_retries=3)
    async def run_task():
        return await orchestrator.execute(task_id, prompt)

    try:
        result = await run_task()
        return {"status": "success", "result": result}
    except TimeoutError:
        raise TimeoutError(
            "Task execution exceeded timeout",
            details={"task_id": task_id, "timeout": 300}
        )
```

### Example 2: Service with Circuit Breaker

```python
from services.error_handler import CircuitBreaker, retry_with_backoff

class ExternalAPIService:
    def __init__(self):
        self.breaker = CircuitBreaker(
            name="external_api",
            failure_threshold=5,
            recovery_timeout=60
        )

    @retry_with_backoff(max_retries=3, initial_delay=0.5)
    async def _api_call(self, endpoint: str, **kwargs):
        """Actual API call with retry logic"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.example.com/{endpoint}",
                json=kwargs,
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()

    async def fetch_data(self, **kwargs):
        """Fetch data with circuit breaker protection"""
        try:
            return await self.breaker.call_async(
                self._api_call,
                "fetch",
                **kwargs
            )
        except HTTPException as e:
            # Circuit open - use fallback
            logger.warning(f"Circuit breaker open: {e}")
            return load_fallback_data(**kwargs)
```

### Example 3: Database Operation Error Handling

```python
from services.error_handler import DatabaseError, ConflictError

async def create_task(task_data):
    """Create task with database error handling"""
    try:
        # Check if task already exists
        existing = await db.query(
            "SELECT id FROM tasks WHERE name = $1",
            task_data.name
        )

        if existing:
            raise ConflictError(
                "Task with this name already exists",
                details={"name": task_data.name, "existing_id": existing[0]["id"]}
            )

        # Insert new task
        result = await db.execute(
            "INSERT INTO tasks (name, description) VALUES ($1, $2) RETURNING id",
            task_data.name,
            task_data.description
        )

        return {"id": result[0]["id"], "status": "created"}

    except asyncpg.UniqueViolationError as e:
        raise ConflictError(
            "Unique constraint violation",
            cause=e,
            details={"constraint": "tasks_name_unique"}
        )
    except asyncpg.ForeignKeyViolationError as e:
        raise ValidationError(
            "Invalid foreign key reference",
            cause=e
        )
    except Exception as e:
        raise DatabaseError(
            "Database operation failed",
            cause=e,
            details={"operation": "insert", "table": "tasks"}
        )
```

## Monitoring & Debugging

### View Error Metrics

```python
# Get error statistics from monitoring system
errors = {
    "validation": 245,
    "not_found": 12,
    "timeout": 8,
    "service_error": 3
}
```

### Track Error Trends

All errors are automatically:

1. Logged to system logger with context
2. Sent to Sentry for monitoring
3. Included in request traces
4. Tagged with request ID for correlation

### Debug Circuit Breaker Status

```python
# Check service health
status = ollama_breaker.get_status()
if status["state"] == "open":
    print(f"Service down: {status['failure_count']} failures")
    print(f"Will retry at: {status['last_state_change']}")
```

## Summary

The error handling system provides:

✅ **Standardized error codes** for consistent client handling  
✅ **Domain-specific exceptions** for clear error semantics  
✅ **Automatic recovery** with retry and circuit breaker patterns  
✅ **Structured logging** with request correlation  
✅ **Sentry integration** for error tracking and monitoring  
✅ **Validation helpers** for input safety  
✅ **Global exception handlers** for universal coverage

Use these patterns to build robust, resilient services that gracefully handle failures.
