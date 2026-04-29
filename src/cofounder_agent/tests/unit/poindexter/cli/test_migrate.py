"""Click CLI tests for ``poindexter migrate`` (gh#226).

The CLI commands are thin Click wrappers — DSN resolution + pool
factory + on-disk migration discovery. We patch ``_make_pool`` and the
on-disk migration list so the test suite exercises the Click glue
(option parsing, formatting, exit codes, --to / --all / --yes
semantics) without a live DB.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.migrate import migrate_group


# ---------------------------------------------------------------------------
# Helpers — fake pool + on-disk file list
# ---------------------------------------------------------------------------


def _make_fake_pool() -> MagicMock:
    """Asyncpg pool stub with the methods the migrate CLI touches."""
    pool = MagicMock()
    pool.close = AsyncMock(return_value=None)

    # The CLI uses ``async with pool.acquire() as conn`` for the
    # schema_migrations DDL/INSERT/DELETE/SELECT path. Each conn is
    # itself an async context manager.
    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock(return_value=None)
    fake_conn.fetch = AsyncMock(return_value=[])
    fake_conn.fetchval = AsyncMock(return_value=None)

    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=fake_conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    pool._fake_conn = fake_conn  # for assertions
    return pool


@pytest.fixture
def fake_pool():
    return _make_fake_pool()


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def patched_pool_factory(fake_pool):
    async def _make_pool():
        return fake_pool

    with patch(
        "poindexter.cli.migrate._make_pool", side_effect=_make_pool,
    ):
        yield fake_pool


# ---------------------------------------------------------------------------
# migrate status
# ---------------------------------------------------------------------------


class TestMigrateStatus:
    def test_help(self, runner):
        result = runner.invoke(migrate_group, ["status", "--help"])
        assert result.exit_code == 0
        assert "applied" in result.output.lower()

    def test_mixed_applied_and_pending(self, runner, patched_pool_factory):
        """status renders [✓] for applied, [ ] for pending + final counts."""
        files = [
            Path("0001_alpha.py"),
            Path("0002_beta.py"),
            Path("0003_gamma.py"),
        ]
        applied = {
            "0001_alpha.py": datetime(2026, 4, 28, tzinfo=timezone.utc),
            "0002_beta.py": datetime(2026, 4, 29, tzinfo=timezone.utc),
        }

        with patch(
            "poindexter.cli.migrate._list_migration_files",
            return_value=files,
        ), patch(
            "poindexter.cli.migrate._fetch_applied",
            AsyncMock(return_value=applied),
        ), patch(
            "poindexter.cli.migrate._ensure_migrations_table",
            AsyncMock(return_value=None),
        ):
            result = runner.invoke(migrate_group, ["status"])

        assert result.exit_code == 0, result.output
        assert "0001_alpha.py" in result.output
        assert "0002_beta.py" in result.output
        assert "0003_gamma.py" in result.output
        assert "applied 2026-04-28" in result.output
        assert "applied 2026-04-29" in result.output
        assert "pending" in result.output
        assert "Total: 3 migrations" in result.output
        assert "2 applied" in result.output
        assert "1 pending" in result.output

    def test_json_output(self, runner, patched_pool_factory):
        files = [Path("0001_alpha.py"), Path("0002_beta.py")]
        applied = {
            "0001_alpha.py": datetime(2026, 4, 28, tzinfo=timezone.utc),
        }
        with patch(
            "poindexter.cli.migrate._list_migration_files",
            return_value=files,
        ), patch(
            "poindexter.cli.migrate._fetch_applied",
            AsyncMock(return_value=applied),
        ), patch(
            "poindexter.cli.migrate._ensure_migrations_table",
            AsyncMock(return_value=None),
        ):
            result = runner.invoke(migrate_group, ["status", "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 2
        names = {row["name"] for row in payload}
        assert names == {"0001_alpha.py", "0002_beta.py"}
        applied_row = next(r for r in payload if r["name"] == "0001_alpha.py")
        pending_row = next(r for r in payload if r["name"] == "0002_beta.py")
        assert applied_row["applied"] is True
        assert applied_row["applied_at"] is not None
        assert pending_row["applied"] is False
        assert pending_row["applied_at"] is None


# ---------------------------------------------------------------------------
# migrate up
# ---------------------------------------------------------------------------


class TestMigrateUp:
    def test_happy_path_one_pending(self, runner, patched_pool_factory):
        """One pending migration → applies one, summary reads 'applied 1'."""
        files = [Path("0001_alpha.py"), Path("0002_beta.py")]
        before = {"0001_alpha.py": datetime(2026, 4, 28, tzinfo=timezone.utc)}
        after = dict(before)
        after["0002_beta.py"] = datetime(2026, 4, 29, tzinfo=timezone.utc)

        # ``_fetch_applied`` is called twice — before + after run_migrations.
        fetch_applied = AsyncMock(side_effect=[before, after])

        run_migrations_stub = AsyncMock(return_value=True)

        with patch(
            "poindexter.cli.migrate._list_migration_files",
            return_value=files,
        ), patch(
            "poindexter.cli.migrate._fetch_applied", fetch_applied,
        ), patch(
            "poindexter.cli.migrate._ensure_migrations_table",
            AsyncMock(return_value=None),
        ), patch(
            "services.migrations.run_migrations", run_migrations_stub,
        ):
            result = runner.invoke(migrate_group, ["up"])

        assert result.exit_code == 0, result.output
        assert "0002_beta.py" in result.output
        assert "Applied 1 migration" in result.output
        assert "applied 1" in result.output
        run_migrations_stub.assert_awaited_once()

    def test_no_pending_is_a_clean_no_op(self, runner, patched_pool_factory):
        files = [Path("0001_alpha.py")]
        applied = {"0001_alpha.py": datetime(2026, 4, 28, tzinfo=timezone.utc)}

        fetch_applied = AsyncMock(side_effect=[applied, applied])

        with patch(
            "poindexter.cli.migrate._list_migration_files",
            return_value=files,
        ), patch(
            "poindexter.cli.migrate._fetch_applied", fetch_applied,
        ), patch(
            "poindexter.cli.migrate._ensure_migrations_table",
            AsyncMock(return_value=None),
        ), patch(
            "services.migrations.run_migrations",
            AsyncMock(return_value=True),
        ):
            result = runner.invoke(migrate_group, ["up"])

        assert result.exit_code == 0, result.output
        assert "No new migrations to apply" in result.output
        assert "applied 0" in result.output

    def test_to_caps_at_target(self, runner, patched_pool_factory):
        """``--to 0103`` only runs migrations whose prefix sorts <= 0103."""
        files = [
            Path("0102_a.py"),
            Path("0103_b.py"),
            Path("0104_c.py"),
            Path("0105_d.py"),
        ]
        applied: dict[str, datetime] = {}

        fake_modules = {}
        for f in files:
            mod = MagicMock()
            mod.up = AsyncMock(return_value=None)
            fake_modules[f.name] = mod

        def _load_module(path: Path):
            return fake_modules[path.name]

        # Successive _fetch_applied calls: initial (empty) + final (after).
        # The CLI also looks up applied via _fetch_applied at the end to
        # compute pending_after.
        applied_after = {
            "0102_a.py": datetime(2026, 4, 28, tzinfo=timezone.utc),
            "0103_b.py": datetime(2026, 4, 28, tzinfo=timezone.utc),
        }

        fetch_applied = AsyncMock(side_effect=[applied, applied_after])

        with patch(
            "poindexter.cli.migrate._list_migration_files",
            return_value=files,
        ), patch(
            "poindexter.cli.migrate._fetch_applied", fetch_applied,
        ), patch(
            "poindexter.cli.migrate._load_migration_module",
            side_effect=_load_module,
        ), patch(
            "poindexter.cli.migrate._ensure_migrations_table",
            AsyncMock(return_value=None),
        ):
            result = runner.invoke(migrate_group, ["up", "--to", "0103"])

        assert result.exit_code == 0, result.output
        # Only 0102 and 0103 should have run.
        fake_modules["0102_a.py"].up.assert_awaited_once()
        fake_modules["0103_b.py"].up.assert_awaited_once()
        fake_modules["0104_c.py"].up.assert_not_awaited()
        fake_modules["0105_d.py"].up.assert_not_awaited()
        assert "Applied 2 migration" in result.output
        assert "0102_a.py" in result.output
        assert "0103_b.py" in result.output


# ---------------------------------------------------------------------------
# migrate down
# ---------------------------------------------------------------------------


class TestMigrateDown:
    def _make_modules(self, names: list[str]) -> dict[str, MagicMock]:
        """One module-stub per migration name, each with an async down()."""
        out = {}
        for n in names:
            mod = MagicMock()
            mod.down = AsyncMock(return_value=None)
            out[n] = mod
        return out

    def test_default_rolls_back_only_latest(self, runner, patched_pool_factory):
        names = ["0001_alpha.py", "0002_beta.py", "0003_gamma.py"]
        files = [Path(n) for n in names]
        applied = {
            n: datetime(2026, 4, 28, tzinfo=timezone.utc) for n in names
        }
        modules = self._make_modules(names)

        with patch(
            "poindexter.cli.migrate._list_migration_files",
            return_value=files,
        ), patch(
            "poindexter.cli.migrate._fetch_applied",
            AsyncMock(return_value=applied),
        ), patch(
            "poindexter.cli.migrate._ensure_migrations_table",
            AsyncMock(return_value=None),
        ), patch(
            "poindexter.cli.migrate._load_migration_module",
            side_effect=lambda p: modules[p.name],
        ):
            result = runner.invoke(migrate_group, ["down"])

        assert result.exit_code == 0, result.output
        # Only the latest gamma should have been rolled back.
        modules["0003_gamma.py"].down.assert_awaited_once()
        modules["0002_beta.py"].down.assert_not_awaited()
        modules["0001_alpha.py"].down.assert_not_awaited()
        assert "0003_gamma.py" in result.output
        assert "Rolled back 1 migration" in result.output

        # Verify the row was deleted from schema_migrations. The pool's
        # _fake_conn captures every execute() call.
        execute_calls = patched_pool_factory._fake_conn.execute.await_args_list
        delete_calls = [
            c for c in execute_calls
            if "DELETE FROM schema_migrations" in str(c)
        ]
        assert delete_calls, "expected a DELETE FROM schema_migrations"

    def test_down_to_rolls_back_strictly_newer(self, runner, patched_pool_factory):
        names = ["0001_alpha.py", "0002_beta.py", "0003_gamma.py", "0004_delta.py"]
        files = [Path(n) for n in names]
        applied = {
            n: datetime(2026, 4, 28, tzinfo=timezone.utc) for n in names
        }
        modules = self._make_modules(names)

        with patch(
            "poindexter.cli.migrate._list_migration_files",
            return_value=files,
        ), patch(
            "poindexter.cli.migrate._fetch_applied",
            AsyncMock(return_value=applied),
        ), patch(
            "poindexter.cli.migrate._ensure_migrations_table",
            AsyncMock(return_value=None),
        ), patch(
            "poindexter.cli.migrate._load_migration_module",
            side_effect=lambda p: modules[p.name],
        ):
            result = runner.invoke(migrate_group, ["down", "--to", "0002"])

        assert result.exit_code == 0, result.output
        # Everything strictly greater than 0002 should roll back: 0003, 0004.
        modules["0004_delta.py"].down.assert_awaited_once()
        modules["0003_gamma.py"].down.assert_awaited_once()
        modules["0002_beta.py"].down.assert_not_awaited()
        modules["0001_alpha.py"].down.assert_not_awaited()
        assert "Rolled back 2 migration" in result.output

    def test_down_all_without_yes_prompts(self, runner, patched_pool_factory):
        """``--all`` without ``--yes`` must show a confirmation prompt."""
        names = ["0001_alpha.py"]
        files = [Path(n) for n in names]
        applied = {n: datetime(2026, 4, 28, tzinfo=timezone.utc) for n in names}
        modules = self._make_modules(names)

        with patch(
            "poindexter.cli.migrate._list_migration_files",
            return_value=files,
        ), patch(
            "poindexter.cli.migrate._fetch_applied",
            AsyncMock(return_value=applied),
        ), patch(
            "poindexter.cli.migrate._ensure_migrations_table",
            AsyncMock(return_value=None),
        ), patch(
            "poindexter.cli.migrate._load_migration_module",
            side_effect=lambda p: modules[p.name],
        ):
            # Decline the prompt.
            result = runner.invoke(migrate_group, ["down", "--all"], input="n\n")

        # User said no → exit 1 + nothing was rolled back.
        assert result.exit_code == 1
        assert "Roll back EVERY applied migration" in result.output
        modules["0001_alpha.py"].down.assert_not_awaited()

    def test_down_all_with_yes_skips_prompt(self, runner, patched_pool_factory):
        names = ["0001_alpha.py", "0002_beta.py"]
        files = [Path(n) for n in names]
        applied = {n: datetime(2026, 4, 28, tzinfo=timezone.utc) for n in names}
        modules = self._make_modules(names)

        with patch(
            "poindexter.cli.migrate._list_migration_files",
            return_value=files,
        ), patch(
            "poindexter.cli.migrate._fetch_applied",
            AsyncMock(return_value=applied),
        ), patch(
            "poindexter.cli.migrate._ensure_migrations_table",
            AsyncMock(return_value=None),
        ), patch(
            "poindexter.cli.migrate._load_migration_module",
            side_effect=lambda p: modules[p.name],
        ):
            result = runner.invoke(migrate_group, ["down", "--all", "--yes"])

        assert result.exit_code == 0, result.output
        # Both got rolled back, in reverse order.
        modules["0001_alpha.py"].down.assert_awaited_once()
        modules["0002_beta.py"].down.assert_awaited_once()
        assert "Rolled back 2 migration" in result.output

    def test_down_skips_module_without_down_callable(self, runner, patched_pool_factory):
        """Modules without down() emit a ``skipped`` notice but don't crash."""
        names = ["0001_alpha.py"]
        files = [Path(n) for n in names]
        applied = {n: datetime(2026, 4, 28, tzinfo=timezone.utc) for n in names}
        # Build a module with NO down/rollback_migration callable.
        bare = MagicMock(spec=[])  # spec=[] disables auto-attribute creation

        with patch(
            "poindexter.cli.migrate._list_migration_files",
            return_value=files,
        ), patch(
            "poindexter.cli.migrate._fetch_applied",
            AsyncMock(return_value=applied),
        ), patch(
            "poindexter.cli.migrate._ensure_migrations_table",
            AsyncMock(return_value=None),
        ), patch(
            "poindexter.cli.migrate._load_migration_module",
            return_value=bare,
        ):
            result = runner.invoke(migrate_group, ["down"])

        # Non-zero because there's an issue worth surfacing.
        assert result.exit_code == 1
        assert "no down" in result.output.lower() or "skipped" in result.output.lower()

    def test_all_and_to_are_mutually_exclusive(self, runner):
        result = runner.invoke(
            migrate_group, ["down", "--all", "--to", "0001"],
        )
        assert result.exit_code == 2
        assert "mutually exclusive" in result.output
