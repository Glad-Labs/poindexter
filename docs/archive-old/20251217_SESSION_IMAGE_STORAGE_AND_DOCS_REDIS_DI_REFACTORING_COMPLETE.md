# Redis Caching System Refactoring - Singleton to Dependency Injection

## ✅ COMPLETED: Conversion from Singleton to Dependency Injection

### Summary

Successfully refactored the Redis caching system from a singleton pattern to dependency injection throughout the entire codebase.

---

## 📋 Changes Made

### 1. ✅ `src/cofounder_agent/services/redis_cache.py`

**Changes:**

- Removed ALL `@classmethod` decorators and class variables (`_instance`, `_initialized`, `_enabled`)
- Converted all methods to instance methods (removed `cls` parameter)
- Added `__init__(self, redis_instance, enabled)` method for instance initialization
- Added `@classmethod create()` factory method that:
  - Reads `REDIS_URL` and `REDIS_ENABLED` from environment
  - Creates aioredis connection
  - Returns `RedisCache` instance
- Updated all method calls from `RedisCache.method()` to `self.method()`
- Updated `setup_redis_cache()` to use factory pattern (kept for backward compatibility)
- Updated `@cached` decorator to work with dependency-injected redis_cache

**Key Methods (Now Instance Methods):**

- `get(key)` → `redis_cache.get(key)`
- `set(key, value, ttl)` → `redis_cache.set(key, value, ttl)`
- `delete(key)` → `redis_cache.delete(key)`
- `delete_pattern(pattern)` → `redis_cache.delete_pattern(pattern)`
- `exists(key)` → `redis_cache.exists(key)`
- `get_or_set(key, fetch_fn, ttl)` → `redis_cache.get_or_set(key, fetch_fn, ttl)`
- `incr(key, amount)` → `redis_cache.incr(key, amount)`
- `health_check()` → `redis_cache.health_check()`
- `clear_all()` → `redis_cache.clear_all()`
- `close()` → `redis_cache.close()`

---

### 2. ✅ `src/cofounder_agent/services/ai_cache.py`

**Changes:**

- Updated `AIResponseCache.__init__` to accept `redis_cache` parameter
- Changed `RedisCache.get()` calls to `self.redis_cache.get()`
- Changed `RedisCache.set()` calls to `self.redis_cache.set()`
- Store redis_cache as `self.redis_cache` instance variable
- Updated `ImageCache.__init__` to accept `redis_cache` parameter
- Updated `initialize_ai_cache()` to accept `redis_cache` parameter
- Removed singleton pattern from AI cache

**Usage Example:**

```python
# Old (singleton):
ai_cache = AIResponseCache(ttl_hours=24)
await ai_cache.get(prompt, model, params)

# New (dependency injection):
redis_cache = await RedisCache.create()
ai_cache = AIResponseCache(redis_cache=redis_cache, ttl_hours=24)
await ai_cache.get(prompt, model, params)
```

---

### 3. ✅ `src/cofounder_agent/utils/startup_manager.py`

**Changes:**

- Added `self.redis_cache` to StartupManager constructor
- Updated `_setup_redis_cache()` to create RedisCache instance using factory:
  ```python
  self.redis_cache = await RedisCache.create()
  ```
- Added redis_cache cleanup in `shutdown()` method
- Updated `initialize_all_services()` return dict to include `'redis_cache'`
- Updated `_log_startup_summary()` to log redis_cache status

---

### 4. ✅ `src/cofounder_agent/main.py`

**Changes:**

- Updated lifespan manager to inject redis_cache into app.state:
  ```python
  app.state.redis_cache = services['redis_cache']
  ```
- RedisCache now available in all routes via `request.app.state.redis_cache`

---

## 📝 Dependency Injection Pattern

### How to Use in Routes/Services

**Pattern 1: From Request object**

```python
from fastapi import Request, APIRouter

@router.get("/api/items")
async def get_items(request: Request):
    redis_cache = request.app.state.redis_cache

    # Use caching
    items = await redis_cache.get_or_set(
        key="items:all",
        fetch_fn=lambda: fetch_from_db(),
        ttl=3600
    )
    return items
```

**Pattern 2: Dependency Injection via FastAPI Depends**

```python
from fastapi import Depends, APIRouter
from typing import Optional

def get_redis_cache(request: Request):
    return request.app.state.redis_cache

@router.get("/api/users/{user_id}")
async def get_user(user_id: str, redis_cache = Depends(get_redis_cache)):
    user = await redis_cache.get(f"user:{user_id}")
    if not user:
        user = await db.fetch_user(user_id)
        await redis_cache.set(f"user:{user_id}", user, ttl=1800)
    return user
```

**Pattern 3: Service Injection**

```python
class UserService:
    def __init__(self, redis_cache: Optional[RedisCache] = None):
        self.redis_cache = redis_cache

    async def get_user(self, user_id: str):
        if self.redis_cache:
            cached = await self.redis_cache.get(f"user:{user_id}")
            if cached:
                return cached

        user = await self.fetch_from_db(user_id)

        if self.redis_cache:
            await self.redis_cache.set(f"user:{user_id}", user, ttl=1800)

        return user
```

---

## ✅ Validation Checklist

- [x] All RedisCache calls are now instance calls (no more class methods except `create()`)
- [x] All uses of redis_cache come from dependency injection (app.state or parameter)
- [x] No more `@classmethod` decorators on RedisCache (except `create()` factory)
- [x] No more `cls._instance` singleton pattern
- [x] All files import RedisCache but instantiate via `await RedisCache.create()`
- [x] redis_cache properly stored in app.state for access in routes
- [x] redis_cache connection properly closed in shutdown
- [x] AIResponseCache updated to use injected redis_cache
- [x] No syntax errors in refactored files
- [x] Type annotations properly updated

---

## 🎯 Benefits of This Refactoring

1. **Testability**: Easy to mock redis_cache in unit tests
2. **Flexibility**: Can have multiple redis_cache instances if needed
3. **Clarity**: Explicit dependency flow vs implicit singleton state
4. **Maintainability**: Easier to track which services depend on caching
5. **Scalability**: Can easily switch implementations or use different configs per route
6. **Concurrency**: No shared mutable state across requests

---

## 📚 Files Modified

| File                       | Lines Changed | Type       |
| -------------------------- | ------------- | ---------- |
| `services/redis_cache.py`  | ~100          | Refactored |
| `services/ai_cache.py`     | ~40           | Refactored |
| `utils/startup_manager.py` | ~20           | Updated    |
| `main.py`                  | ~5            | Updated    |

**Total Changes**: ~165 lines modified/updated

---

## ⚠️ Migration Guide for Other Services

If you have other services using RedisCache, update them as follows:

**Before (Singleton):**

```python
from services.redis_cache import RedisCache

# In any method:
await RedisCache.get(key)
await RedisCache.set(key, value)
```

**After (Dependency Injection):**

```python
# Get redis_cache from request or constructor
redis_cache = request.app.state.redis_cache

# Use instance methods
await redis_cache.get(key)
await redis_cache.set(key, value)
```

---

## 🧪 Testing

To test the refactored code:

```python
import asyncio
from services.redis_cache import RedisCache

async def test_redis_di():
    # Create instance
    redis_cache = await RedisCache.create()

    # Test methods
    assert await redis_cache.set("test", "value", ttl=60)
    assert await redis_cache.get("test") == "value"
    assert await redis_cache.delete("test")

    # Cleanup
    await redis_cache.close()

asyncio.run(test_redis_di())
```

---

**Status**: ✅ COMPLETE  
**Date**: December 16, 2025  
**Version**: 1.0
