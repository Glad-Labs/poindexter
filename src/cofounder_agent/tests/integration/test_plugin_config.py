"""Integration tests for plugins.config.PluginConfig.

Uses the real-services harness (real Postgres, isolated poindexter_test
DB, truncation between tests) to verify PluginConfig round-trips
correctly against app_settings.
"""

from __future__ import annotations

import asyncpg
import pytest

from plugins import PluginConfig
from tests.integration.conftest import requires_real_services

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, requires_real_services]


async def test_load_missing_returns_defaults(
    migrations_applied, clean_test_tables: asyncpg.Pool
) -> None:
    """Missing row returns a default PluginConfig without writing to the DB."""
    async with clean_test_tables.acquire() as conn:
        cfg = await PluginConfig.load(conn, "tap", "nonexistent")
    assert cfg.plugin_type == "tap"
    assert cfg.name == "nonexistent"
    assert cfg.enabled is True
    assert cfg.interval_seconds == 0
    assert cfg.config == {}

    # Crucially, loading should NOT have inserted a row.
    async with clean_test_tables.acquire() as conn:
        rows = await conn.fetchval("SELECT COUNT(*) FROM app_settings")
    assert rows == 0, "PluginConfig.load must not write when the row doesn't exist"


async def test_load_with_defaults_override(
    migrations_applied, clean_test_tables: asyncpg.Pool
) -> None:
    """defaults= kwarg is honored when the row is absent."""
    async with clean_test_tables.acquire() as conn:
        cfg = await PluginConfig.load(
            conn, "job", "demo",
            defaults={"enabled": False, "interval_seconds": 300, "config": {"foo": "bar"}},
        )
    assert cfg.enabled is False
    assert cfg.interval_seconds == 300
    assert cfg.config == {"foo": "bar"}


async def test_save_then_load_round_trip(
    migrations_applied, clean_test_tables: asyncpg.Pool
) -> None:
    """Save + re-load returns the same values."""
    async with clean_test_tables.acquire() as conn:
        original = PluginConfig(
            plugin_type="probe",
            name="database",
            enabled=True,
            interval_seconds=300,
            config={"query": "SELECT 1", "timeout_ms": 5000},
        )
        await original.save(conn)

        reloaded = await PluginConfig.load(conn, "probe", "database")

    assert reloaded.enabled is True
    assert reloaded.interval_seconds == 300
    assert reloaded.config == {"query": "SELECT 1", "timeout_ms": 5000}


async def test_save_is_upsert(migrations_applied, clean_test_tables: asyncpg.Pool) -> None:
    """A second save for the same plugin updates instead of inserting."""
    async with clean_test_tables.acquire() as conn:
        await PluginConfig(
            plugin_type="tap", name="memory", enabled=True, interval_seconds=3600,
            config={"scope": "user"},
        ).save(conn)
        await PluginConfig(
            plugin_type="tap", name="memory", enabled=False, interval_seconds=7200,
            config={"scope": "system"},
        ).save(conn)

        count = await conn.fetchval(
            "SELECT COUNT(*) FROM app_settings WHERE key = 'plugin.tap.memory'"
        )
        reloaded = await PluginConfig.load(conn, "tap", "memory")

    assert count == 1, "save must upsert, not duplicate rows per plugin"
    assert reloaded.enabled is False
    assert reloaded.interval_seconds == 7200
    assert reloaded.config == {"scope": "system"}


async def test_malformed_json_treated_as_disabled(
    migrations_applied, clean_test_tables: asyncpg.Pool
) -> None:
    """A row with unparseable JSON returns a disabled config, not a crash."""
    async with clean_test_tables.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category) VALUES ($1, $2, 'plugins')
            """,
            "plugin.tap.broken",
            "{not valid json",
        )
        cfg = await PluginConfig.load(conn, "tap", "broken")

    assert cfg.enabled is False, "malformed JSON must disable the plugin, not crash"


async def test_settings_key_convention() -> None:
    """Key format is exactly `plugin.<type>.<name>`."""
    assert PluginConfig.settings_key("tap", "memory") == "plugin.tap.memory"
    assert PluginConfig.settings_key("llm_provider", "openai_compat") == "plugin.llm_provider.openai_compat"


async def test_get_with_default(
    migrations_applied, clean_test_tables: asyncpg.Pool
) -> None:
    """PluginConfig.get() reads from config dict with a default."""
    async with clean_test_tables.acquire() as conn:
        cfg = PluginConfig(
            plugin_type="tap", name="demo",
            config={"endpoint": "https://example.com"},
        )

    assert cfg.get("endpoint") == "https://example.com"
    assert cfg.get("missing") is None
    assert cfg.get("missing", "default") == "default"
