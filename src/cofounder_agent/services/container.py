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

from services.citation_verifier import CitationVerifier
from services.r2_upload_service import R2UploadService
from services.redis_cache import RedisCache
from services.research_quality_service import ResearchQualityService
from services.retention_janitor import RetentionJanitor
from services.revalidation_service import RevalidationService
from services.seed_url_fetcher import SeedURLFetcher
from services.site_config import SiteConfig
from services.telegram_config import TelegramConfig
from services.title_originality_external import TitleOriginalityExternalChecker
from services.url_scraper import URLScraper
from services.url_validator import URLValidator
from services.web_research import WebResearcher

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
    def telegram_config(self) -> TelegramConfig:
        """Telegram bot credentials resolver (DI migration PR 3).

        First service to migrate to constructor DI. ``TelegramConfig``
        wraps ``SiteConfig`` and exposes ``get_telegram_chat_id`` /
        ``get_telegram_bot_token`` / ``telegram_configured``.
        """
        return TelegramConfig(site_config=self.site_config)

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

    @cached_property
    def redis_cache(self) -> RedisCache:
        """Bare ``RedisCache`` wired to the container's SiteConfig.

        Returns a synchronously-constructed instance with no Redis
        connection — every cache operation no-ops cleanly until a
        caller upgrades the instance via :meth:`RedisCache.create`.

        The worker lifespan + brain daemon construct the *connected*
        instance via ``await RedisCache.create(site_config=...)`` and
        attach it to ``app.state.redis_cache``; route handlers that
        need the connected cache should reach for that. This property
        exists so tests + lightweight callers can ask the container
        for a RedisCache without async setup, and so the DI migration
        has a single source of truth for "where does RedisCache wire
        up its SiteConfig dependency".
        """
        return RedisCache(site_config=self.site_config)

    @cached_property
    def r2_upload_service(self) -> R2UploadService:
        """Object-store uploader (R2 / S3 / B2 / MinIO) — SiteConfig DI migration PR 4."""
        return R2UploadService(site_config=self.site_config)

    @cached_property
    def url_validator(self) -> URLValidator:
        """Broken-link / hallucinated-URL checker (#272 leaf batch 1, 2026-05-29).

        Wraps ``SiteConfig`` for the ``site_name`` (User-Agent) and
        ``site_domain`` (internal-skip) reads. The ``url_validation``
        pipeline stage builds its own per-run instance from the context
        SiteConfig (caller-bridge); this property is the canonical wiring
        seam for container-aware callers + tests.
        """
        return URLValidator(site_config=self.site_config)

    @cached_property
    def url_scraper(self) -> URLScraper:
        """URL → structured-content scraper for topic seeding (#272 leaf batch 1).

        Reads ``site_contact_url`` / ``scraper_bot_name`` (User-Agent),
        ``arxiv_base_url``, and the ``url_scraper_allow_internal_ips``
        SSRF override from ``SiteConfig``. ``routes/topics_routes.py``
        builds its own per-request instance from the DI'd SiteConfig
        (caller-bridge); this property is the canonical wiring seam.
        """
        return URLScraper(site_config=self.site_config)

    @cached_property
    def revalidation_service(self) -> RevalidationService:
        """Next.js ISR revalidation (#272 leaf batch 3, 2026-05-29).

        Wraps ``SiteConfig`` for the revalidate URL chain + the async
        ``revalidate_secret`` read. Every publish path (publish_service,
        /go-live route, scheduled_publisher, the operator-facing
        /api/revalidate-cache route) reaches the helpers via a per-call
        instance built from its own lifespan-bound SiteConfig
        (caller-bridge); this property is the canonical wiring seam. The
        process-wide httpx connection pool stays module-level in
        ``services.revalidation_service``.
        """
        return RevalidationService(site_config=self.site_config)

    @cached_property
    def retention_janitor(self) -> RetentionJanitor:
        """Retention pruner for unbounded high-churn tables (#272 leaf batch 3).

        Reads per-table ``retention_days__<table>`` keys + the
        ``retention_janitor_interval_hours`` loop interval from
        ``SiteConfig``. ``startup_manager`` builds its own instance from
        the lifespan-bound SiteConfig to launch the background loop
        (caller-bridge); this property is the canonical wiring seam.
        """
        return RetentionJanitor(site_config=self.site_config)

    @cached_property
    def web_research(self) -> WebResearcher:
        """Free DuckDuckGo web research + content extraction (#272 leaf batch 2).

        Reads the ``web_research_*`` tunables (max_concurrent /
        search_timeout_seconds / fetch_timeout_seconds / max_content_chars)
        from ``SiteConfig``. ``research_service`` / ``multi_model_qa`` /
        ``title_generation`` / the ``web_search`` topic source build their
        own per-call instance from the context SiteConfig (caller-bridge);
        this property is the canonical wiring seam for container-aware
        callers + tests.
        """
        return WebResearcher(site_config=self.site_config)

    @cached_property
    def citation_verifier(self) -> CitationVerifier:
        """External-citation reachability checker (#272 leaf batch 2).

        Reads ``crawler_contact_url`` from ``SiteConfig`` for the HEAD-probe
        User-Agent. ``multi_model_qa`` builds its own per-review instance
        from the module SiteConfig (caller-bridge). The shared
        ``http_client`` fan-out plumbing stays module-level (wired by
        ``services/http_client.py``); this property is the SiteConfig wiring
        seam for container-aware callers + tests.
        """
        return CitationVerifier(site_config=self.site_config)

    @cached_property
    def seed_url_fetcher(self) -> SeedURLFetcher:
        """Single-URL → topic-seed fetcher (#272 leaf batch 2).

        Reads ``seed_url_fetch_timeout_seconds`` / ``seed_url_user_agent`` /
        ``seed_url_max_bytes`` from ``SiteConfig``. ``routes/task_routes.py``
        builds its own per-request instance from the DI'd SiteConfig
        (caller-bridge); this property is the canonical wiring seam.
        """
        return SeedURLFetcher(site_config=self.site_config)

    @cached_property
    def title_originality_external(self) -> TitleOriginalityExternalChecker:
        """External-article title-duplicate checker (#272 leaf batch 2).

        Reads ``title_originality_external_check_enabled`` /
        ``title_originality_external_penalty`` /
        ``title_originality_cache_ttl_hours`` from ``SiteConfig``.
        ``title_generation`` builds its own per-call instance from the
        module SiteConfig (caller-bridge). The process-local result cache
        stays module-level (shared across instances by design); this
        property is the canonical SiteConfig wiring seam.
        """
        return TitleOriginalityExternalChecker(site_config=self.site_config)

    @cached_property
    def research_quality_service(self) -> ResearchQualityService:
        """Research-source filter / dedup / credibility scorer (#272 leaf batch 4).

        Reads the ``research_*_weight`` scoring tunables +
        ``research_min_snippet_length`` / ``research_min_snippet_words`` /
        ``research_dedup_similarity_threshold`` + the
        ``research_tier1_domains`` / ``research_tier2_domains`` overrides
        from ``SiteConfig`` at construction. ``research_service`` builds its
        own per-call instance from the context SiteConfig (caller-bridge);
        this property is the canonical wiring seam for container-aware
        callers + tests.

        ``WebhookDeliveryService`` migrated in the same batch but needs a
        runtime ``pool`` the container can't supply at build time, so it has
        no cached_property here — ``main.py``'s lifespan constructs it as
        ``WebhookDeliveryService(pool, site_config=self.site_config)``
        (caller-bridge).
        """
        return ResearchQualityService(site_config=self.site_config)
