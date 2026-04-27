"""
Site Configuration — DB-first, env-fallback identity and config.

Loads all identity/config from app_settings on startup.
Every service reads from an injected ``SiteConfig`` instance instead
of ``os.getenv()``.

Only DATABASE_URL and PORT remain as env vars (chicken-and-egg).
Everything else comes from the database.

Usage (post Phase H, GH#95):
    ``main.py`` owns the canonical instance. The lifespan constructs it
    once, loads it from the DB, and attaches it to ``app.state.site_config``:

        _site_cfg = SiteConfig()
        await _site_cfg.load(pool)
        app.state.site_config = _site_cfg

    Code that needs site_config gets it via the DI seam for its layer:

    * **Route handlers** — FastAPI dependency:
        ``site_config: SiteConfig = Depends(get_site_config_dependency)``
    * **Services** — accept ``site_config`` in ``__init__``; store on
      ``self._site_config``. Make it required (no None default) for
      production classes; tests construct with
      ``SiteConfig(initial_config={...})``.
    * **Pipeline stages** — read ``context.get("site_config")``. The
      context dict is seeded by ``process_content_generation_task``.
    * **Image providers / taps / topic sources** — read
      ``config.get("_site_config")``. The dispatcher/runner seeds it.

    The module-level ``site_config`` singleton is in the process of
    being removed (GH-117). Until every call site migrates to DI, it
    still exists as a transitional shim. All new code MUST accept the
    instance as a parameter / read it from its DI seam.

Testing:
    Tests should construct their own ``SiteConfig(initial_config=...)``
    or use the ``test_site_config`` fixture in
    ``tests/unit/conftest.py``.

        cfg = SiteConfig(initial_config={"site_url": "https://test"})
        assert cfg.get("site_url") == "https://test"
"""

import os

from services.logger_config import get_logger

logger = get_logger(__name__)


class SiteConfig:
    """Database-backed configuration with env var fallback.

    Priority: DB (app_settings) > env var > hardcoded default

    Can be constructed stand-alone for per-test isolation
    (``SiteConfig(initial_config={...})``) or wired up the
    load-from-pool way in the app lifespan.
    """

    def __init__(
        self,
        *,
        initial_config: dict[str, str] | None = None,
        pool=None,
    ):
        """Build a SiteConfig instance.

        Args:
            initial_config: Pre-populated setting values. Primary use is
                tests that want deterministic values without touching
                the module singleton.
            pool: Optional asyncpg pool. If provided, it's stored so
                ``get_secret()`` can query on demand. Call ``load()``
                separately to populate ``_config`` from app_settings.
        """
        self._config: dict[str, str] = dict(initial_config or {})
        self._loaded = bool(initial_config)
        self._pool = pool

    async def load(self, pool) -> int:
        """Load all non-secret settings from app_settings.

        Call once at app startup after the DB pool is ready.
        Returns number of settings loaded.

        Secrets (is_secret=true) are deliberately NOT kept in the
        in-memory cache — they stay in the DB and callers must use
        `get_secret()` (async, one query per call) when they need
        them. This keeps secrets out of any debug dump that calls
        `site_config.all()`.
        """
        if pool is None:
            logger.warning("[SITE_CONFIG] No DB pool — using env var fallbacks only")
            return 0

        # Attach the pool BEFORE attempting the SELECT so a transient
        # load failure doesn't leave the SiteConfig pool-less for the
        # rest of the session (gitea#322 follow-up). Downstream
        # consumers — CostGuard.record_usage, SiteConfig.get_secret,
        # the LLM provider plugins' _build_cost_guard — all silently
        # no-op when ``_pool`` is None. Losing the pool here was the
        # silent-disabled-cost-tracking class of bug Matt asked us to
        # hunt.
        self._pool = pool

        try:
            rows = await pool.fetch(
                "SELECT key, value FROM app_settings WHERE is_secret = false"
            )
            for row in rows:
                if row["value"]:  # Skip empty values
                    self._config[row["key"]] = row["value"]

            self._loaded = True
            logger.info("[SITE_CONFIG] Loaded %d settings from database", len(self._config))
            return len(self._config)
        except Exception as e:
            logger.warning("[SITE_CONFIG] Failed to load from DB: %s — using env fallbacks", e)
            # Pool is still attached above, so get_secret() and CostGuard
            # writes keep working even though the in-memory cache is
            # incomplete.
            return 0

    async def reload(self, pool) -> int:
        """Reload settings from DB. Call periodically or after settings change.

        Safe to call while the app is running — atomic replacement of config dict.
        """
        if pool is None:
            return 0
        try:
            rows = await pool.fetch(
                "SELECT key, value FROM app_settings WHERE is_secret = false"
            )
            new_config = {}
            for row in rows:
                if row["value"]:
                    new_config[row["key"]] = row["value"]
            self._config = new_config
            logger.debug("[SITE_CONFIG] Reloaded %d settings", len(new_config))
            return len(new_config)
        except Exception as e:
            logger.warning("[SITE_CONFIG] Reload failed: %s", e)
            return 0

    async def get_secret(self, key: str, default: str = "") -> str:
        """Async-fetch and decrypt a secret value from app_settings.

        Secrets aren't kept in the in-memory ``_config`` dict (load()
        filters them out), so each call is one DB query. That's fine —
        secrets are read rarely and at well-defined points (uploads,
        API calls). Falls back to the uppercase env var, then default.

        Handles both encrypted (``enc:v1:...`` prefix) and legacy
        plaintext rows transparently. Delegates to
        ``plugins.secrets.get_secret`` which owns the decryption logic.
        """
        if self._pool is not None:
            try:
                async with self._pool.acquire() as conn:
                    # plugins.secrets.get_secret handles both encrypted
                    # and plaintext rows — returns None if the row
                    # doesn't exist.
                    from plugins.secrets import get_secret as _plugin_get_secret
                    value = await _plugin_get_secret(conn, key)
                    if value is not None and value != "":
                        return str(value)
            except Exception as e:
                logger.warning(
                    "[SITE_CONFIG] get_secret(%s) query failed: %s", key, e
                )
        env_val = os.getenv(key.upper())
        if env_val:
            return env_val
        return default

    def require(self, key: str) -> str:
        """Get a REQUIRED config value. Raises if not configured.

        Use this for settings that MUST be set for the system to work
        (site_name, site_url, company_name, API keys). No silent defaults.
        """
        if key in self._config:
            return self._config[key]
        env_key = key.upper()
        env_val = os.getenv(env_key)
        if env_val:
            return env_val
        raise RuntimeError(
            f"Required setting '{key}' is not configured. "
            f"Set it in app_settings table or as env var {env_key}."
        )

    def get(self, key: str, default: str = "") -> str:
        """Get a config value. Priority: DB > env var > default.

        For optional settings only. Use require() for required settings.
        """
        # DB value takes priority
        if key in self._config:
            return self._config[key]

        # Fall back to env var (uppercase, with common prefix mappings)
        env_key = key.upper()
        env_val = os.getenv(env_key)
        if env_val:
            return env_val

        return default

    def get_int(self, key: str, default: int = 0) -> int:
        """Get an integer config value."""
        val = self.get(key, str(default))
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Get a float config value."""
        val = self.get(key, str(default))
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get a boolean config value."""
        val = self.get(key, str(default)).lower()
        return val in ("true", "1", "yes", "on")

    def get_list(self, key: str, default: str = "") -> list:
        """Get a comma-separated list config value."""
        val = self.get(key, default)
        return [v.strip() for v in val.split(",") if v.strip()] if val else []

    @property
    def is_loaded(self) -> bool:
        """Whether the config has been loaded from DB."""
        return self._loaded

    def all(self) -> dict[str, str]:
        """Get all loaded config values (for debugging)."""
        return dict(self._config)


# ---------------------------------------------------------------------------
# Phase H cleanup status (2026-04-24)
# ---------------------------------------------------------------------------
#
# Many call sites have been migrated to accept a SiteConfig instance via
# DI (TopicDiscovery, plan_images, _plan_and_inject_placeholders,
# DatabaseService.initialize, ContentDatabase.get_metrics,
# services/taps/runner). The remaining importers are enumerated in
# GH-117 — until that issue closes, this module-level singleton stays
# as a transitional shim so those call sites still work.
#
# DO NOT import this singleton in NEW code. Accept ``site_config`` as
# a parameter, read it from ``app.state.site_config`` (routes),
# ``context["site_config"]`` (stages), or ``self._site_config``
# (services). See commits under GH-95 and GH-117 for the established DI
# patterns.
site_config = SiteConfig()
