"""Unit tests for ``services.module_migrations.run_module_migrations``.

These tests do NOT need a real DB — they mock ``pool`` so the runner's
file discovery + ordering + already-applied-skip logic is pinned in
isolation. The end-to-end test against a real DB lives in
``tests/integration_db/test_module_migrations.py``.
"""

from __future__ import annotations

import pytest

from services.module_migrations import (
    ModuleMigrationResult,
    run_module_migrations,
)


class _FakeConn:
    """Records fetchval/execute calls so tests can assert on them."""

    def __init__(self, already_applied_names: set[str] | None = None):
        self.already_applied = already_applied_names or set()
        self.executes: list[tuple[str, tuple]] = []

    async def fetchval(self, _sql: str, _module_name: str, migration_name: str):
        return 1 if migration_name in self.already_applied else None

    async def execute(self, sql: str, *args):
        self.executes.append((sql, args))


class _FakePool:
    """Returns a fresh _FakeConn for each acquire() call. Tests can read
    .conns afterwards to make assertions about what was inserted."""

    def __init__(self, already_applied_names: set[str] | None = None):
        self._already = already_applied_names or set()
        self.conns: list[_FakeConn] = []

    def acquire(self):
        conn = _FakeConn(self._already)
        self.conns.append(conn)
        return _AcquireCM(conn)


class _AcquireCM:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_a):
        return False


@pytest.mark.unit
async def test_returns_noop_when_migrations_dir_missing(tmp_path):
    """A module that doesn't ship migrations is normal — return a
    clean no-op result with no error."""
    pool = _FakePool()
    result = await run_module_migrations(
        pool=pool,
        module_name="content",
        migrations_dir=tmp_path / "does_not_exist",
    )
    assert isinstance(result, ModuleMigrationResult)
    assert result.module_name == "content"
    assert result.applied == 0
    assert result.skipped == 0
    assert result.failed == 0
    assert result.ok is True


@pytest.mark.unit
async def test_returns_noop_when_dir_exists_but_empty(tmp_path):
    """An empty migrations directory is also fine — no-op."""
    (tmp_path / "migrations").mkdir()
    pool = _FakePool()
    result = await run_module_migrations(
        pool=pool,
        module_name="content",
        migrations_dir=tmp_path / "migrations",
    )
    assert result.applied == 0
    assert result.failed == 0


@pytest.mark.unit
async def test_applies_in_alphabetical_order(tmp_path):
    """Filenames sort lexically. The runner must apply b_*.py only
    after a_*.py — otherwise migrations that depend on prior schema
    state silently break."""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    call_order: list[str] = []

    def _make_migration(name: str) -> None:
        body = (
            "async def up(pool):\n"
            f"    import _order_log\n"
            f"    _order_log.calls.append('{name}')\n"
        )
        (migrations / name).write_text(body)

    _make_migration("a_first.py")
    _make_migration("b_second.py")
    _make_migration("c_third.py")

    # Inject a shared module the synthetic migrations can write into.
    import sys
    import types
    order_mod = types.ModuleType("_order_log")
    order_mod.calls = call_order  # type: ignore[attr-defined]
    sys.modules["_order_log"] = order_mod

    try:
        pool = _FakePool()
        result = await run_module_migrations(
            pool=pool,
            module_name="content",
            migrations_dir=migrations,
        )
    finally:
        sys.modules.pop("_order_log", None)

    assert result.applied == 3
    assert result.failed == 0
    assert call_order == ["a_first.py", "b_second.py", "c_third.py"]


@pytest.mark.unit
async def test_skips_already_applied_migrations(tmp_path):
    """If module_schema_migrations already has a row for a given
    file, the runner skips it instead of re-running. Idempotency
    is the whole point."""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "init.py").write_text(
        "async def up(pool):\n    pass\n"
    )

    pool = _FakePool(already_applied_names={"init.py"})
    result = await run_module_migrations(
        pool=pool,
        module_name="content",
        migrations_dir=migrations,
    )
    assert result.applied == 0
    assert result.skipped == 1
    assert result.failed == 0


@pytest.mark.unit
async def test_logs_and_counts_when_migration_raises(tmp_path, caplog):
    """A migration whose up() raises is counted as failed but does
    NOT abort the rest of the run. Blast radius of one bad file is
    exactly one file."""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "a_good.py").write_text(
        "async def up(pool):\n    pass\n"
    )
    (migrations / "b_bad.py").write_text(
        "async def up(pool):\n    raise RuntimeError('boom')\n"
    )
    (migrations / "c_also_good.py").write_text(
        "async def up(pool):\n    pass\n"
    )

    pool = _FakePool()
    with caplog.at_level("ERROR", logger="services.module_migrations"):
        result = await run_module_migrations(
            pool=pool,
            module_name="content",
            migrations_dir=migrations,
        )

    assert result.applied == 2
    assert result.failed == 1
    assert result.ok is False
    bad_errors = [
        r for r in caplog.records
        if r.name == "services.module_migrations"
        and "b_bad.py" in r.message
    ]
    assert len(bad_errors) >= 1


@pytest.mark.unit
async def test_skips_migration_missing_up_function(tmp_path):
    """A .py file without an async up(pool) is a malformed migration;
    log + count as failed, don't crash."""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "init.py").write_text("# no up() defined\n")

    pool = _FakePool()
    result = await run_module_migrations(
        pool=pool,
        module_name="content",
        migrations_dir=migrations,
    )
    assert result.applied == 0
    assert result.failed == 1
