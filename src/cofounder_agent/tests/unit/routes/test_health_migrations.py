"""
Unit tests for the migrations status block in /api/health (#230).

These exercise the ``_build_migrations_health`` helper directly with
a mocked asyncpg pool so we don't need a live DB. The helper scans the
real on-disk ``services/migrations/`` directory — counting those files
is part of what we want to assert it does correctly.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from main import _build_migrations_health


def _make_pool(applied_rows):
    """Build a mock asyncpg pool whose acquire() returns a conn whose
    fetch() yields the given rows. Each row mimics asyncpg.Record by
    supporting ``row["name"]`` lookup."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=applied_rows)

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    return pool


def _make_db_service(pool):
    db = MagicMock()
    db.pool = pool
    return db


def _disk_migration_filenames() -> list[str]:
    migrations_dir = Path(__file__).resolve().parents[3] / "services" / "migrations"
    return sorted(
        f.name for f in migrations_dir.glob("*.py") if f.name != "__init__.py"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_returns_unknown_when_database_service_missing():
    block = await _build_migrations_health(None)
    assert block == {"status": "unknown", "error": "db_pool_unavailable"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_returns_unknown_when_pool_missing():
    db = MagicMock()
    db.pool = None
    block = await _build_migrations_health(db)
    assert block == {"status": "unknown", "error": "db_pool_unavailable"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_drift_when_all_files_applied():
    files = _disk_migration_filenames()
    # Pretend every on-disk migration is already applied; latest first.
    applied_rows = [{"name": name} for name in reversed(files)]
    pool = _make_pool(applied_rows)
    db = _make_db_service(pool)

    block = await _build_migrations_health(db)

    assert block["applied"] == len(files)
    assert block["pending"] == 0
    assert block["drift"] is False
    assert block["latest_applied"] == files[-1]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drift_true_when_unapplied_files_exist():
    files = _disk_migration_filenames()
    # Drop the last 3 files from the applied set to simulate drift.
    applied_files = files[:-3] if len(files) > 3 else []
    applied_rows = [{"name": name} for name in reversed(applied_files)]
    pool = _make_pool(applied_rows)
    db = _make_db_service(pool)

    block = await _build_migrations_health(db)

    assert block["applied"] == len(applied_files)
    assert block["pending"] == len(files) - len(applied_files)
    assert block["drift"] is True
    if applied_files:
        assert block["latest_applied"] == applied_files[-1]
    else:
        assert block["latest_applied"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_returns_unknown_when_query_fails():
    """schema_migrations table missing → unknown, not 500."""
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=Exception('relation "schema_migrations" does not exist'))

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    db = _make_db_service(pool)

    block = await _build_migrations_health(db)

    assert block["status"] == "unknown"
    assert "query_failed" in block["error"]
