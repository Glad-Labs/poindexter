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

import pytest
import pytest_asyncio

from services.site_config import site_config

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

    # plugin registry entry-point cache (lru_cache)
    try:
        from plugins.registry import clear_registry_cache
        clear_registry_cache()
    except Exception:
        pass

    # site_config has a module-level dict; preserve the brand keys we
    # seeded at conftest import time + drop anything set during the test.
    try:
        from services.site_config import site_config as _sc
        _sc._config = {k: v for k, v in _sc._config.items() if k in _TEST_BRAND_CONFIG}
        # Re-seed in case a test wiped the brand keys.
        for k, v in _TEST_BRAND_CONFIG.items():
            _sc._config.setdefault(k, v)
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

    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
        await admin.execute(f"CREATE DATABASE {test_db_name}")
    finally:
        await admin.close()

    # Replay infra init.sql (extensions + base schema) before migrations.
    fresh = await asyncpg.connect(test_dsn)
    try:
        await fresh.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await fresh.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        for p in Path(__file__).resolve().parents:
            init_sql = p / "infrastructure" / "local-db" / "init.sql"
            if init_sql.is_file():
                try:
                    await fresh.execute(init_sql.read_text(encoding="utf-8"))
                except Exception:
                    pass
                break
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
            # CASCADE drops dependent rows in niche_goals + niche_sources +
            # topic_batches + candidates + discovery_runs.
            try:
                await conn.execute("TRUNCATE niches CASCADE")
            except Exception:
                pass
