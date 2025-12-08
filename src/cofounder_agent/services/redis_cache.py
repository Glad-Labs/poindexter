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

import os
import json
import logging
from typing import Optional, Any, Dict, List, Callable
from datetime import datetime, timedelta
import asyncio

try:
    import redis.asyncio as aioredis
    from redis.asyncio import Redis
    from redis.exceptions import RedisError, ConnectionError as RedisConnectionError
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    Redis = None  # Type placeholder when Redis is not available
    logging.warning("Redis SDK not installed. Caching disabled. Install with: pip install redis aioredis")

logger = logging.getLogger(__name__)


class CacheConfig:
    """Configuration for cache behavior."""
    
    # Default TTLs (Time-To-Live) in seconds
    DEFAULT_TTL = 3600  # 1 hour
    QUERY_CACHE_TTL = 1800  # 30 minutes for DB queries
    USER_CACHE_TTL = 300  # 5 minutes for user data
    METRICS_CACHE_TTL = 60  # 1 minute for rapidly changing metrics
    CONTENT_CACHE_TTL = 7200  # 2 hours for content
    MODEL_CACHE_TTL = 86400  # 1 day for model configs
    
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
    """
    
    _instance: Optional[Redis] = None
    _initialized = False
    _enabled = False
    _health_check_scheduled = False
    
    @classmethod
    async def initialize(cls) -> bool:
        """
        Initialize Redis connection.
        
        Returns:
            bool: True if successfully connected, False if Redis unavailable or disabled
        """
        if not REDIS_AVAILABLE:
            logger.warning("❌ Redis not available - caching disabled")
            cls._initialized = True
            cls._enabled = False
            return False
        
        if cls._initialized:
            return cls._enabled
        
        # Get configuration from environment
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_enabled = os.getenv("REDIS_ENABLED", "true").lower() in ("true", "1", "yes")
        
        if not redis_enabled:
            logger.info("ℹ️  Redis disabled via REDIS_ENABLED=false")
            cls._initialized = True
            cls._enabled = False
            return False
        
        try:
            # Create async Redis connection
            cls._instance = await aioredis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
                health_check_interval=30  # Health check every 30s
            )
            
            # Test the connection
            await cls._instance.ping()
            
            logger.info(f"✅ Redis cache initialized successfully")
            logger.info(f"   URL: {redis_url.split('@')[0] if '@' in redis_url else redis_url}...")
            logger.info(f"   Default TTL: {CacheConfig.DEFAULT_TTL}s")
            
            cls._initialized = True
            cls._enabled = True
            return True
            
        except Exception as e:
            logger.warning(f"⚠️  Failed to connect to Redis: {str(e)}")
            logger.info("   System will continue without caching")
            logger.info(f"   To enable caching, ensure Redis is running at: {redis_url}")
            cls._initialized = True
            cls._enabled = False
            return False
    
    @classmethod
    async def is_available(cls) -> bool:
        """Check if Redis is initialized and available."""
        if not cls._initialized:
            await cls.initialize()
        return cls._enabled and cls._instance is not None
    
    @classmethod
    async def get(cls, key: str) -> Optional[Any]:
        """
        Get value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found or Redis unavailable
        """
        if not await cls.is_available():
            return None
        
        try:
            value = await cls._instance.get(key)
            if value:
                logger.debug(f"Cache hit: {key}")
                # Try to parse JSON
                try:
                    return json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    return value
            return None
        except Exception as e:
            logger.warning(f"Cache get error for {key}: {e}")
            return None
    
    @classmethod
    async def set(cls, key: str, value: Any, ttl: int = None) -> bool:
        """
        Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache (will be JSON serialized)
            ttl: Time-to-live in seconds (uses DEFAULT_TTL if not specified)
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not await cls.is_available():
            return False
        
        try:
            ttl = ttl or CacheConfig.DEFAULT_TTL
            
            # Serialize value to JSON
            if isinstance(value, (dict, list)):
                cached_value = json.dumps(value)
            else:
                cached_value = str(value)
            
            await cls._instance.setex(key, ttl, cached_value)
            logger.debug(f"Cache set: {key} (TTL: {ttl}s)")
            return True
        except Exception as e:
            logger.warning(f"Cache set error for {key}: {e}")
            return False
    
    @classmethod
    async def delete(cls, key: str) -> bool:
        """
        Delete key from cache.
        
        Args:
            key: Cache key
            
        Returns:
            bool: True if key was deleted, False otherwise
        """
        if not await cls.is_available():
            return False
        
        try:
            result = await cls._instance.delete(key)
            if result:
                logger.debug(f"Cache deleted: {key}")
            return bool(result)
        except Exception as e:
            logger.warning(f"Cache delete error for {key}: {e}")
            return False
    
    @classmethod
    async def delete_pattern(cls, pattern: str) -> int:
        """
        Delete all keys matching a pattern.
        Useful for cache invalidation.
        
        Args:
            pattern: Key pattern (e.g., "query:tasks:*")
            
        Returns:
            Number of keys deleted
        """
        if not await cls.is_available():
            return 0
        
        try:
            keys = await cls._instance.keys(pattern)
            if keys:
                deleted = await cls._instance.delete(*keys)
                logger.debug(f"Cache invalidated {deleted} keys matching {pattern}")
                return deleted
            return 0
        except Exception as e:
            logger.warning(f"Cache delete pattern error for {pattern}: {e}")
            return 0
    
    @classmethod
    async def exists(cls, key: str) -> bool:
        """Check if key exists in cache."""
        if not await cls.is_available():
            return False
        
        try:
            return bool(await cls._instance.exists(key))
        except Exception as e:
            logger.warning(f"Cache exists error for {key}: {e}")
            return False
    
    @classmethod
    async def get_or_set(cls, key: str, fetch_fn: Callable, ttl: int = None) -> Optional[Any]:
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
            data = await RedisCache.get_or_set(
                "query:tasks:pending",
                fetch_fn=lambda: database_service.get_pending_tasks(),
                ttl=300
            )
        """
        if not await cls.is_available():
            # Redis unavailable, just fetch directly
            return await fetch_fn() if asyncio.iscoroutinefunction(fetch_fn) else fetch_fn()
        
        # Try to get from cache
        cached = await cls.get(key)
        if cached is not None:
            logger.debug(f"Cache hit: {key}")
            return cached
        
        # Cache miss - fetch from source
        try:
            logger.debug(f"Cache miss: {key}, fetching...")
            if asyncio.iscoroutinefunction(fetch_fn):
                value = await fetch_fn()
            else:
                value = fetch_fn()
            
            # Cache the result
            if value is not None:
                await cls.set(key, value, ttl)
            
            return value
        except Exception as e:
            logger.error(f"Error fetching value for {key}: {e}")
            return None
    
    @classmethod
    async def incr(cls, key: str, amount: int = 1) -> int:
        """
        Increment a counter in cache.
        
        Args:
            key: Cache key
            amount: Amount to increment by
            
        Returns:
            New value of counter
        """
        if not await cls.is_available():
            return amount
        
        try:
            return await cls._instance.incrby(key, amount)
        except Exception as e:
            logger.warning(f"Cache incr error for {key}: {e}")
            return amount
    
    @classmethod
    async def health_check(cls) -> Dict[str, Any]:
        """
        Check Redis health status.
        
        Returns:
            Dictionary with health information
        """
        if not await cls.is_available():
            return {
                "status": "disabled",
                "available": False,
                "reason": "Redis not initialized"
            }
        
        try:
            # Ping Redis
            await cls._instance.ping()
            
            # Get info
            info = await cls._instance.info()
            
            return {
                "status": "healthy",
                "available": True,
                "uptime_seconds": info.get("uptime_in_seconds", 0),
                "used_memory_mb": info.get("used_memory", 0) / 1024 / 1024,
                "connected_clients": info.get("connected_clients", 0),
                "ops_per_sec": info.get("instantaneous_ops_per_sec", 0)
            }
        except Exception as e:
            logger.warning(f"Redis health check failed: {e}")
            return {
                "status": "unhealthy",
                "available": False,
                "error": str(e)
            }
    
    @classmethod
    async def clear_all(cls) -> bool:
        """
        Clear all cache (use with caution!).
        
        Returns:
            bool: True if successful
        """
        if not await cls.is_available():
            return False
        
        try:
            await cls._instance.flushdb()
            logger.warning("Cache cleared (all keys deleted)")
            return True
        except Exception as e:
            logger.warning(f"Cache clear error: {e}")
            return False
    
    @classmethod
    async def close(cls):
        """Close Redis connection."""
        if cls._instance:
            try:
                await cls._instance.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.warning(f"Error closing Redis connection: {e}")


# Convenience function for one-time setup
async def setup_redis_cache() -> bool:
    """
    Initialize Redis cache service.
    
    Usage in main.py:
        from services.redis_cache import setup_redis_cache
        await setup_redis_cache()
    """
    return await RedisCache.initialize()


# Decorator for automatic caching
def cached(ttl: int = CacheConfig.DEFAULT_TTL, key_prefix: str = ""):
    """
    Decorator for automatic caching of async function results.
    
    Usage:
        @cached(ttl=300, key_prefix="tasks:")
        async def get_pending_tasks():
            return await database.get_pending_tasks()
    
    Args:
        ttl: Cache TTL in seconds
        key_prefix: Prefix for cache key (default uses function name)
    """
    def decorator(func: Callable):
        async def wrapper(*args, **kwargs):
            # Build cache key from function name and arguments
            cache_key = f"{key_prefix or func.__name__}:{json.dumps(str(args) + str(kwargs))}"
            
            # Try to get from cache
            cached_result = await RedisCache.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            # Cache miss - call function
            result = await func(*args, **kwargs)
            
            # Cache the result
            await RedisCache.set(cache_key, result, ttl)
            
            return result
        
        return wrapper
    
    return decorator
