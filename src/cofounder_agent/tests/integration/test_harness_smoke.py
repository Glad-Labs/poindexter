"""Smoke tests for the real-services integration harness (GitHub #21).

These tests verify the *harness* works — real Postgres reachable, real
Ollama reachable, schema can be bootstrapped into the isolated test DB,
the main Poindexter DB is never touched. They are intentionally small:
the real pipeline tests come in later phases once the harness is in
daily use by the refactor.

Gate: requires INTEGRATION_TESTS=1 AND REAL_SERVICES_TESTS=1.

## What these tests prove

1. `ensure_test_database` — a clean `poindexter_test` DB exists and is reachable.
2. `real_pool` — asyncpg pool is usable.
3. `migrations_applied` — `app_settings` table exists after bootstrap.
4. `real_ollama` — Ollama responds to `/api/tags`.
5. `clean_test_tables` — truncation leaves a pristine slate per-test.
6. Isolation — the main `poindexter_brain` DB is never touched by these tests.

## What these tests do NOT prove

- That the content pipeline produces good output (Phase B+ tests)
- That any plugin actually works (Phase A+ tests)
- That migrations match production (needs a dedicated migration runner test)

This is scaffolding, not coverage.
"""

from __future__ import annotations

import os

import asyncpg
import httpx
import pytest

from tests.integration.conftest_real_services import requires_real_services


pytestmark = [pytest.mark.integration, pytest.mark.asyncio, requires_real_services]


async def test_test_database_exists(real_pool: asyncpg.Pool) -> None:
    """Pool is alive and we're connected to `poindexter_test`, not main."""
    async with real_pool.acquire() as conn:
        dbname = await conn.fetchval("SELECT current_database()")
    assert dbname == "poindexter_test", (
        f"Harness connected to {dbname!r}, not poindexter_test. "
        "This must never touch the operating database."
    )


async def test_app_settings_table_exists_after_bootstrap(
    migrations_applied, real_pool: asyncpg.Pool
) -> None:
    """`app_settings` exists and is usable after `migrations_applied`."""
    async with real_pool.acquire() as conn:
        exists = await conn.fetchval(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'app_settings'
            """
        )
    assert exists, "app_settings table should be created by migrations_applied fixture"


async def test_app_settings_round_trip(
    migrations_applied, clean_test_tables: asyncpg.Pool
) -> None:
    """Can write + read a row; truncation fixture starts clean."""
    async with clean_test_tables.acquire() as conn:
        count_before = await conn.fetchval("SELECT COUNT(*) FROM app_settings")
        assert count_before == 0, "clean_test_tables should leave app_settings empty"

        await conn.execute(
            "INSERT INTO app_settings (key, value, category) VALUES ($1, $2, $3)",
            "harness.smoke.probe",
            "ok",
            "test",
        )
        value = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            "harness.smoke.probe",
        )
    assert value == "ok"


async def test_ollama_reachable(real_ollama: httpx.AsyncClient) -> None:
    """Ollama responds to /api/tags with a JSON body containing 'models'."""
    resp = await real_ollama.get("/api/tags")
    assert resp.status_code == 200
    body = resp.json()
    assert "models" in body, f"Ollama /api/tags returned unexpected shape: {body!r}"


async def test_ollama_has_at_least_one_model(real_ollama: httpx.AsyncClient) -> None:
    """Fail if no models are pulled on the Ollama instance.

    Not strictly required — the harness *can* run against an empty Ollama —
    but without at least one model the pipeline tests will be useless. This
    surfaces the issue at harness-smoke time instead of mid-test.
    """
    resp = await real_ollama.get("/api/tags")
    models = resp.json().get("models", [])
    assert models, (
        "Ollama has no pulled models. Run `ollama pull gemma3:27b` or similar "
        "before running the real-services harness."
    )


async def test_main_database_untouched() -> None:
    """Sanity check: the MAIN poindexter_brain DB was not written to.

    We can't fully prove this without a snapshot, but we can at least verify
    the harness isn't accidentally pointed at the operating DB.
    """
    test_dsn = os.getenv("TEST_ADMIN_DSN") or os.getenv("POSTGRES_ADMIN_DSN") or ""
    # Never allow the test harness to point at the operating DB by accident.
    assert "poindexter_brain" not in test_dsn, (
        "Harness admin DSN points at poindexter_brain — must use postgres admin DB only."
    )
