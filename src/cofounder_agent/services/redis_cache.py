"""
Redis Caching Service for Query Performance Optimization

Provides high-performance caching for frequently accessed database queries
and expensive computational results.

Features:
- Async Redis operations using aioredis
- TTL (Time-To-Live) configuration for automatic expiration
- Cache invalidation strategies
- Batch operations for efficiency
- Health checking and fallback behavior
- Configurable cache prefixes for organization

Configuration:
Set REDIS_URL environment variable:
    export REDIS_URL="redis://localhost:6379/0"
    export REDIS_ENABLED="true"

For local development without Redis, set REDIS_ENABLED=false
The system will work normally but without cache benefits.
"""

import asyncio
import json
import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Dict, Optional

from services.logger_config import get_logger
from services.site_config import site_config

try:
    import redis.asyncio as aioredis  # type: ignore[import-untyped]
    from redis.asyncio import Redis  # type: ignore[import-untyped]

    REDIS_AVAILABLE = True
except ImportError:
    aioredis = None  # type: ignore[assignment]
    REDIS_AVAILABLE = False
    # Type placeholder when Redis is not available
    if TYPE_CHECKING:
        from redis.asyncio import Redis  # type: ignore
    else:
        Redis = None  # type: ignore
    logging.warning("Redis SDK not installed. Caching disabled. Install with: pip install redis")

logger = get_logger(__name__)


def _cache_ttl(key: str, default: int) -> int:
    """Resolve a TTL from app_settings (cache_<key>_ttl_seconds) with a default.

    Read at class-load time — restart the worker to pick up app_settings
    changes. (#198)
    """
    try:
        from services.site_config import site_config as _sc
        return _sc.get_int(f"cache_{key}_ttl_seconds", default)
    except Exception:
        return default


class CacheConfig:
    """Configuration for cache behavior.

    All TTLs read from app_settings with sensible defaults; operators
    tune per-category without a code change via keys like
    `cache_default_ttl_seconds`, `cache_query_ttl_seconds`, etc. (#198)
    """

    # Default TTLs (Time-To-Live) in seconds — values come from app_settings
    DEFAULT_TTL = _cache_ttl("default", 3600)        # 1 hour
    QUERY_CACHE_TTL = _cache_ttl("query", 1800)       # 30 minutes for DB queries
    USER_CACHE_TTL = _cache_ttl("user", 300)          # 5 minutes for user data
    METRICS_CACHE_TTL = _cache_ttl("metrics", 60)     # 1 minute for rapidly changing metrics
    CONTENT_CACHE_TTL = _cache_ttl("content", 7200)   # 2 hours for content
    MODEL_CACHE_TTL = _cache_ttl("model", 86400)      # 1 day for model configs

    # Cache key prefixes for organization
    PREFIX_QUERY = "query:"
    PREFIX_USER = "user:"
    PREFIX_METRICS = "metrics:"
    PREFIX_CONTENT = "content:"
    PREFIX_MODEL = "model:"
    PREFIX_SESSION = "session:"
    PREFIX_TASK = "task:"


class RedisCache:
    """
    High-performance Redis caching service for query optimization.

    Handles async Redis operations with automatic fallback when Redis is unavailable.
    Provides convenience methods for common cache operations.

    Uses dependency injection instead of singleton pattern:
        redis_cache = await RedisCache.create()
        app.state.redis_cache = redis_cache

        # Then in routes/services:
        async def my_route(request: Request):
            redis_cache = request.app.state.redis_cache
            value = await redis_cache.get(key)
    """

    def __init__(self, redis_instance: Redis | None = None, enabled: bool = False):
        """
        Initialize RedisCache with a Redis instance.

        Args:
            redis_instance: Connected Redis instance (or None if disabled)
            enabled: Whether caching is enabled
        """
        self._instance: Redis | None = redis_instance
        self._enabled = enabled

    @classmethod
    async def create(cls) -> "RedisCache":
        """
        Factory method to create a RedisCache instance with environment configuration.

        Returns:
            RedisCache: Instance with Redis connection if available, disabled otherwise
        """
        if not REDIS_AVAILABLE:
            logger.warning("Redis not available - caching disabled")
            return cls(redis_instance=None, enabled=False)

        # Get configuration from site_config (falls back to env vars)
        redis_url = site_config.get("redis_url", "redis://localhost:6379/0")
        redis_enabled = site_config.get("redis_enabled", "true").lower() in ("true", "1", "yes")

        if not redis_enabled:
            logger.info("Redis disabled via REDIS_ENABLED=false")
            return cls(redis_instance=None, enabled=False)

        try:
            # Create async Redis connection
            redis_instance = await aioredis.from_url(  # type: ignore[union-attr]
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
                health_check_interval=30,  # Health check every 30s
            )

            # Test the connection
            await redis_instance.ping()

            logger.info("Redis cache initialized successfully")
            logger.info("   URL: %s...", redis_url.split('@')[0] if '@' in redis_url else redis_url)
            logger.info("   Default TTL: %ss", CacheConfig.DEFAULT_TTL)

            return cls(redis_instance=redis_instance, enabled=True)

        except Exception as e:
            # Redis is optional — the system falls back to no-cache mode.
            # Log at INFO with a short message (not an ERROR + traceback);
            # the full traceback was previously drowning out real errors
            # in the startup logs.
            logger.info(
                "[redis_cache] Redis not available at %s (%s) — running without cache",
                redis_url.split('@')[0] if '@' in redis_url else redis_url,
                type(e).__name__,
            )
            return cls(redis_instance=None, enabled=False)

    async def is_available(self) -> bool:
        """Check if Redis is initialized and available."""
        return self._enabled and self._instance is not None

    async def get(self, key: str) -> Any | None:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found or Redis unavailable
        """
        if not await self.is_available():
            return None

        try:
            # Type guard: we know _instance is not None here due to is_available check
            value = await self._instance.get(key)  # type: ignore
            if value:
                logger.debug("Cache hit: %s", key)
                # Try to parse JSON
                try:
                    return json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    return value
            return None
        except Exception as e:
            logger.error("[_get] Cache get error for %s: %s", key, e, exc_info=True)
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache (will be JSON serialized)
            ttl: Time-to-live in seconds (uses DEFAULT_TTL if not specified)

        Returns:
            bool: True if successful, False otherwise
        """
        if not await self.is_available():
            return False

        try:
            ttl_val = ttl or CacheConfig.DEFAULT_TTL

            # Serialize value to JSON
            if isinstance(value, (dict, list)):
                cached_value = json.dumps(value)
            else:
                cached_value = str(value)

            # Type guard: we know _instance is not None here due to is_available check
            await self._instance.setex(key, ttl_val, cached_value)  # type: ignore
            logger.debug("Cache set: %s (TTL: %ss)", key, ttl_val)
            return True
        except Exception as e:
            logger.error("[_set] Cache set error for %s: %s", key, e, exc_info=True)
            return False

    async def delete(self, key: str) -> bool:
        """
        Delete key from cache.

        Args:
            key: Cache key

        Returns:
            bool: True if key was deleted, False otherwise
        """
        if not await self.is_available():
            return False

        try:
            # Type guard: we know _instance is not None here due to is_available check
            result = await self._instance.delete(key)  # type: ignore
            if result:
                logger.debug("Cache deleted: %s", key)
            return bool(result)
        except Exception as e:
            logger.error("[_delete] Cache delete error for %s: %s", key, e, exc_info=True)
            return False

    async def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching a pattern.
        Useful for cache invalidation.

        Args:
            pattern: Key pattern (e.g., "query:tasks:*")

        Returns:
            Number of keys deleted
        """
        if not await self.is_available():
            return 0

        try:
            # Type guard: we know _instance is not None here due to is_available check
            keys = await self._instance.keys(pattern)  # type: ignore
            if keys:
                deleted = await self._instance.delete(*keys)  # type: ignore
                logger.debug("Cache invalidated %s keys matching %s", deleted, pattern)
                return deleted
            return 0
        except Exception as e:
            logger.error(
                "[_delete_pattern] Cache delete pattern error for %s: %s", pattern, e, exc_info=True
            )
            return 0

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        if not await self.is_available():
            return False

        try:
            # Type guard: we know _instance is not None here due to is_available check
            return bool(await self._instance.exists(key))  # type: ignore
        except Exception as e:
            logger.error("[_exists] Cache exists error for %s: %s", key, e, exc_info=True)
            return False

    async def get_or_set(
        self, key: str, fetch_fn: Callable, ttl: int | None = None
    ) -> Any | None:
        """
        Get value from cache, or fetch and cache if missing.

        Useful for expensive operations that should be cached.

        Args:
            key: Cache key
            fetch_fn: Async function to call if cache miss (must return value)
            ttl: Time-to-live for cached value

        Returns:
            Cached or fetched value, or None if fetch failed

        Example:
            data = await redis_cache.get_or_set(
                "query:tasks:pending",
                fetch_fn=lambda: database_service.get_pending_tasks(),
                ttl=300
            )
        """
        if not await self.is_available():
            # Redis unavailable, just fetch directly
            return await fetch_fn() if asyncio.iscoroutinefunction(fetch_fn) else fetch_fn()

        # Try to get from cache
        cached = await self.get(key)
        if cached is not None:
            logger.debug("Cache hit: %s", key)
            return cached

        # Cache miss - fetch from source
        try:
            logger.debug("Cache miss: %s, fetching...", key)
            if asyncio.iscoroutinefunction(fetch_fn):
                value = await fetch_fn()
            else:
                value = fetch_fn()

            # Cache the result
            if value is not None:
                await self.set(key, value, ttl)

            return value
        except Exception as e:
            logger.error("[_get_or_set] Error fetching value for %s: %s", key, e, exc_info=True)
            return None

    async def incr(self, key: str, amount: int = 1) -> int:
        """
        Increment a counter in cache.

        Args:
            key: Cache key
            amount: Amount to increment by

        Returns:
            New value of counter
        """
        if not await self.is_available():
            return amount

        try:
            # Type guard: we know _instance is not None here due to is_available check
            return await self._instance.incrby(key, amount)  # type: ignore
        except Exception as e:
            logger.error("[_incr] Cache incr error for %s: %s", key, e, exc_info=True)
            return amount

    async def health_check(self) -> dict[str, Any]:
        """
        Check Redis health status.

        Returns:
            Dictionary with health information
        """
        if not await self.is_available():
            return {"status": "disabled", "available": False, "reason": "Redis not initialized"}

        try:
            # Type guard: we know _instance is not None here due to is_available check
            # Ping Redis
            await self._instance.ping()  # type: ignore

            # Get info
            info = await self._instance.info()  # type: ignore

            return {
                "status": "healthy",
                "available": True,
                "uptime_seconds": info.get("uptime_in_seconds", 0),
                "used_memory_mb": info.get("used_memory", 0) / 1024 / 1024,
                "connected_clients": info.get("connected_clients", 0),
                "ops_per_sec": info.get("instantaneous_ops_per_sec", 0),
            }
        except Exception as e:
            logger.error("[_health_check] Redis health check failed: %s", e, exc_info=True)
            return {"status": "unhealthy", "available": False, "error": str(e)}

    async def clear_all(self) -> bool:
        """
        Clear all cache (use with caution!).

        Returns:
            bool: True if successful
        """
        if not await self.is_available():
            return False

        try:
            # Type guard: we know _instance is not None here due to is_available check
            await self._instance.flushdb()  # type: ignore
            logger.warning("Cache cleared (all keys deleted)")
            return True
        except Exception as e:
            logger.error("[_clear_all] Cache clear error: %s", e, exc_info=True)
            return False

    async def close(self):
        """Close Redis connection."""
        if self._instance:
            try:
                await self._instance.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.error("[_close] Error closing Redis connection: %s", e, exc_info=True)


# Convenience function for backward compatibility
async def setup_redis_cache() -> bool:
    """
    Initialize Redis cache service (backward compatibility).

    DEPRECATED: Use RedisCache.create() instead in main startup code.
    This function is kept for compatibility but doesn't follow DI pattern.

    Usage (old way - do not use):
        await setup_redis_cache()

    Usage (new way - recommended):
        redis_cache = await RedisCache.create()
        app.state.redis_cache = redis_cache
    """
    redis_cache = await RedisCache.create()
    return redis_cache._enabled


# Decorator for automatic caching
def cached(ttl: int = CacheConfig.DEFAULT_TTL, key_prefix: str = ""):
    """
    Decorator for automatic caching of async function results.

    IMPORTANT: This decorator requires redis_cache to be injected as the first argument
    or to be available via dependency injection. The function signature must include
    redis_cache parameter.

    Usage:
        @cached(ttl=300, key_prefix="tasks:")
        async def get_pending_tasks(redis_cache: RedisCache):
            return await database.get_pending_tasks()

    Args:
        ttl: Cache TTL in seconds
        key_prefix: Prefix for cache key (default uses function name)
    """

    def decorator(func: Callable):
        async def wrapper(*args, **kwargs):
            # Extract redis_cache from kwargs or args
            redis_cache = kwargs.get("redis_cache")
            if not redis_cache and args:
                # Try to find it in args (it should be first argument with type RedisCache)
                for arg in args:
                    if isinstance(arg, RedisCache):
                        redis_cache = arg
                        break

            if not redis_cache:
                # Fallback: create a disabled cache instance
                logger.warning(
                    "@cached decorator: redis_cache not found in %s, caching disabled",
                    func.__name__,
                )
                return await func(*args, **kwargs)

            # Build cache key from function name and arguments
            cache_key = f"{key_prefix or func.__name__}:{json.dumps(str(args) + str(kwargs))}"

            # Try to get from cache
            cached_result = await redis_cache.get(cache_key)
            if cached_result is not None:
                return cached_result

            # Cache miss - call function
            result = await func(*args, **kwargs)

            # Cache the result
            await redis_cache.set(cache_key, result, ttl)

            return result

        return wrapper

    return decorator
