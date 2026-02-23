# API Validation Patterns & Standards

**Date:** February 22, 2026  
**Phase:** 1B - API Input Validation Consolidation  
**Status:** Complete

---

## Overview

This document defines the validation patterns and standards for all 29 REST API endpoints in the Glad Labs backend. The goal is to:

1. **Centralize validation logic** - Use Pydantic schemas for request validation
2. **Remove duplication** - Eliminate handler-level checks that duplicate Pydantic constraints
3. **Enable reuse** - Provide shared validators for common patterns
4. **Improve clarity** - Document validation rules per endpoint

---

## Validation Architecture

### Request Validation Flow

```
Incoming Request
      ↓
InputValidationMiddleware
  ├─ Body size limit (10MB)
  ├─ Content-Type validation
  ├─ Header security checks
  └─ URL pattern validation
      ↓
Pydantic Schema Validation
  ├─ Type checking
  ├─ Field constraints (min_length, max_length, etc.)
  ├─ Custom field_validators
  └─ Nested model validation
      ↓
Route Handler (Business Logic)
  └─ Optional: Complex validation requiring context
      ↓
Exception Handler Middleware
  └─ Convert validation errors to standard error responses
```

### Three Validation Layers

| Layer | Responsibility | Examples |
|-------|-----------------|----------|
| **Middleware** | Request-level security/limits | Body size, content-type, rate limiting |
| **Pydantic Schema** | Field-level constraints | Type, length, range, format, patterns |
| **Handler** | Business logic validation | Resource ownership, state transitions, conflicts |

---

## Pydantic Schema Validation (Preferred)

### When to Use

Use Pydantic schemas always. Let Pydantic handle all field constraints. Move business logic to handlers, not schemas.

### Field Validator Examples

**Required fields:**
```python
from pydantic import BaseModel, Field

class TaskRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=200)
    # ✅ Pydantic rejects: None, "", "ab" (too short), "x"*201 (too long)
    # ✅ No need for: if not request.topic or len(request.topic) < 3
```

**Email validation:**
```python
from pydantic import BaseModel, Field

class EmailRequest(BaseModel):
    email: str = Field(..., pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
    # ✅ Or use shared validator in services/shared_validators.py
```

**Pagination parameters:**
```python
from pydantic import BaseModel, Field

class ListRequest(BaseModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=1000)
    # ✅ Pydantic enforces: offset >= 0, 1 <= limit <= 1000
```

**Enums:**
```python
from enum import Enum
from pydantic import BaseModel, Field

class TaskType(str, Enum):
    BLOG_POST = "blog_post"
    SOCIAL = "social_media"

class TaskRequest(BaseModel):
    task_type: TaskType = Field(...)
    # ✅ Pydantic rejects invalid values (typos, unknown types)
```

**Lists with constraints:**
```python
from pydantic import BaseModel, Field
from typing import List

class PlatformRequest(BaseModel):
    platforms: List[str] = Field(..., min_items=1, max_items=6)
    # ✅ Pydantic rejects: [], ["a"] is OK, ["a", "b", ...] (7+) rejected
```

### Custom Field Validators

For complex validation, use `@field_validator` with `mode='before'` or `mode='after'`:

```python
from pydantic import BaseModel, Field, field_validator

class UserRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(...)
    
    @field_validator('name')
    @classmethod
    def name_no_special_chars(cls, v: str) -> str:
        if not v.isalnum() and not any(c in v for c in ['-', '_', ' ']):
            raise ValueError('Name can only contain letters, numbers, hyphens, underscores')
        return v.strip()
    
    @field_validator('email')
    @classmethod
    def validate_email(cls, v: str) -> str:
        # Import from shared_validators for reuse
        from services.shared_validators import validate_email
        return validate_email(v, field_name='email')
```

---

## Shared Validators

Location: `src/cofounder_agent/services/shared_validators.py`

Provides reusable validators for common patterns:

### String Validators

```python
from services.shared_validators import (
    validate_non_empty_string,
    validate_identifier,
    validate_slug,
)

# Non-empty string with length constraints
validate_non_empty_string(value, field_name='name', min_length=1, max_length=255)

# URL-safe identifier (alphanumeric + hyphens/underscores)
validate_identifier(value, field_name='id')

# Lowercase alphanumeric + hyphens (e.g., for slugs)
validate_slug(value, field_name='slug')
```

### Email & URL Validators

```python
from services.shared_validators import validate_email, validate_url

validate_email(value, field_name='email')
# Returns: lowercase email string
# Raises: ValidationError if invalid

validate_url(
    value,
    field_name='website',
    allowed_schemes={'http', 'https'},
    require_tld=True
)
# Returns: validated URL string
# Raises: ValidationError if invalid (bad scheme, no domain, etc.)
```

### Pagination Validators

```python
from services.shared_validators import validate_offset, validate_limit

offset = validate_offset(value, field_name='offset')
# Returns: int >= 0
# Raises: ValidationError if negative

limit = validate_limit(
    value,
    field_name='limit',
    min_limit=1,
    max_limit=1000,
    default_limit=10
)
# Returns: int in range [1, 1000]
# Raises: ValidationError if out of range
```

### Numeric Validators

```python
from services.shared_validators import (
    validate_positive_integer,
    validate_number_range,
)

validate_positive_integer(value, field_name='count', allow_zero=False)
validate_number_range(value, field_name='price', min_value=0.01, max_value=999.99)
```

### Date/Time Validators

```python
from services.shared_validators import (
    validate_iso_datetime,
    validate_date_range,
)

dt = validate_iso_datetime(value, field_name='timestamp')
# Returns: datetime object
# Accepts: ISO 8601 strings, datetime objects

start, end = validate_date_range(
    start_date,
    end_date,
    field_name='analysis_period'
)
# Returns: (start_datetime, end_datetime) where end >= start
# Raises: ValidationError if end < start
```

### Enum/Choice Validators

```python
from services.shared_validators import validate_choice

choice = validate_choice(
    value,
    field_name='status',
    choices=['active', 'inactive', 'pending']
)
# Returns: validated choice (one of the choices)
# Raises: ValidationError if not in choices
```

### List Validators

```python
from services.shared_validators import (
    validate_list_non_empty,
    validate_list_of_strings,
)

items = validate_list_non_empty(value, field_name='tags', min_items=1, max_items=10)
strings = validate_list_of_strings(value, field_name='platforms', min_items=1, max_items=6)
```

### Using in Pydantic Models

```python
from pydantic import BaseModel, Field, field_validator
from services.shared_validators import validate_email, validate_slug

class BlogPostRequest(BaseModel):
    title: str = Field(..., min_length=5, max_length=200)
    slug: str = Field(..., max_length=200)
    author_email: str = Field(...)
    tags: list = Field(default_factory=list, max_items=10)
    
    @field_validator('slug')
    @classmethod
    def validate_slug_field(cls, v):
        return validate_slug(v, field_name='slug')
    
    @field_validator('author_email')
    @classmethod
    def validate_email_field(cls, v):
        return validate_email(v, field_name='author_email')
```

---

## What NOT to Do in Handlers

❌ **DON'T** validate after Pydantic has already validated:

```python
# ❌ BAD - redundant (Pydantic already checked min_length)
@app.post("/tasks")
async def create_task(request: UnifiedTaskRequest):
    if not request.topic or not request.topic.strip():
        raise HTTPException(...)
    # Process...

# ✅ GOOD - let Pydantic handle, just process in handler
@app.post("/tasks")
async def create_task(request: UnifiedTaskRequest):
    # request.topic is guaranteed to have min_length=3
    # Pydantic already validated it
    logger.info(f"Creating task with topic: {request.topic}")
    # Process...
```

---

## Validation Error Responses

When validation fails, Pydantic automatically returns structured errors:

### Validation Error Response Format

```json
{
  "detail": [
    {
      "type": "string_type",
      "loc": ["body", "topic"],
      "msg": "Input should be a valid string [type=string_type]",
      "input": 123
    },
    {
      "type": "string_too_short",
      "loc": ["body", "topic"],
      "msg": "String should have at least 3 characters [type=string_too_short]",
      "input": "ab"
    }
  ]
}
```

### Exception Handler

The `exception_handlers` middleware automatically converts these to:

```json
{
  "status": "error",
  "error_code": "VALIDATION_ERROR",
  "message": "Request validation failed",
  "details": [
    {
      "field": "topic",
      "message": "String should have at least 3 characters",
      "code": "string_too_short"
    }
  ],
  "request_id": "req-12345",
  "timestamp": "2024-12-08T10:30:00Z"
}
```

---

## Validation By Route

### Core Task Routes (`task_routes.py`)

| Endpoint | Request Model | Key Fields | Validation |
|----------|---------------|-----------|-----------|
| POST /api/tasks | UnifiedTaskRequest | task_type, topic | Literal enum, min_length(3), max_length(200) |
| GET /api/tasks | PaginationParams | offset, limit | offset >= 0, 1 <= limit <= 100 |
| PUT /api/tasks/{id}/status | TaskStatusUpdateRequest | status | Literal enum |

**Changes Made:**
- ✅ Removed redundant `if not request.topic` check (line 229)
- ✅ Pydantic now fully validates topic field

### Social Media Routes (`social_routes.py`)

| Endpoint | Request Model | Key Fields | Validation |
|----------|---------------|-----------|-----------|
| POST /api/social/posts | SocialPost | content, platforms | min_length(10), min_items(1) |
| POST /api/social/generate | GenerateContentRequest | topic | min_length(3), max_length(200) |
| POST /api/social/cross-post | CrossPostRequest | content, platforms | min_length(10), min_items(2) |

**Changes Made:**
- ✅ Removed `if not request.content.strip()` check (line 130)
- ✅ Removed `if not request.platforms` check (line 132)
- ✅ Removed `if not request.topic.strip()` check (line 233)
- ✅ Removed `if not request.content.strip()` check (line 330)
- ✅ Pydantic now fully validates all fields

### Workflow Routes (`workflow_routes.py`)

| Endpoint | Request Model | Key Fields | Validation |
|----------|---------------|-----------|-----------|
| POST /api/workflows | WorkflowRequest | name, phases | min_length(1), Nested validation |
| GET /api/workflows | PaginationParams | offset, limit | Range validation |

**Status:** No redundant validation found - good pattern

### Settings Routes (`settings_routes.py`)

| Endpoint | Request Model | Key Fields | Validation |
|----------|---------------|-----------|-----------|
| POST /api/settings | SettingCreate | key, value | Required fields, max_length(255) |
| PUT /api/settings/{key} | SettingUpdate | value | Type validation per setting |

**Status:** No redundant validation found - good pattern

### Other Routes

**Privacy Routes:** Email validation uses HTTPException - should use shared validator  
**Writing Style Routes:** Complex file upload logic - validation is necessary (not redundant)  
**Command Queue Routes:** Error handling - not validation  
**Media Routes:** Configuration check - not validation

---

## Validation Best Practices Summary

| Principle | Pattern | Example |
|-----------|---------|---------|
| **Validate at schema level** | Pydantic Field constraints | `min_length`, `max_length`, `pattern`, `ge`/`le` |
| **Reuse validators** | shared_validators module | `validate_email()`, `validate_url()` |
| **Handle exceptions in middleware** | Exception handlers | RequestValidationError → 422 response |
| **Document constraints** | Field descriptions | `Field(..., description="Must be alphanumeric")` |
| **No handler-level duplication** | Trust Pydantic | If Pydantic validates, don't check again |
| **Business logic in handlers** | Route-specific validation | Resource ownership, state transitions |

---

## Testing Validation

### Unit Test Example

```python
import pytest
from pydantic import ValidationError
from schemas.task_schemas import UnifiedTaskRequest

def test_task_topic_validation():
    """Test topic field validation"""
    
    # ✅ Valid request
    req = UnifiedTaskRequest(task_type="blog_post", topic="Valid Topic Here")
    assert req.topic == "Valid Topic Here"
    
    # ❌ Too short
    with pytest.raises(ValidationError) as exc:
        UnifiedTaskRequest(task_type="blog_post", topic="ab")
    assert "string_too_short" in str(exc)
    
    # ❌ Too long
    with pytest.raises(ValidationError) as exc:
        UnifiedTaskRequest(task_type="blog_post", topic="x" * 201)
    assert "string_too_long" in str(exc)
    
    # ❌ Empty
    with pytest.raises(ValidationError) as exc:
        UnifiedTaskRequest(task_type="blog_post", topic="")
    assert "string_too_short" in str(exc)
```

### Integration Test Example

```python
def test_create_task_validation(client):
    """Test task creation endpoint validation"""
    
    # ✅ Valid request
    response = client.post("/api/tasks", json={
        "task_type": "blog_post",
        "topic": "Valid Topic"
    })
    assert response.status_code == 200
    
    # ❌ Missing required field
    response = client.post("/api/tasks", json={"task_type": "blog_post"})
    assert response.status_code == 422
    assert "detail" in response.json()
    
    # ❌ Invalid enum
    response = client.post("/api/tasks", json={
        "task_type": "invalid_type",
        "topic": "Topic"
    })
    assert response.status_code == 422
```

---

## Migration Status

### Completed ✅
- [x] Created shared_validators.py (600+ lines)
- [x] Removed redundant validation from task_routes.py
- [x] Removed redundant validation from social_routes.py (3 locations)
- [x] Documented validation patterns

### Not Required
- Writing style routes (file upload logic is context-dependent)
- Privacy routes (email validation in shared_validators available for future use)
- Most other routes (already follow pattern correctly)

### Optional Future Improvements
- [ ] Add custom field validators to models for business logic rules
- [ ] Add request-level validation for dependent fields
- [ ] Migrate email validation in privacy_routes to shared_validators
- [ ] Add comprehensive validation tests

---

## References

- **Shared Validators:** `src/cofounder_agent/services/shared_validators.py`
- **Pydantic Docs:** https://docs.pydantic.dev/latest/concepts/validators/
- **Error Handling:** `src/cofounder_agent/utils/exception_handlers.py`
- **Route Examples:** `src/cofounder_agent/routes/*.py`
