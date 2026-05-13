"""End-to-end Phase 2 test against a real disposable Postgres DB.

Exercises ``services.module_migrations.run_module_migrations`` on
synthetic test modules. The session-scoped ``test_pool`` fixture
(from ``tests/integration_db/conftest.py``) is a fresh
``poindexter_test_<hex>`` DB with the full schema applied — that
includes ``module_schema_migrations`` (created by Phase 2 Task 1's
substrate migration).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.module_migrations import run_module_migrations


pytestmark = [
    pytest.mark.integration_db,
    # Session-scoped fixtures need the session loop, matching the
    # pattern used by ``test_claim_pending_task.py`` next door.
    pytest.mark.asyncio(loop_scope="session"),
]


def _write_migration(d: Path, name: str, sql: str) -> None:
    """Synchronous helper — writes a Python migration file that, when
    loaded by the runner, executes ``sql`` against the supplied pool.
    """
    body = (
        "async def up(pool):\n"
        "    async with pool.acquire() as conn:\n"
        f"        await conn.execute({sql!r})\n"
    )
    (d / name).write_text(body)


async def test_two_modules_each_apply_independently(test_pool, tmp_path):
    """Two synthetic modules, each with two migrations. Verify all
    four land + are tracked by (module_name, migration_name) so the
    'init.py' filename collision doesn't bite."""
    mod_a = tmp_path / "module_a" / "migrations"
    mod_b = tmp_path / "module_b" / "migrations"
    mod_a.mkdir(parents=True)
    mod_b.mkdir(parents=True)

    # NOTE: `0000_init.py` sorts FIRST (leading zeros), matching the
    # substrate convention. Both modules deliberately use the SAME
    # filename to prove the compound-key tracking works.
    _write_migration(
        mod_a, "0000_init.py",
        "CREATE TABLE IF NOT EXISTS _phase2_test_a_init (id SERIAL PRIMARY KEY)",
    )
    _write_migration(
        mod_a, "0001_add_col.py",
        "ALTER TABLE _phase2_test_a_init ADD COLUMN IF NOT EXISTS extra INT",
    )
    _write_migration(
        mod_b, "0000_init.py",
        "CREATE TABLE IF NOT EXISTS _phase2_test_b_init (id SERIAL PRIMARY KEY)",
    )
    _write_migration(
        mod_b, "0001_seed.py",
        "INSERT INTO _phase2_test_b_init DEFAULT VALUES",
    )

    try:
        ra = await run_module_migrations(test_pool, "module_a", mod_a)
        rb = await run_module_migrations(test_pool, "module_b", mod_b)

        assert ra.applied == 2 and ra.failed == 0
        assert rb.applied == 2 and rb.failed == 0

        async with test_pool.acquire() as conn:
            a_exists = await conn.fetchval(
                "SELECT to_regclass('_phase2_test_a_init')"
            )
            b_exists = await conn.fetchval(
                "SELECT to_regclass('_phase2_test_b_init')"
            )
            assert a_exists is not None
            assert b_exists is not None

            rows = await conn.fetch(
                "SELECT module_name, migration_name FROM module_schema_migrations "
                "WHERE module_name IN ('module_a', 'module_b') "
                "ORDER BY module_name, migration_name"
            )
            names = [(r["module_name"], r["migration_name"]) for r in rows]
            assert ("module_a", "0000_init.py") in names
            assert ("module_a", "0001_add_col.py") in names
            assert ("module_b", "0000_init.py") in names
            assert ("module_b", "0001_seed.py") in names
            # Both modules have a 0000_init.py — proves the compound key works
            init_rows = [n for n in names if n[1] == "0000_init.py"]
            assert len(init_rows) == 2
    finally:
        async with test_pool.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS _phase2_test_a_init CASCADE")
            await conn.execute("DROP TABLE IF EXISTS _phase2_test_b_init CASCADE")
            await conn.execute(
                "DELETE FROM module_schema_migrations "
                "WHERE module_name IN ('module_a', 'module_b')"
            )


async def test_rerun_is_no_op(test_pool, tmp_path):
    """Once a module's migrations apply, running again must skip
    every one of them (the idempotency contract)."""
    mod = tmp_path / "module_c" / "migrations"
    mod.mkdir(parents=True)
    _write_migration(
        mod, "0000_init.py",
        "CREATE TABLE IF NOT EXISTS _phase2_test_c_init (id SERIAL PRIMARY KEY)",
    )

    try:
        first = await run_module_migrations(test_pool, "module_c", mod)
        second = await run_module_migrations(test_pool, "module_c", mod)
        assert first.applied == 1 and first.skipped == 0
        assert second.applied == 0 and second.skipped == 1
    finally:
        async with test_pool.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS _phase2_test_c_init CASCADE")
            await conn.execute(
                "DELETE FROM module_schema_migrations WHERE module_name = 'module_c'"
            )
