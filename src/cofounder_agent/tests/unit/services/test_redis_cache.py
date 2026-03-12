"""
Unit tests for services/redis_cache.py

Tests RedisCache when Redis is disabled (no real connection required).
Also tests the @cached decorator and CacheConfig constants.
"""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.redis_cache import CacheConfig, RedisCache, cached, setup_redis_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_disabled_cache() -> RedisCache:
    """Return a RedisCache with Redis disabled (no real connection)."""
    return RedisCache(redis_instance=None, enabled=False)


def make_enabled_cache() -> tuple[RedisCache, MagicMock]:
    """Return a RedisCache with a mocked Redis instance."""
    mock_redis = MagicMock()
    # Make all Redis methods async
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=1)
    mock_redis.keys = AsyncMock(return_value=[])
    mock_redis.exists = AsyncMock(return_value=1)
    mock_redis.incrby = AsyncMock(return_value=5)
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.info = AsyncMock(
        return_value={
            "uptime_in_seconds": 1000,
            "used_memory": 1048576,
            "connected_clients": 2,
            "instantaneous_ops_per_sec": 10,
        }
    )
    mock_redis.flushdb = AsyncMock(return_value=True)
    mock_redis.close = AsyncMock()
    cache = RedisCache(redis_instance=mock_redis, enabled=True)
    return cache, mock_redis


# ---------------------------------------------------------------------------
# CacheConfig
# ---------------------------------------------------------------------------


class TestCacheConfig:
    def test_default_ttl_is_one_hour(self):
        assert CacheConfig.DEFAULT_TTL == 3600

    def test_prefixes_are_strings(self):
        assert CacheConfig.PREFIX_QUERY.endswith(":")
        assert CacheConfig.PREFIX_USER.endswith(":")

    def test_ttl_hierarchy(self):
        # Metrics (rapidly changing) < user < query < content < model
        assert CacheConfig.METRICS_CACHE_TTL < CacheConfig.USER_CACHE_TTL
        assert CacheConfig.USER_CACHE_TTL < CacheConfig.QUERY_CACHE_TTL


# ---------------------------------------------------------------------------
# Disabled cache (no Redis)
# ---------------------------------------------------------------------------


class TestRedisCacheDisabled:
    @pytest.mark.asyncio
    async def test_is_available_false(self):
        cache = make_disabled_cache()
        assert await cache.is_available() is False

    @pytest.mark.asyncio
    async def test_get_returns_none(self):
        cache = make_disabled_cache()
        assert await cache.get("any-key") is None

    @pytest.mark.asyncio
    async def test_set_returns_false(self):
        cache = make_disabled_cache()
        assert await cache.set("key", "value") is False

    @pytest.mark.asyncio
    async def test_delete_returns_false(self):
        cache = make_disabled_cache()
        assert await cache.delete("key") is False

    @pytest.mark.asyncio
    async def test_delete_pattern_returns_zero(self):
        cache = make_disabled_cache()
        assert await cache.delete_pattern("query:*") == 0

    @pytest.mark.asyncio
    async def test_exists_returns_false(self):
        cache = make_disabled_cache()
        assert await cache.exists("key") is False

    @pytest.mark.asyncio
    async def test_incr_returns_amount(self):
        cache = make_disabled_cache()
        result = await cache.incr("counter", amount=3)
        assert result == 3

    @pytest.mark.asyncio
    async def test_health_check_disabled(self):
        cache = make_disabled_cache()
        health = await cache.health_check()
        assert health["status"] == "disabled"
        assert health["available"] is False

    @pytest.mark.asyncio
    async def test_clear_all_returns_false(self):
        cache = make_disabled_cache()
        assert await cache.clear_all() is False

    @pytest.mark.asyncio
    async def test_close_noop(self):
        cache = make_disabled_cache()
        # Should not raise
        await cache.close()

    @pytest.mark.asyncio
    async def test_get_or_set_calls_fetch_fn_directly(self):
        cache = make_disabled_cache()
        fetch_fn = AsyncMock(return_value={"key": "value"})
        result = await cache.get_or_set("key", fetch_fn, ttl=60)
        assert result == {"key": "value"}
        fetch_fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_or_set_sync_fn_when_disabled(self):
        cache = make_disabled_cache()
        result = await cache.get_or_set("key", lambda: 42)
        assert result == 42


# ---------------------------------------------------------------------------
# Enabled cache (mocked Redis)
# ---------------------------------------------------------------------------


class TestRedisCacheEnabled:
    @pytest.mark.asyncio
    async def test_is_available_true(self):
        cache, _ = make_enabled_cache()
        assert await cache.is_available() is True

    @pytest.mark.asyncio
    async def test_get_hit_parses_json(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.get.return_value = json.dumps({"result": 42})
        value = await cache.get("my-key")
        assert value == {"result": 42}

    @pytest.mark.asyncio
    async def test_get_hit_returns_raw_string_if_not_json(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.get.return_value = "plain-text"
        value = await cache.get("my-key")
        assert value == "plain-text"

    @pytest.mark.asyncio
    async def test_get_miss_returns_none(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.get.return_value = None
        assert await cache.get("no-key") is None

    @pytest.mark.asyncio
    async def test_set_dict_value_serialized(self):
        cache, mock_redis = make_enabled_cache()
        result = await cache.set("key", {"a": 1}, ttl=60)
        assert result is True
        mock_redis.setex.assert_awaited_once()
        call_args = mock_redis.setex.call_args[0]
        assert call_args[0] == "key"
        assert call_args[1] == 60
        assert '"a": 1' in call_args[2]

    @pytest.mark.asyncio
    async def test_set_list_value_serialized(self):
        cache, mock_redis = make_enabled_cache()
        result = await cache.set("key", [1, 2, 3])
        assert result is True

    @pytest.mark.asyncio
    async def test_set_string_value_not_json(self):
        cache, mock_redis = make_enabled_cache()
        await cache.set("key", "hello")
        call_args = mock_redis.setex.call_args[0]
        assert call_args[2] == "hello"

    @pytest.mark.asyncio
    async def test_set_uses_default_ttl_if_none(self):
        cache, mock_redis = make_enabled_cache()
        await cache.set("key", "val", ttl=None)
        call_args = mock_redis.setex.call_args[0]
        assert call_args[1] == CacheConfig.DEFAULT_TTL

    @pytest.mark.asyncio
    async def test_delete_returns_true(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.delete.return_value = 1
        assert await cache.delete("key") is True

    @pytest.mark.asyncio
    async def test_delete_returns_false_key_not_found(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.delete.return_value = 0
        assert await cache.delete("missing") is False

    @pytest.mark.asyncio
    async def test_delete_pattern_returns_count(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.keys.return_value = ["a", "b"]
        mock_redis.delete.return_value = 2
        count = await cache.delete_pattern("prefix:*")
        assert count == 2

    @pytest.mark.asyncio
    async def test_delete_pattern_no_keys_returns_zero(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.keys.return_value = []
        count = await cache.delete_pattern("nothing:*")
        assert count == 0

    @pytest.mark.asyncio
    async def test_exists_true(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.exists.return_value = 1
        assert await cache.exists("key") is True

    @pytest.mark.asyncio
    async def test_exists_false(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.exists.return_value = 0
        assert await cache.exists("missing") is False

    @pytest.mark.asyncio
    async def test_incr_returns_new_value(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.incrby.return_value = 5
        assert await cache.incr("counter", amount=1) == 5

    @pytest.mark.asyncio
    async def test_health_check_healthy(self):
        cache, mock_redis = make_enabled_cache()
        health = await cache.health_check()
        assert health["status"] == "healthy"
        assert health["available"] is True
        assert "uptime_seconds" in health

    @pytest.mark.asyncio
    async def test_clear_all_calls_flushdb(self):
        cache, mock_redis = make_enabled_cache()
        result = await cache.clear_all()
        assert result is True
        mock_redis.flushdb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_calls_redis_close(self):
        cache, mock_redis = make_enabled_cache()
        await cache.close()
        mock_redis.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_or_set_returns_cached_value(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.get.return_value = json.dumps("cached")
        fetch_fn = AsyncMock(return_value="fresh")
        result = await cache.get_or_set("key", fetch_fn)
        assert result == "cached"
        fetch_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_or_set_cache_miss_fetches_and_stores(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.get.return_value = None
        fetch_fn = AsyncMock(return_value={"data": "fresh"})
        result = await cache.get_or_set("key", fetch_fn, ttl=300)
        assert result == {"data": "fresh"}
        fetch_fn.assert_awaited_once()
        mock_redis.setex.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_or_set_none_result_not_cached(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.get.return_value = None
        fetch_fn = AsyncMock(return_value=None)
        result = await cache.get_or_set("key", fetch_fn)
        assert result is None
        mock_redis.setex.assert_not_awaited()


# ---------------------------------------------------------------------------
# Error paths — Redis errors should not propagate
# ---------------------------------------------------------------------------


class TestRedisCacheErrorHandling:
    @pytest.mark.asyncio
    async def test_get_exception_returns_none(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.get.side_effect = Exception("connection lost")
        assert await cache.get("key") is None

    @pytest.mark.asyncio
    async def test_set_exception_returns_false(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.setex.side_effect = Exception("timeout")
        assert await cache.set("key", "val") is False

    @pytest.mark.asyncio
    async def test_delete_exception_returns_false(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.delete.side_effect = Exception("error")
        assert await cache.delete("key") is False

    @pytest.mark.asyncio
    async def test_health_check_exception_returns_unhealthy(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.ping.side_effect = Exception("ping failed")
        health = await cache.health_check()
        assert health["status"] == "unhealthy"
        assert health["available"] is False


# ---------------------------------------------------------------------------
# @cached decorator
# ---------------------------------------------------------------------------


class TestCachedDecorator:
    @pytest.mark.asyncio
    async def test_cached_returns_cached_value_on_hit(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.get.return_value = json.dumps("cached-result")

        @cached(ttl=60, key_prefix="test:")
        async def expensive(redis_cache: RedisCache):
            return "fresh-result"

        result = await expensive(redis_cache=cache)
        assert result == "cached-result"

    @pytest.mark.asyncio
    async def test_cached_calls_fn_on_miss(self):
        cache, mock_redis = make_enabled_cache()
        mock_redis.get.return_value = None

        @cached(ttl=60, key_prefix="test:")
        async def expensive(redis_cache: RedisCache):
            return "fresh-result"

        result = await expensive(redis_cache=cache)
        assert result == "fresh-result"

    @pytest.mark.asyncio
    async def test_cached_without_redis_cache_calls_fn(self):
        """If no redis_cache found, function is called directly."""

        @cached(ttl=60)
        async def no_cache_fn():
            return "fallback"

        result = await no_cache_fn()
        assert result == "fallback"


# ---------------------------------------------------------------------------
# setup_redis_cache backward compat
# ---------------------------------------------------------------------------


class TestSetupRedisCache:
    @pytest.mark.asyncio
    async def test_setup_redis_cache_disabled_env(self, monkeypatch):
        monkeypatch.setenv("REDIS_ENABLED", "false")
        result = await setup_redis_cache()
        assert result is False
