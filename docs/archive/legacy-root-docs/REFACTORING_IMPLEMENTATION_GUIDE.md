# 🔧 REFACTORING IMPLEMENTATION GUIDE

**Detailed Code Examples & Implementation Steps**

---

## PHASE 1: DATABASE LAYER CONSOLIDATION

### Step 1: Make cms_routes.py Fully Async

**Current (Broken - Blocks Event Loop):**

```python
# src/cofounder_agent/routes/cms_routes.py (lines 20-80)
@router.get("/api/posts")
def list_posts(skip: int = Query(0), limit: int = Query(20)):
    """List posts - BLOCKING I/O"""
    conn = psycopg2.connect(DB_URL)  # ❌ Blocking!
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM posts OFFSET %s LIMIT %s", (skip, limit))
    rows = cur.fetchall()
    return {"data": rows}
```

**Fixed (Pure Async):**

```python
# src/cofounder_agent/routes/cms_routes.py
from services.database_service import DatabaseService

# Get singleton instance
_db_service: Optional[DatabaseService] = None

def get_db_service() -> DatabaseService:
    global _db_service
    if _db_service is None:
        _db_service = DatabaseService()
    return _db_service

@router.get("/api/posts")
async def list_posts(
    skip: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=100),
    published_only: bool = Query(True)
):
    """List posts - ASYNC NON-BLOCKING"""
    db = get_db_service()
    posts, total = await db.list_posts(
        skip=skip,
        limit=limit,
        published_only=published_only
    )
    return {
        "data": posts,
        "meta": {"total": total, "skip": skip, "limit": limit}
    }
```

**Changes Required:**

1. Remove all `psycopg2.connect()` calls
2. Add `async def` to all route handlers
3. Use `await database_service.*()` calls
4. Update tests to handle async

**Testing:**

```bash
# Test before: Should work but slow
curl http://localhost:8000/api/posts

# Test after: Should work and fast
curl http://localhost:8000/api/posts?limit=50
```

---

### Step 2: Extend DatabaseService to Cover cms_routes Operations

**Add to `src/cofounder_agent/services/database_service.py`:**

```python
# Add these methods to DatabaseService class around line 800

async def list_posts(
    self,
    skip: int = 0,
    limit: int = 20,
    published_only: bool = True,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    List blog posts with pagination.

    Returns:
        Tuple[posts, total_count]
    """
    async with self.pool.acquire() as conn:
        # Get total count
        if published_only:
            total_row = await conn.fetchrow(
                "SELECT COUNT(*) as total FROM posts WHERE published_at IS NOT NULL"
            )
        else:
            total_row = await conn.fetchrow("SELECT COUNT(*) as total FROM posts")

        total = total_row["total"] if total_row else 0

        # Get posts
        query = """
            SELECT id, title, slug, excerpt, featured_image_url,
                   category_id, published_at, created_at, updated_at
            FROM posts
        """
        if published_only:
            query += " WHERE published_at IS NOT NULL"

        query += " ORDER BY published_at DESC NULLS LAST OFFSET $1 LIMIT $2"

        rows = await conn.fetch(query, skip, limit)
        posts = [dict(row) for row in rows]

        return posts, total

async def get_post_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
    """Get post by slug (URL-friendly identifier)"""
    async with self.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM posts WHERE slug = $1", slug)
        return dict(row) if row else None

async def create_post(
    self,
    title: str,
    slug: str,
    content: str,
    category_id: Optional[str] = None,
    featured_image_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Create new blog post"""
    async with self.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO posts (title, slug, content, category_id, featured_image_url, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
            RETURNING *
            """,
            title, slug, content, category_id, featured_image_url
        )
        return dict(row) if row else {}
```

**Testing:**

```python
# In tests - verify async works
async def test_list_posts():
    db = DatabaseService()
    await db.initialize()
    posts, total = await db.list_posts(limit=10)
    assert isinstance(posts, list)
    assert isinstance(total, int)
    await db.close()
```

---

### Step 3: Delete Duplicate Task Store Files

**Files to DELETE:**

```bash
# These are no longer needed after consolidation
rm src/cofounder_agent/services/async_task_store.py
rm src/cofounder_agent/services/content_orchestrator.py  # If covered by others

# Check with git first
git ls-files | grep -E "(async_task_store|content_orchestrator)"
```

**Consolidate task_store_service.py:**

- Move critical methods to `database_service.py`
- Keep only if providing different interface (check carefully)
- Otherwise delete and update imports

---

## PHASE 2: CODE QUALITY - ERROR HANDLING

### Step 1: Create Centralized Error Handler

**New File: `src/cofounder_agent/services/error_handler.py`**

```python
"""Centralized error handling for the application"""

from enum import Enum
from typing import Optional, Dict, Any
from fastapi.responses import JSONResponse
from fastapi import Request

class ErrorCode(str, Enum):
    """Application error codes"""
    # 4xx - Client errors
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    RATE_LIMIT = "RATE_LIMIT"

    # 5xx - Server errors
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    DATABASE_ERROR = "DATABASE_ERROR"
    EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"


class AppError(Exception):
    """Base application error with HTTP status code"""

    def __init__(
        self,
        status_code: int,
        message: str,
        error_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        details: Optional[Dict[str, Any]] = None
    ):
        self.status_code = status_code
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON response dict"""
        return {
            "error": {
                "code": self.error_code.value,
                "message": self.message,
                "details": self.details
            }
        }


class ValidationError(AppError):
    """Invalid input data"""
    def __init__(self, message: str, details: Optional[Dict] = None):
        super().__init__(
            status_code=422,
            message=message,
            error_code=ErrorCode.VALIDATION_ERROR,
            details=details
        )


class NotFoundError(AppError):
    """Resource not found"""
    def __init__(self, resource: str, resource_id: Optional[str] = None):
        message = f"{resource} not found"
        if resource_id:
            message += f" (id: {resource_id})"
        super().__init__(
            status_code=404,
            message=message,
            error_code=ErrorCode.NOT_FOUND
        )


class UnauthorizedError(AppError):
    """Authentication required or failed"""
    def __init__(self, message: str = "Authentication required"):
        super().__init__(
            status_code=401,
            message=message,
            error_code=ErrorCode.UNAUTHORIZED
        )


class ForbiddenError(AppError):
    """User lacks permission"""
    def __init__(self, message: str = "Permission denied"):
        super().__init__(
            status_code=403,
            message=message,
            error_code=ErrorCode.FORBIDDEN
        )


class ConflictError(AppError):
    """Resource already exists or conflict"""
    def __init__(self, message: str):
        super().__init__(
            status_code=409,
            message=message,
            error_code=ErrorCode.CONFLICT
        )


class DatabaseError(AppError):
    """Database operation failed"""
    def __init__(self, message: str, details: Optional[Dict] = None):
        super().__init__(
            status_code=500,
            message=f"Database error: {message}",
            error_code=ErrorCode.DATABASE_ERROR,
            details=details
        )


class ExternalServiceError(AppError):
    """External service (API, LLM, etc.) failed"""
    def __init__(self, service: str, message: str):
        super().__init__(
            status_code=503,
            message=f"{service} service error: {message}",
            error_code=ErrorCode.EXTERNAL_SERVICE_ERROR
        )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Global error handler for AppError exceptions"""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict()
    )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Fallback handler for unexpected errors"""
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": "An unexpected error occurred",
                "details": {"type": exc.__class__.__name__}
            }
        }
    )
```

### Step 2: Register Error Handlers in main.py

**Update `src/cofounder_agent/main.py` around line 350:**

```python
from services.error_handler import AppError, app_error_handler, generic_error_handler

# ... after FastAPI app creation ...

app = FastAPI(...)

# Register error handlers
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, generic_error_handler)
```

### Step 3: Update Routes to Use AppError

**Example: Update `routes/chat_routes.py`**

```python
# BEFORE (inconsistent error handling)
@router.post("/api/chat")
async def chat(request: ChatRequest) -> ChatResponse:
    if not request.message:
        raise HTTPException(status_code=400, detail="Message required")

    try:
        response = await process_chat(request.message)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return {"error": str(e)}  # ❌ No status code

# AFTER (consistent error handling)
from services.error_handler import ValidationError, AppError

@router.post("/api/chat")
async def chat(request: ChatRequest) -> ChatResponse:
    if not request.message:
        raise ValidationError("Message is required")

    try:
        response = await process_chat(request.message)
        return ChatResponse(message=response)
    except ValueError as e:
        raise ValidationError(str(e))
    except Exception as e:
        logger.error(f"Unexpected chat error: {e}", exc_info=True)
        raise AppError(status_code=500, message="Chat processing failed")
```

---

## PHASE 2: INPUT VALIDATION

### Add Validation to cms_routes.py

**Before:**

```python
@router.get("/api/posts")
def list_posts(skip: int = Query(0), limit: int = Query(20)):
    # No validation! Could request skip=-1000, limit=999999
```

**After:**

```python
@router.get("/api/posts")
async def list_posts(
    skip: int = Query(0, ge=0, le=10000, description="Number of posts to skip"),
    limit: int = Query(20, ge=1, le=100, description="Number of posts to return"),
    published_only: bool = Query(True, description="Only show published posts"),
):
    """List blog posts with pagination"""
    db = get_db_service()
    posts, total = await db.list_posts(skip=skip, limit=limit, published_only=published_only)
    return {"data": posts, "meta": {"total": total, "skip": skip, "limit": limit}}
```

**Pydantic Model for POST endpoints:**

```python
from pydantic import BaseModel, Field

class CreatePostRequest(BaseModel):
    title: str = Field(..., min_length=5, max_length=200, description="Post title")
    slug: str = Field(..., min_length=3, max_length=100, regex="^[a-z0-9-]+$", description="URL slug")
    content: str = Field(..., min_length=100, description="Post content")
    category_id: Optional[str] = Field(None, description="Category ID")

    class Config:
        example = {
            "title": "My First Post",
            "slug": "my-first-post",
            "content": "Lorem ipsum...",
            "category_id": "cat-123"
        }

@router.post("/api/posts")
async def create_post(request: CreatePostRequest):
    """Create new post - Pydantic validates input"""
    db = get_db_service()
    post = await db.create_post(
        title=request.title,
        slug=request.slug,
        content=request.content,
        category_id=request.category_id
    )
    return post
```

---

## PHASE 2: CONFIGURATION MODULE

**New File: `src/cofounder_agent/services/config.py`**

```python
"""
Centralized configuration using Pydantic Settings.

Single source of truth for all environment variables.
Validates configuration on startup.
"""

from pydantic_settings import BaseSettings
from pydantic import Field, PostgresDsn
from typing import Optional
import os

class Settings(BaseSettings):
    """Application settings loaded from environment"""

    # Database
    DATABASE_URL: str = Field(
        ...,
        description="PostgreSQL connection string"
    )
    DATABASE_POOL_MIN_SIZE: int = Field(10, description="Min connection pool size")
    DATABASE_POOL_MAX_SIZE: int = Field(20, description="Max connection pool size")

    # API
    API_BASE_URL: str = Field(
        "http://localhost:8000",
        description="Base URL for API"
    )
    API_PORT: int = Field(8000, description="API port")
    API_HOST: str = Field("0.0.0.0", description="API host")

    # Environment
    ENVIRONMENT: str = Field("development", description="Environment (development/staging/production)")
    DEBUG: bool = Field(False, description="Enable debug mode")
    LOG_LEVEL: str = Field("INFO", description="Logging level")

    # AI Models
    OPENAI_API_KEY: Optional[str] = Field(None, description="OpenAI API key")
    ANTHROPIC_API_KEY: Optional[str] = Field(None, description="Anthropic API key")
    GEMINI_API_KEY: Optional[str] = Field(None, description="Google Gemini API key")
    HUGGINGFACE_API_TOKEN: Optional[str] = Field(None, description="HuggingFace token")

    # Ollama
    OLLAMA_BASE_URL: str = Field("http://localhost:11434", description="Ollama base URL")
    OLLAMA_MODEL: str = Field("mistral", description="Default Ollama model")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

# Global singleton
_settings: Optional[Settings] = None

def get_settings() -> Settings:
    """Get settings singleton"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

# Usage throughout codebase:
# from services.config import get_settings
# settings = get_settings()
# print(settings.DATABASE_URL)
```

**Update main.py to use config:**

```python
from services.config import get_settings

settings = get_settings()

logger.info(f"Environment: {settings.ENVIRONMENT}")
logger.info(f"Database: {settings.DATABASE_URL[:50]}...")
logger.info(f"Log Level: {settings.LOG_LEVEL}")
logger.info(f"Ollama: {settings.OLLAMA_BASE_URL}")
```

---

## CONSOLIDATION CHECKLIST

### Files to Delete (After Migration)

```bash
# Old route implementations
rm src/cofounder_agent/routes/content.py
rm src/cofounder_agent/routes/content_generation.py
rm src/cofounder_agent/routes/enhanced_content.py
rm src/cofounder_agent/routes/auth_routes_old_sqlalchemy.py

# Old service implementations
rm src/cofounder_agent/services/async_task_store.py
rm -f src/cofounder_agent/services/content_orchestrator.py  # If fully migrated

# Old test files
rm src/cofounder_agent/tests/test_content_generation.py  # If replaced
rm src/cofounder_agent/tests/firestore_client.py  # Firestore removed
```

### Imports to Update

```bash
# Find all files importing from deleted modules
grep -r "from routes import content" src/cofounder_agent/
grep -r "from services.async_task_store" src/cofounder_agent/
grep -r "from routes.content_generation" src/cofounder_agent/

# Update to:
# from routes.content_routes import ...  # Single source
# from services.database_service import ...  # Single source
```

### Update main.py Route Registration

```python
# BEFORE (importing multiple overlapping routes)
from routes.content import content_router as content_router_old
from routes.content_generation import content_gen_router
from routes.enhanced_content import enhanced_router
from routes.content_routes import content_router

# AFTER (single import)
from routes.content_routes import content_router
```

---

## TESTING MIGRATION

### Test Old Routes (Before Delete)

```bash
# Verify old routes work
curl http://localhost:8000/api/content/create-blog-post
curl http://localhost:8000/api/content/blog-posts

# Then check new consolidated route works
curl http://localhost:8000/api/content/tasks
```

### Test New Error Handler

```python
# Test validation error
curl -X POST http://localhost:8000/api/posts \
  -H "Content-Type: application/json" \
  -d '{"title": "x"}'  # Too short, should trigger ValidationError

# Expect response:
# {"error": {"code": "VALIDATION_ERROR", "message": "...", "details": {}}}
```

### Test Async Routes

```bash
# Load test - verify non-blocking
ab -n 100 -c 10 http://localhost:8000/api/posts

# Should handle 10 concurrent requests without blocking
```

---

## MIGRATION SUMMARY

| Phase     | Files Changed | Files Deleted | New Files | Test Time    |
| --------- | ------------- | ------------- | --------- | ------------ |
| Phase 1   | 3             | 5             | 0         | 30 min       |
| Phase 2   | 10            | 2             | 2         | 45 min       |
| Phase 3   | 5             | 0             | 3         | 1 hour       |
| **Total** | **18**        | **7**         | **5**     | **~2 hours** |

---

**Implementation Guide Complete** ✅
