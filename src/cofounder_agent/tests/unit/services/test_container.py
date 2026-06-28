"""
Unit tests for services/container.py

Covers two complementary container types in the same module:

* ``ServiceContainer`` — legacy name-keyed registry.
* ``AppContainer`` — composition root for the SiteConfig constructor-DI
  migration (PR 1 scaffold, design doc:
  ``docs/architecture/2026-05-28-site-config-di-migration.md``).

Also covers ``services/bootstrap.py::build_container``, the helper
every entry point will eventually call to construct an AppContainer
from an asyncpg pool.
"""

from functools import cached_property
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.bootstrap import build_container
from services.container import (
    AppContainer,
    ServiceContainer,
    get_service,
    initialize_services,
    register_service,
    service_container,
)
from services.site_config import SiteConfig


@pytest.fixture(autouse=True)
def reset_container():
    """Reset the global service_container before each test, restore after."""
    saved = service_container._services.copy()
    service_container.clear()
    yield
    service_container.clear()
    service_container._services.update(saved)


class TestServiceContainerInstances:
    """ServiceContainer instances are independent (no shared state)."""

    def test_each_instance_has_own_services(self):
        a = ServiceContainer()
        b = ServiceContainer()
        svc = MagicMock()
        a.register("alpha", svc)
        assert a.get("alpha") is svc
        assert b.get("alpha") is None

    def test_global_service_container_is_instance(self):
        assert isinstance(service_container, ServiceContainer)


class TestServiceContainerRegisterGet:
    """Register and retrieve services."""

    def test_register_and_get_service(self):
        svc = MagicMock()
        service_container.register("my_service", svc)
        assert service_container.get("my_service") is svc

    def test_get_missing_service_returns_none(self):
        assert service_container.get("nonexistent") is None

    def test_register_overwrites_previous(self):
        svc1 = MagicMock()
        svc2 = MagicMock()
        service_container.register("svc", svc1)
        service_container.register("svc", svc2)
        assert service_container.get("svc") is svc2

    def test_get_all_returns_copy(self):
        svc = MagicMock()
        service_container.register("alpha", svc)
        all_services = service_container.get_all()
        assert "alpha" in all_services
        # Modifying the returned dict should not affect the container
        all_services["alpha"] = None
        assert service_container.get("alpha") is svc

    def test_clear_removes_all_services(self):
        service_container.register("a", MagicMock())
        service_container.register("b", MagicMock())
        service_container.clear()
        assert service_container.get_all() == {}


class TestHelperFunctions:
    """Module-level helper functions delegate to global container."""

    def test_register_service_and_get_service(self):
        svc = MagicMock()
        register_service("helper_svc", svc)
        assert get_service("helper_svc") is svc

    def test_get_service_missing_returns_none(self):
        assert get_service("does_not_exist") is None

    def test_initialize_services_registers_all(self):
        db = MagicMock()
        cache = MagicMock()
        app = MagicMock()
        app.state = MagicMock()
        initialize_services(app, database=db, cache=cache)
        assert get_service("database") is db
        assert get_service("cache") is cache
        # initialize_services also stashes the container on app.state
        assert app.state.service_container is service_container

    def test_initialize_services_empty_kwargs(self):
        """Calling with no services should not raise."""
        app = MagicMock()
        app.state = MagicMock()
        initialize_services(app)
        assert get_service("database") is None


# ---------------------------------------------------------------------------
# AppContainer tests (SiteConfig constructor-DI migration, PR 1)
# ---------------------------------------------------------------------------


class _AppContainerWithSmokeService(AppContainer):
    """Subclass used by the cached_property pattern-demo test.

    Documents the shape every migration PR will follow when it adds a
    service: a ``@cached_property`` constructing the service from the
    container's wiring fields. Kept as a subclass (not added to the
    production ``AppContainer`` class) so the production container
    stays empty until services actually migrate.
    """

    @cached_property
    def _smoke(self) -> object:
        return object()


class TestAppContainerConstruction:
    """AppContainer constructs cleanly from its wiring fields."""

    def test_container_constructs_from_loaded_site_config(self):
        """Happy path: realistic SiteConfig + a pool sentinel."""
        site_config = SiteConfig(initial_config={"site_name": "Test Site"})
        pool = MagicMock(name="asyncpg_pool")
        container = AppContainer(site_config=site_config, pool=pool)
        assert container.site_config is site_config
        assert container.pool is pool
        # The wiring fields round-trip through the dataclass __init__
        # — confirms there's no hidden mutation in the constructor.
        assert container.site_config.get("site_name") == "Test Site"

    def test_container_constructs_with_empty_site_config(self):
        """Degenerate path: an empty SiteConfig still constructs.

        The container is pure wiring — services that need real
        settings will fail loud when they try to read them, but the
        container itself never gates on settings being present. This
        matters for tests + bootstrap-time call sites that genuinely
        have an empty config.
        """
        site_config = SiteConfig()  # no initial_config, no pool
        pool = MagicMock(name="asyncpg_pool")
        container = AppContainer(site_config=site_config, pool=pool)
        assert container.site_config is site_config
        assert container.pool is pool

    def test_container_hashable_contract(self):
        """Document the (un)hashability choice.

        ``AppContainer`` is a dataclass with mutable fields and the
        default ``eq=True, frozen=False`` — which by Python dataclass
        rules sets ``__hash__`` to ``None`` (unhashable). This is the
        right call: containers are passed by reference and identity-
        compared (``is``), never used as dict keys. Locking the
        contract in a test so a later ``@dataclass(frozen=True)``
        change is a deliberate, reviewed decision rather than a
        silent semantics drift.
        """
        site_config = SiteConfig()
        container = AppContainer(site_config=site_config, pool=MagicMock())
        with pytest.raises(TypeError, match="unhashable"):
            hash(container)

    def test_cached_property_pattern(self):
        """Demonstrate + verify the migration's service-property shape.

        Every migration PR adds a ``@cached_property`` like the
        ``_smoke`` one on the test-only subclass above. Two calls
        return the SAME instance — that's the memoisation the
        migration relies on so dependent services share a single
        instance of their dependency rather than reconstructing it
        per lookup.
        """
        site_config = SiteConfig()
        container = _AppContainerWithSmokeService(
            site_config=site_config, pool=MagicMock()
        )
        first = container._smoke
        second = container._smoke
        assert first is second


class TestAppContainerRedisCache:
    """``AppContainer.redis_cache`` cached_property — DI migration PR 5."""

    def test_redis_cache_wired_with_site_config(self):
        """The container constructs a RedisCache carrying its SiteConfig."""
        from services.redis_cache import RedisCache

        site_config = SiteConfig()
        container = AppContainer(site_config=site_config, pool=MagicMock())
        cache = container.redis_cache
        assert isinstance(cache, RedisCache)
        assert cache._site_config is site_config

    def test_redis_cache_memoised(self):
        """Two accesses return the same instance (cached_property)."""
        container = AppContainer(site_config=SiteConfig(), pool=MagicMock())
        assert container.redis_cache is container.redis_cache

    def test_redis_cache_defaults_disabled(self):
        """Bare construction returns a disabled (no Redis connection) cache.

        The connected variant lives in ``startup_manager._setup_redis_cache``
        via ``await RedisCache.create(...)``. The container's property
        just provides the dependency-wiring seam.
        """
        container = AppContainer(site_config=SiteConfig(), pool=MagicMock())
        assert container.redis_cache._enabled is False
        assert container.redis_cache._instance is None


class TestBuildContainer:
    """``services.bootstrap.build_container`` happy + sad paths."""

    async def test_build_container_loads_site_config_from_pool(self):
        """Builds a container; SiteConfig has the pool's rows in it."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                {"key": "site_name", "value": "Loaded Site"},
                {"key": "preferred_ollama_model", "value": "qwen2.5:14b"},
                {"key": "empty_key", "value": ""},  # filtered out
            ]
        )
        container = await build_container(pool)
        assert isinstance(container, AppContainer)
        assert container.pool is pool
        assert container.site_config.is_loaded is True
        assert container.site_config.get("site_name") == "Loaded Site"
        assert (
            container.site_config.get("preferred_ollama_model") == "qwen2.5:14b"
        )
        # Empty-string values shouldn't land in the cache, matching
        # SiteConfig.load semantics.
        assert "empty_key" not in container.site_config._config  # noqa: SLF001

    async def test_build_container_skips_secrets(self):
        """The SQL the helper runs filters secrets out at query time."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        await build_container(pool)
        # We assert on the SQL string the helper actually issued —
        # tracking the seam that keeps ``is_secret = true`` rows out
        # of the in-memory cache (those go through
        # ``SiteConfig.get_secret`` async per-call instead).
        pool.fetch.assert_awaited_once()
        sql_used = pool.fetch.await_args.args[0]
        assert "is_secret = false" in sql_used
        assert "app_settings" in sql_used

    async def test_build_container_raises_runtime_error_on_query_failure(self):
        """Fail-loud per feedback_no_silent_defaults — no empty-config fallback."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("connection reset"))
        with pytest.raises(RuntimeError) as excinfo:
            await build_container(pool)
        msg = str(excinfo.value)
        # The error message should echo the SQL it tried so an
        # operator reading the traceback can immediately see what
        # broke.
        assert "is_secret = false" in msg
        assert "app_settings" in msg
        assert "connection reset" in msg

    async def test_build_container_rejects_none_pool(self):
        """Passing pool=None is always a programming error — fail loud."""
        with pytest.raises(RuntimeError, match="pool=None"):
            await build_container(None)

    async def test_build_container_default_creates_fresh_instance(self):
        """Without ``site_config=``, the container owns a brand-new
        SiteConfig (the legacy CLI / brain / subprocess path)."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        existing = SiteConfig(initial_config={"site_name": "Caller Owned"})
        container = await build_container(pool)
        # The container did NOT adopt some unrelated instance.
        assert container.site_config is not existing

    async def test_build_container_reuses_passed_site_config(self):
        """When the caller passes its already-loaded SiteConfig, the
        container holds the SAME object — the worker hot-reload fix.

        This is the invariant that keeps the periodic ``reload_site_config``
        job (which refreshes the lifespan instance) visible to route
        handlers (which read ``container.site_config``).
        """
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[{"key": "enforce_niche_allowlist", "value": "true"}]
        )
        _site_cfg = SiteConfig()
        await _site_cfg.load(pool)
        container = await build_container(pool, site_config=_site_cfg)
        assert container.site_config is _site_cfg

    async def test_build_container_refreshes_reused_site_config_from_db(self):
        """Reusing an instance still runs the fail-loud probe and loads
        its rows into that instance (so a stale caller instance is
        brought current at build time)."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[{"key": "site_name", "value": "Fresh From DB"}]
        )
        stale = SiteConfig(initial_config={"site_name": "Stale"})
        container = await build_container(pool, site_config=stale)
        assert container.site_config is stale
        assert stale.get("site_name") == "Fresh From DB"
        assert stale.is_loaded is True

    async def test_build_container_reuse_preserves_fail_loud(self):
        """Passing a site_config does NOT bypass the fail-loud probe —
        a query failure still raises (feedback_no_silent_defaults)."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("connection reset"))
        with pytest.raises(RuntimeError, match="connection reset"):
            await build_container(pool, site_config=SiteConfig())


def test_container_exposes_gpu_registry():
    """AppContainer wires the VRAM pool auto-detector (2026-06-28).

    pool=None: GPURegistry reads VRAM totals from Prometheus, never the DB pool.
    """
    from services.container import AppContainer
    from services.gpu_registry import GPURegistry
    from services.site_config import SiteConfig

    c = AppContainer(site_config=SiteConfig(initial_config={}), pool=None)
    assert isinstance(c.gpu_registry, GPURegistry)
    assert c.gpu_registry is c.gpu_registry  # cached
