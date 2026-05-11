"""Shared fixtures for all unit tests.

Two layers of isolation:

1. **site_config seed** — the legacy behavior of this file. Populates
   brand-identity keys so tests that call ``site_config.require("...")``
   don't blow up on a missing-key error.

2. **Autouse isolation fixtures** (Gitea #241) — reset module-level
   singletons + restore ``os.environ`` between every test. Fixes the
   test-ordering pollution that showed up as 85 failures + 13 errors
   in full-suite runs where every failing test passes in isolation.

Why the isolation fixtures are necessary:

- ``services/content_task_store.py`` holds a module-level
  ``_content_task_store`` singleton.
- ``services/text_utils.py`` holds a module-level ``_slug_cache`` set
  updated by the content-generation stage.
- ``services/image_style_rotation.py`` holds a module-level deque of
  recently-picked featured-image styles.
- ``plugins/registry.py`` caches entry-point discovery via lru_cache.
- Several test modules set ``os.environ`` values (``DATABASE_URL``,
  ``PEXELS_API_KEY``, etc.) that leak into every subsequent test.

Long-term fix (tracked separately): move each singleton to
FastAPI-Depends-injected factory so tests pass explicit instances.
For now the fixtures reset the shared state between tests.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# Glad-Labs/glad-labs-stack#410 follow-up. The Prefect Phase-0 cutover
# (commit 669bf1ef) made several services import
# ``services.flows.content_generation`` transitively (notably
# ``services.task_executor``). That module's ``@flow`` / ``@task``
# decorators trigger Prefect's ephemeral-server fallback when
# ``PREFECT_API_URL`` is unset — a uvicorn subprocess on a random
# port whose ``GlobalEventLoopThread`` never drains at pytest
# teardown, hanging the whole ``services (forked)`` step in CI for
# the full 30-minute workflow timeout. Setting these env vars at the
# top of the unit-tier conftest (before *any* services import) blocks
# the ephemeral fallback for every unit test. Tests that genuinely
# need a Prefect API still get one via the per-module
# ``prefect_test_harness`` fixture in ``services/flows/conftest.py``.
os.environ.setdefault("PREFECT_SERVER_ALLOW_EPHEMERAL_MODE", "false")
os.environ.setdefault("PREFECT_API_URL", "http://127.0.0.1:1/api")

import pytest
import pytest_asyncio

from services.site_config import SiteConfig

# Single shared SiteConfig instance for the unit-test process. This
# replaces the deleted module-level singleton at
# ``services.site_config.site_config``. Every per-module ``site_config``
# attr (post-#330 sweep) is repointed at this instance below so tests
# that seed brand-config values via ``_TEST_BRAND_CONFIG`` see them
# everywhere.
site_config = SiteConfig()

# ---------------------------------------------------------------------------
# Layer 1 — brand-identity seed (legacy)
# ---------------------------------------------------------------------------


_TEST_BRAND_CONFIG = {
    "site_name": "Test Site",
    "site_url": "https://www.test-site.example.com",
    "site_domain": "test-site.example.com",
    "company_name": "Test Company, LLC",
    "support_email": "hello@test.example.com",
    "privacy_email": "privacy@test.example.com",
    "newsletter_email": "news@test.example.com",
    "podcast_name": "Test Podcast",
    "podcast_description": "A test podcast feed",
    "video_feed_name": "Test Video",
    "site_title": "Test Site",
    "site_tagline": "Testing",
    "site_description": "Test site description",
    "owner_name": "Tester",
    "owner_email": "owner@test.example.com",
}

for _key, _value in _TEST_BRAND_CONFIG.items():
    site_config._config.setdefault(_key, _value)


# Post-#330 sweep: every module that previously read the global
# ``services.site_config.site_config`` singleton now owns its own
# ``site_config: SiteConfig`` attribute (default fresh empty instance,
# wired by main.py at lifespan startup). Tests that drive those
# modules directly need the SAME brand seed values present on each
# module-level instance — otherwise ``site_config.require("site_url")``
# raises in code paths that were previously satisfied by the test
# fixture's seed of the global singleton.
#
# Walking every module is too expensive (and triggers heavy imports);
# instead we point each known module's ``site_config`` attribute at the
# SAME instance the conftest seeds. This preserves the previous
# semantic (one shared seed surfaces everywhere) while keeping the new
# DI seam in place. ``set_site_config()`` is the canonical wiring path
# main.py uses; we use it here for parity.
_SHARED_TEST_MODULES = (
    "services.publish_service",
    "services.image_service",
    "services.content_router_service",
    "services.seo_content_generator",
    "services.image_decision_agent",
    "services.podcast_service",
    "services.video_service",
    "services.newsletter_service",
    "services.content_validator",
    "services.multi_model_qa",
    "services.research_service",
    "services.research_quality_service",
    "services.seed_url_fetcher",
    "services.self_review",
    "services.title_generation",
    "services.title_originality_external",
    "services.internal_rag_source",
    "services.scheduled_publisher",
    "services.topic_ranking",
    "services.topic_batch_service",
    "services.database_service",
    "services.quality_scorers",
    "services.quality_models",
    "services.quality_service",
    "services.validator_config",
    "services.template_runner",
    "services.pipeline_architect",
    "services.prompt_manager",
    "services.retention_janitor",
    "services.ai_content_generator",
    "services.task_executor",
    "services.social_poster",
    "services.gpu_scheduler",
    "services.decorators",
    "services.ollama_client",
    "services.url_validator",
    "services.url_scraper",
    "services.web_research",
    "services.redis_cache",
    "services.r2_upload_service",
    "services.revalidation_service",
    "services.static_export_service",
    "services.telegram_config",
    "services.webhook_delivery_service",
    "services.devto_service",
    "utils.route_utils",
)


def _share_test_site_config() -> None:
    """Point every migrated module's ``site_config`` attribute at the
    same instance the conftest seeded with _TEST_BRAND_CONFIG."""
    import importlib
    for _modname in _SHARED_TEST_MODULES:
        try:
            _mod = importlib.import_module(_modname)
        except Exception:
            continue
        _setter = getattr(_mod, "set_site_config", None)
        if callable(_setter):
            try:
                _setter(site_config)
            except Exception:
                pass


_share_test_site_config()


# ---------------------------------------------------------------------------
# Layer 2 — environment isolation
# ---------------------------------------------------------------------------


# Some production modules (notably ``agents/content_agent/config.py``)
# evaluate ``os.environ["DATABASE_URL"]`` at import time and raise
# ValueError if unset. We seed a dummy DSN at collection time so
# test-module imports don't crash when DATABASE_URL isn't already set
# from the developer's shell environment.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")


# Env var names that tests / production code mutate and that leak between
# test cases if not restored. Extend as new leaks surface.
_ENV_KEYS_TO_ISOLATE = (
    "DATABASE_URL",
    "PEXELS_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "OLLAMA_URL",
    "OLLAMA_BASE_URL",
    "API_TOKEN",
    "SECRET_KEY",
    "JWT_SECRET_KEY",
    "REVALIDATE_SECRET",
    "SITE_URL",
    "SITE_NAME",
    "SITE_DOMAIN",
    "ENVIRONMENT",
    "R2_PUBLIC_URL",
)


@pytest.fixture(autouse=True)
def _reset_env_between_tests(monkeypatch):
    """Snapshot + restore os.environ for a curated set of keys."""
    snapshot: dict[str, str | None] = {
        key: os.environ.get(key) for key in _ENV_KEYS_TO_ISOLATE
    }
    yield
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


# ---------------------------------------------------------------------------
# Layer 3 — module-level singleton resets
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singletons_between_tests():
    """Clear shared mutable state exposed by various services modules.

    Each reset targets a specific known leak. Module imports are lazy +
    wrapped in try/except so tests that don't pull in the module don't
    pay the import cost, and optional modules that fail to import don't
    break unrelated tests.
    """
    yield

    # (content_task_store singleton removed in Phase G1 — no reset needed)

    # (text_utils _slug_cache singleton removed in Phase G1 —
    # scrub_fabricated_links now takes known_slugs as a parameter)

    # (image_style_rotation singleton removed in Phase G1 —
    # ImageStyleTracker is now a class; workers inject an instance.)

    # plugin registry entry-point cache (lru_cache).
    #
    # GH#311: some service tests stub ``sys.modules["plugins"]`` with a
    # bare ``types.ModuleType("plugins")`` (e.g. ``test_stores_cli`` to
    # avoid dragging in apscheduler/etc.). When such a polluter mutates
    # ``sys.modules`` directly (without monkeypatch teardown), the
    # parent ``plugins`` module ends up without a ``registry`` attribute
    # even though ``sys.modules["plugins.registry"]`` is still the real
    # module. A subsequent ``monkeypatch.setattr("plugins.registry.X",
    # ...)`` then fails with:
    #     'module' object at plugins.registry has no attribute 'registry'
    #
    # Defensive cleanup: re-bind the original ``plugins.registry``
    # submodule onto the (possibly stub) ``plugins`` package so attribute
    # lookups via the dotted path succeed. We deliberately do NOT
    # re-import plugins.registry — other test modules already hold
    # references to the original module's globals (e.g. ``get_taps``,
    # ``entry_points``), and a fresh import would create a parallel
    # module object whose globals nobody can patch from the outside.
    # Idempotent + cheap when nothing is wrong.
    try:
        registry = sys.modules.get("plugins.registry")
        if registry is None:
            import importlib
            registry = importlib.import_module("plugins.registry")
        plugins_pkg = sys.modules.get("plugins")
        if plugins_pkg is not None and getattr(plugins_pkg, "registry", None) is not registry:
            plugins_pkg.registry = registry  # type: ignore[attr-defined]

        registry.clear_registry_cache()
    except Exception:
        pass

    # site_config has a module-level dict; preserve the brand keys we
    # seeded at conftest import time + drop anything set during the test.
    # Post-#330 sweep there's no global singleton — reset the conftest's
    # shared instance instead.
    try:
        site_config._config = {
            k: v for k, v in site_config._config.items() if k in _TEST_BRAND_CONFIG
        }
        # Re-seed in case a test wiped the brand keys.
        for k, v in _TEST_BRAND_CONFIG.items():
            site_config._config.setdefault(k, v)
    except Exception:
        pass

    # settings_service caches the DB read; clear to force re-fetch.
    try:
        import services.settings_service as _ss
        if hasattr(_ss, "_settings_service"):
            _ss._settings_service = None
        if hasattr(_ss.SettingsService, "_cache"):
            # Instance cache — some tests build an instance with a fake pool,
            # later tests shouldn't see that cache.
            pass  # instance-scoped; no module-level state to reset here.
    except Exception:
        pass

    # container service registry (the DI holder used by get_service).
    try:
        from services import container
        if hasattr(container, "_services"):
            container._services = {}
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Layer 4 — optional real-Postgres ``db_pool`` fixture
# ---------------------------------------------------------------------------
#
# Some service tests need to roundtrip SQL against a real Postgres (e.g.
# tests that exercise CHECK constraints, UNIQUE indexes, or transactional
# behavior — anything a mock pool would silently let through). These tests
# request the ``db_pool`` fixture below.
#
# Mirrors the integration_db tier's skip pattern: if no live Postgres DSN
# resolves, the whole module is skipped at fixture time so unit-only CI
# runners don't blow up.


def _bootstrap_resolve_dsn() -> str | None:
    """Walk the tree up until we find brain/bootstrap.py, then call its
    resolver. Same trick the integration_db conftest uses.
    """
    for p in Path(__file__).resolve().parents:
        if (p / "brain" / "bootstrap.py").is_file():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            break
    try:
        from brain.bootstrap import resolve_database_url
        return resolve_database_url()
    except Exception:
        return None


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _db_pool_session():
    """Session-scoped asyncpg pool against a disposable test database.

    Creates a fresh ``poindexter_unit_<hex>`` database, runs every
    migration in ``services/migrations/`` against it, and yields a pool.
    Drops the database at session teardown.

    Skips the test if no live Postgres DSN is reachable.
    """
    import asyncpg

    base = _bootstrap_resolve_dsn()
    if not base or base == "postgresql://test:test@localhost/test":
        pytest.skip(
            "No live Postgres DSN configured — db_pool fixture requires a reachable DB"
        )

    parsed = urlparse(base)
    admin_dsn = urlunparse(parsed._replace(path="/postgres"))
    test_db_name = f"poindexter_unit_{secrets.token_hex(6)}"
    test_dsn = urlunparse(parsed._replace(path=f"/{test_db_name}"))

    # The bootstrap DSN may resolve (e.g. points at a Docker-mapped port) but
    # the server isn't actually reachable when Docker is down. Treat that the
    # same as "no DSN configured" — skip rather than ERROR every db-backed test.
    try:
        admin = await asyncpg.connect(admin_dsn)
    except (OSError, asyncpg.exceptions.PostgresError) as exc:
        pytest.skip(
            f"Postgres unreachable at {admin_dsn} ({exc!r}) — "
            "db_pool fixture requires a running DB"
        )
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
        await admin.execute(f"CREATE DATABASE {test_db_name}")
    finally:
        await admin.close()

    # Install extensions before the baseline migration runs. CREATE
    # EXTENSION needs ownership of the database, which a worker-level
    # connection in this DSN already has. We deliberately do NOT replay
    # ``infrastructure/local-db/init.sql`` here anymore — that file
    # carried a legacy ``embeddings`` schema (no ``text_search`` column)
    # that conflicted with the baseline's ``CREATE INDEX ... USING gin
    # (text_search)``. The 0000_baseline migration now owns bootstrap.
    fresh = await asyncpg.connect(test_dsn)
    try:
        await fresh.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await fresh.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await fresh.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        await fresh.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
    finally:
        await fresh.close()

    pool = await asyncpg.create_pool(test_dsn, min_size=1, max_size=4)
    try:
        from services.migrations import run_migrations

        class _StubService:
            def __init__(self, pool):
                self.pool = pool

        ok = await run_migrations(_StubService(pool))
        if not ok:
            pytest.fail("Migrations failed against the unit-tier test DB")

        try:
            yield pool
        finally:
            await pool.close()
    finally:
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                test_db_name,
            )
            await admin.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
        finally:
            await admin.close()


@pytest_asyncio.fixture(loop_scope="session")
async def db_pool(_db_pool_session):
    """Function-scoped wrapper around the session pool that wipes niche
    tables after each test so cross-test slug collisions don't leak.

    Tests just request ``db_pool`` — yields the same underlying pool, but
    handles cleanup transparently.
    """
    try:
        yield _db_pool_session
    finally:
        async with _db_pool_session.acquire() as conn:
            # Cascade-delete every test-created niche while leaving the
            # baseline-seeded ``dev_diary`` row in place — tests that
            # validate the seed (test_dev_diary_niche.py) need it
            # present every time, not freshly truncated. Pre-squash this
            # was ``TRUNCATE niches CASCADE`` because the fixture
            # re-imported migration 0134 to recreate the seed; that
            # migration file is now part of 0000_baseline and only runs
            # at session setup, so we keep the row instead of replaying.
            try:
                await conn.execute(
                    "DELETE FROM niches WHERE slug NOT IN ('dev_diary')"
                )
            except Exception:
                pass
