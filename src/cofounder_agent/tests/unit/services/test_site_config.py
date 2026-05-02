"""
Unit tests for services/site_config.py

Tests DB-first config loading, env var fallback, and type helpers.
All database calls are mocked — no real asyncpg pool required.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

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


# ---------------------------------------------------------------------------
# Round-2 fills: previously-uncovered branches (lines 107-122 reload,
# 136-153 get_secret, 161-167 require, 202-203 get_float exception).
# ---------------------------------------------------------------------------


class TestReload:
    """``SiteConfig.reload`` re-reads from the DB atomically (~lines 102-122)."""

    async def test_reload_replaces_existing_config(self, config):
        # Seed with one value
        pool1 = AsyncMock()
        pool1.fetch = AsyncMock(return_value=[
            {"key": "site_name", "value": "Old"},
            {"key": "old_only", "value": "stale"},
        ])
        await config.load(pool1)
        assert config.get("old_only") == "stale"

        # Reload with a different snapshot — old key disappears
        pool2 = AsyncMock()
        pool2.fetch = AsyncMock(return_value=[
            {"key": "site_name", "value": "New"},
            {"key": "new_only", "value": "fresh"},
        ])
        loaded = await config.reload(pool2)
        assert loaded == 2
        assert config.get("site_name") == "New"
        assert config.get("new_only") == "fresh"
        # The old-only key should be gone from the new snapshot
        assert config.get("old_only", "MISSING") == "MISSING"

    async def test_reload_skips_empty_values(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"key": "filled", "value": "yes"},
            {"key": "empty", "value": ""},
        ])
        loaded = await config.reload(pool)
        assert loaded == 1
        assert config.get("filled") == "yes"

    async def test_reload_with_none_pool_returns_zero(self, config):
        loaded = await config.reload(None)
        assert loaded == 0

    async def test_reload_handles_db_error(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("connection lost"))
        loaded = await config.reload(pool)
        assert loaded == 0


class TestGetSecret:
    """Async secret-fetch path — secrets bypass the in-memory cache."""

    async def test_get_secret_returns_value_from_pool(self, config):
        """Happy path: the underlying plugins.secrets.get_secret returns a value."""
        pool = AsyncMock()
        # Async context manager on pool.acquire()
        conn = AsyncMock()
        acquire_ctx = AsyncMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = lambda: acquire_ctx

        cfg = SiteConfig(pool=pool)
        with patch("plugins.secrets.get_secret",
                   new=AsyncMock(return_value="s3cret-value")):
            val = await cfg.get_secret("api_key")
        assert val == "s3cret-value"

    async def test_get_secret_falls_back_to_env_when_db_returns_none(self, config):
        pool = AsyncMock()
        conn = AsyncMock()
        acquire_ctx = AsyncMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = lambda: acquire_ctx
        cfg = SiteConfig(pool=pool)

        with patch("plugins.secrets.get_secret",
                   new=AsyncMock(return_value=None)):
            with patch.dict(os.environ, {"MY_SECRET_KEY": "from-env"}):
                val = await cfg.get_secret("my_secret_key")
        assert val == "from-env"

    async def test_get_secret_falls_back_to_default_on_db_error(self, config):
        """A DB exception must not crash callers — log + fall through to env/default."""
        pool = AsyncMock()
        # acquire() returns a context that raises on enter -> exception path
        acquire_ctx = AsyncMock()
        acquire_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("pool exhausted"))
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = lambda: acquire_ctx
        cfg = SiteConfig(pool=pool)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MISSING_SECRET", None)
            val = await cfg.get_secret("missing_secret", default="fallback-val")
        assert val == "fallback-val"

    async def test_get_secret_returns_default_when_no_pool_no_env(self, config):
        cfg = SiteConfig()  # no pool
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NEVER_SET_KEY", None)
            val = await cfg.get_secret("never_set_key", default="default-x")
        assert val == "default-x"

    async def test_get_secret_empty_string_treated_as_missing(self, config):
        """Empty value from DB is not a real value — fall through to env."""
        pool = AsyncMock()
        conn = AsyncMock()
        acquire_ctx = AsyncMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = lambda: acquire_ctx
        cfg = SiteConfig(pool=pool)

        with patch("plugins.secrets.get_secret",
                   new=AsyncMock(return_value="")):
            with patch.dict(os.environ, {"EMPTY_KEY": "from-env"}):
                val = await cfg.get_secret("empty_key", default="default-x")
        assert val == "from-env"


class TestRequire:
    """Required-setting accessor — raises if not configured."""

    def test_require_returns_db_value(self):
        cfg = SiteConfig(initial_config={"site_url": "https://x"})
        assert cfg.require("site_url") == "https://x"

    def test_require_falls_back_to_env_var(self):
        cfg = SiteConfig()
        with patch.dict(os.environ, {"REQUIRED_FROM_ENV": "env-val"}):
            assert cfg.require("required_from_env") == "env-val"

    def test_require_raises_when_unset_anywhere(self):
        cfg = SiteConfig()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DEFINITELY_NOT_SET_XYZ", None)
            with pytest.raises(RuntimeError, match="not configured"):
                cfg.require("definitely_not_set_xyz")

    def test_require_error_includes_env_key_name(self):
        cfg = SiteConfig()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MY_REQUIRED_SETTING", None)
            with pytest.raises(RuntimeError) as exc:
                cfg.require("my_required_setting")
        assert "MY_REQUIRED_SETTING" in str(exc.value)


class TestGetFloatException:
    async def test_get_float_invalid_returns_default(self, config):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"key": "rate", "value": "not-a-float"}])
        await config.load(pool)
        assert config.get_float("rate", 0.5) == 0.5

    def test_get_float_missing_returns_default(self):
        cfg = SiteConfig()
        assert cfg.get_float("missing_key", 1.5) == 1.5
