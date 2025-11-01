# Backend Startup Fix - Async/Sync SQLAlchemy Mismatch

## Problem Identified

**Error:** `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called`

**Root Cause:**
Your code is using **synchronous SQLAlchemy** (`db.query()`) inside **async FastAPI endpoints** with an **async database driver** (`asyncpg`).

**Location:** `src/cofounder_agent/routes/auth_routes.py:223`

```python
user = db.query(User).filter_by(id=user_id).first()  # ❌ SYNC call in async context
```

**Why it fails:**

- FastAPI with uvicorn is async
- Endpoints are defined as `async def`
- `asyncpg` is an async driver
- SQLAlchemy needs to be configured as async too
- OR the database calls need to be moved to a background thread

---

## Quick Fix Options

### Option 1: Use Async SQLAlchemy (Recommended for Now)

**File:** `src/cofounder_agent/routes/auth_routes.py:223`

Change from:

```python
user = db.query(User).filter_by(id=user_id).first()
```

To:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# In the function signature, inject the async session:
async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    # ...
    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalar_one_or_none()
```

**Status:** Quick fix but doesn't solve the underlying complexity issue

---

### Option 2: Move to Background Thread (Thread-Safe)

```python
import asyncio
from functools import partial

async def get_current_user(request: Request, db=Depends(get_db)):
    user_id = claims.get("user_id")

    # Run sync query in background thread
    loop = asyncio.get_event_loop()
    user = await loop.run_in_executor(None, partial(
        lambda: db.query(User).filter_by(id=user_id).first()
    ))

    return user
```

**Status:** Works but is slower (thread overhead)

---

### Option 3: Remove SQLAlchemy Entirely (What You Actually Want)

This is what the architecture review recommends.

Use raw `asyncpg` queries instead:

```python
from src.cofounder_agent.services.database_service import database_service

async def get_current_user(request: Request):
    token = extract_bearer_token(request)
    is_valid, claims = validate_access_token(token)

    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = claims.get("user_id")

    # Raw asyncpg query - no SQLAlchemy
    user = await database_service.get_user_by_id(user_id)

    if not user:
        # Mock development user
        user = {
            "id": user_id,
            "email": claims.get("email", "dev@example.com"),
            "created_at": datetime.utcnow().isoformat(),
        }

    return user
```

**DatabaseService method:**

```python
async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
    async with self.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        return dict(row) if row else None
```

---

## Immediate Temporary Fix

To get the backend running **TODAY** while you decide on architecture:

**File:** `src/cofounder_agent/routes/auth_routes.py`

Find line ~223 that says:

```python
user = db.query(User).filter_by(id=user_id).first()
```

Replace with:

```python
# Temporary: Skip database check for development
user = None  # This will trigger the mock user creation below
```

This will:
✅ Let the backend start
✅ Let you develop
✅ Mock users will work
❌ But won't persist user data

**Then decide:** Do you want to:

1. Fix async/sync SQLAlchemy properly (keep current architecture)
2. Move to raw asyncpg (remove SQLAlchemy - recommended)

---

## Long-Term Fix: Remove SQLAlchemy

Based on your architecture review, here's the 2-day plan:

### Day 1: Create asyncpg DatabaseService

**File:** `src/cofounder_agent/services/database_service_asyncpg.py`

```python
import asyncpg
from typing import Optional, Dict, Any

class DatabaseServiceAsyncpg:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool = None

    async def connect(self):
        """Initialize connection pool"""
        self.pool = await asyncpg.create_pool(self.database_url)

    async def disconnect(self):
        """Close connection pool"""
        await self.pool.close()

    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
            return dict(row) if row else None

    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user by email"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
            return dict(row) if row else None

    async def create_task(self, title: str, description: str) -> Dict[str, Any]:
        """Create new task"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tasks (title, description, status, created_at)
                VALUES ($1, $2, $3, NOW())
                RETURNING *
                """,
                title, description, "pending"
            )
            return dict(row)

    async def get_tasks(self) -> list[Dict[str, Any]]:
        """Get all pending tasks"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM tasks WHERE status = $1", "pending")
            return [dict(row) for row in rows]
```

### Day 2: Update Routes

**File:** `src/cofounder_agent/routes/auth_routes.py:223`

```python
from src.cofounder_agent.services.database_service_asyncpg import database_service

async def get_current_user(request: Request):
    # ... token validation ...
    user = await database_service.get_user_by_id(user_id)
    # ... rest of logic ...
```

---

## Decision Time

**Which approach do you want?**

1. **Quick fix** (1 hour): Comment out the database check, mock users work, database not used
   - ✅ Backend runs today
   - ✅ Development unblocked
   - ❌ No persistence

2. **Proper fix** (2-3 hours): Fix async SQLAlchemy configuration
   - ✅ Backend runs
   - ✅ Database persistence works
   - ❌ Complexity remains (for future refactoring)

3. **Architecture fix** (2-3 days): Remove SQLAlchemy entirely
   - ✅ Backend runs
   - ✅ Database persistence works
   - ✅ Simpler code (50% less)
   - ✅ Easier to maintain

**Recommendation:**
→ Do the quick fix today
→ Get backend + frontend talking again
→ Do the architecture refactor this week when time permits

Let me know which you want and I'll implement it!
