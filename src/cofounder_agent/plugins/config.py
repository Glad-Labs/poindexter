"""PluginConfig — load per-install plugin config from ``app_settings``.

Every plugin instance has one row in ``app_settings``:

.. code::

    key: plugin.<type>.<name>
    value: '{"enabled": true, "interval_seconds": 3600, "config": {...}}'
    category: plugins

The shape is always the same: a JSON blob with at minimum ``enabled``,
plus whatever per-plugin config the implementation needs under
``config``. Using a single structured row (vs. several flat keys per
plugin) makes it trivial to:

- Disable a plugin: ``enabled: false``
- Inventory installed plugins: one SELECT
- Copy a plugin's entire config for backup/migration
- Show an operator "installed plugins" UI later without schema pain
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PluginConfig:
    """Per-install config for one plugin.

    Use :meth:`PluginConfig.load` to read from the DB. Use
    :meth:`PluginConfig.save` to persist changes.
    """

    plugin_type: str
    name: str
    enabled: bool = True
    interval_seconds: int = 0
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def settings_key(cls, plugin_type: str, name: str) -> str:
        """Return the ``app_settings.key`` for this plugin."""
        return f"plugin.{plugin_type}.{name}"

    @classmethod
    async def load(
        cls,
        pool_or_conn: Any,  # asyncpg Pool or Connection
        plugin_type: str,
        name: str,
        defaults: dict[str, Any] | None = None,
    ) -> PluginConfig:
        """Load a plugin's config from ``app_settings``.

        Behavior when the row doesn't exist:

        - If ``defaults`` is provided, returns a PluginConfig built from
          it without writing to the DB.
        - Otherwise returns a ``PluginConfig`` with defaults
          (``enabled=True``, ``interval_seconds=0``, empty config).

        Callers that want first-boot seeding should combine this with
        :class:`brain.seed_loader` patterns.
        """
        key = cls.settings_key(plugin_type, name)
        raw = await pool_or_conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key
        )
        if not raw:
            seed = defaults or {}
            return cls(
                plugin_type=plugin_type,
                name=name,
                enabled=seed.get("enabled", True),
                interval_seconds=seed.get("interval_seconds", 0),
                config=seed.get("config", {}),
            )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Treat malformed JSON as "present but unreadable" — disable
            # and log loudly. Better than crashing on a typo.
            import logging

            logging.getLogger(__name__).warning(
                "plugin.%s.%s has malformed JSON in app_settings; treating as disabled",
                plugin_type,
                name,
            )
            return cls(plugin_type=plugin_type, name=name, enabled=False)
        return cls(
            plugin_type=plugin_type,
            name=name,
            enabled=bool(parsed.get("enabled", True)),
            interval_seconds=int(parsed.get("interval_seconds", 0)),
            config=dict(parsed.get("config", {})),
        )

    async def save(self, pool_or_conn: Any) -> None:
        """Persist this PluginConfig to ``app_settings`` (upsert)."""
        key = self.settings_key(self.plugin_type, self.name)
        value = json.dumps(
            {
                "enabled": self.enabled,
                "interval_seconds": self.interval_seconds,
                "config": self.config,
            }
        )
        await pool_or_conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description)
            VALUES ($1, $2, 'plugins', $3)
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value,
                  updated_at = NOW()
            """,
            key,
            value,
            f"Config for plugin {self.plugin_type}/{self.name}",
        )

    def get(self, key: str, default: Any = None) -> Any:
        """Shortcut to read from ``self.config`` with a default."""
        return self.config.get(key, default)
