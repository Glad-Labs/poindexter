"""
Unit tests for services/redis_cache.py

Tests RedisCache get/set/delete lifecycle, TTL handling, key prefixing,
JSON serialization/deserialization, connection failure handling, cache miss
behavior, get_or_set, incr, health_check, clear_all, delete_pattern,
exists, close, and the @cached decorator.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.redis_cache import CacheConfig, RedisCache, cached


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis():
    """A mock Redis client with async methods."""
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.setex = AsyncMock()
    r.delete = AsyncMock(return_value=1)
    r.exists = AsyncMock(return_value=1)
    r.keys = AsyncMock(return_value=[])
    r.incrby = AsyncMock(return_value=1)
    r.ping = AsyncMock()
    r.info = AsyncMock(return_value={
        "uptime_in_seconds": 3600,
        "used_memory": 1048576,
        "connected_clients": 2,
        "instantaneous_ops_per_sec": 50,
    })
    r.flushdb = AsyncMock()
    r.close = AsyncMock()
    return r


@pytest.fixture
def cache(mock_redis):
    """An enabled RedisCache backed by the mock Redis client."""
    return RedisCache(redis_instance=mock_redis, enabled=True)


@pytest.fixture
def disabled_cache():
    """A disabled RedisCache (no Redis instance)."""
    return RedisCache(redis_instance=None, enabled=False)


# ---------------------------------------------------------------------------
# CacheConfig
# ---------------------------------------------------------------------------


class TestCacheConfig:
    def test_default_ttl_is_positive(self):
        assert CacheConfig.DEFAULT_TTL > 0

    def test_prefix_constants_exist(self):
        assert CacheConfig.PREFIX_QUERY == "query:"
        assert CacheConfig.PREFIX_USER == "user:"
        assert CacheConfig.PREFIX_METRICS == "metrics:"
        assert CacheConfig.PREFIX_CONTENT == "content:"
        assert CacheConfig.PREFIX_MODEL == "model:"
        assert CacheConfig.PREFIX_SESSION == "session:"
        assert CacheConfig.PREFIX_TASK == "task:"

    def test_ttl_ordering(self):
        """Metrics TTL < User TTL < Query TTL < Default TTL < Content TTL < Model TTL."""
        assert CacheConfig.METRICS_CACHE_TTL < CacheConfig.USER_CACHE_TTL
        assert CacheConfig.USER_CACHE_TTL < CacheConfig.QUERY_CACHE_TTL
        assert CacheConfig.QUERY_CACHE_TTL < CacheConfig.DEFAULT_TTL
        assert CacheConfig.DEFAULT_TTL < CacheConfig.CONTENT_CACHE_TTL
        assert CacheConfig.CONTENT_CACHE_TTL < CacheConfig.MODEL_CACHE_TTL


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    @pytest.mark.asyncio
    async def test_available_when_enabled(self, cache):
        assert await cache.is_available() is True

    @pytest.mark.asyncio
    async def test_not_available_when_disabled(self, disabled_cache):
        assert await disabled_cache.is_available() is False

    @pytest.mark.asyncio
    async def test_not_available_when_no_instance(self):
        c = RedisCache(redis_instance=None, enabled=True)
        # enabled=True but no instance -> still False
        assert await c.is_available() is False


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


class TestGet:
    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self, cache, mock_redis):
        mock_redis.get.return_value = None
        result = await cache.get("missing_key")
        assert result is None
        mock_redis.get.assert_awaited_once_with("missing_key")

    @pytest.mark.asyncio
    async def test_cache_hit_returns_json_deserialized(self, cache, mock_redis):
        data = {"name": "test", "count": 42}
        mock_redis.get.return_value = json.dumps(data)
        result = await cache.get("json_key")
        assert result == data

    @pytest.mark.asyncio
    async def test_cache_hit_returns_plain_string(self, cache, mock_redis):
        mock_redis.get.return_value = "plain_value"
        result = await cache.get("str_key")
        assert result == "plain_value"

    @pytest.mark.asyncio
    async def test_cache_hit_json_list(self, cache, mock_redis):
        data = [1, 2, 3]
        mock_redis.get.return_value = json.dumps(data)
        result = await cache.get("list_key")
        assert result == data

    @pytest.mark.asyncio
    async def test_get_disabled_returns_none(self, disabled_cache):
        result = await disabled_cache.get("any_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_connection_error_returns_none(self, cache, mock_redis):
        mock_redis.get.side_effect = ConnectionError("Connection refused")
        result = await cache.get("error_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_generic_exception_returns_none(self, cache, mock_redis):
        mock_redis.get.side_effect = Exception("unexpected")
        result = await cache.get("error_key")
        assert result is None


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


class TestSet:
    @pytest.mark.asyncio
    async def test_set_dict_value(self, cache, mock_redis):
        data = {"key": "value"}
        result = await cache.set("dict_key", data, ttl=300)
        assert result is True
        mock_redis.setex.assert_awaited_once_with("dict_key", 300, json.dumps(data))

    @pytest.mark.asyncio
    async def test_set_list_value(self, cache, mock_redis):
        data = [1, 2, 3]
        result = await cache.set("list_key", data)
        assert result is True
        mock_redis.setex.assert_awaited_once_with(
            "list_key", CacheConfig.DEFAULT_TTL, json.dumps(data)
        )

    @pytest.mark.asyncio
    async def test_set_string_value(self, cache, mock_redis):
        result = await cache.set("str_key", "hello")
        assert result is True
        mock_redis.setex.assert_awaited_once_with(
            "str_key", CacheConfig.DEFAULT_TTL, "hello"
        )

    @pytest.mark.asyncio
    async def test_set_integer_value(self, cache, mock_redis):
        result = await cache.set("int_key", 42)
        assert result is True
        mock_redis.setex.assert_awaited_once_with(
            "int_key", CacheConfig.DEFAULT_TTL, "42"
        )

    @pytest.mark.asyncio
    async def test_set_uses_default_ttl_when_none(self, cache, mock_redis):
        await cache.set("key", "val")
        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == CacheConfig.DEFAULT_TTL

    @pytest.mark.asyncio
    async def test_set_uses_custom_ttl(self, cache, mock_redis):
        await cache.set("key", "val", ttl=60)
        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == 60

    @pytest.mark.asyncio
    async def test_set_disabled_returns_false(self, disabled_cache):
        result = await disabled_cache.set("key", "val")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_connection_error_returns_false(self, cache, mock_redis):
        mock_redis.setex.side_effect = ConnectionError("Connection refused")
        result = await cache.set("key", "val")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_generic_exception_returns_false(self, cache, mock_redis):
        mock_redis.setex.side_effect = Exception("unexpected")
        result = await cache.set("key", "val")
        assert result is False


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_existing_key(self, cache, mock_redis):
        mock_redis.delete.return_value = 1
        result = await cache.delete("existing_key")
        assert result is True
        mock_redis.delete.assert_awaited_once_with("existing_key")

    @pytest.mark.asyncio
    async def test_delete_missing_key(self, cache, mock_redis):
        mock_redis.delete.return_value = 0
        result = await cache.delete("missing_key")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_disabled_returns_false(self, disabled_cache):
        result = await disabled_cache.delete("key")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_connection_error_returns_false(self, cache, mock_redis):
        mock_redis.delete.side_effect = ConnectionError("Connection refused")
        result = await cache.delete("key")
        assert result is False


# ---------------------------------------------------------------------------
# delete_pattern
# ---------------------------------------------------------------------------


class TestDeletePattern:
    @pytest.mark.asyncio
    async def test_delete_pattern_with_matches(self, cache, mock_redis):
        mock_redis.keys.return_value = ["query:a", "query:b"]
        mock_redis.delete.return_value = 2
        result = await cache.delete_pattern("query:*")
        assert result == 2
        mock_redis.keys.assert_awaited_once_with("query:*")
        mock_redis.delete.assert_awaited_once_with("query:a", "query:b")

    @pytest.mark.asyncio
    async def test_delete_pattern_no_matches(self, cache, mock_redis):
        mock_redis.keys.return_value = []
        result = await cache.delete_pattern("nonexistent:*")
        assert result == 0

    @pytest.mark.asyncio
    async def test_delete_pattern_disabled_returns_zero(self, disabled_cache):
        result = await disabled_cache.delete_pattern("query:*")
        assert result == 0

    @pytest.mark.asyncio
    async def test_delete_pattern_error_returns_zero(self, cache, mock_redis):
        mock_redis.keys.side_effect = Exception("fail")
        result = await cache.delete_pattern("query:*")
        assert result == 0


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------


class TestExists:
    @pytest.mark.asyncio
    async def test_exists_true(self, cache, mock_redis):
        mock_redis.exists.return_value = 1
        assert await cache.exists("key") is True

    @pytest.mark.asyncio
    async def test_exists_false(self, cache, mock_redis):
        mock_redis.exists.return_value = 0
        assert await cache.exists("key") is False

    @pytest.mark.asyncio
    async def test_exists_disabled_returns_false(self, disabled_cache):
        assert await disabled_cache.exists("key") is False

    @pytest.mark.asyncio
    async def test_exists_error_returns_false(self, cache, mock_redis):
        mock_redis.exists.side_effect = Exception("fail")
        assert await cache.exists("key") is False


# ---------------------------------------------------------------------------
# get_or_set
# ---------------------------------------------------------------------------


class TestGetOrSet:
    @pytest.mark.asyncio
    async def test_returns_cached_value_on_hit(self, cache, mock_redis):
        mock_redis.get.return_value = json.dumps({"cached": True})
        fetch_fn = AsyncMock(return_value={"fresh": True})
        result = await cache.get_or_set("key", fetch_fn, ttl=300)
        assert result == {"cached": True}
        fetch_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fetches_and_caches_on_miss(self, cache, mock_redis):
        mock_redis.get.return_value = None
        fetch_fn = AsyncMock(return_value={"fresh": True})
        result = await cache.get_or_set("key", fetch_fn, ttl=300)
        assert result == {"fresh": True}
        fetch_fn.assert_awaited_once()
        mock_redis.setex.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_cache_none_result(self, cache, mock_redis):
        mock_redis.get.return_value = None
        fetch_fn = AsyncMock(return_value=None)
        result = await cache.get_or_set("key", fetch_fn)
        assert result is None
        mock_redis.setex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_fetch_fn(self, cache, mock_redis):
        mock_redis.get.return_value = None

        def sync_fetch():
            return {"sync": True}

        result = await cache.get_or_set("key", sync_fetch)
        assert result == {"sync": True}

    @pytest.mark.asyncio
    async def test_disabled_calls_fetch_directly(self, disabled_cache):
        fetch_fn = AsyncMock(return_value={"direct": True})
        result = await disabled_cache.get_or_set("key", fetch_fn)
        assert result == {"direct": True}
        fetch_fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disabled_calls_sync_fetch_directly(self, disabled_cache):
        def sync_fetch():
            return {"sync_direct": True}

        result = await disabled_cache.get_or_set("key", sync_fetch)
        assert result == {"sync_direct": True}

    @pytest.mark.asyncio
    async def test_fetch_exception_returns_none(self, cache, mock_redis):
        mock_redis.get.return_value = None
        fetch_fn = AsyncMock(side_effect=Exception("fetch failed"))
        result = await cache.get_or_set("key", fetch_fn)
        assert result is None


# ---------------------------------------------------------------------------
# incr
# ---------------------------------------------------------------------------


class TestIncr:
    @pytest.mark.asyncio
    async def test_incr_default_amount(self, cache, mock_redis):
        mock_redis.incrby.return_value = 5
        result = await cache.incr("counter")
        assert result == 5
        mock_redis.incrby.assert_awaited_once_with("counter", 1)

    @pytest.mark.asyncio
    async def test_incr_custom_amount(self, cache, mock_redis):
        mock_redis.incrby.return_value = 10
        result = await cache.incr("counter", amount=5)
        assert result == 10
        mock_redis.incrby.assert_awaited_once_with("counter", 5)

    @pytest.mark.asyncio
    async def test_incr_disabled_returns_amount(self, disabled_cache):
        result = await disabled_cache.incr("counter", amount=3)
        assert result == 3

    @pytest.mark.asyncio
    async def test_incr_error_returns_amount(self, cache, mock_redis):
        mock_redis.incrby.side_effect = Exception("fail")
        result = await cache.incr("counter", amount=7)
        assert result == 7


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self, cache, mock_redis):
        result = await cache.health_check()
        assert result["status"] == "healthy"
        assert result["available"] is True
        assert "uptime_seconds" in result
        assert "used_memory_mb" in result
        mock_redis.ping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disabled(self, disabled_cache):
        result = await disabled_cache.health_check()
        assert result["status"] == "disabled"
        assert result["available"] is False

    @pytest.mark.asyncio
    async def test_unhealthy_on_error(self, cache, mock_redis):
        mock_redis.ping.side_effect = ConnectionError("Connection refused")
        result = await cache.health_check()
        assert result["status"] == "unhealthy"
        assert result["available"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# clear_all
# ---------------------------------------------------------------------------


class TestClearAll:
    @pytest.mark.asyncio
    async def test_clear_all_success(self, cache, mock_redis):
        result = await cache.clear_all()
        assert result is True
        mock_redis.flushdb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clear_all_disabled(self, disabled_cache):
        result = await disabled_cache.clear_all()
        assert result is False

    @pytest.mark.asyncio
    async def test_clear_all_error(self, cache, mock_redis):
        mock_redis.flushdb.side_effect = Exception("fail")
        result = await cache.clear_all()
        assert result is False


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    @pytest.mark.asyncio
    async def test_close_with_instance(self, cache, mock_redis):
        await cache.close()
        mock_redis.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_without_instance(self, disabled_cache):
        # Should not raise
        await disabled_cache.close()

    @pytest.mark.asyncio
    async def test_close_error_does_not_raise(self, cache, mock_redis):
        mock_redis.close.side_effect = Exception("fail")
        # Should not raise
        await cache.close()


# ---------------------------------------------------------------------------
# create (factory)
# ---------------------------------------------------------------------------


class TestCreate:
    @pytest.mark.asyncio
    async def test_create_disabled_via_env(self):
        with patch.dict("os.environ", {"REDIS_ENABLED": "false"}):
            with patch("services.redis_cache.REDIS_AVAILABLE", True):
                instance = await RedisCache.create()
                assert instance._enabled is False

    @pytest.mark.asyncio
    async def test_create_when_redis_not_available(self):
        with patch("services.redis_cache.REDIS_AVAILABLE", False):
            instance = await RedisCache.create()
            assert instance._enabled is False
            assert instance._instance is None

    @pytest.mark.asyncio
    async def test_create_connection_failure_returns_disabled(self):
        mock_aioredis = MagicMock()
        mock_aioredis.from_url = AsyncMock(side_effect=ConnectionError("refused"))
        with patch("services.redis_cache.REDIS_AVAILABLE", True):
            with patch("services.redis_cache.aioredis", mock_aioredis):
                with patch.dict("os.environ", {"REDIS_ENABLED": "true"}):
                    instance = await RedisCache.create()
                    assert instance._enabled is False

    @pytest.mark.asyncio
    async def test_create_success(self):
        mock_redis_inst = AsyncMock()
        mock_redis_inst.ping = AsyncMock()
        mock_aioredis = MagicMock()
        mock_aioredis.from_url = AsyncMock(return_value=mock_redis_inst)
        with patch("services.redis_cache.REDIS_AVAILABLE", True):
            with patch("services.redis_cache.aioredis", mock_aioredis):
                with patch.dict("os.environ", {"REDIS_ENABLED": "true"}):
                    instance = await RedisCache.create()
                    assert instance._enabled is True
                    assert instance._instance is mock_redis_inst


# ---------------------------------------------------------------------------
# @cached decorator
# ---------------------------------------------------------------------------


class TestCachedDecorator:
    @pytest.mark.asyncio
    async def test_decorator_caches_result(self, cache, mock_redis):
        mock_redis.get.return_value = None  # cache miss

        @cached(ttl=120, key_prefix="test:")
        async def my_func(redis_cache):
            return {"result": "computed"}

        result = await my_func(cache)
        assert result == {"result": "computed"}
        mock_redis.setex.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_decorator_returns_cached(self, cache, mock_redis):
        mock_redis.get.return_value = json.dumps({"result": "cached"})

        @cached(ttl=120, key_prefix="test:")
        async def my_func(redis_cache):
            return {"result": "fresh"}

        result = await my_func(cache)
        assert result == {"result": "cached"}

    @pytest.mark.asyncio
    async def test_decorator_kwarg_redis_cache(self, cache, mock_redis):
        mock_redis.get.return_value = None

        @cached(ttl=60)
        async def my_func(redis_cache=None):
            return "value"

        result = await my_func(redis_cache=cache)
        assert result == "value"

    @pytest.mark.asyncio
    async def test_decorator_no_cache_falls_through(self):
        """When no RedisCache is passed, decorator calls function directly."""

        @cached(ttl=60)
        async def my_func():
            return "direct"

        result = await my_func()
        assert result == "direct"


# ---------------------------------------------------------------------------
# Key prefixing integration
# ---------------------------------------------------------------------------


class TestKeyPrefixing:
    """Verify that prefix constants work correctly with get/set."""

    @pytest.mark.asyncio
    async def test_prefixed_key_set_and_get(self, cache, mock_redis):
        key = f"{CacheConfig.PREFIX_QUERY}tasks:pending"
        mock_redis.get.return_value = json.dumps([1, 2, 3])

        await cache.set(key, [1, 2, 3], ttl=CacheConfig.QUERY_CACHE_TTL)
        result = await cache.get(key)

        mock_redis.setex.assert_awaited_once_with(
            "query:tasks:pending", CacheConfig.QUERY_CACHE_TTL, json.dumps([1, 2, 3])
        )
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_delete_pattern_with_prefix(self, cache, mock_redis):
        pattern = f"{CacheConfig.PREFIX_CONTENT}*"
        mock_redis.keys.return_value = ["content:a", "content:b"]
        mock_redis.delete.return_value = 2
        deleted = await cache.delete_pattern(pattern)
        assert deleted == 2
        mock_redis.keys.assert_awaited_once_with("content:*")
