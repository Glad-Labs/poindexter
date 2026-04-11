"""
Settings Service - DB-backed key-value configuration.

Reads from the `app_settings` table, with an in-memory cache that refreshes
every `_cache_ttl` seconds. Falls back to environment variables when a DB
value is empty, providing a smooth migration path from env-var-only config.

Managed via OpenClaw; secrets are masked in `get_all()` unless explicitly
requested.
"""

import os
import time
from typing import Optional

from services.logger_config import get_logger

logger = get_logger(__name__)

_SECRET_MASK = "********"


class SettingsService:
    """Async key-value settings backed by the app_settings table."""

    def __init__(self, pool):
        self.pool = pool
        self._cache: dict[str, dict] = {}
        self._cache_ttl = 60  # seconds
        self._last_refresh: float = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self, key: str, default: str | None = None) -> str | None:
        """Get a setting value, using cache.

        Falls back to the matching environment variable (upper-cased key) when
        the DB value is empty, so services keep working during migration.
        """
        await self._ensure_cache()

        entry = self._cache.get(key)
        if entry is not None and entry["value"]:
            return entry["value"]

        # Fallback: try env var (e.g. key "anthropic_api_key" -> "ANTHROPIC_API_KEY")
        env_val = os.getenv(key.upper())
        if env_val:
            return env_val

        return default

    async def get_by_category(self, category: str) -> dict:
        """Get all settings in a category as ``{key: value}``."""
        await self._ensure_cache()
        return {
            k: v["value"]
            for k, v in self._cache.items()
            if v.get("category") == category
        }

    async def set(
        self,
        key: str,
        value: str,
        category: str | None = None,
        description: str | None = None,
        is_secret: bool | None = None,
    ):
        """Set a setting value (upsert).

        Only non-None optional fields are updated on conflict.
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret, updated_at)
                VALUES ($1, $2, COALESCE($3, 'general'), $4, COALESCE($5, FALSE), NOW())
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    category = COALESCE(EXCLUDED.category, app_settings.category),
                    description = COALESCE(EXCLUDED.description, app_settings.description),
                    is_secret = COALESCE(EXCLUDED.is_secret, app_settings.is_secret),
                    updated_at = NOW()
                """,
                key,
                value,
                category,
                description,
                is_secret,
            )

        # Invalidate cache so next read picks up the change
        self._last_refresh = 0
        logger.info("[SETTINGS] Updated key=%r", key)

    async def delete(self, key: str):
        """Delete a setting."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM app_settings WHERE key = $1", key
            )
        self._last_refresh = 0
        logger.info("[SETTINGS] Deleted key=%r (%s)", key, result)

    async def get_all(self, include_secrets: bool = False) -> list[dict]:
        """Get all settings. Masks secret values unless *include_secrets*."""
        await self._ensure_cache()

        out: list[dict] = []
        for key, entry in sorted(self._cache.items()):
            row = {
                "key": key,
                "value": (
                    entry["value"]
                    if (include_secrets or not entry.get("is_secret"))
                    else _SECRET_MASK
                ),
                "category": entry.get("category", "general"),
                "description": entry.get("description"),
                "is_secret": entry.get("is_secret", False),
                "updated_at": entry.get("updated_at"),
            }
            out.append(row)
        return out

    async def refresh_cache(self):
        """Force refresh the in-memory cache from DB."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT key, value, category, description, is_secret, updated_at "
                    "FROM app_settings"
                )
            self._cache = {
                row["key"]: {
                    "value": row["value"],
                    "category": row["category"],
                    "description": row["description"],
                    "is_secret": row["is_secret"],
                    "updated_at": (
                        row["updated_at"].isoformat() if row["updated_at"] else None
                    ),
                }
                for row in rows
            }
            self._last_refresh = time.monotonic()
            logger.debug("[SETTINGS] Cache refreshed -- %d keys loaded", len(self._cache))
        except Exception:
            logger.error("[SETTINGS] Failed to refresh cache from DB", exc_info=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _ensure_cache(self):
        """Refresh cache if stale or empty."""
        now = time.monotonic()
        if not self._cache or (now - self._last_refresh) > self._cache_ttl:
            await self.refresh_cache()
