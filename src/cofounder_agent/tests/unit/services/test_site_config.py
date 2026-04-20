"""
Unit tests for services/site_config.py

Tests DB-first config loading, env var fallback, and type helpers.
All database calls are mocked — no real asyncpg pool required.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

from services.site_config import SiteConfig


@pytest.fixture
def config():
    """Fresh SiteConfig instance for each test."""
    return SiteConfig()


class TestGetBeforeLoad:
    """Before load() is called, should fall back to env vars or defaults."""

    def test_returns_default_when_not_loaded(self, config):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SITE_NAME", None)
            assert config.get("site_name", "fallback") == "fallback"

    def test_returns_env_var_when_not_loaded(self, config):
        with patch.dict(os.environ, {"SITE_NAME": "From Env"}):
            assert config.get("site_name", "default") == "From Env"

    def test_is_loaded_false_before_load(self, config):
        assert config.is_loaded is False


class TestLoadFromDB:
    async def test_load_populates_config(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"key": "site_name", "value": "Test Site"},
            {"key": "site_domain", "value": "test.com"},
        ])
        loaded = await config.load(pool)
        assert loaded == 2
        assert config.is_loaded is True
        assert config.get("site_name") == "Test Site"
        assert config.get("site_domain") == "test.com"

    async def test_load_skips_empty_values(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"key": "filled", "value": "yes"},
            {"key": "empty", "value": ""},
        ])
        await config.load(pool)
        assert config.get("filled") == "yes"
        assert config.get("empty", "default") == "default"

    async def test_load_with_none_pool(self, config):
        loaded = await config.load(None)
        assert loaded == 0
        assert config.is_loaded is False

    async def test_load_handles_db_error(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=Exception("connection refused"))
        loaded = await config.load(pool)
        assert loaded == 0


class TestGetPriority:
    """DB value takes priority over env var."""

    async def test_db_overrides_env(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"key": "site_name", "value": "From DB"},
        ])
        await config.load(pool)
        with patch.dict(os.environ, {"SITE_NAME": "From Env"}):
            assert config.get("site_name") == "From DB"

    async def test_env_used_when_not_in_db(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        await config.load(pool)
        with patch.dict(os.environ, {"MISSING_KEY": "from env"}):
            assert config.get("missing_key", "default") == "from env"


class TestTypeHelpers:
    async def test_get_int(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"key": "count", "value": "42"}])
        await config.load(pool)
        assert config.get_int("count") == 42

    async def test_get_int_default(self, config):
        assert config.get_int("missing", 10) == 10

    async def test_get_int_invalid(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"key": "bad", "value": "abc"}])
        await config.load(pool)
        assert config.get_int("bad", 5) == 5

    async def test_get_float(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"key": "rate", "value": "0.256"}])
        await config.load(pool)
        assert abs(config.get_float("rate") - 0.256) < 1e-6

    async def test_get_bool_true(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"key": "flag", "value": "true"}])
        await config.load(pool)
        assert config.get_bool("flag") is True

    async def test_get_bool_false(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"key": "flag", "value": "false"}])
        await config.load(pool)
        assert config.get_bool("flag") is False

    async def test_get_list(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"key": "items", "value": "a,b,c"}])
        await config.load(pool)
        assert config.get_list("items") == ["a", "b", "c"]

    async def test_get_list_empty(self, config):
        assert config.get_list("missing") == []


class TestAll:
    async def test_all_returns_loaded_config(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"key": "a", "value": "1"},
            {"key": "b", "value": "2"},
        ])
        await config.load(pool)
        all_config = config.all()
        assert all_config == {"a": "1", "b": "2"}


class TestInitialConfig:
    """``SiteConfig(initial_config={...})`` unblocks per-test isolation —
    seeds values without having to touch the module singleton or mock
    a pool. Gitea #242 foundation."""

    def test_initial_config_populates_values(self):
        cfg = SiteConfig(initial_config={"site_url": "https://test", "site_name": "Test"})
        assert cfg.get("site_url") == "https://test"
        assert cfg.get("site_name") == "Test"

    def test_initial_config_marks_loaded(self):
        """When we hand over a pre-populated dict, the instance is
        effectively loaded — callers that gate on ``is_loaded`` should
        see True so they don't short-circuit to env fallback."""
        cfg = SiteConfig(initial_config={"k": "v"})
        assert cfg.is_loaded is True

    def test_empty_initial_config_leaves_unloaded(self):
        cfg = SiteConfig(initial_config={})
        assert cfg.is_loaded is False

    def test_default_constructor_still_works(self):
        """Backwards compat: ``SiteConfig()`` with no args still starts
        empty and unloaded, matching pre-#242 behavior."""
        cfg = SiteConfig()
        assert cfg._config == {}
        assert cfg.is_loaded is False

    def test_initial_config_isolates_between_instances(self):
        """The fundamental Gitea #242 win: two independent instances don't
        see each other's state, so tests can't pollute each other."""
        a = SiteConfig(initial_config={"shared": "A"})
        b = SiteConfig(initial_config={"shared": "B"})
        assert a.get("shared") == "A"
        assert b.get("shared") == "B"

    def test_pool_kwarg_stored_without_load(self):
        """Passing a pool to the constructor doesn't auto-load (load is
        async), but it IS retained so get_secret() can use it later."""
        pool = AsyncMock()
        cfg = SiteConfig(pool=pool)
        assert cfg._pool is pool
        # But _config stays empty — load() hasn't run.
        assert cfg._config == {}
