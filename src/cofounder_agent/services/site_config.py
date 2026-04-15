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
"""

import os
from typing import Any, Dict, Optional

from services.logger_config import get_logger

logger = get_logger(__name__)


class SiteConfig:
    """Database-backed configuration with env var fallback.

    Priority: DB (app_settings) > env var > hardcoded default
    """

    def __init__(self):
        self._config: dict[str, str] = {}
        self._loaded = False

    async def load(self, pool) -> int:
        """Load all non-secret settings from app_settings.

        Call once at app startup after the DB pool is ready.
        Returns number of settings loaded.
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
