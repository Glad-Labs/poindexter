# Redis Dependency Injection - Quick Reference

## How to Access Redis Cache in Your Code

### Option 1: From FastAPI Request (Recommended for Routes)

```python
from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/api/data")
async def get_data(request: Request):
    redis_cache = request.app.state.redis_cache

    # Get from cache or fetch
    data = await redis_cache.get_or_set(
        key="data:key",
        fetch_fn=lambda: fetch_from_database(),
        ttl=3600  # 1 hour
    )
    return data
```

### Option 2: Via FastAPI Dependency Injection

```python
from fastapi import APIRouter, Depends, Request
from typing import Optional
from services.redis_cache import RedisCache

def get_redis_cache(request: Request) -> Optional[RedisCache]:
    return request.app.state.redis_cache

router = APIRouter()

@router.get("/api/users/{user_id}")
async def get_user(user_id: str, redis_cache: Optional[RedisCache] = Depends(get_redis_cache)):
    if redis_cache:
        cached = await redis_cache.get(f"user:{user_id}")
        if cached:
            return cached

    # Fetch from DB
    user = await fetch_user(user_id)

    if redis_cache:
        await redis_cache.set(f"user:{user_id}", user, ttl=1800)

    return user
```

### Option 3: In Services (Dependency Injection via Constructor)

```python
from typing import Optional
from services.redis_cache import RedisCache, CacheConfig

class UserService:
    def __init__(self, redis_cache: Optional[RedisCache] = None):
        self.redis_cache = redis_cache

    async def get_user_profile(self, user_id: str):
        # Check cache first
        if self.redis_cache:
            cached = await self.redis_cache.get(f"profile:{user_id}")
            if cached:
                return cached

        # Fetch from database
        profile = await self.fetch_profile_from_db(user_id)

        # Cache result
        if self.redis_cache and profile:
            await self.redis_cache.set(
                f"profile:{user_id}",
                profile,
                ttl=CacheConfig.USER_CACHE_TTL  # 5 minutes
            )

        return profile
```

## Core Methods

### Get Value

```python
value = await redis_cache.get("my:key")
```

### Set Value (with TTL)

```python
# TTL in seconds
await redis_cache.set("my:key", value, ttl=3600)
```

### Get or Set (Most Common Pattern)

```python
# Fetch and cache in one call
result = await redis_cache.get_or_set(
    key="my:key",
    fetch_fn=lambda: expensive_operation(),
    ttl=3600
)
```

### Delete Value

```python
await redis_cache.delete("my:key")
```

### Delete by Pattern

```python
# Delete all keys matching pattern
deleted_count = await redis_cache.delete_pattern("my:*")
```

### Check if Key Exists

```python
exists = await redis_cache.exists("my:key")
```

### Increment Counter

```python
# Useful for rate limiting, metrics
new_count = await redis_cache.incr("api:calls:user_123", amount=1)
```

## Cache Key Prefixes (Best Practices)

```python
from services.redis_cache import CacheConfig

# Use predefined prefixes for organization
CacheConfig.PREFIX_QUERY      # "query:"
CacheConfig.PREFIX_USER       # "user:"
CacheConfig.PREFIX_METRICS    # "metrics:"
CacheConfig.PREFIX_CONTENT    # "content:"
CacheConfig.PREFIX_MODEL      # "model:"
CacheConfig.PREFIX_SESSION    # "session:"
CacheConfig.PREFIX_TASK       # "task:"

# Example usage
key = f"{CacheConfig.PREFIX_USER}profile:{user_id}"
```

## TTL Recommendations

```python
from services.redis_cache import CacheConfig

# Predefined TTLs
CacheConfig.DEFAULT_TTL         # 3600s (1 hour)
CacheConfig.QUERY_CACHE_TTL     # 1800s (30 minutes) - for DB queries
CacheConfig.USER_CACHE_TTL      # 300s (5 minutes) - for user data
CacheConfig.METRICS_CACHE_TTL   # 60s (1 minute) - for rapidly changing metrics
CacheConfig.CONTENT_CACHE_TTL   # 7200s (2 hours) - for content
CacheConfig.MODEL_CACHE_TTL     # 86400s (1 day) - for model configs
```

## Error Handling

```python
# Cache is designed to gracefully handle failures
# All methods return None or False if redis is unavailable

value = await redis_cache.get("key")  # Returns None if missing or error
success = await redis_cache.set("key", value)  # Returns False if error

# For critical operations, check explicitly:
if redis_cache and await redis_cache.is_available():
    await redis_cache.set("key", value)
```

## Health Check

```python
# Check Redis health
health = await redis_cache.health_check()
print(health)
# {
#   "status": "healthy",
#   "available": True,
#   "uptime_seconds": 86400,
#   "used_memory_mb": 512.5,
#   "connected_clients": 3,
#   "ops_per_sec": 150
# }
```

## AI Response Cache Integration

```python
from services.ai_cache import AIResponseCache

# Create cache with injected redis_cache
ai_cache = AIResponseCache(redis_cache=redis_cache, ttl_hours=24)

# Cache AI responses
cached_response = await ai_cache.get(prompt, "gpt-4", {"temperature": 0.7})
if cached_response:
    return cached_response

# Call AI API...
response = await call_openai_api(prompt)

# Cache the response
await ai_cache.set(prompt, "gpt-4", {"temperature": 0.7}, response)
```

## Image Cache Integration

```python
from services.ai_cache import ImageCache

# Create cache with injected redis_cache
image_cache = ImageCache(redis_cache=redis_cache, ttl_days=30)

# Check for cached image
cached_image = await image_cache.get_cached_image("AI", ["artificial", "intelligence"])
if cached_image:
    return cached_image

# Search for image...
image = await pexels_api.search("AI")

# Cache the image
await image_cache.cache_image("AI", ["artificial", "intelligence"], image)
```

## Testing

```python
import pytest
from services.redis_cache import RedisCache
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_cache_hit():
    # Create disabled cache for testing
    redis_cache = RedisCache(redis_instance=None, enabled=False)

    # Or create with mock
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value='{"value": "test"}')

    redis_cache = RedisCache(redis_instance=mock_redis, enabled=True)
    result = await redis_cache.get("test:key")
    assert result == {"value": "test"}
```

---

**Created**: December 16, 2025  
**Version**: 1.0  
**Status**: Ready for Use
