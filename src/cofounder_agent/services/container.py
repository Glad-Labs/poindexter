"""
Service Container for Glad Labs AI Co-Founder

This module hosts two complementary container types:

1. ``ServiceContainer`` — a generic name -> instance registry kept for
   legacy callers (route handlers stash it on ``app.state``, a small
   number of services look it up by name).
2. ``AppContainer`` — the composition root introduced by the SiteConfig
   constructor-DI migration (design doc:
   ``docs/architecture/2026-05-28-site-config-di-migration.md``).
   Constructed once per entry point (worker lifespan, Prefect
   subprocess, CLI command, brain daemon, test fixture). Services that
   need ``SiteConfig`` reach it through this container — no
   module-level singletons. During the migration period it starts
   essentially empty; each migration PR adds a ``cached_property`` for
   the service being converted.

Both types coexist throughout the migration. The cleanup PR at the
end of the SiteConfig DI migration will retire ``ServiceContainer``
along with ``services/di_wiring.py`` and the per-module
``set_site_config`` setters.
"""

from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from services.site_config import SiteConfig

if TYPE_CHECKING:
    from services.decorators import Decorators


class ServiceContainer:
    """Centralized service registry.

    Instance-scoped: each container instance has its own ``_services``
    dict. No singleton enforcement — callers choose between the
    module-level instance (legacy callers) and ``app.state`` (routes).
    """

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}

    def register(self, name: str, service: Any) -> None:
        """Register a service in the container."""
        self._services[name] = service

    def get(self, name: str) -> Any:
        """Get a service from the container."""
        return self._services.get(name)

    def get_all(self) -> dict[str, Any]:
        """Get all registered services."""
        return self._services.copy()

    def clear(self) -> None:
        """Clear all registered services."""
        self._services.clear()


# Module-level instance — legacy callers (main.py lifespan,
# a handful of lazily-imported services) use this. New code should
# prefer ``request.app.state.service_container`` or a fresh instance.
service_container = ServiceContainer()


def get_service(name: str) -> Any:
    """Get a service from the module-level container."""
    return service_container.get(name)


def register_service(name: str, service: Any) -> None:
    """Register a service in the module-level container."""
    service_container.register(name, service)


def initialize_services(app: FastAPI, **services) -> None:
    """Register services in the module-level container and stash the
    container on ``app.state.service_container`` so route dependencies
    can access it without importing the module-level instance."""
    for name, service in services.items():
        register_service(name, service)
    app.state.service_container = service_container


# ---------------------------------------------------------------------------
# AppContainer — composition root for the SiteConfig constructor-DI migration
# ---------------------------------------------------------------------------


@dataclass
class AppContainer:
    """Composition root: every service the app needs, wired with deps.

    Constructed once per entry point (worker lifespan, Prefect subprocess,
    CLI command, brain daemon, test fixture). Services that need
    ``SiteConfig`` reach it through this container — no module-level
    singletons.

    During the migration period this container starts empty; each
    migration PR adds a ``cached_property`` for the service being
    converted. When all per-module ``site_config`` singletons under
    ``services/*.py`` are migrated and the cleanup PR lands, this is
    the only place wiring happens.

    Why a dataclass: gives us free ``__init__(site_config=..., pool=...)``
    + ``__repr__`` + structural equality on the wiring fields without
    boilerplate. ``cached_property`` works on dataclasses because
    instances are mutable by default (eq=True / frozen=False — the
    default) and have ``__dict__`` for the descriptor cache.

    Why not hashable: mutability matters more than hashability here.
    Containers are passed by reference, not used as dict keys; tests
    that want identity-based comparisons use ``is``. Explicitly:
    ``eq=True, frozen=False`` (defaults) means ``__hash__`` is set to
    ``None`` — see ``test_container_hashable_contract``.

    cached_property service entries are added by subsequent migration
    PRs. Example shape for when services start migrating::

        @cached_property
        def topic_batch_service(self) -> "TopicBatchService":
            return TopicBatchService(
                site_config=self.site_config,
                pool=self.pool,
                internal_rag_source=self.internal_rag_source,
            )
    """

    site_config: SiteConfig
    # asyncpg.Pool — kept loosely typed to avoid the asyncpg import
    # here. Concrete typing happens on the caller side; the container
    # just holds the reference and passes it down to services.
    pool: Any

    # -- Migrated services --------------------------------------------------
    #
    # Added by SiteConfig DI migration PRs. Each cached_property
    # constructs the underlying service once per container instance,
    # wired with the container's ``site_config`` (and ``pool`` where
    # relevant). Order matches the migration PR ledger; new entries get
    # appended.

    @cached_property
    def decorators(self) -> "Decorators":
        """Performance-monitoring decorators (PR 6, 2026-05-28).

        Option B (facade) per the migration design doc. Constructing this
        property also pins the constructed ``Decorators`` instance onto
        ``services.decorators._default_decorators`` so the module-level
        ``@log_query_performance(...)`` decoration sites under
        ``services/*_db.py`` route through this container's ``SiteConfig``.

        Side-effect on first read is deliberate: there's no per-DB-layer
        injection seam for the ~50 existing decorator call sites, and we
        want fresh-DB-loaded settings to take effect the moment the
        container is built — which happens once per entry-point boot.
        Subsequent containers (test fixtures, etc.) overwrite the pin.
        """
        from services.decorators import Decorators, set_default_decorators

        instance = Decorators(site_config=self.site_config)
        set_default_decorators(instance)
        return instance
