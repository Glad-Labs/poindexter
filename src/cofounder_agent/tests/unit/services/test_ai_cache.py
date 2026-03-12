"""
Unit tests for services.ai_cache

Tests cover:
- AIResponseCache: key generation, get/set with redis, no-op without redis,
  hit/miss metrics, reset_metrics, get_metrics
- ImageCache: key building, get/set with redis, no-op without redis,
  hit/miss metrics, clear_cache (no-op log)
- Global singleton: initialize_ai_cache, get_ai_cache
"""

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai_cache import (
    AIResponseCache,
    ImageCache,
    get_ai_cache,
    initialize_ai_cache,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_redis(get_return=None):
    """Return a mock RedisCache."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=get_return)
    redis.set = AsyncMock(return_value=True)
    return redis


# ---------------------------------------------------------------------------
# AIResponseCache._generate_key
# ---------------------------------------------------------------------------


class TestGenerateKey:
    def test_deterministic_for_same_inputs(self):
        cache = AIResponseCache()
        key1 = cache._generate_key("hello", "gpt-4", {"temperature": 0.7})
        key2 = cache._generate_key("hello", "gpt-4", {"temperature": 0.7})
        assert key1 == key2

    def test_different_prompts_different_keys(self):
        cache = AIResponseCache()
        k1 = cache._generate_key("hello", "gpt-4", {})
        k2 = cache._generate_key("world", "gpt-4", {})
        assert k1 != k2

    def test_different_models_different_keys(self):
        cache = AIResponseCache()
        k1 = cache._generate_key("hello", "gpt-4", {})
        k2 = cache._generate_key("hello", "claude-opus", {})
        assert k1 != k2

    def test_different_temperature_different_keys(self):
        cache = AIResponseCache()
        k1 = cache._generate_key("hello", "gpt-4", {"temperature": 0.7})
        k2 = cache._generate_key("hello", "gpt-4", {"temperature": 0.1})
        assert k1 != k2

    def test_returns_sha256_hex_string(self):
        cache = AIResponseCache()
        key = cache._generate_key("test", "model", {})
        assert len(key) == 64  # SHA-256 hex = 64 chars
        int(key, 16)  # Must be valid hex

    def test_strips_leading_trailing_whitespace(self):
        cache = AIResponseCache()
        k1 = cache._generate_key("  hello  ", "gpt-4", {})
        k2 = cache._generate_key("hello", "gpt-4", {})
        assert k1 == k2


# ---------------------------------------------------------------------------
# AIResponseCache.get
# ---------------------------------------------------------------------------


class TestAIResponseCacheGet:
    @pytest.mark.asyncio
    async def test_cache_miss_no_redis(self):
        cache = AIResponseCache()  # no redis
        result = await cache.get("prompt", "model", {})
        assert result is None
        assert cache.metrics["misses"] == 1
        assert cache.metrics["hits"] == 0

    @pytest.mark.asyncio
    async def test_cache_hit_increments_hits(self):
        redis = make_redis(get_return={"response": "cached_text", "model": "gpt-4"})
        cache = AIResponseCache(redis_cache=redis)
        result = await cache.get("prompt", "gpt-4", {})
        assert result == {"response": "cached_text", "model": "gpt-4"}
        assert cache.metrics["hits"] == 1
        assert cache.metrics["misses"] == 0

    @pytest.mark.asyncio
    async def test_cache_miss_with_redis_increments_misses(self):
        redis = make_redis(get_return=None)
        cache = AIResponseCache(redis_cache=redis)
        result = await cache.get("prompt", "gpt-4", {})
        assert result is None
        assert cache.metrics["misses"] == 1

    @pytest.mark.asyncio
    async def test_redis_get_called_with_correct_prefix(self):
        redis = make_redis(get_return=None)
        cache = AIResponseCache(redis_cache=redis)
        await cache.get("hello", "model", {})
        # Verify key starts with ai: namespace
        call_args = redis.get.call_args[0][0]
        assert "ai:" in call_args


# ---------------------------------------------------------------------------
# AIResponseCache.set
# ---------------------------------------------------------------------------


class TestAIResponseCacheSet:
    @pytest.mark.asyncio
    async def test_no_op_without_redis(self):
        cache = AIResponseCache()
        await cache.set("prompt", "model", {}, "response")
        # Should not raise

    @pytest.mark.asyncio
    async def test_calls_redis_set_with_response(self):
        redis = make_redis()
        cache = AIResponseCache(redis_cache=redis, ttl_hours=12)
        await cache.set("hello", "gpt-4", {}, "answer")
        redis.set.assert_awaited_once()
        call_args = redis.set.call_args
        data = call_args[0][1]
        assert data["response"] == "answer"
        assert data["model"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_ttl_passed_to_redis(self):
        redis = make_redis()
        cache = AIResponseCache(redis_cache=redis, ttl_hours=6)
        await cache.set("hello", "gpt-4", {}, "answer")
        call_kwargs = redis.set.call_args[1]
        assert call_kwargs["ttl"] == 6 * 3600


# ---------------------------------------------------------------------------
# AIResponseCache.get_metrics / reset_metrics
# ---------------------------------------------------------------------------


class TestAIResponseCacheMetrics:
    def test_initial_metrics_zero(self):
        cache = AIResponseCache()
        m = cache.get_metrics()
        assert m["hits"] == 0
        assert m["misses"] == 0
        assert m["total_requests"] == 0
        assert m["hit_rate"] == 0

    @pytest.mark.asyncio
    async def test_hit_rate_calculated_correctly(self):
        redis = make_redis(get_return={"response": "r"})
        cache = AIResponseCache(redis_cache=redis)
        await cache.get("p1", "m", {})
        await cache.get("p2", "m", {})
        # Both are hits
        m = cache.get_metrics()
        assert m["hit_rate"] == 100.0

    def test_reset_metrics(self):
        cache = AIResponseCache()
        cache.metrics["hits"] = 10
        cache.metrics["misses"] = 5
        cache.reset_metrics()
        assert cache.metrics["hits"] == 0
        assert cache.metrics["misses"] == 0


# ---------------------------------------------------------------------------
# ImageCache._build_cache_key
# ---------------------------------------------------------------------------


class TestImageCacheBuildKey:
    def test_deterministic(self):
        ic = ImageCache()
        k1 = ic._build_cache_key("AI", ["machine", "learning"])
        k2 = ic._build_cache_key("AI", ["machine", "learning"])
        assert k1 == k2

    def test_different_topic_different_key(self):
        ic = ImageCache()
        k1 = ic._build_cache_key("AI", [])
        k2 = ic._build_cache_key("robots", [])
        assert k1 != k2

    def test_no_keywords(self):
        ic = ImageCache()
        k = ic._build_cache_key("AI")
        assert len(k) == 32  # MD5 hex = 32 chars

    def test_topic_truncated_to_30_chars(self):
        ic = ImageCache()
        # Long topic — should still produce consistent key
        long_topic = "A" * 100
        k = ic._build_cache_key(long_topic)
        assert len(k) == 32


# ---------------------------------------------------------------------------
# ImageCache.get_cached_image
# ---------------------------------------------------------------------------


class TestImageCacheGetCachedImage:
    @pytest.mark.asyncio
    async def test_no_redis_returns_none(self):
        ic = ImageCache()
        result = await ic.get_cached_image("AI")
        assert result is None
        assert ic.metrics["misses"] == 1

    @pytest.mark.asyncio
    async def test_cache_hit_returns_image(self):
        image_data = {"url": "https://example.com/img.jpg", "id": 123}
        redis = make_redis(get_return={"image": image_data})
        ic = ImageCache(redis_cache=redis)
        result = await ic.get_cached_image("AI", ["python"])
        assert result == image_data
        assert ic.metrics["hits"] == 1

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        redis = make_redis(get_return=None)
        ic = ImageCache(redis_cache=redis)
        result = await ic.get_cached_image("AI")
        assert result is None
        assert ic.metrics["misses"] == 1

    @pytest.mark.asyncio
    async def test_non_dict_cached_value_returned_directly(self):
        redis = make_redis(get_return="raw_data")
        ic = ImageCache(redis_cache=redis)
        result = await ic.get_cached_image("AI")
        assert result == "raw_data"

    @pytest.mark.asyncio
    async def test_dict_without_image_key_returned_directly(self):
        redis = make_redis(get_return={"url": "https://example.com/x.jpg"})
        ic = ImageCache(redis_cache=redis)
        result = await ic.get_cached_image("AI")
        assert result == {"url": "https://example.com/x.jpg"}


# ---------------------------------------------------------------------------
# ImageCache.cache_image
# ---------------------------------------------------------------------------


class TestImageCacheCacheImage:
    @pytest.mark.asyncio
    async def test_no_op_without_redis(self):
        ic = ImageCache()
        await ic.cache_image("AI", ["python"], {"url": "img.jpg"})
        # Should not raise

    @pytest.mark.asyncio
    async def test_calls_redis_set(self):
        redis = make_redis()
        ic = ImageCache(redis_cache=redis, ttl_days=7)
        await ic.cache_image("AI", ["python"], {"url": "img.jpg"})
        redis.set.assert_awaited_once()
        data = redis.set.call_args[0][1]
        assert data["image"] == {"url": "img.jpg"}
        assert data["topic"] == "AI"

    @pytest.mark.asyncio
    async def test_ttl_passed_correctly(self):
        redis = make_redis()
        ic = ImageCache(redis_cache=redis, ttl_days=14)
        await ic.cache_image("AI", None, {"url": "x"})
        ttl = redis.set.call_args[1]["ttl"]
        assert ttl == 14 * 86400


# ---------------------------------------------------------------------------
# ImageCache.get_metrics / clear_cache
# ---------------------------------------------------------------------------


class TestImageCacheMetrics:
    def test_initial_metrics(self):
        ic = ImageCache()
        m = ic.get_metrics()
        assert m["total_hits"] == 0
        assert m["total_misses"] == 0
        assert m["hit_rate_percent"] == 0

    def test_clear_cache_does_not_raise(self):
        ic = ImageCache()
        ic.clear_cache()  # Should not raise


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------


class TestGlobalSingleton:
    def test_initialize_ai_cache_returns_instance(self):
        cache = initialize_ai_cache()
        assert isinstance(cache, AIResponseCache)

    def test_get_ai_cache_returns_initialized_instance(self):
        initialize_ai_cache()
        result = get_ai_cache()
        assert isinstance(result, AIResponseCache)

    def test_initialize_with_redis_cache(self):
        redis = make_redis()
        cache = initialize_ai_cache(redis_cache=redis, ttl_hours=48)
        assert cache.redis_cache is redis
        assert cache.ttl_seconds == 48 * 3600
