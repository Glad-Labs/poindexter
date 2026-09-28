"""Both real install orders build the same schema (poindexter#1097).

``poindexter setup`` runs the migrations on an empty database. ``docker compose
up`` on a fresh volume starts the brain first, and the brain's boot seed creates
``app_settings`` before the worker migrates anything. That second order crashed
the baseline (``idx_app_settings_is_active``: column "is_active" does not exist)
on every fresh compose install, because the brain created 8 of the table's 14
columns.

Each test builds its own empty database next to the harness's and compares the
result with a migrations-first database, object for object, using the same
snapshot migrations-smoke's ``--brain-first`` step uses. migrations-smoke covers
the current brain on every PR. This file adds the cases it can't reach: the
8-column table an older brain image leaves behind, the seed precedence the
compose-first order produces, the guard that keeps convergence off once
anything is migrated, and a NULL the stricter table cannot take.
"""

from __future__ import annotations

import importlib
import importlib.util
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
import pytest_asyncio

from poindexter.brain.seed_loader import load_seed_file, seed_app_settings
from poindexter.services.migrations import run_migrations

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_MIGRATIONS = Path(__file__).resolve().parents[2] / "poindexter" / "services" / "migrations"

# The table the brain created before the fix, verbatim: a fixture of history.
_LEGACY_BRAIN_DDL = """
CREATE TABLE IF NOT EXISTS app_settings (
    id SERIAL PRIMARY KEY,
    key VARCHAR(255) UNIQUE NOT NULL,
    value TEXT DEFAULT '',
    category VARCHAR(100) DEFAULT 'general',
    description TEXT DEFAULT '',
    is_secret BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
"""


def _smoke():
    for parent in Path(__file__).resolve().parents:
        script_dir = parent / "scripts" / "ci"
        if (script_dir / "migrations_smoke.py").is_file():
            if str(script_dir) not in sys.path:
                sys.path.insert(0, str(script_dir))
            return importlib.import_module("migrations_smoke")
    pytest.skip("scripts/ci/migrations_smoke.py not reachable from this layout")


def _load_baseline():
    """Load 0000_baseline.py the way the runner does (never in sys.modules)."""
    spec = importlib.util.spec_from_file_location("0000_baseline", _MIGRATIONS / "0000_baseline.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class _Holder:
    def __init__(self, pool):
        self.pool = pool


@asynccontextmanager
async def _database(admin_dsn: str, label: str):
    """A disposable empty database on the harness's server, dropped afterwards."""
    name = f"poindexter_test_{secrets.token_hex(4)}_{label}"
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f"CREATE DATABASE {name}")
    finally:
        await admin.close()
    try:
        yield urlunparse(urlparse(admin_dsn)._replace(path=f"/{name}"))
    finally:
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                name,
            )
            await admin.execute(f"DROP DATABASE IF EXISTS {name}")
        finally:
            await admin.close()


async def _migrate(dsn: str) -> set[str]:
    """Run every migration; return the recorded names."""
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        assert await run_migrations(_Holder(pool)) is True
        return {r["name"] for r in await pool.fetch("SELECT name FROM schema_migrations")}
    finally:
        await pool.close()


async def _snapshot(dsn: str) -> set[str]:
    conn = await asyncpg.connect(dsn)
    try:
        return await _smoke().schema_snapshot(conn)
    finally:
        await conn.close()


async def _settings(dsn: str) -> dict[str, str]:
    conn = await asyncpg.connect(dsn)
    try:
        return {r["key"]: r["value"] for r in await conn.fetch("SELECT key, value FROM app_settings")}
    finally:
        await conn.close()


def _assert_same_schema(actual: set[str], reference: set[str]) -> None:
    diff = _smoke().diff_snapshots(
        actual, reference, actual_label="this order", reference_label="migrations-first"
    )
    assert not diff, "\n".join(diff[:40])


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def reference(admin_dsn):
    """The ``poindexter setup`` order: every migration on an empty database."""
    async with _database(admin_dsn, "migrations_first") as dsn:
        recorded = await _migrate(dsn)
        yield {"snapshot": await _snapshot(dsn), "settings": await _settings(dsn), "recorded": recorded}


async def test_brain_seed_then_migrations_builds_the_migrations_first_schema(admin_dsn, reference):
    async with _database(admin_dsn, "brain_first") as dsn:
        conn = await asyncpg.connect(dsn)
        try:
            seeded = await seed_app_settings(conn)
        finally:
            await conn.close()
        assert seeded["inserted"] == seeded["total_seed"] > 0

        assert await _migrate(dsn) == reference["recorded"]
        _assert_same_schema(await _snapshot(dsn), reference["snapshot"])

        # The precedence the docs state for this order: all ON CONFLICT DO
        # NOTHING, first writer wins, and the brain writes first. Every key the
        # migrations-first database holds is here too, and wherever the two
        # orders disagree, the key is one the brain seeded and holds the
        # brain's value.
        settings = await _settings(dsn)
        brain = {row["key"]: row["value"] for row in load_seed_file()}
        assert set(reference["settings"]) <= set(settings)
        differing = {k for k, v in settings.items() if reference["settings"].get(k) != v}
        assert differing, "the brain's seed left no trace, so this proves nothing"
        assert differing <= set(brain)
        assert {k: settings[k] for k in differing} == {k: brain[k] for k in differing}


async def test_the_table_an_older_brain_left_heals_on_the_next_migration(admin_dsn, reference):
    """An install that already hit the bug: the old brain's 8-column table with
    its rows, a baseline that never recorded. With a current brain image the
    next boot seeds into it (its CREATE finds the table and does nothing); the
    next worker start must bring it to the declared shape and keep the rows."""
    async with _database(admin_dsn, "legacy_brain_table") as dsn:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(_LEGACY_BRAIN_DDL)
            await conn.execute(
                "INSERT INTO app_settings (key, value) VALUES ('operator_note', 'kept')"
            )
            await seed_app_settings(conn)
        finally:
            await conn.close()

        assert await _migrate(dsn) == reference["recorded"]
        _assert_same_schema(await _snapshot(dsn), reference["snapshot"])
        settings = await _settings(dsn)
        assert settings["operator_note"] == "kept"


async def test_nothing_converges_once_a_migration_is_recorded(admin_dsn):
    """With any migration recorded, a declared column a table lacks was dropped on
    purpose later. So the baseline behaves exactly as it always did: here it
    fails on the missing column rather than adding it."""
    async with _database(admin_dsn, "guard") as dsn:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(_LEGACY_BRAIN_DDL)
            await conn.execute(
                "CREATE TABLE schema_migrations (id SERIAL PRIMARY KEY, "
                "name VARCHAR(255) UNIQUE NOT NULL, applied_at TIMESTAMPTZ DEFAULT now())"
            )
            await conn.execute("INSERT INTO schema_migrations (name) VALUES ('20990101_000000_later.py')")
        finally:
            await conn.close()

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        try:
            with pytest.raises(asyncpg.exceptions.UndefinedColumnError, match="is_active"):
                await _load_baseline().up(pool)
            columns = await pool.fetchval(
                "SELECT count(*) FROM information_schema.columns WHERE table_name = 'app_settings'"
            )
        finally:
            await pool.close()
        assert columns == 8


async def test_a_null_value_in_the_old_table_fails_loudly_and_is_left_alone(admin_dsn):
    """The declared table forbids a NULL value, and convergence never rewrites
    data to get there. The migration fails and names the column, and the row is
    untouched for the operator to decide."""
    async with _database(admin_dsn, "legacy_null") as dsn:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(_LEGACY_BRAIN_DDL)
            await conn.execute("INSERT INTO app_settings (key, value) VALUES ('hand_written', NULL)")
        finally:
            await conn.close()

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        try:
            with pytest.raises(asyncpg.exceptions.NotNullViolationError, match="value"):
                await run_migrations(_Holder(pool))
            assert await pool.fetchval(
                "SELECT value IS NULL FROM app_settings WHERE key = 'hand_written'"
            )
            assert not await pool.fetchval("SELECT count(*) FROM schema_migrations")
        finally:
            await pool.close()
