# Phase 1C Implementation - Tier 1 Complete Guide

This guide provides templates and patterns for implementing typed error handling in Tier 1 files. After these 3 files, the pattern becomes repeatable across all remaining 65 files.

## Tier 1 Files (Critical Path - ~110 replacements)

### 1. unified_orchestrator.py (47 generic exceptions)
### 2. database_service.py + delegate modules (39 generic exceptions)  
### 3. task_executor.py (31 generic exceptions)

## Error Type Reference

```python
from services.error_handler import (
    AppError, ErrorCode,
    ValidationError, NotFoundError, UnauthorizedError, ForbiddenError,
    ConflictError, StateError, DatabaseError, ServiceError, TimeoutError
)
```

## Implementation Pattern by Context

### Pattern 1: External API Calls (Most Common - 15% of exceptions)

**BEFORE:**
```python
async def call_external_api():
    try:
        response = await client.get("/api/endpoint")
        return response.json()
    except Exception as e:
        logger.error(f"API call failed: {e}")
        raise HTTPException(status_code=500)
```

**AFTER:**
```python
async def call_external_api():
    try:
        response = await client.get("/api/endpoint")
        return response.json()
    except asyncio.TimeoutError as e:
        raise TimeoutError(
            message="External API call timed out",
            details={"endpoint": "/api/endpoint", "timeout_seconds": 30},
            cause=e,
        )
    except HTTPStatusError as e:
        if 400 <= e.response.status_code < 500:
            raise ValidationError(
                message=f"Invalid request to API: {e.response.text}",
                field="request",
                cause=e,
            )
        else:
            raise ExternalAPIError(
                message="External API returned server error",
                details={"status": e.response.status_code},
                cause=e,
            )
    except (ClientError, RequestException) as e:
        raise ExternalAPIError(
            message="External API call failed",
            details={"error_type": type(e).__name__},
            cause=e,
        )
    except Exception as e:
        logger.error("Unexpected error in API call", exc_info=True)
        raise ServiceError(
            message="Unexpected error calling external API",
            cause=e,
        )
```

### Pattern 2: Database Operations (20% of exceptions)

**BEFORE:**
```python
async def create_user(user_data):
    try:
        result = await db.execute(
            "INSERT INTO users (email, name) VALUES ($1, $2)",
            user_data["email"], user_data["name"]
        )
        return {"id": result}
    except Exception as e:
        logger.error(f"Failed to create user: {e}")
        raise
```

**AFTER:**
```python
async def create_user(user_data):
    try:
        result = await db.execute(
            "INSERT INTO users (email, name) VALUES ($1, $2)",
            user_data["email"], user_data["name"]
        )
        return {"id": result}
    except asyncpg.UniqueViolationError as e:
        raise ConflictError(
            message="User with this email already exists",
            details={"field": "email", "value": user_data["email"]},
            cause=e,
        )
    except asyncpg.NotNullViolationError as e:
        raise ValidationError(
            message="Required field is missing",
            field=str(e).split("\"")[1] if "\"" in str(e) else "unknown",
            cause=e,
        )
    except asyncpg.PostgresError as e:
        logger.error("Database error creating user", exc_info=True)
        raise DatabaseError(
            message="Failed to create user in database",
            details={"operation": "INSERT", "table": "users"},
            cause=e,
        )
    except asyncio.TimeoutError as e:
        raise TimeoutError(
            message="Database operation timed out",
            details={"operation": "INSERT", "timeout_seconds": 30},
            cause=e,
        )
    except Exception as e:
        logger.error("Unexpected error creating user", exc_info=True)
        raise ServiceError(
            message="Unexpected error during user creation",
            cause=e,
        )
```

### Pattern 3: Agent/Service Orchestration (25% of exceptions)

**BEFORE:**
```python
async def execute_agent(agent_name, task):
    try:
        agent = self._get_agent(agent_name)
        result = await agent.execute(task)
        return result
    except Exception as e:
        logger.error(f"Agent execution failed: {e}")
        return {"status": "failed", "error": str(e)}
```

**AFTER:**
```python
async def execute_agent(agent_name, task):
    try:
        agent = self._get_agent(agent_name)
        result = await agent.execute(task)
        return result
    except asyncio.TimeoutError as e:
        raise TimeoutError(
            message=f"Agent '{agent_name}' execution timed out",
            details={"agent": agent_name, "timeout_seconds": 300},
            cause=e,
        )
    except ValueError as e:
        raise ValidationError(
            message=f"Invalid task for agent '{agent_name}': {str(e)}",
            field="task_parameters",
            cause=e,
        )
    except KeyError as e:
        raise NotFoundError(
            message=f"Agent '{agent_name}' not found",
            resource_type="agent",
            resource_id=agent_name,
            cause=e,
        )
    except Exception as e:
        logger.error(f"Unexpected error in agent execution", exc_info=True)
        raise ServiceError(
            message="Agent execution failed unexpectedly",
            details={"agent": agent_name, "error_type": type(e).__name__},
            cause=e,
        )
```

### Pattern 4: State Transitions (15% of exceptions)

**BEFORE:**
```python
async def transition_task_state(task_id, new_state):
    try:
        task = await db.get_task(task_id)
        if not task:
            raise ValueError("Task not found")
        task.state = new_state
        await db.update_task(task)
    except Exception as e:
        logger.error(f"State transition failed: {e}")
        raise
```

**AFTER:**
```python
async def transition_task_state(task_id, new_state):
    try:
        task = await db.get_task(task_id)
        if not task:
            raise NotFoundError(
                message="Task not found",
                resource_type="task",
                resource_id=task_id,
            )
        
        # Validate state transition
        if not self._is_valid_transition(task.state, new_state):
            raise StateError(
                message=f"Cannot transition from {task.state} to {new_state}",
                current_state=task.state,
                requested_action=f"transition_to_{new_state}",
            )
        
        task.state = new_state
        await db.update_task(task)
    except (NotFoundError, StateError):
        raise  # Already properly typed
    except asyncpg.PostgresError as e:
        raise DatabaseError(
            message="Failed to update task state",
            details={"task_id": task_id, "new_state": new_state},
            cause=e,
        )
    except Exception as e:
        logger.error("Unexpected error in state transition", exc_info=True)
        raise ServiceError(
            message="State transition failed unexpectedly",
            cause=e,
        )
```

### Pattern 5: Request Validation (10% of exceptions)

**BEFORE:**
```python
def validate_request(request):
    try:
        # Pydantic already validates, but some custom rules
        if len(request.content) < 10:
            raise ValueError("Content too short")
        return True
    except Exception as e:
        logger.error(f"Validation failed: {e}")
        raise HTTPException(status_code=400)
```

**AFTER:**
```python
def validate_request(request):
    try:
        # Pydantic already validates basic constraints
        # This function handles business logic validation
        if len(request.content) < 10:
            raise ValidationError(
                message="Content must be at least 10 characters",
                field="content",
                constraint="min_length=10",
            )
        
        if not self._is_valid_slug(request.slug):
            raise ValidationError(
                message="Slug contains invalid characters",
                field="slug",
                value=request.slug,
            )
        
        return True
    except ValidationError:
        raise  # Already properly typed
    except Exception as e:
        logger.error("Unexpected validation error", exc_info=True)
        raise ServiceError(
            message="Validation failed unexpectedly",
            cause=e,
        )
```

### Pattern 6: Authentication/Authorization (10% of exceptions)

**BEFORE:**
```python
async def verify_permission(user, resource):
    try:
        if not user:
            raise ValueError("User not authenticated")
        if user.id != resource.owner_id:
            raise ValueError("User not authorized")
        return True
    except Exception as e:
        logger.error(f"Permission check failed: {e}")
        raise
```

**AFTER:**
```python
async def verify_permission(user, resource):
    try:
        if not user:
            raise UnauthorizedError(
                message="User authentication required",
                error_code=ErrorCode.UNAUTHORIZED,
            )
        if user.id != resource.owner_id:
            raise ForbiddenError(
                message="User not authorized to access this resource",
                error_code=ErrorCode.PERMISSION_DENIED,
                details={
                    "user_id": user.id,
                    "resource_id": resource.id,
                    "owner_id": resource.owner_id,
                },
            )
        return True
    except (UnauthorizedError, ForbiddenError):
        raise  # Already properly typed
    except Exception as e:
        logger.error("Unexpected error in permission check", exc_info=True)
        raise ServiceError(
            message="Permission check failed unexpectedly",
            cause=e,
        )
```

## Implementation Checklist for Tier 1

### unified_orchestrator.py (47 exceptions × 3 steps = 141 minutes)
- [ ] Line 269: Registry lookup fallback → OrchestratorError
- [ ] Line 381: process_request top-level → ServiceError  
- [ ] Line 687-752: Content creation workflow → Varies by operation (DatabaseError, TimeoutError, OrchestratorError)
- [ ] Continue for all 47 occurrences...

### database_service.py + delegates (39 exceptions × 2 steps = 78 minutes)
- [ ] Users DB: OAuth failures → UnauthorizedError
- [ ] Tasks DB: NOT FOUND queries → NotFoundError
- [ ] Tasks DB: Unique violations → ConflictError
- [ ] Content DB: Parse errors → ValidationError
- [ ] Admin DB: Logging errors → Ignored (safe to skip)
- [ ] Continue for all 39 occurrences...

### task_executor.py (31 exceptions × 2 steps = 62 minutes)
- [ ] Task execution → ServiceError or specific error
- [ ] Content pipeline hooks → OrchestratorError
- [ ] Database updates → DatabaseError
- [ ] Continue for all 31 occurrences...

## Quick Migration Template

For any generic `except Exception as e:` clause, follow this template:

```python
# 1. Identify context (database? API? state? auth?)
# 2. Determine appropriate exception type
# 3. Extract relevant details for error context
# 4. Apply template:

except ExceptionType as e:
    raise AppropriateError(
        message="User-friendly error message",
        details={"context_key": context_value},  # Optional
        cause=e,  # Always preserve original exception
    )

# 5. Keep a catch-all at the end:
except Exception as e:
    logger.error("Unexpected error", exc_info=True)
    raise ServiceError(
        message="An unexpected error occurred",
        cause=e,
    )
```

## Success Criteria for Tier 1

- [ ] All 47 exceptions in unified_orchestrator.py replaced with typed exceptions
- [ ] All 39 exceptions in database_service.py + delegates replaced
- [ ] All 31 exceptions in task_executor.py replaced
- [ ] Tests run without errors
- [ ] Error responses include proper error_codes
- [ ] All exceptions have message, details (where applicable), and cause preserved
- [ ] No bare `except:` clauses remain
- [ ] Request ID propagation works in all new error paths

## Next Steps After Tier 1

After Tier 1 is complete (3.5 hours), apply the same patterns to:
- Tier 2: Content pipeline (5 files, 84 exceptions, 2.5 hours)
- Tier 3: Supporting services (15 files, 30-40 exceptions, 1.5 hours)
- Tier 4: Edge cases (15 files, 20-30 exceptions, 0.5 hours)

Total time: 8.5 hours (Tiers 1-4 both aligned with original estimate)
