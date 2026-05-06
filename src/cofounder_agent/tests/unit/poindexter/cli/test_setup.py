"""Unit tests for ``poindexter.cli.setup._run_migrations`` (gh#380).

The setup wizard's ``_run_migrations`` was previously a proxy check —
it just looked at ``information_schema.tables`` for ``app_settings``
and used that as a stand-in for "previously migrated". Issue #380
replaced it with a real call to ``services.migrations.run_migrations``
so a fresh ``poindexter setup`` against an empty DB ends with every
table the next steps need (notably ``oauth_clients`` for step 4 OAuth
provisioning).

These tests cover the runner-invocation contract:

* Empty DB → migrations actually run → returns ``(True, ...)`` with an
  ``applied N`` summary.
* Already-migrated DB → idempotent fast no-op → returns
  ``(True, "already up to date ...")`` with the runner still invoked
  exactly once (so the runner gets to record any new files added since).
* Runner reports a per-file failure → returns ``(False, ...)`` and the
  reason surfaces the real error context, not a stale ``alembic`` hint.
* Runner raises → returns ``(False, ...)`` with the exception type +
  message, so the operator can see the underlying failure.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.cli.setup import _run_migrations


def _make_fake_pool(*, before_count: int, after_count: int) -> MagicMock:
    """Build an asyncpg-pool stub for the ``schema_migrations`` count probe.

    The before/after fetchval values are returned in order so the test
    can simulate the count delta after a runner invocation.
    """
    pool = MagicMock()
    pool.close = AsyncMock(return_value=None)

    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock(return_value=None)
    # First COUNT(*) is "before", second is "after".
    fake_conn.fetchval = AsyncMock(side_effect=[before_count, after_count])

    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=fake_conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    pool._fake_conn = fake_conn  # for assertions
    return pool


def _make_empty_db_pool(after_count: int) -> MagicMock:
    """Pool stub where the first fetchval raises (no schema_migrations table yet).

    The runner's ``_MIGRATIONS_TABLE_SQL`` creates the tracking table
    on first invocation, so the "after" count succeeds.
    """
    pool = MagicMock()
    pool.close = AsyncMock(return_value=None)

    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock(return_value=None)
    # First call raises (table missing), second returns the after-count.
    fake_conn.fetchval = AsyncMock(
        side_effect=[
            Exception("relation \"schema_migrations\" does not exist"),
            after_count,
        ]
    )

    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=fake_conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    pool._fake_conn = fake_conn
    return pool


@pytest.fixture
def stub_create_pool():
    """Patch ``asyncpg.create_pool`` to return whatever the test installs."""
    with patch("asyncpg.create_pool", new_callable=AsyncMock) as mock:
        yield mock


class TestRunMigrationsEmptyDb:
    """Empty DB — migrations actually run end-to-end."""

    def test_empty_db_runs_migrations_and_reports_count(self, stub_create_pool):
        """Fresh DB → runner is invoked → success message includes applied count."""
        # Empty DB: schema_migrations doesn't exist before, has 12 rows after.
        pool = _make_empty_db_pool(after_count=12)
        stub_create_pool.return_value = pool

        run_migrations_stub = AsyncMock(return_value=True)

        with patch(
            "services.migrations.run_migrations", run_migrations_stub,
        ):
            ok, reason = asyncio.run(_run_migrations("postgresql://x/y"))

        assert ok is True, reason
        assert "applied 12" in reason
        assert "12 total" in reason
        # Runner was actually invoked (not just a proxy check).
        run_migrations_stub.assert_awaited_once()
        # Adapter has the pool the CLI built.
        adapter = run_migrations_stub.await_args.args[0]
        assert adapter.pool is pool
        # Pool was cleaned up.
        pool.close.assert_awaited_once()

    def test_empty_db_no_alembic_reference_in_message(self, stub_create_pool):
        """Regression for #380: the bogus ``alembic upgrade head`` hint is gone."""
        pool = _make_empty_db_pool(after_count=12)
        stub_create_pool.return_value = pool

        with patch(
            "services.migrations.run_migrations",
            AsyncMock(return_value=True),
        ):
            ok, reason = asyncio.run(_run_migrations("postgresql://x/y"))

        assert "alembic" not in reason.lower()


class TestRunMigrationsIdempotent:
    """Already-migrated DB — fast no-op, runner still consulted."""

    def test_already_up_to_date_returns_true_quickly(self, stub_create_pool):
        """Re-running setup against an up-to-date DB is a clean no-op."""
        # Same count before and after → nothing was applied this run.
        pool = _make_fake_pool(before_count=12, after_count=12)
        stub_create_pool.return_value = pool

        run_migrations_stub = AsyncMock(return_value=True)
        with patch(
            "services.migrations.run_migrations", run_migrations_stub,
        ):
            ok, reason = asyncio.run(_run_migrations("postgresql://x/y"))

        assert ok is True, reason
        assert "already up to date" in reason
        assert "12 migrations applied" in reason
        # Runner is still consulted — that's how we'd pick up new files
        # added since the last setup run.
        run_migrations_stub.assert_awaited_once()
        pool.close.assert_awaited_once()


class TestRunMigrationsFailure:
    """Per-migration failure surfaces honestly + cleans up the pool."""

    def test_runner_returns_false_means_failure_with_partial_count(
        self, stub_create_pool,
    ):
        """``run_migrations`` returns False when any file failed."""
        # 5 applied before, 8 after → 3 succeeded before the failing one.
        pool = _make_fake_pool(before_count=5, after_count=8)
        stub_create_pool.return_value = pool

        with patch(
            "services.migrations.run_migrations",
            AsyncMock(return_value=False),
        ):
            ok, reason = asyncio.run(_run_migrations("postgresql://x/y"))

        assert ok is False
        assert "failed" in reason.lower()
        assert "applied 3" in reason
        pool.close.assert_awaited_once()

    def test_runner_raises_returns_false_with_exception_context(
        self, stub_create_pool,
    ):
        """An unexpected exception bubbles up as a (False, reason) pair."""
        pool = _make_fake_pool(before_count=0, after_count=0)
        stub_create_pool.return_value = pool

        with patch(
            "services.migrations.run_migrations",
            AsyncMock(side_effect=RuntimeError("connection lost mid-migration")),
        ):
            ok, reason = asyncio.run(_run_migrations("postgresql://x/y"))

        assert ok is False
        assert "RuntimeError" in reason
        assert "connection lost mid-migration" in reason
        pool.close.assert_awaited_once()

    def test_create_pool_failure_returns_clean_error(self, stub_create_pool):
        """If we can't even build the pool, we report it without crashing."""
        stub_create_pool.side_effect = OSError("connection refused")

        ok, reason = asyncio.run(_run_migrations("postgresql://x/y"))

        assert ok is False
        assert "OSError" in reason
        assert "connection refused" in reason


class TestRunMigrationsSeedsDefaults:
    """After a successful migration run the CLI seeds app_settings (#379)."""

    def test_seeder_invoked_after_successful_migration(self, stub_create_pool):
        """seed_all_defaults() runs against the same pool after migrations."""
        pool = _make_empty_db_pool(after_count=12)
        stub_create_pool.return_value = pool

        seed_stub = AsyncMock(return_value=42)
        with patch(
            "services.migrations.run_migrations",
            AsyncMock(return_value=True),
        ), patch(
            "services.settings_defaults.seed_all_defaults", seed_stub,
        ):
            ok, reason = asyncio.run(_run_migrations("postgresql://x/y"))

        assert ok is True, reason
        assert "applied 12" in reason
        assert "seeded 42 default" in reason
        seed_stub.assert_awaited_once()
        seeded_pool = seed_stub.await_args.args[0]
        assert seeded_pool is pool

    def test_seeder_invoked_on_idempotent_run(self, stub_create_pool):
        """Even when no migrations applied this run, the seeder still runs.

        This is what closes the gap on operators upgrading from a pre-#379
        install: their DB is already at HEAD, but the seeder still tops
        up any keys that never had an explicit migration.
        """
        pool = _make_fake_pool(before_count=12, after_count=12)
        stub_create_pool.return_value = pool

        seed_stub = AsyncMock(return_value=7)
        with patch(
            "services.migrations.run_migrations",
            AsyncMock(return_value=True),
        ), patch(
            "services.settings_defaults.seed_all_defaults", seed_stub,
        ):
            ok, reason = asyncio.run(_run_migrations("postgresql://x/y"))

        assert ok is True, reason
        assert "already up to date" in reason
        assert "seeded 7 default" in reason
        seed_stub.assert_awaited_once()

    def test_seeder_zero_count_does_not_advertise_seed(self, stub_create_pool):
        """If seeder finds nothing to do, the message stays clean."""
        pool = _make_fake_pool(before_count=12, after_count=12)
        stub_create_pool.return_value = pool

        with patch(
            "services.migrations.run_migrations",
            AsyncMock(return_value=True),
        ), patch(
            "services.settings_defaults.seed_all_defaults",
            AsyncMock(return_value=0),
        ):
            ok, reason = asyncio.run(_run_migrations("postgresql://x/y"))

        assert ok is True, reason
        assert "already up to date" in reason
        # No "seeded" suffix when nothing was inserted
        assert "seeded" not in reason

    def test_seeder_failure_does_not_fail_setup(self, stub_create_pool):
        """seed_all_defaults() raising surfaces in the message but ok=True.

        Migrations themselves succeeded; the seeder is best-effort because
        the lazy SettingsService default path is still a working fallback.
        """
        pool = _make_empty_db_pool(after_count=12)
        stub_create_pool.return_value = pool

        with patch(
            "services.migrations.run_migrations",
            AsyncMock(return_value=True),
        ), patch(
            "services.settings_defaults.seed_all_defaults",
            AsyncMock(side_effect=RuntimeError("seed exploded")),
        ):
            ok, reason = asyncio.run(_run_migrations("postgresql://x/y"))

        assert ok is True
        assert "settings seed FAILED" in reason
        assert "RuntimeError" in reason
        assert "seed exploded" in reason
