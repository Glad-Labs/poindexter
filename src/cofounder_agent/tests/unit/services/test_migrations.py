"""
Unit tests for services/migrations/__init__.py — run_migrations().

Tests cover:
- No pool returns False
- No migration files returns True
- Already-applied migrations are skipped
- New migration is applied and recorded
- Migration missing up() function is skipped
- Failed migration continues (does not halt); returns False
- Multiple migrations — failure in one does not prevent others
- Outer exception returns False
"""

import asyncio
import importlib
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_pool(already_applied=None, fetchval_side_effect=None):
    """Build a minimal asyncpg pool mock."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    if fetchval_side_effect is not None:
        conn.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        # Default: first call returns None (not applied), subsequent return migration id
        conn.fetchval = AsyncMock(return_value=already_applied)

    # Context manager for acquire()
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


def _make_db_service(pool=None):
    db = MagicMock()
    db.pool = pool
    return db


@pytest.mark.unit
class TestRunMigrationsNoDB:
    def test_no_database_service_returns_false(self):
        from services.migrations import run_migrations
        result = _run(run_migrations(None))
        assert result is False

    def test_no_pool_returns_false(self):
        from services.migrations import run_migrations
        db = _make_db_service(pool=None)
        result = _run(run_migrations(db))
        assert result is False


@pytest.mark.unit
class TestRunMigrationsNoFiles:
    def test_no_migration_files_returns_true(self):
        from services.migrations import run_migrations
        pool, conn = _make_pool()
        db = _make_db_service(pool=pool)
        # Patch glob to return empty list
        with patch("services.migrations.Path.glob", return_value=iter([])):
            result = _run(run_migrations(db))
        assert result is True

    def test_creates_tracking_table_even_when_no_files(self):
        from services.migrations import run_migrations
        pool, conn = _make_pool()
        db = _make_db_service(pool=pool)
        with patch("services.migrations.Path.glob", return_value=iter([])):
            _run(run_migrations(db))
        # The _MIGRATIONS_TABLE_SQL execute should have been called
        conn.execute.assert_called()


@pytest.mark.unit
class TestRunMigrationsAlreadyApplied:
    def test_already_applied_migrations_are_skipped(self):
        from services.migrations import run_migrations

        mock_file = MagicMock(spec=Path)
        mock_file.name = "0001_initial.py"

        pool, conn = _make_pool(already_applied=42)  # fetchval returns existing id
        db = _make_db_service(pool=pool)

        with patch("services.migrations.Path.glob", return_value=iter([mock_file])):
            result = _run(run_migrations(db))

        assert result is True
        # up() was never called (no importlib.util.spec_from_file_location call needed)


@pytest.mark.unit
class TestRunMigrationsNewMigration:
    def test_new_migration_is_applied_and_recorded(self):
        from services.migrations import run_migrations

        mock_file = MagicMock(spec=Path)
        mock_file.name = "0001_initial.py"

        pool, conn = _make_pool(already_applied=None)  # not yet applied
        db = _make_db_service(pool=pool)

        # Build a fake migration module with up()
        mock_module = types.SimpleNamespace(up=AsyncMock(return_value=None))
        mock_spec = MagicMock()
        mock_spec.loader.exec_module = MagicMock()

        with patch("services.migrations.Path.glob", return_value=iter([mock_file])):
            with patch("services.migrations.importlib.util.spec_from_file_location", return_value=mock_spec):
                with patch("services.migrations.importlib.util.module_from_spec", return_value=mock_module):
                    result = _run(run_migrations(db))

        assert result is True
        mock_module.up.assert_awaited_once_with(pool)

    def test_migration_without_up_function_is_skipped(self):
        from services.migrations import run_migrations

        mock_file = MagicMock(spec=Path)
        mock_file.name = "0002_no_up.py"

        pool, conn = _make_pool(already_applied=None)
        db = _make_db_service(pool=pool)

        # Module has no up() function
        mock_module = types.SimpleNamespace()
        mock_spec = MagicMock()
        mock_spec.loader.exec_module = MagicMock()

        with patch("services.migrations.Path.glob", return_value=iter([mock_file])):
            with patch("services.migrations.importlib.util.spec_from_file_location", return_value=mock_spec):
                with patch("services.migrations.importlib.util.module_from_spec", return_value=mock_module):
                    result = _run(run_migrations(db))

        assert result is True  # Skipped is not a failure


def _sortable_path_mock(name: str):
    """Build a Path mock that supports < comparison (needed for sorted())."""
    m = MagicMock(spec=Path)
    m.name = name
    # Support < by comparing name strings
    m.__lt__ = lambda self, other: self.name < other.name
    m.__gt__ = lambda self, other: self.name > other.name
    m.__le__ = lambda self, other: self.name <= other.name
    m.__ge__ = lambda self, other: self.name >= other.name
    m.__eq__ = lambda self, other: self.name == getattr(other, "name", other)
    return m


@pytest.mark.unit
class TestRunMigrationsFailure:
    def test_failed_migration_continues_and_returns_false(self):
        """A failing migration does not halt subsequent ones; overall result is False."""
        from services.migrations import run_migrations

        file1 = _sortable_path_mock("0001_fail.py")
        file2 = _sortable_path_mock("0002_success.py")

        pool, conn = _make_pool(already_applied=None)
        db = _make_db_service(pool=pool)

        # Module 1 raises, module 2 succeeds
        failing_module = types.SimpleNamespace(up=AsyncMock(side_effect=RuntimeError("SQL error")))
        success_module = types.SimpleNamespace(up=AsyncMock(return_value=None))

        call_count = [0]

        def fake_spec(name, path):
            return MagicMock()

        def fake_module(spec):
            call_count[0] += 1
            if call_count[0] == 1:
                return failing_module
            return success_module

        with patch("services.migrations.Path.glob", return_value=iter([file1, file2])):
            with patch("services.migrations.importlib.util.spec_from_file_location", side_effect=fake_spec):
                with patch("services.migrations.importlib.util.module_from_spec", side_effect=fake_module):
                    result = _run(run_migrations(db))

        assert result is False  # At least one migration failed
        # Second migration still ran
        success_module.up.assert_awaited_once_with(pool)

    def test_outer_exception_returns_false(self):
        """Top-level exception in run_migrations returns False rather than propagating."""
        from services.migrations import run_migrations

        db = _make_db_service(pool=MagicMock())
        # Make pool.acquire raise immediately
        db.pool.acquire.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("pool dead"))
        db.pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        result = _run(run_migrations(db))
        assert result is False


@pytest.mark.unit
class TestRunMigrationsMultipleMixed:
    def test_all_applied_returns_true(self):
        """All migrations already applied — skipped — returns True."""
        from services.migrations import run_migrations

        file1 = _sortable_path_mock("0001_initial.py")
        file2 = _sortable_path_mock("0002_indexes.py")

        pool, conn = _make_pool(already_applied=1)  # Both already applied
        db = _make_db_service(pool=pool)

        with patch("services.migrations.Path.glob", return_value=iter([file1, file2])):
            result = _run(run_migrations(db))

        assert result is True
