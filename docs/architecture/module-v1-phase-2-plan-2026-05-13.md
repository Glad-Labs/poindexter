# Module v1 — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-module migration runner so every registered `Module` can ship its own DB migrations alongside its code. Substrate migrations under `services/migrations/` continue unchanged; modules now have a parallel `migrations/` directory inside their package.

**Architecture:** Mirror `services/migrations/__init__.py:run_migrations()` shape but key the tracking table on `(module_name, migration_name)` so two modules can each have a `init.py` migration without colliding. Wire the call into `utils/startup_manager._run_migrations()` AFTER the substrate runner completes — substrate tables (including the new `module_schema_migrations` table) must exist before any module's migrate() runs.

**Tech Stack:** Python 3.13, asyncpg, importlib (dynamic migration discovery), pytest + pytest-asyncio, the existing `integration_db` test harness (per-session disposable Postgres DB).

**Spec reference:** `docs/architecture/module-v1.md` — Component 2 (Per-module migrations). Umbrella tracker: [Glad-Labs/poindexter#490](https://github.com/Glad-Labs/poindexter/issues/490).

**Scope:** Phase 2 only — the runner + substrate table + boot wiring. Phase 3 will produce the first concrete `Module` whose `migrate()` exercises this code path; Phase 2 ships with synthetic test modules instead.

---

## File Structure

This plan creates 3 new files, modifies 1 existing file, and adds 2 test files.

- **Create** `src/cofounder_agent/services/migrations/<timestamp>_create_module_schema_migrations.py` — substrate migration that creates the tracking table.
- **Create** `src/cofounder_agent/services/module_migrations.py` — the per-module migration runner. Public API: `async def run_module_migrations(pool, module_name, migrations_dir) -> RunResult`.
- **Modify** `src/cofounder_agent/utils/startup_manager.py` — after substrate `run_migrations` completes, iterate `get_modules()` and call each module's `migrate(pool)`. Best-effort: a module migration failure logs + continues; it does NOT halt startup. (Same blast-radius posture as the substrate runner.)
- **Create** `src/cofounder_agent/tests/unit/services/test_module_migrations.py` — unit tests for the runner (no-pool, missing dir, file ordering, idempotency-on-rerun, broken-migration-skipped).
- **Create** `src/cofounder_agent/tests/integration_db/test_module_migrations.py` — end-to-end against a disposable `poindexter_test_<hex>` DB: register two test modules with two migrations each, verify both apply, verify rerun is no-op, verify `module_schema_migrations` rows land correctly.

The runner lives at `services/module_migrations.py` (not under `services/migrations/`) so it's clear that it's the _infrastructure for running module migrations_, not itself a substrate migration.

---

## Task 1: Substrate migration creating `module_schema_migrations` table

**Files:**

- Create: `src/cofounder_agent/services/migrations/<timestamp>_create_module_schema_migrations.py`

- [ ] **Step 1: Generate the migration filename**

Run: `python scripts/new-migration.py "create module_schema_migrations table for Module v1 phase 2"`

Expected: script prints the new migration filename, e.g. `20260513_180000_create_module_schema_migrations.py`. Note the actual filename for the subsequent steps.

If the script doesn't exist or fails, fall back to manually creating `src/cofounder_agent/services/migrations/<UTC-now>_create_module_schema_migrations.py` using the format `YYYYMMDD_HHMMSS_<slug>.py`.

- [ ] **Step 2: Replace the generated body with the actual migration**

Edit the new file to contain:

```python
"""Create the ``module_schema_migrations`` table that records every
per-module migration that's been applied.

Phase 2 of Module v1 (Glad-Labs/poindexter#490). Substrate migrations
continue to use the existing ``schema_migrations`` table; modules
record into THIS table so two modules can have a migration named
``init.py`` without colliding.

The compound key (module_name, migration_name) is the natural
identity. ``applied_at`` is the audit timestamp.
"""

from __future__ import annotations

_UP_SQL = """
CREATE TABLE IF NOT EXISTS module_schema_migrations (
    id            SERIAL PRIMARY KEY,
    module_name   VARCHAR(64)  NOT NULL,
    migration_name VARCHAR(255) NOT NULL,
    applied_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (module_name, migration_name)
);
CREATE INDEX IF NOT EXISTS module_schema_migrations_module_idx
    ON module_schema_migrations (module_name);
"""

_DOWN_SQL = "DROP TABLE IF EXISTS module_schema_migrations"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_UP_SQL)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DOWN_SQL)
```

- [ ] **Step 3: Verify the migration file parses**

Run: `python -c "import ast; ast.parse(open('src/cofounder_agent/services/migrations/<the-new-filename>').read()); print('OK')"`

Expected: `OK`.

- [ ] **Step 4: Run the migration against the local DB**

Run: `cd src/cofounder_agent && poetry run python -c "
import asyncio
from utils.startup_manager import StartupManager # noqa: F401 — just to confirm the pool wiring path
from services.migrations import run_migrations
from services.database_service import DatabaseService

async def main():
svc = DatabaseService()
await svc.initialize()
ok = await run_migrations(svc)
print('ok=', ok)
async with svc.pool.acquire() as c:
rows = await c.fetch('SELECT to_regclass(\\'module_schema_migrations\\') AS exists')
print(rows[0]['exists'])
await svc.close()

asyncio.run(main())
"`

Expected: prints `ok= True` and `module_schema_migrations` (the table object, confirming the migration applied).

If your local DB isn't reachable from the host (worker-in-Docker setup), skip this step — Task 4's integration test will exercise it against the disposable test DB instead.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/migrations/<the-new-filename>
git commit -m "feat(db): module_schema_migrations table (#490 phase 2)

Records per-module migrations keyed on (module_name, migration_name)
so two modules can each ship a migration named init.py without
colliding. Substrate migrations continue using schema_migrations
unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Implement `services/module_migrations.py` runner

**Files:**

- Create: `src/cofounder_agent/services/module_migrations.py`

- [ ] **Step 1: Read the existing migration runner for shape reference**

Run: `cat src/cofounder_agent/services/migrations/__init__.py`

Expected: `run_migrations(database_service) -> bool` with the per-file `importlib.util.spec_from_file_location` discovery + `schema_migrations` table tracking pattern. Mirror this shape but accept a pool directly + key the tracking on `(module_name, migration_name)`.

- [ ] **Step 2: Create the runner file**

Write `src/cofounder_agent/services/module_migrations.py`:

```python
"""Per-module migration runner — applies one ``Module``'s migrations.

Phase 2 of Module v1 (Glad-Labs/poindexter#490). Each ``Module``
package can ship a ``migrations/`` subdirectory whose files follow
the SAME convention as ``services/migrations/`` (Python files with
an ``async def up(pool)``). The runner walks them in alphabetical
order, records each in ``module_schema_migrations`` keyed on
``(module_name, migration_name)``, and skips already-applied
migrations on rerun.

Public API:

    await run_module_migrations(pool, module_name, migrations_dir)

Returns a ``ModuleMigrationResult`` summarising applied / skipped /
failed counts. Failures are LOGGED + COUNTED, never raised — one
broken migration in module A must not block module B's migrations.
This matches ``services/migrations/__init__.py``'s posture.

The substrate's ``module_schema_migrations`` table must exist
before this runner is called. The substrate migration shipped in
Phase 2 Task 1 creates it; the boot wiring in
``utils/startup_manager.py`` calls this runner AFTER substrate
``run_migrations`` so the ordering is automatic.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ModuleMigrationResult:
    """What ``run_module_migrations`` produced for one module."""

    module_name: str
    applied: int
    skipped: int
    failed: int

    @property
    def ok(self) -> bool:
        """True if every migration either applied cleanly or was already
        applied. False if any failed."""
        return self.failed == 0


async def run_module_migrations(
    pool: Any,
    module_name: str,
    migrations_dir: Path,
) -> ModuleMigrationResult:
    """Apply every migration in ``migrations_dir`` for ``module_name``.

    Args:
        pool: asyncpg pool. The substrate ``module_schema_migrations``
            table must already exist.
        module_name: the canonical module slug (matches
            ``ModuleManifest.name`` — caller is responsible for
            passing it).
        migrations_dir: ``Path`` to the module's migrations directory.
            If it doesn't exist, the runner logs at INFO and returns
            a no-op result (a module without migrations is normal).

    Returns:
        ``ModuleMigrationResult`` with per-module counters.
    """
    if not migrations_dir.exists() or not migrations_dir.is_dir():
        logger.info(
            "module_migrations: %s has no migrations/ directory at %s — "
            "nothing to apply",
            module_name, migrations_dir,
        )
        return ModuleMigrationResult(
            module_name=module_name, applied=0, skipped=0, failed=0,
        )

    migration_files = sorted(
        [
            f for f in migrations_dir.glob("*.py")
            if f.name != "__init__.py" and not f.name.startswith("_")
        ]
    )

    if not migration_files:
        logger.info("module_migrations: %s has no migration files", module_name)
        return ModuleMigrationResult(
            module_name=module_name, applied=0, skipped=0, failed=0,
        )

    logger.info(
        "module_migrations: %s — found %d migration file(s)",
        module_name, len(migration_files),
    )

    applied = 0
    skipped = 0
    failed = 0

    for migration_file in migration_files:
        migration_name = migration_file.name
        try:
            async with pool.acquire() as conn:
                already_applied = await conn.fetchval(
                    "SELECT id FROM module_schema_migrations "
                    "WHERE module_name = $1 AND migration_name = $2",
                    module_name, migration_name,
                )

            if already_applied:
                logger.debug(
                    "module_migrations: skipping (already applied): %s/%s",
                    module_name, migration_name,
                )
                skipped += 1
                continue

            spec = importlib.util.spec_from_file_location(
                f"{module_name}_migration_{migration_name[:-3]}",
                migration_file,
            )
            if spec is None or spec.loader is None:
                logger.warning(
                    "module_migrations: cannot load %s/%s — invalid spec",
                    module_name, migration_name,
                )
                failed += 1
                continue
            migration_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration_module)

            up_fn = getattr(migration_module, "up", None)
            if up_fn is None or not callable(up_fn):
                logger.warning(
                    "module_migrations: %s/%s missing async up(pool) — "
                    "skipping",
                    module_name, migration_name,
                )
                failed += 1
                continue

            logger.info(
                "module_migrations: applying %s/%s",
                module_name, migration_name,
            )
            await up_fn(pool)

            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO module_schema_migrations "
                    "(module_name, migration_name) VALUES ($1, $2) "
                    "ON CONFLICT (module_name, migration_name) DO NOTHING",
                    module_name, migration_name,
                )

            logger.info(
                "module_migrations: applied %s/%s",
                module_name, migration_name,
            )
            applied += 1

        except Exception:
            logger.error(
                "module_migrations: failed %s/%s",
                module_name, migration_name, exc_info=True,
            )
            failed += 1

    logger.info(
        "module_migrations: %s done — applied=%d skipped=%d failed=%d",
        module_name, applied, skipped, failed,
    )
    return ModuleMigrationResult(
        module_name=module_name, applied=applied, skipped=skipped, failed=failed,
    )


__all__ = ["ModuleMigrationResult", "run_module_migrations"]
```

- [ ] **Step 3: Verify the file parses + lints**

Run: `python -c "import ast; ast.parse(open('src/cofounder_agent/services/module_migrations.py').read()); print('OK')"`

Run: `cd src/cofounder_agent && poetry run ruff check services/module_migrations.py`

Expected: `OK` then `All checks passed!`.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/services/module_migrations.py
git commit -m "feat(plugins): per-module migration runner (#490 phase 2)

services/module_migrations.run_module_migrations(pool, name, dir)
walks one Module's migrations/ directory, applies missing files
via async up(pool), records each in module_schema_migrations
(compound key on module_name + migration_name).

Same posture as services/migrations: log + count failures, never
raise — one broken migration in module A doesn't block module B.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Unit tests for the runner

**Files:**

- Create: `src/cofounder_agent/tests/unit/services/test_module_migrations.py`

- [ ] **Step 1: Create the test file**

Write `src/cofounder_agent/tests/unit/services/test_module_migrations.py`:

```python
"""Unit tests for ``services.module_migrations.run_module_migrations``.

These tests do NOT need a real DB — they mock ``pool`` so the runner's
file discovery + ordering + already-applied-skip logic is pinned in
isolation. The end-to-end test against a real DB lives in
``tests/integration_db/test_module_migrations.py``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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

    async def fetchval(self, _sql: str, module_name: str, migration_name: str):
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

    async def __aexit__(self, *a):
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
    # Use a side effect that records ORDER OF up() calls
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

    # Inject a shared module for the migrations to write into.
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
    # The error log mentions the bad migration
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
```

- [ ] **Step 2: Run the tests, all should pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_module_migrations.py -v`

Expected: 6 tests collected, 6 PASSED.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/tests/unit/services/test_module_migrations.py
git commit -m "test(plugins): unit tests for module_migrations runner (#490 phase 2)

Covers: missing dir / empty dir / alphabetical ordering /
idempotency on rerun / broken-migration-counted-as-failed /
malformed-migration-without-up-function.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Integration test against a disposable DB

**Files:**

- Create: `src/cofounder_agent/tests/integration_db/test_module_migrations.py`

- [ ] **Step 1: Create the integration test**

Write `src/cofounder_agent/tests/integration_db/test_module_migrations.py`:

```python
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


pytestmark = pytest.mark.integration_db


async def _write_migration(d: Path, name: str, sql: str) -> None:
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

    await _write_migration(
        mod_a, "init.py",
        "CREATE TABLE IF NOT EXISTS _phase2_test_a_init (id SERIAL PRIMARY KEY)",
    )
    await _write_migration(
        mod_a, "20260601_120000_add_col.py",
        "ALTER TABLE _phase2_test_a_init ADD COLUMN IF NOT EXISTS extra INT",
    )
    await _write_migration(
        mod_b, "init.py",
        "CREATE TABLE IF NOT EXISTS _phase2_test_b_init (id SERIAL PRIMARY KEY)",
    )
    await _write_migration(
        mod_b, "20260601_130000_seed.py",
        "INSERT INTO _phase2_test_b_init DEFAULT VALUES",
    )

    try:
        ra = await run_module_migrations(test_pool, "module_a", mod_a)
        rb = await run_module_migrations(test_pool, "module_b", mod_b)

        assert ra.applied == 2 and ra.failed == 0
        assert rb.applied == 2 and rb.failed == 0

        async with test_pool.acquire() as conn:
            # Tables exist
            a_exists = await conn.fetchval(
                "SELECT to_regclass('_phase2_test_a_init')"
            )
            b_exists = await conn.fetchval(
                "SELECT to_regclass('_phase2_test_b_init')"
            )
            assert a_exists is not None
            assert b_exists is not None

            # Tracking rows landed under the right (module, migration) keys
            rows = await conn.fetch(
                "SELECT module_name, migration_name FROM module_schema_migrations "
                "WHERE module_name IN ('module_a', 'module_b') "
                "ORDER BY module_name, migration_name"
            )
            names = [(r["module_name"], r["migration_name"]) for r in rows]
            assert ("module_a", "init.py") in names
            assert ("module_a", "20260601_120000_add_col.py") in names
            assert ("module_b", "init.py") in names
            assert ("module_b", "20260601_130000_seed.py") in names
            # Crucially: module_a's init.py and module_b's init.py are BOTH
            # present — proving the compound key works
            init_rows = [n for n in names if n[1] == "init.py"]
            assert len(init_rows) == 2
    finally:
        # Cleanup so reruns are clean even outside transaction isolation
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
    await _write_migration(
        mod, "init.py",
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
```

- [ ] **Step 2: Run the integration test**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_module_migrations.py -v`

Expected: 2 tests collected, 2 PASSED. If the harness skips (no live DB reachable from the host), this is also acceptable for this phase — record the skip reason and proceed; the unit tests + CI will exercise it.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/tests/integration_db/test_module_migrations.py
git commit -m "test(plugins): integration_db test for module_migrations (#490 phase 2)

Two synthetic modules with overlapping init.py filenames — both
must land under the right compound key without colliding. Plus a
rerun no-op test pinning idempotency.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Boot wiring — call module migrations after substrate migrations

**Files:**

- Modify: `src/cofounder_agent/utils/startup_manager.py`

- [ ] **Step 1: Locate the substrate migration call site**

Run: `grep -n 'run_migrations\|_run_migrations' src/cofounder_agent/utils/startup_manager.py`

Expected: lines around 97 (call site) and 271-313 (the `_run_migrations` method body).

- [ ] **Step 2: Append the module-migration pass to `_run_migrations`**

In `src/cofounder_agent/utils/startup_manager.py`, find the existing block at the end of `_run_migrations`:

```python
        try:
            from services.settings_defaults import seed_all_defaults
            ...
            inserted = await seed_all_defaults(self.database_service.pool)
            ...
        except Exception as e:
            logger.warning(
                f"   [WARNING] settings_defaults seed failed: {e!s} ..."
```

Append AFTER the closing `except` of that try block (and before the method ends) the following:

```python

        # Module v1 Phase 2 — per-module migrations. Substrate migrations
        # (including module_schema_migrations table itself) have already
        # run above; now walk every registered Module and apply its own.
        # Best-effort: a module migration failure logs + continues. The
        # blast radius of one broken module's migration is one module.
        try:
            from inspect import iscoroutinefunction
            from pathlib import Path

            from plugins.registry import get_modules
            from services.module_migrations import run_module_migrations

            modules = get_modules()
            if not modules:
                logger.debug("   [INFO] module_migrations: no modules registered")
            else:
                pool = self.database_service.pool if self.database_service else None
                if pool is None:
                    logger.warning(
                        "   [WARNING] module_migrations: no pool — skipping"
                    )
                else:
                    for mod in modules:
                        try:
                            manifest = mod.manifest()
                            mod_name = manifest.name
                            # Migrations live alongside the module package
                            # source — discover via the module's __module__
                            # for fully-imported instances. Fallback to a
                            # ``migrations_dir`` attribute if the module
                            # exposes one explicitly (test modules use
                            # this hook).
                            migrations_dir = getattr(mod, "migrations_dir", None)
                            if migrations_dir is None:
                                pkg_file = getattr(
                                    type(mod).__module__, "__file__", None
                                )
                                if pkg_file:
                                    migrations_dir = Path(pkg_file).parent / "migrations"
                            if migrations_dir is None:
                                logger.info(
                                    "   [INFO] module_migrations: %s — "
                                    "no migrations/ resolvable, skipping",
                                    mod_name,
                                )
                                continue
                            result = await run_module_migrations(
                                pool, mod_name, Path(migrations_dir),
                            )
                            logger.info(
                                "   [OK] module_migrations: %s — "
                                "applied=%d skipped=%d failed=%d",
                                mod_name, result.applied, result.skipped,
                                result.failed,
                            )
                            # Some test modules expose an async post_migrate
                            # hook; not part of the Phase 1 Protocol but
                            # supported for forward-compat with Phase 3.
                            post = getattr(mod, "post_migrate", None)
                            if iscoroutinefunction(post):
                                await post(pool)  # type: ignore[misc]
                        except Exception as inner:
                            logger.warning(
                                "   [WARNING] module_migrations: module "
                                "%r failed — %s",
                                mod, inner, exc_info=True,
                            )
        except Exception as e:
            logger.warning(
                f"   [WARNING] module_migrations bootstrap error: {e!s} "
                "(proceeding anyway)",
                exc_info=True,
            )
```

- [ ] **Step 3: Verify the file parses + lints**

Run: `python -c "import ast; ast.parse(open('src/cofounder_agent/utils/startup_manager.py').read()); print('OK')"`

Run: `cd src/cofounder_agent && poetry run ruff check utils/startup_manager.py`

Expected: `OK` + `All checks passed!`.

- [ ] **Step 4: Verify no existing startup tests broke**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/utils/test_startup_manager.py -q`

Expected: all pre-existing tests still pass. If `get_modules` discovery surprises a mocked test, narrow the mock to import-time only (the wiring tolerates an empty list).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/utils/startup_manager.py
git commit -m "feat(boot): wire per-module migrations into startup (#490 phase 2)

After substrate migrations + settings_defaults seeding, iterate
get_modules() and call run_module_migrations() for each. Discovery
prefers an explicit ``module.migrations_dir`` attribute (test hook)
and falls back to ``<package>/migrations/`` next to the module
package source.

Failures log + continue — startup must not block on a broken
downstream module migration.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Full test sweep + CLAUDE.md + umbrella checkbox

**Files:**

- Modify: `CLAUDE.md`
- Modify: nothing on disk for umbrella poindexter#490 (via `gh issue edit`)

- [ ] **Step 1: Full plugin + services unit-test sweep**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/ tests/unit/services/test_module_migrations.py tests/unit/utils/test_startup_manager.py -q --tb=no`

Expected: every test passes. New tests: 5 (plugins) + 6 (module_migrations) = 11 net additions; existing startup_manager tests stay green.

- [ ] **Step 2: Ruff sweep across every file Phase 2 touched**

Run: `cd src/cofounder_agent && poetry run ruff check services/module_migrations.py services/migrations/<the-substrate-migration> utils/startup_manager.py tests/unit/services/test_module_migrations.py tests/integration_db/test_module_migrations.py`

Expected: `All checks passed!`.

- [ ] **Step 3: Update `CLAUDE.md` Reference Documentation section**

Find the existing Module v1 line (added in Phase 1) under "Reference Documentation":

```markdown
- **Module v1 (Glad-Labs/poindexter#490):** [`docs/architecture/module-v1.md`](docs/architecture/module-v1.md) — Phase 1 (Module Protocol + `get_modules()` registry + manifest validation) shipped 2026-05-13. ...
```

Replace it with the Phase-2-aware version:

```markdown
- **Module v1 (Glad-Labs/poindexter#490):** [`docs/architecture/module-v1.md`](docs/architecture/module-v1.md) — Phase 1 (Module Protocol + `get_modules()` registry + manifest validation) shipped 2026-05-13. Phase 2 (per-module migration runner + `module_schema_migrations` table + boot wiring) shipped 2026-05-13. Each Module bundles lower-level plugin contributions (stages / reviewers / probes / jobs / taps / adapters / providers / packs) plus DB migrations, Grafana panels, HTTP routes, and CLI subcommands. Phases 3-5 (ContentModule package, route/dashboard auto-discovery, visibility flag) get their own plans.
```

- [ ] **Step 4: Tick the Phase 2 checkbox on umbrella poindexter#490**

```bash
gh issue view 490 --repo Glad-Labs/poindexter --json body --jq '.body' > "$TEMP/umbrella490_body.md"
python -c "
import os, pathlib
p = pathlib.Path(os.environ['TEMP']) / 'umbrella490_body.md'
body = p.read_text(encoding='utf-8')
old = '- [ ] Phase 2 — Per-module migration runner + \`module_schema_migrations\` table'
assert old in body, 'Phase 2 line not found verbatim'
new = '- [x] Phase 2 — Per-module migration runner + \`module_schema_migrations\` table (shipped 2026-05-13)'
p.write_text(body.replace(old, new), encoding='utf-8')
print('rewrote Phase 2 line')
"
gh issue edit 490 --repo Glad-Labs/poindexter --body-file "$TEMP/umbrella490_body.md"
```

- [ ] **Step 5: Commit CLAUDE.md + push**

```bash
git add CLAUDE.md
git commit -m "docs(claude): point at Module v1 Phase 2 (shipped 2026-05-13)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

- [ ] **Step 6: Final acceptance — Phase 2 is done if all true**

1. `services/migrations/<timestamp>_create_module_schema_migrations.py` exists; running it creates the `module_schema_migrations` table.
2. `services/module_migrations.py` exposes `run_module_migrations(pool, module_name, migrations_dir) -> ModuleMigrationResult`.
3. `utils/startup_manager._run_migrations` iterates `get_modules()` after substrate migrations and calls `run_module_migrations` per-module.
4. `tests/unit/services/test_module_migrations.py` — 6/6 green.
5. `tests/integration_db/test_module_migrations.py` — 2/2 green (or skipped cleanly with `no live DB reachable`).
6. `CLAUDE.md` — Phase 2 noted alongside Phase 1.
7. Umbrella poindexter#490 — Phase 2 checkbox is `[x]`.

Run: `cd src/cofounder_agent && poetry run python -c "
from services.module_migrations import run_module_migrations, ModuleMigrationResult
print('run_module_migrations:', run_module_migrations)
print('ModuleMigrationResult:', ModuleMigrationResult)
"`

Expected: prints the importable names (confirms the public API is exported).

---

## What's NOT in this plan (Phases 3-5)

- **Phase 3** — Convert `content/` pipeline code into a `ContentModule` package. Will exercise this runner against the first real module.
- **Phase 4** — `Module.register_routes(app)` + `Module.register_dashboards(grafana)` auto-discovery.
- **Phase 5** — `Visibility` flag integration with `scripts/sync-to-github.sh`.

Track at: [Glad-Labs/poindexter#490](https://github.com/Glad-Labs/poindexter/issues/490).
