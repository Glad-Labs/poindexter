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

import pytest

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

    # text_utils fabricated-link scrubber cache
    try:
        import services.text_utils as _tu
        _tu._slug_cache = set()
    except Exception:  # noqa: BLE001
        pass

    # image-style rotation deque
    try:
        import services.image_style_rotation as _isr
        _isr.reset_history()
    except Exception:  # noqa: BLE001
        pass

    # plugin registry entry-point cache (lru_cache)
    try:
        from plugins.registry import clear_registry_cache
        clear_registry_cache()
    except Exception:  # noqa: BLE001
        pass

    # site_config has a module-level dict; preserve the brand keys we
    # seeded at conftest import time + drop anything set during the test.
    try:
        from services.site_config import site_config as _sc
        _sc._config = {k: v for k, v in _sc._config.items() if k in _TEST_BRAND_CONFIG}
        # Re-seed in case a test wiped the brand keys.
        for k, v in _TEST_BRAND_CONFIG.items():
            _sc._config.setdefault(k, v)
    except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
        pass

    # container service registry (the DI holder used by get_service).
    try:
        from services import container
        if hasattr(container, "_services"):
            container._services = {}
    except Exception:  # noqa: BLE001
        pass
