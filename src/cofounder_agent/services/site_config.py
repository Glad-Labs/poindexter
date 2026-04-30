"""
Site Configuration — DB-first, env-fallback identity and config.

Loads all identity/config from app_settings on startup.
Every service reads from this module instead of os.getenv().

Only DATABASE_URL and PORT remain as env vars (chicken-and-egg).
Everything else comes from the database.

Usage:
    from services.site_config import site_config
    name = site_config.get("site_name")            # "Glad Labs"
    url = site_config.get("api_base_url")           # "https://..."
    email = site_config.get("privacy_email")        # "privacy@..."

    # Or with a default
    gpu = site_config.get("gpu_model", "Unknown GPU")

Startup:
    Called from main.py lifespan after DB pool is ready:
        await site_config.load(pool)

Testing:
    Tests should construct their own ``SiteConfig(initial_config=...)``
    instead of mutating the shared singleton. This avoids test pollution
    between cases that seed different values, and unblocks #242 one
    test file at a time without touching production callers.

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

        try:
            rows = await pool.fetch(
                "SELECT key, value FROM app_settings WHERE is_secret = false"
            )
            for row in rows:
                if row["value"]:  # Skip empty values
                    self._config[row["key"]] = row["value"]

            self._loaded = True
            self._pool = pool  # Retain for on-demand get_secret() lookups
            logger.info("[SITE_CONFIG] Loaded %d settings from database", len(self._config))
            return len(self._config)
        except Exception as e:
            logger.warning("[SITE_CONFIG] Failed to load from DB: %s — using env fallbacks", e)
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


# Global singleton — import this everywhere
site_config = SiteConfig()
