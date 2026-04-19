"""Integration tests for services.llm_providers.dispatcher.

Exercises the swap-by-config flow end-to-end against the real-services
harness. Uses a mocked provider registry + a real app_settings table
so the config resolution path is exercised but no actual Ollama or
vllm call happens.
"""

from __future__ import annotations

import asyncpg
import pytest

from services.llm_providers.dispatcher import (
    get_provider,
    get_provider_config,
    get_provider_name,
)
from tests.integration.conftest import requires_real_services

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, requires_real_services]


class TestProviderNameResolution:
    async def test_defaults_to_ollama_native_when_unset(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        name = await get_provider_name(clean_test_tables, "standard")
        assert name == "ollama_native"

    async def test_reads_configured_provider_per_tier(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with clean_test_tables.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category)
                VALUES ('plugin.llm_provider.primary.standard', 'openai_compat', 'plugins')
                """
            )
        name = await get_provider_name(clean_test_tables, "standard")
        assert name == "openai_compat"

    async def test_different_tiers_can_have_different_providers(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with clean_test_tables.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category) VALUES
                  ('plugin.llm_provider.primary.free', 'ollama_native', 'plugins'),
                  ('plugin.llm_provider.primary.standard', 'openai_compat', 'plugins')
                """
            )
        assert await get_provider_name(clean_test_tables, "free") == "ollama_native"
        assert await get_provider_name(clean_test_tables, "standard") == "openai_compat"

    async def test_whitespace_only_value_falls_back_to_default(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with clean_test_tables.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category)
                VALUES ('plugin.llm_provider.primary.budget', '   ', 'plugins')
                """
            )
        assert await get_provider_name(clean_test_tables, "budget") == "ollama_native"


class TestProviderConfigLoad:
    async def test_returns_empty_dict_when_no_config(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        cfg = await get_provider_config(clean_test_tables, "openai_compat")
        assert cfg == {}

    async def test_reads_config_blob(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with clean_test_tables.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category)
                VALUES (
                  'plugin.llm_provider.openai_compat',
                  '{"enabled": true, "interval_seconds": 0, "config": {"base_url": "http://localhost:8080/v1", "api_key": "sk-test"}}',
                  'plugins'
                )
                """
            )
        cfg = await get_provider_config(clean_test_tables, "openai_compat")
        assert cfg["base_url"] == "http://localhost:8080/v1"
        assert cfg["api_key"] == "sk-test"


class TestGetProvider:
    async def test_returns_configured_provider_from_registry(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with clean_test_tables.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category)
                VALUES ('plugin.llm_provider.primary.standard', 'openai_compat', 'plugins')
                """
            )
        provider = await get_provider(clean_test_tables, "standard")
        assert provider.name == "openai_compat"

    async def test_falls_back_to_ollama_native_when_unconfigured(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        provider = await get_provider(clean_test_tables, "standard")
        assert provider.name == "ollama_native"

    async def test_falls_back_when_configured_provider_not_registered(
        self, migrations_applied, clean_test_tables: asyncpg.Pool, caplog
    ):
        async with clean_test_tables.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category)
                VALUES ('plugin.llm_provider.primary.standard', 'nonexistent_provider', 'plugins')
                """
            )
        with caplog.at_level("WARNING"):
            provider = await get_provider(clean_test_tables, "standard")
        # Falls back to ollama_native + logs a clear warning.
        assert provider.name == "ollama_native"
        assert any("nonexistent_provider" in r.message for r in caplog.records)
